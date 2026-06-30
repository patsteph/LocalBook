"""CollectorAgentBase — extracted from the former agents/collector.py (Wave 6 split)."""
from ._models import *  # noqa: F401,F403


class CollectorAgentBase:
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
            except RuntimeError as _e:
                logger.debug(f"[collector] {type(_e).__name__}: {_e}")

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
