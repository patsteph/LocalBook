"""Linked Folders — the ledger, the scanner, and the promises they make.

Three promises are load-bearing, and each has a test that fails loudly if it
ever stops being true:

  1. **The same file is never ingested twice** — not on a rescan, not after a
     rename, not after a move between watched folders.
  2. **We never write inside a linked folder.** The user's recordings are
     theirs. We stat them and read them; that is the entire contract.
  3. **An unreadable folder says so.** macOS TCC denies with PermissionError,
     and `os.walk` swallows errors by default — so without an explicit probe a
     permission denial looks exactly like an empty folder, and a link that can
     never read anything reports "nothing new" forever.
"""
import asyncio
import os
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from storage.folder_link_store import (
    FolderLinkPathError,
    folder_link_store,
    validate_folder_path,
)
from services.folder_watcher import FolderWatcher, folder_watcher


# ── fixtures ────────────────────────────────────────────────────────────────

def _mk_notebook(nid: str) -> str:
    """Insert a bare notebook row. The FK on folder_links is deliberate — it is
    what makes deleting a notebook also delete its folder links, instead of
    leaving an orphan link scanning into nothing forever."""
    from storage.database import get_db
    conn = get_db().get_connection()
    now = datetime.utcnow().isoformat()
    conn.execute(
        "INSERT OR IGNORE INTO notebooks (id, title, created_at, updated_at) VALUES (?,?,?,?)",
        (nid, f"Test {nid}", now, now),
    )
    conn.commit()
    return nid


@pytest.fixture(autouse=True)
def _notebooks():
    for nid in ("nb-test", "nb-dupe", "nb-other", "nb-move", "nb-bulk"):
        _mk_notebook(nid)


@pytest.fixture
def watched(tmp_path):
    """Three transcripts, plus the two kinds of file a real folder also holds:
    one we CAN read (a PDF) and two we cannot (an archive, and OS junk)."""
    d = tmp_path / "recordings"
    d.mkdir()
    (d / "2026-09-14 1-1 sarah.md").write_text("# 1:1 with Sarah\n\nWe discussed goals.")
    (d / "2026-09-15 1-1 priya.md").write_text("# 1:1 with Priya\n\nRoadmap review.")
    (d / "standup.md").write_text("# Standup\n\nBlockers: none.")
    (d / "handout.pdf").write_bytes(b"%PDF-1.4 a readable format")
    (d / "backup.zip").write_bytes(b"PK\x03\x04 not something we can read")
    (d / ".DS_Store").write_bytes(b"junk")
    return d


@pytest.fixture
def link(watched):
    lk = folder_link_store.create_link(
        path=str(watched), notebook_id="nb-test", frequency="hourly"
    )
    yield lk
    folder_link_store.delete_link(lk["id"])


@pytest.fixture(autouse=True)
def treatment(monkeypatch):
    """Capture the post-ingest treatment instead of performing it.

    Without this the folder tests call the real auto-tagger, which means real
    LLM work — the suite went 16s → 190s the moment `finalize_source` was
    wired in. Recording the call is also the better assertion: what matters is
    that the folder path ASKS for the full treatment, not that the tagger works
    (which is the tagger's own test).
    """
    calls = []

    async def _fake(notebook_id, source_id, filename, text, **kw):
        calls.append({"notebook_id": notebook_id, "source_id": source_id,
                      "filename": filename, "text": text, **kw})
        return {"tagged": True, "tags": ["stub"]}

    import services.post_ingest as pi
    monkeypatch.setattr(pi, "finalize_source", _fake)
    return calls


@pytest.fixture
def fake_ingest(monkeypatch):
    """Stand in for document_processor so the tests measure the SCANNER, not
    the RAG pipeline. Records every filename it was handed."""
    calls = []

    class _FakeProcessor:
        async def process(self, content, filename, notebook_id, reporter=None):
            calls.append(filename)
            # MUST mirror the real return shape. This fake previously returned
            # {"id": ...}, which is NOT what document_processor.process returns —
            # so the tests happily agreed with a caller reading the wrong key,
            # and the bug only surfaced in the field as an untagged file.
            # A fake that does not match the contract validates nothing.
            return {"source_id": f"src-{len(calls)}", "filename": filename,
                    "format": "md", "chunks": 3, "characters": len(content)}

    import services.document_processor as dp
    monkeypatch.setattr(dp, "document_processor", _FakeProcessor())
    return calls


