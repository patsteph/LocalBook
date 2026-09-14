"""No undefined names anywhere in the backend.

Five of these shipped at once, all the same shape: a refactor deleted a definition and left a
reader behind. Python does not complain until the line actually executes, so each one sat in
the build looking fine:

  - `_mlx_embed` (evaluator) — the config collapse deleted `settings.embed_engine`; EVERY
    evaluation run then died with `name '_mlx_embed' is not defined` before its first assertion.
  - `engines` (api/system.py) — same collapse. The NameError landed inside a bare `except`, so
    `/system/model-readiness` could report ready with a dead MLX engine, on the endpoint the
    debugging playbook sends you to first.
  - `_HTML_TEMPLATE` / `_browser_lock` (mermaid_renderer) — the shared-browser refactor moved
    the browser out and deleted the template; mermaid diagrams in exports had been raising ever
    since.
  - `logger` (health_portal) — eight call sites, no import. NameErrors in the diagnostic path,
    which is the worst place for one: the lifeboat has to work when everything else is broken.
  - `List` (api/correspondent.py) — pydantic could not build `BatchApproveRequest`, so
    queue/batch-approve failed at request time.

The tests already passing did not catch any of them, because a NameError on a line no test
executes is invisible to pytest. This check is static, so it does not care.
"""
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
TARGETS = ["services", "api", "agents", "evaluator", "storage", "utils", "main.py"]


def test_no_undefined_names_in_backend_source():
    pyflakes = pytest.importorskip("pyflakes.api", reason="pyflakes not installed")
    from pyflakes.reporter import Reporter
    import io

    out, err = io.StringIO(), io.StringIO()
    for target in TARGETS:
        path = BACKEND / target
        if path.is_dir():
            pyflakes.checkRecursive([str(path)], Reporter(out, err))
        else:
            pyflakes.checkPath(str(path), Reporter(out, err))

    offenders = [
        line for line in out.getvalue().splitlines()
        if "undefined name" in line
        # `from X import *` defeats static analysis — pyflakes says so rather than naming a
        # symbol. The curator/collector/chat packages use it deliberately; not our target.
        and "import *" not in line
    ]
    assert not offenders, (
        "undefined name(s) — each is a NameError waiting on a line no test happens to run:\n  "
        + "\n  ".join(offenders)
    )
