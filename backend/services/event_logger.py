"""
Event Logger - Crash-safe immediate event persistence

Writes every user action to an append-only log file immediately.
No learning is lost on app crash or restart.

Events are later processed by the memory consolidator.
"""
import json
import os
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional
from enum import Enum
import logging

from config import settings

logger = logging.getLogger(__name__)


class EventType(str, Enum):
    """Types of events we track for memory learning"""
    SOURCE_APPROVED = "source_approved"
    SOURCE_REJECTED = "source_rejected"
    CHAT_QA = "chat_qa"
    HIGHLIGHT_CREATED = "highlight_created"
    BOOKMARK_CREATED = "bookmark_created"
    DOCUMENT_CAPTURED = "document_captured"
    DOCUMENT_READ = "document_read"
    SEARCH_PERFORMED = "search_performed"
    FINDING_CREATED = "finding_created"
    NOTE_ADDED = "note_added"
    QUIZ_COMPLETED = "quiz_completed"
    CONTENT_GENERATED = "content_generated"


class MemoryEvent:
    """A single event to be logged"""
    
    def __init__(
        self,
        event_type: EventType,
        notebook_id: str,
        data: Dict[str, Any],
        source_id: Optional[str] = None
    ):
        self.timestamp = datetime.utcnow()
        self.event_type = event_type
        self.notebook_id = notebook_id
        self.source_id = source_id
        self.data = data
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "timestamp": self.timestamp.isoformat(),
            "event_type": self.event_type.value,
            "notebook_id": self.notebook_id,
            "source_id": self.source_id,
            "data": self.data
        }
    
    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "MemoryEvent":
        event = cls(
            event_type=EventType(d["event_type"]),
            notebook_id=d["notebook_id"],
            data=d["data"],
            source_id=d.get("source_id")
        )
        event.timestamp = datetime.fromisoformat(d["timestamp"])
        return event


class EventLogger:
    """
    Append-only event logger for crash-safe memory persistence.
    
    Every event is immediately written to a JSONL (JSON Lines) file.
    This survives app crashes and restarts.
    """
    
    _instance = None
    _lock = threading.Lock()
    
    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance
    
    def __init__(self):
        if self._initialized:
            return
        
        self.data_dir = Path(settings.data_dir)
        self.events_dir = self.data_dir / "memory" / "events"
        self.events_dir.mkdir(parents=True, exist_ok=True)
        
        # Current event log file (rotate daily)
        self._current_log_path: Optional[Path] = None
        self._file_lock = threading.Lock()
        
        self._initialized = True
        logger.info(f"EventLogger initialized at {self.events_dir}")
    
    def _get_log_path(self) -> Path:
        """Get the current log file path (rotates daily)"""
        today = datetime.utcnow().strftime("%Y-%m-%d")
        return self.events_dir / f"events_{today}.jsonl"
    
    def log_event(self, event: MemoryEvent) -> bool:
        """
        Log an event immediately to the append-only log.
        Returns True if successful.
        """
        try:
            log_path = self._get_log_path()
            event_json = json.dumps(event.to_dict())
            
            with self._file_lock:
                with open(log_path, "a") as f:
                    f.write(event_json + "\n")
                    f.flush()  # Ensure it's written to disk
                    os.fsync(f.fileno())  # Force OS to write to disk
            
            return True
        except Exception as e:
            logger.error(f"Failed to log event: {e}")
            return False
    
    def log(
        self,
        event_type: EventType,
        notebook_id: str,
        data: Dict[str, Any],
        source_id: Optional[str] = None
    ) -> bool:
        """Convenience method to log an event"""
        event = MemoryEvent(
            event_type=event_type,
            notebook_id=notebook_id,
            data=data,
            source_id=source_id
        )
        return self.log_event(event)
    
    def get_events_since(
        self,
        since: datetime,
        notebook_id: Optional[str] = None,
        event_types: Optional[List[EventType]] = None
    ) -> List[MemoryEvent]:
        """
        Get events since a given time.
        Used by the memory consolidator.
        """
        events = []
        
        # Get all log files that might have events since the given time
        for log_file in sorted(self.events_dir.glob("events_*.jsonl")):
            # Parse date from filename
            try:
                file_date_str = log_file.stem.replace("events_", "")
                file_date = datetime.strptime(file_date_str, "%Y-%m-%d")
                
                # Skip files that are too old
                if file_date.date() < since.date():
                    continue
                
                with open(log_file, "r") as f:
                    for line in f:
                        try:
                            event = MemoryEvent.from_dict(json.loads(line.strip()))
                            
                            # Filter by time
                            if event.timestamp < since:
                                continue
                            
                            # Filter by notebook
                            if notebook_id and event.notebook_id != notebook_id:
                                continue
                            
                            # Filter by event type
                            if event_types and event.event_type not in event_types:
                                continue
                            
                            events.append(event)
                        except Exception as e:
                            logger.warning(f"Failed to parse event line: {e}")
                            
            except Exception as e:
                logger.warning(f"Failed to process log file {log_file}: {e}")
        
        return events
    
    def get_unprocessed_events(self, last_processed: datetime) -> List[MemoryEvent]:
        """Get all events that haven't been processed by the consolidator"""
        return self.get_events_since(last_processed)
    
    def get_event_counts(self, notebook_id: Optional[str] = None) -> Dict[str, int]:
        """Get counts of events by type for debugging"""
        counts: Dict[str, int] = {}
        
        for log_file in self.events_dir.glob("events_*.jsonl"):
            try:
                with open(log_file, "r") as f:
                    for line in f:
                        try:
                            data = json.loads(line.strip())
                            
                            if notebook_id and data.get("notebook_id") != notebook_id:
                                continue
                            
                            event_type = data.get("event_type", "unknown")
                            counts[event_type] = counts.get(event_type, 0) + 1
                        except:
                            pass
            except:
                pass
        
        return counts
    
    def cleanup_old_logs(self, days_to_keep: int = 7) -> int:
        """Remove log files older than the specified days"""
        cutoff = datetime.utcnow() - timedelta(days=days_to_keep)
        removed = 0
        
        for log_file in self.events_dir.glob("events_*.jsonl"):
            try:
                file_date_str = log_file.stem.replace("events_", "")
                file_date = datetime.strptime(file_date_str, "%Y-%m-%d")
                
                if file_date < cutoff:
                    log_file.unlink()
                    removed += 1
                    logger.info(f"Removed old event log: {log_file.name}")
            except Exception as e:
                logger.warning(f"Failed to clean up {log_file}: {e}")
        
        return removed


