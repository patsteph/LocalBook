"""Curator-brain package — split from the former 3855-line services/curator_brain.py.
Public API preserved: `from services.curator_brain import curator_brain` is unchanged."""
from ._brain import CuratorBrain  # noqa: F401

# Singleton instantiated LAST (opens brain.db + runs the full 15-table schema at import).
curator_brain = CuratorBrain()
