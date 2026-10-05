"""LB-10 item 7: dev builds get their own data directory.

`get_data_directory()` used to return the production path unconditionally, with
the comment "ensures consistent data across development and production". That
consistency was the hazard, not the feature: every script, REPL, test run and
stray `TestClient(main.app)` operated on the user's real notebooks, credentials
and keys.

It has cost this project real damage — `save_default_combo({})` once overwrote
`user_preferences.json`, the release Evaluator rebuilt the production topic model
and left it holding a 2-topic test notebook where it had held 59, and on
2026-09-29 two ad-hoc checks wrote a stray companion key and rotated
`.app_token`.

The shipped app is unaffected: frozen builds still use the production path.
"""

import sys
from pathlib import Path

import pytest

import config


@pytest.fixture
def env(monkeypatch):
    """A clean environment for each resolution, since these are read at call."""
    for key in ("LOCALBOOK_DATA_DIR", "LOCALBOOK_USE_PRODUCTION_DATA"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(config, "DEV_USING_PRODUCTION_DATA", False, raising=False)
    return monkeypatch


# ── the default ─────────────────────────────────────────────────────────────


def test_an_unfrozen_process_gets_the_dev_sandbox(env):
    """The whole point: running from backend/.venv must not touch real data."""
    env.setattr(sys, "frozen", False, raising=False)
    assert config.get_data_directory() == config.DEV_DATA_DIR
    assert config.get_data_directory() != config.PRODUCTION_DATA_DIR


def test_the_sandbox_is_a_sibling_not_a_subdirectory(env):
    """Inside the production dir it would be swept up by backups, sync and the
    encrypted volume — it must be somewhere none of those reach."""
    assert config.DEV_DATA_DIR.parent == config.PRODUCTION_DATA_DIR.parent
    assert config.PRODUCTION_DATA_DIR not in config.DEV_DATA_DIR.parents


def test_a_frozen_build_still_gets_production(env, monkeypatch):
    """Nothing here may change behaviour for the shipped app."""
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(config, "_migrate_old_data", lambda d: None)
    assert config.get_data_directory() == config.PRODUCTION_DATA_DIR


# ── the overrides ───────────────────────────────────────────────────────────


def test_an_explicit_path_wins(env, tmp_path):
    env.setenv("LOCALBOOK_DATA_DIR", str(tmp_path))
    assert config.get_data_directory() == tmp_path


def test_an_explicit_path_wins_even_for_a_frozen_build(env, tmp_path, monkeypatch):
    """LB-12 needs a dev build able to point at a scratch dir on a real machine."""
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    env.setenv("LOCALBOOK_DATA_DIR", str(tmp_path))
    assert config.get_data_directory() == tmp_path


def test_a_tilde_path_is_expanded(env):
    env.setenv("LOCALBOOK_DATA_DIR", "~/lb-scratch")
    assert config.get_data_directory() == Path.home() / "lb-scratch"


def test_a_blank_override_is_ignored_rather_than_becoming_cwd(env):
    """An empty env var must not resolve to Path('') — that is the working
    directory, which for a bundled app is read-only and for a dev run is the
    repo."""
    env.setattr(sys, "frozen", False, raising=False)
    env.setenv("LOCALBOOK_DATA_DIR", "   ")
    assert config.get_data_directory() == config.DEV_DATA_DIR


@pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes"])
def test_opting_in_to_production_works_and_is_recorded(env, value):
    env.setattr(sys, "frozen", False, raising=False)
    env.setenv("LOCALBOOK_USE_PRODUCTION_DATA", value)

    assert config.get_data_directory() == config.PRODUCTION_DATA_DIR
    # The flag is what main.py prints a banner from. Opting in deliberately is
    # fine; doing it without noticing is the failure mode.
    assert config.DEV_USING_PRODUCTION_DATA is True


@pytest.mark.parametrize("value", ["0", "false", "no", ""])
def test_a_falsey_opt_in_does_not_count(env, value):
    env.setattr(sys, "frozen", False, raising=False)
    env.setenv("LOCALBOOK_USE_PRODUCTION_DATA", value)
    assert config.get_data_directory() == config.DEV_DATA_DIR
    assert config.DEV_USING_PRODUCTION_DATA is False


def test_the_opt_in_flag_is_not_set_by_an_ordinary_resolution(env):
    """A banner that is on by default is a banner nobody reads.

    Deliberately NOT `importlib.reload(config)`: reloading rebinds
    `config.settings` to a brand-new object while every module that did
    `from config import settings` keeps the old one, so later tests monkeypatch
    a different object than the code reads. That polluted five unrelated tests
    in test_folder_links and test_mlx_idle_eviction, which passed alone and
    failed in a full run — the classic shape.
    """
    env.setattr(sys, "frozen", False, raising=False)
    config.get_data_directory()
    assert config.DEV_USING_PRODUCTION_DATA is False


# ── the thing this protects ─────────────────────────────────────────────────


def test_the_default_settings_object_is_not_pointed_at_production(env):
    """The end-to-end property, asserted on the real `settings` singleton the
    rest of the app reads — not on the resolver in isolation.

    If this ever fails, every test in the suite is writing to the user's data.
    """
    from config import settings

    if getattr(sys, "frozen", False):  # pragma: no cover — never true under pytest
        pytest.skip("frozen build")
    assert Path(settings.data_dir) != config.PRODUCTION_DATA_DIR
