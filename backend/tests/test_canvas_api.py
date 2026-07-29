"""CI-pure API tests for the Journey Canvas router (2.2.0 Crawl P1).

Mounts just canvas_api.router on a bare FastAPI app and drives it with TestClient,
with the store's connection monkeypatched to an in-memory sqlite (check_same_thread=False
for TestClient's threadpool) — verifies routing / status codes / validation with no
production localbook.db writes.
"""
import sqlite3

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from storage import canvas_layout_store as cl
from api import canvas as canvas_api


@pytest.fixture()
def client(monkeypatch):
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    cl._ensure_schema(conn)
    monkeypatch.setattr(cl, "_get_conn", lambda: conn)
    app = FastAPI()
    app.include_router(canvas_api.router)
    return TestClient(app)


def test_get_empty_layout(client):
    r = client.get("/canvas/layout/nb1")
    assert r.status_code == 200
    assert r.json() == {"nodes": [], "edges": [], "viewport": {"x": 0.0, "y": 0.0, "zoom": 1.0}}


def test_save_then_get(client):
    r = client.put("/canvas/layout/nb1", json={
        "nodes": [{"id": "n1", "x": 1, "y": 2, "kind": "chat_turn"}],
        "edges": [], "viewport": {"x": 0, "y": 0, "zoom": 1},
    })
    assert r.status_code == 200 and r.json()["nodes"] == 1
    assert len(client.get("/canvas/layout/nb1").json()["nodes"]) == 1


def test_patch_node_found_and_missing(client):
    client.put("/canvas/layout/nb1", json={"nodes": [{"id": "n1", "x": 0, "y": 0, "kind": "k"}], "edges": []})
    assert client.patch("/canvas/node/nb1/n1", json={"x": 9, "y": 8}).status_code == 200
    assert client.patch("/canvas/node/nb1/missing", json={"x": 1, "y": 1}).status_code == 404


def test_edge_lifecycle(client):
    r = client.post("/canvas/edge/nb1", json={"source": "a", "target": "b", "state": "user", "id": "e1"})
    assert r.status_code == 200 and r.json()["state"] == "user"
    assert client.delete("/canvas/edge/nb1/e1").status_code == 200
    assert client.delete("/canvas/edge/nb1/e1").status_code == 404  # already gone


def test_invalid_edge_state_422(client):
    r = client.post("/canvas/edge/nb1", json={"source": "a", "target": "b", "state": "bogus"})
    assert r.status_code == 422


def test_viewport_persist(client):
    assert client.put("/canvas/viewport/nb1", json={"x": 1, "y": 2, "zoom": 3}).status_code == 200
    assert client.get("/canvas/layout/nb1").json()["viewport"] == {"x": 1.0, "y": 2.0, "zoom": 3.0}
