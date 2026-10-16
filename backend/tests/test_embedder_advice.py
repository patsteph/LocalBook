"""A device that searches with a different embedder than its library is told so (incl. the iPhone app)."""

import pytest

from services.sync import embedder_advice as ea

GEMMA, ARCTIC = "mlx-community/embeddinggemma-2-bf16", "mlx-community/snowflake-arctic-embed-l-v2.0-bf16"


@pytest.fixture
def lib(monkeypatch):
    from config import settings
    from services import embedding_migration
    from services.sync import roles, store

    state = {"peers": [], "collector": None, "present": True, "mig": {}}
    monkeypatch.setattr(store, "enabled", lambda: True)
    monkeypatch.setattr(ea, "_peers", lambda: state["peers"])
    monkeypatch.setattr(roles, "collector", lambda: state["collector"])
    monkeypatch.setattr("services.model_presence.is_present", lambda m: state["present"])
    monkeypatch.setattr(embedding_migration, "status", lambda: state["mig"])
    monkeypatch.setattr(settings, "embedding_model", ARCTIC)
    return state


def _peer(i, model):
    return {"device_id": f"d{i}", "name": f"Mac {i}", "model": model}


def test_no_advice_when_this_device_matches_its_library(lib):
    lib["peers"] = [_peer(1, ARCTIC)]
    assert ea.advice() is None


def test_a_device_behind_its_library_is_told_to_download_and_reindex(lib):
    lib["peers"] = [_peer(1, GEMMA), _peer(2, GEMMA)]
    lib["present"] = False
    a = ea.advice()
    assert a["library_model"] == GEMMA and a["devices"] == ["Mac 1", "Mac 2"]
    assert a["downloaded"] is False and "Download embeddinggemma-2-bf16 and re-index" in a["message"]


def test_once_downloaded_it_is_told_to_switch_and_then_that_it_is_in_progress(lib):
    lib["peers"] = [_peer(1, GEMMA), _peer(2, GEMMA)]
    assert "Switch this one" in ea.advice()["message"]
    lib["mig"] = {"target": GEMMA, "running": True}
    a = ea.advice()
    assert a["in_progress"] and "being prepared" in a["message"]


def test_a_tie_goes_to_the_collector_macs_model(lib):
    lib["peers"] = [_peer(1, GEMMA)]                     # this device arctic, one peer gemma
    lib["collector"] = {"device_id": "d1"}
    assert ea.advice()["library_model"] == GEMMA
    lib["collector"] = {"device_id": "elsewhere"}
    assert ea.advice() is None                           # no collector vote → keep this one


def test_nothing_is_said_with_sync_off(lib, monkeypatch):
    from services.sync import store
    monkeypatch.setattr(store, "enabled", lambda: False)
    lib["peers"] = [_peer(1, GEMMA), _peer(2, GEMMA)]
    assert ea.advice() is None
