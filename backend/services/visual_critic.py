"""Visual System v2 — vision-based critic with adaptive retry.

Renders the freeform SVG output to a PNG, feeds it to a multimodal model
(Gemma for Setup B, granite-vision for Setup A), and scores it on 5 axes
matching the spike-validated approach:

  legibility • hierarchy • balance • color_harmony • message_clarity

When the overall score is below threshold (default 0.70), emits a diff
hint — strengths + weaknesses + suggestions verbatim — that the freeform
generator consumes for a single retry. Max 1 retry per visual.

Critic latency from spike: ~14s per visual on Gemma. Negligible vs the
~2-3 minute generation cost.
"""
from __future__ import annotations

import base64
import json
import logging
import re
import time
from dataclasses import dataclass
from typing import Optional

from services.ollama_service import ollama_service
from services.visual_capability import VisualCapability, get_capability

logger = logging.getLogger(__name__)


DEFAULT_THRESHOLD = 0.70
# Granite vision is less capable than Gemma vision; expect noisier scores
# and lower absolute values. Threshold scales to model family in
# threshold_for_model() so retry logic still does the right thing on Setup A.
GRANITE_THRESHOLD = 0.55
CRITIC_NUM_PREDICT = 1500
CRITIC_TIMEOUT = 180.0


def threshold_for_model(critic_model: Optional[str]) -> float:
    """Return the adaptive critic threshold for the model family in use."""
    if not critic_model:
        return DEFAULT_THRESHOLD
    name = critic_model.lower()
    if name.startswith("granite"):
        return GRANITE_THRESHOLD
    return DEFAULT_THRESHOLD


CRITIC_SYSTEM = """You are a senior information designer reviewing a visual for an enterprise customer presentation. You are the LAST LINE OF DEFENSE before the user ships this. Inflated scores destroy trust — the user is relying on you to be harsh so they know when to retry.

YOUR PROCESS — follow these steps in order, do not skip:

STEP 1 — EXAMINE
Look carefully at the image. Read every visible text character. Compare every element to the user's specification provided in the user message.

STEP 2 — FILL OUT THE CHECKLIST
For each item below, decide PASS (true) or FAIL (false) AND write one short sentence of evidence. Be ruthless. "Looks fine" is not evidence; "the word 'Lacenab' is misspelled — should be LanceDB" is evidence.

  [text_legible]   — EVERY visible text character forms a correctly spelled English word? Even ONE misspelled / partial / nonsense word ("Lacenab", "Embd_r", "Vctor") is FAIL. If the image contains NO text at all, this is PASS.
  [subject_match]  — Does the rendered image clearly show the SUBJECT the user asked for? Wrong object, missing key element, or wrong setup is FAIL. ("Mac Mini WITH a walnut top piece" instead of "Mac Mini ON a walnut desk" is FAIL.)
  [palette_match]  — If the user named specific palette colors, are those colors visibly present? If no palette was specified, PASS.
  [style_match]    — If the user named a style/medium (cinematic, isometric, flat vector, watercolor, photographic, etc.), does the image clearly exhibit it? If no style was specified, PASS.
  [mood_match]     — If the user described a mood/feel (calm, didactic, ominous, warm, cinematic, etc.), is it conveyed? If no mood was specified, PASS.
  [no_artifacts]   — Free of broken geometry, extra/missing limbs, duplicated elements, melted shapes, distorted faces, or visible diffusion artifacts? PASS only if clean.
  [composition]    — Is the composition intentional and balanced? Subject framed sensibly, no awkward cropping, focal point clear? PASS only if intentional-looking.

STEP 3 — DERIVE THE SCORE FROM THE CHECKLIST
The overall score is determined by the checklist. You MAY NOT contradict it.

  All 7 PASS                          → overall in range 0.85 – 0.95
  Exactly 1 FAIL                      → overall in range 0.65 – 0.74
  Exactly 2 FAIL                      → overall in range 0.45 – 0.59
  3 or more FAIL                      → overall in range 0.25 – 0.44

HARD CEILINGS (apply AFTER the range above, take the lower):
  If [text_legible] is FAIL          → overall cannot exceed 0.50
  If [subject_match] is FAIL         → overall cannot exceed 0.55
  If [no_artifacts] is FAIL          → overall cannot exceed 0.45

If you find yourself wanting to score 0.90+ but the checklist shows ANY failures, RE-READ the checklist — you are wrong, lower the score to the rule-permitted range.

STEP 4 — RETURN JSON

Per-axis sub-scores (each 0.0-1.0):
  legibility — 0.0 if [text_legible] FAILS, else 0.8+ if text is crisp / no text present
  hierarchy — title/focal point clear? eye knows where to land first?
  balance — whitespace intentional, composition stable, not cramped
  color_harmony — palette coherent; if named colors specified, they're present
  message_clarity — visual matches the user's stated intent at a glance

Return ONLY valid JSON matching exactly this schema (no extra keys, no missing keys):
{
  "checklist": {
    "text_legible":   {"pass": true, "evidence": "..."},
    "subject_match":  {"pass": true, "evidence": "..."},
    "palette_match":  {"pass": true, "evidence": "..."},
    "style_match":    {"pass": true, "evidence": "..."},
    "mood_match":     {"pass": true, "evidence": "..."},
    "no_artifacts":   {"pass": true, "evidence": "..."},
    "composition":    {"pass": true, "evidence": "..."}
  },
  "legibility": 0.0,
  "hierarchy": 0.0,
  "balance": 0.0,
  "color_harmony": 0.0,
  "message_clarity": 0.0,
  "overall": 0.0,
  "strengths": ["specific thing 1", "specific thing 2"],
  "weaknesses": ["specific blocker 1", "specific blocker 2"],
  "suggestions": ["concrete fix 1", "concrete fix 2"]
}"""


