"""Embedding quality test runner — tests throughput, dimensions, and semantic discrimination."""

import time
import math
from datetime import datetime
from evaluator.models import EvalResult
from evaluator.capabilities import capabilities_for, FEATURES
import httpx


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    """Compute cosine similarity between two vectors."""
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


async def _embed(text: str, model: str) -> list[float]:
    """Get embedding from the resolved backend (Ollama or sidecar).

    v1.8.2: uses the provider resolver's base URL instead of hardcoding
    localhost:11434. If the embedding model isn't actually an embedding
    model on its backend, callers should skip the test via capabilities.

    Wave 9.6: when MLX embeddings are adopted (embed_engine==mlx), embed through the app's
    real seam (`ollama_service.embed`, which dispatches to the in-process MLX arctic engine
    with an Ollama fallback) so the Evaluator measures exactly what RAG runs — not Ollama.
    """
    from config import settings
    if getattr(settings, "embed_engine", "ollama") == "mlx":
        try:
            from services.ollama_service import ollama_service
            res = await ollama_service.embed(text)
            embs = (res or {}).get("embeddings") or []
            return embs[0] if embs else []
        except Exception as e:
            print(f"[EVAL-EMBED] MLX seam embed failed ({e})")
            return []
    from services.llm_provider import resolve as _resolve_provider, Provider as _Provider
    route = _resolve_provider(model)
    # Ollama is the only backend that serves /api/embeddings today; sidecar
    # embeddings would require llama-server --embeddings which we don't spawn.
    if route.provider is not _Provider.OLLAMA:
        return []
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            f"{route.base_url}/api/embeddings",
            json={"model": model, "prompt": text},
        )
        if response.status_code == 200:
            data = response.json()
            return data.get("embedding", [])
    return []


