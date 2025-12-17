"""Web search and scraping API endpoints"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import List, Optional
import os
import re
from services.web_scraper import web_scraper
from services.rag_engine import rag_service
from storage.source_store import source_store

router = APIRouter()

class WebSearchRequest(BaseModel):
    query: str
    max_results: int = 20
    offset: int = 0  # For pagination - maps to freshness filter
    freshness: str = None  # pd=past day, pw=past week, pm=past month, py=past year

class WebSearchResult(BaseModel):
    title: str
    snippet: str
    url: str

class WebSearchResponse(BaseModel):
    query: str
    results: List[WebSearchResult]
    count: int
    offset: int = 0
    has_more: bool = False

class WebScrapeRequest(BaseModel):
    urls: List[str]

class ScrapedContent(BaseModel):
    success: bool
    url: str
    title: Optional[str] = None
    author: Optional[str] = None
    date: Optional[str] = None
    text: Optional[str] = None
    word_count: Optional[int] = None
    char_count: Optional[int] = None
    error: Optional[str] = None

class WebScrapeResponse(BaseModel):
    results: List[ScrapedContent]
    successful_count: int
    failed_count: int

class AddToNotebookRequest(BaseModel):
    notebook_id: str
    urls: List[str]
    scraped_content: List[dict]

@router.post("/search", response_model=WebSearchResponse)
async def search(request: WebSearchRequest):
    """Search the web using Brave Search API with freshness-based pagination"""
    try:
        # Brave free tier: offset max is 9, so we use freshness filters for more results
        # Order: initial (all time) -> past week -> past month -> past year (most recent first)
        freshness_map = {0: None, 20: "pw", 40: "pm", 60: "py"}
        freshness = request.freshness or freshness_map.get(request.offset)
        
        results = await web_scraper.search_web(
            request.query, 
            request.max_results,
            freshness=freshness
        )
        
        # More results available if we haven't exhausted freshness filters
        has_more = len(results) >= request.max_results and request.offset < 60
        
        return WebSearchResponse(
            query=request.query,
            results=[WebSearchResult(**r) for r in results],
            count=len(results),
            offset=request.offset,
            has_more=has_more
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Search failed: {str(e)}")

@router.get("/sources/{notebook_id}")
async def get_web_sources(notebook_id: str):
    """Get all web sources previously added to a notebook"""
    try:
        sources = await source_store.list(notebook_id)
        # Filter to only web sources
        web_sources = [
            {
                "id": s["id"],
                "title": s.get("filename", s.get("name", "Unknown")),
                "url": s.get("metadata", {}).get("url", ""),
                "word_count": s.get("metadata", {}).get("word_count", 0),
                "date_added": s.get("created_at", ""),
                "type": s.get("metadata", {}).get("format", "web")
            }
            for s in sources
            if s.get("metadata", {}).get("type") == "web"
        ]
        return {"sources": web_sources, "count": len(web_sources)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get web sources: {str(e)}")

@router.post("/scrape", response_model=WebScrapeResponse)
async def scrape(request: WebScrapeRequest):
    """Scrape content from URLs (including YouTube)"""
    try:
        results = await web_scraper.scrape_urls(request.urls)
        successful = sum(1 for r in results if r["success"])
        failed = len(results) - successful

        return WebScrapeResponse(
            results=[ScrapedContent(**r) for r in results],
            successful_count=successful,
            failed_count=failed
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Scraping failed: {str(e)}")

@router.post("/add-to-notebook")
async def add_to_notebook(request: AddToNotebookRequest):
    """Add scraped web content to a notebook"""
    try:
        added_sources = []

        for content in request.scraped_content:
            if not content.get("success") or not content.get("text"):
                continue

            url = content["url"]
            title = content.get("title", url)
            text = content["text"]

            # Create source
            source = await source_store.create(
                notebook_id=request.notebook_id,
                filename=title,
                metadata={
                    "type": "web",
                    "format": "web",
                    "url": url,
                    "author": content.get("author"),
                    "date": content.get("date"),
                    "word_count": content.get("word_count"),
                    "char_count": content.get("char_count"),
                    "status": "processing"
                }
            )

            # Ingest into RAG with metadata
            source_type = "youtube" if "youtube.com" in url or "youtu.be" in url else "web"
            result = await rag_service.ingest_document(
                notebook_id=request.notebook_id,
                source_id=source["id"],
                text=text,
                filename=title,
                source_type=source_type
            )

            # Update source with processing results
            chunks = result.get("chunks", 0)
            characters = result.get("characters", len(text))
            await source_store.update(request.notebook_id, source["id"], {
                "chunks": chunks,
                "characters": characters,
                "status": "completed",
                "content": text  # Save full text for viewing
            })

            added_sources.append({
                "source_id": source["id"],
                "title": title,
                "url": url,
                "chunks": result.get("chunks", 0),
                "characters": result.get("characters", len(text))
            })

        return {
            "message": f"Added {len(added_sources)} sources to notebook",
            "notebook_id": request.notebook_id,
            "sources": added_sources
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to add to notebook: {str(e)}")
