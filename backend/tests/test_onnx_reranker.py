"""The full-precision reranker behaves like FlashRank's and never breaks reranking when absent."""
from pathlib import Path

import pytest

from services import onnx_reranker, rag_search

EXPORT = Path(__file__).resolve().parents[2] / "ios" / "LocalBookLab" / "tools" / "cache" / "ms-marco-MiniLM-L-12-v2-fp32"


class _Req:
    def __init__(self, query, passages):
        self.query, self.passages = query, passages


@pytest.mark.skipif(not (EXPORT / "model.onnx").exists(), reason="export tools/export_reranker_onnx.py first")
def test_same_shape_scores_and_order_as_flashranks_ranker():
    r = onnx_reranker.FullPrecisionReranker(EXPORT)
    passages = [{"id": 0, "text": "Cats are small domesticated mammals.", "meta": {"original_idx": 0}},
                {"id": 1, "text": "Agentic RAG lets a model decide when to retrieve and re-plan its search.", "meta": {"original_idx": 1}},
                {"id": 2, "text": "The recipe calls for two eggs and a cup of flour.", "meta": {"original_idx": 2}}]
    out = r.rerank(_Req("How does agentic RAG differ from standard RAG?", [dict(p) for p in passages]))
    assert [p["id"] for p in out][0] == 1                       # the relevant passage wins
    assert all(0.0 <= float(p["score"]) <= 1.0 and "meta" in p for p in out)
    assert [p["score"] for p in out] == sorted((p["score"] for p in out), reverse=True)


def test_falls_back_to_flashrank_when_the_model_file_is_missing(tmp_path, monkeypatch):
    from config import settings
    monkeypatch.setattr(settings, "reranker_precision", "fp32", raising=False)
    monkeypatch.setattr(settings, "data_dir", tmp_path, raising=False)
    monkeypatch.setattr(onnx_reranker, "fetch_in_background", lambda: None)
    monkeypatch.setattr(rag_search, "_fp32_reranker", None)
    monkeypatch.setattr(rag_search, "_flashrank_reranker", None)

    class _StubFlashRank:                                  # never touch the network or the real model here
        def __init__(self, **kwargs):
            pass
    monkeypatch.setattr(rag_search, "FlashRanker", _StubFlashRank, raising=False)
    got = rag_search._get_reranker()
    assert isinstance(got, _StubFlashRank)                 # FlashRank keeps working while the file is missing
