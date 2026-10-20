"""A paired phone reads chunks and the Mac's vectors; owner-excluded notebooks are invisible to it."""
import base64
import datetime
import sqlite3
import time

import numpy as np
import pyarrow as pa
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID
from fastapi.testclient import TestClient

from services.sync import identity, peer, phone_library, store

DIMS = 768
NB_OPEN, NB_PRIVATE = "nb-open", "nb-private"


def _make_table(db, notebook_id, rows):
    schema = pa.schema([("vector", pa.list_(pa.float32(), DIMS)), ("text", pa.string()),
                        ("parent_text", pa.string()), ("synthetic_questions", pa.string()),
                        ("source_id", pa.string()), ("chunk_index", pa.int64()),
                        ("filename", pa.string()), ("source_type", pa.string())])
    data = [{"vector": [float(i % 7) / 7] * DIMS, "text": t, "parent_text": t, "synthetic_questions": "",
             "source_id": s, "chunk_index": c, "filename": f"{s}.md", "source_type": "md"}
            for i, (s, c, t) in enumerate(rows)]
    return db.create_table(f"notebook_{notebook_id}", data=pa.Table.from_pylist(data, schema=schema), schema=schema)


@pytest.fixture()
def lib(tmp_path, monkeypatch):
    import lancedb
    from config import settings
    from services import embedding_migration as em, keyvault
    monkeypatch.setattr(settings, "data_dir", tmp_path, raising=False)
    monkeypatch.setattr(settings, "db_path", tmp_path / "lancedb", raising=False)
    monkeypatch.setattr(settings, "embedding_model", "mlx-community/embeddinggemma-2-bf16", raising=False)
    monkeypatch.setattr(keyvault, "get_or_create", lambda purpose: bytes(range(32)))
    monkeypatch.setattr(keyvault, "device_id", lambda: "mac-test")
    main = tmp_path / "localbook.db"
    c = sqlite3.connect(main)
    c.execute("CREATE TABLE notebooks (id TEXT PRIMARY KEY, title TEXT)")
    c.executemany("INSERT INTO notebooks VALUES (?,?)", [(NB_OPEN, "Open"), (NB_PRIVATE, "Personal")])
    c.commit(); c.close()
    db = lancedb.connect(str(tmp_path / "lancedb"))
    _make_table(db, NB_OPEN, [("s1", 0, "alpha"), ("s1", 1, "beta"), ("s2", 0, "gamma"), ("s2", 1, "delta"), ("s2", 2, "eps")])
    _make_table(db, NB_PRIVATE, [("p1", 0, "secret")])
    phone_library._digest_cache.clear(); phone_library._rows_cache.clear()
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "phone")])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(1).not_valid_before(now).not_valid_after(now + datetime.timedelta(days=1))
            .sign(key, hashes.SHA256()))
    pem = cert.public_bytes(serialization.Encoding.PEM).decode()
    store.put("enabled", True)
    store.pin_phone({"device_id": "phone-1", "name": "iPhone", "cert_pem": pem, "fingerprint": identity.fingerprint(pem)})
    monkeypatch.setattr(peer, "_phone_failures", peer._FailureLimiter())
    monkeypatch.setattr(peer, "_phone_nonces", peer._NonceCache())
    return key, TestClient(peer.phone_sync_app), db


def _call(lib, path, body=None):
    import json, os
    key, client, _ = lib
    raw = json.dumps(body or {}, separators=(",", ":")).encode()
    ts, nonce = str(int(time.time())), os.urandom(16).hex()
    msg = identity.phone_message("POST", path, ts, nonce, "mac-test", identity.fingerprint(identity.phone_cert_pem()), raw)
    sig = base64.b64encode(key.sign(msg, ec.ECDSA(hashes.SHA256()))).decode()
    return client.post(path, content=raw, headers={"Content-Type": "application/json", "X-LB-Device": "phone-1",
                                                   "X-LB-Time": ts, "X-LB-Nonce": nonce, "X-LB-Sig": sig})


