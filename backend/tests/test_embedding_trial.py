"""The LLM Locker's embedding trial: scoring, the distractor haystack, the job — no real models."""

import asyncio

import numpy as np
import pytest

from services import embedding_trial as T

CHUNKS = [{"text": "Arctic produces 1024-dimensional dense vectors.", "parent_text": ""},
          {"text": "The volume opens at login.", "parent_text": ""},
          {"text": "Unrelated passage about maps.", "parent_text": "", "distractor": True}]
GOLD = [{"id": "dims", "question": "how many dims?", "markers": ["1024-dimensional"]},
        {"id": "vol", "question": "when does it open?", "markers": ["opens at login"]}]


def _unit(*rows):
    a = np.array(rows, dtype=np.float32)
    return a / np.linalg.norm(a, axis=1, keepdims=True)


def test_scoring_finds_the_right_passage_and_reports_rank():
    docs = _unit([1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0])
    good = _unit([1, 0.1, 0, 0], [0, 1, 0.1, 0])
    s = T._score(docs, good, CHUNKS, GOLD)
    assert (s["hit_at_1"], s["hit_at_5"], s["mrr"], s["missed"]) == (1.0, 1.0, 1.0, [])
    swapped = _unit([0, 0.9, 0, 0.1], [0.9, 0, 0, 0.1])         # each query nearest the wrong passage
    s = T._score(docs, swapped, CHUNKS, GOLD)
    assert s["hit_at_1"] == 0.0 and s["hit_at_5"] == 1.0 and s["mean_rank"] == 2.0


def test_matryoshka_truncation_renormalises():
    docs = _unit([1, 0, 0, 3], [0, 1, 0, 3], [0, 0, 1, 3])
    q = _unit([1, 0, 0, 3], [0, 1, 0, 3])
    full, short = T._score(docs, q, CHUNKS, GOLD), T._score(docs, q, CHUNKS, GOLD, dims=2)
    assert full["dims"] == 4 and short["dims"] == 2 and short["hit_at_1"] == 1.0


def test_distractors_are_read_only_deterministic_and_never_an_answer(tmp_path, monkeypatch):
    lancedb = pytest.importorskip("lancedb")
    from config import settings
    db = lancedb.connect(str(tmp_path))
    rows = [{"text": f"A long enough distractor passage number {i}, about something else entirely, comfortably over eighty characters.",
             "vector": [0.0, 1.0]} for i in range(30)]
    rows.append({"text": "This passage leaks the answer: it produces 1024-dimensional dense vectors, really.",
                 "vector": [1.0, 0.0]})
    db.create_table("notebook_x", data=rows)
    monkeypatch.setattr(settings, "db_path", tmp_path)
    a = T._distractors(10, ["1024-dimensional"])
    b = T._distractors(10, ["1024-dimensional"])
    assert len(a) == 10 and [x["text"] for x in a] == [x["text"] for x in b]
    assert all("1024-dimensional" not in x["text"] for x in T._distractors(100, ["1024-dimensional"]))
    assert db.open_table("notebook_x").count_rows() == 31              # untouched


def test_the_job_compares_both_models_and_unloads_the_candidate(monkeypatch):
    from config import settings
    from services.mlx_engine import mlx_engine
    monkeypatch.setattr(settings, "embedding_model", "live/model")

    async def corpus():
        return list(CHUNKS[:2])
    monkeypatch.setattr(T, "_corpus", corpus)
    monkeypatch.setattr(T, "_gold", lambda chunks: GOLD)
    monkeypatch.setattr(T, "_distractors", lambda n, m: [CHUNKS[2]])

    async def fake_embed(model, texts):
        out = []
        for t in texts:
            v = [1.0 if "1024" in t or "dims" in t else 0.0, 1.0 if "login" in t or "open" in t else 0.0, 0.1]
            out.append(v)
        return np.asarray(out, dtype=np.float32)
    monkeypatch.setattr(T, "_embed", fake_embed)
    unloaded = []

    async def admit(m, **kw):
        return True

    async def unload(m, **kw):
        unloaded.append(m)
        return True
    monkeypatch.setattr(mlx_engine, "admit", admit)
    monkeypatch.setattr(mlx_engine, "unload", unload)

    async def run():
        T._job.clear()
        assert (await T.start("cand/model"))["started"] is True
        assert (await T.start("cand/model"))["started"] is False          # one at a time
        for _ in range(200):
            if not T.status().get("running"):
                break
            await asyncio.sleep(0.01)
        return T.status()

    st = asyncio.run(run())
    assert st["error"] is None and st["stage"] == "done"
    res = st["result"]
    assert res["live"]["model"] == "live/model" and res["candidate"]["model"] == "cand/model"
    assert res["candidate"]["full"]["hit_at_1"] == 1.0 and res["distractors"] == 1
    assert unloaded == ["cand/model"]
