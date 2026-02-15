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
        self._use_sqlite = settings.use_sqlite
        self.storage_path = settings.data_dir / "exploration.json"
        if not self._use_sqlite:
            self._ensure_storage()

    def _get_db(self):
        from storage.database import get_db
        return get_db().get_connection()
    
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
        query_id = str(uuid.uuid4())
        now = datetime.now().isoformat()
        preview = answer_preview[:200] if answer_preview else ""
        
        query_record = {
            "id": query_id,
            "query": query,
            "topics": topics,
            "sources_used": sources_used,
            "confidence": confidence,
            "answer_preview": preview,
            "timestamp": now
        }
        
        if self._use_sqlite:
            conn = self._get_db()
            conn.execute(
                """INSERT INTO exploration_queries
                   (id, notebook_id, query, topics, sources_used, confidence, answer_preview, timestamp)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (query_id, notebook_id, query, json.dumps(topics),
                 json.dumps(sources_used), confidence, preview, now)
            )
            # Update topic counts
            for topic in topics:
                topic_lower = topic.lower()
                conn.execute(
                    """INSERT INTO exploration_topics (notebook_id, topic, count, first_seen, last_seen)
                       VALUES (?, ?, 1, ?, ?)
                       ON CONFLICT(notebook_id, topic) DO UPDATE SET
                       count = count + 1, last_seen = ?""",
                    (notebook_id, topic_lower, now, now, now)
                )
            # Update source access counts
            for source_id in sources_used:
                conn.execute(
                    """INSERT INTO exploration_sources (notebook_id, source_id, count, first_accessed, last_accessed)
                       VALUES (?, ?, 1, ?, ?)
                       ON CONFLICT(notebook_id, source_id) DO UPDATE SET
                       count = count + 1, last_accessed = ?""",
                    (notebook_id, source_id, now, now, now)
                )
            # Keep only last 500 queries per notebook
            conn.execute(
                """DELETE FROM exploration_queries WHERE id IN (
                   SELECT id FROM exploration_queries WHERE notebook_id = ?
                   ORDER BY timestamp DESC LIMIT -1 OFFSET 500)""",
                (notebook_id,)
            )
            conn.commit()
            return query_record
        
        data = self._load_data()
        if notebook_id not in data["explorations"]:
            data["explorations"][notebook_id] = {
                "queries": [],
                "topics_explored": {},
                "sources_accessed": {},
                "created_at": now
            }
        
        exploration = data["explorations"][notebook_id]
        exploration["queries"].append(query_record)
        
        for topic in topics:
            topic_lower = topic.lower()
            if topic_lower not in exploration["topics_explored"]:
                exploration["topics_explored"][topic_lower] = {"count": 0, "first_seen": now}
            exploration["topics_explored"][topic_lower]["count"] += 1
            exploration["topics_explored"][topic_lower]["last_seen"] = now
        
        for source_id in sources_used:
            if source_id not in exploration["sources_accessed"]:
                exploration["sources_accessed"][source_id] = {"count": 0, "first_accessed": now}
            exploration["sources_accessed"][source_id]["count"] += 1
            exploration["sources_accessed"][source_id]["last_accessed"] = now
        
        if len(exploration["queries"]) > 500:
            exploration["queries"] = exploration["queries"][-500:]
        
        self._save_data(data)
        return query_record
    
    async def get_journey(self, notebook_id: str, limit: int = 50) -> Dict:
        """Get the exploration journey for a notebook"""
        if self._use_sqlite:
            conn = self._get_db()
            # Recent queries
            q_rows = conn.execute(
                "SELECT * FROM exploration_queries WHERE notebook_id = ? ORDER BY timestamp DESC LIMIT ?",
                (notebook_id, limit)
            ).fetchall()
            recent_queries = []
            for r in q_rows:
                d = dict(r)
                d['topics'] = json.loads(d['topics']) if isinstance(d['topics'], str) else d['topics']
                d['sources_used'] = json.loads(d['sources_used']) if isinstance(d['sources_used'], str) else d['sources_used']
                recent_queries.append(d)
            # Top topics
            t_rows = conn.execute(
                "SELECT * FROM exploration_topics WHERE notebook_id = ? ORDER BY count DESC LIMIT 20",
                (notebook_id,)
            ).fetchall()
            topics = [{"name": r['topic'], "count": r['count'], "first_seen": r['first_seen'], "last_seen": r['last_seen']} for r in t_rows]
            # Most accessed sources
            s_rows = conn.execute(
                "SELECT * FROM exploration_sources WHERE notebook_id = ? ORDER BY count DESC LIMIT 10",
                (notebook_id,)
            ).fetchall()
            sources = [{"source_id": r['source_id'], "count": r['count'], "first_accessed": r['first_accessed'], "last_accessed": r['last_accessed']} for r in s_rows]
            total = conn.execute(
                "SELECT COUNT(*) as cnt FROM exploration_queries WHERE notebook_id = ?", (notebook_id,)
            ).fetchone()['cnt']
            return {
                "notebook_id": notebook_id,
                "queries": recent_queries,
                "topics_explored": topics,
                "sources_accessed": sources,
                "total_queries": total
            }
        
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
        recent_queries = exploration["queries"][-limit:][::-1]
        topics = sorted(
            [{"name": k, **v} for k, v in exploration["topics_explored"].items()],
            key=lambda x: x["count"],
            reverse=True
        )[:20]
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
        if self._use_sqlite:
            conn = self._get_db()
            # Shallow topics (explored once)
            t_rows = conn.execute(
                "SELECT topic as name, count FROM exploration_topics WHERE notebook_id = ? AND count = 1 LIMIT 5",
                (notebook_id,)
            ).fetchall()
            shallow_topics = [{"name": r['name'], "count": r['count']} for r in t_rows]
            # Most recent query
            q_row = conn.execute(
                "SELECT query FROM exploration_queries WHERE notebook_id = ? ORDER BY timestamp DESC LIMIT 1",
                (notebook_id,)
            ).fetchone()
            suggestions = []
            if q_row:
                suggestions.append({
                    "type": "continue",
                    "message": f"Continue exploring: {q_row['query'][:50]}...",
                    "query": q_row['query']
                })
            if shallow_topics:
                suggestions.append({
                    "type": "dive_deeper",
                    "message": f"Dive deeper into: {shallow_topics[0]['name']}",
                    "topic": shallow_topics[0]["name"]
                })
            return {"suggestions": suggestions, "shallow_topics": shallow_topics}
        
        data = self._load_data()
        if notebook_id not in data["explorations"]:
            return {"suggestions": [], "unexplored_topics": []}
        
        exploration = data["explorations"][notebook_id]
        shallow_topics = [
            {"name": k, "count": v["count"]}
            for k, v in exploration["topics_explored"].items()
            if v["count"] == 1
        ][:5]
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
        if self._use_sqlite:
            conn = self._get_db()
            conn.execute("DELETE FROM exploration_queries WHERE notebook_id = ?", (notebook_id,))
            conn.execute("DELETE FROM exploration_topics WHERE notebook_id = ?", (notebook_id,))
            conn.execute("DELETE FROM exploration_sources WHERE notebook_id = ?", (notebook_id,))
            conn.commit()
            return
        data = self._load_data()
        if notebook_id in data["explorations"]:
            del data["explorations"][notebook_id]
            self._save_data(data)


# Singleton instance
exploration_store = ExplorationStore()
