"""Assembled CuratorAgent (Wave 3 split — behavior-preserving mixin merge)."""
from ._base import CuratorConfigMixin
from ._judgment import CuratorJudgmentMixin
from ._brief import CuratorBriefMixin
from ._collection import CuratorCollectionMixin
from ._overwatch import CuratorOverwatchMixin
from ._synthesis import CuratorSynthesisMixin
from ._html import CuratorHtmlMixin


class CuratorAgent(
    CuratorConfigMixin,
    CuratorJudgmentMixin,
    CuratorBriefMixin,
    CuratorCollectionMixin,
    CuratorOverwatchMixin,
    CuratorSynthesisMixin,
    CuratorHtmlMixin,
):
    """
    The overseer of all Collectors. Acts as judge/parent/teacher/cop.
    Has cross-notebook access and editorial judgment.
    """
