"""article_sectioner — Phase 14.D (2026-06-10).

Classify a content article into the best-fit notebook section, OR
propose a new section if none of the existing ones fit. Single
phi4-mini JSON call per article (cheap; ~0.5s).

Auto-create threshold: 0.85. Below that, the candidate name lands in
`articles.section_proposal` and `section_id` stays NULL — the user can
later approve. Above, the section is auto-created (idempotent on name)
and the article is assigned.

Design call (2026-06-10, see READFIRST/planning/article-depth-phase-14.md):
- phi4-mini, not gemma4 — categorization is a narrow task; fast model is
  plenty and avoids burning the main model on per-article overhead.
- Compact prompt: existing section names + new article title/summary.
- Conservative defaults — high threshold + idempotent create-on-name
  prevent section sprawl from a flaky LLM run.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

AUTO_CREATE_THRESHOLD = 0.85
MIN_REASONABLE_CONFIDENCE = 0.40  # below this, leave section blank


_SECTIONER_SYSTEM = """You assign an article to a subject section within a notebook.

Given:
- A list of EXISTING sections in this notebook (with article counts)
- A new article's title and summary

Pick ONE of:
- An existing section by id, if the article clearly fits
- A NEW section name, if the article is about a distinctly different subject

Return ONLY a JSON object:
{
  "match_existing_id": "<id-from-list-OR-null>",
  "proposed_new_section": "<short noun phrase, 2-5 words, OR null>",
  "confidence": 0.0-1.0,
  "reason": "<one short sentence>"
}

Rules:
- Prefer existing sections when reasonable. Don't propose a new section unless the topic is genuinely orthogonal.
- New section names are short noun phrases ("AI Accounting", "Crypto Regulation", "Frontier Models"). NOT sentences.
- Either match_existing_id OR proposed_new_section is set, not both. If you can't decide, set match_existing_id=null, proposed_new_section=null, confidence=0.0.
"""


def _format_sections_for_prompt(sections: List[Dict[str, Any]]) -> str:
    if not sections:
        return "(no existing sections — propose a new one)"
    lines = []
    for s in sections[:20]:  # cap at 20 for prompt budget
        name = (s.get("name") or "").strip()
        count = int(s.get("article_count") or 0)
        sid = s.get("id") or ""
        lines.append(f'  - id={sid} name="{name}" article_count={count}')
    return "\n".join(lines)


async def classify_section(
    *,
    notebook_id: str,
    title: str,
    summary: str,
) -> Dict[str, Any]:
    """Return: {match_existing_id, proposed_new_section, confidence, reason}.
    Defaults to all-None on failure (caller persists no section)."""
    from services.ollama_service import ollama_service
    from storage.article_section_store import article_section_store
    from config import settings

    existing = await article_section_store.list_for_notebook(notebook_id)
    text = (summary or "").strip() or (title or "").strip()
    if not text or len(text) < 12:
        return {"match_existing_id": None, "proposed_new_section": None,
                "confidence": 0.0, "reason": "too little article text"}

    user_prompt = (
        f"EXISTING SECTIONS:\n{_format_sections_for_prompt(existing)}\n\n"
        f"NEW ARTICLE:\nTITLE: {title or '(no title)'}\nSUMMARY: {text[:400]}"
    )
    try:
        result = await ollama_service.generate(
            prompt=user_prompt,
            system=_SECTIONER_SYSTEM,
            model=settings.ollama_fast_model,
            temperature=0.1,
            num_predict=150,
            format="json",
        )
        raw = (result or {}).get("response", "").strip()
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise ValueError("non-dict response")
        match_id = data.get("match_existing_id")
        new_name = data.get("proposed_new_section")
        # Validate match_id is actually in our list (LLM can hallucinate)
        if match_id is not None:
            valid_ids = {s["id"] for s in existing}
            if str(match_id) not in valid_ids:
                logger.debug(f"[sectioner] LLM returned unknown section_id {match_id}; downgrading to proposal")
                match_id = None
                if not new_name:
                    new_name = None  # nothing actionable
        return {
            "match_existing_id": match_id if match_id else None,
            "proposed_new_section": (str(new_name).strip()[:80] if new_name else None),
            "confidence": float(data.get("confidence", 0.0)),
            "reason": str(data.get("reason", "")).strip()[:200],
        }
    except Exception as e:
        logger.debug(f"[article_sectioner] failed (non-fatal): {e}")
        return {"match_existing_id": None, "proposed_new_section": None,
                "confidence": 0.0, "reason": "sectioner failed"}


async def assign_section(
    *,
    article_id: str,
    notebook_id: str,
    title: str,
    summary: str,
) -> Optional[Dict[str, Any]]:
    """End-to-end: classify, then either assign to existing section or
    auto-create new section (≥ AUTO_CREATE_THRESHOLD confidence) or store
    the proposal text for later review.

    Returns the section dict when assigned, or None when only proposal
    was stored.
    """
    from storage.article_section_store import article_section_store
    from storage.article_store import article_store

    verdict = await classify_section(
        notebook_id=notebook_id, title=title, summary=summary,
    )
    confidence = float(verdict.get("confidence") or 0.0)
    match_id = verdict.get("match_existing_id")
    proposal = verdict.get("proposed_new_section")

    # Path 1: existing-section match
    if match_id and confidence >= MIN_REASONABLE_CONFIDENCE:
        await article_store.update_section(
            article_id, section_id=match_id, confidence=confidence,
        )
        await article_section_store.increment_count(match_id)
        return {"section_id": match_id, "auto_assigned": True,
                "confidence": confidence, "via": "existing"}

    # Path 2: new-section auto-create (high confidence)
    if proposal and confidence >= AUTO_CREATE_THRESHOLD:
        new_id = await article_section_store.create(notebook_id, proposal)
        await article_store.update_section(
            article_id, section_id=new_id, confidence=confidence,
        )
        await article_section_store.increment_count(new_id)
        return {"section_id": new_id, "auto_assigned": True,
                "confidence": confidence, "via": "auto-created",
                "new_name": proposal}

    # Path 3: low-confidence proposal — store text only, leave section_id NULL
    if proposal:
        await article_store.update_section(
            article_id, section_id=None, proposal=proposal, confidence=confidence,
        )
    return None
