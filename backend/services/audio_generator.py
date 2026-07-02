"""Audio generation service for podcasts

Uses Kokoro-82M for high-quality text-to-speech generation.
50+ voices across 9 languages with Apache 2.0 licensing.
"""
import asyncio
import math
import random
import re
import struct
import subprocess
import wave
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from config import settings
from storage.audio_store import audio_store
from storage.source_store import source_store
from storage.skills_store import skills_store
from services.rag_engine import rag_engine
from services.context_builder import context_builder
import logging
logger = logging.getLogger(__name__)

class AudioGenerator:
    """Generate podcast audio from notebooks using Kokoro-82M"""

    def __init__(self):
        self.audio_dir = settings.data_dir / "audio"
        self.audio_dir.mkdir(parents=True, exist_ok=True)
        self._background_tasks = set()
        
        # Kokoro voice pools per accent × gender — rotated for podcast variety
        self.voices = {
            "us": {
                "male":   ["am_adam", "am_michael", "am_fenrir"],
                "female": ["af_heart", "af_bella", "af_nicole", "af_sarah", "af_sky", "af_nova"],
            },
            "uk": {
                "male":   ["bm_george", "bm_lewis", "bm_daniel"],
                "female": ["bf_emma", "bf_isabella"],
            },
            "es": {
                "male":   ["em_alex", "em_santa"],
                "female": ["ef_dora"],
            },
            "fr": {
                "male":   ["ff_siwis"],  # only one French voice
                "female": ["ff_siwis"],
            },
            "hi": {
                "male":   ["hm_omega", "hm_psi"],
                "female": ["hf_alpha", "hf_beta"],
            },
            "it": {
                "male":   ["im_nicola"],
                "female": ["if_sara"],
            },
            "ja": {
                "male":   ["jf_alpha"],  # no dedicated ja male voice available
                "female": ["jf_alpha", "jf_gongitsune"],
            },
            "pt": {
                "male":   ["pm_alex", "pm_santa"],
                "female": ["pf_dora"],
            },
            "zh": {
                "male":   ["zm_yunjian", "zm_yunxi"],
                "female": ["zf_xiaobei", "zf_xiaoni", "zf_xiaoxiao"],
            },
        }
        
        self._voice_index = 0  # Track voice rotation for variety

    # ═══════════════════════════════════════════════════════════════════════
    # Script Blueprints — structural definitions per audio type
    # ═══════════════════════════════════════════════════════════════════════
    # Each blueprint defines phases with percentage word allocations,
    # acceptable turn lengths, and speaker balance requirements.
    # The validator enforces these structurally — not by hoping the LLM complies.
    SCRIPT_BLUEPRINTS = {
        'podcast_script': {
            'phases': [
                {'name': 'opening', 'pct': 0.12, 'instruction': 'Both hosts introduce themselves by name. Hook the listener with a surprising fact or provocative question from the research.'},
                {'name': 'deep_dive', 'pct': 0.60, 'instruction': 'Substantive discussion of the research. Natural back-and-forth with reactions, questions, and challenges. Cover key findings and implications.'},
                {'name': 'synthesis', 'pct': 0.15, 'instruction': 'Connect the dots — what does this all mean? Draw broader conclusions from the research.'},
                {'name': 'closing', 'pct': 0.13, 'instruction': 'Natural wind-down. Each host shares ONE specific personal takeaway — not a summary. Sign off by name. End with one forward-looking question for the listener. Do NOT rehash earlier points.'},
            ],
            'words_per_turn': (12, 80),
            'speaker_balance': (0.35, 0.65),
        },
        'debate': {
            'phases': [
                {'name': 'opening_positions', 'pct': 0.15, 'instruction': 'Both debaters introduce themselves and state their positions clearly and forcefully.'},
                {'name': 'clash', 'pct': 0.50, 'instruction': 'Direct engagement — rebuttals, counter-arguments, evidence from the research. They respond to each other, not just monologue.'},
                {'name': 'escalation', 'pct': 0.20, 'instruction': 'The debate intensifies. Strongest arguments, hypotheticals, real-world implications.'},
                {'name': 'closing_statements', 'pct': 0.15, 'instruction': 'Each debater gives ONE concise closing statement with their strongest remaining argument. Neither side wins cleanly. Sign off by name. Do NOT repeat earlier arguments.'},
            ],
            'words_per_turn': (15, 100),
            'speaker_balance': (0.40, 0.60),
        },
        'interview': {
            'phases': [
                {'name': 'introduction', 'pct': 0.10, 'instruction': 'Interviewer introduces themselves and the expert by name. Sets up the topic with a hook.'},
                {'name': 'exploration', 'pct': 0.55, 'instruction': 'Probing questions and detailed expert answers. Follow the thread — dig deeper on interesting points.'},
                {'name': 'implications', 'pct': 0.20, 'instruction': 'Big-picture questions. What does this mean for the field? For everyday people?'},
                {'name': 'lightning_round', 'pct': 0.15, 'instruction': 'Quick-fire questions and concise answers. Final question: "one thing you want listeners to take away?" Sign off by name. Do NOT rehash earlier answers.'},
            ],
            'words_per_turn': (10, 120),
            'speaker_balance': (0.25, 0.75),  # Expert talks more
        },
        'storytelling': {
            'phases': [
                {'name': 'hook', 'pct': 0.10, 'instruction': 'Brief intro, then a compelling hook: "Picture this..." or "It all started when..."'},
                {'name': 'rising_action', 'pct': 0.40, 'instruction': 'Build the narrative. Introduce key findings as story beats. Build suspense and curiosity.'},
                {'name': 'climax', 'pct': 0.30, 'instruction': 'The big reveal — the most surprising or important finding. Dramatic payoff.'},
                {'name': 'resolution', 'pct': 0.20, 'instruction': 'Tie it all together. Connect back to the opening hook. Each speaker shares one lasting insight. Sign off by name. Do NOT retell the story.'},
            ],
            'words_per_turn': (10, 90),
            'speaker_balance': (0.55, 0.75),  # Storyteller talks more
        },
        'feynman_curriculum': {
            'phases': [
                {'name': 'foundation', 'pct': 0.25, 'instruction': 'Explain core concepts simply — like to a 12-year-old. Everyday analogies.'},
                {'name': 'building', 'pct': 0.25, 'instruction': 'Deeper connections, real-world examples, address misconceptions.'},
                {'name': 'first_principles', 'pct': 0.25, 'instruction': 'Go beyond what to WHY. Root mechanisms and underlying principles.'},
                {'name': 'mastery', 'pct': 0.25, 'instruction': 'Learner teaches back. Teacher asks tough questions. Synthesize everything. End with teacher confirming understanding and both signing off by name. Do NOT re-explain concepts already covered.'},
            ],
            'words_per_turn': (10, 70),
            'speaker_balance': (0.40, 0.60),
        },
    }

    # TTS speech rate — Kokoro-82M speaks at ~180 wpm empirically;
    # 170 gives slight headroom for jingles, speaker pauses, crossfade
    TTS_WORDS_PER_MINUTE = 170

    # Validation thresholds
    SECTION_WORD_TOLERANCE = 0.40       # Section can be ±40% of budget before retry
    TOTAL_WORD_TOLERANCE = 0.15         # Final script must be within ±15% of target
    MAX_TURN_CHARS = 500                # No single turn should exceed this after cleaning
    MIN_TURNS_PER_SECTION = 4           # At least 4 speaker turns per section
    DEGENERATE_LINE_THRESHOLD = 500     # Lines > this with few sentence endings = word salad
    MAX_SECTION_RETRIES = 2             # Two retries per section

    @staticmethod
    def _audio_profile_multiplier() -> float:
        """Return the active main model's word-tolerance multiplier.

        Models like Gemma tend to produce more verbose prose than expected,
        so their audio_profile.multi_pass_word_tolerance > 1.0 widens the
        per-section pass band. olmo's tighter prose uses 1.0-1.1.
        Falls back to 1.0 if no profile is configured.
        """
        try:
            from config import settings as _s
            from evaluator.model_registry import model_registry
            info = model_registry.get_model(_s.ollama_model)
            if info and info.audio_profile:
                return float(info.audio_profile.get("multi_pass_word_tolerance", 1.0))
        except Exception:
            pass
        return 1.0

    # Gender × accent name pools — picked randomly for each generation
    HOST_NAME_POOLS = {
        "us": {
            "male": ["Marcus", "David", "James", "Alex", "Ryan", "Chris", "Jordan", "Tyler", "Ethan", "Noah"],
            "female": ["Sarah", "Emily", "Maya", "Rachel", "Nicole", "Olivia", "Sophia", "Ava", "Chloe", "Lily"],
        },
        "uk": {
            "male": ["Oliver", "George", "William", "Thomas", "Edward", "Henry", "James", "Arthur", "Hugo", "Alfie"],
            "female": ["Charlotte", "Emma", "Sophie", "Amelia", "Grace", "Isla", "Eleanor", "Lily", "Ruby", "Poppy"],
        },
    }

    def _pick_host_names(self, host1_gender: str, host2_gender: str, accent: str) -> Tuple[str, str]:
        """Pick two distinct, gender/accent-appropriate character names."""
        pool = self.HOST_NAME_POOLS.get(accent, self.HOST_NAME_POOLS["us"])
        pool1 = pool.get(host1_gender, pool["male"])
        pool2 = pool.get(host2_gender, pool["female"])
        name1 = random.choice(pool1)
        # Ensure name2 is different from name1
        candidates = [n for n in pool2 if n != name1]
        name2 = random.choice(candidates) if candidates else random.choice(pool2)
        return name1, name2

    async def generate(
        self,
        notebook_id: str,
        topic: Optional[str] = None,
        duration_minutes: int = 10,
        skill_id: Optional[str] = None,
        host1_gender: str = "male",
        host2_gender: str = "female",
        accent: str = "us",
        chat_context: Optional[str] = None,
        register: Optional[str] = None,
    ) -> Dict:
        """Generate podcast audio.
        
        Returns immediately with a 'pending' record. Script generation and
        audio synthesis both run in the background so the API never blocks
        and the UI never freezes.
        """

        # Create audio record FIRST — return instantly to the frontend
        generation = await audio_store.create(
            notebook_id=notebook_id,
            script="",
            topic=topic or "the research content",
            duration_minutes=duration_minutes,
            host1_gender=host1_gender,
            host2_gender=host2_gender,
            accent=accent,
            skill_id=skill_id
        )

        # All audio formats use two-host conversation style
        is_two_host = True

        # Start EVERYTHING in background — script gen + audio gen
        print(f"🎬 Starting background audio pipeline for {generation['audio_id']}")
        from utils.tasks import safe_create_task
        task = safe_create_task(
            self._full_pipeline_async(
                audio_id=generation["audio_id"],
                notebook_id=notebook_id,
                topic=topic,
                duration_minutes=duration_minutes,
                skill_id=skill_id,
                host1_gender=host1_gender,
                host2_gender=host2_gender,
                accent=accent,
                is_two_host=is_two_host,
                chat_context=chat_context,
                register=register,
            ),
            name=f"audio-pipeline-{generation['audio_id']}"
        )
        # Keep reference to prevent garbage collection
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)

        return generation

    # Tier 4.2 (2026-06-01) — per-style default register for audio.
    # Used when the caller doesn't specify an explicit register override.
    _AUDIO_DEFAULT_REGISTER = {
        "podcast_script": "engaged",
        "debate": "engaged",
        "interview": "measured",
        "storytelling": "warm",
        "feynman_curriculum": "warm",
    }

    # Audio-specific addendum: punctuation conventions that the TTS engine
    # reflects as prosody. Appended to the shared register brief so the
    # script-writer knows the TTS will pick up these cues.
    _AUDIO_PROSODY_OVERLAY = (
        "\nTTS PROSODY (Kokoro reflects these in audible output):\n"
        "- Em-dashes ( — ) produce a slight pause; use them for thought-breaks and asides.\n"
        "- Ellipses (...) produce a longer pause + slight trailing-off; use for unfinished thoughts.\n"
        "- Question marks even mid-clause produce a rising intonation; use when the speaker is "
        "genuinely puzzled.\n"
        "- ALL-CAPS on a single emphasized word produces audible stress — use SPARINGLY (one or two "
        "per turn, never more) and only on words that genuinely carry the weight.\n"
        "- Sentence length variety matters as much as punctuation. A short clipped sentence after "
        "three long ones produces audible emphasis the listener will feel."
    )

    async def _generate_script(
        self,
        notebook_id: str,
        topic: Optional[str],
        duration_minutes: int,
        skill_id: Optional[str],
        host_names: Optional[Tuple[str, str]] = None,
        chat_context: Optional[str] = None,
        register: Optional[str] = None,
    ) -> str:
        """Generate podcast script from notebook sources.
        
        Scales source utilization and generation strategy with duration:
        - Short (1-10 min): single-pass, top sources
        - Medium (11-20 min): single-pass, more sources, larger context
        - Long (21-30 min): multi-pass sections for coherent long-form
        """
        
        # Use centralized context builder with duration-aware budgets
        audio_skill = "feynman_curriculum" if skill_id == "feynman_curriculum" else "podcast_script"
        built = await context_builder.build_context(
            notebook_id=notebook_id,
            skill_id=audio_skill,
            topic=topic,
            duration_minutes=duration_minutes,
        )
        
        context = built.context
        
        # Prepend chat context if provided ("From Chat" mode)
        if chat_context:
            context = f"""The user has been exploring this topic in a chat conversation. Use their discussion to focus the podcast on what matters most:

--- RECENT CHAT ---
{chat_context[:3000]}
--- END CHAT ---

{context}"""
        
        # ── Resolve character names ──
        name_a = host_names[0] if host_names else "Host A"
        name_b = host_names[1] if host_names else "Host B"
        
        # ── Shared TTS rules (all styles) ──
        # Kept SHORT — local models respond better to examples than long rule lists
        _TTS_RULES = f"""FORMAT RULES — This script will be read aloud by a text-to-speech engine:
- Write ONLY spoken words. One speaker per line: {name_a}: [words] or {name_b}: [words]
- Each turn: 1-3 sentences (15-50 words). Alternate speakers every turn.
- NEVER write stage directions, markdown, headers, bullet points, or meta-text.
- NEVER use "Assistant:" or "User:" — ONLY {name_a}: and {name_b}:
- {name_a} and {name_b} are ONLY character names. NEVER use a host name as a technical term, method, tool, or concept."""

        target_words = duration_minutes * self.TTS_WORDS_PER_MINUTE
        target_exchanges = max(8, target_words // 35)  # ~35 words per exchange pair

        # Citation contract (Tier 2.3, 2026-06-01): each source in the
        # research context is prefixed with [Sn]. The script must anchor
        # specific claims to those tags. Speaker turns surface citations
        # naturally — never as "according to source 1", which is
        # robotic. Suffix the tag right after the claim instead.
        legend = built.citation_legend() if built else ""
        legend_block = ""
        if legend:
            legend_block = f"""SOURCE LEGEND (use these [Sn] tags when anchoring claims):
{legend}
"""

        # Voice register (Tier 4.2). Caller override > per-style default >
        # "engaged" floor. Brief gets the TTS prosody overlay appended.
        from services.output_templates import get_register_brief
        effective_register = register or self._AUDIO_DEFAULT_REGISTER.get(skill_id or "podcast_script", "engaged")
        register_brief = get_register_brief(effective_register, fallback="engaged") + self._AUDIO_PROSODY_OVERLAY

        _GROUNDING = f"""LENGTH: Write a LONG conversation — {duration_minutes} minutes of audio.
Do NOT wrap up early. Do NOT summarize. Keep exploring new angles and reactions.
Every section needs many back-and-forth exchanges — keep the conversation going!

GROUNDING: ONLY discuss facts from the provided research. Do NOT invent statistics or quotes.
{legend_block}When citing a specific fact, statistic, finding, or quote, append the source tag in brackets at the end of the sentence — "[S2]". Anchor AT LEAST 4 turns per section to concrete sourced material this way. Connective conversation between cited turns doesn't need tags.
Topic: {topic or 'the main topics and insights from the research'}

{register_brief}"""

        # ── Per-style few-shot examples ──
        # Each example demonstrates the structural MOVE that defines the style,
        # not just two people taking polite turns. 7B models clone tone and
        # shape from few-shots far more reliably than from prose rules, so
        # this is the highest-leverage lever for style differentiation.
        # Canned reactions ("Wait, really?", "That's wild", "No way!") are
        # deliberately absent — they ended up in every generated podcast.

        _EXAMPLE_PODCAST = f"""EXAMPLE — match the length, the citation-anchoring discipline, and the analytical move. Do NOT copy the literal phrases.
{name_a}: There's something in the data I want to start with, because I think most people would guess wrong. The finding is that adoption isn't tracking budget — it's tracking the half-life of legacy expertise on the team [S1].
{name_b}: Half-life as in radioactive decay. So the longer the migration drags, the fewer people remain who know how the old thing worked.
{name_a}: Exactly. And by month six the institutional knowledge has dropped by half, which means the migration team is making decisions blind to what the old system was actually doing. That's where the cost overruns live [S1].
{name_b}: The part I'd push on, though, is that the same paper shows the fast migrations also overran [S2]. So is it really the timeline that's the problem?
{name_a}: That's the part I had to read twice. Their answer is that speed only helps if you've documented enough to absorb the knowledge loss. Without documentation, fast or slow, you're toast.
{name_b}: So the lever isn't pace, it's docs.
{name_a}: And there's a stronger version of that claim later in the paper — teams that documented as they went, day by day, had zero overruns. None [S1].
{name_b}: Zero is striking. Did they say anything about WHY that worked when other interventions didn't?
{name_a}: The hypothesis is that documentation forces the migrating team to externalize the model. Once it's outside their head, it survives them leaving.
{name_b}: It's like the team becomes its own backup.
{name_a}: That's a clean way to put it. Now, the second study pushes back on this — they argue documentation is necessary but nowhere near sufficient [S3].
{name_b}: Necessary but not sufficient is doing a lot of work in that sentence. What's the missing piece?
{name_a}: Cross-team review of the docs as they're being written. Without that loop, the docs accumulate but they don't actually capture what an outside team needs."""

        _EXAMPLE_DEBATE = f"""EXAMPLE — match the length, the explicit rebuttal moves, and the citation-anchoring discipline. Do NOT copy the literal phrases.
{name_a}: My position is that centralization improves throughput because shared infrastructure amortizes fixed cost across more users. Every centralization migration in the study reduced per-user cost by 30 to 50 percent within twelve months [S1].
{name_b}: That argument has a hole in it. Your own source — the same paper — also reports that three of those four migrations experienced more severe outages in year two than they had pre-migration [S1]. You're cherry-picking the cost line and ignoring the reliability line.
{name_a}: I'm not ignoring it, I'm saying the reliability hit is a transitional artifact. The team is still learning the new operational model.
{name_b}: That's exactly the kind of hand-wave I want to push on. The paper measures year-two outage severity, not month-three. By year two the team is past the learning curve. The reliability hit isn't transitional — it's structural [S1].
{name_a}: Fair, but consider what the second study found: federations of independent systems carry their own coordination tax that scales with the number of teams. Centralization eliminates that [S2].
{name_b}: Eliminates one tax and creates another. You're trading distributed coordination overhead for centralized single-point-of-failure risk. The math only favors centralization if your demand is predictable.
{name_a}: That's actually a concession I'll take. Under predictable demand, centralization wins. The argument is about where the threshold is.
{name_b}: Then we're not really disagreeing about whether centralization works — we're disagreeing about how often a real workload actually meets the predictability bar. And I'd argue almost never [S3].
{name_a}: I think that's where you and I genuinely part. I read the same paper and I see two-thirds of workloads in the "predictable enough" range [S3].
{name_b}: We can't both be reading that table the same way. What's your column?
{name_a}: Steady-state requests per second over rolling thirty-day windows.
{name_b}: That's the wrong column. Peak-to-trough ratio is the column that matters for centralization risk. By that measure it's the opposite — two-thirds are unpredictable.
{name_a}: Then the disagreement isn't about evidence, it's about which metric matters."""

        _EXAMPLE_INTERVIEW = f"""EXAMPLE — match the length, the follow-the-thread pattern, and the citation-anchoring discipline. Do NOT copy the literal phrases.
{name_a}: The thing I want to start with is something you mentioned in passing in the paper but didn't develop — the half-life of legacy expertise [S1]. Can you unpack what you actually meant by that?
{name_b}: Sure. We were looking at why migration projects went over budget, and we kept hitting a pattern: the team that started the project wasn't the team that finished it. People left, got rotated, the original architects moved on. By month six, half the institutional knowledge of what the legacy system actually did had walked out the door.
{name_a}: That's a striking framing. Did you find any team that escaped it?
{name_b}: Three teams in the sample. They all did the same thing — they documented as they went. Not afterward, not in a final write-up. Daily. The act of writing it down forced the team to externalize the model [S1].
{name_a}: Was that something they did intentionally, or did it emerge from the constraints they were under?
{name_b}: Two of the three said it was intentional from day one. The third — and this is the more interesting case — it emerged because they had a regulatory requirement to produce a paper trail. So they got the benefit accidentally.
{name_a}: That accidental case is actually the strongest evidence for the mechanism, isn't it? You can't argue it was a placebo effect if they didn't know it was supposed to help.
{name_b}: Exactly. That's why we lean on it in the discussion section.
{name_a}: I want to come back to the cost overrun number for a second. You report a wide range — 1.4x to 3.2x [S1]. What predicts where in that range a given team lands?
{name_b}: The honest answer is we don't fully know yet. The documentation variable explains roughly half the variance. The rest is signal we can see but haven't isolated.
{name_a}: Any speculation on what's in that residual?
{name_b}: My personal guess is the depth of original architectural review — but that's speculation, not finding.
{name_a}: That's exactly the kind of thing follow-up work could test."""

        _EXAMPLE_STORYTELLING = f"""EXAMPLE — match the length, the narrative arc, and the citation-anchoring discipline. Do NOT copy the literal phrases.
{name_a}: A small team at a mid-size bank — I'll call it Continental — agreed to a database migration that everyone on the floor knew was impossible. The original architects had moved on years earlier. The system had been holding together on tribal knowledge and three people who knew where the bodies were buried. Two of those three had given notice.
{name_b}: So they were starting the project with the institutional memory already walking out the door.
{name_a}: Exactly. And the project lead — Maya — knew it. She'd watched two similar migrations fail at her last job. She sat down on day one and wrote a memo to the team: every conversation about the legacy system gets written up the same day. Not next week. Not at the end. That day. Six months later, the migration completed under budget — the only one in the sample of fourteen that did [S1].
{name_b}: What was in the memo? What did people actually write down?
{name_a}: That's the interesting part. The team thought they were documenting the system. They were really documenting their own confusion. Every time someone said "I don't know why it does this" — that went into the file. By month three the file was eight hundred pages, and it had become the actual map.
{name_a}: The researchers studying this called it "externalizing the model" [S1] — but Maya called it something different. She called it "writing down what you'll forget by Friday."
{name_b}: That's the line, isn't it? That's the actual lesson.
{name_a}: It is. And the second study took the principle further — they argued that documentation alone isn't enough; you need outside review of the documentation as it's written [S2]. Maya's team did that too, almost by accident. She'd send the day's file to the team that owned the new system. Read this and tell me what doesn't make sense.
{name_b}: So the new-system team was effectively pressure-testing the legacy-system docs in real time.
{name_a}: Right. And when they came back with questions, those questions were exactly the gaps the legacy team hadn't realized they had. The migration didn't fail because they'd already simulated all the failure modes on paper.
{name_b}: That's the kind of thing that sounds obvious in retrospect.
{name_a}: Every good practice sounds obvious in retrospect. The point of these papers is to make obvious before the disaster instead of after it [S1][S2]."""

        _EXAMPLE_FEYNMAN = f"""EXAMPLE — match the length, the explain-then-check pattern, the analogy density, and the citation-anchoring discipline. Do NOT copy the literal phrases.
{name_a}: I want to start with something that confuses everyone. The idea is called "credit assignment" and it's the heart of reinforcement learning. So here's the picture — imagine you're training a dog. You yell "Good!" only at the end of a ten-minute trick. The dog has to figure out which step earned the praise. Which step? That's credit assignment [S1].
{name_b}: Wait. So the dog isn't told step three was the bad one. It just knows the whole sequence was bad or good.
{name_a}: Exactly. And the question — for the dog AND for the algorithm — is how to assign credit, or blame, to the individual steps when you only get feedback at the end.
{name_b}: Let me try to play it back. In supervised learning you get told the answer at every step. In reinforcement learning you only get told at the end. So the algorithm has to backpropagate the reward across all the actions that led to it.
{name_a}: Almost. Backpropagate is the right intuition but the wrong term — in RL we call it "temporal difference learning" or "Monte Carlo estimation" depending on flavor [S2]. The deeper question is: how MUCH credit does each action deserve?
{name_b}: The last action probably deserves more than the first one, because the first one was so long ago.
{name_a}: That's the standard intuition. And it's where most introductory treatments stop. But here's what makes it interesting — the first action might actually deserve MORE credit, because it set up the entire chain. Without action one, action ten couldn't have happened.
{name_b}: So the credit isn't just about recency.
{name_a}: Right. It's about counterfactual contribution. And we don't know how to compute that exactly, so we approximate.
{name_b}: Let me see if I can teach this back to you. Credit assignment is the problem of deciding which past action caused a delayed reward. We approximate it by some mix of recency weighting and counterfactual estimation. The hard part is that the truly important action might be the one furthest from the reward, not the closest.
{name_a}: That's the cleanest summary I've heard. One last piece: the failure mode of RL — when it doesn't converge — is almost always credit assignment going wrong. Either the algorithm credits the wrong actions, or it credits them at the wrong scale [S3].
{name_b}: So a debugging session is essentially asking "did the algorithm assign credit to actions that actually mattered, or just actions that happened close to the reward?"
{name_a}: That's exactly the question. Now you understand it."""

        _EXAMPLES = {
            "feynman_curriculum": _EXAMPLE_FEYNMAN,
            "debate": _EXAMPLE_DEBATE,
            "interview": _EXAMPLE_INTERVIEW,
            "storytelling": _EXAMPLE_STORYTELLING,
            "podcast_script": _EXAMPLE_PODCAST,
        }
        _EXAMPLE = _EXAMPLES.get(skill_id, _EXAMPLE_PODCAST)

        # ── Character intro rule (all styles) ──
        # Concise prompt with no canned opener — let each style's few-shot
        # demonstrate the actual opening move.
        _INTRO_RULE = f"""OPENING: Both speakers introduce themselves by name in the first 2-3 lines. Do not use generic show-host openers like "Welcome to the show" or "Today we're diving into" — the introductions should sound like two people about to talk, not a podcast intro template."""

        # ── Style-specific prompts ──
        is_feynman = skill_id == 'feynman_curriculum'

        if is_feynman:
            system_prompt = f"""You are writing a TEACHING podcast script. Every word will be spoken aloud by text-to-speech.

{_TTS_RULES}

HOST POV:
- {name_a} is the TEACHER — explains by building from a concrete everyday analogy, then layers nuance on top. Uses "imagine you're..." constructions. Quizzes {name_b} mid-stream: "Can you play that back to me?" or "What do you think happens next?" When {name_b} gets something slightly wrong, corrects it gently and shows WHY the right framing matters.
- {name_b} is the LEARNER — attempts to restate concepts in their own words. Gets things partly wrong on purpose so the teacher can correct. Asks "Is this right: ...?" rather than "Can you explain?". The structural move of Feynman style is the LEARNER TEACHING BACK and getting corrected — every section MUST contain at least one such attempt.

{_INTRO_RULE}

{_EXAMPLE}

{_GROUNDING}"""

        elif skill_id == 'debate':
            system_prompt = f"""You are writing a DEBATE podcast script. Every word will be spoken aloud by text-to-speech.

{_TTS_RULES}

HOST POV:
- {name_a} argues FOR the main thesis — leads with the strongest evidence, anchors each major claim to a specific [Sn] source, accepts a partial concession when forced.
- {name_b} argues AGAINST — does NOT just disagree; specifically REBUTS by either (a) showing {name_a}'s own source undermines their point, or (b) introducing a counter-finding with citation. Avoid generic "I disagree" — every rebuttal must name what is wrong with the argument and why.

OPENING:
{name_a}: I'm {name_a}, and I want to defend a position that I think the data actually supports more than people realize.
{name_b}: I'm {name_b}, and I'm going to push on the position because I think the same data shows the opposite when you read it carefully.

STRUCTURAL REQUIREMENTS:
- AT LEAST 2 explicit rebuttals where one host points out a flaw in the other's specific argument — not just stating a contrary view.
- AT LEAST 1 moment where one host concedes a partial point (debate without concessions feels rigged).
- Neither side wins cleanly; the conversation surfaces where the genuine disagreement lives.

{_EXAMPLE}

{_GROUNDING}"""

        elif skill_id == 'interview':
            system_prompt = f"""You are writing an INTERVIEW podcast script. Every word will be spoken aloud by text-to-speech.

{_TTS_RULES}

HOST POV:
- {name_a} is the INTERVIEWER — listens for the offhand remark and digs in. Builds questions FROM things {name_b} just said ("You mentioned X — can you unpack what you actually meant?"). Avoids prepared-list questions. The signature move is the callback — picking up something said 2-3 turns ago and asking about it now.
- {name_b} is the EXPERT — answers with specificity, owns uncertainty when it's there ("we don't fully know yet"), distinguishes finding from speculation. Drops one or two ideas in passing that {name_a} can pick up later.

OPENING:
{name_a}: I'm {name_a}. Today I'm sitting down with {name_b}, who's been deep in this work, and I want to start with something you said in passing in the paper.
{name_b}: Thanks for having me. Happy to unpack any of it.

STRUCTURAL REQUIREMENTS:
- AT LEAST 1 explicit callback: {name_a} picks up something {name_b} said earlier and asks about it.
- AT LEAST 1 moment where {name_b} admits the limits of the finding ("we don't know yet", "that's speculation").

{_EXAMPLE}

{_GROUNDING}"""

        elif skill_id == 'storytelling':
            system_prompt = f"""You are writing a STORYTELLING podcast script. Every word will be spoken aloud by text-to-speech.

{_TTS_RULES}

HOST POV:
- {name_a} is the STORYTELLER — sets a concrete scene with a named person, place, and stakes. Builds toward a turning point. Drops the research findings as part of the narrative ("the researchers later called this..."), not as a separate "and now for the data" section.
- {name_b} is the engaged LISTENER — asks the question a curious friend would ask, not the question a producer wants asked. Reflects back what stood out: "That's the lesson, isn't it?" or "So the actual lever was X."

{_INTRO_RULE}

STRUCTURAL REQUIREMENTS:
- Open with a concrete scene — a named person facing a specific problem at a specific time. NEVER open with "Picture this..." or "Here's something nobody saw coming..." (those are AI-script tells).
- One clear turning point in the middle where the situation changes.
- Final beat callbacks the opening — a phrase, a person, or the original stakes — so the story closes on itself.

{_EXAMPLE}

{_GROUNDING}"""

        else:
            # Standard two-host conversation (podcast_script and fallback)
            system_prompt = f"""You are writing a podcast script. Every word will be spoken aloud by text-to-speech.

{_TTS_RULES}

HOST POV:
- {name_a} is suspicious of headline framing and looks for the part of the finding that contradicts the obvious read. Pushes for what the data actually says when you read it twice. Specific by default — names the study, names the number, names the limit.
- {name_b} pushes for the practical implication and the failure mode. Asks "what breaks?" and "where does this not apply?". Plays back what {name_a} said in their own words and tests it against an edge case.

DO NOT USE THESE PHRASES — they appear in every AI-generated script and signal slop: "Wait, really?", "That's wild", "No way!", "Okay but here's the thing", "Then what happened?", "Hold on", "I had no idea". Build reactions from the actual content instead.

{_INTRO_RULE}

{_EXAMPLE}

{_GROUNDING}"""

        # ── Generation with retry ──
        # If first attempt is too short after cleanup, retry once before accepting
        max_attempts = 2
        best_script = None
        best_word_count = 0
        
        for attempt in range(max_attempts):
            try:
                if attempt > 0:
                    print(f"[AudioGen] Retry attempt {attempt + 1}/{max_attempts} — boosting temperature")
                
                temp = 0.8 + (attempt * 0.1)  # 0.8, then 0.9 on retry
                
                # Feynman always uses multi-pass with 4 curriculum parts
                if is_feynman:
                    script = await self._generate_feynman_multipass(
                        system_prompt, context, topic, duration_minutes, host_names=(name_a, name_b)
                    )
                elif duration_minutes >= 5:
                    # 5+ min of ANY style gets the per-style blueprint phase arc
                    # (A1 parity: was gated at >=7, so 5-6 min debates/interviews
                    # silently lost their structural arc and fell to a generic
                    # talking-points single pass).
                    script = await self._generate_script_multipass(
                        system_prompt, context, topic, duration_minutes,
                        host_names=(name_a, name_b), skill_id=skill_id
                    )
                else:
                    # Single-pass generation (genuinely short scripts < 5 min)
                    talking_points = await self._extract_talking_points(context, topic or '', num_points=10)
                    target_exchanges = max(8, target_words // 30)
                    
                    prompt = f"""Write a natural podcast conversation between {name_a} and {name_b} about these key points:

{talking_points}

Write at least {target_exchanges} back-and-forth exchanges. Keep going — do NOT wrap up early.

{name_a}:"""
                    
                    audio_num_predict = min(int(target_words * 2.5), 4000)
                    audio_num_ctx = max(8192, audio_num_predict + 4000)
                    script = await rag_engine._call_ollama(
                        system_prompt, prompt, model=settings.ollama_model,
                        num_predict=audio_num_predict, num_ctx=audio_num_ctx,
                        temperature=temp, repeat_penalty=1.15,
                        # Audio scripts need natural conversational flow; the
                        # generic "skip preamble / lead with conclusion" voice
                        # modifier would clip dialogue openings.
                        voice_modifier=False,
                    )
                
                # ── Quality Gate — structural enforcement before TTS ──
                script = self._validate_full_script(
                    script, duration_minutes, skill_id, host_names=(name_a, name_b)
                )
                
                # Diagnostic: track newline count through the pipeline
                nl_count = script.count('\n')
                line_count = len([l for l in script.split('\n') if l.strip()])
                print(f"[AudioGen] Post-validation: {nl_count} newlines, {line_count} non-empty lines")
                if nl_count == 0 and len(script.split()) > 50:
                    print(f"[AudioGen] ⚠⚠⚠ WARNING: Script has NO newlines after validation! ({len(script.split())} words on 1 line)")
                
                # Track best attempt
                wc = len(script.split())
                if wc > best_word_count:
                    best_script = script
                    best_word_count = wc
                
                # If we hit ≥60% of target, we're good — stop retrying
                if wc >= target_words * 0.6:
                    return script
                
                # Script passed but is short (25-60%) — try once more for a better result
                if attempt < max_attempts - 1:
                    print(f"[AudioGen] Script usable but short ({wc} words, {wc*100//target_words}% of target). Retrying for better result...")
                    continue
                
            except RuntimeError as e:
                # Quality gate hard-rejected (<25% of target)
                print(f"[AudioGen] Attempt {attempt + 1} rejected: {e}")
                if attempt < max_attempts - 1:
                    continue
                # Last attempt failed — if we have ANY previous usable script, use it
                if best_script:
                    print(f"[AudioGen] ⚠ Using best previous attempt ({best_word_count} words)")
                    return best_script
                raise  # No usable script at all
        
        # Return best attempt from all tries
        return best_script
    
    async def _extract_talking_points(self, context: str, topic: str, num_points: int = 15) -> str:
        """Step 1: Distill raw research into numbered talking points.
        
        Eliminates source regurgitation by converting raw PDF/web text into
        clean factual bullet points that the dialogue generator can discuss
        without copying metadata, page refs, or formatting.
        """
        import time
        from services.rag_engine import rag_engine
        t0 = time.time()
        
        max_ctx = min(len(context), 8000)
        result = await rag_engine._call_ollama(
            "You extract key facts and insights from research material. "
            "Output ONLY a numbered list. No commentary, no headers, no metadata.",
            f"Extract the {num_points} most important, interesting, and discussion-worthy "
            f"facts and insights from this research about {topic or 'the topic'}.\n"
            f"Each point: one specific factual claim or insight (1-2 sentences).\n"
            f"Format: 1. [point]  2. [point]  etc.\n"
            f"ONLY include facts stated in the research. Do NOT add opinions.\n\n"
            f"{context[:max_ctx]}",
            model=settings.ollama_model,
            num_predict=600,
            temperature=0.3,
        )
        
        # Clean: keep only numbered lines
        cleaned = []
        for line in result.strip().split('\n'):
            line = line.strip()
            if line and (line[0].isdigit() or line.startswith('-')):
                cleaned.append(line)
        
        points = '\n'.join(cleaned) if cleaned else result.strip()
        elapsed = time.time() - t0
        print(f"[AudioGen] Extracted {len(cleaned)} talking points ({len(points.split())} words) in {elapsed:.1f}s")
        return points
    
    async def _plan_section(
        self, talking_points: str, phase_instruction: str,
        prev_summary: str, phase_exchanges: int,
        name_a: str, name_b: str, skill_id: str = None,
    ) -> str:
        """Step 2: Chain-of-thought section planning.
        
        LLM outlines the section structure before writing dialogue:
        which points to cover, opening hook, key reactions, transitions.
        Keeps the generation organized and on-topic.
        """
        import time
        from services.rag_engine import rag_engine
        t0 = time.time()
        
        already_covered = ""
        if prev_summary:
            already_covered = f"\nAlready discussed (skip these): {prev_summary}\n"
        
        result = await rag_engine._call_ollama(
            "You are a podcast script planner. Output a brief section outline. "
            "No dialogue, no speaker labels — just the plan.",
            f"Plan a {phase_exchanges}-exchange podcast section.\n"
            f"Section goal: {phase_instruction}\n"
            f"{already_covered}\n"
            f"Available talking points:\n{talking_points}\n\n"
            f"Write a 5-8 line outline:\n"
            f"- Opening: How {name_a} starts this section (hook or transition)\n"
            f"- Points: Which 3-5 talking points to cover, in what order\n"
            f"- Reactions: Key questions or reactions from {name_b}\n"
            f"- Closing: How to transition to the next section\n",
            num_predict=300,
            temperature=0.4,
        )
        
        elapsed = time.time() - t0
        print(f"[AudioGen] Section plan: {len(result.split())} words in {elapsed:.1f}s")
        return result.strip()
    
    async def _generate_script_multipass(
        self,
        system_prompt: str,
        context: str,
        topic: Optional[str],
        duration_minutes: int,
        host_names: Optional[Tuple[str, str]] = None,
        skill_id: Optional[str] = None,
    ) -> str:
        """Blueprint-driven script generation with per-section validation and retry.
        
        Uses SCRIPT_BLUEPRINTS to define structural phases (intro/body/outro),
        validates each section, retries on failure, and tracks word budgets
        dynamically so the total hits the target duration.
        """
        blueprint_key = skill_id or 'podcast_script'
        blueprint = self.SCRIPT_BLUEPRINTS.get(blueprint_key, self.SCRIPT_BLUEPRINTS['podcast_script'])
        phases = blueprint['phases']
        total_target = duration_minutes * self.TTS_WORDS_PER_MINUTE
        
        name_a = host_names[0] if host_names else "Host A"
        name_b = host_names[1] if host_names else "Host B"
        
        print(f"[AudioGen] Blueprint '{blueprint_key}': {len(phases)} phases, target {total_target} words ({duration_minutes} min)")
        for p in phases:
            print(f"   {p['name']}: {int(p['pct'] * total_target)} words ({p['pct']:.0%})")
        
        # ── Extract talking points ONCE (no raw research in dialogue prompts) ──
        talking_points = await self._extract_talking_points(context, topic or '', num_points=15)
        
        sections = []
        total_words_so_far = 0
        covered_topics = []  # Simple topic tracking (no LLM summary calls)
        
        for i, phase in enumerate(phases):
            is_first = i == 0
            is_last = i == len(phases) - 1
            
            # Dynamic budget: phase percentage of total, adjusted by what's left
            remaining_words = total_target - total_words_so_far
            remaining_pct = sum(p['pct'] for p in phases[i:])
            phase_budget = max(self.TTS_WORDS_PER_MINUTE, int(remaining_words * (phase['pct'] / remaining_pct))) if remaining_pct > 0 else self.TTS_WORDS_PER_MINUTE
            phase_exchanges = max(8, int(phase_budget / 30))
            
            # Continuity: last few lines + simple topic list (no LLM summary)
            prev_context = ""
            if sections:
                last_lines = sections[-1].strip().split('\n')[-4:]
                prev_context = f"\n\nTopics already covered (don't repeat): {', '.join(covered_topics[-8:])}"
                prev_context += f"\n\nContinue from:\n" + "\n".join(last_lines)
            
            # Direct prompt — no plan step, no word counts, just exchange target
            prompt = f"""{phase['instruction']}

Key facts to discuss:
{talking_points}

Write at least {phase_exchanges} back-and-forth exchanges between {name_a} and {name_b}. Keep going — do NOT wrap up early.
{prev_context}

{name_a}:"""
            
            section_predict = min(int(phase_budget * 2.5), 4000)
            section_ctx = max(8192, section_predict + 4000)
            
            section_script = await self._generate_section_with_retry(
                system_prompt=system_prompt,
                prompt=prompt,
                word_budget=phase_budget,
                phase_name=phase['name'],
                host_names=host_names,
                num_predict=section_predict,
                num_ctx=section_ctx,
                temperature=0.8,
                repeat_penalty=1.15,
            )
            
            section_words = len(section_script.split())
            total_words_so_far += section_words
            sections.append(section_script)
            print(f"   Phase '{phase['name']}': {section_words} words (budget: {phase_budget}, total: {total_words_so_far}/{total_target})")
            
            # Track topics from this section (simple keyword extraction, no LLM call)
            section_lower = section_script.lower()
            for tp in talking_points.split('\n'):
                # Extract first few words of each talking point as a topic marker
                words = tp.strip().lstrip('0123456789.-) ').split()[:5]
                if words and any(w.lower() in section_lower for w in words if len(w) > 4):
                    covered_topics.append(' '.join(words[:3]))
        
        SECTION_BREAK = "\n\n---SECTION_BREAK---\n\n"
        return SECTION_BREAK.join(sections)
    
    async def _generate_feynman_multipass(
        self,
        system_prompt: str,
        context: str,
        topic: Optional[str],
        duration_minutes: int,
        host_names: Optional[Tuple[str, str]] = None
    ) -> str:
        """Blueprint-driven Feynman teaching podcast with validation and retry.
        
        Uses the feynman_curriculum blueprint for phase structure, plus
        Feynman-specific detailed instructions per part. Each part validated
        and retried on failure.
        """
        name_a = host_names[0] if host_names else "Host A"
        name_b = host_names[1] if host_names else "Host B"
        total_target = duration_minutes * self.TTS_WORDS_PER_MINUTE
        
        # Feynman-specific detailed instructions per part
        feynman_details = [
            (
                f"{name_a} introduces the topic and explains core concepts simply — like to a 12-year-old. "
                f"Use everyday analogies. {name_b} is a beginner asking 'what is this?' and 'why should I care?'. "
                f"End with {name_a} checking: 'So tell me in your own words, what is X?' and {name_b} attempts to explain back."
            ),
            (
                f"Now that {name_b} gets the basics, go deeper. {name_a} shows how concepts connect and gives real-world examples. "
                f"{name_b} makes connections: 'Oh, so it's kind of like when...'. "
                f"Address misconceptions — {name_b} voices one, {name_a} corrects gently. "
                f"End with a harder check question testing whether {name_b} sees the connections."
            ),
            (
                f"Go beyond what to WHY. {name_a} explains root mechanisms and underlying principles. "
                f"{name_b} pushes back with 'but why?' and 'what if?' questions. Discuss edge cases. "
                f"{name_b} should be noticeably more confident now. "
                f"End with an analysis question: 'Why does X happen instead of Y?'"
            ),
            (
                f"Roles partially flip — {name_b} tries to teach the subject back to {name_a} (the Feynman test). "
                f"{name_a} plays skeptical student, asking tough questions and poking holes. "
                f"{name_b} synthesizes everything from earlier parts. Discuss what's still unknown. "
                f"End with both reflecting on what they learned and {name_a} suggesting where to go next."
            ),
        ]
        
        blueprint = self.SCRIPT_BLUEPRINTS['feynman_curriculum']
        phases = blueprint['phases']
        
        print(f"[AudioGen] Feynman blueprint: 4 phases, target {total_target} words ({duration_minutes} min)")
        
        # ── Extract talking points ONCE ──
        talking_points = await self._extract_talking_points(context, topic or '', num_points=15)
        
        sections = []
        total_words_so_far = 0
        covered_topics = []  # Simple topic tracking (no LLM summary calls)
        
        for i, phase in enumerate(phases):
            is_last = i == len(phases) - 1
            
            # Dynamic budget
            remaining_words = total_target - total_words_so_far
            remaining_pct = sum(p['pct'] for p in phases[i:])
            phase_budget = max(self.TTS_WORDS_PER_MINUTE, int(remaining_words * (phase['pct'] / remaining_pct))) if remaining_pct > 0 else self.TTS_WORDS_PER_MINUTE
            detail = feynman_details[i] if i < len(feynman_details) else phase['instruction']
            phase_exchanges = max(8, int(phase_budget / 30))
            
            # Continuity: last few lines + simple topic list (no LLM summary)
            prev_context = ""
            if sections:
                last_lines = sections[-1].strip().split('\n')[-4:]
                prev_context = f"\n\nTopics already covered (don't repeat): {', '.join(covered_topics[-8:])}"
                prev_context += f"\n\nContinue from:\n" + "\n".join(last_lines)
            
            # Direct prompt — no plan step, no word counts
            prompt = f"""{detail}

Key facts to discuss:
{talking_points}

Write at least {phase_exchanges} back-and-forth exchanges between {name_a} and {name_b}. Keep going — do NOT wrap up early.
{prev_context}

{name_a}:"""
            
            section_predict = min(int(phase_budget * 2.5), 4000)
            section_ctx = max(8192, section_predict + 4000)
            part_repeat_penalty = 1.1 + (i * 0.05)  # 1.1, 1.15, 1.2, 1.25
            
            section_script = await self._generate_section_with_retry(
                system_prompt=system_prompt,
                prompt=prompt,
                word_budget=phase_budget,
                phase_name=phase['name'],
                host_names=host_names,
                num_predict=section_predict,
                num_ctx=section_ctx,
                temperature=0.8,
                repeat_penalty=part_repeat_penalty,
            )
            
            section_words = len(section_script.split())
            total_words_so_far += section_words
            sections.append(section_script)
            print(f"   Part {i+1}/4 ({phase['name']}): {section_words} words (budget: {phase_budget}, total: {total_words_so_far}/{total_target})")
            
            # Track topics from this section (simple keyword extraction, no LLM call)
            section_lower = section_script.lower()
            for tp in talking_points.split('\n'):
                words = tp.strip().lstrip('0123456789.-) ').split()[:5]
                if words and any(w.lower() in section_lower for w in words if len(w) > 4):
                    covered_topics.append(' '.join(words[:3]))
        
        SECTION_BREAK = "\n\n---SECTION_BREAK---\n\n"
        return SECTION_BREAK.join(sections)

    def _sanitize_script_output(self, script: str, target_words: int) -> str:
        """Per-section sanitizer — catches the worst LLM failures early.
        
        Runs BEFORE validation. Strips everything that isn't spoken dialogue
        so the section validator sees an accurate word count.
        """
        # ── Model-specific artifact stripping ──
        # Gemma 4 thinking tokens (must strip BEFORE other processing)
        script = re.sub(r'<\|channel\|>.*?<\|channel\|>', '', script, flags=re.DOTALL)
        script = re.sub(r'<\|channel>thought.*?<\|channel\|>', '', script, flags=re.DOTALL)
        
        # ── Leaked prompt/context metadata ──
        script = re.sub(r'Research content:.*$', '', script, flags=re.MULTILINE | re.IGNORECASE)
        script = re.sub(r'Use ONLY facts from this research:.*$', '', script, flags=re.MULTILINE | re.IGNORECASE)
        script = re.sub(r'---\s*RECENT CHAT\s*---.*$', '', script, flags=re.MULTILINE | re.IGNORECASE)
        script = re.sub(r'You already discussed these topics.*$', '', script, flags=re.MULTILINE | re.IGNORECASE)
        script = re.sub(r'Pick up naturally from where you left off.*$', '', script, flags=re.MULTILINE | re.IGNORECASE)
        script = re.sub(r'Pick up from where you left off.*$', '', script, flags=re.MULTILINE | re.IGNORECASE)
        script = re.sub(r'Follow this plan:.*$', '', script, flags=re.MULTILINE | re.IGNORECASE)
        script = re.sub(r'Section goal:.*$', '', script, flags=re.MULTILINE | re.IGNORECASE)
        # Chain-of-thought plan fragments that leak into dialogue
        script = re.sub(r'\bOpening:\s+(?:How\s+)?\w+\s+starts?\b.*$', '', script, flags=re.MULTILINE | re.IGNORECASE)
        script = re.sub(r'\bPoints:\s+(?:Which\s+)?\d.*$', '', script, flags=re.MULTILINE | re.IGNORECASE)
        script = re.sub(r'\bReactions:\s+.*$', '', script, flags=re.MULTILINE | re.IGNORECASE)
        script = re.sub(r'\bClosing:\s+.*$', '', script, flags=re.MULTILINE | re.IGNORECASE)
        script = re.sub(r'\b\w+\s+Expert (?:on|Guide on)\b[^.!?\n]*$', '', script, flags=re.MULTILINE | re.IGNORECASE)
        script = re.sub(r'\bstarts? off by introducing\b.*$', '', script, flags=re.MULTILINE | re.IGNORECASE)
        script = re.sub(r'\bfollows up,? mentioning\b.*$', '', script, flags=re.MULTILINE | re.IGNORECASE)
        script = re.sub(r'\breacts to this information\b.*$', '', script, flags=re.MULTILINE | re.IGNORECASE)
        script = re.sub(r'\bexpressing surprise at\b.*$', '', script, flags=re.MULTILINE | re.IGNORECASE)
        script = re.sub(r'TOPICS ALREADY COVERED.*$', '', script, flags=re.MULTILINE | re.IGNORECASE)
        script = re.sub(r'PREVIOUS SECTION ENDED WITH.*$', '', script, flags=re.MULTILINE | re.IGNORECASE)
        script = re.sub(r'PHASE:\s*\w+.*$', '', script, flags=re.MULTILINE | re.IGNORECASE)
        script = re.sub(r'WORD COUNT:.*$', '', script, flags=re.MULTILINE | re.IGNORECASE)
        script = re.sub(r'STYLE RULES:.*$', '', script, flags=re.MULTILINE | re.IGNORECASE)
        script = re.sub(r'STOP writing after.*$', '', script, flags=re.MULTILINE | re.IGNORECASE)
        # Word count annotations the model outputs inline: (156 words), (1628 words)
        script = re.sub(r'\(\d+\s*words?\)', '', script, flags=re.IGNORECASE)
        # Orphan lowercase speaker names at end of lines (e.g. "ruby" after stripping)
        script = re.sub(r'\b[a-z]{3,}\s*$', '', script, flags=re.MULTILINE)
        script = re.sub(r'^User\s+\d+/\d+\s+©.*$', '', script, flags=re.MULTILINE)
        script = re.sub(r'https?://\S+', '', script)
        script = re.sub(r'www\.\S+', '', script)
        script = re.sub(r'©.*$', '', script, flags=re.MULTILINE)
        script = re.sub(r'\[\d+\]', '', script)  # Citations [1], [2]
        # Source metadata that LLM regurgitates from PDFs
        script = re.sub(r'^.*===.*===.*$', '', script, flags=re.MULTILINE)  # === Medium PDF ===
        script = re.sub(r'^.*---\s*PAGE.*---.*$', '', script, flags=re.MULTILINE | re.IGNORECASE)
        script = re.sub(r'^\s*Pages?\s*referenced.*$', '', script, flags=re.MULTILINE | re.IGNORECASE)
        script = re.sub(r'^\s*==\s*Page.*==\s*$', '', script, flags=re.MULTILINE | re.IGNORECASE)
        script = re.sub(r'^\s*Image by\b.*$', '', script, flags=re.MULTILINE | re.IGNORECASE)
        script = re.sub(r'^\s*Note:\s*This\b.*$', '', script, flags=re.MULTILINE | re.IGNORECASE)
        script = re.sub(r'^\s*Source\s*[:(].*$', '', script, flags=re.MULTILINE | re.IGNORECASE)
        script = re.sub(r'\bCreative Commons\b.*$', '', script, flags=re.MULTILINE | re.IGNORECASE)
        script = re.sub(r'\bPublic Domain\b.*$', '', script, flags=re.MULTILINE | re.IGNORECASE)
        script = re.sub(r'^\s*For (?:a deeper|more detailed)\b.*$', '', script, flags=re.MULTILINE | re.IGNORECASE)
        script = re.sub(r'\[Access \w+\]', '', script, flags=re.IGNORECASE)
        
        # ── Stage directions, silence markers, meta-commentary ──
        # These inflate word count but aren't dialogue
        script = re.sub(r'\([^)]*(?:transition|silence|pause|laughs?|sighs?|clears throat|music|continues|shifts?|switches?|opens?|closes?|concludes|beat)[^)]*\)', '', script, flags=re.IGNORECASE)
        script = re.sub(r'\[[^\]]*(?:silence|pause|minutes?|seconds?|music|transition|intro|outro)[^\]]*\]', '', script, flags=re.IGNORECASE)
        script = re.sub(r'--+\s*End\s+Transcript\s*--+', '', script, flags=re.IGNORECASE)
        script = re.sub(r'--+\s*End\s+(?:of\s+)?(?:Episode|Segment|Section|Script)\s*--+', '', script, flags=re.IGNORECASE)
        script = re.sub(r'^.*(?:Continue writing|Script generated|next segment will|will be automatically generated).*$', '', script, flags=re.MULTILINE | re.IGNORECASE)
        script = re.sub(r'^\s*\((?:The\s+)?podcast\s+(?:concludes|ends|wraps).*$', '', script, flags=re.MULTILINE | re.IGNORECASE)
        # Meta-lines that describe what should happen instead of being dialogue
        script = re.sub(r'^\s*(?:Closing|Opening|Next)\s+segment\s+.*$', '', script, flags=re.MULTILINE | re.IGNORECASE)
        # Parenthesized speaker labels → proper labels
        script = re.sub(r'\((\w+)\)\s*:', r'\n\1:', script)
        # Strip empty speaker turns (just "Edward:" with nothing after)
        script = re.sub(r'^\s*\w+\s*:\s*$', '', script, flags=re.MULTILINE)
        # Strip markdown bold/italic markers
        script = re.sub(r'\*\*(.+?)\*\*', r'\1', script)
        script = re.sub(r'(?<!\w)\*\s+', '', script)
        script = re.sub(r'\*(.+?)\*', r'\1', script)
        
        # ── Degenerate text: run-on lines ──
        lines = script.split('\n')
        cleaned_lines = []
        for line in lines:
            stripped = line.strip()
            if not stripped:
                continue
            line_words = len(stripped.split())
            if line_words > 40:
                punct = stripped.count('.') + stripped.count('!') + stripped.count('?')
                min_punct = max(1, line_words // 18)
                if punct < min_punct:
                    first_sent = re.match(r'^(.*?[.!?])\s', stripped)
                    if first_sent and len(first_sent.group(1).split()) > 5:
                        line = first_sent.group(1)
                    else:
                        line = ' '.join(stripped.split()[:30]) + '.'
                    print(f"[AudioGen] ⚠ Truncated run-on line ({line_words} words)")
            cleaned_lines.append(line)
        
        script = '\n'.join(cleaned_lines)
        
        # Hard word cap: truncate to 130% of target
        word_count = len(script.split())
        max_words = int(target_words * 1.3)
        if word_count > max_words:
            script = self._truncate_script_to_words(script, max_words)
            print(f"[AudioGen] ⚠ Section trimmed from {word_count} to {max_words} words")
        
        return script
    
    def _truncate_script_to_words(self, script: str, max_words: int) -> str:
        """Truncate script at a natural boundary near max_words.
        
        Tries to cut at: speaker turn boundary > sentence boundary > word boundary.
        """
        words = script.split()
        if len(words) <= max_words:
            return script
        
        # Build truncated text
        truncated = ' '.join(words[:max_words])
        
        # Try to find the last complete speaker turn (line starting with a name + colon)
        lines = truncated.split('\n')
        # Walk backwards to find the last complete speaker turn
        for i in range(len(lines) - 1, max(0, len(lines) - 10), -1):
            if re.match(r'^[\s*]*\w+\s*:', lines[i]):
                return '\n'.join(lines[:i + 1])
        
        # Fall back to last sentence boundary
        last_period = truncated.rfind('.')
        last_question = truncated.rfind('?')
        last_excl = truncated.rfind('!')
        cut_point = max(last_period, last_question, last_excl)
        if cut_point > len(truncated) * 0.7:
            return truncated[:cut_point + 1]
        
        return truncated

    # ═══════════════════════════════════════════════════════════════════════
    # Script Validation — structural enforcement, not hope
    # ═══════════════════════════════════════════════════════════════════════

    def _validate_section(self, text: str, word_budget: int, phase_name: str,
                          host_names: Optional[Tuple[str, str]] = None) -> Tuple[bool, List[str]]:
        """Validate a generated section against structural requirements.
        
        Returns (passed, list_of_error_strings).
        A section passes if it has no critical errors.
        """
        errors = []
        words = text.split()
        word_count = len(words)

        # 1. Word count within tolerance — widened per active model's
        #    audio_profile.multi_pass_word_tolerance (e.g. 1.3 for Gemma's
        #    verbose prose, 1.0-1.1 for olmo's tighter output).
        effective_tolerance = min(0.95, self.SECTION_WORD_TOLERANCE * self._audio_profile_multiplier())
        min_words = int(word_budget * (1 - effective_tolerance))
        max_words = int(word_budget * (1 + effective_tolerance))
        if word_count < min_words:
            errors.append(f"TOO_SHORT: {word_count} words (need {min_words}-{max_words})")
        if word_count > max_words:
            errors.append(f"TOO_LONG: {word_count} words (need {min_words}-{max_words})")
        
        # 2. Degeneration detection — lines with 500+ chars and few sentence endings
        for i, line in enumerate(text.split('\n')):
            if len(line) > self.DEGENERATE_LINE_THRESHOLD:
                punct = line.count('.') + line.count('!') + line.count('?')
                line_words = len(line.split())
                expected = max(1, line_words // 20)
                if punct < expected // 3:
                    errors.append(f"DEGENERATE_LINE: line {i+1} has {line_words} words with only {punct} sentence endings")
        
        # 3. Speaker alternation — need both speakers present
        name_a = host_names[0] if host_names else "Host A"
        name_b = host_names[1] if host_names else "Host B"
        has_a = bool(re.search(rf'^{re.escape(name_a)}\s*:', text, re.MULTILINE | re.IGNORECASE))
        has_b = bool(re.search(rf'^{re.escape(name_b)}\s*:', text, re.MULTILINE | re.IGNORECASE))
        if not has_a and not has_b:
            # Check for Host A/B or A:/B: fallback labels
            has_a = bool(re.search(r'^(?:Host\s*)?A\s*:', text, re.MULTILINE | re.IGNORECASE))
            has_b = bool(re.search(r'^(?:Host\s*)?B\s*:', text, re.MULTILINE | re.IGNORECASE))
        if not has_a or not has_b:
            errors.append(f"MISSING_SPEAKER: {'A' if not has_a else 'B'} not found in section")
        
        # 4. Forbidden patterns
        assistant_count = len(re.findall(r'^Assistant\s*:', text, re.MULTILINE | re.IGNORECASE))
        if assistant_count > 0:
            errors.append(f"FORBIDDEN_LABEL: 'Assistant:' used {assistant_count} times as speaker label")
        
        # 5. Minimum turns
        turn_count = len(re.findall(r'^\s*\w+\s*:', text, re.MULTILINE))
        if turn_count < self.MIN_TURNS_PER_SECTION:
            errors.append(f"FEW_TURNS: only {turn_count} speaker turns (need ≥{self.MIN_TURNS_PER_SECTION})")
        
        passed = not any(e.startswith(('DEGENERATE', 'FORBIDDEN')) for e in errors)
        # Word count: hard fail if too short (<50% budget) or way too long (>2x)
        if word_count < word_budget * 0.5:
            passed = False
        if word_count > word_budget * 2:
            passed = False
        
        return passed, errors

    async def _generate_section_with_retry(
        self,
        system_prompt: str,
        prompt: str,
        word_budget: int,
        phase_name: str,
        host_names: Optional[Tuple[str, str]] = None,
        num_predict: int = 2000,
        num_ctx: int = 8192,
        temperature: float = 0.8,
        repeat_penalty: float = 1.15,
    ) -> str:
        """Generate a script section with validation and one retry on failure.
        
        If the first attempt fails validation, retries once with targeted feedback
        about what went wrong. Returns the best result.
        """
        name_a = host_names[0] if host_names else "Host A"
        
        for attempt in range(1 + self.MAX_SECTION_RETRIES):
            section = await rag_engine._call_ollama(
                system_prompt, prompt, model=settings.ollama_model,
                num_predict=num_predict, num_ctx=num_ctx,
                temperature=temperature, repeat_penalty=repeat_penalty,
                voice_modifier=False,  # podcast section: dialogue flow
            )
            
            # Always sanitize (catches degenerate lines)
            section = self._sanitize_script_output(section, word_budget)
            
            # Validate
            passed, errors = self._validate_section(section, word_budget, phase_name, host_names)
            
            if passed and not errors:
                return section
            
            error_summary = '; '.join(errors)
            print(f"[AudioGen] Section '{phase_name}' attempt {attempt+1}: {error_summary}")
            
            if passed:
                # Passed with warnings — acceptable, return it
                return section
            
            if attempt < self.MAX_SECTION_RETRIES:
                # Build retry prompt with specific feedback
                feedback_lines = []
                for e in errors:
                    if e.startswith('TOO_SHORT'):
                        feedback_lines.append(f"Your previous attempt was too short. Write MORE — you need {word_budget} words.")
                    elif e.startswith('TOO_LONG'):
                        feedback_lines.append(f"Your previous attempt was too long. Write EXACTLY {word_budget} words, then STOP.")
                    elif e.startswith('DEGENERATE'):
                        feedback_lines.append("Your previous attempt contained rambling word lists. Write natural spoken sentences with proper punctuation.")
                    elif e.startswith('FORBIDDEN'):
                        feedback_lines.append(f"NEVER use 'Assistant:' as a speaker label. Only use {name_a}: and the other host's name.")
                    elif e.startswith('MISSING_SPEAKER'):
                        feedback_lines.append("Both speakers must appear in every section. Alternate between them.")
                    elif e.startswith('FEW_TURNS'):
                        feedback_lines.append("Too few speaker turns. Each section needs at least 4 back-and-forth exchanges.")
                
                retry_feedback = '\n'.join(feedback_lines)
                prompt = f"""RETRY — your previous attempt had issues:
{retry_feedback}

{prompt}"""
                print(f"[AudioGen] Retrying section '{phase_name}'...")
        
        # Return whatever we have after retries
        return section

    def _validate_full_script(self, script: str, duration_minutes: int,
                               skill_id: Optional[str],
                               host_names: Optional[Tuple[str, str]] = None) -> str:
        """Final quality gate — comprehensive structural enforcement before TTS.
        
        8-phase pipeline that catches every class of LLM failure:
        1. Strip leaked prompt/context metadata
        2. Strip non-spoken artifacts (markdown, citations, stage directions)
        3. Detect and remove repetition loops (repeated n-grams)
        4. Detect and truncate degenerate text (run-on word salad)
        5. Replace forbidden speaker labels (Assistant:, User:)
        6. Break monologues into turns (max words per turn enforcement)
        7. Enforce total word count (hard cap)
        8. Report and reject if unsalvageable
        
        Returns cleaned script. Raises RuntimeError if unsalvageable.
        """
        target_words = duration_minutes * self.TTS_WORDS_PER_MINUTE
        blueprint = self.SCRIPT_BLUEPRINTS.get(skill_id or 'podcast_script',
                                                self.SCRIPT_BLUEPRINTS['podcast_script'])
        name_a = host_names[0] if host_names else "Host A"
        name_b = host_names[1] if host_names else "Host B"
        repairs = []
        before_words = len(script.split())
        
        # ── Phase 0: Normalize speaker label separators ──
        # The LLM sometimes uses hyphens instead of colons for speaker labels.
        # "George-Hey" → "George: Hey", but only at line starts.
        script = re.sub(
            rf'^({re.escape(name_a)}|{re.escape(name_b)})\s*[-–—]\s*',
            r'\1: ',
            script,
            flags=re.MULTILINE | re.IGNORECASE
        )
        # Also normalize "User-" and "Source-" and "Assistant-" labels
        script = re.sub(r'^(User|Assistant|Source)\s*[-–—]\s*', r'\1: ', script, flags=re.MULTILINE | re.IGNORECASE)
        
        # ── Phase 0.5: Split mega-lines ──
        # The LLM sometimes crams multiple speaker turns onto one line.
        speaker_names = [re.escape(name_a), re.escape(name_b),
                         r'Host\s*[AB]', r'Speaker\s*[12]', 'Assistant', 'User']
        speaker_pattern = '|'.join(speaker_names)
        script = re.sub(
            rf'(?<=\S)\s+({speaker_pattern})\s*:',
            rf'\n\1:',
            script,
            flags=re.IGNORECASE
        )
        
        # ── Phase 1: Normalize garbled speaker names ──
        # The LLM frequently corrupts speaker names across sections:
        # "Georg", "Giorge", "Geo-ger", "Georoa" → "George"
        # "ISllA", "Illa", "Isaia" → "Isla"
        # Use difflib for fuzzy matching against the two host names.
        from difflib import SequenceMatcher
        lines = script.split('\n')
        name_fixes = 0
        normalized_lines = []
        garbled_to_correct = {}  # Track garbled→correct for text-body replacement
        for line in lines:
            # Match any word-like label at line start followed by :
            m = re.match(r'^([A-Za-z][\w\-]*(?:\s+[\w\-]+)?)\s*[:]\s*', line)
            if m:
                label = m.group(1).strip()
                label_lower = re.sub(r'[\s\-]', '', label).lower()
                na_lower = name_a.lower()
                nb_lower = name_b.lower()
                
                # Skip if already correct
                if label_lower == na_lower or label_lower == nb_lower:
                    normalized_lines.append(line)
                    continue
                
                # Skip known non-speaker labels
                if label_lower in ('source', 'user', 'assistant', 'narrator', 'host'):
                    normalized_lines.append(line)
                    continue
                
                # Fuzzy match against both host names
                score_a = SequenceMatcher(None, label_lower, na_lower).ratio()
                score_b = SequenceMatcher(None, label_lower, nb_lower).ratio()
                
                # Also check shared prefix (handles "Georg" → "George")
                prefix_a = 0
                for c1, c2 in zip(label_lower, na_lower):
                    if c1 == c2: prefix_a += 1
                    else: break
                prefix_b = 0
                for c1, c2 in zip(label_lower, nb_lower):
                    if c1 == c2: prefix_b += 1
                    else: break
                
                best_name = None
                if score_a >= 0.5 or prefix_a >= 3:
                    if score_a > score_b or prefix_a > prefix_b:
                        best_name = name_a
                if score_b >= 0.5 or prefix_b >= 3:
                    if score_b > score_a or prefix_b > prefix_a:
                        best_name = name_b
                
                if best_name and label != best_name:
                    # Use m.end(0) to skip past the full "Label: " match
                    line = f"{best_name}: {line[m.end(0):]}"
                    garbled_to_correct[label] = best_name
                    name_fixes += 1
            normalized_lines.append(line)
        
        if name_fixes > 0:
            script = '\n'.join(normalized_lines)
            repairs.append(f"Normalized {name_fixes} garbled speaker names")
        
        # Also replace garbled name variants within dialogue TEXT (not just labels).
        # Scan ALL capitalized words in the script for fuzzy matches, not just
        # the ones we found as line-start labels.
        all_caps_words = set(re.findall(r'\b([A-Z][a-zA-Z\-]{2,})\b', script))
        text_replacements = dict(garbled_to_correct)  # Start with label findings
        for word in all_caps_words:
            if word in (name_a, name_b):
                continue
            word_lower = re.sub(r'[\s\-]', '', word).lower()
            if word_lower in ('source', 'user', 'assistant', 'narrator', 'host',
                              'the', 'and', 'but', 'for', 'not', 'are', 'was',
                              'right', 'true', 'well', 'let', 'hey', 'now'):
                continue
            sa = SequenceMatcher(None, word_lower, name_a.lower()).ratio()
            sb = SequenceMatcher(None, word_lower, name_b.lower()).ratio()
            if sa >= 0.5 and sa > sb:
                text_replacements[word] = name_a
            elif sb >= 0.5 and sb > sa:
                text_replacements[word] = name_b
        
        if text_replacements:
            for garbled, correct in text_replacements.items():
                if garbled != correct:
                    script = re.sub(rf'\b{re.escape(garbled)}\b', correct, script)
            repairs.append(f"Fixed {len(text_replacements)} name variants in text")
        
        # ── Phase 2: Strip leaked prompt/context metadata ──
        leaked_patterns = [
            (r'Research content:.*$', 'Research content: block'),
            (r'---\s*RECENT CHAT\s*---.*$', 'RECENT CHAT block'),
            (r'TOPICS ALREADY COVERED.*$', 'TOPICS ALREADY COVERED block'),
            (r'PREVIOUS SECTION ENDED WITH.*$', 'PREVIOUS SECTION block'),
            (r'PHASE:\s*\w+.*$', 'PHASE instruction'),
            (r'WORD COUNT:.*$', 'WORD COUNT instruction'),
            (r'STYLE RULES:.*$', 'STYLE RULES instruction'),
            (r'STOP writing after.*$', 'STOP instruction'),
            (r'^User\s+\d+/\d+\s+©.*$', 'Source copyright header'),
            (r'https?://\S+', 'URL'),
            (r'www\.\S+', 'URL'),
            (r'©\s+\S+', 'Copyright notice'),
            # Source/book metadata the LLM regurgitates from PDF content
            (r'^\s*Source\s*[:(].*$', 'Source reference'),
            (r'^.*===.*===.*$', 'Section marker'),
            (r'^.*---\s*PAGE.*---.*$', 'Page separator'),
            (r'^\s*==\s*Page.*==\s*$', 'Page marker'),
            (r'^\s*Pages?\s*referenced.*$', 'Page reference'),
            (r'^\s*Page\s+\w+(?:/\w+)+.*$', 'Page reference'),
            (r'^\s*Pages?\s+(?:One|Two|Three|Four|Five|Six|Seven|Eight|Nine|Ten|Eleven|Twelve|Thirteen|Fourteen|Fifteen|Sixteen|Seventeen|Eighteen|Nineteen|Twenty).*$', 'Spelled-out page reference'),
            (r'^\s*Image\s+by\b.*$', 'Image credit'),
            (r'^\s*Note:\s*This\b.*$', 'Meta note'),
            (r'^\s*For\s+(?:a\s+deeper|more\s+detailed)\b.*$', 'Meta note'),
            (r'\bCreative\s+Commons\b.*$', 'License text'),
            (r'\bPublic\s+Domain\b.*$', 'License text'),
            (r'\[Access\s+\w+\]', 'Access link'),
            (r'^\s*Images\s+From\s+the\s+Book\s*$', 'Book metadata'),
            (r'^\s*Prompt\s+Library\s*$', 'Book metadata'),
            (r'^\s*(?:Ai|AI)\s+Readiness\s+Assessment\s*$', 'Book metadata'),
            (r'^\s*Acknowledgements?\s*$', 'Book metadata'),
            (r'^\s*(?:Geoffrey|Geoff)\s+(?:F\.\s+)?Wood(?:s|son)\s*[-–—]?\s*(?:Founder|Author|CEO)?\s*.*$', 'Author attribution'),
            (r'^\s*\[?source\s*\d*\]?\s*$', 'Source reference'),
            (r'\bas\s+(?:noted|mentioned|described|stated)\s+(?:in\s+)?(?:source|the\s+(?:book|pdf|document))\b', 'Source citation phrase'),
            # Inline website/org names the LLM regurgitates from sources
            (r'\b[Aa][Ii][Ll][Ee][Aa][Dd][Ee][Rr][Ss][Hh][Ii][Pp](?:\.com)?\b', 'AILEADERSHIP reference'),
            (r'aileadership\s+dot\s+com', 'AILEADERSHIP reference'),
            (r'@medium\.com', 'Medium reference'),
            # Inline author names from source PDFs
            (r'(?:as\s+)?(?:Geoffrey|Geoff)\s+(?:F\.\s+)?Wood(?:s|son)\s+(?:from|notes|suggests|puts|describes|says|mentions|at)\b[^.!?\n]*', 'Author inline reference'),
            (r'\b(?:Geoffrey|Geoff)\s+(?:F\.\s+)?Wood(?:s|son)\b', 'Author name'),
            # Our own prompt text that leaked through
            (r'Use ONLY facts from this research:.*$', 'Leaked prompt'),
            (r'You already discussed these topics.*$', 'Leaked prompt'),
            (r'Pick up naturally from where you left off.*$', 'Leaked prompt'),
            (r'Pick up from where you left off.*$', 'Leaked prompt'),
            (r'Follow this plan:.*$', 'Leaked prompt'),
            (r'Section goal:.*$', 'Leaked prompt'),
            # Chain-of-thought plan fragments that leak into dialogue
            (r'\bOpening:\s+(?:How\s+)?\w+\s+starts?\b.*$', 'Leaked plan'),
            (r'\bPoints:\s+(?:Which\s+)?\d.*$', 'Leaked plan'),
            (r'\bReactions:\s+.*$', 'Leaked plan'),
            (r'\bClosing:\s+.*$', 'Leaked plan'),
            (r'\b\w+\s+Expert (?:on|Guide on)\b[^.!?\n]*', 'Leaked plan role'),
            (r'\bstarts? off by introducing\b.*$', 'Leaked plan'),
            (r'\bfollows up,? mentioning\b.*$', 'Leaked plan'),
            (r'\breacts to this information\b.*$', 'Leaked plan'),
            (r'\bexpressing surprise at\b.*$', 'Leaked plan'),
            # Word count annotations the model outputs inline
            (r'\(\d+\s*words?\)', 'Word count annotation'),
        ]
        for pattern, label in leaked_patterns:
            matches = re.findall(pattern, script, re.MULTILINE | re.IGNORECASE)
            if matches:
                script = re.sub(pattern, '', script, flags=re.MULTILINE | re.IGNORECASE)
                repairs.append(f"Stripped {len(matches)} leaked '{label}'")
        
        # ── Phase 3: Strip non-spoken artifacts ──
        script = re.sub(r'\[\d+\]', '', script)  # Citations [1], [2]
        script = re.sub(r'\[source\s*\d*\]', '', script, flags=re.IGNORECASE)  # [source 2]
        script = re.sub(r'^#{1,6}\s+.*$', '', script, flags=re.MULTILINE)  # Full markdown header lines
        script = re.sub(r'#{1,6}\s+', '', script)  # Mid-line markdown headers
        script = re.sub(r'^\s*[-*•]\s+', '', script, flags=re.MULTILINE)  # Bullet points
        script = re.sub(r'^\s*\d+\.\s+', '', script, flags=re.MULTILINE)  # Numbered lists
        script = re.sub(r'^[\s]*[-=_]{3,}[\s]*$', '', script, flags=re.MULTILINE)  # Separator lines
        script = re.sub(r'---SECTION_BREAK---', '', script)  # Section breaks
        script = re.sub(r'\*\*(.+?)\*\*', r'\1', script)  # Bold
        script = re.sub(r'\*(.+?)\*', r'\1', script)  # Italic
        script = re.sub(r'`[^`]+`', '', script)  # Inline code
        script = re.sub(r',?\s*as (?:noted|mentioned|described)\s*\.?\s*$', '.', script, flags=re.MULTILINE)
        # Stage directions, silence markers, meta-commentary (defense-in-depth)
        script = re.sub(r'\([^)]*(?:transition|silence|pause|laughs?|sighs?|clears throat|music|continues|shifts?|switches?|opens?|closes?|concludes|beat)[^)]*\)', '', script, flags=re.IGNORECASE)
        script = re.sub(r'\[[^\]]*(?:silence|pause|minutes?|seconds?|music|transition|intro|outro)[^\]]*\]', '', script, flags=re.IGNORECASE)
        script = re.sub(r'--+\s*End\s+(?:Transcript|Episode|Segment|Section|Script)\s*--+', '', script, flags=re.IGNORECASE)
        script = re.sub(r'^.*(?:Continue writing|Script generated|next segment will|will be automatically generated).*$', '', script, flags=re.MULTILINE | re.IGNORECASE)
        script = re.sub(r'^\s*\((?:The\s+)?podcast\s+(?:concludes|ends|wraps).*$', '', script, flags=re.MULTILINE | re.IGNORECASE)
        script = re.sub(r'^\s*(?:Closing|Opening|Next)\s+segment\s+.*$', '', script, flags=re.MULTILINE | re.IGNORECASE)
        # Parenthesized speaker labels → proper labels
        script = re.sub(r'\((\w+)\)\s*:', r'\n\1:', script)
        # Empty speaker turns
        script = re.sub(r'^\s*\w+\s*:\s*$', '', script, flags=re.MULTILINE)
        
        # ── Phase 3.5: STRICT speaker-label enforcement ──
        # Every line of dialogue MUST start with "Name:" — anything else is garbage
        # or a continuation. Short continuations get merged; everything else is removed.
        # Also: only the two valid hosts are allowed as speakers.
        valid_speakers = {name_a.lower(), name_b.lower()} if name_a and name_b else set()
        dialogue_lines = []
        orphans_removed = 0
        wrong_speakers_fixed = 0
        speaker_pattern = re.compile(r'^([A-Za-z][\w]*)\s*:\s*(.*)')
        for line in script.split('\n'):
            stripped = line.strip()
            if not stripped:
                continue
            m = speaker_pattern.match(stripped)
            if m:
                speaker = m.group(1)
                content = m.group(2).strip()
                if not content:
                    continue  # Empty turn
                if valid_speakers and speaker.lower() not in valid_speakers:
                    # Unknown speaker — reassign to the host who spoke least recently
                    if dialogue_lines:
                        last_m = speaker_pattern.match(dialogue_lines[-1])
                        last_speaker = last_m.group(1) if last_m else name_a
                        # Assign to the OTHER host for natural alternation
                        new_speaker = name_b if last_speaker.lower() == name_a.lower() else name_a
                    else:
                        new_speaker = name_a
                    dialogue_lines.append(f"{new_speaker}: {content}")
                    wrong_speakers_fixed += 1
                else:
                    dialogue_lines.append(stripped)
            else:
                # No speaker label — is it a short continuation of previous turn?
                wc = len(stripped.split())
                if dialogue_lines and wc <= 25 and not re.search(
                    r'===|---|==|Page|Source|Image|Note:|Creative|Public Domain|©|referenced|Medium|PDF|Expert (?:on|Guide)',
                    stripped, re.IGNORECASE
                ):
                    # Merge short non-metadata continuation with previous line
                    dialogue_lines[-1] = dialogue_lines[-1] + ' ' + stripped
                else:
                    # Garbage — remove
                    orphans_removed += 1
        if orphans_removed > 0:
            repairs.append(f"Removed {orphans_removed} lines without speaker labels")
        if wrong_speakers_fixed > 0:
            repairs.append(f"Reassigned {wrong_speakers_fixed} lines from unknown speakers to valid hosts")
        script = '\n'.join(dialogue_lines)
        
        # ── Phase 3: Detect and remove repetition loops ──
        # Find 6-word phrases that appear 3+ times — these are degenerate loops
        words = script.split()
        if len(words) > 20:
            from collections import Counter
            ngram_size = 6
            ngrams = []
            for i in range(len(words) - ngram_size + 1):
                ngram = ' '.join(words[i:i+ngram_size]).lower()
                ngrams.append(ngram)
            ngram_counts = Counter(ngrams)
            repeated = {phrase for phrase, count in ngram_counts.items() if count >= 3}
            
            if repeated:
                # Find and remove duplicate paragraphs/sections that contain repeated phrases
                lines = script.split('\n')
                seen_content = set()
                deduped_lines = []
                removed = 0
                for line in lines:
                    stripped = line.strip()
                    if not stripped:
                        deduped_lines.append(line)
                        continue
                    # Normalize for comparison (lowercase, collapse whitespace)
                    normalized = re.sub(r'\s+', ' ', stripped.lower())
                    # Check if this line's content substantially overlaps with something we've seen
                    # Use first 60 chars as fingerprint
                    fingerprint = normalized[:60]
                    if fingerprint in seen_content and len(normalized) > 40:
                        removed += 1
                        continue
                    seen_content.add(fingerprint)
                    deduped_lines.append(line)
                
                if removed > 0:
                    script = '\n'.join(deduped_lines)
                    repairs.append(f"Removed {removed} repeated lines/sections")
        
        # ── Phase 4: Detect and truncate degenerate text ──
        # Run-on text: lines with many words but insufficient sentence structure.
        # Healthy spoken dialogue: ~1 sentence per 12-18 words.
        # Degenerate word salad: long runs of nouns/adjectives with no periods.
        clean_lines = []
        for line in script.split('\n'):
            line_words = len(line.split())
            if line_words > 35:
                punct = line.count('.') + line.count('!') + line.count('?')
                # Need at least 1 sentence ending per ~18 words
                min_punct = max(1, line_words // 18)
                if punct < min_punct:
                    # Degenerate — salvage first complete sentence if possible
                    first_sent = re.match(r'^(.*?[.!?])\s', line)
                    if first_sent and len(first_sent.group(1).split()) >= 5:
                        line = first_sent.group(1)
                    else:
                        # No sentence found — take first 30 words and add a period
                        line = ' '.join(line.split()[:30]) + '.'
                    repairs.append(f"Truncated run-on ({line_words} words, {punct} periods)")
            clean_lines.append(line)
        script = '\n'.join(clean_lines)
        
        # ── Phase 5: Replace forbidden speaker labels ──
        assistant_count = len(re.findall(r'(?:^|\s)Assistant\s*:', script, re.MULTILINE | re.IGNORECASE))
        if assistant_count > 0:
            # As line-start label
            script = re.sub(r'^Assistant\s*:', f'{name_a}:', script, flags=re.MULTILINE | re.IGNORECASE)
            # As inline label (mid-sentence)
            script = re.sub(r'(?<=\s)Assistant\s*:', f'{name_a}:', script, flags=re.IGNORECASE)
            repairs.append(f"Replaced {assistant_count} 'Assistant:' labels → '{name_a}:'")
        
        user_label_count = len(re.findall(r'^User\s*:', script, re.MULTILINE | re.IGNORECASE))
        if user_label_count > 0:
            script = re.sub(r'^User\s*:', f'{name_b}:', script, flags=re.MULTILINE | re.IGNORECASE)
            repairs.append(f"Replaced {user_label_count} 'User:' labels → '{name_b}:'")
        
        # Replace inline "Assistant" / "User" references in text
        script = re.sub(r'\bthe assistant\b', 'the host', script, flags=re.IGNORECASE)
        script = re.sub(r'\bAssistant I\b', f'{name_a}: I', script)
        script = re.sub(r'\bAssistant\b', name_a, script)
        
        # ── Phase 6: Enforce total word count ──
        word_count = len(script.split())
        max_words = int(target_words * (1 + self.TOTAL_WORD_TOLERANCE))
        if word_count > max_words:
            script = self._truncate_script_to_words(script, max_words)
            repairs.append(f"Truncated from {word_count} to {max_words} words")
        
        # ── Phase 7: Clean up whitespace from all the stripping ──
        # Collapse multiple blank lines into one
        script = re.sub(r'\n{3,}', '\n\n', script)
        # Remove lines that are now empty or just whitespace
        lines = [l for l in script.split('\n') if l.strip()]
        script = '\n'.join(lines)
        
        # ── Phase 8: Report and reject ──
        final_count = len(script.split())
        final_est = final_count / self.TTS_WORDS_PER_MINUTE
        
        # Speaker balance (informational)
        words_by_speaker = {'A': 0, 'B': 0}
        current_speaker = 'A'
        for line in script.split('\n'):
            ls = line.strip()
            if re.match(rf'^{re.escape(name_a)}\s*:', ls, re.IGNORECASE):
                current_speaker = 'A'
            elif re.match(rf'^{re.escape(name_b)}\s*:', ls, re.IGNORECASE):
                current_speaker = 'B'
            words_by_speaker[current_speaker] += len(ls.split())
        total = words_by_speaker['A'] + words_by_speaker['B']
        if total > 0:
            ratio_a = words_by_speaker['A'] / total
            min_bal, max_bal = blueprint['speaker_balance']
            if ratio_a < min_bal or ratio_a > max_bal:
                repairs.append(f"Speaker balance: A={ratio_a:.0%}, B={1-ratio_a:.0%} (target: {min_bal:.0%}-{max_bal:.0%})")
        
        if repairs:
            print(f"[AudioGen] Quality gate ({len(repairs)} repairs): {'; '.join(repairs)}")
        print(f"[AudioGen] Quality gate: {before_words} → {final_count} words, est. {final_est:.1f} min (target: {duration_minutes} min)")
        
        ratio = final_count / target_words if target_words > 0 else 0
        if ratio < 0.25:
            # Truly garbage — not enough for any usable podcast
            raise RuntimeError(f"Script too short after cleanup ({final_count} words, need {int(target_words*0.25)}+ for {duration_minutes}-min target). Generation failed.")
        elif ratio < 0.6:
            # Short but usable — warn but don't fail
            actual_minutes = final_count / self.TTS_WORDS_PER_MINUTE
            print(f"[AudioGen] ⚠ Script shorter than target: {final_count} words ({actual_minutes:.1f} min vs {duration_minutes} min target). Proceeding with shorter podcast.")
        
        return script

    def _clean_script_for_tts(self, script: str) -> str:
        """Strip non-spoken artifacts from script before sending to TTS.
        
        Aggressively removes stage directions, markdown, speaker labels,
        parenthetical text, separator lines, section headers, transcript
        markers, and anything else that should not be read aloud.
        """
        lines = script.split('\n')
        cleaned = []
        
        # Lines to skip entirely
        skip_re = re.compile(
            r'^[\s]*[-=_]{3,}[\s]*$'
            r'|^\s*\[.*\]\s*$'
            r'|^(?:end\s+)?transcript\b'
            r'|^(?:end\s+of\s+)?(?:episode|podcast|debate|interview)\b'
            r'|^(?:opening|closing)\s+(?:positions?|statements?|remarks?)\s*$'
            r'|^(?:part|section|segment|act)\s+\d+\b'
            r'|^(?:clash|intensity|rising|climax|resolution|rebuttal|conclusion)\b',
            re.IGNORECASE
        )
        
        for line in lines:
            line = line.strip()
            if not line:
                continue
            
            # Skip non-spoken lines entirely
            if skip_re.match(line):
                continue
            
            # Remove ALL bracketed text
            line = re.sub(r'\*?\[.*?\]\*?', '', line)
            
            # Remove ALL parenthetical text
            line = re.sub(r'\([^)]*\)', '', line)
            
            # Remove markdown formatting
            line = re.sub(r'^#{1,6}\s+', '', line)       # Headings
            line = re.sub(r'\*\*(.+?)\*\*', r'\1', line) # Bold
            line = re.sub(r'\*(.+?)\*', r'\1', line)     # Italic
            line = re.sub(r'^\s*[-*•]\s+', '', line)      # Bullet points
            line = re.sub(r'^\s*\d+\.\s+', '', line)      # Numbered lists
            
            # Strip speaker labels (Host A:, Host B:, Speaker 1:, Name:, Assistant:, etc.)
            line = re.sub(r'^(?:Host|Speaker)\s*[AB12]?\s*:\s*', '', line, flags=re.IGNORECASE)
            line = re.sub(r'^Assistant\s*:\s*', '', line, flags=re.IGNORECASE)
            
            # Replace "Assistant" / "User" / "the user" with natural alternatives
            line = re.sub(r'\bthe assistant\b', 'the host', line, flags=re.IGNORECASE)
            line = re.sub(r'\bAssistant\b', 'the host', line)
            line = re.sub(r'\bthe user\b', 'the listener', line, flags=re.IGNORECASE)
            line = re.sub(r'\bUser\b', 'the listener', line)
            
            # Remove leftover artifacts
            line = re.sub(r'\[\s*\]', '', line)
            line = re.sub(r'\*+', '', line)
            
            # Clean up whitespace
            line = re.sub(r'\s+', ' ', line).strip()
            
            if line and len(line) > 1:
                cleaned.append(line)
        
        result = '\n'.join(cleaned)
        if result != script:
            original_len = len(script)
            cleaned_len = len(result)
            print(f"   [TTS Clean] Stripped {original_len - cleaned_len} chars of non-spoken content")
        return result

    def _generate_jingle(self, output_path: Path, duration_sec: float = 3.0,
                          fade_in: bool = True, fade_out: bool = True) -> Path:
        """Generate a short musical jingle using pure Python (no dependencies).
        
        Creates a pleasant chord progression with fade in/out.
        Uses standard library only: struct, wave, math.
        """
        sample_rate = 24000  # Match Kokoro TTS output rate
        num_samples = int(sample_rate * duration_sec)
        
        # Pleasant major chord frequencies (C major 7th voicing)
        # C4=261.6, E4=329.6, G4=392.0, B4=493.9
        chord_freqs = [261.63, 329.63, 392.00, 493.88]
        # Add a gentle fifth above for shimmer
        chord_freqs.append(523.25)  # C5
        
        samples = []
        for i in range(num_samples):
            t = i / sample_rate
            progress = i / num_samples  # 0.0 → 1.0
            
            # Mix chord tones with decreasing amplitude for higher harmonics
            sample = 0.0
            for j, freq in enumerate(chord_freqs):
                amplitude = 0.3 / (1 + j * 0.4)  # Higher notes quieter
                # Slight detuning for warmth
                detune = 1.0 + (j * 0.001)
                sample += amplitude * math.sin(2 * math.pi * freq * detune * t)
            
            # Apply envelope
            envelope = 1.0
            if fade_in and progress < 0.3:
                envelope *= progress / 0.3  # Fade in over first 30%
            if fade_out and progress > 0.5:
                envelope *= (1.0 - progress) / 0.5  # Fade out over last 50%
            
            sample *= envelope
            
            # Soft clipping
            sample = max(-0.95, min(0.95, sample))
            samples.append(sample)
        
        # Write WAV file
        with wave.open(str(output_path), 'w') as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)  # 16-bit
            wf.setframerate(sample_rate)
            for s in samples:
                wf.writeframes(struct.pack('<h', int(s * 32767)))
        
        return output_path

    def _generate_transition_stinger(self, output_path: Path) -> Path:
        """Generate a short musical transition stinger (1.5s).
        
        Different chord voicing from intro/outro — signals a topic shift.
        Uses a sus4 → resolve progression for a "moving forward" feel.
        """
        sample_rate = 24000
        duration_sec = 1.5
        num_samples = int(sample_rate * duration_sec)
        
        # Suspended 4th → major resolution (F sus4 → F major feel)
        # First half: sus4 (F, Bb, C), second half: resolve (F, A, C)
        sus4_freqs = [174.61, 233.08, 261.63]  # F3, Bb3, C4
        resolve_freqs = [174.61, 220.00, 261.63]  # F3, A3, C4
        
        samples = []
        for i in range(num_samples):
            t = i / sample_rate
            progress = i / num_samples
            
            # Crossfade from sus4 to resolved at midpoint
            if progress < 0.5:
                freqs = sus4_freqs
                blend = 0.0
            else:
                freqs = resolve_freqs
                blend = (progress - 0.5) * 2  # 0→1 over second half
            
            sample = 0.0
            for j, freq in enumerate(freqs):
                amp = 0.25 / (1 + j * 0.3)
                sample += amp * math.sin(2 * math.pi * freq * t)
            
            # Bell-curve envelope: rise quickly, sustain, gentle fade
            if progress < 0.15:
                envelope = progress / 0.15
            elif progress > 0.6:
                envelope = (1.0 - progress) / 0.4
            else:
                envelope = 1.0
            
            sample *= envelope * 0.7  # Slightly quieter than intro
            sample = max(-0.95, min(0.95, sample))
            samples.append(sample)
        
        with wave.open(str(output_path), 'w') as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(sample_rate)
            for s in samples:
                wf.writeframes(struct.pack('<h', int(s * 32767)))
        
        return output_path

    def _concatenate_with_jingles(self, speech_path: Path, output_path: Path,
                                   add_intro: bool = True, add_outro: bool = True) -> Path:
        """Concatenate intro jingle + speech + outro jingle using ffmpeg.
        
        Falls back to speech-only if ffmpeg is not available.
        """
        jingle_dir = self.audio_dir / "jingles"
        jingle_dir.mkdir(parents=True, exist_ok=True)
        
        # Generate jingles if they don't exist yet
        intro_path = jingle_dir / "intro_jingle.wav"
        outro_path = jingle_dir / "outro_jingle.wav"
        
        if add_intro and not intro_path.exists():
            self._generate_jingle(intro_path, duration_sec=3.0, fade_in=True, fade_out=True)
            print(f"   Generated intro jingle: {intro_path}")
        
        if add_outro and not outro_path.exists():
            self._generate_jingle(outro_path, duration_sec=3.5, fade_in=True, fade_out=True)
            print(f"   Generated outro jingle: {outro_path}")
        
        # Build ffmpeg concat filter
        inputs = []
        filter_parts = []
        idx = 0
        
        if add_intro and intro_path.exists():
            inputs.extend(["-i", str(intro_path)])
            filter_parts.append(f"[{idx}:a]aformat=sample_rates=24000:channel_layouts=mono[a{idx}]")
            idx += 1
        
        inputs.extend(["-i", str(speech_path)])
        filter_parts.append(f"[{idx}:a]aformat=sample_rates=24000:channel_layouts=mono[a{idx}]")
        speech_idx = idx
        idx += 1
        
        if add_outro and outro_path.exists():
            inputs.extend(["-i", str(outro_path)])
            filter_parts.append(f"[{idx}:a]aformat=sample_rates=24000:channel_layouts=mono[a{idx}]")
            idx += 1
        
        # Concat all streams
        concat_inputs = ''.join(f'[a{i}]' for i in range(idx))
        filter_str = ';'.join(filter_parts) + f';{concat_inputs}concat=n={idx}:v=0:a=1[out]'
        
        try:
            cmd = [
                "ffmpeg", "-y",
                *inputs,
                "-filter_complex", filter_str,
                "-map", "[out]",
                str(output_path)
            ]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            if result.returncode == 0 and output_path.exists():
                print(f"   Assembled final audio with jingles: {output_path}")
                return output_path
            else:
                print(f"   ffmpeg concat failed: {result.stderr[:200]}")
        except FileNotFoundError:
            print("   ffmpeg not found, skipping jingle concatenation")
        except Exception as e:
            print(f"   Jingle concat error: {e}")
        
        # Fallback: just use the speech file directly
        if speech_path != output_path:
            import shutil
            shutil.copy2(speech_path, output_path)
        return output_path

    async def _full_pipeline_async(
        self,
        audio_id: str,
        notebook_id: str,
        topic: Optional[str],
        duration_minutes: int,
        skill_id: Optional[str],
        host1_gender: str,
        host2_gender: str,
        accent: str,
        is_two_host: bool = True,
        chat_context: Optional[str] = None,
        register: Optional[str] = None,
    ):
        """Full background pipeline: script generation → audio synthesis.

        Runs entirely in the background so the API returns instantly.
        Updates audio_store status at each stage for frontend polling.
        """
        import traceback

        # Pick character names for this generation
        host_names = self._pick_host_names(host1_gender, host2_gender, accent)
        print(f"🎭 Characters: {host_names[0]} (A) and {host_names[1]} (B)")

        # Free RAM for the script-generation main model BEFORE we hit it.
        # On a memory-pressured Mac, an idle vision model (~4.6 GB) +
        # follow-up cache can crowd Ollama and cause the main model to
        # swap or crash mid-script. The eviction is universal — same
        # benefit for olmo, gemma, phi, llama. Anything we DO need
        # later (the active main model + embeddings for any in-flight
        # RAG context) is whitelisted. Best-effort: any failure here is
        # logged and the pipeline proceeds normally.
        try:
            from services.memory_steward import free_for_pipeline
            from config import settings as _s
            keep = {
                _s.ollama_model,             # we're about to call this
                _s.ollama_fast_model,        # may be used for follow-ups
                _s.embedding_model,          # context retrieval still active
            }
            keep = {m for m in keep if m}
            evicted = await free_for_pipeline(keep, reason="audio_pipeline")
            if evicted:
                logger.info(f"[audio-gen] freed RAM by unloading: {evicted}")
        except Exception as _e:
            logger.warning(f"[audio-gen] memory_steward call failed (non-fatal): {_e}")

        try:
            # Stage 1: Generate script
            await audio_store.update(audio_id, {
                "status": "processing",
                "error_message": "Writing script..."
            })
            
            script = await self._generate_script(
                notebook_id=notebook_id,
                topic=topic,
                duration_minutes=duration_minutes,
                skill_id=skill_id,
                host_names=host_names,
                chat_context=chat_context,
                register=register,
            )
            
            if not script or len(script.strip()) < 20:
                raise RuntimeError("Script generation produced no usable content")
            
            # Diagnostic: confirm script integrity before storage
            nl_at_store = script.count('\n')
            lines_at_store = len([l for l in script.split('\n') if l.strip()])
            print(f"📝 Script generated for {audio_id}: {len(script)} chars, {len(script.split())} words, {nl_at_store} newlines, {lines_at_store} lines")
            if nl_at_store == 0 and len(script.split()) > 50:
                print(f"[AudioGen] ⚠⚠⚠ CRITICAL: Script about to be stored with NO newlines! This will break TTS parsing.")
            
            # Save script to the record
            await audio_store.update(audio_id, {
                "script": script,
                "error_message": "Script ready. Starting audio generation..."
            })
            
            # Stage 2: Generate audio
            await self._generate_audio_async(
                audio_id=audio_id,
                script=script,
                host1_gender=host1_gender,
                host2_gender=host2_gender,
                accent=accent,
                is_two_host=is_two_host,
                host_names=host_names
            )
            
        except Exception as e:
            print(f"❌ Pipeline failed for {audio_id}: {e}")
            traceback.print_exc()
            await audio_store.update(audio_id, {
                "status": "failed",
                "error_message": str(e)
            })
    
    async def _generate_audio_async(
        self,
        audio_id: str,
        script: str,
        host1_gender: str,
        host2_gender: str,
        accent: str,
        is_two_host: bool = True,
        host_names: Optional[Tuple[str, str]] = None
    ):
        """Background task to generate audio using Kokoro-82M.
        
        Two-host mode: parses Host A/Host B turns, uses different voices per speaker.
        Single-narrator mode: cleans script and chunks by paragraph.
        No script length limit — chunked generation handles any length.
        """
        import traceback
        import shutil
        from services.audio_llm import audio_llm
        
        print(f"🎤 Starting Kokoro TTS generation for {audio_id}")
        
        try:
            # Initialize audio model if needed
            await audio_llm.initialize()
            
            if not audio_llm.is_available:
                detail = audio_llm._init_error or "unknown error"
                raise RuntimeError(
                    f"Kokoro TTS not available. Check Health Portal → AI & Models for details. "
                    f"Click 'Repair' to download the model (~350 MB). Error: {detail}"
                )
            
            # Build voice map based on accent + gender
            # Voice pools are lists — pick a random voice from each pool
            accent_voices = self.voices.get(accent, self.voices["us"])
            pool_a = accent_voices.get(host1_gender, accent_voices.get("male", ["am_adam"]))
            pool_b = accent_voices.get(host2_gender, accent_voices.get("female", ["af_heart"]))
            voice_a = random.choice(pool_a) if isinstance(pool_a, list) else pool_a
            voice_b = random.choice(pool_b) if isinstance(pool_b, list) else pool_b
            # Ensure two-host voices are different when possible
            if voice_a == voice_b and len(pool_b) > 1:
                voice_b = random.choice([v for v in pool_b if v != voice_a] or pool_b)
            voice_map = {
                "A": voice_a,
                "B": voice_b,
            }
            
            # Parse script into generation segments
            if is_two_host:
                segments = self._parse_script_for_tts(script, host_names=host_names)
                print(f"   Two-host mode: {len(segments)} speaker turns")
            else:
                # Single narrator — clean and chunk using local method
                clean = self._clean_script_for_tts(script)
                chunks = self._split_script_into_chunks(clean, max_chars=750)
                segments = [("A", chunk) for chunk in chunks if chunk.strip()]
                print(f"   Narrator mode: {len(segments)} chunks from {len(clean)} chars")
            
            total_chars = sum(len(text) for _, text in segments)
            print(f"   Total script: {total_chars} chars, voices: A={voice_map['A']}, B={voice_map['B']}")
            
            # Generate audio per segment with progress tracking
            speech_path = self.audio_dir / f"{audio_id}_speech.wav"
            output_path = self.audio_dir / f"{audio_id}.wav"
            temp_dir = self.audio_dir / f"{audio_id}_parts"
            temp_dir.mkdir(parents=True, exist_ok=True)
            
            part_paths = []
            
            # Pre-generate transition stinger for section breaks
            jingle_dir = self.audio_dir / "jingles"
            jingle_dir.mkdir(parents=True, exist_ok=True)
            transition_path = jingle_dir / "transition_stinger.wav"
            if not transition_path.exists():
                self._generate_transition_stinger(transition_path)
                print(f"   Generated transition stinger: {transition_path}")
            
            # Count real (non-BREAK) segments for progress
            real_segments = [(i, s, t) for i, (s, t) in enumerate(segments) if s != "BREAK"]
            total_real = len(real_segments)
            
            import time
            gen_start_time = time.time()
            last_error = None
            real_done = 0
            segments_failed = 0
            for i, (speaker, text) in enumerate(segments):
                part_path = temp_dir / f"part_{i:04d}.wav"
                
                # Handle section break — insert transition stinger
                if speaker == "BREAK":
                    if transition_path.exists():
                        import shutil as _shutil
                        _shutil.copy2(transition_path, part_path)
                        part_paths.append(part_path)
                        print(f"   ♪ Section transition stinger inserted")
                    continue
                
                if not text.strip():
                    continue
                
                voice = voice_map.get(speaker, voice_map["A"])
                real_done += 1
                
                # Update progress with ETA
                elapsed = time.time() - gen_start_time
                if real_done > 1:
                    avg_per_seg = elapsed / (real_done - 1)
                    remaining = avg_per_seg * (total_real - real_done + 1)
                    eta_min = int(remaining // 60)
                    eta_sec = int(remaining % 60)
                    eta_str = f" — ~{eta_min}m {eta_sec}s remaining" if eta_min > 0 else f" — ~{eta_sec}s remaining"
                else:
                    eta_str = ""
                await audio_store.update(audio_id, {
                    "status": "processing",
                    "error_message": f"Generating audio: segment {real_done}/{total_real}{eta_str}"
                })
                
                try:
                    seg_timeout = max(120, int(len(text) / 500 * 60))
                    result_path = await asyncio.wait_for(
                        audio_llm.text_to_speech(
                            text=text,
                            voice=voice,
                            output_path=str(part_path)
                        ),
                        timeout=seg_timeout
                    )
                    if result_path and Path(result_path).exists():
                        part_paths.append(part_path)
                        progress_pct = int((real_done / total_real) * 100)
                        await audio_store.update(audio_id, {
                            "status": "processing",
                            "error_message": f"Generating audio: {progress_pct}% ({real_done}/{total_real} done)"
                        })
                        print(f"   ✓ Segment {real_done}/{total_real} ({speaker}): {len(text)} chars → {part_path.name}")
                    else:
                        print(f"   ⚠ Segment {real_done}/{total_real} produced no file, skipping")
                    # Resource guardrails: prevent thermal throttling and memory pressure
                    # Clear GPU/MPS memory caches and yield to event loop between segments
                    import gc
                    gc.collect()
                    try:
                        import mlx.core as mx
                        mx.clear_cache()
                    except Exception as _e:
                        logger.debug(f"[audio-generator] {type(_e).__name__}: {_e}")
                    # Brief pause to let GPU cool and OS reclaim resources
                    await asyncio.sleep(0.5)
                except asyncio.TimeoutError:
                    last_error = f"Segment {real_done} timed out after {seg_timeout}s"
                    print(f"   ⚠ {last_error}, skipping")
                    segments_failed += 1
                except Exception as seg_err:
                    last_error = f"Segment {real_done}: {seg_err}"
                    print(f"   ⚠ Segment {real_done}/{total_real} failed: {seg_err}, skipping")
                    segments_failed += 1
                
                # Early abort: if >70% of attempted segments have failed,
                # something is fundamentally wrong — stop wasting time
                if real_done >= 4 and segments_failed / real_done > 0.7:
                    print(f"   ❌ ABORTING: {segments_failed}/{real_done} segments failed (>70%). TTS engine may be broken.")
                    break
            
            # Log summary
            segments_succeeded = len(part_paths)
            if segments_failed > 0:
                print(f"   📊 TTS segment summary: {segments_succeeded} succeeded, {segments_failed} failed out of {total_real} total")
            
            if not part_paths:
                detail = f" Last error: {last_error}" if last_error else ""
                raise RuntimeError(f"No audio segments were generated successfully ({segments_failed}/{total_real} failed).{detail}")
            
            # Concatenate all parts into one speech file
            self._concatenate_wav_parts(part_paths, speech_path)
            print(f"   Assembled {len(part_paths)} segments → {speech_path}")
            
            # Clean up temp parts
            shutil.rmtree(temp_dir, ignore_errors=True)
            
            # Assemble final audio: intro jingle + speech + outro jingle
            final_path = self._concatenate_with_jingles(
                speech_path=speech_path,
                output_path=output_path,
                add_intro=True,
                add_outro=True
            )
            
            # Clean up temp speech file if concat succeeded
            if final_path == output_path and speech_path.exists() and speech_path != output_path:
                speech_path.unlink(missing_ok=True)
            
            # Post-process: normalize volume for consistent listening
            self._normalize_audio(final_path)
            
            # ── Verification: validate audio before marking complete ──
            await audio_store.update(audio_id, {
                "status": "processing",
                "error_message": "Verifying audio quality..."
            })
            self._verify_audio(final_path)
            
            duration_seconds = self._get_audio_duration(final_path)
            
            await audio_store.update(audio_id, {
                "status": "completed",
                "audio_file_path": str(final_path),
                "duration_seconds": duration_seconds,
                "error_message": None
            })
            print(f"✅ Audio generated: {audio_id} → {final_path} ({duration_seconds}s)")
            
        except Exception as e:
            print(f"❌ Audio generation failed: {e}")
            traceback.print_exc()
            await audio_store.update(audio_id, {
                "status": "failed",
                "error_message": str(e)
            })
            # Clean up temp _parts directory on failure
            try:
                _td = self.audio_dir / f"{audio_id}_parts"
                if _td.exists():
                    import shutil
                    shutil.rmtree(_td, ignore_errors=True)
            except Exception as _e:
                logger.debug(f"[audio-generator] {type(_e).__name__}: {_e}")

    def _parse_script_for_tts(self, script: str, host_names: Optional[Tuple[str, str]] = None) -> List[tuple]:
        """Parse script into speaker turns, cleaned for TTS.
        
        Returns list of (speaker, clean_text) tuples where speaker is 'A' or 'B'
        and clean_text has stage directions, markdown, and labels stripped.
        Long turns are further chunked at sentence boundaries for reliable generation.
        """
        # ── Safety net: ensure script has proper line breaks before parsing ──
        # Defense-in-depth: if the script is a single mega-line despite all
        # upstream fixes, split it at speaker-label boundaries here too.
        if host_names and '\n' not in script.strip():
            names = [re.escape(host_names[0]), re.escape(host_names[1]),
                     r'Host\s*[AB]', r'Speaker\s*[12]', 'Assistant', 'User']
            pattern = '|'.join(names)
            script = re.sub(
                rf'(?<=\S)\s+({pattern})\s*:',
                lambda m: '\n' + m.group(1) + ':',
                script,
                flags=re.IGNORECASE
            )
            split_count = len([l for l in script.split('\n') if l.strip()])
            if split_count > 1:
                print(f"[AudioGen] ⚠ _parse_script_for_tts: Split single-line script into {split_count} lines (defense-in-depth)")
        
        # First parse into raw speaker segments
        raw_segments = self._parse_script(script, host_names=host_names)
        
        # Clean each segment's text for TTS
        cleaned = []
        for speaker, text in raw_segments:
            # Pass through section break markers
            if speaker == "BREAK":
                cleaned.append(("BREAK", ""))
                continue
            
            clean = self._clean_text_for_tts(text)
            if not clean or len(clean.strip()) < 2:
                continue
            
            # If a turn is very long (>350 chars), sub-chunk it but keep same speaker
            # Shorter chunks produce dramatically better prosody from Kokoro TTS
            if len(clean) > 350:
                sub_chunks = self._sub_chunk_text(clean, max_chars=350)
                for chunk in sub_chunks:
                    if chunk.strip():
                        cleaned.append((speaker, chunk.strip()))
            else:
                cleaned.append((speaker, clean))
        
        if not cleaned:
            # Fallback: treat whole script as single speaker
            clean = self._clean_script_for_tts(script)
            if clean:
                cleaned = [("A", clean)]
        
        return cleaned
    
    def _clean_text_for_tts(self, text: str) -> str:
        """Clean a single text segment for TTS (no speaker labels to strip).
        
        Aggressively removes ALL non-spoken artifacts that LLMs commonly inject:
        stage directions, markdown, section headers, dashed lines, parenthetical
        labels, transcript markers, etc. Only spoken dialogue should survive.
        """
        # Remove ALL bracketed text — stage directions, annotations, etc.
        text = re.sub(r'\*?\[.*?\]\*?', '', text)
        
        # Remove ALL parenthetical text — (laughs), (Opening Positions), (Name), etc.
        # This is aggressive but necessary — LLMs inject parenthetical non-speech constantly
        text = re.sub(r'\([^)]*\)', '', text)
        
        # Remove markdown formatting
        text = re.sub(r'^#{1,6}\s+', '', text, flags=re.MULTILINE)
        text = re.sub(r'\*\*(.+?)\*\*', r'\1', text)
        text = re.sub(r'\*(.+?)\*', r'\1', text)
        
        # Remove dashed/equals separator lines (any line that is mostly dashes, equals, or underscores)
        text = re.sub(r'^[\s]*[-=_]{3,}[\s]*$', '', text, flags=re.MULTILINE)
        
        # Remove transcript/episode markers
        text = re.sub(r'^(?:end\s+)?transcript\b.*$', '', text, flags=re.MULTILINE | re.IGNORECASE)
        text = re.sub(r'^(?:end\s+of\s+)?(?:episode|podcast|debate|interview|conversation)\b.*$', '', text, flags=re.MULTILINE | re.IGNORECASE)
        text = re.sub(r'^(?:opening|closing)\s+(?:positions?|statements?|remarks?)\s*$', '', text, flags=re.MULTILINE | re.IGNORECASE)
        
        # Remove section/part headers the LLM might inject
        text = re.sub(r'^(?:part|section|segment|chapter|act)\s+\d+\b.*$', '', text, flags=re.MULTILINE | re.IGNORECASE)
        text = re.sub(r'^(?:clash|intensity|rising|climax|resolution|rebuttal|conclusion)\b.*$', '', text, flags=re.MULTILINE | re.IGNORECASE)
        
        # Remove bullet points and numbered lists
        text = re.sub(r'^\s*[-*•]\s+', '', text, flags=re.MULTILINE)
        text = re.sub(r'^\s*\d+\.\s+', '', text, flags=re.MULTILINE)
        
        # Replace "Assistant" / "User" references that leak from LLM training
        text = re.sub(r'\bthe assistant\b', 'the host', text, flags=re.IGNORECASE)
        text = re.sub(r'\bAssistant\b', 'the host', text)
        text = re.sub(r'\bthe user\b', 'the listener', text, flags=re.IGNORECASE)
        text = re.sub(r'\bUser\b', 'the listener', text)
        
        # Clean leftover artifacts
        text = re.sub(r'\[\s*\]', '', text)
        text = re.sub(r'\*+', '', text)
        text = re.sub(r'\s+', ' ', text).strip()
        return text
    
    def _sub_chunk_text(self, text: str, max_chars: int = 1000) -> List[str]:
        """Sub-chunk long text at sentence boundaries with fallback splitting.
        
        Primary split: sentence endings (.!?)
        Fallback: clause boundaries (newlines, semicolons, commas, em-dashes)
        Last resort: hard split at word boundary near max_chars
        """
        sentences = re.split(r'(?<=[.!?])\s+', text)
        chunks = []
        current = ""
        for s in sentences:
            if current and len(current) + len(s) + 1 > max_chars:
                chunks.append(current)
                current = s
            else:
                current = f"{current} {s}".strip() if current else s
        if current:
            chunks.append(current)
        
        # Fallback: force-split any chunk that's still too large
        hard_limit = max_chars * 2
        final = []
        for chunk in chunks:
            if len(chunk) <= hard_limit:
                final.append(chunk)
            else:
                final.extend(self._force_split_text(chunk, max_chars))
        return final
    
    def _force_split_text(self, text: str, max_chars: int) -> List[str]:
        """Force-split oversized text at clause boundaries, then word boundaries."""
        # Try clause-level splits: newlines, semicolons, em-dashes, commas
        parts = re.split(r'(?:\n|;\s*|—\s*|,\s+)', text)
        chunks = []
        current = ""
        for p in parts:
            p = p.strip()
            if not p:
                continue
            if current and len(current) + len(p) + 1 > max_chars:
                chunks.append(current)
                current = p
            else:
                current = f"{current} {p}".strip() if current else p
        if current:
            chunks.append(current)
        
        # Last resort: any chunk still over max_chars gets hard-split at word boundary
        final = []
        for chunk in chunks:
            if len(chunk) <= max_chars:
                final.append(chunk)
            else:
                words = chunk.split()
                buf = ""
                for w in words:
                    if buf and len(buf) + len(w) + 1 > max_chars:
                        final.append(buf)
                        buf = w
                    else:
                        buf = f"{buf} {w}".strip() if buf else w
                if buf:
                    final.append(buf)
        return final
    
    def _concatenate_wav_parts(self, part_paths: List[Path], output_path: Path):
        """Concatenate multiple WAV files with crossfading for seamless joins.
        
        Uses short crossfades at segment boundaries to eliminate clicks/pops.
        Also normalizes each segment's volume before joining for consistency.
        """
        import array
        
        if not part_paths:
            return
        
        # If only one part, just copy it
        if len(part_paths) == 1:
            import shutil
            shutil.copy2(part_paths[0], output_path)
            return
        
        # Read all parts as sample arrays
        segments = []
        params = None
        
        for p in part_paths:
            try:
                with wave.open(str(p), 'rb') as wf:
                    if params is None:
                        params = wf.getparams()
                    raw = wf.readframes(wf.getnframes())
                    if raw and params.sampwidth == 2:
                        samples = array.array('h', raw)
                        # Per-segment peak normalization to -3dB for consistent volume
                        peak = max(abs(s) for s in samples) if samples else 0
                        if peak > 0:
                            target = 32767 * 0.708  # -3dB
                            gain = target / peak
                            if gain < 0.8 or gain > 1.3:
                                for i in range(len(samples)):
                                    samples[i] = max(-32767, min(32767, int(samples[i] * gain)))
                        segments.append(samples)
            except Exception as e:
                print(f"   Warning: couldn't read {p.name}: {e}")
                continue
        
        if not segments or params is None:
            return
        
        # Crossfade between segments (30ms at 24kHz = 720 samples)
        crossfade_len = int(params.framerate * 0.03)
        # Natural pause between speaker turns (100ms silence)
        pause_len = int(params.framerate * 0.10)
        pause_samples = array.array('h', [0] * pause_len)
        
        merged = segments[0]
        for j in range(1, len(segments)):
            nxt = segments[j]
            # Add a short natural pause
            merged.extend(pause_samples)
            # Apply crossfade if both segments are long enough
            if len(merged) > crossfade_len and len(nxt) > crossfade_len:
                for k in range(crossfade_len):
                    fade_out = 1.0 - (k / crossfade_len)
                    fade_in = k / crossfade_len
                    idx = len(merged) - crossfade_len + k
                    blended = int(merged[idx] * fade_out + nxt[k] * fade_in)
                    merged[idx] = max(-32767, min(32767, blended))
                merged.extend(nxt[crossfade_len:])
            else:
                merged.extend(nxt)
        
        with wave.open(str(output_path), 'wb') as out:
            out.setparams(params)
            out.writeframes(merged.tobytes())
    
    def _normalize_audio(self, wav_path: Path, target_db: float = -1.0):
        """Peak-normalize a WAV file to target dB level.
        
        Reads the file, finds the peak sample, scales all samples so the peak
        reaches the target level. Overwrites the file in place.
        -1 dB ≈ 89% of max, leaving headroom to avoid clipping.
        """
        import array
        
        try:
            with wave.open(str(wav_path), 'rb') as wf:
                params = wf.getparams()
                raw = wf.readframes(wf.getnframes())
            
            if not raw or params.sampwidth != 2:
                return  # Only handle 16-bit PCM
            
            # Convert to array of signed 16-bit integers
            samples = array.array('h', raw)
            if not samples:
                return
            
            # Find peak
            peak = max(abs(s) for s in samples)
            if peak == 0:
                return
            
            # Calculate gain to reach target dB
            # target_db = -1.0 → target_linear ≈ 0.891
            target_linear = 10 ** (target_db / 20.0)
            gain = (32767 * target_linear) / peak
            
            if 0.95 <= gain <= 1.05:
                return  # Already close enough, skip rewrite
            
            # Apply gain with clipping protection
            for i in range(len(samples)):
                samples[i] = max(-32767, min(32767, int(samples[i] * gain)))
            
            # Write back
            with wave.open(str(wav_path), 'wb') as wf:
                wf.setparams(params)
                wf.writeframes(samples.tobytes())
            
            print(f"   📊 Normalized audio: peak gain {gain:.2f}x → {target_db} dB")
        except Exception as e:
            print(f"   Warning: audio normalization failed: {e}")

    def _verify_audio(self, wav_path: Path):
        """Verify generated audio meets minimum quality standards.
        
        Checks:
        1. File is a valid readable WAV
        2. Sample rate is 24000 Hz (Kokoro TTS native)
        3. Duration is at least 5 seconds (catches truncated/empty output)
        4. Audio is not silent (RMS energy above threshold)
        
        Raises RuntimeError on failure so the pipeline marks the generation as failed.
        """
        import array
        
        if not wav_path.exists():
            raise RuntimeError(f"Audio file not found: {wav_path}")
        
        file_size = wav_path.stat().st_size
        if file_size < 1000:
            raise RuntimeError(f"Audio file too small ({file_size} bytes) — likely corrupt")
        
        try:
            with wave.open(str(wav_path), 'rb') as wf:
                params = wf.getparams()
                n_frames = wf.getnframes()
                raw = wf.readframes(n_frames)
        except Exception as e:
            raise RuntimeError(f"Cannot read WAV file: {e}")
        
        # Check sample rate
        if params.framerate not in (24000, 22050, 44100, 48000):
            print(f"   ⚠ Unexpected sample rate: {params.framerate} Hz")
        
        # Check minimum duration
        duration = n_frames / params.framerate if params.framerate > 0 else 0
        if duration < 5.0:
            raise RuntimeError(f"Audio too short ({duration:.1f}s) — generation likely failed")
        
        # Check for silence (RMS energy)
        if params.sampwidth == 2 and raw:
            samples = array.array('h', raw)
            if samples:
                rms = (sum(s * s for s in samples) / len(samples)) ** 0.5
                if rms < 50:
                    raise RuntimeError(f"Audio appears silent (RMS={rms:.0f}) — generation likely failed")
                print(f"   🔊 Audio verification passed: {duration:.1f}s, RMS={rms:.0f}")
            else:
                raise RuntimeError("Audio file has no samples")
        else:
            print(f"   🔊 Audio verification passed: {duration:.1f}s (skipped RMS check — {params.sampwidth}-byte samples)")

    def _parse_script(self, script: str, host_names: Optional[Tuple[str, str]] = None) -> List[tuple]:
        """Parse script into speaker segments.
        
        Handles various label formats the LLM might produce:
          Host A: ...       **Host A:** ...      *Host A:* ...
          Speaker A: ...    ## Host A: ...       A: ...
          Host 1: ...       Speaker 1: ...
          Sarah: ...        Marcus: ...          (character names)
          (Sarah) ...       (Marcus) ...         (parenthetical names)
        """
        # Build name → speaker mapping for character names
        name_map: dict = {}
        if host_names:
            name_map[host_names[0].lower()] = "A"
            name_map[host_names[1].lower()] = "B"
        # Map common LLM fallback labels to speakers
        name_map["assistant"] = "A"
        name_map["user"] = "B"
        
        # Regex for traditional Host A/B labels
        host_re = re.compile(
            r'^[\s*#]*'                        # leading whitespace, *, #
            r'(?:host|speaker)?\s*'            # optional "host" / "speaker"
            r'([AaBb1-2])'                     # speaker identifier
            r'[\s*#]*:[\s*]*'                  # colon with optional surrounding markdown
            r'(.*)',                            # rest of line
            re.IGNORECASE
        )
        
        # Regexes for character name labels
        name_re = None
        name_paren_re = None
        # Always include common LLM fallback labels
        all_names = list(host_names) if host_names else []
        all_names.extend(["Assistant", "User"])
        if all_names:
            escaped = [re.escape(n) for n in all_names]
            name_pattern = '|'.join(escaped)
            # "Sarah: ..." or "**Sarah:** ..."
            name_re = re.compile(
                r'^[\s*#]*'
                r'(' + name_pattern + r')'
                r'[\s*#]*:[\s*]*'
                r'(.*)',
                re.IGNORECASE
            )
            # "(Sarah) ..." or "(Sarah)" on its own line
            name_paren_re = re.compile(
                r'^\s*\(\s*(' + name_pattern + r')\s*\)\s*(.*)',
                re.IGNORECASE
            )
        
        # Lines to skip entirely — non-spoken content
        skip_re = re.compile(
            r'^[\s]*[-=_]{3,}[\s]*$'           # dashed/equals separator lines
            r'|^\s*\[.*\]\s*$'                  # [stage directions] on own line
            r'|^(?:end\s+)?transcript\b'          # End Transcript
            r'|^(?:end\s+of\s+)?(?:episode|podcast|debate|interview)\b'
            r'|^(?:opening|closing)\s+(?:positions?|statements?|remarks?)\s*$'
            r'|^(?:part|section|segment|act)\s+\d+\b',
            re.IGNORECASE
        )
        
        # ── Mega-line safety net ──
        # If the script has very few newlines relative to its length, the LLM
        # likely crammed all dialogue onto one line.  Split at speaker-label
        # boundaries so the parser can identify individual turns.
        raw_lines = [l for l in script.split('\n') if l.strip()]
        script_words = len(script.split())
        if len(raw_lines) <= 2 and script_words > 100:
            split_names = []
            if host_names:
                split_names.extend([re.escape(n) for n in host_names])
            split_names.extend([r'Host\s*[AB]', r'Speaker\s*[12]', 'Assistant', 'User'])
            split_pat = '|'.join(split_names)
            script = re.sub(
                rf'(?<=\S)\s+({split_pat})\s*:',
                lambda m: '\n' + m.group(1) + ':',
                script,
                flags=re.IGNORECASE
            )
            new_line_count = len([l for l in script.split('\n') if l.strip()])
            if new_line_count > len(raw_lines):
                print(f"[AudioGen] ⚠ _parse_script: Split mega-line into {new_line_count} speaker turns (was {len(raw_lines)} lines, {script_words} words)")
        
        segments = []
        current_speaker = "A"
        current_text = []
        
        for line in script.split('\n'):
            line = line.strip()
            if not line:
                continue
            
            # Detect section break marker from multi-pass generation
            if '---SECTION_BREAK---' in line:
                if current_text:
                    segments.append((current_speaker, ' '.join(current_text)))
                    current_text = []
                segments.append(("BREAK", ""))
                continue
            
            # Skip non-spoken lines entirely
            if skip_re.match(line):
                continue
            
            # Try character name match first — colon format: "Sarah: ..."
            matched = False
            if name_re:
                m = name_re.match(line)
                if m:
                    if current_text:
                        segments.append((current_speaker, ' '.join(current_text)))
                    current_speaker = name_map.get(m.group(1).lower(), "A")
                    rest = m.group(2).strip().rstrip('*').strip()
                    current_text = [rest] if rest else []
                    matched = True
            
            # Try parenthetical name format: "(Sarah) ..."
            if not matched and name_paren_re:
                m = name_paren_re.match(line)
                if m:
                    if current_text:
                        segments.append((current_speaker, ' '.join(current_text)))
                    current_speaker = name_map.get(m.group(1).lower(), "A")
                    rest = m.group(2).strip()
                    current_text = [rest] if rest else []
                    matched = True
            
            # Fall back to Host A/B pattern
            if not matched:
                m = host_re.match(line)
                if m:
                    if current_text:
                        segments.append((current_speaker, ' '.join(current_text)))
                    ident = m.group(1).upper()
                    current_speaker = "A" if ident in ("A", "1") else "B"
                    rest = m.group(2).strip().rstrip('*').strip()
                    current_text = [rest] if rest else []
                else:
                    current_text.append(line)
        
        # Add final segment
        if current_text:
            segments.append((current_speaker, ' '.join(current_text)))
        
        # If no segments parsed, treat whole script as one segment
        if not segments:
            segments = [("A", script)]
        
        print(f"[AudioGen] _parse_script: {len(segments)} segments found")
        return segments

    def _split_script_into_chunks(self, script: str, max_chars: int = 2000) -> List[str]:
        """Split script into chunks for TTS processing"""
        chunks = []
        paragraphs = script.split('\n\n')
        current_chunk = ""
        
        for para in paragraphs:
            para = para.strip()
            if not para:
                continue
            
            # If adding this paragraph exceeds max, save current and start new
            if len(current_chunk) + len(para) > max_chars and current_chunk:
                chunks.append(current_chunk.strip())
                current_chunk = para
            else:
                current_chunk += "\n\n" + para if current_chunk else para
        
        # Add final chunk
        if current_chunk:
            chunks.append(current_chunk.strip())
        
        # If no chunks, return whole script as one chunk
        if not chunks:
            chunks = [script]
        
        return chunks

    def _get_audio_duration(self, audio_file: Path) -> int:
        """Get audio duration in seconds via ffprobe, with wave fallback."""
        # Try ffprobe first
        try:
            result = subprocess.run(
                ["ffprobe", "-v", "error", "-show_entries", "format=duration",
                 "-of", "default=noprint_wrappers=1:nokey=1", str(audio_file)],
                capture_output=True,
                text=True
            )
            if result.returncode == 0 and result.stdout.strip():
                duration = int(float(result.stdout.strip()))
                if duration > 0:
                    return duration
            print(f"[AudioGen] ffprobe returned no duration (rc={result.returncode}, stderr={result.stderr.strip()})")
        except Exception as e:
            print(f"[AudioGen] ffprobe failed: {e}")
        
        # Fallback: read WAV header directly
        try:
            with wave.open(str(audio_file), 'r') as wf:
                duration = int(wf.getnframes() / wf.getframerate())
                print(f"[AudioGen] WAV fallback duration: {duration}s")
                return duration
        except Exception as e:
            print(f"[AudioGen] WAV fallback also failed: {e}")
        
        return 0

    async def list(self, notebook_id: str) -> List[Dict]:
        """List audio files for a notebook"""
        return await audio_store.list(notebook_id)

    async def get(self, notebook_id: str, audio_id: str) -> Optional[Dict]:
        """Get audio file info"""
        return await audio_store.get_by_notebook(notebook_id, audio_id)
    
    async def get_by_id(self, audio_id: str) -> Optional[Dict]:
        """Get audio file info by ID only"""
        return await audio_store.get(audio_id)
    
    async def delete(self, audio_id: str) -> bool:
        """Delete audio generation record"""
        return await audio_store.delete(audio_id)


audio_service = AudioGenerator()
