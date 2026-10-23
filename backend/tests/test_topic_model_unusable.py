"""A topic model saved with malformed centroids must be detected, not fail on every document."""
import asyncio

import numpy as np

from services import topic_modeling as tm


class _M:
    def __init__(self, te): self.topic_embeddings_ = te; self._outliers = 1


def test_usable_topic_embeddings():
    assert tm.usable_topic_embeddings(_M(np.zeros((3, 8), dtype="float32"))).shape == (3, 8)
    assert tm.usable_topic_embeddings(_M(np.array(0.0, dtype="float32"))) is None     # the 0-d value found in the wild
    assert tm.usable_topic_embeddings(_M(np.zeros((0, 8)))) is None
    assert tm.usable_topic_embeddings(_M(None)) is None
    assert tm.usable_topic_embeddings(object()) is None


def test_assign_raises_a_named_error_for_a_malformed_model():
    svc = tm.TopicModelingService.__new__(tm.TopicModelingService)
    svc._model = _M(np.array(0.0, dtype="float32"))
    try:
        asyncio.run(svc._assign_to_existing_topics(["x"], np.zeros((1, 8), dtype="float32")))
        raise AssertionError("expected UnusableTopicModel")
    except tm.UnusableTopicModel:
        pass
