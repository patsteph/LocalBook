"""Deterministic unit tests for the living-view synthesis signal (NS-B1).

Dev artifact (not shipped). No live Ollama / DB needed — everything is
monkeypatched.

    .venv/bin/python3 _livingview_test.py

Covers:
  - _split_key parses label:nb:source, label:nb (no source), malformed keys
  - notebook_progress / progress_by_notebook aggregate the queue per notebook
  - the worker pushes synthesis_progress on GENUINE completion of graph jobs
    (monotonic synthesized, reaching total when the queue drains) and emits
    NOTHING for non-graph / no-notebook jobs
"""
import asyncio
import sys

results = []


def check(name, ok, detail=""):
    results.append((name, ok, detail))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"  — {detail}" if detail and not ok else ""))


async def _noop():
    return None


# ────────────────────────────────────────────────────────────────────────
# _split_key + notebook_progress (pure reads, no worker loop)
# ────────────────────────────────────────────────────────────────────────
async def test_keys_and_progress():
    print("[nsb1] _split_key + notebook_progress")
    from services.enrichment_worker import EnrichmentWorker, _split_key
    from services.enrichment_jobs import EnrichmentJob, JobTier

    check("_split_key full", _split_key("graph-deep:nb1:s1") == ("graph-deep", "nb1", "s1"))
    check("_split_key no source", _split_key("community-summaries:nb1") == ("community-summaries", "nb1", None))
    check("_split_key label only", _split_key("weekly-journal") == ("weekly-journal", None, None))
    check("_split_key empty", _split_key("") == (None, None, None))

    w = EnrichmentWorker()
    w.enqueue(EnrichmentJob(key="entities-daydream:nb1:s1", tier=JobTier.DAYDREAM,
                            factory=lambda: _noop(), label="entities-daydream", notebook_id="nb1"))
    w.enqueue(EnrichmentJob(key="graph-deep:nb1:s2", tier=JobTier.DEEP,
                            factory=lambda: _noop(), label="graph-deep", notebook_id="nb1"))
    w.enqueue(EnrichmentJob(key="community-summaries:nb1", tier=JobTier.DEEP,
                            factory=lambda: _noop(), label="community-summaries", notebook_id="nb1"))
    w.enqueue(EnrichmentJob(key="graph-deep:nb2:s9", tier=JobTier.DEEP,
                            factory=lambda: _noop(), label="graph-deep", notebook_id="nb2"))

    prog = w.notebook_progress("nb1")
    check("pending nb1 == 3", prog["pending"] == 3, str(prog))
    check("source_ids_pending nb1 == [s1,s2]", prog["source_ids_pending"] == ["s1", "s2"], str(prog))
    check("nb2 isolated (pending==1)", w.notebook_progress("nb2")["pending"] == 1)
    check("progress_by_notebook keys", set(w.progress_by_notebook().keys()) == {"nb1", "nb2"})

    # in-flight job resolved via _current_key
    w._current_key = "graph-deep:nb1:s3"
    p2 = w.notebook_progress("nb1")
    check("running counts _current_key", p2["running"] == 1 and "s3" in p2["source_ids_pending"], str(p2))


# ────────────────────────────────────────────────────────────────────────
# broadcast on genuine completion (drives the worker loop)
# ────────────────────────────────────────────────────────────────────────
async def test_broadcast_on_completion():
    print("[nsb1] synthesis_progress broadcast on drain")
    import services.enrichment_worker as ewm
    from services.enrichment_worker import EnrichmentWorker
    from services.enrichment_jobs import EnrichmentJob, JobTier
    from services import presence
    from services.presence import Tier

    ewm._IDLE_RECHECK = 0.05
    ewm._POLL = 0.02
    ewm._OLLAMA_QUIET = 0.0

    presence.current_tier = lambda: Tier.AWAY
    presence.is_active = lambda: False
    presence.system_busy = lambda *a, **k: False
    presence.memory_pressure = lambda: False

    # M = 1 completed source (s1)
    import storage.source_store as ss_mod
    async def fake_list(nb):
        return [{"status": "completed", "id": "s1"}]
    ss_mod.source_store.list = fake_list

    import services.community_detection as cd_mod
    cd_mod.community_detector.get_all_communities = lambda nb: []
    cd_mod.community_detector.count_missing_summaries = lambda nb: 0

    import api.constellation_ws as ws_mod
    captured = []
    async def cap(data):
        captured.append(data)
    ws_mod.notify_synthesis_progress = cap

    w = EnrichmentWorker()
    w.enqueue(EnrichmentJob(key="entities-daydream:nb:s1", tier=JobTier.DAYDREAM,
                            factory=lambda: _noop(), label="entities-daydream", notebook_id="nb"))
    w.enqueue(EnrichmentJob(key="graph-deep:nb:s1", tier=JobTier.DEEP,
                            factory=lambda: _noop(), label="graph-deep", notebook_id="nb"))
    w.enqueue(EnrichmentJob(key="community-summaries:nb", tier=JobTier.DEEP,
                            factory=lambda: _noop(), label="community-summaries", notebook_id="nb"))
    # non-graph label → no broadcast; no-notebook job → no broadcast
    w.enqueue(EnrichmentJob(key="mem-compact:nb", tier=JobTier.DEEP,
                            factory=lambda: _noop(), label="mem-compact", notebook_id="nb"))
    w.enqueue(EnrichmentJob(key="weekly-journal", tier=JobTier.NIGHT,
                            factory=lambda: _noop(), label="weekly-journal", notebook_id=None))

    await w.start()
    await asyncio.sleep(1.0)
    await w.stop()

    check("exactly 3 graph broadcasts (non-graph/no-nb skipped)", len(captured) == 3, f"got {len(captured)}")
    if captured:
        synth = [c["synthesized"] for c in captured]
        check("synthesized monotonic non-decreasing", all(b >= a for a, b in zip(synth, synth[1:])), str(synth))
        last = captured[-1]
        check("final synthesized == total == 1", last["synthesized"] == 1 and last["total"] == 1, str(last))
        check("payload carries notebook_id", last["notebook_id"] == "nb")
        check("payload has community fields", "communities_built" in last and "communities_total" in last)


async def main():
    await test_keys_and_progress()
    await test_broadcast_on_completion()
    print()
    n_pass = sum(1 for _, ok, _ in results if ok)
    print(f"{n_pass}/{len(results)} checks passed")
    sys.exit(0 if n_pass == len(results) else 1)


if __name__ == "__main__":
    asyncio.run(main())
