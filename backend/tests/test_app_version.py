"""The version the app reports is the version the app is.

2026-09-23: the splash screen showed **2.1.1** while package.json,
tauri.conf.json and Cargo.toml all said 2.3.0. `backend/version.py` hardcoded
it, was the backend's only version source, and had been missed by the last two
releases. A constant that must be edited in lockstep with four other files
eventually will not be — and was not.
"""
import json
from pathlib import Path


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def test_the_backend_agrees_with_the_manifests():
    from version import APP_VERSION
    pkg = json.loads((_repo_root() / "package.json").read_text())["version"]
    assert APP_VERSION == pkg, (
        f"backend reports {APP_VERSION}, package.json says {pkg} — this is what "
        f"put 2.1.1 on the splash screen of a 2.3.0 build")


def test_every_manifest_agrees_with_every_other():
    root = _repo_root()
    pkg = json.loads((root / "package.json").read_text())["version"]
    tauri = json.loads((root / "src-tauri" / "tauri.conf.json").read_text())["version"]
    cargo = next(l.split('"')[1] for l in
                 (root / "src-tauri" / "Cargo.toml").read_text().splitlines()
                 if l.startswith("version"))
    assert pkg == tauri == cargo, f"package={pkg} tauri={tauri} cargo={cargo}"


def test_the_version_is_derived_not_declared():
    """The fix is structural: a bundle knows its own version, and a checkout can
    read its manifests. The constant is only a last resort."""
    import inspect
    import version
    src = inspect.getsource(version)
    assert "_from_bundle" in src and "_from_manifest" in src
    assert src.index("_FALLBACK = ") < src.index("APP_VERSION = ")


def test_the_fallback_is_not_stale_either():
    """Reached only when both lookups fail, but a wrong value there is exactly
    the bug we just fixed."""
    import version
    pkg = json.loads((_repo_root() / "package.json").read_text())["version"]
    assert version._FALLBACK == pkg
