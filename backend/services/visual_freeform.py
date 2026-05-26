"""Visual System v2 — SkeletonGenerator (primary path for both setups).

Single generator class. Picks an idiom via the two-stage picker in
visual_idioms, then fills a pre-built skeleton (visual_skeletons) using
slot-fill prompts from visual_slotfill. Structural quality is guaranteed
by the skeleton; the model only handles content (idiom pick + slot text).

The legacy GemmaFreeformGenerator was removed in the 2026-05-26
consolidation — benchmarks showed skeletons beat freeform on every test
prompt AND ran 3-5× faster. When skeletons fail, the composer falls
through directly to the 42-template Mermaid path (Tier D safety net).

Module-level singletons exposed to the composer:
  olmo_skeleton, gemma_skeleton — SkeletonGenerator instances per family
  olmo_freeform                  — backward-compat alias for olmo_skeleton

Idiom catalog, picker prompts, and slot-fill prompts live in companion
modules (visual_idioms.py, visual_slotfill.py) — file split per the
CLAUDE.md 800-line budget rule.
"""
from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass
from typing import Optional

from services.ollama_service import ollama_service
from services.visual_capability import VisualCapability, get_capability
from services.visual_idioms import (
    CATEGORIES,
    OLMO_IDIOMS,
    pick_category_and_meta,
    pick_idiom_in_category,
)
from services.visual_skeletons import get_skeleton
from services.visual_slotfill import (
    _apply_slot_fill,
    _has_unfilled_slots,
    _olmo_slotfill_system,
    validate_key_slots,
)

logger = logging.getLogger(__name__)

# 16:9 slide-friendly viewBox by default
SLIDE_W = 1600
SLIDE_H = 900

WARM_TIMEOUT = 300.0


# ──────────────────────────────────────────────────────────────────────
# Result type
# ──────────────────────────────────────────────────────────────────────
@dataclass
class FreeformResult:
    success: bool
    svg_markup: Optional[str] = None
    title: str = ""
    subtitle: str = ""
    description: str = ""
    idiom_id: Optional[str] = None
    plan: Optional[dict] = None
    truncated: bool = False
    model_used: Optional[str] = None
    elapsed_ms: int = 0
    error: Optional[str] = None


