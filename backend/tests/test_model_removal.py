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
