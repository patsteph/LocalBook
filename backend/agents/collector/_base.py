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
        # LB-12 D1: one synced document per item (`approval_item/<nb>/<item id>`),
        # so items queued on two Macs both survive. The JSON file is imported once.
        from storage import documents
        prefix = f"{self.notebook_id}/"
        try:
            if not documents.items("approval_item", prefix) and self._get_queue_path().exists() \
                    and not documents.exists("approval_queue_imported", self.notebook_id):
                for entry in json.loads(self._get_queue_path().read_text()):
                    documents.put("approval_item", prefix + str(entry["item"].get("id")), entry)
                documents.put("approval_queue_imported", self.notebook_id, True)
            data = [body for _, body in documents.items("approval_item", prefix)]
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
        """Persist the approval queue (synced documents, one per item)."""
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
            from storage import documents
            documents.put("approval_queue_imported", self.notebook_id, True)
            documents.replace_set("approval_item", f"{self.notebook_id}/",
                                  {f"{self.notebook_id}/{e['item'].get('id')}":
                                   json.loads(json.dumps(e, default=str)) for e in data})
        except Exception as e:
            logger.error(f"Error saving approval queue for {self.notebook_id}: {e}")

    def _load_config(self) -> CollectorConfig:
        """Collector configuration (synced `documents`, LB-12 D1; the YAML file
        is imported once)."""
        from storage import documents

        try:
            data = documents.import_file("collector_config", self.notebook_id,
                                         self._get_config_path(), documents.read_yaml) \
                or documents.get("collector_config", self.notebook_id)
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
        """Save Collector configuration (synced `documents`)."""
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
        
        from storage import documents
        documents.put("collector_config", self.notebook_id, data)

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
