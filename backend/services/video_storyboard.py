"""Video Storyboard Generator — LLM produces a JSON storyboard from notebook sources.

Generates a structured storyboard with scenes, narration text, and visual directives.
Each scene maps to a slide that will be rendered via Playwright and composited with
TTS narration audio via FFmpeg.

Production quality guidelines embedded in the LLM prompt ensure:
- Narration word counts that match the target video duration (~140 wpm)
- Deliberate pacing: fewer scenes with deeper content per slide
- Content quality validation: no empty/placeholder data in visuals
- Visual type variety without gratuitous switching

This module is completely independent — it does NOT modify any existing services.
"""

import asyncio
import json
import logging
import re
from typing import Dict, List, Optional
from dataclasses import dataclass, field, asdict

logger = logging.getLogger(__name__)

# TTS speaking rate — used for all duration math
WORDS_PER_MINUTE = 140  # Natural narration pace (not rushed, not slow)

# =============================================================================
# STORYBOARD DATA STRUCTURES
# =============================================================================

@dataclass
class SceneVisual:
    """Visual directive for a single scene/slide."""
    visual_type: str          # title_slide, stat_callout, bullet_list, quote, key_point,
                               # comparison, timeline_point, closing
    content: Dict             # Type-specific content payload
    ken_burns: str = "zoom_in"  # zoom_in, zoom_out, pan_left, pan_right, none


@dataclass
class Scene:
    """A single scene in the storyboard — maps to one slide + narration segment."""
    scene_id: int
    narration: str            # Text that will be spoken by TTS
    visual: SceneVisual       # What to render on the slide
    duration_hint: str = "auto"  # "auto" = derive from narration length, or seconds


@dataclass
class Storyboard:
    """Complete storyboard for a video explainer."""
    title: str
    topic: str
    scenes: List[Scene]
    source_names: List[str]
    estimated_duration_seconds: int
    format_type: str = "explainer"  # explainer or brief

    def to_dict(self) -> Dict:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2)


# =============================================================================
# VISUAL TYPE DEFINITIONS — what each visual_type expects in content
# =============================================================================

# Ken Burns effect pools per visual type — variety + appropriate motion
VISUAL_TYPE_EFFECTS = {
    "title_slide": ["zoom_in_slow", "zoom_out_slow"],
    "stat_callout": ["zoom_in", "zoom_in_slow"],
    "bullet_list": ["drift_right", "drift_left", "pan_right"],
    "quote": ["zoom_in_slow", "drift_right", "drift_left"],
    "key_point": ["zoom_in", "zoom_out", "drift_right", "drift_left"],
    "comparison": ["pan_right", "pan_left", "drift_right"],
    "timeline_point": ["drift_right", "pan_right"],
    "closing": ["zoom_out_slow", "zoom_out"],
}

# Varied bridge phrases for padding thin scenes (NEVER repeat the same one)
_BRIDGE_LONG = [
    "This reshapes how we understand the broader picture, weaving together several patterns that emerge from the research.",
    "What stands out here is how these findings challenge conventional thinking and open up genuinely new possibilities.",
    "Consider how this connects to the bigger story — it is one of the most compelling threads running through the data.",
    "The implications extend well beyond the surface, touching on themes that will resurface as we dig deeper.",
    "This is exactly the kind of insight that makes the research worth a closer look — it reframes the entire conversation.",
    "Pay attention to the nuance — the details matter, and they paint a richer picture than a quick summary suggests.",
    "What is particularly striking is how this finding echoes across multiple sources, reinforcing its significance.",
    "Think of this as a turning point in the analysis — the evidence points clearly in a direction worth following.",
]

_BRIDGE_MEDIUM = [
    "This insight is worth lingering on — it is central to the story.",
    "The evidence here is particularly compelling and well-supported.",
    "Notice how this builds on what came before and sets up what follows.",
    "This connects several important threads in the research together.",
    "Here is where the data really starts to tell an interesting story.",
]


