"""Read-only: do this Mac's stores agree on which notebooks exist?

  LOCALBOOK_USE_PRODUCTION_DATA=1 .venv/bin/python scripts/data_integrity.py

Compares the live notebook list (localbook.db) with what is on disk and reports leftovers from deleted
notebooks: vector tables (the expensive one — every re-embedding migration walks ALL of them), notebook data
folders, entity / graph / community entries and source rows. Writes nothing.

  --drop-empty-tables   after a backup of lancedb/, delete vector tables that belong to no notebook AND hold
                        no real rows (placeholders). Tables that hold data are only ever reported.
"""
import argparse
import json
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config import settings


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--drop-empty-tables", action="store_true")
    args = ap.parse_args()
    home = Path(settings.data_dir)
    print(f"data dir: {home}\n")

    conn = sqlite3.connect(f"file:{home / 'localbook.db'}?mode=ro", uri=True)
    live = {r[0]: r[1] for r in conn.execute("SELECT id, title FROM notebooks")}
    print(f"live notebooks: {len(live)}")

    import lancedb
    db_dir = home / "lancedb"
    db = lancedb.connect(str(db_dir))
    dirs = sorted(p.name[:-len('.lance')][len('notebook_'):] for p in db_dir.glob("notebook_*.lance"))
    print(f"vector tables on disk: {len(dirs)}")
    rows = {}
    for nb in dirs:
        try:
            t = db.open_table(f"notebook_{nb}")
            n = t.count_rows()
            real = n - (len(t.search().where("source_id = 'placeholder'").limit(10).to_list()) if n else 0)
            rows[nb] = max(real, 0)
        except Exception:
            rows[nb] = -1
    live_t = [nb for nb in dirs if nb in live]
    orphan_data = [nb for nb in dirs if nb not in live and rows[nb] > 0]
    orphan_empty = [nb for nb in dirs if nb not in live and rows[nb] == 0]
    unreadable = [nb for nb in dirs if nb not in live and rows[nb] < 0]
    print(f"  belonging to a live notebook: {len(live_t)}   (live notebooks without a table: {len([n for n in live if n not in dirs])})")
    print(f"  orphan, EMPTY (placeholders):  {len(orphan_empty)}")
    print(f"  orphan, holding data:          {len(orphan_data)}  ({sum(rows[n] for n in orphan_data)} chunks)")
    if unreadable:
        print(f"  orphan, unreadable:            {len(unreadable)}")
    # Who owns the chunks in an orphan table? If its source ids exist in the sources table (under ANY
    # notebook), the notebook was re-id'd or the sources moved — the table is NOT safe to treat as garbage.
    src_owner = {sid: nbid for sid, nbid in conn.execute("SELECT id, notebook_id FROM sources")}
    src_name = {sid: fn for sid, fn in conn.execute("SELECT id, filename FROM sources")}
    claimed = {}
    for nb in orphan_data:
        t = db.open_table(f"notebook_{nb}")
        sids = {r["source_id"] for r in t.search().select(["source_id"]).limit(t.count_rows()).to_list()}
        sids.discard("placeholder")
        known = {sid for sid in sids if sid in src_owner}
        claimed[nb] = (len(sids), known)
    print("\n  orphan tables whose chunks belong to sources that STILL EXIST (do not delete these):")
    hit = [(nb, c) for nb, c in claimed.items() if c[1]]
    for nb, (total, known) in sorted(hit, key=lambda kv: -kv[1][0])[:25]:
        owners = {src_owner[sid][:8] for sid in known}
        ex = next(iter(known))
        print(f"      {nb[:8]}…  {rows[nb]} chunks, {len(known)}/{total} sources still exist, owned by notebook(s) {sorted(owners)}  e.g. {src_name.get(ex, '?')[:40]}")
    if not hit:
        print("      none")
    print(f"\n  truly dead (no source anywhere refers to them): {len([nb for nb, c in claimed.items() if not c[1]])} tables, "
          f"{sum(rows[nb] for nb, c in claimed.items() if not c[1])} chunks")
    for nb in [n for n, c in claimed.items() if not c[1]][:15]:
        print(f"      {nb[:8]}…  {rows[nb]} chunks")
    missing = [n for n in live if n not in dirs]
    if missing:
        print("\n  live notebooks WITHOUT a vector table (their sources cannot be searched):")
        for n in missing:
            c = conn.execute("SELECT COUNT(*) FROM sources WHERE notebook_id=?", (n,)).fetchone()[0]
            print(f"      {n[:8]}…  \"{live[n]}\"  sources={c}")

    nb_dirs = [p.name for p in (home / "notebooks").glob("*") if p.is_dir()] if (home / "notebooks").exists() else []
    print(f"\nnotebook data folders: {len(nb_dirs)}, not belonging to a live notebook: {len([d for d in nb_dirs if d not in live])}")
    src_orphans = conn.execute("SELECT COUNT(*) FROM sources WHERE notebook_id NOT IN (SELECT id FROM notebooks)").fetchone()[0]
    print(f"source rows whose notebook is gone: {src_orphans}")
    for fname, key in (("entities.json", None), ("entity_graph.json", "relationships"), ("communities.json", None)):
        p = home / fname
        if not p.exists():
            continue
        try:
            data = json.loads(p.read_text())
            keys = data.get(key, {}) if key else data
            keys = keys if isinstance(keys, dict) else {}
            print(f"{fname}: {len(keys)} notebook entries, {len([k for k in keys if k not in live])} not live")
        except Exception as exc:
            print(f"{fname}: unreadable ({exc})")

    if args.drop_empty_tables and orphan_empty:
        backup = home / f"lancedb_backup_{datetime.now():%Y%m%d_%H%M%S}_before_orphan_cleanup"
        shutil.copytree(db_dir, backup)
        print(f"\nbackup: {backup.name}")
        for nb in orphan_empty:
            shutil.rmtree(db_dir / f"notebook_{nb}.lance", ignore_errors=True)
        print(f"dropped {len(orphan_empty)} empty orphan table(s)")
    elif orphan_empty or orphan_data:
        print("\n(nothing changed; --drop-empty-tables removes only the empty placeholders, after a backup)")


if __name__ == "__main__":
    main()
