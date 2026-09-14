#!/usr/bin/env python3
"""Re-embed the rows whose vector is all zeros.

WHY THIS EXISTS
---------------
A zero vector matches nothing under cosine similarity — forever. The chunk's TEXT is still in
the table, so the source looks present in the library and in every count, but RAG behaves as
if it was never added. There is no error and no symptom except answers that quietly omit it.

They were produced by an old fallback: when an embedding call failed or returned the wrong
shape, the batch was zero-filled "so retrieval gaps stay visible" — except nothing ever made
them visible. As of 2026-08-20 the embed paths RAISE instead (see
`llm_runtime._mlx_embed_or_none`), so no new ones can be created. This script repairs what the
old behaviour left behind.

Measured on this install, 2026-08-20: 148 chunks across 5 sources, four of them 100 % dead.

WHAT IT DOES
------------
For every notebook table, finds rows whose vector is all zeros, re-embeds their `text` through
the SAME seam ingestion uses, and writes them back. Nothing else is touched.

SAFETY
------
· DRY RUN by default. `--apply` is required to write anything.
· Rewrites a table only via delete-then-add of the AFFECTED ROWS ONLY, inside one pass, with
  every column carried over verbatim — LanceDB has no partial row update, so the read → embed
  → delete → add order matters and the new rows are built BEFORE anything is deleted.
· Verifies each re-embedded vector is non-zero and the right dimension BEFORE writing. A batch
  that still comes back zero is left exactly as it was rather than rewritten to the same
  garbage.
· `--notebook` / `--source` narrow the blast radius.
· Never touches a table it cannot fully read.

Usage:
    .venv/bin/python scripts/reembed_zero_vectors.py                 # report only
    .venv/bin/python scripts/reembed_zero_vectors.py --apply
    .venv/bin/python scripts/reembed_zero_vectors.py --apply --source <id>
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
import warnings

warnings.filterwarnings("ignore")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _is_zero(v) -> bool:
    return v is not None and not any(v)


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true",
                    help="actually write. Without this the script only reports.")
    ap.add_argument("--notebook", help="limit to one notebook id")
    ap.add_argument("--source", help="limit to one source id")
    ap.add_argument("--batch", type=int, default=32, help="embed batch size (default 32)")
    args = ap.parse_args()

    import lancedb

    from config import settings
    from services import rag_embeddings

    db = lancedb.connect(str(settings.db_path))
    listed = db.list_tables()
    names = dict(listed).get("tables", []) if not isinstance(listed, list) else list(listed)
    if args.notebook:
        names = [t for t in names if args.notebook in t]

    dim = settings.embedding_dim
    print(f"embedding model : {settings.embedding_model}")
    print(f"expected dim    : {dim}")
    print(f"tables to scan  : {len(names)}")
    print(f"mode            : {'APPLY (will write)' if args.apply else 'DRY RUN (no writes)'}\n")

    grand_found = grand_fixed = grand_skipped = 0

    for tname in names:
        try:
            tbl = db.open_table(tname)
            arrow = tbl.to_arrow()
        except Exception as e:
            print(f"  ! {tname[:44]}: unreadable ({type(e).__name__}) — skipped")
            continue
        if "vector" not in arrow.column_names or arrow.num_rows == 0:
            continue

        cols = arrow.column_names
        rows = arrow.to_pylist()
        targets = [r for r in rows if _is_zero(r.get("vector"))]
        if args.source:
            targets = [r for r in targets if r.get("source_id") == args.source]
        if not targets:
            continue

        grand_found += len(targets)
        by_src: dict = {}
        for r in targets:
            by_src.setdefault(r.get("source_id"), []).append(r)
        print(f"  {tname[:44]}: {len(targets)} zero row(s) across {len(by_src)} source(s)")
        for sid, rs in by_src.items():
            print(f"      {len(rs):4d}  {str(rs[0].get('filename'))[:52]}")

        if not args.apply:
            continue

        # Embed FIRST, verify, and only then touch the table. A row with no text cannot be
        # repaired — re-embedding "" just produces another dead vector — so it is left alone
        # and reported.
        texts, embeddable = [], []
        for r in targets:
            t = (r.get("text") or "").strip()
            if t:
                embeddable.append(r)
                texts.append(t)
        if len(embeddable) != len(targets):
            n = len(targets) - len(embeddable)
            grand_skipped += n
            print(f"      ! {n} row(s) have empty text — cannot be repaired, left as-is")
        if not embeddable:
            continue

        try:
            vecs = []
            for i in range(0, len(texts), args.batch):
                chunk = texts[i:i + args.batch]
                out = await rag_embeddings.encode_async(chunk)
                vecs.extend([list(map(float, v)) for v in out])
        except Exception as e:
            print(f"      ! embedding failed ({type(e).__name__}: {e}) — table left untouched")
            continue

        good, bad = [], 0
        for r, v in zip(embeddable, vecs):
            if len(v) == dim and any(v):
                nr = {c: r.get(c) for c in cols}
                nr["vector"] = v
                good.append(nr)
            else:
                bad += 1
        if bad:
            grand_skipped += bad
            print(f"      ! {bad} row(s) re-embedded to a zero/wrong-dim vector — NOT written")
        if not good:
            continue

        # Delete only the affected rows, then re-add them repaired. Scoped per source so a
        # partially-dead source keeps its healthy rows.
        # `chunk_index` is Int64 in the table. Quoting the values makes Lance reject the
        # filter at PLAN time ("Received literal Utf8 ... could not convert to Int64"), which
        # is a safe failure — nothing is deleted — but it repairs nothing. Emit each literal
        # in its real type.
        def _lit(v):
            return str(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else f"'{v}'"

        try:
            for sid in {r.get("source_id") for r in good}:
                idxs = [r.get("chunk_index") for r in good if r.get("source_id") == sid]
                joined = ", ".join(_lit(i) for i in idxs)
                tbl.delete(f"source_id = '{sid}' AND chunk_index IN ({joined})")
            tbl.add(good)
            grand_fixed += len(good)
            print(f"      ✓ re-embedded and rewrote {len(good)} row(s)")
        except Exception as e:
            print(f"      ! WRITE FAILED ({type(e).__name__}: {e})")
            print(f"        rows were NOT deleted if this raised before delete; verify with a "
                  f"dry run before retrying")

    print(f"\n{'-'*60}")
    print(f"zero rows found : {grand_found}")
    if args.apply:
        print(f"repaired        : {grand_fixed}")
        print(f"left as-is      : {grand_skipped}")
        print("\nRe-run without --apply to confirm the count is now 0.")
    else:
        print("DRY RUN — nothing written. Re-run with --apply to repair.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