def _scan(link_id):
    return asyncio.run(folder_watcher.scan_link(link_id))


# ── path validation ─────────────────────────────────────────────────────────

def test_a_missing_folder_is_rejected_with_a_readable_reason(tmp_path):
    with pytest.raises(FolderLinkPathError) as e:
        validate_folder_path(str(tmp_path / "nope"))
    assert "does not exist" in str(e.value)


def test_a_file_is_not_a_folder(tmp_path):
    f = tmp_path / "a.md"
    f.write_text("x")
    with pytest.raises(FolderLinkPathError):
        validate_folder_path(str(f))


def test_localbooks_own_data_directory_cannot_be_linked():
    """Linking the data dir would have the scanner ingesting the database it
    writes to. That is a loop, not a feature."""
    from config import settings
    with pytest.raises(FolderLinkPathError) as e:
        validate_folder_path(str(settings.data_dir))
    assert "data directory" in str(e.value)


def test_the_same_folder_cannot_be_linked_to_one_notebook_twice(watched):
    a = folder_link_store.create_link(path=str(watched), notebook_id="nb-dupe")
    try:
        with pytest.raises(FolderLinkPathError):
            folder_link_store.create_link(path=str(watched), notebook_id="nb-dupe")
        # ...but the same folder MAY feed two different notebooks.
        b = folder_link_store.create_link(path=str(watched), notebook_id="nb-other")
        folder_link_store.delete_link(b["id"])
    finally:
        folder_link_store.delete_link(a["id"])


# ── discovery / patterns ────────────────────────────────────────────────────

def test_a_linked_folder_takes_everything_it_can_read(link, watched):
    """No file-type picker: the folder's contents ARE the answer. The default
    is every format `document_processor` can read, sourced from that module so
    adding a format there does not silently stay invisible here."""
    names = {c.name for c in folder_watcher.discover(link)}
    assert names == {"2026-09-14 1-1 sarah.md", "2026-09-15 1-1 priya.md",
                     "standup.md", "handout.pdf"}
    assert "backup.zip" not in names, "an archive is not a document — don't log it as a failure"
    assert ".DS_Store" not in names, "hidden files are noise, never content"


def test_the_default_patterns_track_the_ingest_path(link):
    """One source of truth. If these drift, a format LocalBook learns to read
    becomes invisible to every watched folder, with nothing to show why."""
    from services.document_processor import INGESTIBLE_EXTENSIONS
    from storage.folder_link_store import DEFAULT_PATTERNS
    assert set(DEFAULT_PATTERNS) == {f"*.{e}" for e in INGESTIBLE_EXTENSIONS}
    assert set(link["patterns"]) == set(DEFAULT_PATTERNS)


def test_subfolders_are_ignored_unless_recursive_is_set(link, watched):
    sub = watched / "archive"
    sub.mkdir()
    (sub / "old.md").write_text("# Old")
    assert "old.md" not in {c.name for c in folder_watcher.discover(link)}
    folder_link_store.update_link(link["id"], recursive=True)
    deep = folder_link_store.get_link(link["id"])
    assert "old.md" in {c.name for c in folder_watcher.discover(deep)}


# ── the ledger: never twice ─────────────────────────────────────────────────

def test_a_first_scan_ingests_everything_matching(link, fake_ingest):
    r = _scan(link["id"])
    assert r.ingested == 4, r.to_dict()
    assert r.failed == 0
    assert sorted(fake_ingest) == [
        "2026-09-14 1-1 sarah.md", "2026-09-15 1-1 priya.md",
        "handout.pdf", "standup.md"]


def test_a_rescan_ingests_nothing(link, fake_ingest):
    _scan(link["id"])
    fake_ingest.clear()
    r = _scan(link["id"])
    assert r.ingested == 0
    assert fake_ingest == [], "a rescan re-ingested files — the ledger is not holding"


def test_a_rescan_does_not_even_open_unchanged_files(link, fake_ingest, monkeypatch):
    """The (mtime, size) fast path is what keeps a 500-file folder cheap. If a
    rescan reads every file, the feature works but does not scale."""
    _scan(link["id"])
    opened = []
    real = Path.read_bytes
    monkeypatch.setattr(Path, "read_bytes",
                        lambda self: (opened.append(self.name), real(self))[1])
    _scan(link["id"])
    assert opened == [], f"unchanged files were read from disk: {opened}"


