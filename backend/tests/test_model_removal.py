"""Removing a downloaded model.

Testing models means sometimes deciding against one, and a 16 GB checkpoint
that lost a bake-off should not have to be hunted down in ~/.cache by hand.

The entire risk is deleting something the app is standing on. A model assigned
to a role is refused outright: removing it leaves the app pointing at weights
that no longer exist, and that failure surfaces later as a broken chat rather
than as a refused deletion — much harder to connect back to its cause.
"""
import pytest

from services import mlx_download


def test_a_model_in_use_names_the_role_using_it():
    from config import settings
    roles = mlx_download.roles_using(settings.main_model)
    assert "main" in roles


def test_an_unused_model_is_free_to_remove():
    assert mlx_download.roles_using("mlx-community/some-model-nobody-picked") == []


def test_refusing_is_the_default_when_we_cannot_tell(monkeypatch):
    """Refusing a safe deletion costs a click; allowing an unsafe one costs the
    running app. The asymmetry decides the default."""
    import config
    monkeypatch.setattr(config, "settings", None)
    assert mlx_download.roles_using("anything") == ["unknown"]




def test_deleting_a_role_model_is_refused_with_a_reason():
    import asyncio
    from config import settings
    r = asyncio.run(mlx_download.delete_model(settings.main_model))
    assert r["ok"] is False
    assert "main" in r["error"]
    assert r["in_use_by"]


def test_deleting_something_not_cached_says_so():
    import asyncio
    r = asyncio.run(mlx_download.delete_model("mlx-community/definitely-not-here"))
    assert r["ok"] is False
    assert "not in the local cache" in r["error"] or "Could not remove" in r["error"]


def test_nothing_is_deleted_without_a_model_id():
    import asyncio
    assert asyncio.run(mlx_download.delete_model(""))["ok"] is False


def test_the_model_is_unloaded_before_its_files_go():
    """Deleting files out from under a resident model leaves the process
    holding mappings into a file that no longer exists."""
    import ast
    import inspect
    src = inspect.getsource(mlx_download.delete_model)
    tree = ast.parse(src.lstrip())
    body = "\n".join(l for l in src.splitlines() if not l.strip().startswith("#"))
    assert body.index("unload") < body.index("scan_cache_dir")


def test_removal_is_verified_not_assumed():
    """The rule this project keeps relearning: check the artifact, not the call."""
    import inspect
    src = inspect.getsource(mlx_download.delete_model)
    assert "still appears in the cache" in src


def test_removal_is_offered_where_installed_models_live():
    """2026-09-23: the button went into the model BROWSER, which lists what is
    on Hugging Face. The Locker is what is on THIS MAC — and so it is where
    someone who has just tested a model and decided against it looks for the
    way to remove it. Browse was the wrong surface for the request."""
    from pathlib import Path
    locker = (Path(__file__).resolve().parents[2]
              / "src" / "components" / "LLMSelector.tsx").read_text()
    assert "handleRemove" in locker
    assert "settings/mlx/models/" in locker


def test_the_locker_identifies_models_the_way_the_endpoint_expects():
    """The Locker keys rows by `m.name`. If that were a display name rather
    than the repo id, every delete would 404."""
    from pathlib import Path
    api = (Path(__file__).resolve().parents[1] / "api" / "settings.py").read_text()
    assert '"name": _mid' in api, "the Locker card no longer carries the repo id as `name`"


def test_an_active_model_cannot_be_removed_from_the_locker_ui():
    """Belt and braces with the backend refusal: the button is disabled for the
    active model, so the common case never becomes an error message."""
    from pathlib import Path
    locker = (Path(__file__).resolve().parents[2]
              / "src" / "components" / "LLMSelector.tsx").read_text()
    assert "disabled={removing === m.name || isActive}" in locker


# ── the refresh (2026-09-23) ────────────────────────────────────────────────
#
# "I click remove, nothing happens. I click again and it says not found in
# cache. I close the window and reopen and it's gone."
#
# The delete worked every time. THREE separate caches remember what is
# installed, and only one was being cleared, so the refreshed list still
# contained a model that no longer existed:
#
#   api.settings._ollama_models_cache  — the whole endpoint response, 30s
#   model_presence._CACHE["enum"]      — the cache-directory scan, 30s
#   model_sizing._CACHE["w::<id>"]     — per-model weight size
#
# The second click then hit a real "not in the local cache", which read as a
# different bug entirely.

def test_deleting_clears_every_cache_that_remembers_the_model():
    import inspect
    from services.mlx_download import delete_model
    src = inspect.getsource(delete_model)
    for module in ("services.model_sizing", "services.model_presence", "api.settings"):
        assert module in src, f"{module} still holds a stale view after a delete"


def test_the_endpoint_cache_can_actually_be_invalidated():
    """It was a module-level dict with no way to clear it — the only remedy was
    waiting out the TTL, which is exactly what "close and reopen" was doing."""
    from api import settings as api_settings
    api_settings._ollama_models_cache["ts"] = 12345
    api_settings._ollama_models_cache["data"] = {"models": ["stale"]}
    api_settings.invalidate_models_cache()
    assert api_settings._ollama_models_cache["ts"] is None
    assert api_settings._ollama_models_cache["data"] is None


def test_presence_and_sizing_caches_clear_together():
    from services import model_presence, model_sizing
    model_presence.enumerate_cached(force=True)
    model_sizing._CACHE["w::probe"] = 1.0
    model_presence.reset_cache()
    model_sizing.reset_cache()
    assert not model_presence._CACHE and not model_sizing._CACHE


def test_the_row_disappears_without_waiting_for_the_round_trip():
    """A second of "nothing happened" is what made the first click look broken
    and invited a second one."""
    from pathlib import Path
    locker = (Path(__file__).resolve().parents[2]
              / "src" / "components" / "LLMSelector.tsx").read_text()
    assert "setModels((prev) => prev.filter((x) => x.name !== m.name));" in locker
