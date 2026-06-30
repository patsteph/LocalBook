"""
Collector Agent - Per-notebook content discovery and collection

Each notebook has its own Collector with:
- Intent profile (what to look for)
- Source configuration (RSS, web pages, etc.)
- Collection mode (manual, automatic, hybrid)
- Approval workflow settings

Enhancements included:
- Immediate first sweep on creation
- Duplicate detection via embeddings
- Source health monitoring
- Confidence scoring with explanations
- Approval queue with auto-expire
- Structured rejection feedback
"""
import hashlib
import json
import logging
import uuid
import yaml
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Any, List, Optional
from enum import Enum
from pydantic import BaseModel, Field

from storage.memory_store import memory_store, AgentNamespace
from models.memory import ArchivalMemoryEntry, MemorySourceType, MemoryImportance
from services.ollama_service import ollama_service
from config import settings

logger = logging.getLogger(__name__)


class CollectionMode(str, Enum):
    MANUAL = "manual"      # User adds everything
    AUTOMATIC = "automatic"  # Collector adds everything
    HYBRID = "hybrid"      # Both user and Collector add


class ApprovalMode(str, Enum):
    TRUST_ME = "trust_me"      # Auto-approve all
    SHOW_ME = "show_me"        # Queue all for approval
    MIXED = "mixed"            # Auto-approve high confidence, queue uncertain


class SourceHealth(str, Enum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"  # Slow or intermittent
    FAILING = "failing"    # Multiple failures
    DEAD = "dead"          # No response for extended period


class CollectorConfig(BaseModel):
    """Configuration for a notebook's Collector"""
    name: str = "Scout"
    notebook_purpose: str = ""  # Template type: company_intel, topic_research, industry_watch, project_archive, people, custom
    subject: str = ""  # Key research entity (e.g. "Costco") - combined with focus_areas for searches
    intent: str = ""
    focus_areas: List[str] = Field(default_factory=list)
    excluded_topics: List[str] = Field(default_factory=list)
    disabled_sources: List[str] = Field(default_factory=list)  # Source IDs/URLs that are paused
    company_profile: Dict[str, Any] = Field(default_factory=dict)  # Cached company profile from discovery
    collection_mode: CollectionMode = CollectionMode.HYBRID
    approval_mode: ApprovalMode = ApprovalMode.MIXED

    sources: Dict[str, Any] = Field(default_factory=lambda: {
        "rss_feeds": [],
        "web_pages": [],
        "news_keywords": []
    })
    
    schedule: Dict[str, Any] = Field(default_factory=lambda: {
        "frequency": "daily",
        "max_items_per_run": 10
    })
    
    filters: Dict[str, Any] = Field(default_factory=lambda: {
        "max_age_days": 30,
        "min_relevance": 0.5,
        "language": "en"
    })
    
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class CollectedItem(BaseModel):
    """An item found by a Collector"""
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    title: str
    url: Optional[str] = None
    content: str
    preview: str = ""
    source_name: str
    source_type: str = "web"  # rss, web, news, manual
    source_url: str = ""  # feed/page URL this item was fetched from
    collected_at: datetime = Field(default_factory=datetime.utcnow)
    
    # Confidence scoring (Enhancement #8)
    relevance_score: float = 0.5
    source_trust: float = 0.5
    freshness_score: float = 1.0
    overall_confidence: float = 0.5
    confidence_reasons: List[str] = Field(default_factory=list)
    
    # Duplicate detection (Enhancement #4)
    content_hash: str = ""
    is_duplicate: bool = False
    duplicate_of: Optional[str] = None
    
    # Temporal Intelligence (Enhancement #6)
    delta_summary: Optional[str] = None       # "NEW: European expansion wasn't in prior coverage"
    is_new_topic: bool = True                 # True if no related existing content
    temporal_context: Optional[str] = None    # "This follows your Jan 2026 Rockstar articles"
    knowledge_overlap: float = 0.0            # 0.0 = entirely new, 1.0 = fully known
    related_titles: List[str] = Field(default_factory=list)  # Titles of related existing items
    
    # Approval status
    status: str = "pending"  # pending, approved, rejected, expired
    curator_decision: Optional[str] = None
    rejection_reason: Optional[str] = None

    # Depth+1 link expansion provenance — set when this item was queued
    # by the link expander (rather than the regular collector). The UI
    # uses these to render a "From: <parent>" badge and a
    # "📌 Also relevant: <NotebookX>" cross-notebook hint. Empty/None on
    # all regular collector items, so existing behaviour is unchanged.
    parent_source_id: Optional[str] = None
    discovery_url: Optional[str] = None  # URL of the parent page that linked here
    cross_notebook_matches: List[Dict[str, Any]] = Field(
        default_factory=list,
        description="[{notebook_id, notebook_name, score, snippet}] from curator cross-notebook check"
    )


class SourceHealthRecord(BaseModel):
    """Health tracking for a source"""
    source_id: str
    source_url: str
    health: SourceHealth = SourceHealth.HEALTHY
    last_success: Optional[datetime] = None
    last_failure: Optional[datetime] = None
    failure_count: int = 0
    avg_response_time_ms: float = 0
    items_collected: int = 0


class ApprovalQueueItem(BaseModel):
    """Item in the approval queue"""
    item: CollectedItem
    queued_at: datetime = Field(default_factory=datetime.utcnow)
    expires_at: datetime = Field(default_factory=lambda: datetime.utcnow() + timedelta(days=7))
    batch_id: Optional[str] = None  # For batch operations
