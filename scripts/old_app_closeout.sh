#!/bin/sh
# The old app's half of the Phase 3 close-out, unattended: the C4 ingestion fixture, then the C6 visual golden set.
#
#     scripts/old_app_closeout.sh <notebook id>        (the old app must be running)
#
# Writes testdata/phase3/notebook/ingestion-fixture.json (open it in the Swift app: Ingestion diff › Open fixture…)
# and ~/Documents/visual-golden/old-app-visuals.{json,html} (for scripts/visual_golden_blind.py).
set -e
if [ $# -ne 1 ]; then echo "usage: scripts/old_app_closeout.sh <notebook id>"; exit 2; fi
cd "$(dirname "$0")/.."
echo "[close-out] C4: reading testdata/phase3/notebook with the old app's readers"
( cd backend && .venv/bin/python ../scripts/ingestion_diff_export.py ../testdata/phase3/notebook )
echo "[close-out] C6: drawing the 15 with the old app (this is the slow part)"
python3 scripts/visual_golden_export.py "$1" ~/Documents/visual-golden
echo "[close-out] done. Fixture: testdata/phase3/notebook/ingestion-fixture.json. Old app visuals: ~/Documents/visual-golden"
