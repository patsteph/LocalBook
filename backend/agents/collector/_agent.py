"""Assembled CollectorAgent (Wave 6 split — behavior-preserving mixin merge)."""
from ._discovery import DiscoveryMixin
from ._collection import CollectionMixin
from ._dedup import DedupMixin
from ._scoring import ScoringMixin
from ._health import HealthMixin
from ._approval import ApprovalMixin
from ._feedback import FeedbackMixin
from ._base import CollectorAgentBase


class CollectorAgent(
    DiscoveryMixin,
    CollectionMixin,
    DedupMixin,
    ScoringMixin,
    HealthMixin,
    ApprovalMixin,
    FeedbackMixin,
    CollectorAgentBase,
):
    """
    Per-notebook Collector that finds and proposes content.
    Each notebook has its own Collector instance with isolated config.
    """
