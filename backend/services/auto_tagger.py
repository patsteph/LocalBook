"""Auto-Tagging Service for Sources at Ingestion

Uses local LLM (Ollama) to generate context-aware tags for sources as they are
added to notebooks. Tags are derived from:
1. The source content (title + body)
2. The notebook context (subject, focus areas)

Tag categories:
- Content-type: financials, strategy, leadership, product, legal, marketing, operations, technology
- Entity-relationship: competitor, partner, subsidiary, regulator, customer, supplier
- Topical: industry, market-share, earnings, sustainability, innovation, risk, governance

Tags are stored via source_store.set_tags() and are immediately available for:
- Source filtering in the Sources view
- Constellation graph edges (sources sharing tags are linked)
- RAG retrieval biasing
"""
import asyncio
import json
import re
from typing import List, Dict, Optional

import httpx

from config import settings


# Standard tag vocabulary — LLM picks from these to ensure consistency
TAG_VOCABULARY = {
    "content_type": [
        "financials", "strategy", "leadership", "product", "legal",
        "marketing", "operations", "technology", "research", "policy",
        "earnings", "sustainability", "innovation", "risk", "governance",
        "culture", "supply-chain", "partnerships", "m&a", "regulation",
    ],
    "entity_relationship": [
        "competitor", "partner", "subsidiary", "regulator", "customer",
        "supplier", "investor", "analyst", "industry-body",
    ],
    "topical": [
        "breaking-news", "quarterly-results", "annual-report", "sec-filing",
        "opinion", "interview", "press-release", "case-study", "forecast",
    ],
}

# Flatten for the prompt
ALL_TAGS = []
for category_tags in TAG_VOCABULARY.values():
    ALL_TAGS.extend(category_tags)


