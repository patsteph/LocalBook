"""
Exploration Store - Track user's learning journey through their notebooks
"""
import json
from datetime import datetime
from typing import List, Dict
from config import settings
from utils.json_io import atomic_write_json
import uuid


class ExplorationStore:
    """Store and retrieve user exploration history"""
    
    def __init__(self):
        self.storage_path = settings.data_dir / "exploration.json"
        self._ensure_storage()
    
    def _ensure_storage(self):
        """Ensure storage file exists"""
        if not self.storage_path.exists():
            self._save_data({"explorations": {}})
    
    def _load_data(self) -> dict:
        """Load exploration data"""
        if not self.storage_path.exists():
            self._ensure_storage()
        with open(self.storage_path, 'r') as f:
            return json.load(f)
    
    def _save_data(self, data: dict):
        """Save exploration data"""
        atomic_write_json(self.storage_path, data)
    
    async def record_query(
        self,
        notebook_id: str,
        query: str,
        topics: List[str],
        sources_used: List[str],
        confidence: float,
        answer_preview: str = ""
    ) -> Dict:
        """Record a user query as part of their exploration journey"""
        data = self._load_data()
        
        if notebook_id not in data["explorations"]:
            data["explorations"][notebook_id] = {
                "queries": [],
                "topics_explored": {},
                "sources_accessed": {},
                "created_at": datetime.now().isoformat()
            }
        
        exploration = data["explorations"][notebook_id]
        
        # Create query record
        query_record = {
            "id": str(uuid.uuid4()),
            "query": query,
            "topics": topics,
            "sources_used": sources_used,
            "confidence": confidence,
            "answer_preview": answer_preview[:200] if answer_preview else "",
            "timestamp": datetime.now().isoformat()
        }
        
        exploration["queries"].append(query_record)
        
        # Update topic counts
        for topic in topics:
            topic_lower = topic.lower()
            if topic_lower not in exploration["topics_explored"]:
                exploration["topics_explored"][topic_lower] = {"count": 0, "first_seen": datetime.now().isoformat()}
            exploration["topics_explored"][topic_lower]["count"] += 1
            exploration["topics_explored"][topic_lower]["last_seen"] = datetime.now().isoformat()
        
        # Update source access counts
        for source_id in sources_used:
            if source_id not in exploration["sources_accessed"]:
                exploration["sources_accessed"][source_id] = {"count": 0, "first_accessed": datetime.now().isoformat()}
            exploration["sources_accessed"][source_id]["count"] += 1
            exploration["sources_accessed"][source_id]["last_accessed"] = datetime.now().isoformat()
        
        # Keep only last 500 queries per notebook
        if len(exploration["queries"]) > 500:
            exploration["queries"] = exploration["queries"][-500:]
        
        self._save_data(data)
        return query_record
    
    async def get_journey(self, notebook_id: str, limit: int = 50) -> Dict:
        """Get the exploration journey for a notebook"""
        data = self._load_data()
        
        if notebook_id not in data["explorations"]:
            return {
                "notebook_id": notebook_id,
                "queries": [],
                "topics_explored": [],
                "sources_accessed": [],
                "total_queries": 0
            }
        
        exploration = data["explorations"][notebook_id]
        
        # Get recent queries
        recent_queries = exploration["queries"][-limit:][::-1]  # Reverse for newest first
        
        # Get top topics
        topics = sorted(
            [{"name": k, **v} for k, v in exploration["topics_explored"].items()],
            key=lambda x: x["count"],
            reverse=True
        )[:20]
        
        # Get most accessed sources
        sources = sorted(
            [{"source_id": k, **v} for k, v in exploration["sources_accessed"].items()],
            key=lambda x: x["count"],
            reverse=True
        )[:10]
        
        return {
            "notebook_id": notebook_id,
            "queries": recent_queries,
            "topics_explored": topics,
            "sources_accessed": sources,
            "total_queries": len(exploration["queries"])
        }
    
    async def get_suggestions(self, notebook_id: str) -> Dict:
        """Get suggestions for continuing exploration based on history"""
        data = self._load_data()
        
        if notebook_id not in data["explorations"]:
            return {"suggestions": [], "unexplored_topics": []}
        
        exploration = data["explorations"][notebook_id]
        
        # Find topics explored only once (potential areas to dive deeper)
        shallow_topics = [
            {"name": k, "count": v["count"]}
            for k, v in exploration["topics_explored"].items()
            if v["count"] == 1
        ][:5]
        
        # Get the most recent query for "continue where you left off"
        recent = exploration["queries"][-1] if exploration["queries"] else None
        
        suggestions = []
        if recent:
            suggestions.append({
                "type": "continue",
                "message": f"Continue exploring: {recent['query'][:50]}...",
                "query": recent["query"]
            })
        
        if shallow_topics:
            suggestions.append({
                "type": "dive_deeper",
                "message": f"Dive deeper into: {shallow_topics[0]['name']}",
                "topic": shallow_topics[0]["name"]
            })
        
        return {
            "suggestions": suggestions,
            "shallow_topics": shallow_topics
        }
    
    async def clear_notebook(self, notebook_id: str):
        """Clear exploration history for a notebook"""
        data = self._load_data()
        if notebook_id in data["explorations"]:
            del data["explorations"][notebook_id]
            self._save_data(data)


# Singleton instance
exploration_store = ExplorationStore()