def test_a_renamed_file_is_recognised_by_content(link, watched, fake_ingest):
    """mtime and path both change on a rename; the content hash does not."""
    _scan(link["id"])
    fake_ingest.clear()
    (watched / "standup.md").rename(watched / "standup-renamed.md")
    r = _scan(link["id"])
    assert fake_ingest == [], "a rename caused a re-ingest"
    assert r.skipped >= 1
    assert r.ingested == 0


def test_a_file_moved_between_two_watched_folders_is_not_re_ingested(
        tmp_path, fake_ingest):
    """The ledger is global on content precisely for this case."""
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir(); b.mkdir()
    (a / "call.md").write_text("# Call\n\nSame bytes either side.")
    la = folder_link_store.create_link(path=str(a), notebook_id="nb-move")
    lb = folder_link_store.create_link(path=str(b), notebook_id="nb-move")
    try:
        assert _scan(la["id"]).ingested == 1
        fake_ingest.clear()
        (a / "call.md").rename(b / "call.md")
        r = _scan(lb["id"])
        assert fake_ingest == [], "the same bytes were ingested into the notebook twice"
        assert r.skipped == 1
    finally:
        folder_link_store.delete_link(la["id"])
        folder_link_store.delete_link(lb["id"])


def test_a_new_file_is_picked_up_on_the_next_scan(link, watched, fake_ingest):
    _scan(link["id"])
    fake_ingest.clear()
    (watched / "2026-09-16 1-1 sarah.md").write_text("# Follow-up\n\nPromotion timeline.")
    r = _scan(link["id"])
    assert r.ingested == 1
    assert fake_ingest == ["2026-09-16 1-1 sarah.md"]


def test_an_edited_file_is_re_ingested(link, watched, fake_ingest):
    """A transcript corrected after the fact should update, not be ignored."""
    _scan(link["id"])
    fake_ingest.clear()
    p = watched / "standup.md"
    p.write_text("# Standup\n\nBlockers: the build.")
    os.utime(p, (p.stat().st_atime + 10, p.stat().st_mtime + 10))
    r = _scan(link["id"])
    assert r.ingested == 1 and fake_ingest == ["standup.md"]


# ── the read-only promise ───────────────────────────────────────────────────

def test_a_scan_never_modifies_the_watched_folder(link, watched, fake_ingest):
    """The strongest promise this feature makes. Compare the folder byte for
    byte, name for name, mtime for mtime, before and after."""
    def snapshot():
        return {p.name: (p.stat().st_size, p.stat().st_mtime, p.read_bytes())
                for p in sorted(watched.iterdir()) if p.is_file()}
    before = snapshot()
    _scan(link["id"])
    _scan(link["id"])
    assert snapshot() == before, "the scanner touched the user's files"


# ── failure surfaces ────────────────────────────────────────────────────────

def test_an_empty_file_is_skipped_with_a_reason_not_ingested(link, watched, fake_ingest):
    (watched / "empty.md").write_text("")
    r = _scan(link["id"])
    assert "empty.md" not in fake_ingest
    assert r.skipped >= 1


def test_an_oversized_file_is_skipped_with_a_reason(link, watched, fake_ingest,
                                                    monkeypatch):
    from config import settings
    monkeypatch.setattr(settings, "folder_link_max_file_mb", 0.000001, raising=False)
    r = _scan(link["id"])
    assert r.ingested == 0
    assert r.skipped >= 4
    entry = folder_link_store.seen_map(link["id"])
    assert any("exceeds" in (v.get("error") or "") for v in entry.values())


def test_a_permission_denial_is_reported_not_silently_empty(link, monkeypatch):
    """os.walk swallows errors, so without the explicit probe this returns an
    empty list and the user is told 'nothing new' forever."""
    import services.folder_watcher as fw

    def _boom(path):
        raise PermissionError(13, "Operation not permitted")
    monkeypatch.setattr(fw.os, "scandir", _boom)

    r = _scan(link["id"])
    assert r.error and "Full Disk Access" in r.error
    stored = folder_link_store.get_link(link["id"])
    assert stored["last_error"], "the error must persist on the link, not just the response"