VISUAL_TYPE_DOCS = """
Available visual_types and their REQUIRED content fields:

1. "title_slide" — Opening slide (MUST be the first scene)
   content: {"title": "Main Title", "subtitle": "A 5-10 word subtitle that frames the topic"}

2. "stat_callout" — Big number/statistic highlight (MUST use a real number from the research)
   content: {"number": "60%", "label": "of enterprises adopted this by 2024", "context": "Source: McKinsey Report"}

3. "bullet_list" — Progressive bullet points (3-5 items, each 8-15 words with real substance)
   content: {"title": "Section Title", "items": ["First substantive point with detail", "Second point explaining why", "Third point with evidence"]}

4. "quote" — Highlighted quote (MUST be a real quote from the source material)
   content: {"quote": "The actual quote text from the research", "attribution": "Author Name, Source"}

5. "key_point" — Single key insight (heading + 2-3 sentence explanation)
   content: {"heading": "The Key Insight in 5-8 Words", "body": "A full 2-3 sentence explanation that gives the viewer real understanding of this point. Include specifics."}

6. "comparison" — Side-by-side comparison (BOTH sides must have 2-3 real, specific points)
   content: {"title": "Meaningful Comparison Title", "left_label": "Specific Thing A", "left_points": ["Real point about A", "Another real point"], "right_label": "Specific Thing B", "right_points": ["Real point about B", "Another real point"]}

7. "timeline_point" — A point on a timeline or process sequence
   content: {"step_number": 1, "total_steps": 4, "label": "Descriptive Step Name", "description": "2-3 sentence explanation of what happens at this stage and why it matters."}

8. "closing" — Final slide with 3-4 substantive takeaways
   content: {"title": "Key Takeaways", "items": ["First actionable takeaway with specifics", "Second takeaway the viewer should remember", "Third takeaway connecting back to the main theme"]}
"""


# =============================================================================
# STORYBOARD GENERATOR
# =============================================================================

