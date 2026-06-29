"""
Curator Brain — Pre-computed, evolving understanding of the user's research.

Reads from existing systems (knowledge_graph.py LanceDB, source_store).
Writes ONLY to its own brain.db (SQLite WAL) and brain LanceDB.
Never touches the knowledge graph, RAG pipeline, or memory store.

Design principle: every public method is safe to call even if the brain is
empty or partially built. All fallback paths return gracefully so existing
Curator behavior is never degraded.
"""

import asyncio
import json
import logging
import sqlite3
from datetime import datetime, timezone, timedelta
from itertools import combinations
from pathlib import Path
from typing import Dict, List, Optional, Any

import lancedb

from config import settings
from services.ollama_service import ollama_service
from utils.singleflight import KeyedSingleflight

logger = logging.getLogger(__name__)

# PB-1b: dedup per-notebook background re-score so rapid thesis edits don't launch
# concurrent rescores for the same notebook. Audit ref: 10_plan_of_attack PB-1b.
_stance_rescore_sf = KeyedSingleflight("stance-rescore")