# ──────────────────────────────────────────────────────────────────────
# SkeletonGenerator — primary path for both Olmo (Setup A) and Gemma (Setup B)
# ──────────────────────────────────────────────────────────────────────
class SkeletonGenerator:
    """Generic skeleton-based SVG generator.

    Two-stage picker → skeleton lookup → slot-fill → apply. Same code
    works for any model that can produce structured JSON. Benchmark-
    validated as the best-quality + fastest path for both Olmo (~50s
    avg) and Gemma (~40s avg).

    Two singletons below (olmo_skeleton + gemma_skeleton) wrap this
    class with different model resolvers so the composer routes per
    setup.
    """

    def __init__(self, family: str):
        # family is "olmo" or "gemma"
        self._family = family
        # Gemma's channel-token output format eats tokens fast — bumped
        # per family. cqrs_pattern (30+ slots) needs the larger budget.
        self._slotfill_num_predict = 12000 if family == "gemma" else 3000
        self._pick_num_predict = 1500 if family == "gemma" else 500

    def _model_for(self, cap: VisualCapability) -> Optional[str]:
        if self._family == "gemma":
            return cap.gemma_model
        if self._family == "olmo":
            return cap.olmo_model
        return None

    async def generate(
        self,
        content: str,
        capability: Optional[VisualCapability] = None,
        force_idiom: Optional[str] = None,
    ) -> FreeformResult:
        cap = capability or await get_capability()
        model = self._model_for(cap)
        if not model:
            return FreeformResult(
                success=False,
                error=f"{self._family} model not installed; cannot run skeleton path",
            )

        t0 = time.time()

        if force_idiom:
            pick = {"idiom_id": force_idiom, "title": "", "subtitle": ""}
        else:
            pick = await self._run_pick(content, model)
            if not pick:
                return FreeformResult(
                    success=False,
                    model_used=model,
                    elapsed_ms=int((time.time() - t0) * 1000),
                    error=f"{self._family} idiom pick failed",
                )

        idiom_id = pick.get("idiom_id") or ""
        # Defensively normalize: models sometimes return "idiom_id: description"
        if idiom_id and idiom_id not in OLMO_IDIOMS:
            for sep in (":", " ", "(", ","):
                if sep in idiom_id:
                    candidate = idiom_id.split(sep, 1)[0].strip()
                    if candidate in OLMO_IDIOMS:
                        logger.info(
                            f"[visual_freeform] normalized {idiom_id!r} → {candidate!r}"
                        )
                        idiom_id = candidate
                        break
        if idiom_id not in OLMO_IDIOMS:
            logger.warning(
                f"[visual_freeform] {self._family} picked unknown idiom "
                f"{idiom_id!r}; defaulting to linear_process"
            )
            idiom_id = "linear_process"

        skeleton = get_skeleton(idiom_id)
        if not skeleton:
            return FreeformResult(
                success=False,
                model_used=model,
                elapsed_ms=int((time.time() - t0) * 1000),
                error=f"no skeleton for idiom {idiom_id}",
            )

        seed_slots = {
            "TITLE": pick.get("title") or "Untitled Visual",
            "SUBTITLE": pick.get("subtitle") or "",
        }

        slots = await self._run_slotfill(content, idiom_id, model, seed_slots)
        if not slots:
            return FreeformResult(
                success=False,
                model_used=model,
                idiom_id=idiom_id,
                plan=pick,
                elapsed_ms=int((time.time() - t0) * 1000),
                error=f"{self._family} slot fill failed",
            )

        # Detect mostly-empty slot fills (the "junk input → empty visual" case).
        # If the slot-fill model returned >40% empty/whitespace values, the input
        # was likely too vague to generate from. Treat as a failure so the
        # composer falls back to the template path instead of shipping a
        # visual full of empty boxes.
        non_seed_slots = {k: v for k, v in slots.items() if k not in seed_slots}
        if non_seed_slots:
            empty_count = sum(
                1 for v in non_seed_slots.values()
                if not isinstance(v, str) or not v.strip()
            )
            empty_ratio = empty_count / len(non_seed_slots)
            if empty_ratio > 0.4:
                logger.warning(
                    f"[visual_freeform] {self._family} slot-fill produced "
                    f"{empty_ratio:.0%} empty values ({empty_count}/{len(non_seed_slots)}); "
                    f"likely garbage input — failing to template fallback"
                )
                return FreeformResult(
                    success=False,
                    model_used=model,
                    idiom_id=idiom_id,
                    plan=pick,
                    elapsed_ms=int((time.time() - t0) * 1000),
                    error=(
                        f"slot-fill returned mostly empty values "
                        f"({empty_count}/{len(non_seed_slots)} slots blank) — "
                        f"input was likely too vague"
                    ),
                )

            # Per-idiom KEY-SLOT validation. Catches the case where the
            # model returned content for SOME slots (overall ratio OK) but
            # blanked the structurally critical ones (e.g., all stage labels
            # missing from linear_process). The visual would render as
            # decorative shells with no information.
            ok, reason = validate_key_slots(idiom_id, slots)
            if not ok:
                logger.warning(f"[visual_freeform] {self._family} {reason}")
                return FreeformResult(
                    success=False,
                    model_used=model,
                    idiom_id=idiom_id,
                    plan=pick,
                    elapsed_ms=int((time.time() - t0) * 1000),
                    error=reason,
                )

        merged = {**slots, **{k: v for k, v in seed_slots.items() if v}}
        svg = _apply_slot_fill(skeleton, merged)
        unfilled = _has_unfilled_slots(svg)

        if unfilled:
            svg = re.sub(r"\{\{[A-Z0-9_]+\}\}", "", svg)
            logger.warning(
                f"[visual_freeform] {self._family} slot-fill left unfilled "
                f"placeholders; stripped them. {self._family} returned {len(slots)} keys."
            )

        return FreeformResult(
            success=True,
            svg_markup=svg,
            title=merged.get("TITLE", "Untitled Visual"),
            subtitle=merged.get("SUBTITLE", ""),
            description=f"{self._family} scaffolded {idiom_id}",
            idiom_id=idiom_id,
            plan={"pick": pick, "slot_count": len(slots), "unfilled": unfilled},
            truncated=False,
            model_used=model,
            elapsed_ms=int((time.time() - t0) * 1000),
        )

    async def _run_pick(self, content: str, model: str) -> Optional[dict]:
        """Two-stage pick: category → idiom within category."""
        stage1 = await pick_category_and_meta(content, model, self._pick_num_predict)
        if not stage1 or not stage1.get("category"):
            logger.warning(f"[visual_freeform] {self._family} stage-1 (category) failed")
            return None

        category = stage1["category"].strip().upper()
        if category not in CATEGORIES:
            for known in CATEGORIES.keys():
                if known.startswith(category[:4]) or category.startswith(known[:4]):
                    category = known
                    break
            else:
                logger.warning(
                    f"[visual_freeform] {self._family} stage-1 returned unknown "
                    f"category {category!r}; defaulting to PROCESS"
                )
                category = "PROCESS"

        cap = await get_capability()
        allow_hero_klein = (self._family == "gemma") and cap.can_diffusion_klein

        idiom_id = await pick_idiom_in_category(
            content, category, model, self._pick_num_predict,
            allow_hero_klein=allow_hero_klein,
        )
        if not idiom_id:
            options = CATEGORIES[category]["idioms"]
            if not allow_hero_klein:
                options = [i for i in options if i != "hero_with_callouts"]
            idiom_id = options[0] if options else "linear_process"
            logger.warning(
                f"[visual_freeform] {self._family} stage-2 (idiom) failed; "
                f"defaulting to {idiom_id}"
            )

        return {
            "idiom_id": idiom_id,
            "title": stage1.get("title", ""),
            "subtitle": stage1.get("subtitle", ""),
            "category": category,
        }

    async def _run_slotfill(self, content, idiom_id, model, seed) -> Optional[dict]:
        logger.info(f"[visual_freeform] {self._family} pass 2 (slotfill) idiom={idiom_id}")
        system = _olmo_slotfill_system(idiom_id)
        result = await ollama_service.generate(
            prompt=(
                f"SOURCE CONTENT:\n{content}\n\n"
                f"Already populated (do NOT override):\n"
                f"  TITLE: {seed.get('TITLE', '')}\n"
                f"  SUBTITLE: {seed.get('SUBTITLE', '')}\n\n"
                f"Fill in the remaining slots based on the source content. "
                f"Return JSON only with all keys from the schema."
            ),
            system=system,
            model=model,
            temperature=0.2,
            num_predict=self._slotfill_num_predict,
            timeout=WARM_TIMEOUT,
            format="json",
            voice_modifier=False,
        )
        raw = result.get("response", "")
        if not raw:
            return None
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


# ──────────────────────────────────────────────────────────────────────
# Module-level singletons + backward-compat aliases
# ──────────────────────────────────────────────────────────────────────
olmo_skeleton = SkeletonGenerator(family="olmo")
gemma_skeleton = SkeletonGenerator(family="gemma")

# Backward-compat aliases (composer + tests still use these names)
OlmoFreeformGenerator = SkeletonGenerator
olmo_freeform = olmo_skeleton
