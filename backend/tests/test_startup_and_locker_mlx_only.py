"""Three failures found by running the built app on 2026-08-20, all user-reported.

1. **Startup hung with no Ollama models.** The Tauri shell started `ollama serve` and then
   BLOCKED the launch pulling a hardcoded REQUIRED_MODELS list — still naming
   `olmo-3:7b-instruct`, retired months earlier — before the backend was spawned at all.
   Deleting gemma/phi from Ollama therefore bricked startup on a multi-GB download of models
   the app no longer uses.
2. **LLM Studio defaulted to the Ollama tab** and offered Ollama model cards, none of which
   anything can load.
3. **LLM Studio listed models that were not downloaded**, because presence was
   `try_to_load_from_cache(id, "config.json")` — true for a download that fetched the config
   and then died.

The Rust ones are asserted against lib.rs SOURCE: there is no way to exercise the Tauri shell
from pytest, and an untested startup path is exactly how #1 shipped.
"""
import json
import os

import pytest

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


def _read(rel: str) -> str:
    with open(os.path.join(REPO, rel)) as fh:
        return fh.read()


# ── 1. Nothing may block launch ─────────────────────────────────────────────────

def test_the_tauri_shell_does_not_start_or_probe_ollama():
    src = _read("src-tauri/src/lib.rs")
    code = "\n".join(l for l in src.splitlines() if not l.strip().startswith("//"))
    for banned in ("ensure_ollama_running", "ensure_required_models", "REQUIRED_MODELS",
                   "pull_ollama_model", "11434"):
        assert banned not in code, f"{banned} is back in the startup path"


def test_no_model_is_mandatory_at_launch():
    """Wave 9 decision #1: never auto-download at boot. The shell must not pull anything, and
    the backend's model check must only REPORT."""
    src = _read("backend/services/startup_checks.py")
    assert "check_models_present" in src
    code = "\n".join(l for l in src.splitlines() if not l.strip().startswith("#"))
    assert "/api/pull" not in code and "ollama pull" not in code


def test_startup_model_check_never_raises(monkeypatch):
    """A model check that raises stops the app booting — strictly worse than stale info."""
    import asyncio

    from services import startup_checks as sc

    def boom(_m):
        raise RuntimeError("cache exploded")

    monkeypatch.setattr("services.model_presence.is_present", boom)
    available, missing = asyncio.run(sc.check_models_present())
    assert isinstance(available, list) and isinstance(missing, list)


def test_required_models_are_resolved_at_call_time_not_import_time():
    """A role repointed in the Locker must be reflected without a restart — an import-time
    snapshot keeps checking the model the user just moved away from."""
    from config import settings
    from services import startup_checks as sc

    original = settings.mlx_fast_model
    try:
        settings.mlx_fast_model = "mlx-community/some-other-model"
        assert any("some-other-model" in name for name, _ in sc._required_models())
    finally:
        settings.mlx_fast_model = original


# ── 2 & 3. The Locker lists only what can actually run ──────────────────────────

def test_the_locker_lists_only_downloaded_mlx_models():
    import asyncio

    import api.settings as st

    st._ollama_models_cache["ts"] = None
    resp = asyncio.run(st.get_ollama_models())
    models = resp.get("models", resp) if isinstance(resp, dict) else resp

    assert models, "the Locker must not be empty on a machine with models downloaded"
    for m in models:
        assert m.get("provider") == "mlx", f"non-MLX card offered: {m.get('name')}"
        assert m.get("installed") is not False, f"undownloaded card offered: {m.get('name')}"


def test_the_locker_does_not_touch_the_network():
    """`hf_hub_download` revalidates against huggingface.co even for a cached file. The model
    list must be answerable with the machine offline — it is a filesystem question, and
    LocalBook's premise is that nothing leaves the Mac."""
    import asyncio

    import api.settings as st

    src = _read("backend/evaluator/capability_probe.py")
    code = "\n".join(l for l in src.splitlines() if not l.strip().startswith("#"))
    assert "hf_hub_download" not in code, "capability probing must read the cache, not the Hub"

    st._ollama_models_cache["ts"] = None
    resp = asyncio.run(st.get_ollama_models())
    models = resp.get("models", resp) if isinstance(resp, dict) else resp
    assert all(m.get("context_window") for m in models), "cached configs should still yield ctx"


def test_the_frontend_has_no_engine_toggle_and_no_ollama_cards():
    src = _read("src/components/LLMSelector.tsx")
    assert "engineFilter" not in src, "the Ollama/MLX toggle is back"
    assert "🦙 Ollama" not in src
    # The filter that makes rule 2 and 3 true.
    assert "m.provider !== 'mlx'" in src
    assert "m.installed === false" in src


def test_presence_requires_weights_not_just_a_config_file():
    """A half-finished download leaves config.json behind. Reading that as installed is how a
    role gets pointed at a model whose weights never arrived."""
    src = _read("backend/api/settings.py")
    code = "\n".join(l for l in src.splitlines() if not l.strip().startswith("#"))
    # Scoped to the presence decision — `try_to_load_from_cache` is still legitimately used
    # by the cache-size helper, where "is the config there" is the right question.
    assert "_is_present(_mid)" in code
    assert '_installed = _tlfc(' not in code


# ── Shell scripts (2026-08-20) ──────────────────────────────────────────────────

def _sh(rel: str) -> str:
    """Script source with comment-only lines stripped, so prose about the removal
    doesn't read as the removed code."""
    return "\n".join(l for l in _read(rel).splitlines() if not l.strip().startswith("#"))


def test_no_script_pulls_or_starts_ollama():
    """`build.sh` ran three `ollama pull`s AFTER bundling. Once those models were deleted
    the pull returned 412 and failed the build, even though the app had bundled fine — a
    green build reported red. `start.sh` additionally `exit 1`d when the ollama binary was
    absent, which is now the normal state.
    """
    for script in ("build.sh", "start.sh", "release.sh", "install.sh"):
        code = _sh(script)
        assert "ollama pull" not in code, f"{script} still pulls models"
        assert "ollama serve" not in code, f"{script} still starts a daemon"
        assert "brew install ollama" not in code, f"{script} still installs Ollama"
        assert "11434" not in code, f"{script} still probes the Ollama port"


def test_the_rag_v3_sentinel_still_writes():
    """THE fragile edit in install.sh. The RAG V3 upgrade sat inside `if curl ollama`; with
    Ollama gone that branch never runs, so the upgrade is skipped forever AND
    `.rag_v3_upgraded` is never written — every later upgrade retries and re-skips. Dropping
    the guard has to keep the body, sentinel included."""
    code = _sh("install.sh")
    assert 'upgrade_rag_v3.py' in code
    assert '> "$rag_v3_marker"' in code, "the sentinel write was lost with the guard"
    assert "if true; then" not in code, "leftover no-op guard"
