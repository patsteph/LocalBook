"""Record what the old app cites for real questions, for the Swift Mac's citation gate.

Phase 3's gate (docs/swift-migration/00-decision.md) asks for the personal library migrated with 95 % identical
citations on a fixture, every difference explained. This script is the Python half. For each notebook it takes the
questions the user actually asked there (``exploration_queries``, newest first) and runs the old app's own
retrieval, step for step as ``rag_engine.query`` does up to the point where the answer model would start: the
rule-based query analysis and expansion, the embedding through the production engine, ``adaptive_search`` (hybrid
vector + BM25), the reranker, the entity and source-type boosts, and ``build_citations_and_context``. What that
step would have handed the answer model as [1]…[n] is written down: ``source_id``, ``chunk_index``, filename,
confidence. The Swift Mac then opens the fixture in Settings › Diagnostics › Citation gate after "Move my library
here", asks the same questions of its own copy, and compares citation by citation.

Run from ``backend/`` with the old app's Python, the old app closed (both would hold the embedder)::

    LOCALBOOK_USE_PRODUCTION_DATA=1 .venv/bin/python ../scripts/citation_gate_export.py \
        [--per-notebook 10] [--notebooks id,id] [--out ~/Desktop/localbook-parity/citation-fixture.json]

READ-ONLY on user data: it reads localbook.db and the LanceDB tables, loads the embedder and the reranker in this
process, and writes the one fixture file. No metrics row, no answer cache, no memory. The query analysis is the
rule-based fallback (``rag_query_analyzer.fallback_query_analysis``), not the phi4 pass: the Swift Mac has no
such pass (its recipe carries the same rules), and a fixture must give the same answer twice.
"""
from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import json
import os
import platform
import sqlite3
import sys
import time

sys.path.insert(0, ".")

FORMAT = "localbook-citation-fixture/1"


def questions_for(conn: sqlite3.Connection, notebook_id: str, n: int) -> list[str]:
    rows = conn.execute(
        "SELECT query FROM exploration_queries WHERE notebook_id = ? AND length(query) > 12 "
        "ORDER BY timestamp DESC LIMIT ?", (notebook_id, n * 4)).fetchall()
    seen, out = set(), []
    for (q,) in rows:
        k = (q or "").strip().lower()
        if k and k not in seen:
            seen.add(k)
            out.append(q.strip())
        if len(out) >= n:
            break
    return out


async def cite(engine, notebook_id: str, question: str, top_k: int = 4) -> dict:
    """rag_engine.query's steps 1–4 without the model: what the answer would have been allowed to cite."""
    from config import settings
    from services import rag_context, rag_search, rag_query_analyzer, rag_storage
    from services.entity_extractor import entity_extractor
    from services.source_router import source_router

    analysis = rag_query_analyzer.fallback_query_analysis(question)
    basic_expanded = engine._expand_query(question)
    query_embedding = engine.encode(basic_expanded, is_query=True)[0].tolist()
    table = rag_storage.get_table(notebook_id)
    overcollect_k = settings.retrieval_overcollect if settings.use_reranker else top_k
    results = await engine._adaptive_search(table, question, query_embedding, analysis, overcollect_k)
    candidates = len(results)
    if settings.use_reranker and len(results) > top_k:
        results = rag_search.rerank(question, results, top_k=top_k + 1)
    results = entity_extractor.boost_results_by_entity(notebook_id, question, results)
    results = source_router.apply_routing_boost(results, source_router.route(question))
    citations, _sources, _context, low = await rag_context.build_citations_and_context(results, "[gate]")
    return {
        "question": question,
        "candidates": candidates,
        "low_confidence": bool(low),
        "citations": [{"source_id": c["source_id"], "chunk_index": int(c.get("chunk_index") or 0),
                       "filename": c.get("filename", ""), "confidence": float(c.get("confidence", 0))} for c in citations],
    }


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--per-notebook", type=int, default=10)
    ap.add_argument("--notebooks", default="", help="comma-separated notebook ids; default every notebook with questions")
    ap.add_argument("--out", default=os.path.expanduser("~/Desktop/localbook-parity/citation-fixture.json"))
    args = ap.parse_args()

    from config import settings
    prefs = os.path.join(str(settings.data_dir), "user_preferences.json")
    if os.path.exists(prefs):
        try:
            m = (json.load(open(prefs)).get("default_combo") or {}).get("embedding_model")
            if m:
                settings.embedding_model = m
        except Exception as e:  # noqa: BLE001
            print(f"(could not read {prefs}: {e})")
    from services.rag_engine import rag_engine
    from services.sync import phone_library

    titles = phone_library.notebook_titles()
    wanted = [n for n in args.notebooks.split(",") if n] or sorted(titles, key=lambda i: titles[i].lower())
    conn = sqlite3.connect(f"file:{os.path.join(str(settings.data_dir), 'localbook.db')}?mode=ro", uri=True)
    items, t0 = [], time.time()
    for nid in wanted:
        if nid not in titles:
            print(f"  ⚠️ no notebook {nid}")
            continue
        qs = questions_for(conn, nid, args.per_notebook)
        if not qs or phone_library._table(nid) is None:
            continue
        print(f"{titles[nid]}: {len(qs)} questions")
        for q in qs:
            try:
                item = await cite(rag_engine, nid, q)
            except Exception as e:  # noqa: BLE001 — one bad question must not stop the export
                print(f"  ⚠️ {q[:60]}: {type(e).__name__}: {e}")
                continue
            item.update({"notebook_id": nid, "notebook_title": titles[nid]})
            items.append(item)
            print(f"  {len(item['citations'])} cited · {q[:70]}")
    conn.close()

    fixture = {
        "format": FORMAT,
        "created_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "mac": {"device": platform.node(), "embedder": settings.embedding_model,
                "reranker": f"{settings.reranker_type}/{getattr(settings, 'reranker_precision', '')}" if settings.use_reranker else "off",
                "analysis": "fallback", "top_k": 4, "overcollect": int(settings.retrieval_overcollect)},
        "questions": items,
    }
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(fixture, f, indent=1, ensure_ascii=False)
    print(f"\n{len(items)} questions over {len({i['notebook_id'] for i in items})} notebooks in {time.time() - t0:.0f} s → {args.out}")
    return 0 if items else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
