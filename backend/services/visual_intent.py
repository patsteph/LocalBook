"""Visual System v2 — illustration-intent pre-classifier.

Runs ONCE in the Gemma compose path, before the structural picker. Detects
when the user is asking for a cinematic / illustrated / hero image vs a
structural diagram. When intent is high, the composer bypasses the
two-stage idiom picker and routes directly to a Klein full-bleed path —
the same mechanism Gemini / Claude / ChatGPT use to decide "image tool"
vs "structured output."

Single Gemma JSON call. Returns illustration verdict + render hints
(aspect ratio, quality tier, passthrough recommendation) + extracted
title/subtitle so the composer doesn't need a second LLM call to label
the hero.

When the classifier fails or Gemma is unavailable, returns a "no
illustration intent" default so the existing skeleton pipeline runs
unchanged. Safety floor: this module is fully additive — its only
effect is to OPT IN to the new Klein-first path.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Optional

from services.ollama_service import ollama_service

logger = logging.getLogger(__name__)


VALID_ASPECTS = ("16:9", "4:3", "1:1", "9:16")
VALID_TIERS = ("draft", "hero")

# Confidence floor for routing to the Klein full-bleed path. Below this
# we fall through to the existing structural picker even if is_illustration
# came back True — defends against weak / hedged classifier outputs.
DEFAULT_CONFIDENCE_FLOOR = 0.6


@dataclass
class IllustrationIntent:
    """Verdict from the intent classifier.

    is_illustration + confidence drive the routing decision. The other
    fields are render hints used by the Klein full-bleed path:
      • aspect_ratio  — picks Klein width/height
      • quality_tier  — picks Klein step count + max resolution
      • passthrough_recommended — skip Gemma prompt-rewrite, use the
        user's prompt directly (best for art-directed prompts)
      • style_hint    — short style summary, used when not in passthrough
        mode to bias write_klein_prompt
      • title/subtitle — extracted hero text so the composer doesn't
        need a follow-up LLM call to label the visual
    """
    is_illustration: bool
    confidence: float
    aspect_ratio: str
    quality_tier: str
    passthrough_recommended: bool
    style_hint: Optional[str]
    title: str
    subtitle: str
    reason: str

    def routes_to_klein(self, confidence_floor: float = DEFAULT_CONFIDENCE_FLOOR) -> bool:
        return self.is_illustration and self.confidence >= confidence_floor


DEFAULT_INTENT = IllustrationIntent(
    is_illustration=False,
    confidence=0.0,
    aspect_ratio="16:9",
    quality_tier="draft",
    passthrough_recommended=False,
    style_hint=None,
    title="",
    subtitle="",
    reason="classifier unavailable",
)


_INTENT_SYSTEM = """You are classifying a visual generation request. Decide whether the user is asking for:

A) An ILLUSTRATED IMAGE — a creative picture, hero image, cinematic render, scene, poster, photograph, painting, or illustrated graphic. The user uses art-direction vocabulary (cinematic, isometric, palette, lighting, mood, render, photo, scene, hero, illustration, photograph, painting, watercolor, low-poly, Studio Ghibli, golden hour, depth of field, etc.) or describes a visual SUBJECT to depict (a Mac Mini on a walnut desk, a rocket launching, a mountain landscape).

B) A STRUCTURAL DIAGRAM — a chart, flowchart, architecture diagram, comparison matrix, process flow, swimlane, timeline, etc. The user describes data, components, stages, or relationships to visualize.

Many prompts mix both — they describe a structural subject (a pipeline, an architecture) but request it AS an illustration ("a cinematic isometric illustration of a RAG pipeline"). When art-direction vocabulary is present OR the prompt reads like a scene description rather than a data description, classify as ILLUSTRATION.

ALSO DECIDE:

aspect_ratio:
- "16:9" — default wide hero, cinematic scenes, cover slides
- "4:3" — standard slide, technical illustrations
- "1:1" — square, icon-like, social media
- "9:16" — portrait, poster, vertical phone format
Look for cues: "poster" / "cover" → portrait or 16:9, "wide" / "cinematic" → 16:9, "square" / "icon" → 1:1, "banner" → 16:9.