# ──────────────────────────────────────────────────────────────────────
# Result type
# ──────────────────────────────────────────────────────────────────────
@dataclass
class CritiqueResult:
    success: bool
    legibility: float = 0.0
    hierarchy: float = 0.0
    balance: float = 0.0
    color_harmony: float = 0.0
    message_clarity: float = 0.0
    overall: float = 0.0
    strengths: list[str] = None
    weaknesses: list[str] = None
    suggestions: list[str] = None
    elapsed_ms: int = 0
    critic_model: Optional[str] = None
    error: Optional[str] = None

    def __post_init__(self):
        if self.strengths is None:
            self.strengths = []
        if self.weaknesses is None:
            self.weaknesses = []
        if self.suggestions is None:
            self.suggestions = []

    def passed(self, threshold: float = DEFAULT_THRESHOLD) -> bool:
        return self.success and self.overall >= threshold

    def diff_hint(self) -> str:
        """Format weaknesses + suggestions for inclusion in a regen prompt."""
        parts = ["The previous version of this visual scored below threshold. "
                 "Fix these specific issues in the next attempt:\n"]
        if self.weaknesses:
            parts.append("WEAKNESSES TO FIX:")
            parts.extend(f"- {w}" for w in self.weaknesses)
            parts.append("")
        if self.suggestions:
            parts.append("CONCRETE CHANGES TO MAKE:")
            parts.extend(f"- {s}" for s in self.suggestions)
        return "\n".join(parts)