# Singleton instance
event_logger = EventLogger()


# Convenience functions for common events
def log_source_approved(notebook_id: str, source_id: str, source_data: Dict[str, Any]) -> bool:
    """Log when user approves a discovered source"""
    return event_logger.log(
        EventType.SOURCE_APPROVED,
        notebook_id,
        {"source": source_data},
        source_id
    )


def log_source_rejected(notebook_id: str, source_id: str, source_data: Dict[str, Any]) -> bool:
    """Log when user rejects a discovered source"""
    return event_logger.log(
        EventType.SOURCE_REJECTED,
        notebook_id,
        {"source": source_data},
        source_id
    )


def log_chat_qa(notebook_id: str, question: str, answer: str, sources_used: List[str]) -> bool:
    """Log a chat Q&A interaction"""
    return event_logger.log(
        EventType.CHAT_QA,
        notebook_id,
        {
            "question": question,
            "answer_preview": answer[:500],  # Don't store huge answers
            "sources_used": sources_used
        }
    )


def log_highlight(notebook_id: str, source_id: str, text: str, note: Optional[str] = None) -> bool:
    """Log when user creates a highlight"""
    return event_logger.log(
        EventType.HIGHLIGHT_CREATED,
        notebook_id,
        {"text": text[:1000], "note": note},
        source_id
    )


def log_document_captured(notebook_id: str, url: str, title: str, source_type: str) -> bool:
    """Log when user captures a document"""
    return event_logger.log(
        EventType.DOCUMENT_CAPTURED,
        notebook_id,
        {"url": url, "title": title, "source_type": source_type}
    )


def log_quiz_completed(notebook_id: str, topic: str, difficulty: str, score: Optional[int] = None, total: Optional[int] = None) -> bool:
    """Log when user completes a quiz"""
    return event_logger.log(
        EventType.QUIZ_COMPLETED,
        notebook_id,
        {"topic": topic, "difficulty": difficulty, "score": score, "total": total}
    )


def log_content_generated(notebook_id: str, content_type: str, skill_id: str, topic: str = "") -> bool:
    """Log when studio generates content (document, audio, visual)"""
    return event_logger.log(
        EventType.CONTENT_GENERATED,
        notebook_id,
        {"content_type": content_type, "skill_id": skill_id, "topic": topic[:200]}
    )


def log_search(notebook_id: str, query: str, result_count: int = 0) -> bool:
    """Log when user performs a search"""
    return event_logger.log(
        EventType.SEARCH_PERFORMED,
        notebook_id,
        {"query": query[:500], "result_count": result_count}
    )


def log_source_viewed(notebook_id: str, source_id: str, title: str = "") -> bool:
    """Log when user views a source document"""
    return event_logger.log(
        EventType.DOCUMENT_READ,
        notebook_id,
        {"title": title[:200]},
        source_id
    )
