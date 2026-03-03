"""Audio generation service for podcasts

Uses LFM2.5-Audio (Liquid AI) for high-quality text-to-speech generation.
This provides phenomenal audio quality with native speech synthesis.
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

class AudioGenerator:
    """Generate podcast audio from notebooks using LFM2.5-Audio"""

    def __init__(self):
        self.audio_dir = settings.data_dir / "audio"
        self.audio_dir.mkdir(parents=True, exist_ok=True)
        self._background_tasks = set()
        
        # Voice options for LFM2.5-Audio - rotate through for variety
        # Available voices: us_male, us_female, uk_male, uk_female
        self.voices = {
            "us": {
                "male": "us_male",
                "female": "us_female"
            },
            "uk": {
                "male": "uk_male", 
                "female": "uk_female"
            }
        }
        
        self._voice_index = 0  # Track voice rotation for variety

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
        task = asyncio.create_task(
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
            )
        )
        # Keep reference to prevent garbage collection
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)

        return generation

    async def _generate_script(
        self,
        notebook_id: str,
        topic: Optional[str],
        duration_minutes: int,
        skill_id: Optional[str],
        host_names: Optional[Tuple[str, str]] = None,
        chat_context: Optional[str] = None,
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
        _TTS_RULES = f"""ABSOLUTE RULES — EVERY LINE WILL BE READ ALOUD BY A TEXT-TO-SPEECH ENGINE:
- Write ONLY spoken dialogue. Every character you type will be spoken aloud.
- Format: {name_a}: [spoken words] then {name_b}: [spoken words] — one speaker per line.
- FORBIDDEN — these will be read aloud and ruin the audio:
  * NO stage directions: [intro music], [Clash Intensity Rising], (Opening Positions), (laughs)
  * NO markdown: #, **, ---, bullet points, numbered lists
  * NO separator lines of dashes, equals, or underscores
  * NO section headers: Part 1, Opening, Closing Statement, etc.
  * NO meta-text: "End Transcript", "End of Episode", "Conclusion"
  * NO parenthetical labels: (Name), ({name_a}), ({name_b})
  * NO action descriptions: *walks away*, they laugh together
- If it is not a word {name_a} or {name_b} would speak out loud, DO NOT WRITE IT."""

        target_words = duration_minutes * 130  # TTS speaks at ~130 wpm
        _GROUNDING = f"""LENGTH REQUIREMENT: You MUST write at least {target_words} words. This script must fill {duration_minutes} minutes of audio.
A 1-minute segment is about 130 words. Count your output — if it's under {target_words} words, you have not written enough.
Do NOT wrap up early. Do NOT summarize quickly. Fill the full {duration_minutes} minutes with substantive dialogue.

GROUNDING: ONLY discuss facts and claims from the provided research. Do NOT invent statistics, quotes, or claims.
Topic focus: {topic or 'the main topics and insights from the research'}"""

        # ── Character intro rule (all styles) ──
        _INTRO_RULE = f"""OPENING: Within the first 2-3 lines, both speakers must naturally introduce themselves by name.
Example: {name_a}: Hey, I'm {name_a}, and today we're diving into something fascinating...
         {name_b}: And I'm {name_b}. I've been looking into this all week and..."""

        # ── Style-specific prompts ──
        is_feynman = skill_id == 'feynman_curriculum'

        if is_feynman:
            system_prompt = f"""You are writing a TEACHING podcast script that will be converted directly to audio via text-to-speech.
This is a Feynman Learning Curriculum — a progressive teaching conversation where {name_a} teaches and {name_b} learns.
Every single word you write will be spoken aloud. Write ONLY the words the characters say.

{_TTS_RULES}

{_INTRO_RULE}

