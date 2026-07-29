"""Wave 1 (2.2.0): the CI unit tier — wires LocalBook's pre-existing, backend-free
unit checks into pytest so they gate every push/PR.

Each item below is a standalone `check()`-based script (or module) that already lived
at backend root and exited non-zero on failure, but was never run by anything. Rather
than rewrite ~800 LOC of working assertions, we run each as a subprocess and assert a
clean exit — preserving the logic while making the tier discoverable + CI-gating.

Only the CI-PURE set runs here: tests whose import surface installs on the ubuntu CI
runner with the slim `requirements-test.txt` (no mlx/torch/lancedb/langgraph, no live
services). Verified 2026-07-28. Excluded, on purpose (local-only tier):
  - `_phase5_test.py`      → transitively needs `langgraph` (agent stack)
  - `_runprofile_test.py`  → has a "LIVE derive against real Ollama" tail

Run locally:  cd backend && python -m pytest tests/ -v
"""
import subprocess
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parent.parent

# (id, argv-relative-to-BACKEND). _eval_smoke is a package module → run with -m.
CI_PURE = [
    ("eval_smoke", ["-m", "evaluator._eval_smoke"]),
    ("scoring_fairness", ["_scoring_fairness_test.py"]),
    ("capability_probe", ["_probe_test.py"]),
    ("wave1_embed", ["_wave1_embed_test.py"]),
    ("livingview", ["_livingview_test.py"]),
    ("wave2_spacy", ["_wave2_spacy_test.py"]),  # self-SKIPs if en_core_web_sm absent
]


@pytest.mark.parametrize("argv", [c[1] for c in CI_PURE], ids=[c[0] for c in CI_PURE])
def test_unit_tier(argv):
    """Each pre-existing unit script must exit 0 (its own asserts pass)."""
    r = subprocess.run(
        [sys.executable, *argv],
        cwd=BACKEND,
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert r.returncode == 0, (
        f"unit script {argv} failed (exit {r.returncode}):\n"
        f"--- stdout ---\n{r.stdout}\n--- stderr ---\n{r.stderr}"
    )
