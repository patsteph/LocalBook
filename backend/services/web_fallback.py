"""Web Search Fallback Service

Provides web search augmentation when local RAG confidence is too low.
Searches the web, summarizes results, and combines with local context.
"""
import asyncio
from typing import Dict, List, Optional, Tuple
import httpx

from api.settings import get_api_key
from config import settings


class WebFallbackService:
    """Fallback to web search when local RAG confidence is insufficient."""
    
    def __init__(self):
        self.confidence_threshold = 0.20  # Trigger web search if max confidence < 20%
        self.max_web_results = 5
        self.scrape_timeout = 15.0
    
    def should_use_web_fallback(
        self,
        max_confidence: float,
        citations_count: int,
        low_confidence_flag: bool
    ) -> Tuple[bool, str]:
        """Determine if web search fallback should be triggered.
        
        Returns: (should_fallback, reason)
        """
        # No citations at all - definitely need web
        if citations_count == 0:
            return True, "no_local_results"
        
        # Very low confidence
        if max_confidence < self.confidence_threshold:
            return True, "low_confidence"
        
        # Low confidence flag already set by RAG
        if low_confidence_flag and max_confidence < 0.35:
            return True, "low_confidence_flag"
        
        return False, ""
    
    async def search_web(self, query: str, max_results: int = 5) -> List[Dict]:
        """Search the web using Brave Search API.
        
        Returns list of: {title, url, snippet}
        """
        brave_api_key = get_api_key("brave_api_key")
        
        if not brave_api_key:
            print("[WebFallback] Brave API key not configured, skipping web search")
            return []
        
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(
                    "https://api.search.brave.com/res/v1/web/search",
                    headers={
                        "Accept": "application/json",
                        "Accept-Encoding": "gzip",
                        "X-Subscription-Token": brave_api_key
                    },
                    params={
                        "q": query,
                        "count": max_results,
                    }
                )
                
                if response.status_code != 200:
                    print(f"[WebFallback] Brave API error: {response.status_code}")
                    return []
                
                data = response.json()
                results = []
                
                for result in data.get("web", {}).get("results", [])[:max_results]:
                    results.append({
                        "title": result.get("title", ""),
                        "url": result.get("url", ""),
                        "snippet": result.get("description", ""),
                    })
                
                print(f"[WebFallback] Found {len(results)} web results for: {query[:50]}")
                return results
                
        except Exception as e:
            print(f"[WebFallback] Search error: {e}")
            return []
    
    async def scrape_url(self, url: str) -> Optional[str]:
        """Scrape content from a URL using trafilatura."""
        try:
            import trafilatura
            
            async with httpx.AsyncClient(timeout=self.scrape_timeout) as client:
                response = await client.get(url, follow_redirects=True)
                if response.status_code != 200:
                    return None
                
                html = response.text
                text = trafilatura.extract(html, include_comments=False, include_tables=True)
                
                if text:
                    # Limit to first ~2000 chars for context
                    return text[:2000] + "..." if len(text) > 2000 else text
                return None
                
        except Exception as e:
            print(f"[WebFallback] Scrape error for {url}: {e}")
            return None
    
    async def get_web_context(
        self,
        query: str,
        scrape_top_n: int = 2
    ) -> Tuple[str, List[Dict]]:
        """Get web context for a query.
        
        Args:
            query: The search query
            scrape_top_n: Number of top results to scrape for full content
        
        Returns: (context_string, web_sources_list)
        """
        # Search
        results = await self.search_web(query, self.max_web_results)
        if not results:
            return "", []
        
        # Scrape top N results for detailed content
        scraped = []
        for result in results[:scrape_top_n]:
            content = await self.scrape_url(result["url"])
            if content:
                scraped.append({
                    **result,
                    "content": content
                })
        
        # Build context
        context_parts = []
        web_sources = []
        
        # Add scraped content first (more detailed)
        for i, item in enumerate(scraped):
            context_parts.append(
                f"[Web {i+1}] {item['title']}\n"
                f"Source: {item['url']}\n"
                f"{item['content']}"
            )
            web_sources.append({
                "title": item["title"],
                "url": item["url"],
                "type": "scraped"
            })
        
        # Add snippets for remaining results
        snippet_start = len(scraped)
        for i, result in enumerate(results[scrape_top_n:]):
            idx = snippet_start + i + 1
            context_parts.append(
                f"[Web {idx}] {result['title']}\n"
                f"Source: {result['url']}\n"
                f"{result['snippet']}"
            )
            web_sources.append({
                "title": result["title"],
                "url": result["url"],
                "type": "snippet"
            })
        
        context = "\n\n".join(context_parts)
        return context, web_sources
    
    async def augment_rag_response(
        self,
        question: str,
        local_context: str,
        local_citations: List[Dict],
        local_answer: str,
        max_confidence: float,
        llm_generate_fn
    ) -> Dict:
        """Augment a RAG response with web search when confidence is low.
        
        Args:
            question: Original question
            local_context: Context from local sources
            local_citations: Citations from local sources
            local_answer: Answer generated from local sources
            max_confidence: Maximum confidence from local retrieval
            llm_generate_fn: Async function to generate LLM response
            
        Returns: Augmented response dict
        """
        # Get web context
        web_context, web_sources = await self.get_web_context(question)
        
        if not web_context:
            # No web results, return original
            return {
                "answer": local_answer,
                "citations": local_citations,
                "web_sources": None,
                "web_augmented": False
            }
        
        # Combine contexts
        combined_context = ""
        if local_context:
            combined_context = f"LOCAL SOURCES:\n{local_context}\n\n"
        combined_context += f"WEB SOURCES:\n{web_context}"
        
        # Generate new answer with combined context
        prompt = f"""Answer the question using BOTH local sources and web sources provided.
Prioritize local sources when they have relevant information.
Use web sources to fill in gaps or provide additional context.
Cite sources with [1], [2], etc. for local sources and [Web 1], [Web 2], etc. for web sources.

Question: {question}

{combined_context}

Answer:"""
        
        try:
            new_answer = await llm_generate_fn(prompt)
            
            return {
                "answer": new_answer,
                "citations": local_citations,
                "web_sources": web_sources,
                "web_augmented": True
            }
        except Exception as e:
            print(f"[WebFallback] LLM generation error: {e}")
            # Return original on error
            return {
                "answer": local_answer,
                "citations": local_citations,
                "web_sources": web_sources,
                "web_augmented": False,
                "web_error": str(e)
            }


# Singleton instance
web_fallback = WebFallbackService()
