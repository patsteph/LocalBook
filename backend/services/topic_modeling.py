"""BERTopic-based Topic Modeling Service for LocalBook v0.6.5

Provides automatic topic discovery from document chunks with:
- Incremental topic updates via partial_fit()
- Two-stage naming: instant c-TF-IDF + background LLM enhancement
- Integration with existing embedding model (snowflake-arctic-embed2)
- WebSocket notifications for real-time UI updates
"""
import asyncio
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any
from dataclasses import dataclass, field
from uuid import uuid4

import httpx
import numpy as np

from config import settings


@dataclass
class Topic:
    """Represents a discovered topic/theme."""
    id: str = field(default_factory=lambda: str(uuid4()))
    topic_id: int = -1  # BERTopic's internal ID
    name: str = ""  # c-TF-IDF generated name
    enhanced_name: Optional[str] = None  # LLM-enhanced name
    keywords: List[Tuple[str, float]] = field(default_factory=list)  # (word, weight) pairs
    document_count: int = 0
    representative_docs: List[str] = field(default_factory=list)
    notebook_ids: List[str] = field(default_factory=list)
    source_ids: List[str] = field(default_factory=list)
    created_at: datetime = field(default_factory=datetime.utcnow)
    updated_at: datetime = field(default_factory=datetime.utcnow)
    
    @property
    def display_name(self) -> str:
        """Return the best available name for display."""
        return self.enhanced_name or self.name or f"Topic {self.topic_id}"
    
    def to_dict(self) -> Dict:
        return {
            "id": self.id,
            "topic_id": self.topic_id,
            "name": self.display_name,
            "raw_name": self.name,
            "enhanced_name": self.enhanced_name,
            "keywords": [{"word": w, "weight": s} for w, s in self.keywords[:10]],
            "document_count": self.document_count,
            "representative_docs": self.representative_docs[:3],
            "notebook_ids": self.notebook_ids,
            "source_ids": self.source_ids,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }


@dataclass 
class TopicDocument:
    """Maps a document chunk to its topic."""
    doc_id: str
    text: str
    topic_id: int
    probability: float
    source_id: str
    notebook_id: str


