"""Try an embedding model WITHOUT switching to it — the LLM Locker's "Test" (2026-10-14).

Swapping the live embedder means re-indexing every notebook, so it is the last thing to do,
not the first way to find out whether a model is any good. This answers "is it better?" on
its own:

  * The Evaluator's committed corpus (three local documents) is extracted and chunked
    exactly as an ingest would, and the 22-question gold set (any-of answer markers) is
    scored against it. Only questions whose markers exist in this corpus count.
  * Each model embeds the chunks and the questions in ITS OWN formats
    (services/embedding_profile) into an in-memory index — nothing touches LanceDB or any
    notebook — and ranks by cosine. Pure vector retrieval: this isolates the embedder, which
    is the thing being compared (rerank would mask it).
  * The candidate is also scored WITHOUT its prompts (the ablation the model card warns
    about) and, when it supports Matryoshka truncation, at 256 dimensions.

One trial at a time, as a background job with progress (holding an HTTP request for
minutes makes WebKit give up). It goes through the GPU budget and unloads a candidate it
loaded, so it cannot push the Mac into swap or leave 1.5 GB resident.
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np

logger = logging.getLogger(__name__)

DEEP_K = 20
DISTRACTORS = 600          # real passages from the user's notebooks, as a haystack
CORPUS_DIR = Path(__file__).resolve().parents[1] / "evaluator" / "test_content"
CORPUS_FILES = ("test_document.pdf", "test_presentation.pptx", "test_report.docx")

_job: Dict[str, Any] = {}
_lock = asyncio.Lock()


# ── corpus + gold ───────────────────────────────────────────────────────────


async def _corpus() -> List[Dict[str, str]]:
    from services import rag_chunking
    from services.document_processor import document_processor

    chunks: List[Dict[str, str]] = []
    for name in CORPUS_FILES:
        path = CORPUS_DIR / name
        if not path.exists():
            continue
        text = await document_processor._extract_text(path.read_bytes(), name)
        pieces = rag_chunking.chunk_text_smart(text or "", path.suffix.lstrip("."), name)
        for i, piece in enumerate(pieces):
            chunks.append({"text": piece, "parent_text": rag_chunking.get_parent_context(pieces, i)})
    return chunks


def _distractors(n: int, markers: List[str]) -> List[Dict[str, str]]:
    """Real passages from the user's notebooks — READ-ONLY, text column only — so the answer
    has to be found among hundreds of plausible passages, not eleven. Deterministic (fixed
    seed), and any passage containing a gold answer marker is dropped so it can never count
    as a false hit. Never written anywhere; never leaves this process."""
    import os
    import random

    import lancedb
    from config import settings

    db_path = str(settings.db_path)
    if n <= 0 or not os.path.isdir(db_path):
        return []
    db = lancedb.connect(db_path)
    names = sorted(d[: -len(".lance")] for d in os.listdir(db_path)
                   if d.startswith("notebook_") and d.endswith(".lance"))
    rng = random.Random(1014)
    pool: List[str] = []
    low = [m.lower() for m in markers if m]
    for name in names:
        try:
            rows = db.open_table(name).search().select(["text"]).limit(400).to_list()
        except Exception as exc:
            logger.debug("[embedding-trial] %s unreadable: %s", name, exc)
            continue
        for r in rows:
            t = str(r.get("text") or "").strip()
            if len(t) > 80 and not any(m in t.lower() for m in low):
                pool.append(t)
    rng.shuffle(pool)
    return [{"text": t, "parent_text": "", "distractor": True} for t in pool[:n]]


def _gold(chunks: List[Dict[str, str]]) -> List[Dict[str, Any]]:
    """Committed gold questions whose answer exists in THIS corpus (the local overlay refers to
    the user's notebooks, which a trial never reads)."""
    from evaluator.test_runners.retrieval import _load_gold

    hay = " ".join((c["text"] + " " + c["parent_text"]).lower() for c in chunks)
    return [q for q in _load_gold() if any(m and m.lower() in hay for m in q.get("markers") or [])]


# ── scoring ─────────────────────────────────────────────────────────────────


def _score(doc_vecs: np.ndarray, q_vecs: np.ndarray, chunks, gold, dims: Optional[int] = None) -> Dict[str, Any]:
    from evaluator import retrieval_metrics as rm

    d, q = doc_vecs, q_vecs
    if dims and dims < d.shape[1]:                   # Matryoshka: truncate, then re-normalise
        d = d[:, :dims] / np.linalg.norm(d[:, :dims], axis=1, keepdims=True)
        q = q[:, :dims] / np.linalg.norm(q[:, :dims], axis=1, keepdims=True)
    with np.errstate(all="ignore"):       # Accelerate's matmul raises spurious FP warnings
        sims = q @ d.T
    hits1 = hits5 = 0
    rr, ranks, misses = [], [], []
    for qi, case in enumerate(gold):
        top = np.argsort(-sims[qi])[:DEEP_K]
        rel = rm.relevance_from_markers([chunks[i] for i in top], case.get("markers") or [])
        first = rm.first_relevant_rank(rel)
        hits1 += int(rm.hit_at_k(rel, 1) > 0)
        hits5 += int(rm.hit_at_k(rel, 5) > 0)
        rr.append(rm.mrr(rel))
        if first:
            ranks.append(first)
        else:
            misses.append(case.get("id"))
    n = max(1, len(gold))
    return {"dims": int(q.shape[1]), "hit_at_1": round(hits1 / n, 3), "hit_at_5": round(hits5 / n, 3),
            "mrr": round(float(np.mean(rr)) if rr else 0.0, 3),
            "mean_rank": round(float(np.mean(ranks)), 2) if ranks else None,
            "missed": misses}


def _loaded_dtype(model: str) -> str:
    """The dtype of the weights actually loaded — a config's `dtype` can be stale."""
    try:
        from mlx.utils import tree_flatten
        from services.mlx_engine import mlx_engine
        pair = mlx_engine._embed_resident.get(model)
        if pair:
            kinds = {str(v.dtype).replace("mlx.core.", "") for _, v in tree_flatten(pair[0].parameters())}
            floats = sorted(k for k in kinds if k.startswith(("float", "bfloat")))
            return "+".join(floats) or "+".join(sorted(kinds))
    except Exception:
        pass
    return ""


async def _embed(model: str, texts: List[str]) -> np.ndarray:
    from services.llm_runtime import llm_runtime
    return np.asarray(await llm_runtime.embed_with_model(texts, model), dtype=np.float32)


async def _evaluate(model: str, chunks, gold, *, ablation: bool, progress) -> Dict[str, Any]:
    from services import embedding_profile, mlx_embed_loaders
    from services.model_sizing import exact_weight_gb

    prof = embedding_profile.for_model(model)
    # No titles: the app embeds every chunk as "title: none", so the trial does too.
    docs = embedding_profile.format_documents([c["text"] for c in chunks], prof)
    queries = embedding_profile.format_queries([q["question"] for q in gold], prof)
    progress(f"embedding {len(docs)} passages with {model}")
    await _embed(model, docs[:1])                      # load + warm, so speed is speed
    t0 = time.perf_counter()
    d = await _embed(model, docs)
    secs = time.perf_counter() - t0
    q = await _embed(model, queries)
    out: Dict[str, Any] = {
        "model": model, "profile": {"source": prof.source, "loader": prof.loader, "pooling": prof.pooling,
                                    "query_template": prof.query_template,
                                    "document_template": prof.document_template},
        "weights_dtype": _loaded_dtype(model) or mlx_embed_loaders.stored_dtype(model) or "unknown",
        "weights_gb": exact_weight_gb(model),
        "passages_per_sec": round(len(docs) / secs, 1) if secs > 0 else None,
        "full": _score(d, q, chunks, gold),
    }
    if 256 in prof.mrl_dims and d.shape[1] > 256:
        out["dims_256"] = _score(d, q, chunks, gold, dims=256)
    if ablation and (prof.query_template != "{text}" or prof.document_template != "{text}"):
        progress(f"{model}: the same again without its prompts")
        d0 = await _embed(model, [c["text"] for c in chunks])
        q0 = await _embed(model, [g["question"] for g in gold])
        out["without_prompts"] = _score(d0, q0, chunks, gold)
    return out


# ── the job ─────────────────────────────────────────────────────────────────


def status() -> Dict[str, Any]:
    return dict(_job)


async def start(candidate: str, *, with_distractors: bool = True) -> Dict[str, Any]:
    if _job.get("running"):
        return {"started": False, "reason": "a trial is already running", "job": status()}
    _job.clear()
    _job.update({"running": True, "candidate": candidate, "stage": "starting",
                 "started_at": datetime.now(timezone.utc).isoformat(), "result": None, "error": None})
    from utils.tasks import safe_create_task
    _job["with_distractors"] = with_distractors
    safe_create_task(_run(candidate, with_distractors), name="embedding-trial")
    return {"started": True, "job": status()}


async def _run(candidate: str, with_distractors: bool = True) -> None:
    from config import settings
    from services.mlx_engine import mlx_engine
    from services.model_sizing import exact_weight_gb

    def progress(stage: str) -> None:
        _job["stage"] = stage
        logger.info("[embedding-trial] %s", stage)

    live = settings.embedding_model
    was_resident = candidate in getattr(mlx_engine, "_embed_resident", {})
    try:
        async with _lock:
            progress("reading the test corpus")
            chunks = await _corpus()
            gold = _gold(chunks)
            if not chunks or not gold:
                raise RuntimeError("the Evaluator's test corpus or gold set is missing")
            if with_distractors:
                progress("sampling passages from your notebooks as distractors (read-only)")
                markers = [m for q in gold for m in (q.get("markers") or [])]
                chunks = chunks + await asyncio.to_thread(_distractors, DISTRACTORS, markers)
            need = (exact_weight_gb(candidate) or 1.5) * 1.3
            if not was_resident and not await mlx_engine.admit(candidate, need_gb=need, wait_s=60.0):
                raise RuntimeError("not enough GPU memory for the candidate right now — try again when idle")
            current = await _evaluate(live, chunks, gold, ablation=False, progress=progress)
            cand = current if candidate == live else await _evaluate(candidate, chunks, gold,
                                                                     ablation=True, progress=progress)
            _job["result"] = {"live": current, "candidate": cand, "questions": len(gold),
                              "passages": len(chunks),
                              "distractors": sum(1 for c in chunks if c.get("distractor")),
                              "note": "Pure vector retrieval — the embedder alone, before hybrid search "
                                      "and rerank. The answers are in the Evaluator's test documents; "
                                      "distractors are passages from your notebooks."}
            progress("done")
    except Exception as exc:
        logger.warning("[embedding-trial] failed: %s", exc)
        _job["error"] = str(exc)
        _job["stage"] = "failed"
    finally:
        if candidate != live and not was_resident:
            try:
                await mlx_engine.unload(candidate)
            except Exception as exc:
                logger.debug("[embedding-trial] unload: %s", exc)
        _job["running"] = False
        _job["finished_at"] = datetime.now(timezone.utc).isoformat()
