"""article_batch_processor — P14.BATCH (2026-06-12).

Combines the three per-content-article phi4-mini calls (classifier,
summarizer, sectioner) into a single LLM call. Same model, same
context, one model load. ~30s per article instead of ~90s — 3x speedup
on Apple Silicon when phi4-mini is under load (the observed regime
during reprocess + IMAP catch-up).

The batch prompt returns:
  - kind (content/sponsor/ad/jobs/navigation) + kind_confidence
  - summary (1-2 sentences, content-only)
  - topic_tags (2-4 lowercase, content-only)
  - section_assignment (match existing OR propose new, content-only)

For non-content articles the LLM is instructed to leave summary/section
fields null. We still get the kind decision in one call without wasting
work on the downstream fields.

Replaces the three previously-separate calls in
`correspondent_processor._summarize_articles_background_unlocked`:
  - article_classifier.classify_article
  - correspondent_processor.summarize_article
  - article_sectioner.classify_section
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


VALID_KINDS = ("content", "sponsor", "ad", "jobs", "navigation")


_BATCH_SYSTEM = """You analyze a single newsletter article and output ONE JSON object with ALL of these decisions in a single response.

1. kind: classify into exactly one of:
   - content: a real article / story / analysis / commentary / link to news
   - sponsor: clearly labeled paid placement ("Sponsor", "Promoted", "Brought to you by")
   - ad: standalone advertisement (product pitch, "Try X today")
   - jobs: job listings, hiring posts, "We're hiring"
   - navigation: header / footer / "View in browser" / unsubscribe / chrome / "Click here"

2. summary: a 1-2 sentence factual summary (max ~50 words).
   IF kind is NOT 'content', set this to null.

3. topic_tags: 2-4 short topic labels (lowercase, hyphenated for multi-word).
   IF kind is NOT 'content', set this to [].

4. section_assignment: pick best fit for THIS article among existing
   notebook sections, OR propose a new short noun phrase section.
   - match_existing_id: id of best-fit existing section (verbatim from list), OR null
   - proposed_new_section: short noun phrase (2-5 words) for a NEW section, OR null
   - section_confidence: 0.0-1.0
   Either match_existing_id OR proposed_new_section is set, not both.
   IF kind is NOT 'content', set both to null and section_confidence to 0.0.

5. kind_confidence: 0.0-1.0 confidence in the kind decision.

Output JSON only:
{
  "kind": "content" | "sponsor" | "ad" | "jobs" | "navigation",
  "kind_confidence": 0.0-1.0,
  "summary": "..." | null,
  "topic_tags": [...] ,
  "match_existing_id": "..." | null,
  "proposed_new_section": "..." | null,
  "section_confidence": 0.0-1.0
}

Rules:
- Be strict on kind. A section that mentions products in passing is still content. A section that's primarily SELLING something is sponsor or ad.
- Prefer existing sections when reasonable. Don't propose a new section unless the topic is genuinely orthogonal.
- topic_tags: 2-4, lowercase, hyphenated for multi-word (e.g. "ai-accounting").
"""


def _format_sections_list(sections: List[Dict[str, Any]]) -> str:
    if not sections:
        return "(no existing sections in this notebook — propose a new one if the article is content)"
    lines = []
    for s in sections[:20]:
        sid = s.get("id") or ""
        name = (s.get("name") or "").strip()
        count = int(s.get("article_count") or 0)
        lines.append(f'  - id={sid} name="{name}" article_count={count}')
    return "\n".join(lines)


async def batch_analyze_article(
    *,
    title: str,
    body_text: str,
    existing_sections: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Single phi4-mini call = classifier + summarizer + sectioner.

    Returns a dict with keys:
      kind, kind_confidence, summary, topic_tags,
      match_existing_id, proposed_new_section, section_confidence

    On failure returns safe defaults — `kind='content'`, `confidence=0.0`,
    null section/summary — so downstream gracefully treats failures as
    "include but don't auto-act."
    """
    from services.ollama_service import ollama_service, PRIORITY_BACKGROUND
    from config import settings

    body = (body_text or "")[:2500]
    if not body.strip() or len(body) < 50:
        return {
            "kind": "navigation",
            "kind_confidence": 0.7,
            "summary": None,
            "topic_tags": [],
            "match_existing_id": None,
            "proposed_new_section": None,
            "section_confidence": 0.0,
        }

    user_prompt = (
        f"ARTICLE:\nTITLE: {title or '(no title)'}\nBODY:\n{body}\n\n"
        f"EXISTING NOTEBOOK SECTIONS:\n{_format_sections_list(existing_sections)}"
    )

    try:
        # Yield to any in-progress foreground generation (see foreground_guard).
        from services.memory_steward import await_background_clearance
        await await_background_clearance()
        result = await ollama_service.generate(
            prompt=user_prompt,
            system=_BATCH_SYSTEM,
            model=settings.ollama_fast_model,
            temperature=0.1,
            num_predict=400,
            format="json",
            priority=PRIORITY_BACKGROUND,
        )
        raw = (result or {}).get("response", "").strip()
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise ValueError("non-dict response")

        kind = str(data.get("kind", "content")).strip().lower()
        if kind not in VALID_KINDS:
            kind = "content"
        is_content = (kind == "content")

        # Validate match_existing_id against the real section list (LLM
        # can hallucinate ids). Drop unknown ones.
        match_id = data.get("match_existing_id")
        if match_id is not None:
            valid_ids = {s.get("id") for s in existing_sections if s.get("id")}
            if str(match_id) not in valid_ids:
                logger.debug(f"[batch] LLM hallucinated section_id {match_id!r} — discarding")
                match_id = None

        proposed = data.get("proposed_new_section")
        if proposed:
            proposed = str(proposed).strip()[:80]
            if not proposed:
                proposed = None

        summary = data.get("summary")
        if summary and isinstance(summary, str):
            summary = summary.strip()[:300]
        else:
            summary = None

        topic_tags = data.get("topic_tags") or []
        if isinstance(topic_tags, list):
            topic_tags = [str(t).strip().lower() for t in topic_tags if t][:4]
        else:
            topic_tags = []

        return {
            "kind": kind,
            "kind_confidence": float(data.get("kind_confidence", 0.5)),
            "summary": summary if is_content else None,
            "topic_tags": topic_tags if is_content else [],
            "match_existing_id": match_id if is_content else None,
            "proposed_new_section": proposed if is_content else None,
            "section_confidence": float(data.get("section_confidence", 0.0)) if is_content else 0.0,
        }
    except Exception as e:
        logger.debug(f"[batch_analyze_article] failed (non-fatal): {e}")
        return {
            "kind": "content",
            "kind_confidence": 0.0,
            "summary": None,
            "topic_tags": [],
            "match_existing_id": None,
            "proposed_new_section": None,
            "section_confidence": 0.0,
        }
