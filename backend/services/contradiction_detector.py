"""Contradiction Detection Service

Detects conflicting information across sources in a notebook.
Uses embedding similarity to find related claims, then LLM to verify contradictions.
"""

from typing import List, Dict, Optional
from datetime import datetime
import hashlib
import json

from pydantic import BaseModel
from storage.source_store import source_store


class Claim(BaseModel):
    """An extracted claim from a source."""
    id: str
    text: str
    source_id: str
    source_name: str
    chunk_text: str  # Surrounding context
    claim_type: str  # factual, statistical, temporal, conclusion


class Contradiction(BaseModel):
    """A detected contradiction between two claims."""
    id: str
    claim_a: Claim
    claim_b: Claim
    contradiction_type: str  # factual, statistical, temporal, methodological, interpretive
    severity: str  # low, medium, high
    explanation: str
    resolution_hint: Optional[str] = None
    detected_at: str
    dismissed: bool = False
    resolved: bool = False


class ContradictionReport(BaseModel):
    """Report of contradictions in a notebook."""
    notebook_id: str
    generated_at: str
    contradictions: List[Contradiction]
    claims_analyzed: int
    sources_analyzed: int


# In-memory storage for detected contradictions
_contradiction_cache: Dict[str, ContradictionReport] = {}


class ContradictionDetector:
    """Service for detecting contradictions in notebook sources.

    All LLM/embedding calls route through the canonical `ollama_service`
    (priority lane + token tracking + configured models) — no hardcoded URL
    or model. Claim extraction + contradiction checks use the fast model.
    """

    async def _extract_claims_from_chunk(self, chunk: str, source_id: str, source_name: str) -> List[Claim]:
        """Extract factual claims from a text chunk using LLM."""
        prompt = f"""Extract factual claims from this text. Each claim should be:
- A single, atomic statement that can be verified
- Focus on: facts, statistics, dates, conclusions, recommendations

Text:
{chunk[:2000]}

Return ONLY a JSON array of claims. Each claim has:
- "text": the claim statement
- "type": one of "factual", "statistical", "temporal", "conclusion"

Example output:
[{{"text": "The study found a 40% improvement rate", "type": "statistical"}}]

If no clear claims, return: []"""

        try:
            from services.ollama_service import ollama_service
            from config import settings
            # NB: no format="json" here — this prompt asks for a bare JSON
            # ARRAY, but Ollama's JSON mode forces an OBJECT, which makes phi4
            # jam the array into a key ({"[{...}]": false}). robust_json_parse
            # extracts the [...] from plain output reliably.
            _resp = await ollama_service.generate(
                prompt=prompt,
                model=settings.ollama_fast_model,
                temperature=0.1,
                num_predict=600,
                timeout=60.0,
            )
            result = _resp.get("response", "")
            if result:
                from utils.json_repair import robust_json_parse
                claims_data = robust_json_parse(result, expect="array", fallback=[], label="ContradictionDetector")
                # JSON-mode small models often wrap the array in an object
                # ({"claims": [...]}) — unwrap to the first list value. Guards
                # the [:10] slice against a dict/non-list (the original code
                # assumed a bare array and crashed once the model actually ran).
                if isinstance(claims_data, dict):
                    claims_data = next((v for v in claims_data.values() if isinstance(v, list)), [])
                if not isinstance(claims_data, list):
                    claims_data = []
                claims = []
                for i, c in enumerate(claims_data[:10]):  # Limit to 10 claims per chunk
                    if not isinstance(c, dict):
                        continue
                    claim_id = hashlib.md5(f"{source_id}:{c.get('text', '')}".encode()).hexdigest()[:12]
                    claims.append(Claim(
                        id=claim_id,
                        text=c.get("text", ""),
                        source_id=source_id,
                        source_name=source_name,
                        chunk_text=chunk[:500],
                        claim_type=c.get("type", "factual")
                    ))
                return claims
            return []
        except Exception as e:
            print(f"[CONTRADICTION] Claim extraction error: {e}")
            return []
    
    async def _check_contradiction(self, claim_a: Claim, claim_b: Claim) -> Optional[Contradiction]:
        """Check if two claims contradict each other."""
        prompt = f"""Analyze if these two claims contradict each other.

Claim 1 (from "{claim_a.source_name}"):
"{claim_a.text}"

Claim 2 (from "{claim_b.source_name}"):
"{claim_b.text}"

If they contradict, respond with JSON:
{{
  "contradicts": true,
  "type": "factual|statistical|temporal|methodological|interpretive",
  "severity": "low|medium|high",
  "explanation": "Brief explanation of the contradiction",
  "resolution_hint": "Possible reason for difference (optional)"
}}

If they do NOT contradict (they agree, are unrelated, or compatible), respond:
{{"contradicts": false}}"""

        try:
            from services.ollama_service import ollama_service
            from config import settings
            _resp = await ollama_service.generate(
                prompt=prompt,
                model=settings.ollama_fast_model,
                temperature=0.1,
                num_predict=400,
                format="json",
                timeout=60.0,
            )
            result = _resp.get("response", "")
            if not result:
                return None

            from utils.json_repair import robust_json_parse
            data = robust_json_parse(result, label="ContradictionCheck", fallback={})

            if data.get("contradicts"):
                contra_id = hashlib.md5(f"{claim_a.id}:{claim_b.id}".encode()).hexdigest()[:12]
                return Contradiction(
                    id=contra_id,
                    claim_a=claim_a,
                    claim_b=claim_b,
                    contradiction_type=data.get("type", "factual"),
                    severity=data.get("severity", "medium"),
                    explanation=data.get("explanation", ""),
                    resolution_hint=data.get("resolution_hint"),
                    detected_at=datetime.utcnow().isoformat()
                )

            return None
        except Exception as e:
            print(f"[CONTRADICTION] Check error: {e}")
            return None
    
    async def _get_embeddings(self, texts: List[str]) -> List[List[float]]:
        """Get embeddings for texts using Ollama (via the canonical service)."""
        from services.ollama_service import ollama_service
        from config import settings

        embeddings = []
        for text in texts:
            try:
                data = await ollama_service.embed(text[:1000])
                if data.get("embeddings"):
                    embeddings.append(data["embeddings"][0])
                elif data.get("embedding"):
                    embeddings.append(data["embedding"])
                else:
                    embeddings.append([0] * settings.embedding_dim)
            except Exception:
                embeddings.append([0] * settings.embedding_dim)
        return embeddings
    
    def _cosine_similarity(self, a: List[float], b: List[float]) -> float:
        """Calculate cosine similarity between two vectors."""
        import math
        dot = sum(x * y for x, y in zip(a, b))
        norm_a = math.sqrt(sum(x * x for x in a))
        norm_b = math.sqrt(sum(x * x for x in b))
        if norm_a == 0 or norm_b == 0:
            return 0
        return dot / (norm_a * norm_b)
    
    async def scan_notebook(self, notebook_id: str, force_rescan: bool = False) -> ContradictionReport:
        """Scan a notebook for contradictions."""
        
        # Check cache unless force rescan
        if not force_rescan and notebook_id in _contradiction_cache:
            return _contradiction_cache[notebook_id]
        
        sources = await source_store.list(notebook_id)
        if not sources:
            return ContradictionReport(
                notebook_id=notebook_id,
                generated_at=datetime.utcnow().isoformat(),
                contradictions=[],
                claims_analyzed=0,
                sources_analyzed=0
            )
        
        # Extract claims from each source
        all_claims: List[Claim] = []
        
        for source in sources[:10]:  # Limit to 10 sources for performance
            content = source.get("content", "")
            if not content:
                continue
            
            source_id = source.get("id", "")
            source_name = source.get("filename", "Unknown")
            
            # Split into chunks and extract claims
            chunks = [content[i:i+1500] for i in range(0, min(len(content), 6000), 1500)]
            
            for chunk in chunks[:4]:  # Limit chunks per source
                claims = await self._extract_claims_from_chunk(chunk, source_id, source_name)
                all_claims.extend(claims)
        
        if len(all_claims) < 2:
            return ContradictionReport(
                notebook_id=notebook_id,
                generated_at=datetime.utcnow().isoformat(),
                contradictions=[],
                claims_analyzed=len(all_claims),
                sources_analyzed=len(sources)
            )
        
        # Get embeddings for all claims
        claim_texts = [c.text for c in all_claims]
        embeddings = await self._get_embeddings(claim_texts)
        
        # Find similar claim pairs (candidates for contradiction)
        candidates = []
        for i in range(len(all_claims)):
            for j in range(i + 1, len(all_claims)):
                # Skip claims from same source
                if all_claims[i].source_id == all_claims[j].source_id:
                    continue
                
                sim = self._cosine_similarity(embeddings[i], embeddings[j])
                if sim > 0.6:  # High similarity = about same topic
                    candidates.append((all_claims[i], all_claims[j], sim))
        
        # Sort by similarity and check top candidates
        candidates.sort(key=lambda x: x[2], reverse=True)
        
        contradictions: List[Contradiction] = []
        
        for claim_a, claim_b, _ in candidates[:20]:  # Check top 20 candidates
            contradiction = await self._check_contradiction(claim_a, claim_b)
            if contradiction:
                contradictions.append(contradiction)
        
        report = ContradictionReport(
            notebook_id=notebook_id,
            generated_at=datetime.utcnow().isoformat(),
            contradictions=contradictions,
            claims_analyzed=len(all_claims),
            sources_analyzed=len(sources)
        )
        
        # Cache the report
        _contradiction_cache[notebook_id] = report
        
        return report
    
    async def get_cached_report(self, notebook_id: str) -> Optional[ContradictionReport]:
        """Get cached contradiction report if available."""
        return _contradiction_cache.get(notebook_id)
    
    async def dismiss_contradiction(self, notebook_id: str, contradiction_id: str) -> bool:
        """Mark a contradiction as dismissed."""
        if notebook_id in _contradiction_cache:
            report = _contradiction_cache[notebook_id]
            for c in report.contradictions:
                if c.id == contradiction_id:
                    c.dismissed = True
                    return True
        return False
    
    async def clear_cache(self, notebook_id: str):
        """Clear cached report for a notebook."""
        if notebook_id in _contradiction_cache:
            del _contradiction_cache[notebook_id]


# Singleton instance
contradiction_detector = ContradictionDetector()
