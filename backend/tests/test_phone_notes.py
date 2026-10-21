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