class TopicModelingService:
    """BERTopic-based topic modeling with incremental updates and two-stage naming."""
    
    # Rebuild thresholds
    REBUILD_SOURCE_THRESHOLD = 5      # Rebuild after 5 new sources since last rebuild
    REBUILD_DOC_RATIO_THRESHOLD = 0.3 # Or when 30% more documents than at last rebuild
    MIN_DOCS_FOR_REBUILD = 15         # Don't rebuild if total docs < 15
    
    def __init__(self):
        self._model = None
        self._initialized = False
        self._documents: List[TopicDocument] = []
        self._topics: Dict[int, Topic] = {}  # topic_id -> Topic
        self._enhancement_queue: List[int] = []  # topic_ids needing name enhancement
        self._enhancement_task: Optional[asyncio.Task] = None
        self._lock = asyncio.Lock()
        self._last_load_time: float = 0  # Track when we last loaded from disk
        self._rebuild_doc_counts: Dict[str, int] = {}   # notebook_id -> doc count at last rebuild
        self._rebuild_source_counts: Dict[str, int] = {} # notebook_id -> unique source count at last rebuild
        self._rebuild_in_progress: set = set()           # notebook_ids currently rebuilding
        
        # Paths
        self.data_dir = Path(settings.data_dir) / "topic_model"
        self.model_path = self.data_dir / "bertopic_model"
        self.topics_path = self.data_dir / "topics.json"
        self.docs_path = self.data_dir / "documents.json"
        self.rebuild_state_path = self.data_dir / "rebuild_state.json"
        
    async def initialize(self) -> bool:
        """Initialize or load the BERTopic model."""
        if self._initialized:
            return True
            
        try:
            self.data_dir.mkdir(parents=True, exist_ok=True)
            
            # Import BERTopic components
            from bertopic import BERTopic
            from bertopic.vectorizers import OnlineCountVectorizer
            from umap import UMAP
            from hdbscan import HDBSCAN
            
            # Check if we have a saved model
            if self.model_path.exists():
                print("[TopicModel] Loading existing model...")
                self._model = BERTopic.load(str(self.model_path))
                await self._load_state()
            else:
                print("[TopicModel] Creating new model...")
                # Configure for incremental learning
                umap_model = UMAP(
                    n_neighbors=15,
                    n_components=5,
                    min_dist=0.0,
                    metric='cosine',
                    random_state=42
                )
                
                hdbscan_model = HDBSCAN(
                    min_cluster_size=3,
                    min_samples=2,
                    metric='euclidean',
                    prediction_data=True
                )
                
                # Online vectorizer for incremental updates
                vectorizer_model = OnlineCountVectorizer(
                    stop_words="english",
                    ngram_range=(1, 2)
                )
                
                self._model = BERTopic(
                    umap_model=umap_model,
                    hdbscan_model=hdbscan_model,
                    vectorizer_model=vectorizer_model,
                    calculate_probabilities=True,
                    verbose=False
                )
            
            self._initialized = True
            print(f"[TopicModel] Initialized with {len(self._topics)} topics, {len(self._documents)} documents")
            return True
            
        except Exception as e:
            print(f"[TopicModel] Initialization error: {e}")
            import traceback
            traceback.print_exc()
            return False
    
    async def _load_state(self):
        """Load persisted topics and document mappings."""
        import time
        try:
            # Clear existing in-memory state before loading
            self._topics.clear()
            self._documents.clear()
            
            if self.topics_path.exists():
                with open(self.topics_path, 'r') as f:
                    topics_data = json.load(f)
                    for td in topics_data:
                        topic = Topic(
                            id=td["id"],
                            topic_id=td["topic_id"],
                            name=td.get("raw_name", td.get("name", "")),  # Use raw_name if available
                            enhanced_name=td.get("enhanced_name"),
                            keywords=[(k["word"], k["weight"]) for k in td.get("keywords", [])],
                            document_count=td.get("document_count", 0),
                            representative_docs=td.get("representative_docs", []),
                            notebook_ids=td.get("notebook_ids", []),
                            source_ids=td.get("source_ids", []),
                            created_at=datetime.fromisoformat(td["created_at"]),
                            updated_at=datetime.fromisoformat(td["updated_at"])
                        )
                        self._topics[topic.topic_id] = topic
            
            if self.docs_path.exists():
                with open(self.docs_path, 'r') as f:
                    docs_data = json.load(f)
                    for dd in docs_data:
                        doc = TopicDocument(
                            doc_id=dd["doc_id"],
                            text=dd["text"][:500],  # Store truncated for memory
                            topic_id=dd["topic_id"],
                            probability=dd["probability"],
                            source_id=dd["source_id"],
                            notebook_id=dd["notebook_id"]
                        )
                        self._documents.append(doc)
            
            # Load rebuild state tracking
            if self.rebuild_state_path.exists():
                try:
                    with open(self.rebuild_state_path, 'r') as f:
                        rebuild_data = json.load(f)
                    self._rebuild_doc_counts = rebuild_data.get("doc_counts", {})
                    self._rebuild_source_counts = rebuild_data.get("source_counts", {})
                except Exception:
                    pass
            
            # Update load time AFTER successful load
            self._last_load_time = time.time()
            print(f"[TopicModel] Loaded {len(self._topics)} topics, {len(self._documents)} documents from disk")
                        
        except Exception as e:
            print(f"[TopicModel] Error loading state: {e}")
    
    async def _save_state(self):
        """Persist topics and document mappings."""
        import time
        try:
            # Save topics
            topics_data = [t.to_dict() for t in self._topics.values()]
            with open(self.topics_path, 'w') as f:
                json.dump(topics_data, f, indent=2)
            
            # Update load time so we know our in-memory state matches disk
            self._last_load_time = time.time()
            
            # Save document mappings (truncated text)
            docs_data = [
                {
                    "doc_id": d.doc_id,
                    "text": d.text[:500],
                    "topic_id": d.topic_id,
                    "probability": d.probability,
                    "source_id": d.source_id,
                    "notebook_id": d.notebook_id
                }
                for d in self._documents
            ]
            with open(self.docs_path, 'w') as f:
                json.dump(docs_data, f, indent=2)
            
            # Save rebuild state tracking
            try:
                rebuild_data = {
                    "doc_counts": self._rebuild_doc_counts,
                    "source_counts": self._rebuild_source_counts,
                }
                with open(self.rebuild_state_path, 'w') as f:
                    json.dump(rebuild_data, f, indent=2)
            except Exception:
                pass
            
            # Save BERTopic model
            if self._model is not None and hasattr(self._model, 'save'):
                self._model.save(str(self.model_path), serialization="safetensors", save_ctfidf=True)
                
        except Exception as e:
            print(f"[TopicModel] Error saving state: {e}")
    
    def should_rebuild(self, notebook_id: str) -> bool:
        """Check if a notebook has enough new data to justify a topic rebuild.
        
        Returns True if:
        - At least REBUILD_SOURCE_THRESHOLD new sources since last rebuild, OR
        - Document count grew by REBUILD_DOC_RATIO_THRESHOLD (30%) since last rebuild
        - AND total docs >= MIN_DOCS_FOR_REBUILD
        - AND no rebuild is currently in progress for this notebook
        """
        if notebook_id in self._rebuild_in_progress:
            return False
        
        # Count current docs and unique sources for this notebook
        nb_docs = [d for d in self._documents if d.notebook_id == notebook_id]
        current_doc_count = len(nb_docs)
        current_source_count = len(set(d.source_id for d in nb_docs))
        
        if current_doc_count < self.MIN_DOCS_FOR_REBUILD:
            return False
        
        last_doc_count = self._rebuild_doc_counts.get(notebook_id, 0)
        last_source_count = self._rebuild_source_counts.get(notebook_id, 0)
        
        # Never rebuilt — should rebuild
        if last_doc_count == 0 and current_doc_count >= self.MIN_DOCS_FOR_REBUILD:
            return True
        
        # Check source threshold
        new_sources = current_source_count - last_source_count
        if new_sources >= self.REBUILD_SOURCE_THRESHOLD:
            print(f"[TopicModel] Rebuild recommended for {notebook_id}: {new_sources} new sources since last rebuild")
            return True
        
        # Check document ratio threshold
        if last_doc_count > 0:
            growth_ratio = (current_doc_count - last_doc_count) / last_doc_count
            if growth_ratio >= self.REBUILD_DOC_RATIO_THRESHOLD:
                print(f"[TopicModel] Rebuild recommended for {notebook_id}: {growth_ratio:.0%} doc growth since last rebuild")
                return True
        
        return False
    
    def record_rebuild(self, notebook_id: str):
        """Record current counts after a successful rebuild."""
        nb_docs = [d for d in self._documents if d.notebook_id == notebook_id]
        self._rebuild_doc_counts[notebook_id] = len(nb_docs)
        self._rebuild_source_counts[notebook_id] = len(set(d.source_id for d in nb_docs))
        self._rebuild_in_progress.discard(notebook_id)
        print(f"[TopicModel] Recorded rebuild state for {notebook_id}: "
              f"{self._rebuild_doc_counts[notebook_id]} docs, "
              f"{self._rebuild_source_counts[notebook_id]} sources")
    
    def mark_rebuild_started(self, notebook_id: str):
        """Mark that a rebuild is in progress for a notebook."""
        self._rebuild_in_progress.add(notebook_id)
    
    async def add_documents(
        self,
        texts: List[str],
        source_id: str,
        notebook_id: str,
        embeddings: Optional[np.ndarray] = None
    ) -> Dict[str, Any]:
        """Add new documents and update topics incrementally.
        
        Args:
            texts: List of document chunks to add
            source_id: ID of the source these chunks belong to
            notebook_id: ID of the notebook
            embeddings: Pre-computed embeddings (optional, will compute if not provided)
            
        Returns:
            Dict with topics found and documents processed
        """
        if not self._initialized:
            await self.initialize()
        
        if not texts:
            return {"topics": [], "documents": 0}
        
        async with self._lock:
            try:
                # Generate doc IDs
                doc_ids = [f"{source_id}_{i}" for i in range(len(texts))]
                
                # Check if model has been fitted
                is_first_fit = not hasattr(self._model, 'topics_') or self._model.topics_ is None
                
                if is_first_fit:
                    # First time - need at least min_cluster_size documents
                    if len(texts) < 3:
                        print(f"[TopicModel] Not enough documents for initial fit ({len(texts)} < 3)")
                        # Store documents for later
                        for i, text in enumerate(texts):
                            self._documents.append(TopicDocument(
                                doc_id=doc_ids[i],
                                text=text[:500],
                                topic_id=-1,
                                probability=0.0,
                                source_id=source_id,
                                notebook_id=notebook_id
                            ))
                        return {"topics": [], "documents": len(texts), "status": "queued"}
                    
                    print(f"[TopicModel] Initial fit with {len(texts)} documents")
                    topics, probs = self._model.fit_transform(texts, embeddings=embeddings)
                else:
                    # Incremental update
                    print(f"[TopicModel] Incremental update with {len(texts)} documents")
                    topics, probs = self._model.transform(texts, embeddings=embeddings)
                
                # Process results
                new_topic_ids = set()
                for i, (topic_id, prob) in enumerate(zip(topics, probs if probs is not None else [0.5] * len(topics))):
                    # Handle probability array
                    if isinstance(prob, np.ndarray):
                        prob = float(prob.max())
                    
                    doc = TopicDocument(
                        doc_id=doc_ids[i],
                        text=texts[i][:500],
                        topic_id=int(topic_id),
                        probability=float(prob),
                        source_id=source_id,
                        notebook_id=notebook_id
                    )
                    self._documents.append(doc)
                    
                    if topic_id != -1:
                        new_topic_ids.add(int(topic_id))
                
                # Update topic metadata
                await self._update_topics_metadata(new_topic_ids, source_id, notebook_id)
                
                # Queue topics for name enhancement
                for topic_id in new_topic_ids:
                    if topic_id not in self._enhancement_queue:
                        self._enhancement_queue.append(topic_id)
                
                # Start background enhancement if not running
                self._start_enhancement_task()
                
                # Save state
                await self._save_state()
                
                # Notify frontend
                await self._notify_topics_updated()
                
                return {
                    "topics": list(new_topic_ids),
                    "documents": len(texts),
                    "status": "processed"
                }
                
            except Exception as e:
                print(f"[TopicModel] Error adding documents: {e}")
                import traceback
                traceback.print_exc()
                return {"topics": [], "documents": 0, "error": str(e)}
    
    async def _update_topics_metadata(self, topic_ids: set, source_id: str, notebook_id: str):
        """Update topic metadata with new source/notebook info."""
        if not self._model or not hasattr(self._model, 'get_topic_info'):
            return
            
        topic_info = self._model.get_topic_info()
        
        for topic_id in topic_ids:
            if topic_id == -1:
                continue
                
            # Get or create topic
            if topic_id not in self._topics:
                self._topics[topic_id] = Topic(topic_id=topic_id)
            
            topic = self._topics[topic_id]
            
            # Update from BERTopic
            topic_row = topic_info[topic_info['Topic'] == topic_id]
            if not topic_row.empty:
                # Get c-TF-IDF name
                name = topic_row['Name'].values[0] if 'Name' in topic_row.columns else ""
                if name and not topic.name:
                    # Clean up the name (remove topic ID prefix like "0_")
                    topic.name = re.sub(r'^\d+_', '', str(name)).replace('_', ' ').strip()
                
                topic.document_count = int(topic_row['Count'].values[0]) if 'Count' in topic_row.columns else 0
            
            # Get keywords
            keywords = self._model.get_topic(topic_id)
            if keywords:
                topic.keywords = keywords[:10]
            
            # Get representative docs
            try:
                rep_docs = self._model.get_representative_docs(topic_id)
                if rep_docs:
                    topic.representative_docs = [d[:200] for d in rep_docs[:3]]
            except:
                pass
            
            # Update source/notebook tracking
            if source_id not in topic.source_ids:
                topic.source_ids.append(source_id)
            if notebook_id not in topic.notebook_ids:
                topic.notebook_ids.append(notebook_id)
            
            topic.updated_at = datetime.utcnow()
    
    def _start_enhancement_task(self):
        """Start background task to enhance topic names with LLM."""
        import threading
        
        def run_enhancement_sync():
            """Run enhancement in a new event loop (for bundled app compatibility)."""
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                loop.run_until_complete(self._enhance_names_background())
            finally:
                loop.close()
        
        # Use threading for reliable background execution in bundled app
        thread = threading.Thread(target=run_enhancement_sync, daemon=True)
        thread.start()
    
    async def _enhance_names_background(self):
        """Background task: enhance topic names using Ollama."""
        # Wait a bit for more topics to accumulate
        await asyncio.sleep(5)
        
        total_to_enhance = len(self._enhancement_queue)
        enhanced_count = 0
        
        # Notify frontend that enhancement is starting
        await self._notify_enhancement_progress(enhanced_count, total_to_enhance, "starting")
        
        while self._enhancement_queue:
            topic_id = self._enhancement_queue.pop(0)
            
            if topic_id not in self._topics:
                continue
                
            topic = self._topics[topic_id]
            
            # Skip if already enhanced
            if topic.enhanced_name:
                enhanced_count += 1
                continue
            
            try:
                enhanced = await self._generate_enhanced_name(topic)
                if enhanced:
                    topic.enhanced_name = enhanced
                    topic.updated_at = datetime.utcnow()
                    enhanced_count += 1
                    print(f"[TopicModel] Enhanced topic {topic_id}: '{topic.name}' → '{enhanced}'")
                    
                    # Save state but don't notify on every enhancement (causes flickering)
                    await self._save_state()
                    # Only send progress update, not full topics_updated (reduces flickering)
                    await self._notify_enhancement_progress(enhanced_count, total_to_enhance, "enhancing")
                    
            except Exception as e:
                print(f"[TopicModel] Enhancement error for topic {topic_id}: {e}")
                enhanced_count += 1  # Count as done even if failed
            
            # Small delay between enhancements
            await asyncio.sleep(1)
        
        # Notify frontend that enhancement is complete - NOW trigger full refresh
        await self._notify_topics_updated()
        await self._notify_enhancement_progress(enhanced_count, total_to_enhance, "complete")
    
    async def _generate_enhanced_name(self, topic: Topic) -> Optional[str]:
        """Use Ollama to generate a better topic name."""
        if not topic.keywords:
            return None
        
        keywords_str = ", ".join([w for w, _ in topic.keywords[:7]])
        rep_docs_str = "\n".join([f"- {d}" for d in topic.representative_docs[:2]])
        
        prompt = f"""What theme connects these concepts? Give a 2-4 word name.

Keywords: {keywords_str}

Sample text:
{rep_docs_str}

Rules:
- Title case (e.g., "Machine Learning Applications")
- Be specific, not generic
- 2-4 words only
- No punctuation

Theme name:"""

        try:
            timeout = httpx.Timeout(15.0, read=30.0)
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.post(
                    f"{settings.ollama_base_url}/api/generate",
                    json={
                        "model": settings.ollama_fast_model,
                        "prompt": prompt,
                        "stream": False,
                        "options": {
                            "temperature": 0.3,
                            "num_predict": 20,
                        }
                    }
                )
                
                if response.status_code == 200:
                    result = response.json()
                    name = result.get("response", "").strip()
                    # Clean up
                    name = name.strip('"\'').strip()
                    name = re.sub(r'^(topic|theme|subject):\s*', '', name, flags=re.IGNORECASE)
                    if name and len(name) > 3 and len(name) < 50:
                        return name
                        
        except Exception as e:
            print(f"[TopicModel] LLM enhancement failed: {e}")
        
        return None
    
    async def _notify_topics_updated(self):
        """Send WebSocket notification that topics have been updated."""
        try:
            from api.constellation_ws import notify_topics_updated
            await notify_topics_updated({
                "topic_count": len([t for t in self._topics.values() if t.topic_id != -1])
            })
        except Exception as e:
            print(f"[TopicModel] Notification error: {e}")
    
    async def _notify_enhancement_progress(self, current: int, total: int, status: str):
        """Send WebSocket notification about enhancement progress."""
        try:
            from api.constellation_ws import notify_concept_added
            await notify_concept_added({
                "type": "enhancement_progress",
                "current": current,
                "total": total,
                "status": status  # "starting", "enhancing", "complete"
            })
        except Exception as e:
            print(f"[TopicModel] Enhancement notification error: {e}")
    
    # =========================================================================
    # Query Methods
    # =========================================================================
    
    async def get_topics(self, notebook_id: Optional[str] = None) -> List[Topic]:
        """Get all topics, optionally filtered by notebook."""
        if not self._initialized:
            await self.initialize()
        
        # THREADING FIX: Check if file was modified after our last load
        # This handles the case where rebuild happened in a background thread
        if self.topics_path.exists():
            file_mtime = self.topics_path.stat().st_mtime
            if file_mtime > self._last_load_time:
                print(f"[TopicModel] Topics file modified (file={file_mtime}, last_load={self._last_load_time}) - reloading")
                await self._load_state()
        
        # Cross-validate: ensure topic notebook_ids match actual document data
        # This fixes contaminated data from the old topic ID collision bug
        if notebook_id:
            nb_doc_topic_ids = set(d.topic_id for d in self._documents if d.notebook_id == notebook_id)
            topics = []
            for t in self._topics.values():
                if notebook_id in t.notebook_ids:
                    # Verify this topic actually has documents from this notebook
                    if t.topic_id in nb_doc_topic_ids:
                        topics.append(t)
                    # else: stale notebook_id reference, skip it
        else:
            topics = list(self._topics.values())
        
        # Filter out outlier topic (-1) and sort by document count
        topics = [t for t in topics if t.topic_id != -1]
        topics.sort(key=lambda t: t.document_count, reverse=True)
        
        return topics
    
    async def get_topic(self, topic_id: int) -> Optional[Topic]:
        """Get a specific topic by ID."""
        return self._topics.get(topic_id)
    
    async def get_topics_for_source(self, source_id: str) -> List[Topic]:
        """Get topics associated with a specific source."""
        topic_ids = set()
        for doc in self._documents:
            if doc.source_id == source_id and doc.topic_id != -1:
                topic_ids.add(doc.topic_id)
        
        return [self._topics[tid] for tid in topic_ids if tid in self._topics]
    
    async def get_document_topics(self, source_id: str) -> List[Dict]:
        """Get topic assignments for all documents from a source."""
        results = []
        for doc in self._documents:
            if doc.source_id == source_id:
                topic = self._topics.get(doc.topic_id)
                results.append({
                    "doc_id": doc.doc_id,
                    "topic_id": doc.topic_id,
                    "topic_name": topic.display_name if topic else "Uncategorized",
                    "probability": doc.probability
                })
        return results
    
    async def find_topics(self, query: str) -> List[Tuple[int, float]]:
        """Find topics matching a query string."""
        if not self._model or not hasattr(self._model, 'find_topics'):
            return []
        
        try:
            topics, scores = self._model.find_topics(query, top_n=5)
            return list(zip(topics, scores))
        except:
            return []
    
    async def get_stats(self, notebook_id: Optional[str] = None) -> Dict:
        """Get topic modeling statistics."""
        topics = await self.get_topics(notebook_id)
        
        docs = self._documents
        if notebook_id:
            docs = [d for d in docs if d.notebook_id == notebook_id]
        
        return {
            "total_topics": len(topics),
            "total_documents": len(docs),
            "topics_with_enhanced_names": len([t for t in topics if t.enhanced_name]),
            "average_docs_per_topic": len(docs) / max(len(topics), 1),
        }
    
    # =========================================================================
    # Batch Fitting Methods
    # =========================================================================
    
    async def fit_all(
        self,
        texts: List[str],
        embeddings: np.ndarray,
        metadata: List[Dict],
        notebook_id: str
    ) -> Dict:
        """Fit BERTopic on all documents at once (for rebuild).
        
        This is the proper way to use BERTopic - fit on all documents together
        so it can discover topics across the entire corpus.
        """
        if not self._initialized:
            await self.initialize()
        
        if not texts or len(texts) < 5:
            return {"error": "Not enough documents", "topics_found": 0}
        
        async with self._lock:
            try:
                print(f"[TopicModel] fit_all: {len(texts)} documents")
                
                # Clear existing data for this notebook before rebuild
                self._documents = [d for d in self._documents if d.notebook_id != notebook_id]
                
                # Remove this notebook from ALL topics (not just exclusive ones)
                # This prevents topic ID collisions: BERTopic always generates IDs 0,1,2...
                # which would collide with other notebooks' topics if we only cleared exclusive ones.
                for tid in list(self._topics.keys()):
                    topic = self._topics[tid]
                    if notebook_id in topic.notebook_ids:
                        topic.notebook_ids.remove(notebook_id)
                    # Recalculate doc count from remaining documents
                    topic.document_count = len([d for d in self._documents if d.topic_id == tid])
                    topic.source_ids = list(set(
                        d.source_id for d in self._documents if d.topic_id == tid
                    ))
                # Delete topics with no notebooks left
                empty_topics = [tid for tid, t in self._topics.items() if not t.notebook_ids]
                for tid in empty_topics:
                    del self._topics[tid]
                
                # Compute topic ID offset to prevent collisions with other notebooks' topics
                # BERTopic always generates sequential IDs (0, 1, 2, ...) so we offset them
                existing_max_id = max(self._topics.keys(), default=-1)
                topic_id_offset = existing_max_id + 1 if self._topics else 0
                
                # Create fresh BERTopic model for this fit
                from bertopic import BERTopic
                from bertopic.representation import MaximalMarginalRelevance
                from umap import UMAP
                from hdbscan import HDBSCAN
                from sklearn.feature_extraction.text import CountVectorizer
                
                # Configure UMAP - use precomputed embeddings
                umap_model = UMAP(
                    n_neighbors=15,
                    n_components=5,
                    min_dist=0.0,
                    metric='cosine',
                    random_state=42
                )
                
                # Configure HDBSCAN for clustering
                # Balance: not too few themes (was 4), not too many (was 83)
                # Target: 35-45 meaningful themes for rich exploration
                hdbscan_model = HDBSCAN(
                    min_cluster_size=5,   # Smaller clusters for more themes (target 35-45)
                    min_samples=2,        # Moderate strictness
                    metric='euclidean',
                    cluster_selection_method='eom',  # Default method, less granular than 'leaf'
                    prediction_data=True
                )
                
                # Configure CountVectorizer to remove stopwords and use n-grams
                # Extended stopwords to filter out conversational filler words
                from sklearn.feature_extraction.text import ENGLISH_STOP_WORDS
                custom_stopwords = list(ENGLISH_STOP_WORDS) + [
                    # Conversational fillers
                    'just', 'like', 'dont', 'thats', 'theres', 'youre', 'theyre', 'weve',
                    'gonna', 'gotta', 'wanna', 'kinda', 'sorta', 'really', 'actually',
                    'basically', 'literally', 'probably', 'maybe', 'right', 'okay', 'ok',
                    'yeah', 'yes', 'hey', 'well', 'thing', 'things', 'stuff', 'way',
                    'lot', 'lots', 'bit', 'kind', 'sort', 'type', 'types',
                    # Time fillers
                    'time', 'times', 'today', 'now', 'year', 'years', 'day', 'days',
                    # Generic verbs/actions
                    'make', 'makes', 'making', 'made', 'get', 'gets', 'getting', 'got',
                    'go', 'goes', 'going', 'went', 'come', 'comes', 'coming', 'came',
                    'see', 'sees', 'seeing', 'saw', 'look', 'looks', 'looking', 'looked',
                    'know', 'knows', 'knowing', 'knew', 'think', 'thinks', 'thinking',
                    'want', 'wants', 'wanting', 'wanted', 'need', 'needs', 'needing',
                    'use', 'uses', 'using', 'used', 'try', 'tries', 'trying', 'tried',
                    'say', 'says', 'saying', 'said', 'tell', 'tells', 'telling', 'told',
                    # Generic nouns
                    'people', 'person', 'example', 'examples', 'point', 'points',
                    'question', 'questions', 'answer', 'answers', 'idea', 'ideas',
                    'part', 'parts', 'place', 'places', 'case', 'cases', 'fact', 'facts',
                    # Web/content noise
                    'click', 'read', 'article', 'post', 'video', 'image', 'link',
                    'page', 'site', 'website', 'content', 'information', 'details',
                ]
                vectorizer_model = CountVectorizer(
                    stop_words=custom_stopwords,
                    min_df=2,  # Word must appear in at least 2 docs
                    ngram_range=(1, 2)  # Bigrams for phrases
                )
                
                # Use MaximalMarginalRelevance for diverse, non-redundant keywords
                mmr = MaximalMarginalRelevance(diversity=0.5)
                
                # Try to use Ollama for better topic labels via BERTopic's native OpenAI integration
                representation_model = mmr  # Default to MMR only
                try:
                    import openai
                    from bertopic.representation import OpenAI as BERTopicOpenAI
                    
                    # Configure OpenAI client to point to local Ollama
                    client = openai.OpenAI(
                        base_url=f"{settings.ollama_base_url}/v1",
                        api_key="ollama"  # Required but unused
                    )
                    
                    # Custom prompt for concise, meaningful topic labels
                    label_prompt = """I have a topic that contains the following documents:
[DOCUMENTS]

The topic is described by the following keywords: [KEYWORDS]

Based on the information above, create a short, descriptive label (2-4 words) for this topic.
The label should be specific and meaningful, not generic.
Use title case (e.g., "Machine Learning Applications").
Return ONLY the label, nothing else."""

                    ollama_model = BERTopicOpenAI(
                        client,
                        model=settings.ollama_model,
                        prompt=label_prompt,
                        nr_docs=3,
                        doc_length=150,
                        chat=True
                    )
                    
                    # Chain MMR (for keywords) then Ollama (for labels)
                    representation_model = [mmr, ollama_model]
                    print(f"[TopicModel] Using Ollama ({settings.ollama_model}) for topic labeling")
                except Exception as e:
                    print(f"[TopicModel] Ollama integration failed, using MMR only: {e}")
                    representation_model = mmr
                
                # Create model with improved representation
                self._model = BERTopic(
                    umap_model=umap_model,
                    hdbscan_model=hdbscan_model,
                    vectorizer_model=vectorizer_model,
                    representation_model=representation_model,
                    calculate_probabilities=True,
                    verbose=True
                )
                
                print(f"[TopicModel] Running fit_transform with {len(texts)} docs, embeddings shape: {embeddings.shape}")
                
                # FIT on all documents with pre-computed embeddings
                topics, probs = self._model.fit_transform(texts, embeddings=embeddings)
                
                print(f"[TopicModel] fit_transform complete, found {len(set(topics)) - (1 if -1 in topics else 0)} topics")
                
                # Build BERTopic-ID → unique-ID mapping to prevent cross-notebook collisions
                bertopic_ids = set(int(t) for t in topics if t != -1)
                bt_to_unique = {}
                next_id = topic_id_offset
                for bt_id in sorted(bertopic_ids):
                    bt_to_unique[bt_id] = next_id
                    next_id += 1
                
                print(f"[TopicModel] Topic ID mapping: offset={topic_id_offset}, {len(bt_to_unique)} topics")
                
                # Process results with remapped IDs
                topic_ids_found = set()
                for i, (topic_id, prob) in enumerate(zip(topics, probs if probs is not None else [0.5] * len(topics))):
                    if isinstance(prob, np.ndarray):
                        prob = float(prob.max())
                    
                    source_id = metadata[i]["source_id"]
                    remapped_id = bt_to_unique.get(int(topic_id), -1) if topic_id != -1 else -1
                    
                    doc = TopicDocument(
                        doc_id=f"{source_id}_{i}",
                        text=texts[i][:500],
                        topic_id=remapped_id,
                        probability=float(prob),
                        source_id=source_id,
                        notebook_id=notebook_id
                    )
                    self._documents.append(doc)
                    
                    if remapped_id != -1:
                        topic_ids_found.add(remapped_id)
                
                # Create topic objects directly with remapped IDs
                # (Don't use _update_topics_metadata which would merge with existing topics)
                topic_info = self._model.get_topic_info()
                for bt_id, unique_id in bt_to_unique.items():
                    topic = Topic(topic_id=unique_id)
                    
                    # Get metadata from BERTopic using original ID
                    topic_row = topic_info[topic_info['Topic'] == bt_id]
                    if not topic_row.empty:
                        name = topic_row['Name'].values[0] if 'Name' in topic_row.columns else ""
                        if name:
                            topic.name = re.sub(r'^\d+_', '', str(name)).replace('_', ' ').strip()
                        topic.document_count = int(topic_row['Count'].values[0]) if 'Count' in topic_row.columns else 0
                    
                    keywords = self._model.get_topic(bt_id)
                    if keywords:
                        topic.keywords = keywords[:10]
                    
                    try:
                        rep_docs = self._model.get_representative_docs(bt_id)
                        if rep_docs:
                            topic.representative_docs = [d[:200] for d in rep_docs[:3]]
                    except:
                        pass
                    
                    topic.notebook_ids = [notebook_id]
                    topic.source_ids = list(set(
                        d.source_id for d in self._documents if d.topic_id == unique_id
                    ))
                    
                    self._topics[unique_id] = topic
                
                # Queue topics WITHOUT enhanced names for background enhancement
                topics_needing_enhancement = [
                    tid for tid in topic_ids_found 
                    if tid in self._topics and not self._topics[tid].enhanced_name
                ]
                if topics_needing_enhancement:
                    print(f"[TopicModel] Queuing {len(topics_needing_enhancement)} topics for name enhancement")
                    self._enhancement_queue = topics_needing_enhancement
                    self._start_enhancement_task()
                
                # Record rebuild counts for threshold-based auto-rebuild
                self.record_rebuild(notebook_id)
                
                # Save state (includes rebuild tracking)
                await self._save_state()
                
                # Notify frontend
                await self._notify_topics_updated()
                
                return {
                    "topics_found": len(topic_ids_found),
                    "documents_processed": len(texts),
                    "outliers": sum(1 for t in topics if t == -1)
                }
                
            except Exception as e:
                self._rebuild_in_progress.discard(notebook_id)
                print(f"[TopicModel] fit_all error: {e}")
                import traceback
                traceback.print_exc()
                return {"error": str(e), "topics_found": 0}
    
    # =========================================================================
    # Maintenance Methods
    # =========================================================================
    
    async def clear_notebook(self, notebook_id: str) -> None:
        """Clear all documents and topics for a specific notebook."""
        async with self._lock:
            # Remove documents for this notebook
            self._documents = [d for d in self._documents if d.notebook_id != notebook_id]
            
            # Update topics to remove this notebook
            topics_to_remove = []
            for topic_id, topic in self._topics.items():
                if notebook_id in topic.notebook_ids:
                    topic.notebook_ids.remove(notebook_id)
                    # Recalculate document count
                    topic.document_count = len([d for d in self._documents if d.topic_id == topic_id])
                    if topic.document_count == 0:
                        topics_to_remove.append(topic_id)
            
            # Remove empty topics
            for topic_id in topics_to_remove:
                del self._topics[topic_id]
            
            await self._save_state()
            print(f"[TopicModel] Cleared notebook {notebook_id}")
    
    async def rebuild_topics(self, notebook_id: Optional[str] = None) -> Dict:
        """Rebuild all topics from scratch."""
        async with self._lock:
            try:
                # Get all documents
                docs = self._documents
                if notebook_id:
                    docs = [d for d in docs if d.notebook_id == notebook_id]
                
                if len(docs) < 3:
                    return {"error": "Not enough documents to build topics"}
                
                texts = [d.text for d in docs]
                
                # Reset model
                from bertopic import BERTopic
                from bertopic.vectorizers import OnlineCountVectorizer
                from umap import UMAP
                from hdbscan import HDBSCAN
                
                umap_model = UMAP(n_neighbors=15, n_components=5, min_dist=0.0, metric='cosine')
                hdbscan_model = HDBSCAN(min_cluster_size=3, min_samples=2, metric='euclidean', prediction_data=True)
                vectorizer_model = OnlineCountVectorizer(stop_words="english", ngram_range=(1, 2))
                
                self._model = BERTopic(
                    umap_model=umap_model,
                    hdbscan_model=hdbscan_model,
                    vectorizer_model=vectorizer_model,
                    calculate_probabilities=True,
                    verbose=False
                )
                
                # Fit
                topics, probs = self._model.fit_transform(texts)
                
                # Update document assignments
                self._topics.clear()
                for i, (topic_id, prob) in enumerate(zip(topics, probs if probs is not None else [0.5] * len(topics))):
                    if isinstance(prob, np.ndarray):
                        prob = float(prob.max())
                    docs[i].topic_id = int(topic_id)
                    docs[i].probability = float(prob)
                
                # Rebuild topic metadata
                all_topic_ids = set(int(t) for t in topics if t != -1)
                for doc in docs:
                    await self._update_topics_metadata({doc.topic_id}, doc.source_id, doc.notebook_id)
                
                # Queue all for enhancement
                self._enhancement_queue = list(all_topic_ids)
                self._start_enhancement_task()
                
                await self._save_state()
                await self._notify_topics_updated()
                
                return {
                    "topics": len(all_topic_ids),
                    "documents": len(docs),
                    "status": "rebuilt"
                }
                
            except Exception as e:
                print(f"[TopicModel] Rebuild error: {e}")
                import traceback
                traceback.print_exc()
                return {"error": str(e)}
    
    async def delete_source(self, source_id: str) -> bool:
        """Remove all documents from a source."""
        async with self._lock:
            # Remove documents
            self._documents = [d for d in self._documents if d.source_id != source_id]
            
            # Update topic source lists
            for topic in self._topics.values():
                if source_id in topic.source_ids:
                    topic.source_ids.remove(source_id)
            
            await self._save_state()
            return True
    
    async def reset(self) -> bool:
        """Reset all topic data."""
        async with self._lock:
            self._model = None
            self._topics.clear()
            self._documents.clear()
            self._enhancement_queue.clear()
            self._initialized = False
            
            # Delete persisted data
            import shutil
            if self.data_dir.exists():
                shutil.rmtree(self.data_dir)
            
            return True


# Singleton instance
topic_modeling_service = TopicModelingService()
