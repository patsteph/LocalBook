"""Cross-Notebook Search Service

Searches ALL notebooks' LanceDB tables for a query and returns results
with notebook attribution. Used by the Curator agent for cross-notebook
synthesis via @curator mentions in chat.
"""
import asyncio
from typing import List, Dict, Optional

import lancedb
import numpy as np

from config import settings
from storage.notebook_store import notebook_store


class CrossNotebookSearch:
    """Search across all notebook RAG tables with notebook attribution."""

    def __init__(self):
        self._db = None

    def _get_db(self):
        if self._db is None:
            db_path = settings.data_dir / "lancedb"
            self._db = lancedb.connect(str(db_path))
        return self._db

    async def search(
        self,
        query: str,
        notebook_ids: Optional[List[str]] = None,
        exclude_notebook_ids: Optional[List[str]] = None,
        top_k: int = 5,
        top_k_per_notebook: int = 3,
    ) -> Dict:
        """Search across multiple notebooks' LanceDB tables.

        Args:
            query: The search query text.
            notebook_ids: If set, only search these notebooks. Otherwise search all.
            exclude_notebook_ids: Notebooks to skip.
            top_k: Total results to return after cross-notebook ranking.
            top_k_per_notebook: Max results per notebook before merging.

        Returns:
            Dict with 'results' (list of hits with notebook attribution) and
            'notebooks_searched' count.
        """
        from services.rag_engine import rag_engine

        notebooks = await notebook_store.list()
        if not notebooks:
            return {"results": [], "notebooks_searched": 0}

        nb_map = {n["id"]: n.get("title", "Untitled") for n in notebooks}

        if notebook_ids:
            target_ids = [nid for nid in notebook_ids if nid in nb_map]
        else:
            target_ids = list(nb_map.keys())

        if exclude_notebook_ids:
            target_ids = [nid for nid in target_ids if nid not in exclude_notebook_ids]

        if not target_ids:
            return {"results": [], "notebooks_searched": 0}

        query_embedding = rag_engine.encode(query)[0]

        all_results: List[Dict] = []
        notebooks_searched = 0
        db = self._get_db()

        for nb_id in target_ids:
            table_name = f"notebook_{nb_id}"
            try:
                table = db.open_table(table_name)
                if table.count_rows() == 0:
                    continue
            except Exception:
                continue

            notebooks_searched += 1

            try:
                hits = (
                    table.search(query_embedding.tolist())
                    .limit(top_k_per_notebook)
                    .to_list()
                )

                for hit in hits:
                    if hit.get("source_id") == "placeholder":
                        continue
                    all_results.append({
                        "notebook_id": nb_id,
                        "notebook_title": nb_map.get(nb_id, "Untitled"),
                        "source_id": hit.get("source_id", ""),
                        "filename": hit.get("filename", ""),
                        "text": hit.get("text", ""),
                        "chunk_index": hit.get("chunk_index", 0),
                        "source_type": hit.get("source_type", ""),
                        "_distance": hit.get("_distance", 999),
                    })
            except Exception as e:
                print(f"[CrossSearch] Error searching notebook {nb_id}: {e}")
                continue

        all_results.sort(key=lambda r: r["_distance"])
        top_results = all_results[:top_k]

        return {
            "results": top_results,
            "notebooks_searched": notebooks_searched,
        }

    def build_context(self, results: List[Dict], max_chars: int = 8000) -> str:
        """Build a context string from cross-notebook search results for LLM consumption."""
        if not results:
            return ""

        lines = []
        total = 0
        for r in results:
            nb_label = r.get("notebook_title", "Notebook")
            source = r.get("filename", "source")
            text = r.get("text", "")
            entry = f"[{nb_label} / {source}]: {text}"
            if total + len(entry) > max_chars:
                remaining = max_chars - total
                if remaining > 100:
                    lines.append(entry[:remaining] + "...")
                break
            lines.append(entry)
            total += len(entry)

        return "\n\n".join(lines)


cross_notebook_search = CrossNotebookSearch()
