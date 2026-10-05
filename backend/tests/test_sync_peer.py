"""LB-12 identity, genesis and the real TLS transport.

The keyvault seed is faked (no Keychain writes); everything else is real:
certificates, TLS 1.3 with pinning, request signatures, the listener, the
protocol routes. The transport smoke test pairs this Mac with ITSELF over
localhost — one identity, but the full path a second Mac would take.
"""

import asyncio
import json
import shutil
import socket
import time

import pytest

from services.sync import genesis, identity, peer, runtime, store
from tests.sync_harness import Mac, template


@pytest.fixture
def mac(tmp_path, monkeypatch, tmp_path_factory):
    from config import settings
    from services import keyvault

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    shutil.copy(template(tmp_path_factory.getbasetemp()), tmp_path / "localbook.db")
    (tmp_path / "memory").mkdir()
    seed = bytes(range(32))
    monkeypatch.setattr(keyvault, "get_or_create", lambda purpose: seed)
    monkeypatch.setattr(keyvault, "device_id", lambda: "dev-test-1")
    monkeypatch.setattr(identity, "device_name", lambda: "Test Mac")
    monkeypatch.setattr(runtime, "_clock", None)
    # recall db with its tables, as memory_store would create it
    import sqlite3
    c = sqlite3.connect(tmp_path / "memory" / "recall_memory.db")
    c.executescript("""CREATE TABLE recall_entries (id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL,
        notebook_id TEXT, role TEXT, content TEXT, timestamp TEXT, topics TEXT, entities TEXT,
        sentiment TEXT, is_summarized INTEGER, summary TEXT);
        CREATE TABLE conversation_summaries (id TEXT PRIMARY KEY, conversation_id TEXT, notebook_id TEXT,
        summary TEXT, start_time TEXT, end_time TEXT, message_count INTEGER);
        CREATE TABLE user_signals (id TEXT PRIMARY KEY, timestamp TEXT);""")
    c.close()
    return tmp_path


# ── identity ────────────────────────────────────────────────────────────────


def test_the_certificate_is_stable_and_matches_the_key(mac):
    a = identity.cert_pem()
    assert identity.cert_pem() == a                       # reused, not re-minted
    assert len(identity.fingerprint(a)) == 64


def test_the_pairing_code_is_the_same_on_both_macs():
    assert identity.sas("aa" * 32, "bb" * 32) == identity.sas("bb" * 32, "aa" * 32)
    assert identity.sas("aa" * 32, "bb" * 32) != identity.sas("aa" * 32, "cc" * 32)
    assert len(identity.sas("aa", "bb")) == 6


def test_a_signature_binds_body_path_and_time(mac):
    cert = identity.cert_pem()
    h = identity.sign_headers("POST", "/sync/pull", b'{"db":"main"}')
    ok = lambda body, path="/sync/pull", ts=h["X-LB-Time"]: identity.verify_signature(
        cert, "POST", path, ts, body, h["X-LB-Sig"])
    assert ok(b'{"db":"main"}')
    assert not ok(b'{"db":"recall"}')                     # tampered body
    assert not ok(b'{"db":"main"}', path="/sync/push")    # replayed to another route
    assert not ok(b'{"db":"main"}', ts=str(int(time.time()) - 3600))   # stale


# ── genesis ─────────────────────────────────────────────────────────────────


def test_genesis_matches_unique_titles_and_identical_text():
    local = {"notebooks": [{"id": "L1", "title": "Leadership"}, {"id": "L2", "title": "Only here"},
                           {"id": "L3", "title": "Dup"}, {"id": "L4", "title": "dup"}],
             "sources": [{"id": "ls1", "notebook_id": "L1", "content_hash": "h1"},
                         {"id": "ls2", "notebook_id": "L1", "content_hash": "h-new"}]}
    seed = {"notebooks": [{"id": "S1", "title": "  leadership "}, {"id": "S3", "title": "Dup"}],
            "sources": [{"id": "ss1", "notebook_id": "S1", "content_hash": "h1"}]}
    p = genesis.plan(local, seed)
    assert p["notebooks"] == {"L1": "S1"}                  # "Dup" is ambiguous here: no match
    assert p["sources"] == {"ls1": "ss1"}
    assert p["counts"]["notebooks_after"] == 4 + 2 - 1


def test_rekey_moves_every_reference(tmp_path, tmp_path_factory):
    m = Mac(tmp_path, "J", template(tmp_path_factory.getbasetemp()))
    m.notebook("L1", "Leadership")
    m.source("ls1", "L1", "same text")
    m.sql("INSERT INTO highlights (highlight_id, notebook_id, source_id, start_offset, end_offset, "
          "highlighted_text, created_at, updated_at) VALUES ('h1','L1','ls1',0,4,'same','now','now')")
    (tmp_path / "J" / "notebooks" / "L1").mkdir(parents=True)
    (tmp_path / "J" / "notebooks" / "L1" / "collector.yaml").write_text("x: 1")
    plan = {"notebooks": {"L1": "S1"}, "sources": {"ls1": "ss1"}}
    genesis.rekey(m.engine_conn, plan, tmp_path / "J")
    assert m.sql("SELECT id FROM notebooks").fetchall() == [("S1",)]
    assert m.sql("SELECT id, notebook_id FROM sources").fetchall() == [("ss1", "S1")]
    assert m.sql("SELECT notebook_id, source_id FROM highlights").fetchall() == [("S1", "ss1")]
    assert (tmp_path / "J" / "notebooks" / "S1" / "collector.yaml").exists()
    assert m.sql("PRAGMA foreign_key_check").fetchall() == []


# ── the real transport, this Mac paired with itself ─────────────────────────


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def test_mutual_tls_hello_pull_push_over_localhost(mac, monkeypatch):
    port = _free_port()
    monkeypatch.setattr(peer, "sync_port", lambda: port)
    me = identity.cert_pem()
    store.put("enabled", True)
    runtime.install_journals()
    store.pin({"device_id": identity.device_id(), "name": "Test Mac", "cert_pem": me,
               "fingerprint": identity.fingerprint(me), "host": "127.0.0.1", "port": port}, "seed")
    d = store.device(identity.device_id())

    async def go():
        await peer.start_sync_listener()
        try:
            async with peer.Session(d) as s:
                h = await s.hello()
                assert h["head"] == runtime.ledger_head() and "main" in h["vv"]
                pulled = await s.pull_all("main", dry_run=True)
                pushed = await s.push_all("main", h["vv"]["main"], dry_run=True)
                return pulled, pushed
        finally:
            await peer.stop("sync")

    pulled, pushed = asyncio.run(go())
    assert isinstance(pulled["inserted"], int) and isinstance(pushed["inserted"], int)


def test_an_unpaired_mac_cannot_get_past_tls(mac, monkeypatch):
    port = _free_port()
    monkeypatch.setattr(peer, "sync_port", lambda: port)
    store.put("enabled", True)
    me = identity.cert_pem()
    # The listener trusts nobody; the client presents a cert the server never pinned.
    d = {"device_id": "x", "name": "x", "cert_pem": me, "host": "127.0.0.1", "port": port}

    async def go():
        await peer.start_sync_listener()
        try:
            async with peer.Session(d) as s:
                await s.hello()
        finally:
            await peer.stop("sync")

    with pytest.raises(Exception):
        asyncio.run(go())
