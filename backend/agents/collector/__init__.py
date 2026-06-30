"""Collector package — split from the former 2716-line agents/collector.py (Wave 6).
Public API preserved: `from agents.collector import get_collector, CollectorAgent,
CollectionMode, ApprovalMode, CollectedItem, ApprovalQueueItem, clear_collector_cache`."""
from ._models import *  # noqa: F401,F403  (re-exports the 7 supporting classes + Dict/Optional)
from ._agent import CollectorAgent  # noqa: F401


# Registry of active Collectors (singleton per notebook)
_collector_registry: Dict[str, CollectorAgent] = {}


def get_collector(notebook_id: str) -> CollectorAgent:
    """Get or create a Collector for a notebook (cached singleton per notebook)"""
    if notebook_id not in _collector_registry:
        _collector_registry[notebook_id] = CollectorAgent(notebook_id)
    return _collector_registry[notebook_id]


def clear_collector_cache(notebook_id: Optional[str] = None) -> None:
    """Clear collector cache (for testing or notebook deletion)"""
    global _collector_registry
    if notebook_id:
        _collector_registry.pop(notebook_id, None)
    else:
        _collector_registry = {}
