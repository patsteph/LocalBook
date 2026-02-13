"""
Collection Scheduler - Background job runner for Collectors

Manages periodic collection runs for all active notebooks.
Uses asyncio for non-blocking background execution.
"""
import asyncio
import logging
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional

from agents.collector import CollectorAgent, get_collector
from storage.notebook_store import notebook_store

logger = logging.getLogger(__name__)


class CollectionScheduler:
    """
    Background scheduler for running Collector jobs.
    Each notebook's Collector runs according to its configured schedule.
    """
    
    def __init__(self):
        self._running = False
        self._last_runs: Dict[str, datetime] = {}
        self._task: Optional[asyncio.Task] = None
    
    async def start(self) -> None:
        """Start the background scheduler"""
        if self._running:
            return
        
        self._running = True
        self._task = asyncio.create_task(self._run_loop())
        logger.info("Collection scheduler started")
    
    def stop(self) -> None:
        """Stop the background scheduler"""
        self._running = False
        if self._task:
            self._task.cancel()
        logger.info("Collection scheduler stopped")
    
    async def _run_loop(self) -> None:
        """Main scheduler loop"""
        while self._running:
            try:
                await self._check_and_run_collections()
                # Check every 15 minutes
                await asyncio.sleep(900)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Collection scheduler error: {e}")
                await asyncio.sleep(60)
    
    async def _check_and_run_collections(self) -> None:
        """Check all notebooks and run due collections"""
        notebooks = await notebook_store.list()
        
        for notebook in notebooks:
            notebook_id = notebook["id"]
            
            try:
                collector = get_collector(notebook_id)
                config = collector.get_config()
                
                # Skip if manual-only mode
                if config.collection_mode.value == "manual":
                    continue
                
                # Check if collection is due
                if self._is_collection_due(notebook_id, config):
                    logger.info(f"Running scheduled collection for {notebook_id}")
                    await self._run_collection(collector)
                    self._last_runs[notebook_id] = datetime.utcnow()
            except Exception as e:
                logger.error(f"Error checking notebook {notebook_id}: {e}")
    
    def _is_collection_due(self, notebook_id: str, config) -> bool:
        """Check if a notebook's collection is due"""
        last_run = self._last_runs.get(notebook_id)
        
        if last_run is None:
            return True
        
        frequency = config.schedule.get("frequency", "daily")
        
        intervals = {
            "hourly": timedelta(hours=1),
            "every_4_hours": timedelta(hours=4),
            "daily": timedelta(days=1),
            "weekly": timedelta(weeks=1)
        }
        
        interval = intervals.get(frequency, timedelta(days=1))
        return datetime.utcnow() - last_run >= interval
    
    async def _run_collection(self, collector: CollectorAgent) -> Dict[str, Any]:
        """Run a collection job for a Collector"""
        results = {
            "notebook_id": collector.notebook_id,
            "started_at": datetime.utcnow().isoformat(),
            "items_found": 0,
            "items_queued": 0,
            "items_auto_approved": 0,
            "duplicates_skipped": 0,
            "errors": []
        }
        
        config = collector.get_config()
        config.schedule.get("max_items_per_run", 10)
        
        # Collect from RSS feeds
        rss_feeds = config.sources.get("rss_feeds", [])
        for feed_url in rss_feeds:
            try:
                items = await self._collect_from_rss(feed_url, collector)
                results["items_found"] += len(items)
            except Exception as e:
                results["errors"].append(f"RSS {feed_url}: {str(e)}")
        
        # Collect from news keywords
        keywords = config.sources.get("news_keywords", [])
        if keywords:
            try:
                items = await self._collect_from_news(keywords, collector)
                results["items_found"] += len(items)
            except Exception as e:
                results["errors"].append(f"News search: {str(e)}")
        
        results["completed_at"] = datetime.utcnow().isoformat()
        return results
    
    async def _collect_from_rss(
        self, 
        feed_url: str, 
        collector: CollectorAgent
    ) -> List[Dict]:
        """Collect items from an RSS feed"""
        import feedparser
        
        start = datetime.utcnow()
        items = []
        
        try:
            feed = feedparser.parse(feed_url)
            response_time = (datetime.utcnow() - start).total_seconds() * 1000
            
            if feed.bozo:
                collector.update_source_health(
                    source_id=feed_url,
                    source_url=feed_url,
                    success=False,
                    response_time_ms=response_time
                )
                return []
            
            for entry in feed.entries[:10]:
                from agents.collector import CollectedItem
                
                item = CollectedItem(
                    title=entry.get("title", "Untitled"),
                    url=entry.get("link", ""),
                    content=entry.get("summary", entry.get("description", "")),
                    preview=entry.get("summary", "")[:200],
                    source_name=feed.feed.get("title", feed_url),
                    source_type="rss",
                    collected_at=datetime.utcnow()
                )
                items.append(item)
            
            collector.update_source_health(
                source_id=feed_url,
                source_url=feed_url,
                success=True,
                response_time_ms=response_time,
                items_found=len(items)
            )
            
        except Exception as e:
            logger.error(f"RSS collection error for {feed_url}: {e}")
            collector.update_source_health(
                source_id=feed_url,
                source_url=feed_url,
                success=False
            )
        
        return items
    
    async def _collect_from_news(
        self,
        keywords: List[str],
        collector: CollectorAgent
    ) -> List[Dict]:
        """Collect items from news search (placeholder)"""
        # This would integrate with news APIs
        # For now, return empty - will be implemented with actual news sources
        return []
    
    async def run_immediate(self, notebook_id: str) -> Dict[str, Any]:
        """Run collection immediately for a specific notebook"""
        collector = get_collector(notebook_id)
        result = await self._run_collection(collector)
        self._last_runs[notebook_id] = datetime.utcnow()
        return result
    
    def get_status(self) -> Dict[str, Any]:
        """Get scheduler status"""
        return {
            "running": self._running,
            "notebooks_tracked": len(self._last_runs),
            "last_runs": {
                k: v.isoformat() for k, v in self._last_runs.items()
            }
        }


# Singleton instance
collection_scheduler = CollectionScheduler()