# ──────────────────────────────────────────────────────────────────────
# Critic
# ──────────────────────────────────────────────────────────────────────
class VisualCritic:
    """Vision-model critic that scores a rendered visual."""

    def __init__(self, threshold: float = DEFAULT_THRESHOLD):
        self.threshold = threshold

    def _pick_critic_model(self, capability: VisualCapability) -> Optional[str]:
        """Setup B prefers Gemma itself (already multimodal); Setup A uses the
        configured vision model."""
        if capability.can_critic_gemma_vision and capability.gemma_model:
            return capability.gemma_model
        if capability.can_critic_separate_vision and capability.vision_model:
            return capability.vision_model
        return None

    async def critique(
        self,
        png_bytes: bytes,
        visual_title: str,
        visual_intent: str = "",
        capability: Optional[VisualCapability] = None,
    ) -> CritiqueResult:
        """Score a rendered visual. Returns CritiqueResult with axes + critique."""
        t0 = time.time()
        cap = capability or await get_capability()
        critic_model = self._pick_critic_model(cap)
        if not critic_model:
            return CritiqueResult(
                success=False,
                error="no critic model available (need Gemma or a vision model)",
                elapsed_ms=int((time.time() - t0) * 1000),
            )

        b64 = base64.b64encode(png_bytes).decode("ascii")
        spec_block = (
            f"USER'S SPECIFICATION (the rendered image must match this):\n"
            f"{visual_intent}\n\n"
            if visual_intent else ""
        )
        prompt = (
            f"{spec_block}"
            f"VISUAL TITLE (metadata only — not necessarily visible in the image): "
            f"{visual_title}\n\n"
            f"Now follow the 4-step process from the system prompt: examine, "
            f"checklist, derive score, return JSON. Do not skip the checklist. "
            f"The score MUST follow the rule based on PASS/FAIL counts."
        )

        logger.info(f"[visual_critic] critic_model={critic_model}")
        from services.ollama_service import PRIORITY_FOREGROUND
        result = await ollama_service.generate(
            prompt=prompt,
            system=CRITIC_SYSTEM,
            model=critic_model,
            temperature=0.2,
            num_predict=CRITIC_NUM_PREDICT,
            timeout=CRITIC_TIMEOUT,
            images=[b64],
            format="json",
            voice_modifier=False,
            priority=PRIORITY_FOREGROUND,  # final step of user-initiated image gen
        )
        raw = result.get("response", "")
        parsed = _repair_json(raw)
        elapsed_ms = int((time.time() - t0) * 1000)

        if not parsed:
            return CritiqueResult(
                success=False,
                critic_model=critic_model,
                elapsed_ms=elapsed_ms,
                error="critic JSON parse failed",
            )

        # Detect the all-zeros silent-fail: when the LLM returned malformed
        # JSON that parsed as empty (or partial without numeric axes), each
        # axis clamps to 0.0. An all-zero score is meaningless — treat it
        # as a critic failure rather than displaying 0.00 to the user.
        axes = [
            _clamp(parsed.get("legibility")),
            _clamp(parsed.get("hierarchy")),
            _clamp(parsed.get("balance")),
            _clamp(parsed.get("color_harmony")),
            _clamp(parsed.get("message_clarity")),
        ]
        overall = _clamp(parsed.get("overall"))
        if overall == 0.0 and all(a == 0.0 for a in axes):
            return CritiqueResult(
                success=False,
                critic_model=critic_model,
                elapsed_ms=elapsed_ms,
                error="critic returned all-zero scores (likely JSON parse partial-fail)",
            )

        return CritiqueResult(
            success=True,
            legibility=axes[0],
            hierarchy=axes[1],
            balance=axes[2],
            color_harmony=axes[3],
            message_clarity=axes[4],
            overall=overall,
            strengths=list(parsed.get("strengths") or []),
            weaknesses=list(parsed.get("weaknesses") or []),
            suggestions=list(parsed.get("suggestions") or []),
            critic_model=critic_model,
            elapsed_ms=elapsed_ms,
        )


# ──────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────
_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```\s*$", re.IGNORECASE)


def _repair_json(raw: str) -> Optional[dict]:
    """Strip code fences, then try strict parse, then outer-brace scrape."""
    if not raw:
        return None
    cleaned = _FENCE_RE.sub("", raw.strip())
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass
    m = re.search(r"\{[\s\S]*\}", cleaned)
    if m:
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            return None
    return None


def _clamp(v) -> float:
    """Coerce to float and clamp to [0, 1]; missing → 0.0."""
    if v is None:
        return 0.0
    try:
        f = float(v)
    except (TypeError, ValueError):
        return 0.0
    if f < 0:
        return 0.0
    if f > 1:
        return 1.0
    return f


# ──────────────────────────────────────────────────────────────────────
# Module-level singleton
# ──────────────────────────────────────────────────────────────────────
visual_critic = VisualCritic()
