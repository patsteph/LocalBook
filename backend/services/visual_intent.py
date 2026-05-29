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


# ──────────────────────────────────────────────────────────────────────
# User-directed SVG detector — sits BEFORE the LLM classifier
#
# When the user has fully specified an SVG (medium + palette + geometry),
# the right move is to render the SVG from their spec, not classify and
# route to either Klein (raster, wrong medium) or the skeleton picker
# (fixed templates, can't honor custom geometry).
#
# Cheap heuristic check — no LLM call. Returns True only when the prompt
# is unambiguously asking the model to write a custom SVG.
# ──────────────────────────────────────────────────────────────────────
_HEX_COLOR_RE = re.compile(r"#[0-9a-fA-F]{6}\b")
_SVG_MEDIUM_RE = re.compile(
    r"\b(?:generate|create|produce|render|draw|make)\s+(?:a\s+|an\s+)?(?:clean\s+|minimalist\s+|simple\s+)?svg\b",
    re.IGNORECASE,
)
_GEOMETRY_RE = re.compile(
    r"\b(viewbox|viewBox|circle|rectangle|polygon|polyline|stroke|fill|path|opacity|gradient)\b",
    re.IGNORECASE,
)


def is_user_directed_svg(prompt: str) -> bool:
    """True when the prompt is a self-contained SVG specification.

    Requires THREE signals (all cheap regex checks):
      1. An "SVG" medium opener — "Generate a clean SVG..." style.
      2. At least 2 explicit hex colors (#RRGGBB) — proves the user has
         already done the palette work.
      3. At least 2 geometry/SVG-attribute mentions (viewBox, circle,
         stroke, gradient, opacity, etc.) — proves the user is thinking
         in SVG primitives, not abstractly.

    Misses by design: prompts that ask for a "diagram" without saying
    "SVG", prompts with named colors but no hex, prompts with hex but
    no geometry vocabulary. Those still go through the classifier.
    """
    if not prompt:
        return False
    head = prompt[:300]  # SVG opener must be near the start
    if not _SVG_MEDIUM_RE.search(head):
        return False
    if len(_HEX_COLOR_RE.findall(prompt)) < 2:
        return False
    if len(_GEOMETRY_RE.findall(prompt)) < 2:
        return False
    return True


# ──────────────────────────────────────────────────────────────────────
# System prompt for the user-directed SVG renderer (Fix B).
# Kept here next to is_user_directed_svg so the trigger and the prompt
# travel together. Caller (visual_composer) wires the LLM call.
# ──────────────────────────────────────────────────────────────────────
USER_DIRECTED_SVG_SYSTEM = """You are an SVG renderer. The user has written a complete specification for an SVG visual. Your job is to produce that exact SVG.

RULES:
1. Output a single, valid SVG document. Start with <svg ...> and end with </svg>. No markdown fences, no commentary, no preamble.
2. Honor every specified value: hex colors, viewBox dimensions, stroke weights, opacities, geometry, positions. Treat the user's spec as a contract.
3. When a position is described qualitatively ("loose orbital pattern", "balanced composition"), pick coordinates that honor the description AND distribute elements evenly within the viewBox.
4. Include the viewBox attribute the user named (e.g., viewBox="0 0 1200 900"). Default to width="100%" height="100%" so the SVG scales.
5. When the user says "no labels" / "no text", do not add any <text> elements. When they say "subtle", "minimal", or "no gradients", obey.
6. Add a generous internal margin (≥ 6% of the viewBox dimension on each side) so the composition has breathing room — unless the user specifies otherwise.
7. If the user does not specify a background, omit it (transparent). If they specify one, use a <rect> covering the viewBox with the named color.

OUTPUT FORMAT — ONLY the SVG, exactly like:
<svg viewBox="0 0 1200 900" xmlns="http://www.w3.org/2000/svg" width="100%" height="100%">
  <!-- elements honoring the user's spec -->
</svg>"""

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

