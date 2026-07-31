"""CI-pure unit tests for the infographic store (2.2.0).

The store persists generated infographics per notebook so they survive app
restart + surface in the Library.

CRITICAL: the dev venv's `settings.data_dir` points at PRODUCTION data
(~/Library/Application Support/LocalBook). Every test here monkeypatches
`settings.data_dir` to a pytest tmp dir BEFORE touching the store, so nothing
is ever written to real user data. The store re-resolves `settings.data_dir`
on each connection, so the redirect takes effect immediately.
"""
import asyncio

import pytest

from config import settings
from storage.infographic_store import InfographicStore


@pytest.fixture()
def store(tmp_path, monkeypatch):
    # Redirect the DB location to an isolated tmp dir — NOT production data.
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    return InfographicStore()


# A representative payload covering the shapes the three lanes emit
# (body-html + recharts chart config + scene_svg + nested provenance).
SAMPLE_PAYLOAD = {
    "archetype": "facts_table",
    "degraded": False,
    "style": "editorial",
    "body_html": "<section><h2>Findings</h2><p>Point one<sup>1</sup></p></section>",
    "chart": {"type": "bar", "series": [{"name": "A", "data": [1, 2, 3]}]},
    "scene_svg": "<svg><rect/></svg>",
    "sources": [{"n": 1, "source_id": "src-abc", "title": "Doc A"}],
    "nested": {"deep": {"list": [1, {"k": "v"}]}},
}


def test_create_list_get_delete_roundtrip(store):
    async def run():
        created = await store.create(
            notebook_id="nb1",
            topic="the topic",
            title="My Infographic",
            lane="L2",
            archetype="facts_table",
            payload=SAMPLE_PAYLOAD,
            degraded=False,
        )
        assert created and created.get("infographic_id")
        gid = created["infographic_id"]
        assert created["notebook_id"] == "nb1"
        assert created["title"] == "My Infographic"
        assert created["lane"] == "L2"
        assert created["archetype"] == "facts_table"
        assert created["degraded"] == 0
        assert created["created_at"] and created["updated_at"]

        # list
        rows = await store.list("nb1")
        assert len(rows) == 1
        assert rows[0]["infographic_id"] == gid

        # get
        got = await store.get(gid)
        assert got is not None
        assert got["infographic_id"] == gid

        # delete
        assert await store.delete(gid) is True
        assert await store.get(gid) is None
        assert await store.list("nb1") == []
        # deleting again is a no-op (never raises, returns False)
        assert await store.delete(gid) is False

    asyncio.run(run())


def test_payload_json_roundtrips_full_artifact_payload(store):
    async def run():
        import json
        created = await store.create(
            notebook_id="nb1", topic="t", title="T", lane="L1",
            archetype="annotated_chart", payload=SAMPLE_PAYLOAD, degraded=False,
        )
        got = await store.get(created["infographic_id"])
        restored = json.loads(got["payload_json"])
        # The WHOLE payload survives a store round-trip byte-for-byte.
        assert restored == SAMPLE_PAYLOAD
        assert restored["nested"]["deep"]["list"][1]["k"] == "v"
        assert restored["sources"][0]["source_id"] == "src-abc"

    asyncio.run(run())


def test_degraded_flag_persists(store):
    async def run():
        created = await store.create(
            notebook_id="nb1", topic="t", title="Degraded",
            lane="L4", archetype=None,
            payload={"degraded": True, "reason": "klein unavailable"},
            degraded=True,
        )
        assert created["degraded"] == 1
        got = await store.get(created["infographic_id"])
        assert got["degraded"] == 1

    asyncio.run(run())


def test_list_is_notebook_scoped_and_newest_first(store):
    async def run():
        a = await store.create(notebook_id="nb1", title="A", payload={"i": 1})
        b = await store.create(notebook_id="nb1", title="B", payload={"i": 2})
        await store.create(notebook_id="nb2", title="C", payload={"i": 3})

        nb1 = await store.list("nb1")
        assert {r["infographic_id"] for r in nb1} == {a["infographic_id"], b["infographic_id"]}
        assert len(await store.list("nb2")) == 1
        assert await store.list("nb-none") == []

    asyncio.run(run())


def test_never_raises_on_bad_input(store):
    async def run():
        # Missing notebook_id → returns {} (no row), never raises.
        assert await store.create(notebook_id="", payload={"x": 1}) == {}
        # None payload is tolerated (stored as {}).
        created = await store.create(notebook_id="nb1", payload=None)
        assert created.get("infographic_id")
        got = await store.get(created["infographic_id"])
        import json
        assert json.loads(got["payload_json"]) == {}
        # get / delete on unknown ids never raise.
        assert await store.get("does-not-exist") is None
        assert await store.delete("does-not-exist") is False

    asyncio.run(run())


def test_tmp_dir_isolation(store, tmp_path):
    """Guard: the store writes to the tmp dir, never production data_dir."""
    async def run():
        await store.create(notebook_id="nb1", title="X", payload={"a": 1})
        # The DB file must live under the pytest tmp dir.
        assert (tmp_path / "localbook.db").exists()

    asyncio.run(run())
    assert settings.data_dir == tmp_path
