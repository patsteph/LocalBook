"""Every backend module must at least parse.

2026-09-23, caught by release.sh's pre-flight rather than by the test suite:
`services/multimodal_extractor.py` had a SyntaxError — an import placed at
column 0 inside a `try:` block by a scripted edit — and 1143 tests passed
anyway.

Two reasons it got through, and both are worth naming:

  * Nothing imports it. It is lazily imported in production (the standing rule
    for heavy dependencies), so no test ever loaded it.

  * The test that DID cover the change read the file as TEXT and grepped it.
    `test_production_vision_callers_use_the_seam` confirmed the seam was being
    used without ever discovering the file could not be parsed — checking the
    text rather than the artifact, which is the mistake this codebase keeps
    relearning.

release.sh runs this check too, but only at release time. That is where it
surfaced, mid-release, which is the most expensive place to find it.
"""
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
SKIP_DIRS = {".venv", "build", "dist", "__pycache__", ".pytest_cache", "node_modules"}


def _modules():
    for p in sorted(BACKEND.rglob("*.py")):
        if any(part in SKIP_DIRS for part in p.parts):
            continue
        yield p


def test_there_are_modules_to_check():
    """A glob that silently matches nothing would make this file a no-op that
    reports success forever."""
    assert len(list(_modules())) > 100


@pytest.mark.parametrize("path", list(_modules()), ids=lambda p: str(p.relative_to(BACKEND)))
def test_module_parses(path):
    import ast
    source = path.read_text(encoding="utf-8", errors="replace")
    try:
        ast.parse(source, filename=str(path))
    except SyntaxError as e:
        pytest.fail(f"{path.relative_to(BACKEND)}:{e.lineno}: {e.msg}\n"
                    f"    {(e.text or '').rstrip()}")