async def run(notebook_id: str, config: dict, combo_name: str, hw_fingerprint: str) -> list[EvalResult]:
    """Test embedding model quality: dimensions, throughput, semantic discrimination."""
    from config import settings

    # Wave 9.6 — report the model actually exercised: the MLX arctic id when embed_engine==mlx
    # (the seam runs it at the same 1024 dim), else the Ollama embedding model.
    _mlx_embed = getattr(settings, "embed_engine", "ollama") == "mlx"
    embed_model = (getattr(settings, "mlx_embedding_model", "") if _mlx_embed
                   else settings.embedding_model)
    expected_dim = getattr(settings, 'embedding_dim', 0)

    results = []

    # ── Test 1: Dimension correctness + throughput ───────────────────────
    result = EvalResult(
        test_id="embedding_dimensions",
        category="embedding_quality",
        test_name="Embedding Dimensions & Throughput",
        model_combo=combo_name,
        hardware_fingerprint=hw_fingerprint,
        timestamp=datetime.utcnow().isoformat(),
    )
    result.stamp_provider(embed_model)

    # Capability gate: skip if embed backend cannot serve embeddings
    if not embed_model:
        result.mark_skipped("No embedding model configured")
        print("[EVAL-EMBED] skipped — no embedding model configured")
        return [result]
    # The capability gate is Ollama-oriented (probes /api/show). Skip it on MLX — the arctic
    # model is a known embedding model and is served in-process, not via /api/embeddings.
    if not _mlx_embed:
        _caps = capabilities_for(embed_model)
        if not _caps.supports(FEATURES.EMBEDDINGS):
            reason = _caps.skip_reason(FEATURES.EMBEDDINGS) or f"{embed_model} backend has no /api/embeddings"
            result.mark_skipped(reason)
            print(f"[EVAL-EMBED] skipped — {reason}")
            return [result]

    test_passages = [
        "Retrieval-augmented generation combines retrieval with generation.",
        "Vector databases store embeddings for similarity search.",
        "Language models process text using transformer architectures.",
        "Apple Silicon provides hardware acceleration for machine learning.",
        "The chunking strategy uses semantic boundaries for document splitting.",
    ]

    try:
        start = time.time()
        embeddings = []
        for passage in test_passages:
            emb = await _embed(passage, embed_model)
            embeddings.append(emb)
        elapsed = (time.time() - start) * 1000

        result.total_time_ms = elapsed
        result.tokens_per_second = len(test_passages) / max(0.001, elapsed / 1000.0)

        # Check dimensions
        dims = [len(e) for e in embeddings if e]
        if not dims:
            raise ValueError("No embeddings returned")

        actual_dim = dims[0]
        all_same_dim = all(d == actual_dim for d in dims)

        dim_score = 100 if (expected_dim == 0 or actual_dim == expected_dim) and all_same_dim else 0

        result.accuracy_score = dim_score
        result.actual_output_preview = f"Dim={actual_dim}, expected={expected_dim}, throughput={result.tokens_per_second:.1f}/sec"
        # Dimension correctness is the real gate (wrong dim = broken for this app → 0). Throughput
        # here is a LATENCY-bound micro-measurement — 5 sequential single embeds dominated by
        # per-call overhead, not a true batch benchmark: a fast engine still measures only ~8/sec,
        # so scoring it (the 2026-07-23 "40/sec=100" attempt) produced a false "degraded" even on a
        # correctly-wired strong embedder on EITHER engine (user report 2026-07-24). It's now
        # REPORTED (preview + tokens_per_second) but NOT scored. Model-quality discrimination — the
        # axis that actually fixes the old "always 88" — lives in Test 2 below.
        result.overall_score = dim_score
        result.passed = dim_score > 0

        if not result.passed:
            result.failure_reason = f"Wrong dimension: got {actual_dim}, expected {expected_dim}"

        print(f"[EVAL-EMBED] Dim={actual_dim}, {result.tokens_per_second:.1f} embeds/sec, {elapsed:.0f}ms")

    except Exception as e:
        result.passed = False
        result.failure_reason = str(e)[:200]
        result.overall_score = 0
        print(f"[EVAL-EMBED] Dimension test FAILED: {e}")

    results.append(result)

    # ── Test 2: Semantic discrimination ──────────────────────────────────
    result2 = EvalResult(
        test_id="embedding_discrimination",
        category="embedding_quality",
        test_name="Semantic Discrimination",
        model_combo=combo_name,
        hardware_fingerprint=hw_fingerprint,
        timestamp=datetime.utcnow().isoformat(),
    )
    result2.stamp_provider(embed_model)

    try:
        # Prefer the multi-set discriminator: each set anchors a sentence, a genuine
        # paraphrase (should be CLOSE), and hard NEAR-MISS distractors — topically adjacent
        # or lexical traps ("electrical transformers", "the museum STORED vases") that a weak
        # bag-of-words-ish embedder can't separate. The old single RAG-vs-cake-recipe pair was
        # so easy every competent model aced it, pinning the category near-constant regardless
        # of engine/model (user report 2026-07-23). Falls back to the legacy pair when unset.
        sets = config.get("embedding_discrimination_sets") or []
        margins: list[float] = []
        details: list[str] = []
        discriminated_all = True

        start = time.time()
        if sets:
            for s in sets:
                anchor, similar_s, distractors = s.get("anchor", ""), s.get("similar", ""), s.get("distractors", []) or []
                if not anchor or not similar_s or not distractors:
                    continue
                e_anchor = await _embed(anchor, embed_model)
                sim = _cosine_similarity(e_anchor, await _embed(similar_s, embed_model))
                dis_scores = [_cosine_similarity(e_anchor, await _embed(d, embed_model)) for d in distractors]
                max_dis = max(dis_scores) if dis_scores else 0.0
                margins.append(sim - max_dis)
                if sim <= max_dis:
                    discriminated_all = False
                details.append(f"{s.get('label', 'set')}: sim={sim:.3f} vs max_dis={max_dis:.3f} (Δ{sim - max_dis:.3f})")
        else:
            embed_tests = config.get("embedding_test_passages", {})
            similar = embed_tests.get("similar_pair", [])
            dissimilar = embed_tests.get("dissimilar_pair", [])
            if len(similar) >= 2 and len(dissimilar) >= 2:
                sim = _cosine_similarity(await _embed(similar[0], embed_model), await _embed(similar[1], embed_model))
                dis = _cosine_similarity(await _embed(dissimilar[0], embed_model), await _embed(dissimilar[1], embed_model))
                margins.append(sim - dis)
                discriminated_all = sim > dis
                details.append(f"sim={sim:.3f} vs dis={dis:.3f} (Δ{sim - dis:.3f})")

        result2.total_time_ms = (time.time() - start) * 1000

        if margins:
            mean_margin = sum(margins) / len(margins)
            result2.actual_output_preview = "; ".join(details) + f" | mean Δ={mean_margin:.3f}"
            if discriminated_all:
                # Harder pairs → smaller margins → real spread. 250×margin so a strong embedder
                # (~0.20 mean margin on near-miss distractors) reaches ~100 while a mediocre one
                # (~0.05) scores ~62 — the category now separates models instead of flatlining.
                result2.overall_score = max(0, min(100, int(50 + mean_margin * 250)))
            else:
                result2.overall_score = 20  # failed to separate at least one set
            result2.accuracy_score = result2.overall_score
            result2.passed = discriminated_all
            if not discriminated_all:
                result2.failure_reason = f"Failed to discriminate a set: {'; '.join(details)}"
            print(f"[EVAL-EMBED] Discrimination: mean Δ={mean_margin:.3f}, score={result2.overall_score} ({len(margins)} set(s))")
        else:
            result2.skipped = True
            result2.skip_reason = "Missing embedding discrimination pairs/sets in config"
            result2.overall_score = 50

    except Exception as e:
        result2.passed = False
        result2.failure_reason = str(e)[:200]
        result2.overall_score = 0
        print(f"[EVAL-EMBED] Discrimination test FAILED: {e}")

    results.append(result2)
    return results