class VideoStoryboardGenerator:
    """Generates storyboards from notebook content using the local LLM."""

    async def generate(
        self,
        notebook_id: str,
        topic: Optional[str] = None,
        duration_minutes: int = 5,
        format_type: str = "explainer",
        chat_context: Optional[str] = None,
    ) -> Storyboard:
        """Generate a storyboard from notebook sources.

        Args:
            notebook_id: Notebook to pull content from
            topic: Optional focus topic
            duration_minutes: Target video duration (1-10 min)
            format_type: "explainer" (structured, 3-7 min) or "brief" (bite-sized, 1-2 min)
            chat_context: Recent chat conversation for "From Chat" mode

        Returns:
            Storyboard with scenes ready for slide rendering
        """
        from services.context_builder import context_builder

        # Clamp duration
        duration_minutes = max(1, min(10, duration_minutes))

        # Build context from notebook sources
        built = await context_builder.build_context(
            notebook_id=notebook_id,
            skill_id="video_storyboard",
            topic=topic,
            duration_minutes=duration_minutes,
        )

        if not built.context or built.sources_used == 0:
            raise ValueError("No source content available to create a video storyboard")

        # Prepend chat context if provided ("From Chat" mode)
        context_with_chat = built.context
        if chat_context:
            context_with_chat = f"""The user has been exploring this topic in a chat conversation. Use their discussion to focus the video on what matters most:

--- RECENT CHAT ---
{chat_context[:3000]}
--- END CHAT ---

{built.context}"""
            logger.info(f"[VideoStoryboard] 'From Chat' mode: injecting {len(chat_context)} chars of chat context")

        # ── Pacing math ──
        # Total narration budget based on speaking rate
        total_word_budget = int(duration_minutes * WORDS_PER_MINUTE)

        # Scene count: ~1.5 scenes/min for explainer (longer per slide),
        #              ~2.5 scenes/min for brief (punchier)
        if format_type == "brief":
            target_scenes = max(3, min(8, round(duration_minutes * 2.5)))
            min_words_per_scene = 20
        else:
            target_scenes = max(5, min(12, round(duration_minutes * 1.8)))
            min_words_per_scene = 35

        words_per_scene = max(min_words_per_scene, total_word_budget // target_scenes)

        logger.info(
            f"[VideoStoryboard] Targets: {duration_minutes}min → "
            f"{total_word_budget} words, {target_scenes} scenes, ~{words_per_scene} words/scene"
        )

        # ── Phase 1: Main LLM generates storyboard structure ──
        # Uses the deeper model (olmo-3:7b) for visual design + narrative arc
        storyboard_json = await self._generate_storyboard_structure(
            context=context_with_chat,
            topic=topic or "the main topics from the research",
            target_scenes=target_scenes,
            duration_minutes=duration_minutes,
            format_type=format_type,
            total_word_budget=total_word_budget,
            words_per_scene=words_per_scene,
        )

        # Parse structure into scenes (narration may be guide-only at this point)
        scenes = self._parse_storyboard(storyboard_json, format_type)

        if not scenes:
            # Retry once with a much simpler prompt (fewer scenes, less complex JSON)
            logger.warning("[VideoStoryboard] First attempt failed — retrying with simplified prompt")
            retry_json = await self._generate_storyboard_simple_retry(
                context=context_with_chat,
                topic=topic or "the main topics from the research",
                target_scenes=min(5, target_scenes),
                duration_minutes=duration_minutes,
            )
            scenes = self._parse_storyboard(retry_json, format_type)

        if not scenes:
            # Last resort: build a minimal fallback storyboard from context
            logger.warning("[VideoStoryboard] Retry also failed — building fallback storyboard")
            scenes = self._build_fallback_storyboard(
                topic=topic or "the main topics from the research",
                context=context_with_chat[:3000],
                target_scenes=min(5, target_scenes),
            )

        # ── Phase 2: Fast LLM generates narration per-scene in parallel ──
        # Uses phi4-mini for natural spoken language — all scenes concurrently
        scenes = await self._generate_narration_parallel(
            scenes=scenes,
            topic=topic or "the main topics from the research",
            context_excerpt=context_with_chat[:4000],
            words_per_scene=words_per_scene,
            duration_minutes=duration_minutes,
        )

        # Strip repeated filler phrases the LLM may have added across scenes
        scenes = self._deduplicate_narration(scenes)

        # ── Post-parse quality enforcement ──
        total_words = sum(len(s.narration.split()) for s in scenes)
        min_acceptable = int(total_word_budget * 0.50)  # At least 50% of target

        if total_words < min_acceptable:
            logger.warning(
                f"[VideoStoryboard] Narration too short: {total_words} words "
                f"(need ≥{min_acceptable} for {duration_minutes}min). "
                f"Padding thin scenes..."
            )
            scenes = self._pad_thin_scenes(scenes, min_words_per_scene)
            total_words = sum(len(s.narration.split()) for s in scenes)

        # Estimate duration from actual word count
        est_duration = int((total_words / WORDS_PER_MINUTE) * 60)

        title = topic or "Video Overview"
        if scenes and scenes[0].visual.visual_type == "title_slide":
            title = scenes[0].visual.content.get("title", title)

        storyboard = Storyboard(
            title=title,
            topic=topic or "",
            scenes=scenes,
            source_names=built.source_names,
            estimated_duration_seconds=est_duration,
            format_type=format_type,
        )

        logger.info(
            f"[VideoStoryboard] Final: {len(scenes)} scenes, "
            f"{total_words} words narration, est. {est_duration}s "
            f"(target was {duration_minutes * 60}s)"
        )

        return storyboard

    async def _generate_storyboard_structure(
        self,
        context: str,
        topic: str,
        target_scenes: int,
        duration_minutes: int,
        format_type: str,
        total_word_budget: int,
        words_per_scene: int,
    ) -> str:
        """Phase 1: Main LLM (olmo-3:7b) generates storyboard structure and visual content.

        Focuses the deeper model on what it's best at: picking visual types,
        extracting real data, structuring the narrative arc. Produces a
        narration_guide per scene (brief description of what narration should cover)
        rather than full spoken prose — that's handled by Phase 2.
        """
        from services.rag_engine import rag_engine
        from config import settings

        if format_type == "brief":
            format_guidance = f"""FORMAT: BRIEF — {duration_minutes} minute{'s' if duration_minutes > 1 else ''}, {target_scenes} slides, punchy overview."""
        else:
            format_guidance = f"""FORMAT: EXPLAINER — {duration_minutes} minute{'s' if duration_minutes > 1 else ''}, {target_scenes} slides, structured educational video.
Build progressively: introduce topic → explain key concepts → provide evidence → synthesize."""

        system_prompt = f"""You are an expert video storyboard designer. Your job is to design the VISUAL STRUCTURE
of a narrated slide video — choosing the right slide types, extracting real data for each slide,
and planning what the narrator should discuss.

{format_guidance}

YOUR OUTPUT: A JSON array of scene objects. Each scene has exactly three keys:
- "visual_type" (string): The type of slide to show
- "content" (object): The data displayed ON the slide — must be real, specific data from the research
- "narration_guide" (string): A 1-2 sentence description of what the narrator should explain for this slide.
  Include specific facts, numbers, or insights the narrator should mention. This guides the narration writer.

CONTENT QUALITY:
1. ONLY use facts, data, and quotes from the provided research. Never invent statistics.
2. Every content field must have REAL, SPECIFIC data — no placeholders or generic text.
3. Bullet lists: 3-5 items, each a substantive point (8+ words).
4. Stat callouts: actual numbers from research with proper context.
5. If research doesn't support a visual type, use key_point or bullet_list instead.

STRUCTURE:
1. Output ONLY valid JSON — no markdown, no commentary, no code fences.
2. First scene MUST be "title_slide". Last scene MUST be "closing" with 3-4 substantive takeaways.
3. Exactly {target_scenes} scenes. Vary visual types — never repeat the same type 3 times in a row.

{VISUAL_TYPE_DOCS}

TARGET: {target_scenes} scenes for a {duration_minutes}-minute video about: {topic}"""

        max_context = min(len(context), 12000)
        prompt = f"""Based ONLY on the following research content, design a {duration_minutes}-minute video storyboard.

Research content:
---
{context[:max_context]}
---

Generate a JSON array of exactly {target_scenes} scenes. Output ONLY the JSON array."""

        # Structure is smaller than full narration — less token budget needed
        # Let call_ollama auto-size num_ctx from actual prompt length (system + user)
        num_predict = max(2000, target_scenes * 300)

        result = await rag_engine._call_ollama(
            system_prompt, prompt,
            model=settings.ollama_model,  # Use main model for structure
            num_predict=num_predict,
            temperature=0.55,
            repeat_penalty=1.05
        )

        return result

    async def _generate_storyboard_simple_retry(
        self,
        context: str,
        topic: str,
        target_scenes: int,
        duration_minutes: int,
    ) -> str:
        """Retry storyboard generation with a much simpler prompt.

        Uses only key_point and bullet_list types, fewer scenes, and a
        more explicit JSON example to maximize parse success on local models.
        """
        from services.rag_engine import rag_engine
        from config import settings

        system_prompt = f"""You are a video storyboard designer. Output ONLY a JSON array — no other text.

Each object has exactly 3 keys:
- "visual_type": either "title_slide", "key_point", "bullet_list", or "closing"
- "content": object with the slide data
- "narration_guide": 1 sentence describing what the narrator should say

EXAMPLE (2 scenes):
[
  {{"visual_type": "title_slide", "content": {{"title": "My Topic", "subtitle": "A brief overview"}}, "narration_guide": "Welcome to our exploration of this topic."}},
  {{"visual_type": "key_point", "content": {{"heading": "Key Insight", "body": "Explanation here."}}, "narration_guide": "This is the most important finding."}}
]

Output ONLY the JSON array. No markdown fences. No commentary."""

        max_context = min(len(context), 6000)
        prompt = f"""Create a {target_scenes}-scene storyboard about: {topic}

Research:
{context[:max_context]}

Output a JSON array of exactly {target_scenes} scenes. First scene must be title_slide, last must be closing."""

        num_predict = max(1500, target_scenes * 250)
        num_ctx = max(8192, num_predict + 4000)

        result = await rag_engine._call_ollama(
            system_prompt, prompt,
            model=settings.ollama_model,
            num_predict=num_predict,
            num_ctx=num_ctx,
            temperature=0.4,
            repeat_penalty=1.05,
        )

        logger.info(f"[VideoStoryboard] Retry produced {len(result)} chars")
        return result

    def _build_fallback_storyboard(
        self,
        topic: str,
        context: str,
        target_scenes: int,
    ) -> "List[Scene]":
        """Build a minimal storyboard deterministically from context — no LLM call.

        Extracts key sentences from the source text and creates simple
        key_point slides so the video pipeline can still produce output.
        """
        # Extract meaningful sentences from context
        sentences = [s.strip() for s in re.split(r'[.!?]\s+', context) if len(s.strip()) > 30]
        # Deduplicate similar sentences
        unique = []
        for s in sentences:
            if not any(s[:40].lower() in u.lower() for u in unique):
                unique.append(s)
        sentences = unique

        scenes: List[Scene] = []

        # Title slide
        scenes.append(Scene(
            scene_id=0,
            narration=f"Let's explore {topic}.",
            visual=SceneVisual(
                visual_type="title_slide",
                content={"title": topic[:80], "subtitle": "An overview from the research"},
                ken_burns="zoom_in_slow",
            ),
        ))

        # Content slides from extracted sentences
        content_count = min(target_scenes - 2, len(sentences), 6)
        effects = ["zoom_in", "drift_right", "zoom_out", "drift_left", "pan_right"]
        for i in range(max(1, content_count)):
            sentence = sentences[i] if i < len(sentences) else f"Key insight {i+1} from the research."
            # Split sentence into heading (first ~8 words) and body (rest)
            words = sentence.split()
            heading = " ".join(words[:8])
            body = " ".join(words[8:]) if len(words) > 8 else sentence
            scenes.append(Scene(
                scene_id=i + 1,
                narration=sentence,
                visual=SceneVisual(
                    visual_type="key_point",
                    content={"heading": heading, "body": body},
                    ken_burns=effects[i % len(effects)],
                ),
            ))

        # Closing slide
        scenes.append(Scene(
            scene_id=len(scenes),
            narration=f"That wraps up our look at {topic}. The research reveals several important themes worth exploring further.",
            visual=SceneVisual(
                visual_type="closing",
                content={"title": "Key Takeaways", "items": [
                    sentences[0][:100] if sentences else "Review the research for deeper insights",
                    sentences[1][:100] if len(sentences) > 1 else "Multiple perspectives enrich understanding",
                    "Explore the source material for more detail",
                ]},
                ken_burns="zoom_out_slow",
            ),
        ))

        logger.info(f"[VideoStoryboard] Fallback storyboard: {len(scenes)} scenes from extracted text")
        return scenes

    async def _generate_narration_parallel(
        self,
        scenes: List[Scene],
        topic: str,
        context_excerpt: str,
        words_per_scene: int,
        duration_minutes: int,
    ) -> List[Scene]:
        """Phase 2: Fast LLM (phi4-mini) generates narration for each scene in parallel.

        Each scene gets its own focused prompt with the slide content as context.
        All scenes are generated concurrently via asyncio.gather for speed.
        """
        from services.rag_engine import rag_engine

        total_scenes = len(scenes)

        async def _narrate_scene(scene: Scene, scene_idx: int) -> Scene:
            """Generate narration for a single scene."""
            # Title slides and closings get shorter narration
            if scene.visual.visual_type == "title_slide":
                target_words = max(20, words_per_scene // 2)
            elif scene.visual.visual_type == "closing":
                target_words = max(30, words_per_scene)
            else:
                target_words = words_per_scene

            # Build a description of what's on the slide
            slide_description = self._describe_slide(scene.visual)

            # Use the narration_guide if available (from Phase 1), else the existing narration
            guide = scene.narration if scene.narration else "Explain this slide to the viewer."

            system_prompt = f"""You are a professional video narrator. Write the spoken narration for ONE slide
in a {duration_minutes}-minute educational video about "{topic}".

Your narration will be converted to speech via TTS. Write natural, engaging spoken language.

RULES:
1. Write exactly {target_words}-{target_words + 15} words. Count carefully.
2. Be specific and substantive — reference the actual data shown on the slide.
3. Speak directly to the viewer. Vary your tone: use questions, insights, transitions.
4. Do NOT describe the slide itself ("As you can see..."). Instead, EXPLAIN the content.
5. Output ONLY the narration text — no labels, no quotes, no formatting."""

            prompt = f"""Scene {scene_idx + 1} of {total_scenes}.

SLIDE CONTENT: {slide_description}

NARRATION GUIDE: {guide}

RESEARCH CONTEXT (use for specific details):
{context_excerpt[:2000]}

Write {target_words}-{target_words + 15} words of spoken narration for this slide. Output ONLY the narration text."""

            try:
                narration = await rag_engine._call_ollama(
                    system_prompt, prompt,
                    # model=None uses the default fast model (phi4-mini)
                    num_predict=max(200, target_words * 3),
                    num_ctx=4096,
                    temperature=0.7,
                    repeat_penalty=1.1
                )

                # Clean up: strip any quotes, labels, or formatting the LLM added
                narration = narration.strip().strip('"').strip("'")
                narration = re.sub(r'^(Narration|Scene \d+|Slide \d+)[:\s]*', '', narration, flags=re.IGNORECASE).strip()

                if narration and len(narration.split()) >= 10:
                    return Scene(
                        scene_id=scene.scene_id,
                        narration=narration,
                        visual=scene.visual,
                        duration_hint=scene.duration_hint,
                    )
            except Exception as e:
                logger.warning(f"[VideoStoryboard] Narration generation failed for scene {scene_idx}: {e}")

            # Fallback: keep original narration/guide
            return scene

        # Run all narration tasks in parallel
        logger.info(f"[VideoStoryboard] Generating narration for {total_scenes} scenes in parallel (fast LLM)...")
        tasks = [_narrate_scene(scene, i) for i, scene in enumerate(scenes)]
        results = await asyncio.gather(*tasks)

        total_words = sum(len(s.narration.split()) for s in results)
        logger.info(f"[VideoStoryboard] Parallel narration complete: {total_words} words across {total_scenes} scenes")

        return list(results)

    def _describe_slide(self, visual: SceneVisual) -> str:
        """Create a text description of a slide's visual content for the narration LLM."""
        vt = visual.visual_type
        c = visual.content

        if vt == "title_slide":
            return f"Title slide: \"{c.get('title', '')}\" — {c.get('subtitle', '')}"
        elif vt == "stat_callout":
            return f"Big statistic: {c.get('number', '?')} — {c.get('label', '')}. Context: {c.get('context', '')}"
        elif vt == "bullet_list":
            items = c.get("items", [])
            return f"Bullet list \"{c.get('title', '')}\": " + "; ".join(str(i) for i in items[:5])
        elif vt == "quote":
            return f"Quote: \"{c.get('quote', '')}\" — {c.get('attribution', 'Unknown')}"
        elif vt == "key_point":
            return f"Key point: {c.get('heading', '')} — {c.get('body', '')}"
        elif vt == "comparison":
            return (f"Comparison: {c.get('left_label', 'A')} vs {c.get('right_label', 'B')}. "
                    f"{c.get('left_label', 'A')}: {'; '.join(c.get('left_points', [])[:3])}. "
                    f"{c.get('right_label', 'B')}: {'; '.join(c.get('right_points', [])[:3])}")
        elif vt == "timeline_point":
            return f"Timeline step {c.get('step_number', '?')}/{c.get('total_steps', '?')}: {c.get('label', '')} — {c.get('description', '')}"
        elif vt == "closing":
            items = c.get("items", [])
            return f"Closing takeaways: " + "; ".join(str(i) for i in items[:4])
        else:
            return f"{vt}: {json.dumps(c)[:200]}"

    def _parse_storyboard(self, raw_json: str, format_type: str = "explainer") -> List[Scene]:
        """Parse LLM output into Scene objects with quality validation."""
        text = raw_json.strip()

        # Strip markdown code fences if present
        text = re.sub(r'^```(?:json)?\s*', '', text)
        text = re.sub(r'\s*```\s*$', '', text)
        text = text.strip()

        # Find the JSON array
        start = text.find('[')
        end = text.rfind(']')

        scenes_data = None

        if start != -1 and end != -1 and end > start:
            json_str = text[start:end + 1]
            try:
                scenes_data = json.loads(json_str)
            except json.JSONDecodeError as e:
                logger.warning(f"[VideoStoryboard] JSON parse failed: {e}\nRaw excerpt: {json_str[:400]}")
                # Try to fix common issues: trailing commas
                fixed = re.sub(r',\s*([}\]])', r'\1', json_str)
                try:
                    scenes_data = json.loads(fixed)
                except json.JSONDecodeError:
                    pass  # Fall through to per-object recovery

        # Per-object recovery: extract individual scene objects from truncated/malformed output
        if scenes_data is None:
            search_text = text[start:] if start != -1 else text
            logger.warning(f"[VideoStoryboard] Attempting per-object recovery from output ({len(search_text)} chars)")
            scenes_data = []
            for m in re.finditer(r'\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}', search_text):
                try:
                    obj = json.loads(m.group())
                    if isinstance(obj, dict) and ("visual_type" in obj or "content" in obj):
                        scenes_data.append(obj)
                except json.JSONDecodeError:
                    continue
            if scenes_data:
                logger.info(f"[VideoStoryboard] Recovered {len(scenes_data)} scenes from truncated output")
            else:
                logger.error(f"[VideoStoryboard] Could not recover any scenes. Raw output ({len(text)} chars): {text[:800]}")
                return []

        if not isinstance(scenes_data, list):
            logger.error("[VideoStoryboard] LLM output is not a JSON array")
            return []

        # Visual types that produce reliable, content-rich slides
        valid_types = {
            "title_slide", "stat_callout", "bullet_list", "quote",
            "key_point", "comparison", "timeline_point", "closing"
        }

        scenes = []
        for i, scene_data in enumerate(scenes_data):
            if not isinstance(scene_data, dict):
                continue

            # Phase 1 produces narration_guide; fall back to narration for compat
            narration = (scene_data.get("narration_guide", "") or
                         scene_data.get("narration", "") or "")
            visual_type = scene_data.get("visual_type", "key_point")
            content = scene_data.get("content", {})

            if not narration.strip() and not content:
                logger.debug(f"[VideoStoryboard] Skipping scene {i}: empty narration and content")
                continue

            # Normalize visual type — drop diagram_placeholder (unreliable)
            if visual_type not in valid_types or visual_type == "diagram_placeholder":
                visual_type = "key_point"

            # Ensure content is a dict
            if not isinstance(content, dict):
                content = {"heading": str(content), "body": ""}

            # Quality-validate content — may downgrade visual_type if data is thin
            visual_type, content = self._validate_content_quality(visual_type, content)

            # Assign Ken Burns effect based on visual type for variety
            effects = VISUAL_TYPE_EFFECTS.get(visual_type, ["zoom_in", "zoom_out", "drift_right"])
            kb = effects[i % len(effects)]
            # Avoid same effect as the previous scene
            if scenes and scenes[-1].visual.ken_burns == kb:
                kb = effects[(i + 1) % len(effects)]

            scenes.append(Scene(
                scene_id=i,
                narration=narration.strip(),
                visual=SceneVisual(
                    visual_type=visual_type,
                    content=content,
                    ken_burns=kb
                ),
                duration_hint="auto"
            ))

        logger.info(f"[VideoStoryboard] Parsed {len(scenes)} valid scenes from LLM output")
        return scenes

    def _validate_content_quality(self, visual_type: str, content: Dict) -> tuple:
        """Validate content has real data. Downgrades type to key_point if content is thin.

        Returns (visual_type, content) tuple — visual_type may be changed.
        """
        if visual_type == "title_slide":
            content.setdefault("title", "Video Overview")
            content.setdefault("subtitle", "")
            return visual_type, content

        if visual_type == "closing":
            content.setdefault("title", "Key Takeaways")
            if "items" not in content or not isinstance(content["items"], list):
                content["items"] = ["Review the research for deeper insights"]
            # Filter out empty/very short items
            content["items"] = [
                item for item in content["items"]
                if isinstance(item, str) and len(item.strip()) > 5
            ]
            if len(content["items"]) < 2:
                content["items"] = ["Explore the research material for more details",
                                    "Apply these insights to your own context"]
            return visual_type, content

        if visual_type == "stat_callout":
            num = content.get("number", "?")
            label = content.get("label", "")
            # Reject if number is a placeholder
            if num in ("?", "", "N/A", "TBD") or not label or len(label) < 5:
                logger.debug(f"[VideoStoryboard] Downgrading thin stat_callout to key_point")
                return "key_point", {"heading": str(num), "body": label or "Key statistic"}
            content.setdefault("context", "")
            return visual_type, content

        if visual_type == "bullet_list":
            content.setdefault("title", "")
            items = content.get("items", [])
            if not isinstance(items, list):
                items = []
            # Filter out empty/very short items
            items = [i for i in items if isinstance(i, str) and len(i.strip()) > 5]
            if len(items) < 2:
                logger.debug(f"[VideoStoryboard] Downgrading thin bullet_list to key_point")
                title = content.get("title", "")
                body = "; ".join(items) if items else ""
                return "key_point", {"heading": title or "Key Points", "body": body}
            content["items"] = items[:6]  # Cap at 6 items
            return visual_type, content

        if visual_type == "quote":
            quote = content.get("quote", "")
            if not quote or len(quote.strip()) < 10:
                logger.debug(f"[VideoStoryboard] Downgrading thin quote to key_point")
                return "key_point", {"heading": "Notable Insight",
                                      "body": quote or "An important perspective from the research."}
            content.setdefault("attribution", "")
            return visual_type, content

        if visual_type == "comparison":
            left_pts = content.get("left_points", [])
            right_pts = content.get("right_points", [])
            left_label = content.get("left_label", "")
            right_label = content.get("right_label", "")
            # Filter empty points
            left_pts = [p for p in left_pts if isinstance(p, str) and len(p.strip()) > 5] if isinstance(left_pts, list) else []
            right_pts = [p for p in right_pts if isinstance(p, str) and len(p.strip()) > 5] if isinstance(right_pts, list) else []
            # Reject if labels are generic or points are empty
            generic_labels = {"a", "b", "option a", "option b", "left", "right", ""}
            if (left_label.lower().strip() in generic_labels or
                right_label.lower().strip() in generic_labels or
                len(left_pts) < 1 or len(right_pts) < 1):
                logger.debug(f"[VideoStoryboard] Downgrading thin comparison to bullet_list")
                all_points = left_pts + right_pts
                if len(all_points) >= 2:
                    return "bullet_list", {
                        "title": content.get("title", "Key Differences"),
                        "items": all_points[:5]
                    }
                return "key_point", {
                    "heading": content.get("title", "Comparison"),
                    "body": f"{left_label}: {'; '.join(left_pts)}. {right_label}: {'; '.join(right_pts)}"
                }
            content["left_points"] = left_pts
            content["right_points"] = right_pts
            return visual_type, content

        if visual_type == "timeline_point":
            content.setdefault("step_number", 1)
            content.setdefault("total_steps", 1)
            content.setdefault("label", "")
            desc = content.get("description", "")
            if not desc or len(desc.strip()) < 10:
                logger.debug(f"[VideoStoryboard] Downgrading thin timeline_point to key_point")
                return "key_point", {
                    "heading": content.get("label", "Process Step"),
                    "body": desc or "An important step in the process."
                }
            return visual_type, content

        # key_point (default)
        content.setdefault("heading", "")
        content.setdefault("body", "")
        if not content["body"] or len(content["body"].strip()) < 10:
            # Try to salvage from heading
            if content["heading"] and len(content["heading"]) > 20:
                content["body"] = content["heading"]
                content["heading"] = "Key Insight"
        return visual_type, content

    def _deduplicate_narration(self, scenes: List[Scene]) -> List[Scene]:
        """Detect and remove phrases repeated across 3+ scenes (LLM repetition artifact)."""
        if len(scenes) < 3:
            return scenes

        # Split each scene into sentences and count cross-scene frequency
        scene_sentences = []
        sentence_counts: Dict[str, int] = {}
        for scene in scenes:
            sents = [s.strip() for s in re.split(r'(?<=[.!?])\s+', scene.narration) if len(s.strip()) > 15]
            scene_sentences.append(sents)
            seen_in_scene: set = set()
            for s in sents:
                key = s.lower().rstrip('.!? ')
                if key not in seen_in_scene:
                    sentence_counts[key] = sentence_counts.get(key, 0) + 1
                    seen_in_scene.add(key)

        # Sentences appearing in 3+ scenes are almost certainly filler
        repeated = {k for k, v in sentence_counts.items() if v >= 3}
        if not repeated:
            return scenes

        logger.warning(f"[VideoStoryboard] Stripping {len(repeated)} repeated phrase(s) from narration")

        cleaned = []
        for scene, sents in zip(scenes, scene_sentences):
            filtered = [s for s in sents if s.strip().lower().rstrip('.!? ') not in repeated]
            new_narration = ' '.join(filtered).strip() if filtered else scene.narration
            if new_narration != scene.narration:
                scene = Scene(
                    scene_id=scene.scene_id,
                    narration=new_narration,
                    visual=scene.visual,
                    duration_hint=scene.duration_hint,
                )
            cleaned.append(scene)
        return cleaned

    def _pad_thin_scenes(self, scenes: List[Scene], min_words: int) -> List[Scene]:
        """Ensure every scene has at least min_words in its narration.

        Uses a rotating pool of varied bridge phrases — never repeats the same one.
        """
        long_idx = 0
        med_idx = 0
        padded = []
        for scene in scenes:
            words = scene.narration.split()
            if len(words) < min_words and scene.visual.visual_type not in ("title_slide",):
                deficit = min_words - len(words)
                if deficit > 15:
                    bridge = _BRIDGE_LONG[long_idx % len(_BRIDGE_LONG)]
                    long_idx += 1
                elif deficit > 8:
                    bridge = _BRIDGE_MEDIUM[med_idx % len(_BRIDGE_MEDIUM)]
                    med_idx += 1
                else:
                    bridge = ""
                if bridge:
                    scene = Scene(
                        scene_id=scene.scene_id,
                        narration=scene.narration.strip() + " " + bridge,
                        visual=scene.visual,
                        duration_hint=scene.duration_hint,
                    )
            padded.append(scene)
        return padded


# Singleton
video_storyboard = VideoStoryboardGenerator()