def test_an_ingest_failure_does_not_poison_the_whole_scan(link, monkeypatch, watched):
    """One bad file must not stop the other two from landing."""
    calls = []

    class _Flaky:
        async def process(self, content, filename, notebook_id, reporter=None):
            calls.append(filename)
            if filename == "standup.md":
                raise ValueError("no text content could be extracted")
            return {"source_id": f"src-{len(calls)}", "chunks": 1, "characters": 10}

    import services.document_processor as dp
    monkeypatch.setattr(dp, "document_processor", _Flaky())
    r = _scan(link["id"])
    assert r.failed == 1 and r.ingested == 3, r.to_dict()
    # And the failure is remembered, with its reason, rather than retried blindly.
    row = [v for v in folder_link_store.seen_map(link["id"]).values()
           if v["abs_path"].endswith("standup.md")][0]
    assert row["status"] == "failed" and "no text content" in row["error"]


# ── batching ────────────────────────────────────────────────────────────────

def test_a_large_folder_drains_in_batches_and_reports_what_is_pending(
        tmp_path, fake_ingest, monkeypatch):
    """A first scan of 200 files must not monopolise the machine — and the UI
    must be told the folder is not finished rather than shown a count that
    implies it is."""
    from config import settings
    monkeypatch.setattr(settings, "folder_link_batch_limit", 5, raising=False)
    d = tmp_path / "bulk"
    d.mkdir()
    for i in range(12):
        (d / f"rec-{i:02d}.md").write_text(f"# Recording {i}\n\nbody {i}")
    lk = folder_link_store.create_link(path=str(d), notebook_id="nb-bulk")
    try:
        r1 = _scan(lk["id"])
        assert r1.ingested == 5 and r1.pending == 7
        r2 = _scan(lk["id"])
        assert r2.ingested == 5 and r2.pending == 2
        r3 = _scan(lk["id"])
        assert r3.ingested == 2 and r3.pending == 0
        assert len(fake_ingest) == 12, "batching lost files instead of deferring them"
    finally:
        folder_link_store.delete_link(lk["id"])


# ── cadence ─────────────────────────────────────────────────────────────────

def test_a_never_scanned_link_is_due(link):
    assert FolderWatcher.is_due(link) is True


def test_a_disabled_or_manual_link_is_never_due(link):
    assert FolderWatcher.is_due({**link, "enabled": False}) is False
    assert FolderWatcher.is_due({**link, "frequency": "manual"}) is False


def test_due_ness_respects_the_links_own_frequency(link):
    now = datetime.utcnow()
    recent = {**link, "last_scan_at": (now - timedelta(minutes=30)).isoformat()}
    assert FolderWatcher.is_due(recent, now) is False        # hourly, 30m ago
    assert FolderWatcher.is_due({**recent, "frequency": "daily"}, now) is False
    old = {**link, "last_scan_at": (now - timedelta(hours=2)).isoformat()}
    assert FolderWatcher.is_due(old, now) is True
    assert FolderWatcher.is_due({**old, "frequency": "daily"}, now) is False


def test_a_corrupt_last_scan_timestamp_makes_the_link_due_not_broken(link):
    assert FolderWatcher.is_due({**link, "last_scan_at": "not-a-date"}) is True


# ── unlink ──────────────────────────────────────────────────────────────────

def test_deleting_a_notebook_takes_its_folder_links_with_it(watched):
    """An orphaned link would keep scanning into a notebook that no longer
    exists. The foreign key makes that impossible rather than a cleanup job."""
    nid = _mk_notebook("nb-doomed")
    lk = folder_link_store.create_link(path=str(watched), notebook_id=nid)
    from storage.database import get_db
    conn = get_db().get_connection()
    conn.execute("DELETE FROM notebooks WHERE id = ?", (nid,))
    conn.commit()
    assert folder_link_store.get_link(lk["id"]) is None


def test_unlinking_forgets_the_watch_but_not_what_it_taught(link, fake_ingest):
    """Stopping a watch is not the same as undoing it. Sources stay; only the
    link and its ledger go."""
    _scan(link["id"])
    assert folder_link_store.stats(link["id"])["ingested"] == 4
    assert folder_link_store.delete_link(link["id"]) is True
    assert folder_link_store.get_link(link["id"]) is None
    assert folder_link_store.seen_map(link["id"]) == {}


# ── smart folders (Part 2 groundwork) ───────────────────────────────────────