def test_manifest_lists_visible_notebooks_with_the_embedder_key(lib):
    m = _call(lib, "/phone/library/manifest").json()
    assert {n["id"] for n in m["notebooks"]} == {NB_OPEN, NB_PRIVATE}
    assert m["embedder"]["dims"] == DIMS and m["embedder"]["max_tokens"] == 512
    open_nb = next(n for n in m["notebooks"] if n["id"] == NB_OPEN)
    assert open_nb["sources"] == 2 and open_nb["chunks"] == 5


def test_an_excluded_notebook_is_invisible_everywhere(lib):
    phone_library.set_excluded([NB_PRIVATE])
    m = _call(lib, "/phone/library/manifest").json()
    assert [n["id"] for n in m["notebooks"]] == [NB_OPEN]
    assert _call(lib, "/phone/library/sources", {"notebook_id": NB_PRIVATE}).status_code == 404
    assert _call(lib, "/phone/library/chunks", {"notebook_id": NB_PRIVATE}).status_code == 404


def test_chunks_page_in_order_with_float16_vectors(lib):
    got, after = [], None
    for _ in range(10):
        page = _call(lib, "/phone/library/chunks", {"notebook_id": NB_OPEN, "limit": 2, "after": after}).json()
        got += page["chunks"]
        if not page["more"]:
            break
        after = page["after"]
    assert [(c["source_id"], c["chunk_index"]) for c in got] == [("s1", 0), ("s1", 1), ("s2", 0), ("s2", 1), ("s2", 2)]
    v = np.frombuffer(base64.b64decode(got[0]["vector"]), dtype="<f2")
    assert v.shape == (DIMS,) and abs(float(v[0]) - 0.0) < 1e-3


def test_chunks_can_be_limited_to_chosen_sources_and_the_digest_changes(lib):
    only = _call(lib, "/phone/library/chunks", {"notebook_id": NB_OPEN, "source_ids": ["s2"], "limit": 50}).json()
    assert {c["source_id"] for c in only["chunks"]} == {"s2"}
    before = _call(lib, "/phone/library/manifest").json()["notebooks"]
    digest = next(n["digest"] for n in before if n["id"] == NB_OPEN)
    db = lib[2]
    db.open_table(f"notebook_{NB_OPEN}").delete("source_id = 's2' AND chunk_index = 2")
    after = _call(lib, "/phone/library/manifest").json()["notebooks"]
    assert next(n["digest"] for n in after if n["id"] == NB_OPEN) != digest


def test_the_library_needs_a_valid_signature(lib):
    r = lib[1].post("/phone/library/manifest", content=b"{}", headers={"X-LB-Device": "phone-1"})
    assert r.status_code == 403


def test_ask_the_mac_runs_the_macs_own_pipeline_and_returns_a_trimmed_answer(lib, monkeypatch):
    import services.rag_engine as re_mod
    seen = {}

    async def fake_query(notebook_id, question, source_ids=None, top_k=4, **kw):
        seen.update(notebook_id=notebook_id, question=question, top_k=top_k)
        return {"answer": "Because.", "citations": [{"number": 1, "source_id": "s1", "filename": "a.md", "chunk_index": 0,
                                                     "text": "alpha", "parent_text": "x" * 5000, "confidence": 0.8,
                                                     "confidence_level": "high"}],
                "follow_up_questions": ["Why?"], "low_confidence": False}
    monkeypatch.setattr(re_mod.rag_engine, "query", fake_query)
    r = _call(lib, "/phone/ask", {"notebook_id": NB_OPEN, "question": "How does X differ from Y?"})
    assert r.status_code == 200 and seen == {"notebook_id": NB_OPEN, "question": "How does X differ from Y?", "top_k": 4}
    body = r.json()
    assert body["answer"] == "Because." and body["citations"][0]["filename"] == "a.md"
    assert "parent_text" not in body["citations"][0]


def test_ask_refuses_excluded_notebooks_and_bad_questions(lib):
    phone_library.set_excluded([NB_PRIVATE])
    assert _call(lib, "/phone/ask", {"notebook_id": NB_PRIVATE, "question": "hi"}).status_code == 404
    assert _call(lib, "/phone/ask", {"notebook_id": NB_OPEN, "question": "  "}).status_code == 400
    assert _call(lib, "/phone/ask", {"notebook_id": NB_OPEN, "question": "x" * 3000}).status_code == 400
