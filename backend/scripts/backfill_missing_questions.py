"""Backfill HyDE ("questions this chunk answers") for chunks that have none although their source does.

Why: the over-limit chunk migration (services/chunk_limit_migration.py) keeps a long chunk's generated
questions on its FIRST piece only; later pieces got "". Question generation can also fail for single chunks.
Those chunks are less findable than their neighbours.

A candidate is a stored chunk (chunk_index >= 0, below the ingest cap of 200) with empty questions, in a
source where other chunks have questions. Sources with NO questions at all (HyDE was off) are left alone.

  python scripts/backfill_missing_questions.py                 # dry run: counts per notebook, writes nothing
  python scripts/backfill_missing_questions.py --apply         # generate, re-embed, write (after you read the counts)
  python scripts/backfill_missing_questions.py --notebook ID   # one notebook

Runs against the real data dir only with LOCALBOOK_USE_PRODUCTION_DATA=1 (config.py explains why that is loud).
Quit the LocalBook app first when using --apply so two writers never touch the same table.
"""
import argparse
import asyncio
import collections
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

_HYDE_CAP = 200


def candidates(rows):
    by_source = collections.defaultdict(list)
    for r in rows:
        by_source[r["source_id"]].append(r)
    out = []
    for src_rows in by_source.values():
        if not any((r.get("synthetic_questions") or "").strip() for r in src_rows):
            continue
        out += [r for r in src_rows if r["chunk_index"] >= 0 and r["chunk_index"] < _HYDE_CAP
                and not (r.get("synthetic_questions") or "").strip()]
    return out


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--notebook")
    args = ap.parse_args()

    import lancedb
    from config import settings
    prefs = Path(settings.data_dir) / "user_preferences.json"
    if prefs.exists():                                   # the models the app itself uses (user_preferences.json → default_combo)
        combo = json.loads(prefs.read_text()).get("default_combo", {})
        if combo.get("embedding_model"):
            settings.embedding_model = combo["embedding_model"]
        if combo.get("fast_model"):
            settings.fast_model = combo["fast_model"]
    db = lancedb.connect(str(Path(settings.data_dir) / "lancedb"))
    total = 0
    for name in sorted(db.table_names()):
        if not name.startswith("notebook_") or (args.notebook and name != f"notebook_{args.notebook}"):
            continue
        table = db.open_table(name)
        if "synthetic_questions" not in table.schema.names:
            continue
        rows = await asyncio.to_thread(lambda: table.search().select(
            ["source_id", "chunk_index", "text", "synthetic_questions"]).limit(table.count_rows()).to_list())
        cand = candidates(rows)
        total += len(cand)
        print(f"{name[len('notebook_'):][:8]}…  rows={len(rows):5d}  missing questions={len(cand):4d}")
        if not (args.apply and cand):
            continue
        from services import embedding_profile
        from services.embedding_migration import embedded_text
        from services.llm_runtime import llm_runtime
        from services.rag_storage import generate_chunk_questions
        prof = embedding_profile.for_model(settings.embedding_model)
        full = {(r["source_id"], r["chunk_index"]): r for r in await asyncio.to_thread(lambda: table.to_arrow().to_pylist())}
        for i in range(0, len(cand), 16):
            batch = cand[i:i + 16]
            questions = await generate_chunk_questions([r["text"] for r in batch])
            updated = []
            for r, q in zip(batch, questions):
                if not q.strip():
                    continue
                row = dict(full[(r["source_id"], r["chunk_index"])])
                row["synthetic_questions"] = q
                updated.append(row)
            if not updated:
                continue
            vecs = await llm_runtime.embed_with_model([prof.format_document(embedded_text(u)) for u in updated],
                                                      settings.embedding_model)
            if len(vecs) != len(updated) or any(not any(v) for v in vecs):
                raise RuntimeError("embedder returned a bad vector; nothing more written")
            for u, v in zip(updated, vecs):
                u["vector"] = v
            import pyarrow as pa
            replacement = pa.Table.from_pylist(updated, schema=table.schema)       # validated BEFORE anything is deleted

            def _write() -> None:                                                   # same delete-then-add as chunk_limit_migration
                for u in updated:
                    table.delete(f"source_id = '{u['source_id']}' AND chunk_index = {u['chunk_index']}")
                table.add(replacement)
            await asyncio.to_thread(_write)
            print(f"   wrote {len(updated)} (batch {i // 16 + 1})")
    print(f"TOTAL missing: {total}" + ("" if args.apply else "  (dry run — nothing written)"))


if __name__ == "__main__":
    asyncio.run(main())
