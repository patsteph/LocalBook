"""Background browser work uses the ONE shared headless browser.

2026-10-01: a collection on the MBP put 10-12 bouncing icons in the Dock. The web
scraper launched a chromium per page, and on macOS every chromium launch registers
with LaunchServices and bounces before settling as a background element. One
long-lived browser (`playwright_utils.get_shared_browser`) bounces once, at most.
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# social_auth opens a VISIBLE window on purpose — the user signs in there.
ALLOWED = {"services/playwright_utils.py", "services/social_auth.py"}


def test_no_per_call_chromium_launch():
    offenders = []
    for p in list((ROOT / "services").rglob("*.py")) + list((ROOT / "api").rglob("*.py")) \
            + list((ROOT / "agents").rglob("*.py")):
        rel = p.relative_to(ROOT).as_posix()
        if rel in ALLOWED:
            continue
        if re.search(r"chromium\.launch\(", p.read_text(errors="ignore")):
            offenders.append(rel)
    assert not offenders, f"launch a browser via get_shared_browser() instead: {offenders}"


def test_the_frozen_backend_never_boots_for_an_interpreter_invocation():
    """joblib's loky pool starts workers as `sys.executable -m ...`; in the frozen app
    that is the LocalBook binary, which booted a whole backend per worker (a Dock icon
    and a Keychain prompt each). main.py must refuse those and keep joblib in-process."""
    src = (ROOT / "main.py").read_text()
    head = src[:src.index("# ── Fix SSL certificates")]
    assert 'sys.argv[1] in ("-m", "-c")' in head and "sys.exit(" in head
    assert 'os.environ.setdefault("JOBLIB_MULTIPROCESSING", "0")' in head
