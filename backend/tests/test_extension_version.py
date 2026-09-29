"""The extension's manifest version is the app's version.

2026-09-25: the extension zip shipped with **v2.3.0** says `"version":"2.1.1"` —
so did v2.2.0's. Chrome shows whatever the installed manifest says, so the
badge sat two releases stale. Cause: release.sh wrapped every version bump in
`if [ "$NEW_VERSION" != "$CURRENT_VERSION" ]`, and every release since v2.1.1
bumped the root package.json BY HAND first, making that test false and skipping
the whole block — extension/package.json, backend/version.py's fallback and the
README badges with it. Same skipped block, same release, as the 2.1.1 splash
screen (see test_app_version.py).

release.sh is gitignored, so its fix does not travel between the three Macs.
These assertions and `extension/scripts/sync-version.mjs` do.
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _root_version() -> str:
    return json.loads((_repo_root() / "package.json").read_text())["version"]


def _ext_pkg() -> dict:
    return json.loads((_repo_root() / "extension" / "package.json").read_text())


def test_the_extension_agrees_with_the_app():
    ext = _ext_pkg()["version"]
    root = _root_version()
    assert ext == root, (
        f"extension/package.json says {ext}, package.json says {root} — Plasmo copies "
        f"that field into the built manifest, so this is the version Chrome would show")


def test_the_build_cannot_produce_a_stale_manifest():
    """The durable half of the fix: a prebuild hook re-derives the version on every
    `npm run build`, so it holds even on a machine that never runs release.sh."""
    scripts = _ext_pkg().get("scripts", {})
    assert "sync-version.mjs" in scripts.get("prebuild", ""), (
        "extension prebuild must run scripts/sync-version.mjs — without it the manifest "
        "version is only ever as fresh as the last hand edit")
    assert (_repo_root() / "extension" / "scripts" / "sync-version.mjs").is_file()


@pytest.mark.skipif(shutil.which("node") is None, reason="node not available")
def test_the_sync_script_actually_runs():
    """Asserting a guard exists is a step; running it is the artifact. The script is
    idempotent — with the versions already in sync it reports and writes nothing."""
    ext_pkg = _repo_root() / "extension" / "package.json"
    before = ext_pkg.read_bytes()
    proc = subprocess.run(
        ["node", "scripts/sync-version.mjs"],
        cwd=_repo_root() / "extension",
        capture_output=True, text=True, timeout=30,
    )
    assert proc.returncode == 0, f"sync-version.mjs failed: {proc.stderr}"
    assert _root_version() in proc.stdout
    assert ext_pkg.read_bytes() == before, "a no-op sync must not rewrite the file"
