"""The backend log is scoped to the data dir like the Keychain service is."""

from pathlib import Path

from utils import logging_config


def test_production_logs_where_it_always_has(monkeypatch):
    import config

    monkeypatch.delenv("LOCALBOOK_LOG_DIR", raising=False)
    monkeypatch.setattr(config, "get_data_directory", lambda: config.PRODUCTION_DATA_DIR)
    base = Path.home() / "Library" / "Logs" / "LocalBook"
    assert logging_config._scoped_to_data_dir(base) == base


def test_any_other_data_dir_gets_its_own_log_folder(monkeypatch, tmp_path):
    import config

    # Not under a temp dir (the dev sandbox, a second install): its own folder under base.
    other = Path.home() / "LocalBook-scoping-test-never-created" / "LocalBook"
    monkeypatch.setattr(config, "get_data_directory", lambda: other)
    base = tmp_path / "logs"
    scoped = logging_config._scoped_to_data_dir(base)
    assert scoped != base and scoped.parent == base and scoped.name.startswith("LocalBook.")


def test_a_throwaway_data_dir_logs_beside_itself(monkeypatch, tmp_path):
    """An end-to-end run's data dir lives in a temp dir; its logs go with it instead of
    leaving a folder in ~/Library/Logs per run (91 had piled up, 2026-10-15)."""
    import config

    monkeypatch.setattr(config, "get_data_directory", lambda: tmp_path / "LocalBook")
    scoped = logging_config._scoped_to_data_dir(tmp_path / "logs")
    assert scoped == (tmp_path / "LocalBook").resolve().parent / "LocalBook.logs"


def test_an_explicit_log_dir_still_wins(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCALBOOK_LOG_DIR", str(tmp_path / "explicit"))
    assert logging_config._resolve_log_path() == tmp_path / "explicit" / "backend.log"
