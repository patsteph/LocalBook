"""article_classifier — Phase 14.A (2026-06-10).

Single phi4-mini JSON call per article: classify as content / sponsor /
ad / jobs / navigation. Gates everything downstream (summary, embed,
RAG, entity extraction, sections, brain events). Non-content articles
stay in `article_store` for audit trail but bypass the intelligence
layer so the cortex isn't poisoned by sponsored slots and job listings.

Cheap (~0.5s/article on phi4-mini, ~7s for a 12-article TLDR). One LLM
call per article; cached by caller via the `kind` column persistence.

Design call (2026-06-10, see READFIRST/planning/article-depth-phase-14.md):
- phi4-mini, not gemma4 — binary-ish decision, fast model is plenty
- preserve non-content articles in DB — never silently drop
- return reason + confidence so the user can audit / override later
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, Literal

logger = logging.getLogger(__name__)

ArticleKind = Literal["content", "sponsor", "ad", "jobs", "navigation"]
VALID_KINDS = ("content", "sponsor", "ad", "jobs", "navigation")

_CLASSIFIER_SYSTEM = """You classify a single section of a newsletter into one of:

- content: a real article / story / analysis / commentary / link to news
- sponsor: clearly labeled paid placement ("Sponsor", "Promoted", "Brought to you by")
- ad: standalone advertisement (product pitch, "Try X today", banner-style)
- jobs: job listings, hiring posts, "We're hiring" sections
- navigation: header / footer / "View in browser" / unsubscribe links / table of contents — anything that's chrome, not content

Return ONLY a JSON object:
{
  "kind": "content" | "sponsor" | "ad" | "jobs" | "navigation",
  "reason": "<one short sentence>",
  "confidence": 0.0-1.0
}

Be strict: a section that mentions products in passing is still content. A section that's primarily SELLING something is sponsor or ad.
"""


async def classify_article(title: str, body_text: str) -> Dict[str, Any]:
    """Classify one article. Returns:
      {"kind": ArticleKind, "reason": str, "confidence": float}

    Defaults to {"kind": "content", "reason": "classifier failed", "confidence": 0.0}
    on any error — conservative bias toward including. Better to ingest a sponsor
    than to silently drop a real article.
    """
    from services.ollama_service import ollama_service
    from config import settings

    body = (body_text or "")[:2000]  # phi4-mini handles 2KB fine
    if not body.strip() or len(body) < 50:
        return {"kind": "navigation", "reason": "too short to be content", "confidence": 0.7}

    user_prompt = f"TITLE: {title or '(no title)'}\n\nBODY:\n{body}"
    try:
        result = await ollama_service.generate(
            prompt=user_prompt,
            system=_CLASSIFIER_SYSTEM,
            model=settings.ollama_fast_model,
            temperature=0.1,
            num_predict=120,
            format="json",
            # Grammar (MLX-only; Ollama ignores) — flat enum classification, the
            # ideal grammar fit. `kind` constrained to VALID_KINDS so an MLX model
            # can't invent a category; reason/confidence optional (parser defaults).
            json_schema={
                "type": "object",
                "properties": {
                    "kind": {"type": "string", "enum": list(VALID_KINDS)},
                    "reason": {"type": "string"},
                    "confidence": {"type": "number"},
                },
                "required": ["kind"],
                "additionalProperties": False,
            },
        )
        raw = (result or {}).get("response", "").strip()
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise ValueError(f"not a dict: {raw[:100]}")
        kind = str(data.get("kind", "content")).strip().lower()
        if kind not in VALID_KINDS:
            kind = "content"  # safe fallback
        return {
            "kind": kind,
            "reason": str(data.get("reason", "")).strip()[:200],
            "confidence": float(data.get("confidence", 0.5)),
        }
    except Exception as e:
        logger.debug(f"[article_classifier] failed (non-fatal, defaulting to content): {e}")
        return {"kind": "content", "reason": "classifier failed", "confidence": 0.0}


def is_content(kind: str) -> bool:
    """Single source of truth for "should the intelligence layer touch this?"
    Used by downstream passes (summary, embed, RAG, entity, sections,
    brain events) so they all agree on the gate."""
    return (kind or "content").strip().lower() == "content"