class AutoTagger:
    """LLM-based auto-tagger for notebook sources."""
    
    def __init__(self):
        self._semaphore = asyncio.Semaphore(2)  # Max 2 concurrent LLM calls
    
    async def generate_tags(
        self,
        title: str,
        content: str,
        notebook_subject: str = "",
        focus_areas: Optional[List[str]] = None,
    ) -> List[str]:
        """Generate tags for a source using local LLM.
        
        Args:
            title: Source title
            content: Source content (first ~2000 chars used)
            notebook_subject: The notebook's subject (e.g., "PepsiCo")
            focus_areas: Notebook focus areas (e.g., ["financials", "competitors"])
            
        Returns:
            List of lowercase tag strings (3-8 tags typically)
        """
        async with self._semaphore:
            try:
                return await self._call_llm(title, content, notebook_subject, focus_areas)
            except Exception as e:
                print(f"[AutoTagger] LLM tagging failed, using fallback: {e}")
                return self._fallback_tags(title, content)
    
    async def _call_llm(
        self,
        title: str,
        content: str,
        notebook_subject: str,
        focus_areas: Optional[List[str]],
    ) -> List[str]:
        """Call Ollama fast model to generate tags."""
        # Truncate content for prompt efficiency
        content_preview = content[:2000] if content else ""
        
        focus_str = ", ".join(focus_areas[:5]) if focus_areas else "general research"
        subject_str = notebook_subject or "unknown"
        
        tag_list_str = ", ".join(ALL_TAGS)
        
        prompt = f"""Tag this document for a research notebook about "{subject_str}" (focus: {focus_str}).

Title: {title}
Content preview: {content_preview}

Pick 3-8 tags from this list that best describe the document:
{tag_list_str}

You may also add 1-2 custom tags if the document covers something not in the list.
Custom tags must be lowercase, hyphenated, 1-3 words (e.g., "market-share", "ceo-change").

If the document discusses a competitor to {subject_str}, include "competitor".
If it discusses a partner or supplier, include "partner" or "supplier".

Return ONLY a JSON array of tag strings, nothing else.
Example: ["financials", "competitor", "quarterly-results"]

Tags:"""

        timeout = httpx.Timeout(15.0, read=20.0)
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(
                f"{settings.ollama_base_url}/api/generate",
                json={
                    "model": settings.ollama_fast_model,
                    "prompt": prompt,
                    "stream": False,
                    "options": {
                        "temperature": 0.2,
                        "num_predict": 100,
                    }
                }
            )
            
            if response.status_code != 200:
                print(f"[AutoTagger] Ollama returned {response.status_code}")
                return self._fallback_tags(title, content)
            
            result = response.json()
            raw = result.get("response", "").strip()
            
            return self._parse_tags(raw)
    
    def _parse_tags(self, raw: str) -> List[str]:
        """Parse LLM output into a clean tag list."""
        # Try JSON parse first
        try:
            # Find JSON array in response
            match = re.search(r'\[.*?\]', raw, re.DOTALL)
            if match:
                tags = json.loads(match.group())
                if isinstance(tags, list):
                    return self._normalize_tags(tags)
        except (json.JSONDecodeError, ValueError):
            pass
        
        # Fallback: extract quoted strings
        quoted = re.findall(r'"([^"]+)"', raw)
        if quoted:
            return self._normalize_tags(quoted)
        
        # Fallback: comma-separated
        if ',' in raw:
            parts = [p.strip().strip('"\'[]') for p in raw.split(',')]
            return self._normalize_tags(parts)
        
        return []
    
    def _normalize_tags(self, tags: List) -> List[str]:
        """Normalize and validate tags."""
        normalized = []
        seen = set()
        
        for tag in tags:
            if not isinstance(tag, str):
                continue
            # Lowercase, strip, replace spaces with hyphens
            t = tag.lower().strip().replace(' ', '-').replace('_', '-')
            # Remove any non-alphanumeric chars except hyphens and &
            t = re.sub(r'[^a-z0-9\-&]', '', t)
            # Skip empty, too short, or too long
            if not t or len(t) < 2 or len(t) > 30:
                continue
            # Deduplicate
            if t not in seen:
                seen.add(t)
                normalized.append(t)
        
        # Cap at 10 tags
        return normalized[:10]
    
    def _fallback_tags(self, title: str, content: str) -> List[str]:
        """Simple keyword-based fallback when LLM is unavailable."""
        text = f"{title} {content[:1000]}".lower()
        tags = []
        
        # Check for financial content
        if any(w in text for w in ['revenue', 'earnings', 'profit', 'income', 'eps', 'margin', 'ebitda']):
            tags.append('financials')
        if any(w in text for w in ['quarterly', 'q1', 'q2', 'q3', 'q4', 'fiscal']):
            tags.append('earnings')
        
        # Check for SEC filings
        if any(w in text for w in ['10-k', '10-q', '8-k', 'sec filing', 'sec.gov']):
            tags.append('sec-filing')
        
        # Check for strategy/leadership
        if any(w in text for w in ['strategy', 'strategic', 'roadmap', 'vision', 'pivot']):
            tags.append('strategy')
        if any(w in text for w in ['ceo', 'cfo', 'cto', 'executive', 'board', 'appointed', 'resigned']):
            tags.append('leadership')
        
        # Check for product/technology
        if any(w in text for w in ['product', 'launch', 'release', 'feature', 'brand']):
            tags.append('product')
        if any(w in text for w in ['technology', 'ai', 'machine learning', 'cloud', 'platform', 'software']):
            tags.append('technology')
        
        # Check for legal/regulatory
        if any(w in text for w in ['lawsuit', 'regulation', 'compliance', 'antitrust', 'fda', 'ftc']):
            tags.append('legal')
        
        # Check for sustainability
        if any(w in text for w in ['sustainability', 'esg', 'carbon', 'climate', 'environmental']):
            tags.append('sustainability')
        
        return tags if tags else ['research']


    async def tag_source_in_notebook(
        self,
        notebook_id: str,
        source_id: str,
        title: str,
        content: str,
    ) -> List[str]:
        """Convenience: auto-tag a source, loading notebook context automatically.
        
        Reads the collector config to get subject/focus_areas, generates tags,
        and stores them via source_store. Returns the generated tags.
        """
        subject, focus_areas = _load_notebook_context(notebook_id)
        
        tags = await self.generate_tags(
            title=title,
            content=content,
            notebook_subject=subject,
            focus_areas=focus_areas,
        )
        
        if tags:
            from storage.source_store import source_store
            await source_store.set_tags(notebook_id, source_id, tags)
            print(f"[AutoTagger] Tagged '{title[:50]}' → {tags}")
        
        return tags


def _load_notebook_context(notebook_id: str) -> tuple:
    """Load subject and focus_areas from a notebook's collector config.
    
    Returns (subject: str, focus_areas: list[str])
    """
    from pathlib import Path
    config_path = Path(settings.data_dir) / "notebooks" / notebook_id / "collector.yaml"
    
    subject = ""
    focus_areas = []
    
    if config_path.exists():
        try:
            import yaml
            with open(config_path, 'r') as f:
                data = yaml.safe_load(f) or {}
            subject = data.get("subject", "")
            focus_areas = data.get("focus_areas", [])
        except Exception as e:
            print(f"[AutoTagger] Could not load config for {notebook_id}: {e}")
    
    return subject, focus_areas


# Singleton
auto_tagger = AutoTagger()