quality_tier:
- "hero" — user clearly wants polished / cinematic / detailed output. Cues: cinematic, hyper-detailed, 4K, ultra, photorealistic, magazine, editorial. Use when the prompt is long and art-directed.
- "draft" — quicker illustrative spots. Default when prompt is short or casual.

passthrough_recommended:
- TRUE if the user's prompt is ALREADY a polished image-generation prompt: long (>80 words), includes palette + lighting + mood + style cues, reads like Midjourney/DALL-E art direction.
- FALSE if the prompt is sparse, generic ("a picture of X"), or mostly describes data/structure rather than visual aesthetic.

style_hint: 2-6 word style summary extracted from the prompt (e.g. "cinematic isometric warm", "flat editorial vector", "studio ghibli watercolor"). Omit (null) if no style cues are present.

title: 2-8 word concrete title for the visual. Pull from the prompt's subject. Never use generic phrases like "Visual" or "Image."
subtitle: 5-12 word context line. Empty string if not derivable.

Return ONLY JSON, no preamble:
{
  "is_illustration": bool,
  "confidence": 0.0-1.0,
  "aspect_ratio": "16:9" | "4:3" | "1:1" | "9:16",
  "quality_tier": "draft" | "hero",
  "passthrough_recommended": bool,
  "style_hint": "short style summary" or null,
  "title": "concrete title",
  "subtitle": "context line or empty",
  "reason": "one short sentence explaining the classification"
}"""


async def classify_intent(content: str, model: Optional[str]) -> IllustrationIntent:
    """Single Gemma JSON call. Returns DEFAULT_INTENT on any failure so
    the composer's fall-through to the existing skeleton path is safe.
    """
    if not model or not content or not content.strip():
        return DEFAULT_INTENT

    try:
        result = await ollama_service.generate(
            prompt=f"REQUEST:\n{content}\n\nClassify and return JSON only.",
            system=_INTENT_SYSTEM,
            model=model,
            temperature=0.1,
            num_predict=800,
            timeout=60.0,
            format="json",
            voice_modifier=False,
        )
    except Exception as e:
        logger.warning(f"[visual_intent] classifier call failed: {e}")
        return DEFAULT_INTENT

    raw = (result.get("response") or "").strip()
    if not raw:
        logger.info("[visual_intent] classifier returned empty response")
        return DEFAULT_INTENT

    parsed = _parse_json(raw)
    if parsed is None:
        logger.info(f"[visual_intent] classifier returned non-JSON: {raw[:120]}...")
        return DEFAULT_INTENT

    intent = _from_dict(parsed)
    logger.info(
        f"[visual_intent] is_illustration={intent.is_illustration} "
        f"conf={intent.confidence:.2f} aspect={intent.aspect_ratio} "
        f"tier={intent.quality_tier} passthrough={intent.passthrough_recommended} "
        f"style={intent.style_hint!r} reason={intent.reason!r}"
    )
    return intent


def _parse_json(raw: str) -> Optional[dict]:
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        m = re.search(r"\{[\s\S]*\}", raw)
        if not m:
            return None
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            return None


def _from_dict(d: dict) -> IllustrationIntent:
    aspect = str(d.get("aspect_ratio") or "16:9").strip()
    if aspect not in VALID_ASPECTS:
        aspect = "16:9"

    tier = str(d.get("quality_tier") or "draft").strip().lower()
    if tier not in VALID_TIERS:
        tier = "draft"

    try:
        confidence = float(d.get("confidence", 0.0))
    except (TypeError, ValueError):
        confidence = 0.0
    confidence = max(0.0, min(1.0, confidence))

    style_hint_raw = d.get("style_hint")
    if isinstance(style_hint_raw, str) and style_hint_raw.strip():
        style_hint = style_hint_raw.strip()
    else:
        style_hint = None

    title = str(d.get("title") or "").strip()
    subtitle = str(d.get("subtitle") or "").strip()

    return IllustrationIntent(
        is_illustration=bool(d.get("is_illustration", False)),
        confidence=confidence,
        aspect_ratio=aspect,
        quality_tier=tier,
        passthrough_recommended=bool(d.get("passthrough_recommended", False)),
        style_hint=style_hint,
        title=title,
        subtitle=subtitle,
        reason=str(d.get("reason") or "").strip(),
    )
