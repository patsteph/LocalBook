"""'Use the other' on a setting or memory conflict (LB-12, 2026-10-03).

It wrote body_json with a raw UPDATE: no updated_at bump, and this Mac kept serving the
old value from its in-memory caches until a restart. Now it goes through the documents
store (which journals, so the choice ships) and drops those caches.
"""
import asyncio
import json

import pytest


@pytest.fixture
def db(tmp_path, monkeypatch):
    from config import settings
    from storage.database import Database, get_db
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(Database, "_instance", None)
    return get_db().get_connection()


def test_using_the_other_memory_writes_it_back_and_drops_caches(db, monkeypatch):
    from api import sync as sync_api
    from services.sync import merge
    from storage import documents

    documents.put("core_memory", "e1", {"id": "e1", "key": "home city", "value": "Lisbon"})
    other = json.dumps({"id": "e1", "key": "home city", "value": "Porto"})
    db.execute("INSERT INTO sync_conflicts (id, tbl, pk, field, kind, kept_value, other_value, status, created_at) "
               "VALUES ('c1', 'documents', ?, 'body_json', 'concurrent-edit', ?, ?, 'open', 'now')",
               (json.dumps(["core_memory", "e1"]), json.dumps(merge.encode(json.dumps({"value": "Lisbon"}))),
                json.dumps(merge.encode(other))))
    db.commit()
    dropped = []
    monkeypatch.setattr("services.sync.peer.drop_document_caches", lambda: dropped.append(1))
    asyncio.run(sync_api.resolve("c1", sync_api.Resolve(keep="other")))
    assert documents.get("core_memory", "e1")["value"] == "Porto"
    assert dropped == [1]
    assert tuple(db.execute("SELECT status, resolution FROM sync_conflicts WHERE id='c1'").fetchone()) == ("resolved", "other")
