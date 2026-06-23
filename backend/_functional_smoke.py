"""Functional smoke test for the PB-2d / D4 httpx→ollama_service migrations.

Dev artifact (not shipped) — run against a LIVE Ollama to prove migrated
services actually work end-to-end, not just compile. Usage:

    .venv/bin/python3 _functional_smoke.py

Each check exercises a migrated code path with real model calls and asserts a
sensible result shape. Add a row here whenever a file is migrated.
"""
import asyncio


async def _run():
    from config import settings
    from services.ollama_service import ollama_service

    checks = []

    async def check(name, coro_or_val, ok_fn):
        try:
            val = await coro_or_val if asyncio.iscoroutine(coro_or_val) else coro_or_val
            ok = ok_fn(val)
            checks.append((name, ok, "" if ok else f"unexpected: {repr(val)[:80]}"))
        except Exception as e:
            checks.append((name, False, f"{type(e).__name__}: {e}"))

    DIM = settings.embedding_dim

    # ── core primitives ──
    await check("ollama_service.generate(phi4)",
                ollama_service.generate(prompt="Say hello", model=settings.ollama_fast_model,
                                        num_predict=10, timeout=60.0),
                lambda r: bool(r.get("response", "").strip()))
    await check("ollama_service.embed (1024)",
                ollama_service.embed("embedding text"),
                lambda e: len((e.get("embeddings") or [[]])[0]) == DIM)

    # ── migrated background-inference services ──
    from services.knowledge_graph import knowledge_graph_service
    await check("knowledge_graph.get_embedding",
                knowledge_graph_service.get_embedding("artificial intelligence"),
                lambda v: len(v) == DIM)

    from services.entity_extractor import entity_extractor
    await check("entity_extractor._extract_with_llm",
                entity_extractor._extract_with_llm("Tim Cook leads Apple in Cupertino."),
                lambda v: len(v) > 0)

    from services.auto_tagger import auto_tagger
    await check("auto_tagger.generate_tags",
                auto_tagger.generate_tags(title="RAG",
                                          content="Retrieval augmented generation with embeddings and reranking."),
                lambda v: len(v) > 0)

    from services.contradiction_detector import contradiction_detector
    # NB: assert migration-correctness (valid list, no crash) not non-empty —
    # phi4 is an inconsistent claim extractor and legitimately returns [] for
    # "no clear claims" ~half the time. The parse handles both shapes.
    await check("contradiction_detector.claims (shape)",
                contradiction_detector._extract_claims_from_chunk(
                    "Revenue grew 40% to 90 billion in 2025.", "s", "S"),
                lambda v: isinstance(v, list))

    # ── core embedding path (hot — single + batch must keep shape) ──
    from services import rag_embeddings
    await check("rag_embeddings._get_ollama_embedding",
                rag_embeddings._get_ollama_embedding("hello world"),
                lambda v: len(v) == DIM)
    await check("rag_embeddings.encode_async (batch order+shape)",
                rag_embeddings.encode_async(["alpha one", "beta two", "gamma three"]),
                lambda arr: arr.shape == (3, DIM))

    # ── chat-query path (migrated) ──
    from services.query_decomposer import QueryDecomposer
    await check("query_decomposer.decompose",
                QueryDecomposer().decompose("Compare the safety and cost of nuclear versus solar power and explain the tradeoffs."),
                lambda v: isinstance(v, list))

    # ── batched HyDE (per-chunk mapping must survive batching) ──
    from services.rag_storage import generate_chunk_questions
    _hyde_chunks = ["The Eiffel Tower is 330m tall, finished 1889.",
                    "Mitochondria produce ATP in cells.",
                    "HTTP 404 means resource not found.",
                    "Python uses indentation for blocks.",
                    "Everest is 8849m tall.",
                    "Photosynthesis makes glucose from sunlight."]
    await check("rag_storage.generate_chunk_questions (batched, per-chunk)",
                generate_chunk_questions(_hyde_chunks),
                lambda v: len(v) == len(_hyde_chunks) and sum(1 for q in v if q.strip()) >= 4)

    # ── D4 tail (2026-06-23): visual + theme generation paths ──
    _viz_sample = ("First, AI safety and alignment research is accelerating. "
                   "Second, enterprise adoption of autonomous agents grew 40% year over year. "
                   "Third, regulatory frameworks for healthcare AI remain fragmented. "
                   "Recommendation: adopt staged rollout with human oversight.")
    from services.theme_extractor import extract_themes_llm
    await check("theme_extractor.extract_themes_llm",
                extract_themes_llm(_viz_sample),
                lambda vc: bool(vc.title) and len(vc.themes) >= 3)

    from services.visual_generator import VisualGenerator
    await check("visual_generator._call_llm",
                VisualGenerator()._call_llm(
                    "Extract the key themes as JSON with a \"themes\" array.", _viz_sample),
                lambda r: isinstance(r, dict) and len(r) > 0)

    from services.visual_analyzer import visual_analyzer
    await check("visual_analyzer.analyze_with_llm",
                visual_analyzer.analyze_with_llm(_viz_sample),
                lambda r: bool(r.get("visual_type")) and isinstance(r.get("key_items"), list))

    # ── Apple Vision OCR seam (engine strategy) ──
    try:
        import base64 as _b64, io as _io
        from PIL import Image, ImageDraw, ImageFont
        _img = Image.new("RGB", (560, 90), "white")
        _d = ImageDraw.Draw(_img)
        try:
            _f = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 26)
        except Exception:
            _f = ImageFont.load_default()
        _d.text((20, 30), "Order 7788 shipped", fill="black", font=_f)
        _buf = _io.BytesIO(); _img.save(_buf, "PNG")
        _ob64 = _b64.b64encode(_buf.getvalue()).decode()
        await check("apple_vision_ocr via vision_describe(ocr_mode)",
                    ollama_service.vision_describe(image_b64=_ob64, prompt="transcribe", ocr_mode=True),
                    lambda v: isinstance(v, str) and "7788" in v)
    except Exception as _e:
        checks.append(("apple_vision_ocr", False, f"setup error: {_e}"))

    print()
    allok = True
    for name, ok, detail in checks:
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
        allok = allok and ok
    npass = sum(1 for _, ok, _ in checks if ok)
    print(f"\n{'ALL PASS' if allok else 'FAILURES'} ({npass}/{len(checks)})")
    return allok


if __name__ == "__main__":
    import sys
    sys.exit(0 if asyncio.run(_run()) else 1)
