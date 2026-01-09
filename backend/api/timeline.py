"""Timeline API endpoints for date/event extraction"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional, List
from storage.source_store import source_store
from storage.notebook_store import notebook_store
import asyncio
import re
from datetime import datetime
import dateparser

router = APIRouter()

# In-memory storage for timeline data and extraction progress
# In production, this would be in a database
_timeline_data = {}  # notebook_id -> list of events
_extraction_progress = {}  # notebook_id -> progress dict


class TimelineEvent(BaseModel):
    """Timeline event - matches frontend TimelineEvent interface"""
    event_id: str
    notebook_id: str
    source_id: str
    date_timestamp: int  # Unix timestamp for sorting
    date_string: str  # Human-readable date
    date_type: str  # 'exact', 'month', 'year', 'range'
    event_text: str  # The text containing the date
    context: str  # Surrounding context
    page_number: Optional[int] = None
    char_offset: Optional[int] = None
    confidence: float = 0.8
    filename: Optional[str] = None


class ExtractionProgress(BaseModel):
    """Extraction progress - matches frontend ExtractionProgress interface"""
    status: str  # 'idle', 'extracting', 'completed', 'failed'
    current: int
    total: int
    message: str


@router.get("/{notebook_id}")
async def get_timeline(notebook_id: str, source_id: Optional[str] = None):
    """Get timeline events for a notebook"""
    events = _timeline_data.get(notebook_id, [])
    
    # Filter by source if specified
    if source_id:
        events = [e for e in events if e["source_id"] == source_id]
    
    # Sort by date timestamp
    events.sort(key=lambda x: x.get("date_timestamp", 0))
    
    return events


@router.post("/extract/{notebook_id}")
async def extract_timeline(notebook_id: str):
    """Start timeline extraction for a notebook"""
    notebook = await notebook_store.get(notebook_id)
    if not notebook:
        raise HTTPException(status_code=404, detail="Notebook not found")
    
    # Initialize progress
    _extraction_progress[notebook_id] = {
        "status": "extracting",
        "current": 0,
        "total": 0,
        "message": "Starting extraction..."
    }
    
    # Start extraction in background
    asyncio.create_task(_extract_timeline_async(notebook_id))
    
    return {"message": "Timeline extraction started"}


@router.get("/progress/{notebook_id}")
async def get_extraction_progress(notebook_id: str):
    """Get timeline extraction progress"""
    progress = _extraction_progress.get(notebook_id, {
        "status": "idle",
        "current": 0,
        "total": 0,
        "message": "No extraction in progress"
    })
    return progress


@router.delete("/{notebook_id}")
async def delete_timeline(notebook_id: str):
    """Delete all timeline events for a notebook"""
    if notebook_id in _timeline_data:
        del _timeline_data[notebook_id]
    return {"message": "Timeline deleted"}


async def _extract_timeline_async(notebook_id: str):
    """Background task to extract timeline events from sources"""
    try:
        sources = await source_store.list(notebook_id)
        total = len(sources)
        
        _extraction_progress[notebook_id] = {
            "status": "extracting",
            "current": 0,
            "total": total,
            "message": f"Processing {total} sources..."
        }
        
        events = []
        
        for i, source in enumerate(sources):
            _extraction_progress[notebook_id]["current"] = i + 1
            _extraction_progress[notebook_id]["message"] = f"Processing {source.get('filename', 'source')}..."
            
            # Get source content
            content = await source_store.get_content(notebook_id, source["id"])
            if content and content.get("content"):
                # Extract dates from content
                source_events = _extract_dates_from_text(
                    text=content["content"],
                    notebook_id=notebook_id,
                    source_id=source["id"],
                    filename=source.get("filename", "Unknown")
                )
                events.extend(source_events)
            
            # Small delay to not block
            await asyncio.sleep(0.1)
        
        # Store events
        _timeline_data[notebook_id] = events
        
        _extraction_progress[notebook_id] = {
            "status": "complete",  # Must match frontend check for 'complete'
            "current": total,
            "total": total,
            "message": f"Extracted {len(events)} events from {total} sources"
        }
        
    except Exception as e:
        _extraction_progress[notebook_id] = {
            "status": "failed",
            "current": 0,
            "total": 0,
            "message": f"Extraction failed: {str(e)}"
        }


def _extract_dates_from_text(text: str, notebook_id: str, source_id: str, filename: str) -> List[dict]:
    """Extract date mentions from text"""
    events = []
    
    # Common date patterns
    patterns = [
        # Full dates: January 15, 2023 or 15 January 2023
        r'\b(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2},?\s+\d{4}\b',
        r'\b\d{1,2}\s+(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{4}\b',
        # Numeric dates: 2023-01-15, 01/15/2023, 15/01/2023
        r'\b\d{4}-\d{2}-\d{2}\b',
        r'\b\d{1,2}/\d{1,2}/\d{4}\b',
        # Month Year: January 2023
        r'\b(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{4}\b',
        # Year only in context: "in 2023", "during 1999"
        r'\b(?:in|during|since|from|until|by)\s+\d{4}\b',
    ]
    
    event_id_counter = 0
    
    for pattern in patterns:
        for match in re.finditer(pattern, text, re.IGNORECASE):
            date_str = match.group()
            start_pos = match.start()
            
            # Get surrounding context (100 chars before and after)
            context_start = max(0, start_pos - 100)
            context_end = min(len(text), start_pos + len(date_str) + 100)
            context = text[context_start:context_end].strip()
            
            # Try to parse the date
            parsed_date = dateparser.parse(date_str)
            if parsed_date:
                timestamp = int(parsed_date.timestamp())
                date_type = _determine_date_type(date_str)
            else:
                # Fallback for unparseable dates
                timestamp = 0
                date_type = "unknown"
            
            event_id_counter += 1
            events.append({
                "event_id": f"{source_id}_{event_id_counter}",
                "notebook_id": notebook_id,
                "source_id": source_id,
                "date_timestamp": timestamp,
                "date_string": date_str,
                "date_type": date_type,
                "event_text": date_str,
                "context": context,
                "page_number": None,
                "char_offset": start_pos,
                "confidence": 0.8,
                "filename": filename
            })
    
    return events


def _determine_date_type(date_str: str) -> str:
    """Determine the type/precision of a date string"""
    # Check for full date (day, month, year)
    if re.search(r'\d{1,2}[,\s]+\d{4}', date_str) or re.search(r'\d{4}-\d{2}-\d{2}', date_str):
        return "exact"
    # Check for month and year
    elif re.search(r'(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{4}', date_str, re.IGNORECASE):
        return "month"
    # Year only
    else:
        return "year"


# =============================================================================
# Enhanced Timeline Extraction (LLM-powered)
# =============================================================================

@router.post("/extract-smart/{notebook_id}")
async def extract_timeline_smart(notebook_id: str):
    """Extract timeline using LLM for better event understanding.
    
    Uses structured LLM output to identify events with context,
    not just date patterns.
    """
    from services.structured_llm import structured_llm
    
    notebook = await notebook_store.get(notebook_id)
    if not notebook:
        raise HTTPException(status_code=404, detail="Notebook not found")
    
    sources = await source_store.list(notebook_id)
    if not sources:
        raise HTTPException(status_code=404, detail="No sources in notebook")
    
    # Collect content from sources
    content = "\n\n".join([
        s.get("content", "")[:3000] for s in sources[:5]
    ])
    
    # Use structured LLM to extract timeline
    result = await structured_llm.extract_timeline(content)
    
    # Convert to timeline events format
    events = []
    for i, event in enumerate(result.events):
        # Try to parse the date for timestamp
        parsed_date = dateparser.parse(event.date)
        timestamp = int(parsed_date.timestamp()) if parsed_date else 0
        
        events.append({
            "event_id": f"smart_{notebook_id}_{i}",
            "notebook_id": notebook_id,
            "source_id": "multiple",
            "date_timestamp": timestamp,
            "date_string": event.date,
            "date_type": "exact" if parsed_date else "approximate",
            "event_text": event.title,
            "context": event.description,
            "importance": event.importance,
            "confidence": 0.9,
            "filename": "AI-extracted"
        })
    
    # Store events
    _timeline_data[notebook_id] = events
    
    return {
        "notebook_id": notebook_id,
        "events": events,
        "time_span": result.time_span,
        "context": result.context,
        "count": len(events)
    }


async def extract_timeline_for_source(notebook_id: str, source_id: str, content: str, filename: str):
    """Helper function to extract timeline for a single source.
    
    Can be called when a source is added to auto-extract timeline events.
    """
    events = _extract_dates_from_text(
        text=content,
        notebook_id=notebook_id,
        source_id=source_id,
        filename=filename
    )
    
    # Append to existing timeline data
    if notebook_id not in _timeline_data:
        _timeline_data[notebook_id] = []
    
    _timeline_data[notebook_id].extend(events)
    return len(events)


@router.post("/{notebook_id}/extract-all")
async def extract_all_timelines(notebook_id: str):
    """Extract timelines from ALL existing sources in a notebook.
    
    This backfills timeline data for sources that existed before auto-extraction.
    """
    sources = await source_store.list(notebook_id)
    if not sources:
        raise HTTPException(status_code=404, detail="No sources found in notebook")
    
    # Clear existing timeline for this notebook to avoid duplicates
    _timeline_data[notebook_id] = []
    
    total_events = 0
    processed = 0
    
    for source in sources:
        source_id = source.get("id")
        content = source.get("content", "")
        filename = source.get("filename", "Unknown")
        
        if content:
            count = await extract_timeline_for_source(
                notebook_id, source_id, content, filename
            )
            total_events += count
        processed += 1
    
    return {
        "notebook_id": notebook_id,
        "sources_processed": processed,
        "events_extracted": total_events,
        "message": f"Extracted {total_events} timeline events from {processed} sources"
    }