DISPOSITIVE RULE — medium-naming opener:
When the FIRST CLAUSE of the prompt explicitly names a visual medium ("a cinematic photograph of...", "an editorial photograph of...", "an oil painting of...", "a watercolor illustration of...", "a render of...", "a hero image of...", "a poster of..."), the answer is ILLUSTRATION with confidence ≥ 0.85. Abstract, emotional, or thematic vocabulary appearing LATER in the same prompt does NOT override this. "Evoking the quiet weight of leadership", "earned solitude", "the future of X", "a sense of stewardship" are FRAMING — they describe what the depicted scene means, not what kind of artifact the user wants. If the prompt also describes a concrete scene (figure, landscape, lighting, palette, camera), the illustration verdict is locked.

ANTI-PATTERN — abstract framing of a concrete scene:
A prompt that opens by naming a medium and then describes a concrete scene at length (figure + setting + lighting + palette + mood) is an ILLUSTRATION even when it ALSO uses business-coded or philosophical vocabulary ("leadership", "stewardship", "transformation", "the weight of", "evoking"). Do NOT downgrade to STRUCTURAL DIAGRAM just because the scene's symbolic meaning is abstract — the user is asking for a picture that captures that meaning, not a slide that lists it.

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
The question is: would re-writing this prompt produce something BETTER than what the user already wrote? If the user has already done the art direction, passthrough is TRUE — you should NOT have Gemma rewrite their work.

TRUE when ANY TWO of these signals are present in the prompt:
  (a) explicit color palette — named colors, hex codes, or a color scheme described in concrete terms ("cream and walnut", "teal/amber/magenta accents")
  (b) named style or medium — "flat vector", "isometric", "watercolor", "photography", "low-poly", "studio ghibli", "editorial illustration"
  (c) mood/feel adjectives — "cinematic", "calm", "didactic", "moody", "playful", "ominous"
  (d) lighting or composition cues — "golden hour", "shallow depth of field", "wide shot", "rim light", "shot from above"
  (e) named fonts or typography — "Inter font", "serif typography", "clean sans-serif"
  (f) length >150 words of mostly descriptive prose
  (g) explicit references to art styles or eras — "art deco", "Studio Ghibli", "Wes Anderson framing", "engineering handbook"

CRITICAL: structural callouts embedded inside an otherwise art-directed prompt DO NOT disqualify passthrough. A prompt like "A flat vector illustration of [Stage 1/Stage 2/Stage 3], cream palette, Inter font, didactic mood" is PASSTHROUGH because the art direction is rich — the structural callouts are just describing what's IN the illustration.

FALSE only when:
  - the prompt is sparse ("a picture of a dog")
  - the prompt is purely structural data with zero aesthetic cues ("five stages: ingest, chunk, store, retrieve, answer")
  - fewer than two of the above signals are present

EXAMPLES:
  - "A picture of a dog" → FALSE (zero signals)
  - "A cinematic shot of a dog" → FALSE (one weak signal)
  - "A cinematic golden-hour photo of a corgi on a beach, shallow depth of field, warm pastel palette, Wes Anderson framing" → TRUE (palette + style + mood + composition + lighting + reference — strong art direction)
  - "Show me a five-stage RAG pipeline: ingest → chunk → embed → retrieve → generate" → FALSE (purely structural, no aesthetic cues)
  - "A clean flat vector illustration of a three-stage RAG pipeline on a cream background, with teal/amber/magenta accents for the three flows, Inter font, calm didactic mood, like an engineering handbook" → TRUE (palette + style + mood + font + reference — five signals)

ILLUSTRATION-VS-DIAGRAM EXAMPLES (the binary classification — separate from passthrough above):
  - "A cinematic editorial photograph evoking the quiet weight of leadership. A solitary figure stands at the edge of a weathered stone overlook... amber and honey-gold light, weathered umber palette, medium format depth" → ILLUSTRATION conf=0.95 (medium named in first clause + concrete scene + palette + lighting; the "weight of leadership" framing is symbolic meaning of the scene, not a request for a business slide)
  - "An oil painting of the future of work — figures crossing a bridge into mist, warm afternoon light, muted earth palette" → ILLUSTRATION conf=0.95 (medium-naming opener locks the verdict; "future of work" is the painting's subject, not a value-prop slide cue)
  - "The future of cloud is serverless: ship faster, lower cost, infinite scale" → STRUCTURAL conf=0.9 (no medium named, no scene, three bullet-style benefits — this IS a value-prop slide)
  - "A hero image of leadership: someone leading a team" → ILLUSTRATION conf=0.7 (medium named, scene sparse — weaker signal but still illustration)

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