@pytest.fixture
def smart_analysis(monkeypatch):
    """Analysis without the model — and deliberately CONFIDENT, so the safety
    test below is exercising the real hazard rather than a low score."""
    from services import smart_folder

    async def _sum(text, filename):
        return ("A 1:1 about goals.", ["goals"])

    async def _suggest(analysis):
        return {"suggested_id": "nb-test", "suggested_name": "Test",
                "confidence": 0.99, "alternatives": [], "reason": "stub"}

    monkeypatch.setattr(smart_folder, "summarize", _sum)
    monkeypatch.setattr(smart_folder, "suggest", _suggest)


def test_a_smart_folder_never_ingests_without_a_destination(
        watched, fake_ingest, smart_analysis):
    """THE safety test. A folder with no notebook files nothing, ever — not at
    0.99 confidence, not at any confidence. Only a rule the user wrote may."""
    from storage.smart_folder_store import smart_folder_store
    lk = folder_link_store.create_link(path=str(watched), notebook_id=None)
    try:
        assert lk["is_smart"] is True
        r = _scan(lk["id"])
        assert r.ingested == 0
        assert fake_ingest == [], "a Smart Folder ingested a file with no destination"
        assert r.pending_review >= 1
        queued = smart_folder_store.list_pending(link_id=lk["id"])
        assert queued, "files were neither ingested NOR queued — they vanished"
        assert all(q["suggested_id"] == "nb-test" and q["confidence"] == 0.99
                   for q in queued)
    finally:
        smart_folder_store.forget_link(lk["id"])
        folder_link_store.delete_link(lk["id"])


def test_a_user_written_rule_is_the_one_thing_that_files_automatically(
        watched, fake_ingest, smart_analysis):
    """The other half of the contract: once the user HAS authorised it, the
    file lands without another click."""
    from storage.smart_folder_store import smart_folder_store
    lk = folder_link_store.create_link(path=str(watched), notebook_id=None)
    rule = smart_folder_store.create_rule(notebook_id="nb-test", topics=["goals"])
    try:
        r = _scan(lk["id"])
        assert r.ingested >= 1, "a matching rule did not authorise the route"
        assert smart_folder_store.list_pending(link_id=lk["id"]) == []
        assert smart_folder_store.get_rule(rule["id"])["hit_count"] >= 1
    finally:
        smart_folder_store.delete_rule(rule["id"])
        smart_folder_store.forget_link(lk["id"])
        folder_link_store.delete_link(lk["id"])


def test_unlinking_a_smart_folder_clears_its_review_queue(watched, smart_analysis):
    """Cards pointing at a watch that no longer exists are worse than no cards."""
    from storage.smart_folder_store import smart_folder_store
    lk = folder_link_store.create_link(path=str(watched), notebook_id=None)
    _scan(lk["id"])
    assert smart_folder_store.list_pending(link_id=lk["id"])
    smart_folder_store.forget_link(lk["id"])
    folder_link_store.delete_link(lk["id"])
    assert smart_folder_store.list_pending(link_id=lk["id"]) == []


# ── backfill choice ─────────────────────────────────────────────────────────

def test_only_from_now_on_ingests_nothing_and_reads_nothing(
        link, watched, fake_ingest, monkeypatch):
    """Linking a folder with 400 old transcripts must not cost 400 embeds. The
    baseline is a ledger write — the existing files are never even opened."""
    opened = []
    real = Path.read_bytes
    monkeypatch.setattr(Path, "read_bytes",
                        lambda self: (opened.append(self.name), real(self))[1])

    n = folder_watcher.mark_baseline(link)
    assert n == 4
    assert opened == [], "baselining read the files it was meant to skip"

    r = _scan(link["id"])
    assert r.ingested == 0 and fake_ingest == []

    # ...but a file that arrives AFTER the baseline is ingested normally.
    (watched / "new-call.md").write_text("# New\n\nAfter the link was made.")
    r2 = _scan(link["id"])
    assert r2.ingested == 1 and fake_ingest == ["new-call.md"]


