"""Notes sent from a paired phone: added once per client id, only to notebooks the phone may see."""
import asyncio

import pytest
from fastapi import HTTPException

from services.sync import phone_library, phone_notes, runtime


def test_note_is_added_once_and_validated(tmp_path, monkeypatch):
    monkeypatch.setattr(runtime, "data_dir", lambda: tmp_path)
    monkeypatch.setattr(phone_library, "is_visible", lambda n: n == "nb")
    import api.sources as sources
    calls = []

    async def fake_create(nb, req, bg):
        calls.append((nb, req.title, req.content))
        return {"source_id": f"src{len(calls)}"}

    monkeypatch.setattr(sources, "create_note", fake_create)

    first = asyncio.run(phone_notes.add_note("nb", "c1", "T", "hello"))
    again = asyncio.run(phone_notes.add_note("nb", "c1", "T", "hello"))
    assert first == {"source_id": "src1", "duplicate": False}
    assert again == {"source_id": "src1", "duplicate": True}
    assert len(calls) == 1
    for bad, code in [(("other", "c2", "T", "x"), 404), (("nb", "", "T", "x"), 400), (("nb", "c3", "T", "  "), 400)]:
        with pytest.raises(HTTPException) as e:
            asyncio.run(phone_notes.add_note(*bad))
        assert e.value.status_code == code


def test_a_dropped_request_still_finishes_the_note_and_a_retry_gets_the_same_one(tmp_path, monkeypatch):
    """Indexing can outlast the phone's patience. The phone gives up and asks again: the note must be made once,
    and its id must be in the ledger even though the first request was cancelled half way."""
    monkeypatch.setattr(runtime, "data_dir", lambda: tmp_path)
    monkeypatch.setattr(phone_library, "is_visible", lambda n: n == "nb")
    import api.sources as sources
    calls = []

    async def slow_create(nb, req, bg):
        calls.append(req.title)
        await asyncio.sleep(0.3)
        return {"source_id": "src1"}

    monkeypatch.setattr(sources, "create_note", slow_create)

    async def scenario():
        first = asyncio.create_task(phone_notes.add_note("nb", "c1", "T", "hello"))
        await asyncio.sleep(0.05)
        first.cancel()                                   # the phone gave up waiting
        retry = await phone_notes.add_note("nb", "c1", "T", "hello")
        later = await phone_notes.add_note("nb", "c1", "T", "hello")
        return retry, later

    retry, later = asyncio.run(scenario())
    assert retry["source_id"] == "src1" and later == {"source_id": "src1", "duplicate": True}
    assert len(calls) == 1


def test_timeline_work_queued_by_the_note_is_run(tmp_path, monkeypatch):
    monkeypatch.setattr(runtime, "data_dir", lambda: tmp_path)
    monkeypatch.setattr(phone_library, "is_visible", lambda n: True)
    import api.sources as sources
    ran = []

    async def create(nb, req, bg):
        bg.add_task(lambda: ran.append("timeline"))
        return {"source_id": "src1"}

    monkeypatch.setattr(sources, "create_note", create)

    async def scenario():
        await phone_notes.add_note("nb", "c1", "T", "hello")
        await asyncio.gather(*list(phone_notes._after))

    asyncio.run(scenario())
    assert ran == ["timeline"]
