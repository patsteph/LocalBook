"""ScoringMixin — extracted from the former agents/collector.py (Wave 6 split)."""
from ._models import *  # noqa: F401,F403


class ScoringMixin:
    async def _calculate_confidence(self, item: CollectedItem) -> CollectedItem:
        """Calculate confidence scores with explanations, incorporating learned preferences"""
        from agents.curator import curator
        
        reasons = []
        learned_bonus = 0.0
        
        # Get learned preferences from Curator (user's past behavior)
        try:
            preferences = await curator.get_learned_preferences(self.notebook_id)
            
            # Boost if matches preferred topics
            item_text = f"{item.title} {item.content[:500]}".lower()
            for topic in preferences.get("preferred_topics", [])[:5]:
                if topic.lower() in item_text:
                    learned_bonus += 0.1
                    reasons.append(f"Matches preferred topic: {topic}")
                    break  # One bonus per item
            
            # Boost if from preferred source
            if item.source_name in preferences.get("preferred_sources", []):
                learned_bonus += 0.1
                reasons.append(f"From preferred source: {item.source_name}")
            
            # Penalize if matches rejected patterns
            if item.url:
                for rejected in preferences.get("rejected_patterns", []):
                    if rejected and rejected in item.url:
                        learned_bonus -= 0.2
                        reasons.append("Similar to previously rejected content")
                        break
                        
        except Exception as e:
            logger.debug(f"Could not get learned preferences: {e}")
        
        # Relevance score - how well does it match intent?
        relevance = await self._score_relevance(item)
        item.relevance_score = relevance["score"]
        if relevance["reason"]:
            reasons.append(relevance["reason"])
        
        # Source trust - is this a reliable source?
        # User-added sources get high trust — the user explicitly validated them
        user_sources = set(
            list(self.config.sources.get("web_pages", []))
            + list(self.config.sources.get("rss_feeds", []))
            + list(self.config.sources.get("feed_pages", []))
        )
        item_source_url = item.source_url or item.url or ""
        is_user_added = bool(item_source_url) and any(
            item_source_url.startswith(s) or s.startswith(item_source_url)
            for s in user_sources if s
        )

        if is_user_added:
            item.source_trust = 0.95
            reasons.append("User-added source (high trust)")
        else:
            health = self._source_health.get(item.source_name)
            if health:
                if health.health == SourceHealth.HEALTHY:
                    item.source_trust = 0.9
                    reasons.append(f"Trusted source ({health.items_collected} items collected)")
                elif health.health == SourceHealth.DEGRADED:
                    item.source_trust = 0.6
                    reasons.append("Source has been slow recently")
                else:
                    item.source_trust = 0.3
                    reasons.append("Source reliability issues")
            else:
                item.source_trust = 0.5
                reasons.append("New source (no history)")
        
        # Freshness score - how recent is this?
        max_age_days = self.config.filters.get("max_age_days", 30) if self.config.filters else 30
        
        # Try to get actual published date if collected_at defaults to "now"
        age_hours = (datetime.utcnow() - item.collected_at).total_seconds() / 3600
        
        # If age_hours < 1 (i.e. defaulted to utcnow), try extracting date from content
        if age_hours < 1 and item.content:
            try:
                from services.content_date_extractor import extract_content_date
                extracted = extract_content_date(item.title, item.content[:2000])
                if extracted:
                    from datetime import date as date_type
                    parsed_date = datetime.fromisoformat(extracted)
                    age_hours = (datetime.utcnow() - parsed_date).total_seconds() / 3600
            except Exception as _e:
                logger.debug(f"[collector] {type(_e).__name__}: {_e}")
        
        max_age_hours = max_age_days * 24
        
        if age_hours < 24:
            item.freshness_score = 1.0
            reasons.append("Published today")
        elif age_hours < 72:
            item.freshness_score = 0.8
            reasons.append("Published this week")
        elif age_hours < 168:
            item.freshness_score = 0.6
            reasons.append("Published within 7 days")
        elif age_hours < max_age_hours:
            item.freshness_score = max(0.3, 1 - (age_hours / max_age_hours))
        else:
            # HARD GATE: Content older than max_age_days is stale
            item.freshness_score = 0.0
            reasons.append(f"Stale content (>{max_age_days} days old)")
        
        # Overall confidence - weighted combination + learned preference bonus
        base_confidence = (
            item.relevance_score * 0.5 +
            item.source_trust * 0.3 +
            item.freshness_score * 0.2
        )
        
        # Hard cap: if freshness is 0 (stale), cap confidence to prevent passing threshold
        if item.freshness_score == 0.0:
            base_confidence = min(base_confidence, 0.35)
            reasons.append("Confidence capped — content too old")
        
        item.overall_confidence = max(0.0, min(1.0, base_confidence + learned_bonus))
        
        item.confidence_reasons = reasons
        return item

    def _get_intent_embedding(self):
        """Get (and cache) the embedding for this notebook's intent + focus areas."""
        if not hasattr(self, '_intent_embedding') or self._intent_embedding is None:
            from services.rag_embeddings import encode
            import numpy as np
            
            focus_parts = []
            if self.config.intent:
                focus_parts.append(self.config.intent)
            if self.config.focus_areas:
                focus_parts.extend(self.config.focus_areas[:5])
            
            if not focus_parts:
                return None
            
            reference_text = " ".join(focus_parts)
            embs = encode([reference_text])
            self._intent_embedding = np.array(embs[0]) if len(embs) > 0 else None
        return self._intent_embedding

    async def _score_relevance(self, item: CollectedItem) -> Dict[str, Any]:
        """Score how relevant an item is to the notebook intent using embedding similarity.
        
        Uses cosine similarity between item content and notebook intent/focus areas.
        This is ~100x faster than LLM scoring (~5ms vs ~3-5s per item).
        """
        if not self.config.intent and not self.config.focus_areas:
            return {"score": 0.5, "reason": "No intent configured"}
        
        try:
            from services.rag_embeddings import encode
            import numpy as np
            
            ref_emb = self._get_intent_embedding()
            if ref_emb is None:
                return {"score": 0.5, "reason": "Could not encode intent"}
            
            # Build item text from title + preview
            item_text = f"{item.title} {item.content[:500]}"
            
            # Encode item and compute cosine similarity
            item_embs = encode([item_text])
            if len(item_embs) > 0:
                item_emb = np.array(item_embs[0])
                
                dot = np.dot(ref_emb, item_emb)
                norm = np.linalg.norm(ref_emb) * np.linalg.norm(item_emb)
                similarity = float(dot / norm) if norm > 0 else 0.0
                
                # Map similarity to 0-1 score (typical range is 0.3-0.9)
                # Shift and scale so 0.3 → 0.0, 0.9 → 1.0
                score = max(0.0, min(1.0, (similarity - 0.3) / 0.6))
                
                reason = f"Embedding relevance: {similarity:.2f}"
                if score >= 0.7:
                    reason = "Strong semantic match to research focus"
                elif score >= 0.4:
                    reason = "Moderate match to research focus"
                else:
                    reason = "Weak match to research focus"
                
                return {"score": round(score, 2), "reason": reason}
        except Exception as e:
            logger.error(f"Embedding relevance scoring failed: {e}")
        
        return {"score": 0.5, "reason": "Could not score relevance"}