def test_a_backfill_requeues_itself_until_the_folder_is_drained(
        tmp_path, fake_ingest, monkeypatch):
    """The batch limit keeps any single PASS small. It must not stretch a
    backfill across hours of cadence ticks — the remainder re-queues at once
    and drains at whatever pace the machine is idle."""
    from config import settings
    monkeypatch.setattr(settings, "folder_link_batch_limit", 4, raising=False)
    _mk_notebook("nb-drain")
    d = tmp_path / "drain"
    d.mkdir()
    for i in range(10):
        (d / f"r{i}.md").write_text(f"# R{i}\n\nbody {i}")
    lk = folder_link_store.create_link(path=str(d), notebook_id="nb-drain")

    enqueued = []

    class _FakeWorker:
        def enqueue(self, job):
            enqueued.append(job)

    import services.enrichment_worker as ew
    monkeypatch.setattr(ew, "enrichment_worker", _FakeWorker())
    try:
        asyncio.run(folder_watcher._job_factory(lk["id"])())
        assert len(enqueued) == 1, "pending work did not re-queue"
        assert "6 left" in enqueued[0].label

        # Drain it by hand the way the worker would, and confirm it stops.
        enqueued.clear()
        asyncio.run(folder_watcher._job_factory(lk["id"])())   # 4 more, 2 left
        assert len(enqueued) == 1
        enqueued.clear()
        asyncio.run(folder_watcher._job_factory(lk["id"])())   # last 2, 0 left
        assert enqueued == [], "it re-queued with nothing left to do"
        assert len(fake_ingest) == 10
    finally:
        folder_link_store.delete_link(lk["id"])


# ── the full treatment ──────────────────────────────────────────────────────

def test_a_folder_ingested_file_gets_the_same_treatment_as_an_upload(
        link, fake_ingest, treatment):
    """A source that is chunked and embedded but never tagged, with no timeline
    events and no capture record, LOOKS ingested and behaves half-ingested —
    and nothing downstream announces the omission. The folder path must ask for
    the same post-ingest treatment an upload gets."""
    _scan(link["id"])
    assert len(treatment) == 4, f"only {len(treatment)} of 4 files were finalised"
    for call in treatment:
        assert call["origin"] == "linked_folder", (
            "origin must record how the source arrived, or 'where did this come "
            "from' stops being answerable"
        )
        assert call["raw_bytes"], "image-bearing formats need the original bytes"
        assert call["source_id"]
        assert call["filename"]


def test_a_failed_ingest_is_not_finalised(link, monkeypatch, treatment):
    """Treatment applies to sources that actually landed. Finalising a failure
    would tag a source that does not exist."""
    class _Broken:
        async def process(self, content, filename, notebook_id, reporter=None):
            raise ValueError("no text content could be extracted")

    import services.document_processor as dp
    monkeypatch.setattr(dp, "document_processor", _Broken())
    _scan(link["id"])
    assert treatment == [], "a failed ingest was sent for post-processing"


def test_post_ingest_failure_never_fails_the_ingest(link, fake_ingest, monkeypatch):
    """The source is already chunked and searchable by the time treatment runs.
    A tagger outage must not mark a good source bad."""
    async def _boom(*a, **kw):
        raise RuntimeError("auto-tagger unavailable")

    import services.post_ingest as pi
    monkeypatch.setattr(pi, "finalize_source", _boom)
    r = _scan(link["id"])
    assert r.ingested == 4 and r.failed == 0, r.to_dict()


# ── the contract with document_processor ────────────────────────────────────
#
# 2026-09-16 field report: "the new file was not auto tagged". `process` returns
# {"source_id": ...}; the watcher read `.get("id")` and got None. That None then
# flowed into the provenance stamp, the ledger and the tagger — and every step
# "succeeded". Nothing raised, nothing logged, and the evidence was a ledger
# column full of empty strings. These tests make the same mistake impossible to
# make quietly.

def test_the_ingest_result_key_is_pinned():
    """If `process` ever renames its return key, this fails HERE — loudly, in a
    test — instead of silently producing untagged sources in the field."""
    import ast
    import inspect
    from services.document_processor import DocumentProcessor

    src = inspect.getsource(DocumentProcessor.process)
    returns = [n for n in ast.walk(ast.parse(src.lstrip())) if isinstance(n, ast.Return)]
    keys = set()
    for r in returns:
        if isinstance(r.value, ast.Dict):
            keys |= {k.value for k in r.value.keys if isinstance(k, ast.Constant)}
    assert "source_id" in keys, (
        f"document_processor.process no longer returns 'source_id' (returns {keys}). "
        f"folder_watcher._ingest_into reads that key."
    )


