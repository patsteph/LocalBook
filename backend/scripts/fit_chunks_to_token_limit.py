"""Bring stored chunks inside the embedder's shared token limit — by hand.

LocalBook now does this itself once at startup (services/chunk_limit_migration.py); this is
the manual version: a DRY RUN that lists exactly what would change, and `--apply` to run it
with the app closed (two writers on one LanceDB folder). `--apply` backs up each table it
touches to `lancedb_backup_chunklimit/` first.

    cd backend && LOCALBOOK_DATA_DIR="$HOME/Library/Application Support/LocalBook" \
        .venv/bin/python scripts/fit_chunks_to_token_limit.py [--apply]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--apply", action="store_true", help="write the changes (default: dry run)")
    args = ap.parse_args()

    import lancedb
    from config import settings
    from services import chunk_limit_migration as clm, embedding_profile
    from services import embedding_migration as em

    # The app overlays user_preferences.json onto settings at startup (main.py); do the same,
    # or this reports the config default instead of the model the index was built with.
    prefs = os.path.join(str(settings.data_dir), "user_preferences.json")
    if os.path.exists(prefs):
        chosen = (json.load(open(prefs)).get("default_combo") or {}).get("embedding_model")
        if chosen:
            settings.embedding_model = chosen
    model = settings.embedding_model
    prof = embedding_profile.for_model(model)
    if prof.max_tokens <= 0:
        sys.exit(f"❌ {model} has no max_tokens limit — nothing to fit")

    live = em._live_dir()
    db = lancedb.connect(str(live))
    print(f"Embedder {model}, limit {prof.max_tokens}, index {live}")
    total = {"near": 0, "split": 0}
    for name in em._tables(live):
        for item in clm.plan_table(db.open_table(name), model):
            total[item["kind"]] += 1
    print(f"{total['near'] + total['split']} rows over the limit: "
          f"{total['near']} re-embed in place, {total['split']} split")
    if not args.apply:
        print("Dry run — nothing written. Quit LocalBook, then re-run with --apply.")
        return
    if subprocess.run(["pgrep", "-f", "LocalBook.app/Contents/Resources/resources/backend"],
                      capture_output=True).stdout.strip():
        sys.exit("❌ LocalBook is running — quit it first (two writers on one index)")

    from services.mlx_engine import mlx_engine
    if not mlx_engine.available():
        sys.exit("❌ the MLX engine is not available in this process")

    async def embed(texts):
        return await asyncio.to_thread(mlx_engine.embed_sync, texts, model=model)

    print(asyncio.run(clm.run(embed, force=True)))


if __name__ == "__main__":
    main()
