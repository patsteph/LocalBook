"""Assembled CuratorBrain (Wave 4 split — behavior-preserving mixin merge)."""
from ._digest import DigestMixin
from ._connections import ConnectionsMixin
from ._reflections import ReflectionsMixin
from ._events import EventsMixin
from ._engagement import EngagementMixin
from ._briefing import BriefingMixin
from ._plans import PlansMixin
from ._mental_model import MentalModelMixin
from ._stance import StanceMixin
from ._governance import GovernanceMixin
from ._reputation import ReputationMixin
from ._base import CuratorBrainBase


class CuratorBrain(
    DigestMixin,
    ConnectionsMixin,
    ReflectionsMixin,
    EventsMixin,
    EngagementMixin,
    BriefingMixin,
    PlansMixin,
    MentalModelMixin,
    StanceMixin,
    GovernanceMixin,
    ReputationMixin,
    CuratorBrainBase,
):
    """
    The Curator's compiled knowledge — reads from existing systems, never writes to them.

    Lifecycle:
      - mark_notebook_dirty()  → called by ingestion pipeline (one line)
      - rebuild_notebook_digest() → called by memory_manager Tier 3
      - detect_connections()   → called by memory_manager Tier 3 after digests built
      - get_brief_context()    → called by curator.py morning brief
      - get_digest()           → called by curator.py overwatch fast path
    """