def test_the_ledger_records_the_real_source_id(link, fake_ingest):
    """An empty source_id column is what exposed the bug. It must stay full:
    without it, 'which source came from which file' is unanswerable, and the
    dedup + provenance paths all operate on nothing."""
    _scan(link["id"])
    rows = folder_link_store.seen_map(link["id"])
    ingested = [r for r in rows.values() if r["status"] == "ingested"]
    assert ingested, "nothing was ingested"
    missing = [r["abs_path"] for r in ingested if not r.get("source_id")]
    assert not missing, f"ledger rows with no source_id: {missing}"


def test_an_ingest_that_yields_no_id_fails_loudly(link, monkeypatch):
    """Better a visible failure than a source that looks fine and is not."""
    class _NoId:
        async def process(self, content, filename, notebook_id, reporter=None):
            return {"filename": filename, "chunks": 1}      # no id of any kind

    import services.document_processor as dp
    monkeypatch.setattr(dp, "document_processor", _NoId())
    r = _scan(link["id"])
    assert r.ingested == 0 and r.failed == 4, r.to_dict()
    row = next(iter(folder_link_store.seen_map(link["id"]).values()))
    assert "no source id" in (row["error"] or "").lower()


def test_the_treatment_receives_a_real_id_and_the_text(link, fake_ingest, treatment):
    """The two things the tagger actually needs. Handing it None and "" is what
    produced an untagged file with no error anywhere."""
    _scan(link["id"])
    assert treatment, "nothing was finalised"
    for call in treatment:
        assert call["source_id"], "finalize_source was handed a null source id"


# ── excluding a tool's own duplicate output ─────────────────────────────────
#
# 2026-09-22 field report: the meeting recorder writes each set of notes TWICE —
# markdown, and a styled HTML page that opens in the browser — so both landed
# in the notebook. A document indexed twice is worse than indexed once: it
# competes with its own duplicate for retrieval, and a citation could land on
# either copy.
#
# HTML is still ingested everywhere else. This is about a folder some tool
# writes into, not about the format.

@pytest.fixture
def paired(tmp_path):
    """A folder shaped like the recorder's output: X.md beside X.html."""
    d = tmp_path / "meeting notes"
    d.mkdir()
    for stem in ("2026-09-21 sync", "2026-09-22 1-1"):
        (d / f"{stem}.md").write_text(f"# {stem}\n\nDecisions were made.")
        (d / f"{stem}.html").write_text(f"<html><body><h1>{stem}</h1></body></html>")
    return d


def test_excluded_files_are_never_even_candidates(paired):
    _mk_notebook("nb-excl")
    link = folder_link_store.create_link(
        path=str(paired), notebook_id="nb-excl", exclude=["*.html"])
    try:
        names = {c.name for c in folder_watcher.discover(link)}
        assert names == {"2026-09-21 sync.md", "2026-09-22 1-1.md"}
    finally:
        folder_link_store.delete_link(link["id"])


def test_without_an_exclusion_both_copies_are_taken(paired):
    """The behaviour being fixed — kept as a test so the exclusion is provably
    the thing doing the work, not a coincidence of the fixture."""
    _mk_notebook("nb-both")
    link = folder_link_store.create_link(path=str(paired), notebook_id="nb-both")
    try:
        assert len(folder_watcher.discover(link)) == 4
    finally:
        folder_link_store.delete_link(link["id"])


def test_an_exclusion_beats_an_inclusion(paired):
    """*.html appears in the default patterns — the exclusion has to win, or
    declaring one would do nothing."""
    _mk_notebook("nb-beat")
    link = folder_link_store.create_link(
        path=str(paired), notebook_id="nb-beat",
        patterns=["*.md", "*.html"], exclude=["*.html"])
    try:
        assert all(c.name.endswith(".md") for c in folder_watcher.discover(link))
    finally:
        folder_link_store.delete_link(link["id"])


def test_exclusions_ignore_case(paired):
    (paired / "LOUD.HTML").write_text("<html></html>")
    _mk_notebook("nb-case")
    link = folder_link_store.create_link(
        path=str(paired), notebook_id="nb-case", exclude=["*.html"])
    try:
        assert "LOUD.HTML" not in {c.name for c in folder_watcher.discover(link)}
    finally:
        folder_link_store.delete_link(link["id"])


