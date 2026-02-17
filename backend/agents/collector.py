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
from services.ollama_client import ollama_client
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


class CollectorAgent:
    """
    Per-notebook Collector that finds and proposes content.
    Each notebook has its own Collector instance with isolated config.
    """
    
    DEFAULT_CONFIG = CollectorConfig()
    APPROVAL_EXPIRY_DAYS = 7
    
    def __init__(self, notebook_id: str):
        self.notebook_id = notebook_id
        self.config = self._load_config()
        self._approval_queue: List[ApprovalQueueItem] = self._load_approval_queue()
        self._source_health: Dict[str, SourceHealthRecord] = {}
        self._content_hashes: set = set()  # For fast duplicate detection
        self._known_urls: set = set()      # URL-based dedup across restarts
        self._init_dedup_state()
    
    def _init_dedup_state(self):
        """Pre-populate dedup sets from existing sources and approval queue so
        Collect Now never re-adds items that are already stored."""
        try:
            from storage.source_store import source_store
            import asyncio

            # Try to get existing sources synchronously (we're in __init__)
            loop = None
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                pass

            if loop and loop.is_running():
                # Schedule as a background task; sets will fill async
                asyncio.ensure_future(self._async_init_dedup())
            else:
                asyncio.run(self._async_init_dedup())
        except Exception as e:
            logger.debug(f"Dedup state init (non-fatal): {e}")

    async def _async_init_dedup(self):
        """Async portion of dedup initialization."""
        try:
            from storage.source_store import source_store
            existing = await source_store.list(self.notebook_id)
            for src in existing:
                url = src.get("url")
                if url:
                    self._known_urls.add(url)
                content = src.get("content", "")
                if content:
                    self._content_hashes.add(self._generate_content_hash(content))
            # Also include URLs from the approval queue
            for q in self._approval_queue:
                if q.item.url:
                    self._known_urls.add(q.item.url)
                if q.item.content_hash:
                    self._content_hashes.add(q.item.content_hash)
            logger.info(f"Dedup init for {self.notebook_id}: {len(self._known_urls)} URLs, {len(self._content_hashes)} hashes")
        except Exception as e:
            logger.debug(f"Async dedup init failed (non-fatal): {e}")

    def _get_config_path(self) -> Path:
        """Get path to this Collector's config file"""
        notebooks_dir = Path(settings.data_dir) / "notebooks" / self.notebook_id
        notebooks_dir.mkdir(parents=True, exist_ok=True)
        return notebooks_dir / "collector.yaml"
    
    def _load_notebook_md(self) -> Optional[str]:
        """Load notebook.md behavioral guidance if it exists.
        
        This is the human-readable personality/guidance layer that shapes
        how the Collector scores and presents content. Complements the
        structured collector.yaml config.
        """
        md_path = Path(settings.data_dir) / "notebooks" / self.notebook_id / "notebook.md"
        if md_path.exists():
            try:
                text = md_path.read_text(encoding="utf-8")
                if text.strip():
                    return text
            except Exception as e:
                logger.debug(f"Could not read notebook.md: {e}")
        return None
    
    def _get_queue_path(self) -> Path:
        """Get path to this Collector's approval queue file"""
        notebooks_dir = Path(settings.data_dir) / "notebooks" / self.notebook_id
        notebooks_dir.mkdir(parents=True, exist_ok=True)
        return notebooks_dir / "approval_queue.json"
    
    def _load_approval_queue(self) -> List[ApprovalQueueItem]:
        """Load persisted approval queue from disk"""
        queue_path = self._get_queue_path()
        if not queue_path.exists():
            return []
        try:
            with open(queue_path, 'r') as f:
                data = json.load(f)
            now = datetime.utcnow()
            items = []
            for entry in data:
                item = ApprovalQueueItem(**{
                    **entry,
                    "item": CollectedItem(**entry["item"]),
                    "queued_at": datetime.fromisoformat(entry["queued_at"]),
                    "expires_at": datetime.fromisoformat(entry["expires_at"]),
                })
                if item.expires_at > now:
                    items.append(item)
            return items
        except Exception as e:
            logger.error(f"Error loading approval queue for {self.notebook_id}: {e}")
            return []
    
    def _save_approval_queue(self) -> None:
        """Persist approval queue to disk"""
        queue_path = self._get_queue_path()
        try:
            data = []
            for q in self._approval_queue:
                entry = q.item.model_dump()
                # Serialize datetimes in nested item
                for k, v in entry.items():
                    if isinstance(v, datetime):
                        entry[k] = v.isoformat()
                data.append({
                    "item": entry,
                    "queued_at": q.queued_at.isoformat(),
                    "expires_at": q.expires_at.isoformat(),
                    "batch_id": q.batch_id,
                })
            with open(queue_path, 'w') as f:
                json.dump(data, f, indent=2, default=str)
        except Exception as e:
            logger.error(f"Error saving approval queue for {self.notebook_id}: {e}")
    
    def _load_config(self) -> CollectorConfig:
        """Load Collector configuration from YAML file"""
        config_path = self._get_config_path()
        
        if config_path.exists():
            try:
                with open(config_path, 'r') as f:
                    data = yaml.safe_load(f)
                    if data:
                        # Convert string enums back to enums
                        if "collection_mode" in data and isinstance(data["collection_mode"], str):
                            data["collection_mode"] = CollectionMode(data["collection_mode"])
                        if "approval_mode" in data and isinstance(data["approval_mode"], str):
                            data["approval_mode"] = ApprovalMode(data["approval_mode"])
                        # Convert ISO strings back to datetimes
                        if "created_at" in data and isinstance(data["created_at"], str):
                            data["created_at"] = datetime.fromisoformat(data["created_at"])
                        if "updated_at" in data and isinstance(data["updated_at"], str):
                            data["updated_at"] = datetime.fromisoformat(data["updated_at"])
                        return CollectorConfig(**data)
            except Exception as e:
                logger.error(f"Error loading collector config for {self.notebook_id}: {e}")
        
        return CollectorConfig()
    
    def _save_config(self) -> None:
        """Save Collector configuration to YAML file"""
        config_path = self._get_config_path()
        config_path.parent.mkdir(parents=True, exist_ok=True)
        
        self.config.updated_at = datetime.utcnow()
        
        # Convert to dict with serializable values
        data = self.config.model_dump()
        # Convert enums to strings
        if "collection_mode" in data:
            data["collection_mode"] = data["collection_mode"].value if hasattr(data["collection_mode"], "value") else str(data["collection_mode"])
        if "approval_mode" in data:
            data["approval_mode"] = data["approval_mode"].value if hasattr(data["approval_mode"], "value") else str(data["approval_mode"])
        # Convert datetimes to ISO strings
        if "created_at" in data and hasattr(data["created_at"], "isoformat"):
            data["created_at"] = data["created_at"].isoformat()
        if "updated_at" in data and hasattr(data["updated_at"], "isoformat"):
            data["updated_at"] = data["updated_at"].isoformat()
        
        with open(config_path, 'w') as f:
            yaml.dump(data, f, default_flow_style=False)
    
    def update_config(self, updates: Dict[str, Any]) -> CollectorConfig:
        """Update Collector configuration"""
        current = self.config.model_dump()
        current.update(updates)
        self.config = CollectorConfig(**current)
        self._save_config()
        return self.config
    
    def get_config(self) -> CollectorConfig:
        """Get current Collector configuration"""
        return self.config
    
    # =========================================================================
    # Immediate First Sweep (Enhancement #1)
    # =========================================================================
    
    async def run_first_sweep(self) -> Dict[str, Any]:
        """
        Run immediately after notebook setup to show instant value.
        Uses cached/fast sources only - no slow API calls.
        """
        logger.info(f"Running first sweep for notebook {self.notebook_id}")
        
        results = {
            "items_found": 0,
            "items_queued": 0,
            "sources_checked": 0,
            "duration_ms": 0
        }
        
        start = datetime.utcnow()
        
        # Quick keyword-based collection from configured sources
        # Combine subject with focus areas for targeted searches
        subject = self.config.subject.strip()
        sweep_keywords = []
        if subject and self.config.focus_areas:
            for area in self.config.focus_areas[:3]:
                area_stripped = area.strip()
                if subject.lower() not in area_stripped.lower():
                    sweep_keywords.append(f"{subject} {area_stripped}")
                else:
                    sweep_keywords.append(area_stripped)
        elif self.config.focus_areas:
            sweep_keywords = self.config.focus_areas[:3]
        elif subject:
            sweep_keywords = [subject]
        
        # Enrich sweep with seed domains from existing notebook sources
        try:
            seed_domains = await self._extract_seed_domains()
            if seed_domains:
                # Add site-scoped searches for proven domains
                for domain_kw in seed_domains.get("seed_domains", [])[:4]:
                    seed_query = f"{subject} {domain_kw}" if subject else domain_kw
                    if seed_query not in sweep_keywords:
                        sweep_keywords.append(seed_query)
                print(f"[COLLECTOR] First sweep enriched with {len(seed_domains.get('seed_domains', []))} seed domains")
        except Exception:
            pass

        if sweep_keywords:
            items = await self._quick_collect(sweep_keywords)
            results["items_found"] = len(items)
            
            # Process and queue items
            for item in items:
                processed = await self._process_item(item)
                if not processed.is_duplicate:
                    await self._add_to_approval_queue(processed)
                    results["items_queued"] += 1
        
        results["duration_ms"] = (datetime.utcnow() - start).total_seconds() * 1000
        results["sources_checked"] = len(self.config.sources.get("rss_feeds", [])) + len(self.config.sources.get("web_pages", []))
        
        return results
    
    async def _quick_collect(self, keywords: List[str]) -> List[CollectedItem]:
        """Quick collection using fast sources only"""
        items = []
        
        # For now, this is a placeholder - will integrate with actual sources
        # In production, this would check RSS feeds, cached news, etc.
        
        return items
    
    # =========================================================================
    # Source Seeding — Use existing notebook sources as discovery signals
    # =========================================================================

    async def _extract_seed_domains(self) -> Dict[str, List[str]]:
        """
        Analyze existing notebook sources to extract proven-valuable domains and channels.
        Returns additional source config entries derived from what the user already curated.
        """
        from storage.source_store import source_store
        from urllib.parse import urlparse

        sources = await source_store.list(self.notebook_id)
        if not sources:
            return {}

        # Collect all URLs from existing sources
        urls = [s.get("url") for s in sources if s.get("url")]
        if not urls:
            return {}

        # Extract and count domains
        domain_counts: Dict[str, int] = {}
        youtube_channels: List[str] = []
        medium_authors: List[str] = []

        for url in urls:
            try:
                parsed = urlparse(url)
                domain = parsed.netloc.lower().replace("www.", "")

                # YouTube: extract channel/user patterns
                if "youtube.com" in domain:
                    path = parsed.path
                    if "/watch" in path:
                        youtube_channels.append("youtube")  # Can't extract channel from watch URL easily
                    elif "/@" in path or "/c/" in path or "/channel/" in path:
                        channel = path.split("/")[1] if len(path.split("/")) > 1 else ""
                        if channel:
                            youtube_channels.append(channel)
                    continue

                # Medium: extract author/publication
                if "medium.com" in domain:
                    parts = parsed.path.strip("/").split("/")
                    if parts and parts[0].startswith("@"):
                        medium_authors.append(parts[0])
                    elif parts and parts[0] not in ("", "p", "s"):
                        medium_authors.append(parts[0])

                # Skip generic social/platform domains
                skip_domains = {
                    "twitter.com", "x.com", "facebook.com", "linkedin.com",
                    "reddit.com", "github.com", "google.com", "t.co",
                    "bit.ly", "docs.google.com",
                }
                if domain in skip_domains:
                    continue

                domain_counts[domain] = domain_counts.get(domain, 0) + 1
            except Exception:
                continue

        # Build seed sources from domains that appear 2+ times (proven valuable)
        seed_sources: Dict[str, List[str]] = {}

        # Top domains → news keywords (site-scoped searches)
        frequent_domains = sorted(domain_counts.items(), key=lambda x: -x[1])
        site_keywords = []
        for domain, count in frequent_domains[:8]:
            if count >= 2:
                # Use site: scoped search for domains with multiple sources
                site_keywords.append(f"site:{domain}")
            elif count == 1:
                # Single-appearance domains still useful as web pages to monitor
                site_keywords.append(domain)

        if site_keywords:
            seed_sources["seed_domains"] = site_keywords

        # YouTube channels → youtube keywords
        unique_channels = list(set(c for c in youtube_channels if c != "youtube"))
        if unique_channels:
            seed_sources["youtube_channels"] = unique_channels[:5]

        # Medium authors → additional news keywords
        unique_medium = list(set(medium_authors))
        if unique_medium:
            seed_sources["medium_authors"] = unique_medium[:5]

        print(f"[COLLECTOR] Extracted seed domains from {len(urls)} existing sources: "
              f"{len(site_keywords)} domains, {len(unique_channels)} YT channels, {len(unique_medium)} Medium authors")

        return seed_sources

    async def _analyze_coverage_gaps(self) -> List[str]:
        """
        Identify focus areas that are underrepresented in existing sources.
        Returns gap-filling search keywords biased toward underserved topics.
        """
        if not self.config.focus_areas:
            return []

        from storage.source_store import source_store

        sources = await source_store.list(self.notebook_id)
        if not sources:
            return []  # Fresh notebook — no gaps to analyze yet

        subject = self.config.subject.strip()

        # Count how many sources mention each focus area (case-insensitive)
        area_counts: Dict[str, int] = {area: 0 for area in self.config.focus_areas}
        for src in sources:
            text = f"{src.get('filename', '')} {src.get('content', '')[:500]}".lower()
            for area in self.config.focus_areas:
                if area.lower() in text:
                    area_counts[area] += 1

        if not area_counts:
            return []

        avg_count = sum(area_counts.values()) / len(area_counts)
        gap_threshold = max(1, avg_count * 0.4)  # Areas with < 40% of average are gaps

        gap_keywords = []
        for area, count in sorted(area_counts.items(), key=lambda x: x[1]):
            if count < gap_threshold:
                kw = f"{subject} {area}" if subject and subject.lower() not in area.lower() else area
                gap_keywords.append(kw)

        if gap_keywords:
            logger.info(
                f"[COLLECTOR] Coverage gaps detected: {gap_keywords} "
                f"(counts: {area_counts})"
            )
            print(
                f"[COLLECTOR] 🎯 Coverage gaps: {gap_keywords[:5]} — "
                f"will bias collection toward underserved topics"
            )

        return gap_keywords[:5]

    def _enforce_diversity(
        self,
        items: List['CollectedItem'],
        max_per_domain: int = 3,
        max_total: int = 15,
    ) -> List['CollectedItem']:
        """
        Re-rank collected items to maximize diversity across domains and topics.

        Rules applied in order:
        1. Cap items per domain (default 3) — prevents one site from dominating
        2. Prefer items with low knowledge_overlap (genuinely new content)
        3. Prefer items flagged as is_new_topic
        4. Maintain relevance ordering within each tier

        Returns a re-ranked subset of items.
        """
        from urllib.parse import urlparse

        if not items:
            return items

        # Bucket items by domain
        domain_buckets: Dict[str, List[CollectedItem]] = {}
        for item in items:
            domain = "unknown"
            if item.url:
                try:
                    domain = urlparse(item.url).netloc.lower().replace("www.", "")
                except Exception:
                    pass
            domain_buckets.setdefault(domain, []).append(item)

        # Build diversity score for each item:
        #   higher = more valuable for diversity
        #   new_topic bonus + low overlap bonus + domain scarcity bonus
        scored: List[tuple] = []
        domain_selected: Dict[str, int] = {}

        for item in items:
            domain = "unknown"
            if item.url:
                try:
                    domain = urlparse(item.url).netloc.lower().replace("www.", "")
                except Exception:
                    pass

            diversity_score = 0.0

            # Prefer genuinely new topics
            if item.is_new_topic:
                diversity_score += 0.3

            # Prefer low knowledge overlap
            diversity_score += (1.0 - item.knowledge_overlap) * 0.3

            # Prefer domains with fewer items already selected
            selected_from_domain = domain_selected.get(domain, 0)
            if selected_from_domain >= max_per_domain:
                diversity_score -= 1.0  # Heavy penalty — over domain cap
            else:
                diversity_score += 0.2 / (1 + selected_from_domain)

            # Keep relevance as tiebreaker
            diversity_score += item.overall_confidence * 0.2

            scored.append((diversity_score, item, domain))

        # Sort by diversity score descending
        scored.sort(key=lambda x: -x[0])

        # Select items respecting domain cap
        selected: List[CollectedItem] = []
        domain_selected = {}

        for _, item, domain in scored:
            if len(selected) >= max_total:
                break
            if domain_selected.get(domain, 0) >= max_per_domain:
                continue
            selected.append(item)
            domain_selected[domain] = domain_selected.get(domain, 0) + 1

        # Log diversity stats
        domains_used = len(set(domain_selected.keys()))
        new_topics = sum(1 for i in selected if i.is_new_topic)
        logger.info(
            f"[COLLECTOR] Diversity filter: {len(items)} → {len(selected)} items, "
            f"{domains_used} domains, {new_topics} new topics"
        )
        print(
            f"[COLLECTOR] 🌐 Diversity: {len(items)} → {len(selected)} items "
            f"({domains_used} domains, {new_topics} new topics)"
        )

        return selected

    # =========================================================================
    # Curator-Assigned Task Execution (Worker Mode)
    # =========================================================================
    
    async def execute_collection_task(self, task: Dict[str, Any]) -> List['CollectedItem']:
        """
        Execute a collection task assigned by the Curator.
        
        The Collector's config serves as guardrails - we combine:
        - task: What Curator wants us to find (directives, specific queries)
        - config: What this notebook cares about (intent, focus areas, sources)
        
        Args:
            task: Dict from Curator containing:
                - notebook_id: Which notebook this is for
                - intent: The notebook's intent (from config)
                - focus_areas: Topics to focus on
                - sources: Where to look
                - curator_directive: Optional specific instruction from Curator
                - specific_query: Optional specific search query
                - avoid_similar_to: Optional list of content to avoid duplicating
        
        Returns:
            List of CollectedItem ready for Curator's judgment
        """
        from services.content_fetcher import unified_fetcher
        import time as _time
        
        deadline = task.get("_deadline", 0)
        
        print(f"[COLLECTOR] execute_collection_task starting for {self.notebook_id}")
        logger.info(f"Executing Curator-assigned task for notebook {self.notebook_id}")
        
        collected_items: List[CollectedItem] = []
        
        # ── Build search keywords ──
        # Priority: Curator smart queries > coverage gaps > static config fallback
        keywords = []
        subject = self.config.subject.strip()
        
        # 1. Smart queries from Curator (LLM-generated, specific and targeted)
        smart_queries = task.get("smart_queries", [])
        if smart_queries:
            keywords.extend(smart_queries)
            print(f"[COLLECTOR] Using {len(smart_queries)} Curator smart queries as primary keywords")
        
        # 2. Coverage gap keywords (underserved focus areas)
        try:
            gap_keywords = await self._analyze_coverage_gaps()
            if gap_keywords:
                for gk in gap_keywords:
                    if gk not in keywords:
                        keywords.append(gk)
        except Exception as gap_err:
            print(f"[COLLECTOR] Coverage gap analysis failed (non-fatal): {gap_err}")
        
        # 3. Specific query from Curator (e.g. user-triggered "Collect Now" with a topic)
        if task.get("specific_query"):
            keywords.insert(0, task["specific_query"])
        
        # 4. Fallback: static subject + focus areas (only if nothing better available)
        if not keywords:
            if subject and self.config.focus_areas:
                for area in self.config.focus_areas[:5]:
                    area_stripped = area.strip()
                    if subject.lower() not in area_stripped.lower():
                        keywords.append(f"{subject} {area_stripped}")
                    else:
                        keywords.append(area_stripped)
                keywords.append(subject)
            elif self.config.focus_areas:
                keywords.extend(self.config.focus_areas[:5])
            elif subject:
                keywords.append(subject)
        
        # Always include subject as a catch-all if we have one and it's not already there
        if subject and subject not in keywords:
            keywords.append(subject)
        
        # Get sources from task or fall back to config
        sources = task.get("sources", self.config.sources)
        
        # Enrich sources with seed domains from existing notebook content
        try:
            seed_domains = await self._extract_seed_domains()
            if seed_domains:
                # Deep copy to avoid mutating config
                sources = {k: list(v) if isinstance(v, list) else v for k, v in sources.items()}
                
                # Add site-scoped news keywords from proven domains
                existing_news = set(sources.get("news_keywords", []))
                for domain_kw in seed_domains.get("seed_domains", []):
                    # e.g. "site:levelup.gitconnected.com" + subject
                    seed_query = f"{subject} {domain_kw}" if subject else domain_kw
                    if seed_query not in existing_news:
                        sources.setdefault("news_keywords", []).append(seed_query)
                
                # Add YouTube channel keywords
                for channel in seed_domains.get("youtube_channels", []):
                    yt_query = f"{subject} {channel}" if subject else channel
                    if yt_query not in set(sources.get("youtube_keywords", [])):
                        sources.setdefault("youtube_keywords", []).append(yt_query)
                
                # Add Medium author searches
                for author in seed_domains.get("medium_authors", []):
                    medium_query = f"site:medium.com {author} {subject}" if subject else f"site:medium.com {author}"
                    if medium_query not in existing_news:
                        sources.setdefault("news_keywords", []).append(medium_query)
                
                print(f"[COLLECTOR] Enriched sources with seeds: {len(seed_domains.get('seed_domains', []))} domains, "
                      f"{len(seed_domains.get('youtube_channels', []))} YT, {len(seed_domains.get('medium_authors', []))} Medium")
        except Exception as seed_err:
            print(f"[COLLECTOR] Seed domain extraction failed (non-fatal): {seed_err}")
        
        # Use unified fetcher to collect from ALL source types
        # Give fetching at most 60s so processing/judgment still have time
        fetch_timeout = 60
        if deadline:
            fetch_timeout = min(60, max(15, deadline - _time.time() - 60))  # Leave 60s for processing+judgment
        print(f"[COLLECTOR] Fetching from sources: {list(sources.keys())} with {len(keywords)} keywords (timeout: {fetch_timeout:.0f}s)")
        try:
            import asyncio
            fetched_items = await asyncio.wait_for(
                unified_fetcher.fetch_all(sources, keywords),
                timeout=fetch_timeout
            )
            print(f"[COLLECTOR] Unified fetcher returned {len(fetched_items)} items")
            
            # Convert FetchedItem to CollectedItem
            for fetched in fetched_items:
                item = CollectedItem(
                    title=fetched.title,
                    url=fetched.url,
                    content=fetched.content,
                    preview=fetched.summary or fetched.content[:300],
                    source_name=fetched.source_name,
                    source_type=fetched.source_type,
                    collected_at=fetched.published_date or datetime.utcnow(),
                    content_hash=fetched.content_hash
                )
                collected_items.append(item)
                
        except asyncio.TimeoutError:
            # Fetch timed out — continue with whatever items we already collected
            print(f"[COLLECTOR] Fetch timed out after {fetch_timeout:.0f}s — continuing with {len(collected_items)} items already gathered")
        except Exception as e:
            print(f"[COLLECTOR] Unified fetcher error: {type(e).__name__}: {e}")
            logger.error(f"Unified fetcher error: {e}")
            # Fall back to legacy RSS-only collection
            for feed_url in sources.get("rss_feeds", [])[:10]:
                try:
                    items = await self._collect_from_rss(feed_url, keywords)
                    collected_items.extend(items)
                except Exception as e:
                    logger.error(f"RSS collection failed for {feed_url}: {e}")
        
        # Resource list expansion: detect pages that are lists of URLs
        # (e.g., "Top 100 AI RSS Feeds") and fetch the top ~10 individual sites
        # Skip if deadline is tight — this is nice-to-have, not essential
        expanded_items = []
        items_to_remove = []
        skip_expansion = deadline and _time.time() > deadline - 45
        if skip_expansion:
            print(f"[COLLECTOR] Skipping resource list expansion — only {deadline - _time.time():.0f}s left")
        for idx, item in enumerate([] if skip_expansion else collected_items):
            try:
                urls_found = self._detect_resource_list(item)
                if urls_found:
                    print(f"[COLLECTOR] Detected resource list: '{item.title}' — found {len(urls_found)} URLs")
                    items_to_remove.append(idx)  # Always remove the list page itself
                    
                    # Separate RSS feeds from regular web pages
                    rss_urls = []
                    web_urls = []
                    for url in urls_found[:10]:  # Top 10 sites
                        lower = url.lower()
                        if any(hint in lower for hint in ['/rss', '/feed', '/atom', '.xml', 'feeds.']):
                            rss_urls.append(url)
                        else:
                            web_urls.append(url)
                    
                    print(f"[COLLECTOR] List expansion: {len(rss_urls)} RSS feeds, {len(web_urls)} web pages")
                    
                    # Parse RSS feeds to get latest articles from each
                    # Empty keywords = take latest articles from each feed
                    # (the feeds are already topically relevant since they came from a curated list)
                    for feed_url in rss_urls[:8]:
                        try:
                            rss_items = await self._collect_from_rss(feed_url, [])
                            # Take the top 2 articles per feed
                            for rss_item in rss_items[:2]:
                                expanded_items.append(rss_item)
                            if rss_items:
                                print(f"[COLLECTOR]   RSS {feed_url[:60]} → {len(rss_items[:2])} articles")
                        except Exception as rss_err:
                            print(f"[COLLECTOR]   RSS {feed_url[:60]} failed: {rss_err}")
                    
                    # Scrape web pages directly
                    if web_urls:
                        from services.web_scraper import web_scraper
                        scraped = await web_scraper.scrape_urls(web_urls[:8])
                        for result in scraped:
                            if result.get("success") and result.get("text") and len(result["text"]) > 200:
                                expanded_item = CollectedItem(
                                    title=result.get("title", result.get("url", "Untitled")),
                                    url=result.get("url"),
                                    content=result["text"],
                                    preview=result["text"][:300],
                                    source_name=f"via {item.title[:40]}",
                                    source_type="web",
                                    collected_at=datetime.utcnow(),
                                )
                                expanded_items.append(expanded_item)
                    
                    print(f"[COLLECTOR] Expanded resource list into {len(expanded_items)} individual sources")
            except Exception as rl_err:
                print(f"[COLLECTOR] Resource list expansion error for '{item.title}': {rl_err}")
                logger.warning(f"Resource list expansion failed for '{item.title}': {rl_err}")
        
        # Always remove list pages (even if expansion yielded nothing — the raw list isn't useful)
        if items_to_remove:
            collected_items = [item for idx, item in enumerate(collected_items) if idx not in items_to_remove]
            collected_items.extend(expanded_items)
        
        # Process all items (scoring, duplicate detection)
        # Run in PARALLEL with semaphore to limit concurrent LLM calls
        import asyncio
        print(f"[COLLECTOR] Processing {len(collected_items)} raw items...")
        
        process_semaphore = asyncio.Semaphore(4)
        avoid_similar = task.get("avoid_similar_to", [])
        
        async def _process_bounded(item):
            """Process a single item with bounded concurrency."""
            # If deadline is very tight, skip LLM scoring and use heuristic
            if deadline and _time.time() > deadline - 20:
                return None  # Will be picked up in next auto-collection
            async with process_semaphore:
                try:
                    processed = await self._process_item(item)
                    if processed.is_duplicate:
                        return None
                    # Skip items similar to what Curator said to avoid
                    for avoid_content in avoid_similar:
                        if self._content_similarity(processed.content, avoid_content) > 0.8:
                            return None
                    return processed
                except Exception as proc_err:
                    logger.debug(f"Processing failed for '{item.title}' (non-fatal): {proc_err}")
                    return None
        
        process_results = await asyncio.gather(*[_process_bounded(item) for item in collected_items])
        processed_items = [r for r in process_results if r is not None]
        print(f"[COLLECTOR] {len(processed_items)} items passed processing (from {len(collected_items)} raw)")
        
        # Contextualize items — Temporal Intelligence (Enhancement #6)
        # Adds delta insights: what's new vs what user already knows
        # Skip entirely if deadline is tight — this is enrichment, not essential
        if deadline and _time.time() > deadline - 25:
            print(f"[COLLECTOR] Skipping contextualization — only {deadline - _time.time():.0f}s left for judgment")
        else:
            ctx_semaphore = asyncio.Semaphore(4)
            
            async def _contextualize_bounded(item):
                async with ctx_semaphore:
                    try:
                        await self.contextualize_item(item)
                    except Exception as ctx_err:
                        logger.debug(f"Contextualization failed for '{item.title}' (non-fatal): {ctx_err}")
            
            await asyncio.gather(*[_contextualize_bounded(item) for item in processed_items])
        
        # Enforce diversity — cap per-domain, prefer new topics and low-overlap items
        diverse_items = self._enforce_diversity(
            processed_items,
            max_per_domain=3,
            max_total=self.config.schedule.get("max_items_per_run", 15),
        )
        
        logger.info(f"Task execution complete: {len(diverse_items)} diverse items from {len(processed_items)} processed")
        return diverse_items
    
    def _detect_resource_list(self, item: CollectedItem) -> Optional[List[str]]:
        """Detect if content is a list/directory of URLs rather than actual content.
        
        A resource list page has:
        - Many URLs (>5 unique domains)
        - Short text between URLs (list-like, not article-like)
        - Title often contains "list", "top", "best", "resources", "directory"
        
        Returns list of extracted URLs if it's a resource list, None otherwise.
        """
        import re
        
        content = item.content or ""
        title_lower = (item.title or "").lower()
        
        # Quick check: does the title suggest a list page?
        list_indicators = ['top ', 'best ', 'list of', 'resources', 'directory', 'curated', 
                          'awesome ', 'collection of', 'comprehensive list', 'ultimate list',
                          'rss feed', 'feeds', 'sources for']
        title_is_list = any(indicator in title_lower for indicator in list_indicators)
        
        # Extract all URLs from content
        url_pattern = re.compile(
            r'https?://[^\s<>"\'\)\]\,]+',
            re.IGNORECASE
        )
        urls = url_pattern.findall(content)
        
        # Deduplicate and filter
        seen_domains = set()
        unique_urls = []
        for url in urls:
            # Clean trailing punctuation
            url = url.rstrip('.,;:)]}')
            try:
                from urllib.parse import urlparse
                parsed = urlparse(url)
                domain = parsed.netloc.lower()
                # Skip common non-content domains
                if domain in ('github.com', 'twitter.com', 'x.com', 't.co', 'bit.ly'):
                    continue
                if domain and domain not in seen_domains:
                    seen_domains.add(domain)
                    unique_urls.append(url)
            except Exception:
                continue
        
        # Decision: is this a resource list?
        # Need both high URL density AND list-like title or structure
        words_in_content = len(content.split())
        url_density = len(unique_urls) / max(words_in_content, 1) * 100  # URLs per 100 words
        
        is_resource_list = False
        
        if len(unique_urls) >= 5 and title_is_list:
            is_resource_list = True
        elif len(unique_urls) >= 8 and url_density > 1.5:
            # Many URLs even without list-like title
            is_resource_list = True
        elif len(unique_urls) >= 10:
            # Very many unique domains — almost certainly a list
            is_resource_list = True
        
        if is_resource_list:
            # Filter to keep only URLs that look like content sites (not images, stylesheets, etc.)
            content_urls = []
            for url in unique_urls:
                lower_url = url.lower()
                if any(ext in lower_url for ext in ['.png', '.jpg', '.gif', '.css', '.js', '.svg', '.ico']):
                    continue
                content_urls.append(url)
            
            if len(content_urls) >= 3:
                return content_urls[:15]  # Cap at 15 to avoid overwhelming
        
        return None
    
    def _content_similarity(self, content1: str, content2: str) -> float:
        """Quick content similarity check using word overlap"""
        words1 = set(content1.lower().split()[:50])
        words2 = set(content2.lower().split()[:50])
        if not words1 or not words2:
            return 0.0
        intersection = len(words1 & words2)
        union = len(words1 | words2)
        return intersection / union if union > 0 else 0.0
    
    async def _collect_from_rss(
        self, 
        feed_url: str, 
        search_terms: List[str]
    ) -> List[CollectedItem]:
        """Collect items from an RSS feed"""
        import feedparser
        
        items = []
        start_time = datetime.utcnow()
        
        try:
            feed = feedparser.parse(feed_url)
            
            for entry in feed.entries[:20]:  # Limit entries per feed
                # Check if entry matches any search term
                title = entry.get("title", "")
                summary = entry.get("summary", entry.get("description", ""))
                content = f"{title} {summary}".lower()
                
                # Filter by search terms if provided
                if search_terms:
                    if not any(term.lower() in content for term in search_terms):
                        continue
                
                item = CollectedItem(
                    title=title,
                    url=entry.get("link"),
                    content=summary,
                    preview=summary[:300] if summary else title,
                    source_name=feed.feed.get("title", feed_url),
                    source_type="rss",
                    collected_at=datetime.utcnow()
                )
                items.append(item)
            
            # Update source health
            response_time = (datetime.utcnow() - start_time).total_seconds() * 1000
            self.update_source_health(
                source_id=feed_url,
                source_url=feed_url,
                success=True,
                response_time_ms=response_time,
                items_found=len(items)
            )
            
        except Exception as e:
            logger.error(f"RSS feed error for {feed_url}: {e}")
            self.update_source_health(
                source_id=feed_url,
                source_url=feed_url,
                success=False
            )
        
        return items
    
    async def _collect_from_webpage(
        self, 
        page_url: str, 
        search_terms: List[str]
    ) -> List[CollectedItem]:
        """Collect items from a webpage (placeholder for web scraping)"""
        # This would integrate with your existing web scraping infrastructure
        # For now, return empty - actual implementation depends on your scraping setup
        return []
    
    # =========================================================================
    # Content Processing & Confidence Scoring
    # =========================================================================
    
    async def _process_item(self, item: CollectedItem) -> CollectedItem:
        """Process a collected item: score, check duplicates, etc."""
        
        # URL-based duplicate detection (survives restarts via _init_dedup_state)
        if item.url and item.url in self._known_urls:
            item.is_duplicate = True
            logger.debug(f"URL duplicate: {item.url}")
            return item
        
        # Generate content hash for duplicate detection
        item.content_hash = self._generate_content_hash(item.content)
        
        # Check for duplicates (Enhancement #4)
        if item.content_hash in self._content_hashes:
            item.is_duplicate = True
            return item
        
        # Check semantic similarity for near-duplicates
        duplicate = await self._find_semantic_duplicate(item)
        if duplicate:
            item.is_duplicate = True
            item.duplicate_of = duplicate
            return item
        
        # Calculate confidence scores (Enhancement #8)
        item = await self._calculate_confidence(item)
        
        # Add to tracking sets
        self._content_hashes.add(item.content_hash)
        if item.url:
            self._known_urls.add(item.url)
        
        return item
    
    def _generate_content_hash(self, content: str) -> str:
        """Generate hash for exact duplicate detection"""
        normalized = content.lower().strip()
        return hashlib.sha256(normalized.encode()).hexdigest()[:16]
    
    async def _find_semantic_duplicate(self, item: CollectedItem, threshold: float = 0.92) -> Optional[str]:
        """Find near-duplicate via embedding similarity"""
        try:
            # Search existing Collector memories for this notebook
            results = memory_store.search_archival_memory(
                query=item.title + " " + item.content[:500],
                namespace=AgentNamespace.COLLECTOR,
                notebook_id=self.notebook_id,
                limit=5
            )
            
            for r in results:
                if r.similarity_score >= threshold:
                    return r.entry.id
        except Exception as e:
            logger.debug(f"Semantic duplicate check failed (non-fatal): {e}")
        
        return None
    
    async def _calculate_confidence(self, item: CollectedItem) -> CollectedItem:
        """Calculate confidence scores with explanations, incorporating learned preferences"""
        from agents.curator import curator
        
        reasons = []
        learned_bonus = 0.0
        
        # Get learned preferences from Curator (user's past behavior)
        try:
            preferences = await curator.get_learned_preferences(self.notebook_id)
            
            # Boost if matches preferred topics
            item_text = f"{item.title} {item.content[:500]}".lower()
            for topic in preferences.get("preferred_topics", [])[:5]:
                if topic.lower() in item_text:
                    learned_bonus += 0.1
                    reasons.append(f"Matches preferred topic: {topic}")
                    break  # One bonus per item
            
            # Boost if from preferred source
            if item.source_name in preferences.get("preferred_sources", []):
                learned_bonus += 0.1
                reasons.append(f"From preferred source: {item.source_name}")
            
            # Penalize if matches rejected patterns
            if item.url:
                for rejected in preferences.get("rejected_patterns", []):
                    if rejected and rejected in item.url:
                        learned_bonus -= 0.2
                        reasons.append("Similar to previously rejected content")
                        break
                        
        except Exception as e:
            logger.debug(f"Could not get learned preferences: {e}")
        
        # Relevance score - how well does it match intent?
        relevance = await self._score_relevance(item)
        item.relevance_score = relevance["score"]
        if relevance["reason"]:
            reasons.append(relevance["reason"])
        
        # Source trust - is this a reliable source?
        health = self._source_health.get(item.source_name)
        if health:
            if health.health == SourceHealth.HEALTHY:
                item.source_trust = 0.9
                reasons.append(f"Trusted source ({health.items_collected} items collected)")
            elif health.health == SourceHealth.DEGRADED:
                item.source_trust = 0.6
                reasons.append("Source has been slow recently")
            else:
                item.source_trust = 0.3
                reasons.append("Source reliability issues")
        else:
            item.source_trust = 0.5
            reasons.append("New source (no history)")
        
        # Freshness score - how recent is this?
        max_age_days = self.config.filters.get("max_age_days", 30) if self.config.filters else 30
        
        # Try to get actual published date if collected_at defaults to "now"
        age_hours = (datetime.utcnow() - item.collected_at).total_seconds() / 3600
        
        # If age_hours < 1 (i.e. defaulted to utcnow), try extracting date from content
        if age_hours < 1 and item.content:
            try:
                from services.content_date_extractor import extract_content_date
                extracted = extract_content_date(item.title, item.content[:2000])
                if extracted:
                    from datetime import date as date_type
                    parsed_date = datetime.fromisoformat(extracted)
                    age_hours = (datetime.utcnow() - parsed_date).total_seconds() / 3600
            except Exception:
                pass
        
        max_age_hours = max_age_days * 24
        
        if age_hours < 24:
            item.freshness_score = 1.0
            reasons.append("Published today")
        elif age_hours < 72:
            item.freshness_score = 0.8
            reasons.append("Published this week")
        elif age_hours < 168:
            item.freshness_score = 0.6
            reasons.append("Published within 7 days")
        elif age_hours < max_age_hours:
            item.freshness_score = max(0.3, 1 - (age_hours / max_age_hours))
        else:
            # HARD GATE: Content older than max_age_days is stale
            item.freshness_score = 0.0
            reasons.append(f"Stale content (>{max_age_days} days old)")
        
        # Overall confidence - weighted combination + learned preference bonus
        base_confidence = (
            item.relevance_score * 0.5 +
            item.source_trust * 0.3 +
            item.freshness_score * 0.2
        )
        
        # Hard cap: if freshness is 0 (stale), cap confidence to prevent passing threshold
        if item.freshness_score == 0.0:
            base_confidence = min(base_confidence, 0.35)
            reasons.append("Confidence capped — content too old")
        
        item.overall_confidence = max(0.0, min(1.0, base_confidence + learned_bonus))
        
        item.confidence_reasons = reasons
        return item
    
    async def _score_relevance(self, item: CollectedItem) -> Dict[str, Any]:
        """Score how relevant an item is to the notebook intent"""
        if not self.config.intent and not self.config.focus_areas:
            return {"score": 0.5, "reason": "No intent configured"}
        
        # Use LLM for relevance scoring
        try:
            focus = ", ".join(self.config.focus_areas) if self.config.focus_areas else self.config.intent
            
            # Include notebook.md behavioral guidance if present
            behavioral_context = self._load_notebook_md()
            guidance_section = ""
            if behavioral_context:
                guidance_section = f"\n\nAdditional guidance from the user:\n{behavioral_context[:1000]}\n"
            
            prompt = f"""Rate the relevance of this content to the research focus.

Research focus: {focus}{guidance_section}

Content title: {item.title}
Content preview: {item.content[:500]}

Respond with JSON only:
{{"score": 0.0-1.0, "reason": "brief explanation"}}"""

            response = await ollama_client.generate(
                prompt=prompt,
                model=settings.ollama_fast_model,
                temperature=0.2
            )
            
            text = response.get("response", "")
            json_start = text.find("{")
            json_end = text.rfind("}") + 1
            if json_start >= 0 and json_end > json_start:
                result = json.loads(text[json_start:json_end])
                return {
                    "score": float(result.get("score", 0.5)),
                    "reason": result.get("reason", "")
                }
        except Exception as e:
            logger.error(f"Relevance scoring failed: {e}")
        
        return {"score": 0.5, "reason": "Could not score relevance"}
    
    # =========================================================================
    # Source Health Monitoring (Enhancement #5)
    # =========================================================================
    
    def update_source_health(
        self,
        source_id: str,
        source_url: str,
        success: bool,
        response_time_ms: float = 0,
        items_found: int = 0
    ) -> SourceHealthRecord:
        """Update health tracking for a source"""
        if source_id not in self._source_health:
            self._source_health[source_id] = SourceHealthRecord(
                source_id=source_id,
                source_url=source_url
            )
        
        health = self._source_health[source_id]
        
        if success:
            health.last_success = datetime.utcnow()
            health.failure_count = 0
            health.items_collected += items_found
            
            # Update average response time
            if health.avg_response_time_ms > 0:
                health.avg_response_time_ms = (health.avg_response_time_ms + response_time_ms) / 2
            else:
                health.avg_response_time_ms = response_time_ms
            
            # Determine health status
            if response_time_ms > 5000:
                health.health = SourceHealth.DEGRADED
            else:
                health.health = SourceHealth.HEALTHY
        else:
            health.last_failure = datetime.utcnow()
            health.failure_count += 1
            
            # Escalate health status based on failure count
            if health.failure_count >= 5:
                health.health = SourceHealth.DEAD
            elif health.failure_count >= 3:
                health.health = SourceHealth.FAILING
            else:
                health.health = SourceHealth.DEGRADED
        
        return health
    
    def get_source_health_report(self) -> List[Dict[str, Any]]:
        """Get health report for all sources"""
        return [
            {
                "source_id": h.source_id,
                "url": h.source_url,
                "health": h.health.value,
                "failure_count": h.failure_count,
                "items_collected": h.items_collected,
                "avg_response_ms": h.avg_response_time_ms
            }
            for h in self._source_health.values()
        ]
    
    # =========================================================================
    # Approval Queue Management (Enhancement #10)
    # =========================================================================
    
    async def _add_to_approval_queue(self, item: CollectedItem) -> bool:
        """Add item to approval queue based on approval mode.
        
        Returns:
            True if item was queued for user review, False if auto-approved
        """
        # Dedup: skip if URL already in queue or known sources
        if item.url:
            if item.url in self._known_urls:
                logger.info(f"Skipping queue add (URL known): {item.url}")
                return False
            for q in self._approval_queue:
                if q.item.url == item.url:
                    logger.info(f"Skipping queue add (already queued): {item.url}")
                    return False

        if self.config.approval_mode == ApprovalMode.TRUST_ME:
            # Auto-approve
            item.status = "approved"
            await self._store_approved_item(item)
            return False
        
        if self.config.approval_mode == ApprovalMode.MIXED:
            # Auto-approve high confidence
            if item.overall_confidence >= 0.85:
                item.status = "approved"
                await self._store_approved_item(item)
                return False
        
        # Queue for approval
        queue_item = ApprovalQueueItem(
            item=item,
            expires_at=datetime.utcnow() + timedelta(days=self.APPROVAL_EXPIRY_DAYS)
        )
        self._approval_queue.append(queue_item)
        if item.url:
            self._known_urls.add(item.url)
        self._save_approval_queue()
        return True
    
    async def _store_approved_item(self, item: CollectedItem) -> bool:
        """Store an approved item as a notebook source AND in Collector memory.
        
        Returns True if the item was actually stored, False if skipped (dedup, shallow, error).
        """
        from storage.source_store import source_store
        from services.rag_engine import rag_engine
        
        # Final dedup guard: check if this URL already exists in stored sources
        if item.url:
            existing = await source_store.list(self.notebook_id)
            for src in existing:
                if src.get("url") == item.url:
                    logger.info(f"Skipping duplicate store (URL exists): {item.url}")
                    self._known_urls.add(item.url)
                    return False
        
        # Enrich thin content by scraping full article (RSS feeds only have summaries)
        # Minimum content threshold — anything below this is a headline, not a source
        MIN_CONTENT_CHARS = 500
        
        content = item.content
        if item.url and len(content) < 1000:
            try:
                # SEC filings need special handling — SEC.gov requires specific User-Agent
                if item.source_type == "sec":
                    content = await self._deep_fetch_sec_filing(item, content)
                else:
                    from services.web_scraper import web_scraper
                    scraped = await web_scraper._scrape_single(item.url)
                    if scraped.get("success") and scraped.get("text"):
                        full_text = scraped["text"]
                        if len(full_text) > len(content):
                            logger.info(f"Enriched '{item.title}': {len(content)} -> {len(full_text)} chars")
                            content = full_text
                            item.content = full_text
                            if scraped.get("title") and len(scraped["title"]) > len(item.title):
                                item.title = scraped["title"]
            except Exception as enrich_err:
                logger.debug(f"Content enrichment failed (using original): {enrich_err}")
        
        # Gate: reject sources that are still too shallow after enrichment
        if len(content) < MIN_CONTENT_CHARS:
            logger.warning(
                f"[COLLECTOR] Rejecting shallow source '{item.title}' "
                f"({len(content)} chars < {MIN_CONTENT_CHARS} minimum). "
                f"Type: {item.source_type}, URL: {item.url}"
            )
            print(
                f"[COLLECTOR] ⚠ Skipped shallow source: '{item.title}' "
                f"({len(content)} chars) — headline only, no substantive content"
            )
            return False
        
        # 1. Create actual notebook source so it shows up in the UI
        source_data = {
            "id": item.id,
            "notebook_id": self.notebook_id,
            "type": item.source_type or "web",
            "format": item.source_type or "web",
            "url": item.url,
            "title": item.title,
            "filename": item.title,
            "content": content,
            "summary": item.preview or content[:300],
            "word_count": len(content.split()),
            "char_count": len(content),
            "status": "processing",
            "collected_by": "collector",
            "confidence_score": item.overall_confidence,
            "confidence_reasons": item.confidence_reasons,
            "created_at": datetime.utcnow().isoformat()
        }
        
        try:
            await source_store.create(
                notebook_id=self.notebook_id,
                filename=item.title,
                metadata=source_data
            )
            
            # 2. Index in RAG for searchability
            rag_result = await rag_engine.ingest_document(
                notebook_id=self.notebook_id,
                source_id=item.id,
                text=item.content,
                filename=item.title,
                source_type=item.source_type or "web"
            )
            
            chunks = rag_result.get("chunks", 0) if rag_result else 0
            await source_store.update(self.notebook_id, item.id, {
                "chunks": chunks,
                "status": "completed"
            })
            
            logger.info(f"Approved item stored as source: {item.title} ({chunks} chunks)")
            
            # Notify Constellation/frontend that a new source was added
            try:
                from api.constellation_ws import notify_source_updated
                await notify_source_updated({
                    "notebook_id": self.notebook_id,
                    "source_id": item.id,
                    "status": "completed",
                    "title": item.title[:200],
                    "chunks": chunks
                })
            except Exception as ws_err:
                logger.debug(f"WebSocket notification failed (non-fatal): {ws_err}")
            
        except Exception as e:
            logger.error(f"Failed to store approved item as source: {e}")
            return False
        
        # 3. Auto-tag the source using LLM (non-fatal, background-quality)
        try:
            from services.auto_tagger import auto_tagger
            tags = await auto_tagger.generate_tags(
                title=item.title,
                content=item.content[:3000],
                notebook_subject=self.config.subject,
                focus_areas=self.config.focus_areas,
            )
            if tags:
                from storage.source_store import source_store as _ss
                await _ss.set_tags(self.notebook_id, item.id, tags)
                logger.info(f"Auto-tagged source '{item.title[:50]}' with: {tags}")
        except Exception as tag_err:
            logger.debug(f"Auto-tagging failed (non-fatal): {tag_err}")
        
        # 4. Also store in Collector memory for pattern tracking (non-fatal)
        try:
            entry = ArchivalMemoryEntry(
                content=f"{item.title}\n\n{item.content}",
                content_type="collected_item",
                source_type=MemorySourceType.WEB if item.url else MemorySourceType.MANUAL,
                source_id=item.url or item.id,
                source_notebook_id=self.notebook_id,
                topics=self.config.focus_areas[:5],
                importance=MemoryImportance.MEDIUM if item.overall_confidence >= 0.7 else MemoryImportance.LOW,
            )
            
            memory_store.add_archival_memory(
                entry,
                namespace=AgentNamespace.COLLECTOR,
                notebook_id=self.notebook_id
            )
        except Exception as mem_err:
            logger.warning(f"Failed to store item in archival memory (non-fatal): {mem_err}")
        
        # 4. Record approval signal for learning
        memory_store.record_user_signal(
            notebook_id=self.notebook_id,
            signal_type="item_approved",
            item_id=item.id,
            metadata={
                "title": item.title[:200],
                "source_name": item.source_name,
                "confidence": item.overall_confidence,
                "source_type": item.source_type
            }
        )
        
        return True
    
    async def _deep_fetch_sec_filing(self, item: 'CollectedItem', fallback_content: str) -> str:
        """
        Attempt to fetch full SEC filing content from the filing URL.
        
        SEC.gov requires a specific User-Agent header format and blocks generic scrapers.
        Tries multiple strategies:
        1. trafilatura with proper SEC headers
        2. Direct HTTP fetch + HTML text extraction
        
        Returns the best content found, or the original fallback_content if all strategies fail.
        """
        import aiohttp
        
        if not item.url:
            return fallback_content
        
        best_content = fallback_content
        sec_headers = {
            "User-Agent": "LocalBook Research Assistant research@localbook.app",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        }
        
        # Strategy 1: trafilatura with proper headers (handles most HTML filings)
        try:
            import trafilatura
            import asyncio
            loop = asyncio.get_event_loop()
            
            # trafilatura.fetch_url doesn't accept custom headers easily,
            # so we download first, then extract
            async with aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=30),
                headers=sec_headers
            ) as session:
                async with session.get(item.url) as response:
                    if response.status == 200:
                        html = await response.text()
                        
                        # Extract with trafilatura (run in thread pool — blocking)
                        def _extract(h):
                            return trafilatura.extract(
                                h,
                                include_comments=False,
                                include_tables=True,
                                no_fallback=False,
                            )
                        
                        text = await loop.run_in_executor(None, _extract, html)
                        if text and len(text) > len(best_content):
                            logger.info(
                                f"SEC deep fetch (trafilatura): '{item.title}' "
                                f"{len(best_content)} -> {len(text)} chars"
                            )
                            best_content = text
                            item.content = text
        except Exception as e:
            logger.debug(f"SEC deep fetch strategy 1 failed: {e}")
        
        # Strategy 2: If trafilatura didn't yield much, try raw text extraction
        # (SEC filings are often plain-ish HTML with lots of text in <p>, <span>, <td>)
        if len(best_content) < 500:
            try:
                import re
                async with aiohttp.ClientSession(
                    timeout=aiohttp.ClientTimeout(total=30),
                    headers=sec_headers
                ) as session:
                    async with session.get(item.url) as response:
                        if response.status == 200:
                            html = await response.text()
                            # Strip scripts, styles, then extract all text
                            html = re.sub(r'<script[^>]*>.*?</script>', '', html, flags=re.DOTALL | re.IGNORECASE)
                            html = re.sub(r'<style[^>]*>.*?</style>', '', html, flags=re.DOTALL | re.IGNORECASE)
                            html = re.sub(r'<[^>]+>', ' ', html)
                            text = re.sub(r'\s+', ' ', html).strip()
                            # Trim to reasonable size for a filing
                            text = text[:50000]
                            if len(text) > len(best_content):
                                logger.info(
                                    f"SEC deep fetch (raw extract): '{item.title}' "
                                    f"{len(best_content)} -> {len(text)} chars"
                                )
                                best_content = text
                                item.content = text
            except Exception as e:
                logger.debug(f"SEC deep fetch strategy 2 failed: {e}")
        
        return best_content
    
    def get_pending_approvals(self) -> List[Dict[str, Any]]:
        """Get items pending approval"""
        now = datetime.utcnow()
        
        # Filter out expired items
        valid = [q for q in self._approval_queue if q.expires_at > now]
        if len(valid) != len(self._approval_queue):
            self._approval_queue = valid
            self._save_approval_queue()
        
        return [
            {
                "item_id": q.item.id,
                "title": q.item.title,
                "preview": q.item.preview or q.item.content[:200],
                "source": q.item.source_name,
                "confidence": q.item.overall_confidence,
                "confidence_reasons": q.item.confidence_reasons,
                "queued_at": q.queued_at.isoformat(),
                "expires_at": q.expires_at.isoformat(),
                "days_until_expiry": (q.expires_at - now).days,
                # Temporal Intelligence (Enhancement #6)
                "delta_summary": q.item.delta_summary,
                "is_new_topic": q.item.is_new_topic,
                "temporal_context": q.item.temporal_context,
                "knowledge_overlap": q.item.knowledge_overlap,
                "related_titles": q.item.related_titles,
            }
            for q in valid
        ]
    
    def get_expiring_soon(self, days: int = 3) -> List[Dict[str, Any]]:
        """Get items expiring within N days"""
        cutoff = datetime.utcnow() + timedelta(days=days)
        return [
            a for a in self.get_pending_approvals()
            if datetime.fromisoformat(a["expires_at"]) <= cutoff
        ]
    
    async def approve_item(self, item_id: str, curator_approved: bool = False) -> bool:
        """Approve a queued item"""
        for i, q in enumerate(self._approval_queue):
            if q.item.id == item_id:
                q.item.status = "approved"
                await self._store_approved_item(q.item)
                self._approval_queue.pop(i)
                self._save_approval_queue()
                return True
        return False
    
    async def approve_batch(self, item_ids: List[str]) -> int:
        """Approve multiple items (batch operation)"""
        approved = 0
        for item_id in item_ids:
            if await self.approve_item(item_id):
                approved += 1
        return approved
    
    async def approve_all_from_source(self, source_name: str) -> int:
        """Approve all items from a specific source"""
        item_ids = [
            q.item.id for q in self._approval_queue
            if q.item.source_name == source_name
        ]
        return await self.approve_batch(item_ids)
    
    # =========================================================================
    # Rejection Feedback (Enhancement #12)
    # =========================================================================
    
    async def reject_item(
        self,
        item_id: str,
        reason: str,
        feedback_type: Optional[str] = None
    ) -> bool:
        """
        Reject an item with feedback for learning.
        
        feedback_type: wrong_topic, too_old, bad_source, already_knew, other
        """
        for i, q in enumerate(self._approval_queue):
            if q.item.id == item_id:
                q.item.status = "rejected"
                q.item.rejection_reason = reason
                
                # Record signal for learning
                memory_store.record_user_signal(
                    notebook_id=self.notebook_id,
                    signal_type="reject",
                    item_id=item_id,
                    metadata={
                        "reason": reason,
                        "feedback_type": feedback_type,
                        "source": q.item.source_name,
                        "confidence": q.item.overall_confidence
                    }
                )
                
                # Learn from rejection
                await self._learn_from_rejection(q.item, reason, feedback_type)
                
                self._approval_queue.pop(i)
                self._save_approval_queue()
                return True
        return False
    
    async def _learn_from_rejection(
        self,
        item: CollectedItem,
        reason: str,
        feedback_type: Optional[str]
    ) -> None:
        """Adapt Collector behavior based on rejection"""
        if feedback_type == "wrong_topic":
            # Add to excluded topics (placeholder - would extract topics from item)
            pass
        elif feedback_type == "bad_source":
            # Reduce trust for this source
            if item.source_name in self._source_health:
                self._source_health[item.source_name].health = SourceHealth.DEGRADED
        elif feedback_type == "too_old":
            # Tighten freshness filter
            current_max = self.config.filters.get("max_age_days", 30)
            if current_max > 7:
                self.config.filters["max_age_days"] = current_max - 7
                self._save_config()
    
    # =========================================================================
    # Priority Adaptation (from negative signals)
    # =========================================================================
    
    async def reduce_priority_for_patterns(self, patterns: List[Dict]) -> None:
        """Reduce collection priority for ignored patterns"""
        # Extract topics/keywords from ignored items
        # Add to excluded_topics or reduce weight
        for pattern in patterns:
            topics = pattern.get("topics", [])
            for topic in topics:
                if topic not in self.config.excluded_topics:
                    self.config.excluded_topics.append(topic)
        
        if patterns:
            self._save_config()
    
    async def expand_focus_areas(self, search_misses: List[str]) -> None:
        """Expand focus based on search misses (user wanted X, we didn't have it)"""
        # Add search miss queries as focus areas
        for query in search_misses[:5]:
            if query not in self.config.focus_areas:
                self.config.focus_areas.append(query)
        
        if search_misses:
            self._save_config()
    
    # =========================================================================
    # Temporal Intelligence (Enhancement #6)
    # =========================================================================
    
    async def contextualize_item(self, item: CollectedItem) -> Dict[str, Any]:
        """
        Connect new item to existing knowledge.
        Highlight what's NEW vs continuation of known story.
        Also applies delta fields directly onto the CollectedItem.
        """
        # Find related existing content in this notebook's memory
        try:
            related = memory_store.search_archival_memory(
                query=item.title + " " + item.content[:500],
                namespace=AgentNamespace.COLLECTOR,
                notebook_id=self.notebook_id,
                limit=10
            )
        except Exception as e:
            logger.debug(f"Archival search for contextualization failed (non-fatal): {e}")
            related = []
        
        # Filter to meaningfully related items (similarity > 0.3)
        related = [r for r in related if r.similarity_score > 0.3]
        
        if not related:
            # Entirely new topic — no existing knowledge
            item.is_new_topic = True
            item.knowledge_overlap = 0.0
            item.delta_summary = "New topic — not covered in existing research"
            return {
                "is_new_topic": True,
                "related_items": [],
                "delta_summary": item.delta_summary,
                "temporal_context": None,
                "knowledge_overlap": 0.0
            }
        
        # Compute knowledge_overlap from similarity scores
        max_similarity = max(r.similarity_score for r in related)
        avg_similarity = sum(r.similarity_score for r in related[:5]) / min(len(related), 5)
        knowledge_overlap = round((max_similarity * 0.6 + avg_similarity * 0.4), 2)
        
        # Extract related titles for UI display
        related_titles = []
        for r in related[:3]:
            # Extract title (first line or first 80 chars of content)
            content_text = r.entry.content if hasattr(r.entry, 'content') else str(r.entry)
            first_line = content_text.split('\n')[0][:80]
            related_titles.append(first_line)
        
        # Use LLM to identify what's specifically NEW
        related_content = "\n".join([
            f"- {r.entry.content[:200]}" for r in related[:5]
            if hasattr(r.entry, 'content')
        ])
        
        try:
            prompt = f"""Compare this NEW item against what the user already knows.

NEW ITEM:
Title: {item.title}
Content: {item.content[:800]}

EXISTING KNOWLEDGE (user already has these):
{related_content}

Respond with JSON only:
{{
    "is_new_topic": false,
    "delta_summary": "What's specifically NEW in this item that isn't in existing knowledge (one sentence)",
    "temporal_context": "How this relates chronologically to existing items (one sentence, or null if unknown)",
    "knowledge_overlap": {knowledge_overlap}
}}"""

            response = await ollama_client.generate(
                prompt=prompt,
                system="You are a research analyst identifying what's new. Respond only with valid JSON.",
                model=settings.ollama_fast_model,
                temperature=0.2
            )
            
            text = response.get("response", "")
            json_start = text.find("{")
            json_end = text.rfind("}") + 1
            if json_start >= 0 and json_end > json_start:
                result = json.loads(text[json_start:json_end])
                
                # Apply to item
                item.is_new_topic = result.get("is_new_topic", False)
                item.delta_summary = result.get("delta_summary")
                item.temporal_context = result.get("temporal_context")
                item.knowledge_overlap = float(result.get("knowledge_overlap", knowledge_overlap))
                item.related_titles = related_titles
                
                return {
                    "is_new_topic": item.is_new_topic,
                    "related_items": [r.entry.id for r in related[:3]],
                    "delta_summary": item.delta_summary,
                    "temporal_context": item.temporal_context,
                    "knowledge_overlap": item.knowledge_overlap,
                    "connects_to": related_titles
                }
        except Exception as e:
            logger.error(f"Contextualization LLM call failed: {e}")
        
        # Fallback: we know it's related but can't compute delta via LLM
        item.is_new_topic = False
        item.knowledge_overlap = knowledge_overlap
        item.related_titles = related_titles
        return {
            "is_new_topic": False,
            "related_items": [r.entry.id for r in related[:3]],
            "delta_summary": None,
            "temporal_context": None,
            "knowledge_overlap": knowledge_overlap
        }


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
