"""Adding a source AFTER a topic rebuild must still assign topics.

Regression test for a deterministic fault that looked intermittent. BERTopic's
`transform()` branches on the TYPE of `hdbscan_model`:

  * loaded from disk  -> carries a `BaseCluster` -> cosine path -> works
  * freshly fit       -> carries the real clusterer -> `hdbscan_model.predict()`

We build `sklearn.cluster.HDBSCAN`, which has no `predict` method at all — only
the standalone `hdbscan` package does, and BERTopic's `is_supported_hdbscan()`
accepts only that or cuML. So every source ingested between a rebuild and the
next process restart raised AttributeError and silently got no topics.

`_assign_to_existing_topics` removes that second path rather than repairing it.
These tests pin the behaviour that matters: assignment works against a
freshly-fit model, outliers still resolve to -1, and `transform()` is not what
gets called.
"""
import ast
import asyncio
import inspect
from pathlib import Path

import numpy as np
import pytest

from services.topic_modeling import TopicModelingService


class _FakeBERTopic:
    """Stands in for a model still in memory from fit_all().

    `topic_embeddings_[0]` is a zero row because that is exactly what BERTopic
    stores for the -1 outlier topic, and the zero row is what makes the
    similarity maths worth testing rather than assuming.
    """

    def __init__(self, centroids, outliers=1):
        self.topic_embeddings_ = np.asarray(centroids, dtype=np.float32)
        self._outliers = outliers
        self.transform_calls = 0

    def transform(self, texts, embeddings=None):
        self.transform_calls += 1
        raise AssertionError(
            "transform() must not be called — it is the broken path this fix removed"
        )


def _service(model):
    svc = TopicModelingService.__new__(TopicModelingService)
    svc._model = model
    return svc


@pytest.fixture
def centroids():
    # index 0 = the -1 outlier (zeros), then two real, well-separated topics
    return np.array(
        [[0.0, 0.0, 0.0, 0.0], [1.0, 0.0, 0.0, 0.0], [0.0, 1.0, 0.0, 0.0]],
        dtype=np.float32,
    )


def test_assigns_against_a_freshly_fit_model(centroids):
    """The exact sequence that failed: rebuild, then add a source."""
    svc = _service(_FakeBERTopic(centroids))
    embeddings = np.array([[0.9, 0.1, 0.0, 0.0], [0.05, 0.95, 0.0, 0.0]], dtype=np.float32)

    topics, probs = asyncio.run(svc._assign_to_existing_topics(["a", "b"], embeddings))

    # centroid row 1 -> topic 0, row 2 -> topic 1, after the -1 offset
    assert topics.tolist() == [0, 1]
    assert all(0.0 <= p <= 1.0 for p in probs)


def test_transform_is_never_called(centroids):
    """_FakeBERTopic.transform raises if reached; this pins that it isn't."""
    model = _FakeBERTopic(centroids)
    svc = _service(model)
    asyncio.run(svc._assign_to_existing_topics(["a"], np.array([[1.0, 0.0, 0.0, 0.0]], dtype=np.float32)))
    assert model.transform_calls == 0


def test_a_document_similar_to_nothing_becomes_an_outlier(centroids):
    """The zero outlier row must absorb documents rather than a random topic winning.

    sklearn leaves a zero row at similarity 0, so anything negatively similar to
    every real topic lands on -1 — which is the honest answer.
    """
    svc = _service(_FakeBERTopic(centroids))
    embeddings = np.array([[-1.0, -1.0, 0.0, 0.0]], dtype=np.float32)
    topics, _ = asyncio.run(svc._assign_to_existing_topics(["unrelated"], embeddings))
    assert topics.tolist() == [-1]


def test_missing_centroids_raise_rather_than_return_nothing(centroids):
    """A fitted model with no centroids is a fault, not an empty result.

    Returning {} here is what let the original bug hide.
    """
    svc = _service(_FakeBERTopic(np.zeros((0, 4), dtype=np.float32)))
    with pytest.raises(RuntimeError, match="topic_embeddings_"):
        asyncio.run(svc._assign_to_existing_topics(["a"], np.array([[1.0, 0, 0, 0]], dtype=np.float32)))


def test_sklearn_hdbscan_still_has_no_predict():
    """Pins WHY this fix exists, so a future sklearn release is noticed.

    If sklearn ever grows HDBSCAN.predict, BERTopic's transform() would start
    working for a freshly-fit model and this workaround could be reconsidered.
    """
    from sklearn.cluster import HDBSCAN

    assert not hasattr(HDBSCAN, "predict"), (
        "sklearn.cluster.HDBSCAN gained .predict — revisit _assign_to_existing_topics"
    )


def test_incremental_path_does_not_call_bertopic_transform():
    """Structural guard: the call site must not regress back to transform()."""
    src = Path(inspect.getfile(TopicModelingService)).read_text()
    tree = ast.parse(src)
    fn = next(
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.AsyncFunctionDef) and n.name == "add_documents"
    )
    called = {
        node.func.attr
        for node in ast.walk(fn)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }
    assert "transform" not in called, "add_documents must not call self._model.transform()"


