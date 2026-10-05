"""One Mac runs scheduled collections once Macs share a library (LB-12).

Collector settings sync, but each Mac kept its own schedule, so every Mac collected
every notebook: duplicate work, and the same article added twice when two Macs
collected before syncing (2026-10-01). The choice is ONE synced setting; collections
never silently stop (sync off / nothing chosen / chosen Mac unpaired → collect here).
"""
import asyncio

import pytest


@pytest.fixture
def env(tmp_path, monkeypatch):
    from config import settings
    from services.sync import identity, store

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    from storage.database import Database, get_db
    monkeypatch.setattr(Database, "_instance", None)        # a fresh db per test, not the last test's
    get_db()
    monkeypatch.setattr(identity, "device_id", lambda: "mini")
    monkeypatch.setattr(identity, "device_name", lambda: "Mac mini")
    store.put("enabled", True)
    store.conn().execute("INSERT INTO devices (device_id, name, cert_pem, fingerprint, role, mode) "
                         "VALUES ('mbp', 'MacBook Pro', 'x', 'f', 'seed', 'live')")
    return identity


def test_without_a_choice_every_mac_collects(env):
    from services.sync import roles
    assert roles.collects_here()[0] is True


def test_only_the_chosen_mac_collects(env, monkeypatch):
    from services.sync import roles
    roles.set_collector("mini", "Mac mini")
    assert roles.collects_here() == (True, "this Mac is the collector")
    from services.sync import store                          # now look from the MBP, which has the mini paired
    store.conn().execute("INSERT INTO devices (device_id, name, cert_pem, fingerprint, role, mode) "
                         "VALUES ('mini', 'Mac mini', 'x', 'f', 'seed', 'live')")
    monkeypatch.setattr(env, "device_id", lambda: "mbp")
    here, why = roles.collects_here()
    assert here is False and "Mac mini" in why


def test_collections_never_silently_stop(env, monkeypatch):
    from services.sync import roles, store
    roles.set_collector("gone-mac", "Old Mac")              # chosen Mac no longer paired
    assert roles.collects_here()[0] is True
    roles.set_collector("mbp", "MacBook Pro")
    assert roles.collects_here()[0] is False
    store.put("enabled", False)                             # sync off: collect for yourself
    assert roles.collects_here()[0] is True


def test_the_choice_is_one_synced_document(env):
    from services.sync import registry, roles
    from storage import documents
    roles.set_collector("mbp", "MacBook Pro")
    assert documents.get(roles.KIND, roles.KEY)["device_id"] == "mbp"
    assert any(t.name == "documents" for t in registry.tables("main"))   # it travels


def test_the_scheduler_skips_its_cycle_on_a_non_collector(env, monkeypatch):
    from services import collection_scheduler as cs
    from services.sync import roles
    roles.set_collector("mbp", "MacBook Pro")
    called = []

    async def fake_list():
        called.append(1)
        return []
    monkeypatch.setattr(cs.notebook_store, "list", fake_list)
    asyncio.run(cs.CollectionScheduler()._check_and_run_collections())
    assert called == []                                      # never even looked at notebooks


def test_existing_pairs_default_to_the_seed_on_both_macs(env, monkeypatch):
    """mini opened the pairing window (seed); the MBP joined. Both pick the mini."""
    from services.sync import roles, service, store
    from storage import documents
    # on the mini: its peer (the MBP) has role 'joiner' → the mini is the seed
    store.conn().execute("UPDATE devices SET role='joiner' WHERE device_id='mbp'")
    service.default_collector()
    assert roles.collector()["device_id"] == "mini"
    # on the MBP: its peer (the mini) has role 'seed' → also the mini
    documents.delete(roles.KIND, roles.KEY)
    store.conn().execute("DELETE FROM devices")
    store.conn().execute("INSERT INTO devices (device_id, name, cert_pem, fingerprint, role, mode) "
                         "VALUES ('mini', 'Mac mini', 'x', 'f', 'seed', 'live')")
    monkeypatch.setattr(env, "device_id", lambda: "mbp")
    service.default_collector()
    assert roles.collector()["device_id"] == "mini"
    roles.set_collector("mbp", "MacBook Pro")             # a user's choice is never overridden
    service.default_collector()
    assert roles.collector()["device_id"] == "mbp"