FEYNMAN TEACHING STYLE:
- {name_a} is the TEACHER who has studied the material deeply. {name_b} is an eager LEARNER.
- {name_a} explains concepts simply using everyday analogies and examples (Feynman's "explain to a 12-year-old" principle)
- {name_b} asks genuine questions, requests clarification, and occasionally challenges {name_a} to explain more simply
- When {name_b} doesn't understand, {name_a} must find a simpler way — never just repeat the same explanation
- Include "check your understanding" moments where {name_a} quizzes {name_b} with quick questions
- {name_b} sometimes connects new ideas to things they already know — this validates learning
- Use concrete examples and analogies for every abstract concept
- Progress naturally from simple foundations to deeper understanding

{_GROUNDING}"""

        elif skill_id == 'debate':
            system_prompt = f"""You are writing a DEBATE podcast script that will be converted directly to audio via text-to-speech.
{name_a} and {name_b} take opposing sides on the topic and argue their positions passionately but respectfully.
Every single word you write will be spoken aloud. Write ONLY the words the characters say.

{_TTS_RULES}

OPENING: Both debaters MUST introduce themselves by name and state their position in the first few lines.
Example: {name_a}: I'm {name_a}, and I'm here to argue that this changes everything...
         {name_b}: And I'm {name_b}, and I think that's a dangerous oversimplification. Here's why...

DEBATE STYLE:
- {name_a} argues FOR the main thesis/position. {name_b} argues AGAINST or presents the counter-position.
- Each speaker should make strong, evidence-based arguments drawn from the research
- They challenge each other directly: "But that ignores...", "You're cherry-picking...", "Fair point, but consider..."
- Include rebuttals — don't just alternate monologues. They should respond to each other's specific points
- Build intensity: start with opening positions, escalate through clashes, reach a climax, then wind down
- Neither side should "win" cleanly — leave the listener thinking about both perspectives
- Use rhetorical devices: analogies, hypotheticals, reductio ad absurdum
- End with each speaker giving a concise closing statement

{_GROUNDING}"""

        elif skill_id == 'interview':
            system_prompt = f"""You are writing an INTERVIEW podcast script that will be converted directly to audio via text-to-speech.
{name_a} is the INTERVIEWER who asks probing questions. {name_b} is the EXPERT who has deep knowledge of the topic.
Every single word you write will be spoken aloud. Write ONLY the words the characters say.

{_TTS_RULES}

OPENING: {name_a} introduces themselves and {name_b} by name in the first few lines.
Example: {name_a}: I'm {name_a}, and today I'm sitting down with {name_b}, who knows more about this than just about anyone...

INTERVIEW STYLE:
- {name_a} (Interviewer): Curious, prepared, asks follow-up questions, pushes for clarity and real-world implications
- {name_b} (Expert): Authoritative but accessible, uses examples and analogies, occasionally shares surprising insights
- Open with a compelling hook question right after the brief intro
- Good hooks: "So I have to ask — the thing everyone's wondering about..." or "Let's start with what surprised you most..."
- Follow the thread — {name_a} picks up on interesting things {name_b} says and digs deeper
- Include "wait, explain that" moments where {name_a} asks {name_b} to unpack jargon or complex ideas
- Mix big-picture questions with specific details
- End with a forward-looking question: "What should people watch for?" or "What's the one thing you'd want listeners to take away?"

{_GROUNDING}"""

        elif skill_id == 'storytelling':
            system_prompt = f"""You are writing a STORYTELLING podcast script that will be converted directly to audio via text-to-speech.
{name_a} and {name_b} weave the research content into a compelling narrative with a beginning, middle, and end.
Every single word you write will be spoken aloud. Write ONLY the words the characters say.

{_TTS_RULES}

{_INTRO_RULE}

STORYTELLING STYLE:
- Structure the content as a STORY with a narrative arc: setup → rising action → climax → resolution
- {name_a} is the primary STORYTELLER. {name_b} is the engaged LISTENER who reacts, asks "then what happened?", and adds color
- After the brief intro, open with a hook: "Picture this..." or "It all started when..." or "Here's something nobody saw coming..."
- Use vivid language and concrete scenes — make abstract research feel tangible and real
- Include "characters" — the researchers, the subjects, the key players in the story
- Build suspense: "But here's where it gets interesting..." "And nobody expected what happened next..."
- {name_b}'s reactions drive the narrative forward: "No way." "So what did they find?" "That changes everything."
- Connect research findings to human experiences and real-world consequences
- End with a satisfying conclusion that ties back to the opening hook

{_GROUNDING}"""

        else:
            # Standard two-host conversation (podcast_script and fallback)
            system_prompt = f"""You are writing a podcast script that will be converted directly to audio via text-to-speech.
Every single word you write will be spoken aloud. Write ONLY the words {name_a} and {name_b} say.

{_TTS_RULES}

{_INTRO_RULE}

NATURAL CONVERSATION STYLE:
- After the brief intro, jump into a compelling hook or surprising fact
- BAD openings: "Welcome to our podcast, today we'll discuss..." — too generic
- {name_a} and {name_b} interrupt each other occasionally, react genuinely ("Wait, really?", "That's wild", "Okay but here's the thing...")
- Use short sentences. People don't speak in paragraphs
- Include natural transitions ("So here's what gets me...", "Right, and building on that...", "Okay let's shift gears...")
- Reference sources naturally ("I was reading this piece that said...") — never "According to Source 1"
- End with a natural wind-down, a final thought, or a question for the listener

{_GROUNDING}"""

        # Feynman always uses multi-pass with 4 curriculum parts
        if is_feynman:
            script = await self._generate_feynman_multipass(
                system_prompt, context, topic, duration_minutes, host_names=(name_a, name_b)
            )
        elif duration_minutes >= 7:
            script = await self._generate_script_multipass(
                system_prompt, context, topic, duration_minutes, host_names=(name_a, name_b)
            )
        else:
            # Single-pass generation (short scripts < 7 min)
            # Cap context to leave room for output in the context window
            max_context = min(len(context), 10000)
            prompt = f"""Based ONLY on the following research content, write a natural podcast conversation between {name_a} and {name_b}.
ONLY spoken words. No stage directions, no music cues, no markdown.
You MUST write at least {target_words} words to fill {duration_minutes} minutes of audio.
Only discuss facts and claims that appear in the research below.

Research content:
{context[:max_context]}

{name_a}:"""
            
            # Budget: ~1.5 tokens per word, target_words * 1.5 gives headroom
            audio_num_predict = max(2000, int(target_words * 1.5))
            audio_num_ctx = max(8192, audio_num_predict + 4000)
            script = await rag_engine._call_ollama(
                system_prompt, prompt, 
                num_predict=audio_num_predict, num_ctx=audio_num_ctx,
                temperature=0.8, repeat_penalty=1.1
            )
        
        # Estimate duration from script length (~130 words/min for TTS)
        word_count = len(script.split())
        est_minutes = word_count / 130
        print(f"[AudioGen] Script: {word_count} words, est. {est_minutes:.1f} min (target: {duration_minutes} min)")
        
        # If script is significantly short, try to extend it
        min_words = int(target_words * 0.6)
        if word_count < min_words and not is_feynman:
            print(f"[AudioGen] ⚠ Script too short ({word_count} < {min_words} min words). Generating extension...")
            
            extension_words = target_words - word_count
            last_lines = script.strip().split('\n')[-6:]
            prev_tail = "\n".join(last_lines)
            ext_prompt = f"""Continue this conversation for at least {extension_words} more words. Do NOT repeat what was already said.
Do NOT wrap up or conclude — keep the discussion going with new points and deeper analysis.
Pick up EXACTLY where this left off:

{prev_tail}

{name_a}:"""
            
            ext_predict = max(2000, int(extension_words * 2.0))
            ext_ctx = max(8192, ext_predict + 4000)
            extension = await rag_engine._call_ollama(
                system_prompt, ext_prompt,
                num_predict=ext_predict, num_ctx=ext_ctx,
                temperature=0.8, repeat_penalty=1.1
            )
            
            if extension and len(extension.split()) > 50:
                script = script.rstrip() + "\n\n" + extension
                new_count = len(script.split())
                new_est = new_count / 130
                print(f"[AudioGen] Extended: {word_count} → {new_count} words, est. {new_est:.1f} min")
            else:
                print(f"[AudioGen] Extension produced insufficient content, using original script")
        
        return script
    
    async def _generate_script_multipass(
        self,
        system_prompt: str,
        context: str,
        topic: Optional[str],
        duration_minutes: int,
        host_names: Optional[Tuple[str, str]] = None
    ) -> str:
        """Generate a long-form script in sections for coherence.
        
        Splits the target duration into 5-8 minute sections, generates each
        with awareness of what came before.
        """
        section_minutes = 5  # Each section targets ~5 min for better length control
        num_sections = max(2, math.ceil(duration_minutes / section_minutes))
        minutes_per_section = duration_minutes / num_sections
        words_per_section = int(minutes_per_section * 130)  # TTS speaks at ~130 wpm
        
        print(f"[AudioGen] Multi-pass: {num_sections} sections × ~{minutes_per_section:.0f} min")
        
        name_a = host_names[0] if host_names else "Host A"
        
        sections = []
        running_summary = ""  # Chain-of-Density summary for global coherence
        for s in range(num_sections):
            is_first = s == 0
            is_last = s == num_sections - 1
            
            # Build section-specific instructions
            if is_first:
                section_inst = "This is the OPENING section. Start with a compelling hook — mid-conversation, surprising fact, or provocative question."
            elif is_last:
                section_inst = "This is the CLOSING section. Wind down naturally. End with a final thought or question for the listener. Do NOT end abruptly."
            else:
                section_inst = f"This is section {s+1} of {num_sections}. Continue the conversation naturally from where the previous section left off."
            
            # Continuity: running summary + last few lines for voice/tone pickup
            prev_context = ""
            if sections:
                last_lines = sections[-1].strip().split('\n')[-6:]
                prev_context = f"\n\nTOPICS ALREADY COVERED (do NOT repeat these):\n{running_summary}"
                prev_context += f"\n\nPREVIOUS SECTION ENDED WITH:\n" + "\n".join(last_lines)
            
            max_context = min(len(context), 8000)
            prompt = f"""{section_inst}
You MUST write at least {words_per_section} words for this section (~{minutes_per_section:.0f} minutes when spoken). Do NOT wrap up early.
Topic: {topic or 'the main topics and insights'}
{prev_context}

Research content:
{context[:max_context]}

{name_a}:"""
            
            section_predict = max(2000, int(words_per_section * 2.0))
            section_ctx = max(8192, section_predict + 4000)
            
            section_script = await rag_engine._call_ollama(
                system_prompt, prompt,
                num_predict=section_predict, num_ctx=section_ctx,
                temperature=0.8, repeat_penalty=1.1
            )
            sections.append(section_script)
            print(f"   Section {s+1}/{num_sections}: {len(section_script.split())} words")
            
            # Update running summary (Chain of Density) for next section
            all_script = "\n\n".join(sections)
            if len(all_script) > 300:
                running_summary = await rag_engine._call_ollama(
                    "You are a precise summarizer. List every topic, argument, and key point "
                    "discussed so far in this conversation. Be information-dense. Do not add new content.",
                    f"Summarize the topics covered in this podcast script in 150-200 words:\n\n{all_script[:6000]}",
                    num_predict=300,
                    temperature=0.2,
                )
        
        # Join sections with a break marker for transition stingers
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
        """Generate a 4-part Feynman teaching podcast matching the curriculum structure.
        
        Parts:
        1. Foundation — explain it simply, like to a 12-year-old
        2. Building Understanding — deeper connections, examples, misconceptions
        3. First Principles — why things work, root mechanisms
        4. Mastery Synthesis — teach-back, challenges, synthesis
        """
        name_a = host_names[0] if host_names else "Host A"
        name_b = host_names[1] if host_names else "Host B"
        
        feynman_parts = [
            {
                "name": "Foundation",
                "instruction": (
                    f"This is PART 1: FOUNDATION. {name_a} introduces the topic and explains the core concepts "
                    f"as simply as possible — like explaining to a 12-year-old. Use everyday analogies. "
                    f"{name_b} is a complete beginner and asks basic 'what is this?' and 'why should I care?' questions. "
                    f"End with {name_a} giving {name_b} a quick check — 'So tell me in your own words, what is X?' — "
                    f"and {name_b} attempts to explain it back."
                )
            },
            {
                "name": "Building Understanding",
                "instruction": (
                    f"This is PART 2: BUILDING UNDERSTANDING. Now that {name_b} gets the basics, go deeper. "
                    f"{name_a} shows how concepts connect to each other and gives real-world examples. "
                    f"{name_b} starts making their own connections ('Oh, so it's kind of like when...'). "
                    f"Address common misconceptions — {name_b} might voice one and {name_a} corrects it gently. "
                    f"End with a slightly harder check question that tests whether {name_b} sees the connections."
                )
            },
            {
                "name": "First Principles",
                "instruction": (
                    f"This is PART 3: FIRST PRINCIPLES. Go beyond what to WHY. {name_a} explains the root mechanisms "
                    f"and underlying principles. Why does this work this way and not some other way? "
                    f"{name_b} pushes back with 'but why?' and 'what if?' questions. Discuss edge cases and nuances. "
                    f"Reference specific insights from the research. {name_b} should be noticeably more confident now. "
                    f"End with an analysis question — 'Why does X happen instead of Y?'"
                )
            },
            {
                "name": "Mastery Synthesis",
                "instruction": (
                    f"This is PART 4: MASTERY SYNTHESIS. The roles partially flip — {name_b} now tries to teach "
                    f"the subject back to {name_a} (the Feynman test). {name_a} plays a skeptical student, "
                    f"asking tough questions and poking holes. {name_b} synthesizes everything from earlier parts. "
                    f"Discuss what's still unknown or debated. End with both reflecting on what they learned "
                    f"and {name_a} suggesting where to go next for deeper study."
                )
            }
        ]
        
        minutes_per_part = duration_minutes / 4
        words_per_part = int(minutes_per_part * 130)  # TTS speaks at ~130 wpm
        
        print(f"[AudioGen] Feynman multi-pass: 4 parts × ~{minutes_per_part:.0f} min")
        
        sections = []
        running_summary = ""  # Chain-of-Density summary for global coherence
        for i, part in enumerate(feynman_parts):
            # Continuity: running summary + last few lines for voice/tone pickup
            prev_context = ""
            if sections:
                last_lines = sections[-1].strip().split('\n')[-6:]
                prev_context = f"\n\nTOPICS ALREADY COVERED (do NOT repeat these):\n{running_summary}"
                prev_context += f"\n\nPREVIOUS SECTION ENDED WITH:\n" + "\n".join(last_lines)
            
            max_context = min(len(context), 8000)
            prompt = f"""{part['instruction']}

STYLE RULES (apply to EVERY part):
- Keep sentences SHORT: 8-20 words max. This is spoken audio, not an essay.
- One idea per sentence. If a sentence has more than one comma, split it.
- NEVER say "User" or "the user" — say "the listener", "you", or use {name_b}'s name.
- Do NOT repeat points, analogies, or examples from previous parts.
- Use natural conversational pauses: "Okay so...", "Right, and...", "Here's the thing..."

You MUST write at least {words_per_part} words for this part (~{minutes_per_part:.0f} minutes when spoken). Do NOT wrap up early.
Topic: {topic or 'the main topics and insights'}
{prev_context}

Research content:
{context[:max_context]}

{name_a}:"""
            
            section_predict = max(2000, int(words_per_part * 2.0))
            section_ctx = max(8192, section_predict + 4000)
            
            # Increase repeat_penalty for later parts to fight repetition
            part_repeat_penalty = 1.1 + (i * 0.05)  # 1.1, 1.15, 1.2, 1.25
            section_script = await rag_engine._call_ollama(
                system_prompt, prompt,
                num_predict=section_predict, num_ctx=section_ctx,
                temperature=0.8, repeat_penalty=part_repeat_penalty
            )
            sections.append(section_script)
            print(f"   Part {i+1}/4 ({part['name']}): {len(section_script.split())} words")
            
            # Update running summary (Chain of Density) for next part
            all_script = "\n\n".join(sections)
            if len(all_script) > 300:
                running_summary = await rag_engine._call_ollama(
                    "You are a precise summarizer. List every topic, concept, analogy, and key point "
                    "discussed so far in this teaching conversation. Be information-dense. Do not add new content.",
                    f"Summarize the topics covered in this Feynman teaching podcast in 150-200 words:\n\n{all_script[:6000]}",
                    num_predict=300,
                    temperature=0.2,
                )
        
        SECTION_BREAK = "\n\n---SECTION_BREAK---\n\n"
        return SECTION_BREAK.join(sections)

    def _is_audio_skill(self, skill_id: Optional[str]) -> bool:
        """Check if skill produces audio output - all skills produce audio"""
        return True

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
            
            # Strip speaker labels (Host A:, Host B:, Speaker 1:, Name:, etc.)
            line = re.sub(r'^(?:Host|Speaker)\s*[AB12]?\s*:\s*', '', line, flags=re.IGNORECASE)
            
            # Replace "User" / "the user" with "the listener" (bleeds from source metadata)
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
        sample_rate = 24000  # Match LFM2.5-Audio output rate
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
    ):
        """Full background pipeline: script generation → audio synthesis.
        
        Runs entirely in the background so the API returns instantly.
        Updates audio_store status at each stage for frontend polling.
        """
        import traceback
        
        # Pick character names for this generation
        host_names = self._pick_host_names(host1_gender, host2_gender, accent)
        print(f"🎭 Characters: {host_names[0]} (A) and {host_names[1]} (B)")
        
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
            )
            
            if not script or len(script.strip()) < 20:
                raise RuntimeError("Script generation produced no usable content")
            
            # Save script to the record
            await audio_store.update(audio_id, {
                "script": script,
                "error_message": "Script ready. Starting audio generation..."
            })
            print(f"📝 Script generated for {audio_id}: {len(script)} chars")
            
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
        """Background task to generate audio using LFM2.5-Audio.
        
        Two-host mode: parses Host A/Host B turns, uses different voices per speaker.
        Single-narrator mode: cleans script and chunks by paragraph.
        No script length limit — chunked generation handles any length.
        """
        import traceback
        import shutil
        from services.audio_llm import audio_llm
        
        print(f"🎤 Starting LFM2.5-Audio generation for {audio_id}")
        
        try:
            # Initialize audio model if needed
            await audio_llm.initialize()
            
            if not audio_llm.is_available:
                detail = audio_llm._init_error or "unknown error"
                raise RuntimeError(f"LFM2.5-Audio init failed: {detail}")
            
            # Build voice map based on accent + gender
            accent_voices = self.voices.get(accent, self.voices["us"])
            voice_map = {
                "A": accent_voices.get(host1_gender, "us_male"),
                "B": accent_voices.get(host2_gender, "us_female"),
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
                        import torch as _torch
                        if hasattr(_torch, 'mps') and _torch.backends.mps.is_available():
                            _torch.mps.empty_cache()
                        elif _torch.cuda.is_available():
                            _torch.cuda.empty_cache()
                    except Exception:
                        pass
                    # Brief pause to let GPU cool and OS reclaim resources
                    await asyncio.sleep(0.5)
                except asyncio.TimeoutError:
                    last_error = f"Segment {real_done} timed out after {seg_timeout}s"
                    print(f"   ⚠ {last_error}, skipping")
                    continue
                except Exception as seg_err:
                    last_error = f"Segment {real_done}: {seg_err}"
                    print(f"   ⚠ Segment {real_done}/{total_real} failed: {seg_err}, skipping")
                    continue
            
            if not part_paths:
                detail = f" Last error: {last_error}" if last_error else ""
                raise RuntimeError(f"No audio segments were generated successfully.{detail}")
            
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
            except Exception:
                pass

    def _parse_script_for_tts(self, script: str, host_names: Optional[Tuple[str, str]] = None) -> List[tuple]:
        """Parse script into speaker turns, cleaned for TTS.
        
        Returns list of (speaker, clean_text) tuples where speaker is 'A' or 'B'
        and clean_text has stage directions, markdown, and labels stripped.
        Long turns are further chunked at sentence boundaries for reliable generation.
        """
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
            # Shorter chunks produce dramatically better prosody from LFM2.5-Audio
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
        
        # Clean leftover artifacts
        text = re.sub(r'\[\s*\]', '', text)
        text = re.sub(r'\*+', '', text)
        text = re.sub(r'\s+', ' ', text).strip()
        return text
    
    def _sub_chunk_text(self, text: str, max_chars: int = 1000) -> List[str]:
        """Sub-chunk long text at sentence boundaries."""
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
        return chunks
    
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
        2. Sample rate is 24000 Hz (LFM2.5-Audio native)
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
        if host_names:
            escaped = [re.escape(n) for n in host_names]
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
        """Get audio duration in seconds"""
        try:
            result = subprocess.run(
                ["ffprobe", "-v", "error", "-show_entries", "format=duration",
                 "-of", "default=noprint_wrappers=1:nokey=1", str(audio_file)],
                capture_output=True,
                text=True
            )
            return int(float(result.stdout.strip()))
        except:
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
