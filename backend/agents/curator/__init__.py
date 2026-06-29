"""Curator package — split from the former 5755-line agents/curator.py.
Public API preserved: `from agents.curator import …` works exactly as before."""
from ._models import *  # noqa: F401,F403
from ._agent import CuratorAgent  # noqa: F401

# Singleton instantiated LAST so transitive lazy imports see a complete package.
curator = CuratorAgent()
