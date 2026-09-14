"""CI-pure tests for canvas_artifacts (Canvas evolution Phase 1 — artifacts → thread nodes).

Model-free: exercises the pure row→node builders + the async aggregation with fake stores
(no real DB, no I/O). Confirms every artifact type becomes a renderable thread node and that
build_nodes appends them.
"""
import asyncio

from services import canvas_artifacts as ca
from services import canvas_populate as cp


def test_pure_builders_shape_and_render_type():
    a = ca._node_audio({"audio_id": "a1", "topic": "Pipeline forecasting",
                        "duration_minutes": 8, "status": "completed", "created_at": "t"})
    assert a["kind"] == "artifact" and a["ref_type"] == "audio" and a["ref_id"] == "a1"
    assert a["title"] == "Pipeline forecasting" and a["snapshot"]["type"] == "markdown"

    q = ca._node_quiz({"quiz_id": "q1", "topic": "Fiscal calendar",
                       "num_questions": 5, "difficulty": "hard", "created_at": "t"})
    assert q["ref_type"] == "quiz" and "5 questions" in q["snapshot"]["payload"]

    # A visual with SVG renders as an svg artifact (real content), else falls back to markdown.
    v = ca._node_visual({"visual_id": "v1", "title": "Area rollup",
                         "svg_markup": "<svg></svg>", "created_at": "t"})
    assert v["snapshot"]["type"] == "svg"
    v2 = ca._node_visual({"visual_id": "v2", "title": "X", "created_at": "t"})
    assert v2["snapshot"]["type"] == "markdown"

    # Infographic reuses its stored Artifact payload → renders for real.
    i = ca._node_infographic({"infographic_id": "i1", "title": "West",
                              "payload_json": '{"scenes": []}', "created_at": "t"})
    assert i["snapshot"]["type"] == "json:infographic" and i["snapshot"]["payload"] == {"scenes": []}


def test_failed_generations_are_dropped():
    assert ca._node_audio({"audio_id": "a", "topic": "x", "status": "failed"}) is None
    assert ca._node_video({"video_id": "v", "topic": "x", "status": "error"}) is None


def test_build_nodes_appends_artifacts():
    a = ca._node_audio({"audio_id": "a1", "topic": "T", "status": "completed", "created_at": "t"})
    q = ca._node_quiz({"quiz_id": "q1", "topic": "T", "num_questions": 3, "created_at": "t"})
    nodes = cp.build_nodes({"queries": []}, [], {}, artifact_nodes=[a, q])
    assert len(nodes) == 2 and all(n["kind"] == "artifact" for n in nodes)
    # Chat + artifacts coexist.
    j = {"queries": [{"id": "e1", "query": "what is X?", "answer_preview": "…",
                      "topics": ["X"], "sources_used": []}]}
    mixed = cp.build_nodes(j, [], {}, artifact_nodes=[a])
    kinds = sorted(n["kind"] for n in mixed)
    assert kinds == ["artifact", "chat_turn"]


def test_list_notebook_artifacts_aggregates_all_stores(monkeypatch):
    # Fake each store singleton's async list(); confirm aggregation across types + fail-open.
    class _Store:
        def __init__(self, rows): self._rows = rows
        async def list(self, nb): return self._rows

    class _Boom:
        async def list(self, nb): raise RuntimeError("store down")

    import storage.audio_store as aud
    import storage.video_store as vid
    import storage.quiz_store as qz
    import storage.visual_store as vs
    import storage.infographic_store as ig
    monkeypatch.setattr(aud, "audio_store", _Store([{"audio_id": "a1", "topic": "T", "status": "completed", "created_at": "t"}]))
    monkeypatch.setattr(vid, "video_store", _Boom())  # one store failing must not sink the rest
    monkeypatch.setattr(qz, "quiz_store", _Store([{"quiz_id": "q1", "topic": "T", "num_questions": 4, "created_at": "t"}]))
    monkeypatch.setattr(vs, "visual_store", _Store([{"visual_id": "v1", "title": "V", "svg_markup": "<svg/>", "created_at": "t"}]))
    monkeypatch.setattr(ig, "infographic_store", _Store([{"infographic_id": "i1", "title": "I", "payload_json": "{}", "created_at": "t"}]))

    nodes = asyncio.run(ca.list_notebook_artifacts("nb1"))
    by_ref = {n["ref_type"] for n in nodes}
    assert {"audio", "quiz", "visual", "infographic"} <= by_ref   # video failed → absent, no raise
    assert all(n["kind"] == "artifact" and n["ref_id"] for n in nodes)