def test_an_exclusion_can_be_added_to_an_existing_link(paired):
    """Anyone who linked before the exclusion existed must not have to unlink
    and start over to stop the duplicates."""
    _mk_notebook("nb-later")
    link = folder_link_store.create_link(path=str(paired), notebook_id="nb-later")
    try:
        assert len(folder_watcher.discover(link)) == 4
        updated = folder_link_store.update_link(link["id"], exclude=["*.html"])
        assert updated["exclude"] == ["*.html"]
        assert len(folder_watcher.discover(updated)) == 2
    finally:
        folder_link_store.delete_link(link["id"])


def test_links_exclude_nothing_by_default(paired):
    """HTML from anywhere else — uploads, browser captures, a folder of saved
    pages someone linked deliberately — is unaffected."""
    _mk_notebook("nb-default")
    link = folder_link_store.create_link(path=str(paired), notebook_id="nb-default")
    try:
        assert link["exclude"] == []
    finally:
        folder_link_store.delete_link(link["id"])


def test_a_hand_linked_companion_folder_still_gets_its_exclusions(tmp_path, monkeypatch):
    """2026-09-23 field report: the Smart Folder was still sorting .html files.

    The exclusion was applied only by the companion's `connect` flow, so linking
    the SAME folder by hand in Settings produced a link with none — the
    exclusion belonged to how the link was made rather than to what it points
    at. It belongs to the path.
    """
    from services import companions as svc
    d = tmp_path / "Meeting Notes"
    d.mkdir()
    fake = {"id": "t", "name": "T", "produces": {"dir": str(d), "ignore": ["*.html"]}}
    monkeypatch.setattr(svc, "load_manifests", lambda: [fake])
    assert svc.ignores_for_path(str(d)) == ["*.html"]
    assert svc.ignores_for_path(str(tmp_path / "somewhere else")) == []


def test_links_made_before_the_exclusion_existed_are_repaired(tmp_path, monkeypatch):
    """Someone who linked the folder last week must not have to unlink and
    start over to stop the duplicates."""
    from services import companions as svc
    _mk_notebook("nb-recon")
    d = tmp_path / "Meeting Notes"
    d.mkdir()
    (d / "a.md").write_text("# a")
    link = folder_link_store.create_link(path=str(d), notebook_id="nb-recon")
    try:
        assert link["exclude"] == []
        monkeypatch.setattr(svc, "load_manifests",
                            lambda: [{"id": "t", "produces": {"dir": str(d),
                                                              "ignore": ["*.html"]}}])
        assert svc.reconcile_folder_exclusions() == 1
        assert folder_link_store.get_link(link["id"])["exclude"] == ["*.html"]
    finally:
        folder_link_store.delete_link(link["id"])


def test_reconciling_leaves_a_deliberate_choice_alone(tmp_path, monkeypatch):
    """A user who cleared the exclusion meant it. Additive only."""
    from services import companions as svc
    _mk_notebook("nb-keep")
    d = tmp_path / "Meeting Notes"
    d.mkdir()
    link = folder_link_store.create_link(path=str(d), notebook_id="nb-keep",
                                         exclude=["*.tmp"])
    try:
        monkeypatch.setattr(svc, "load_manifests",
                            lambda: [{"id": "t", "produces": {"dir": str(d),
                                                              "ignore": ["*.html"]}}])
        assert svc.reconcile_folder_exclusions() == 0
        assert folder_link_store.get_link(link["id"])["exclude"] == ["*.tmp"]
    finally:
        folder_link_store.delete_link(link["id"])


def test_an_excluded_file_never_reaches_the_review_queue(watched, smart_analysis):
    """A Smart Folder does something a plain link does not — it puts a CARD IN
    FRONT OF THE USER. An excluded file appearing there is worse than one being
    ingested quietly, because it demands a decision nobody should be asked for.
    """
    from storage.smart_folder_store import smart_folder_store
    (watched / "notes.html").write_text("<html><body>dupe</body></html>")
    lk = folder_link_store.create_link(path=str(watched), notebook_id=None,
                                       exclude=["*.html"])
    try:
        _scan(lk["id"])
        queued = {q["filename"] for q in smart_folder_store.list_pending(link_id=lk["id"])}
        assert not any(n.endswith(".html") for n in queued), queued
    finally:
        smart_folder_store.forget_link(lk["id"])
        folder_link_store.delete_link(lk["id"])
