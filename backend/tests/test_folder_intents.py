"""@collector's folder intents — the capability being reachable by asking.

Linked folders were a subsystem no agent could talk about: a user could not ask
"what folders am I watching" or "did anything new land". Every other ingest
surface in LocalBook has an agent that can describe it; this one was mute, which
is most of the difference between a feature in Settings and a capability.

The safety line these tests hold: **no folder intent approves a recording.**
Approval belongs in the review UI, where the user can see who was detected and
where it would go. A chat message cannot show that, so it must not authorise it.
"""
import asyncio
from datetime import datetime

import pytest

from api.chat import _folders
from storage.database import get_db
from storage.folder_link_store import folder_link_store
from storage.smart_folder_store import smart_folder_store


def _nb(nid="nb-intents", title="Recordings"):
    conn = get_db().get_connection()
    now = datetime.utcnow().isoformat()
    conn.execute("INSERT OR IGNORE INTO notebooks (id,title,created_at,updated_at) "
                 "VALUES (?,?,?,?)", (nid, title, now, now))
    conn.commit()
    return nid


@pytest.fixture
def linked(tmp_path):
    _nb()
    d = tmp_path / "recordings"
    d.mkdir()
    (d / "a.md").write_text("# 1:1\n\nnotes")
    link = folder_link_store.create_link(path=str(d), notebook_id="nb-intents")
    yield link, d
    smart_folder_store.forget_link(link["id"])
    folder_link_store.delete_link(link["id"])


def _ask(intent, nb="nb-intents"):
    return asyncio.run(_folders.handle(intent, {}, nb))


# ── the registration itself ─────────────────────────────────────────────────

def test_every_folder_intent_is_classifiable_and_handled():
    """A registered intent with no handler answers nothing; a handler with no
    registered intent is never reached. Both halves must exist."""
    from services.intent_classifier import COLLECTOR_INTENTS
    registered = {i["id"] for i in COLLECTOR_INTENTS if i["id"].startswith("folder_")}
    assert registered == _folders.FOLDER_INTENTS, (
        f"registered {registered} != handled {_folders.FOLDER_INTENTS}"
    )


def test_the_collector_chat_dispatches_them():
    """The intent must actually be routed, not just defined."""
    import inspect
    import api.chat._collector as c
    src = inspect.getsource(c)
    assert "FOLDER_INTENTS" in src, "collector chat does not dispatch folder intents"


# ── the answers ─────────────────────────────────────────────────────────────

def test_with_no_folders_it_explains_what_one_is():
    """A bare "none configured" teaches nothing. The reply should leave the user
    knowing what they could do and where."""
    reply, follow = _ask("folder_status", nb=_nb("nb-empty", "Empty"))
    assert "No folders are linked" in reply
    assert "Settings" in reply, "the reply must say where to set one up"
    assert follow


def test_folder_status_reports_the_real_link(linked):
    link, _ = linked
    reply, _ = _ask("folder_status")
    assert "recordings" in reply.lower()
    assert "feeding this notebook" in reply


def test_a_missing_folder_is_called_out(linked):
    """A folder on an unmounted drive is the most likely silent failure. It must
    read as a problem, not as a quiet zero."""
    import shutil
    link, d = linked
    shutil.rmtree(d)
    reply, _ = _ask("folder_status")
    assert "missing" in reply.lower() or "unmounted" in reply.lower()


def test_scan_now_reports_what_it_did(linked, monkeypatch):
    calls = []

    class _Fake:
        async def process(self, content, filename, notebook_id, reporter=None):
            calls.append(filename)
            return {"source_id": f"s-{len(calls)}", "chunks": 1, "characters": 5}

    import services.document_processor as dp
    monkeypatch.setattr(dp, "document_processor", _Fake())

    async def _noop(*a, **kw):
        return {}
    import services.post_ingest as pi
    monkeypatch.setattr(pi, "finalize_source", _noop)

    reply, _ = _ask("folder_scan_now")
    assert "Added 1 file" in reply, reply
    # ...and a second scan honestly reports nothing rather than repeating itself.
    reply2, _ = _ask("folder_scan_now")
    assert "nothing new" in reply2.lower(), reply2


def test_the_review_queue_lists_who_and_where(linked):
    link, _ = linked
    smart_folder_store.upsert_pending(
        link_id=link["id"], abs_path="/tmp/x.md", filename="1-1 sarah.md",
        size=10, mtime=1.0, content_hash="h",
        participants=["Sarah Chen"], topics=["goals"],
        summary="A 1:1 about goals.", suggested_id="nb-intents",
        suggested_name="Recordings", confidence=0.82, alternatives=[],
    )
    reply, _ = _ask("folder_review_queue")
    assert "1-1 sarah.md" in reply
    assert "Sarah Chen" in reply
    assert "Recordings" in reply
    assert "82%" in reply


def test_chat_never_approves_a_recording(linked, monkeypatch):
    """THE line. The queue exists so a human sees who is in a recording before
    it moves. Chat cannot show that, so no folder intent may file anything."""
    class _Fake:
        async def process(self, content, filename, notebook_id, reporter=None):
            return {"source_id": "s-1", "chunks": 1, "characters": 5}

    async def _noop(*a, **kw):
        return {}

    import services.document_processor as dp
    import services.post_ingest as pi
    monkeypatch.setattr(dp, "document_processor", _Fake())
    monkeypatch.setattr(pi, "finalize_source", _noop)

    link, _ = linked
    item = smart_folder_store.upsert_pending(
        link_id=link["id"], abs_path="/tmp/y.md", filename="sensitive.md",
        size=10, mtime=1.0, content_hash="h2",
        participants=["Sarah Chen"], topics=["performance"],
        summary="A performance conversation.", suggested_id="nb-intents",
        suggested_name="Recordings", confidence=0.99, alternatives=[],
    )
    for intent in sorted(_folders.FOLDER_INTENTS):
        _ask(intent)
    assert smart_folder_store.get_pending(item["id"])["status"] == "pending", (
        "a chat intent resolved a pending recording — approval must stay in the "
        "review UI where the user can see what they are approving"
    )


def test_rules_are_described_in_plain_english(linked):
    _nb("nb-sarah", "Sarah 1:1")
    rule = smart_folder_store.create_rule(notebook_id="nb-sarah",
                                          participants=["Sarah Chen"])
    try:
        reply, _ = _ask("folder_rules")
        assert "Sarah Chen" in reply
        assert "Sarah 1:1" in reply
        assert "nothing routed yet" in reply
        assert "only things that file a recording without asking" in reply
    finally:
        smart_folder_store.delete_rule(rule["id"])


def test_deleting_a_notebook_revokes_the_rules_pointing_at_it(linked):
    """A rule that outlived its notebook would file recordings into nothing.
    The foreign key makes that impossible rather than a cleanup job — so the
    rule disappears with the notebook, and the user is never shown a promise
    the app can no longer keep."""
    rule = smart_folder_store.create_rule(notebook_id="nb-intents",
                                          participants=["Ghost"])
    conn = get_db().get_connection()
    try:
        assert smart_folder_store.get_rule(rule["id"]) is not None
        conn.execute("DELETE FROM notebooks WHERE id = 'nb-intents'")
        conn.commit()
        assert smart_folder_store.get_rule(rule["id"]) is None
        reply, _ = _ask("folder_rules")
        assert "No routing rules yet" in reply
    finally:
        smart_folder_store.delete_rule(rule["id"])
        _nb()
