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

    monkeypatch.setattr(config, "get_data_directory", lambda: tmp_path / "LocalBook")
    base = tmp_path / "logs"
    scoped = logging_config._scoped_to_data_dir(base)
    assert scoped != base and scoped.parent == base and scoped.name.startswith("LocalBook.")


def test_an_explicit_log_dir_still_wins(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCALBOOK_LOG_DIR", str(tmp_path / "explicit"))
    assert logging_config._resolve_log_path() == tmp_path / "explicit" / "backend.log"