def test_against_real_bertopic_not_a_fake():
    """The one that actually matters: real BERTopic, real sklearn HDBSCAN.

    Every test above uses a fake model, and a fake proves nothing about the
    library whose behaviour is the entire bug. This builds the model exactly as
    `fit_all()` does, confirms `transform()` still raises the production error on
    it, and confirms the replacement assigns correctly.

    It is also the canary: if BERTopic or sklearn ever change such that
    `transform()` stops raising here, the workaround can be revisited.
    """
    from bertopic import BERTopic
    from sklearn.cluster import HDBSCAN
    from sklearn.decomposition import PCA

    rng = np.random.default_rng(3)
    n = 40
    emb = np.vstack(
        [
            rng.normal(0, 0.05, (n, 1024)) + np.eye(1024)[0],
            rng.normal(0, 0.05, (n, 1024)) + np.eye(1024)[7],
        ]
    ).astype(np.float32)
    texts = [f"alpha beta gamma delta document {i}" for i in range(n)] + [
        f"zulu yankee xray whiskey document {i}" for i in range(n)
    ]

    model = BERTopic(
        umap_model=PCA(n_components=min(50, len(texts) - 1)),
        hdbscan_model=HDBSCAN(
            min_cluster_size=5,
            min_samples=2,
            metric="euclidean",
            cluster_selection_method="leaf",
        ),
        verbose=False,
    )
    model.fit_transform(texts, embeddings=emb)

    new_emb = (rng.normal(0, 0.05, (3, 1024)) + np.eye(1024)[0]).astype(np.float32)
    new_txt = ["alpha beta gamma new one", "alpha beta delta new two", "alpha gamma new three"]

    # The fault, reproduced against the real library.
    with pytest.raises(AttributeError, match="predict"):
        model.transform(new_txt, embeddings=new_emb)

    # The replacement, on the same model.
    svc = _service(model)
    topics, probs = asyncio.run(svc._assign_to_existing_topics(new_txt, new_emb))
    assert len(topics) == 3
    # Assert on the CONTRACT (valid ids, valid probabilities), not on which
    # cluster wins. An earlier draft asserted a specific cluster and passed alone
    # while failing in-file — which is how the nondeterministic PCA fit below was
    # found. Pinning an arbitrary outcome would have hidden it.
    known = set(model.topics_)
    assert set(topics.tolist()) <= known, f"{set(topics.tolist())} not in {known}"
    assert all(0.0 <= p <= 1.0 for p in probs)


def test_topic_fit_is_reproducible():
    """Two rebuilds of the same notebook must produce the same topics.

    They did not. PCA selects the RANDOMIZED SVD solver at these shapes and, with
    `random_state=None`, seeds it from global numpy state — so the result depended
    on whatever else had drawn from numpy first. Measured on identical input
    before the fix: 4 topics, then 6, then 4.

    This is why `_PCA_RANDOM_STATE` exists. Without it no change to clustering can
    be evaluated, because the baseline moves on its own.
    """
    from bertopic import BERTopic
    from sklearn.cluster import HDBSCAN
    from sklearn.decomposition import PCA

    from services.topic_modeling import _PCA_RANDOM_STATE

    def fit_once():
        rng = np.random.default_rng(3)
        n = 40
        emb = np.vstack(
            [
                rng.normal(0, 0.05, (n, 1024)) + np.eye(1024)[0],
                rng.normal(0, 0.05, (n, 1024)) + np.eye(1024)[7],
            ]
        ).astype(np.float32)
        texts = [f"alpha beta gamma delta document {i}" for i in range(n)] + [
            f"zulu yankee xray whiskey document {i}" for i in range(n)
        ]
        m = BERTopic(
            umap_model=PCA(n_components=min(50, len(texts) - 1), random_state=_PCA_RANDOM_STATE),
            hdbscan_model=HDBSCAN(
                min_cluster_size=5,
                min_samples=2,
                metric="euclidean",
                cluster_selection_method="leaf",
            ),
            verbose=False,
        )
        m.fit_transform(texts, embeddings=emb)
        return sorted(set(m.topics_))

    results = []
    for seed in (0, 1000, 2000):
        np.random.seed(seed)  # perturb ONLY the global state
        results.append(fit_once())

    assert results[0] == results[1] == results[2], f"fit is not reproducible: {results}"


def test_pca_sites_all_seed_their_solver():
    """Structural guard: every PCA in this module must fix its random_state."""
    src = Path(inspect.getfile(TopicModelingService)).read_text()
    tree = ast.parse(src)
    pcas = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "PCA"
    ]
    assert pcas, "expected PCA construction sites"
    for call in pcas:
        kwargs = {kw.arg for kw in call.keywords}
        assert "random_state" in kwargs, f"PCA at line {call.lineno} does not seed random_state"
