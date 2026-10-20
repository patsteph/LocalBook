"""Read-only: how ready is THIS Mac's library for the iPhone companion?

  LOCALBOOK_USE_PRODUCTION_DATA=1 .venv/bin/python scripts/phone_readiness.py

Prints, per notebook: chunks, text size, chunks over the embedder's token limit (the phone refuses to sync
vectors that were not made from the text it receives; services/chunk_limit_migration fixes them at app
start), and the estimates that matter on the phone: sync payload and the memory one notebook needs.
Writes nothing.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import lancedb
from config import settings

prefs = Path(settings.data_dir) / "user_preferences.json"
if prefs.exists():
    settings.embedding_model = json.loads(prefs.read_text()).get("embedding_model") or settings.embedding_model
from services import chunk_limit_migration as m

db = lancedb.connect(str(Path(settings.data_dir) / "lancedb"))
total_rows = total_text = total_over = 0
biggest = 0
print(f"data dir: {settings.data_dir}\nembedder: {settings.embedding_model}\n")
for name in sorted(db.table_names()):
    if not name.startswith("notebook_"):
        continue
    t = db.open_table(name)
    rows = t.count_rows()
    text = sum(len(r["text"] or "") for r in t.search().select(["text"]).limit(rows).to_list())
    try:
        plan = m.plan_table(t, settings.embedding_model)
        over = len([p for p in plan if p["kind"] != "near"])
    except Exception as exc:
        over = -1
        print(f"  (could not plan {name[9:17]}: {exc})")
    print(f"{name[9:17]}…  chunks={rows:6d}  text={text / 1e6:6.2f} MB  over-limit={over}  phone memory when searched≈{rows * 768 * 4 / 1e6:5.1f} MB")
    total_rows += rows; total_text += text; total_over += max(over, 0); biggest = max(biggest, rows)
print(f"\nTOTAL chunks={total_rows}  text={total_text / 1e6:.1f} MB  over-limit={total_over}")
print(f"first sync payload ≈ {(total_rows * 2450 + total_text) / 1e6:.0f} MB (about {max(1, round((total_rows * 2450 + total_text) / 1e6 / 2.7))} s on the Wi-Fi measured earlier)")
print(f"phone memory for the largest notebook ≈ {biggest * 768 * 4 / 1e6:.0f} MB (only one notebook is held at a time)")
print("OK to sync" if total_over == 0 else "Run the app once first: it fixes over-limit chunks at start-up, then check again")
