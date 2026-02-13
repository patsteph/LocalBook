"""Audio generation service for podcasts

Uses LFM2.5-Audio (Liquid AI) for high-quality text-to-speech generation.
This provides phenomenal audio quality with native speech synthesis.
"""
import asyncio
import math
import re
import struct
import subprocess
import wave
from pathlib import Path
from typing import Dict, List, Optional
from config import settings
from storage.audio_store import audio_store
from storage.source_store import source_store
from storage.skills_store import skills_store
from services.rag_engine import rag_engine

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

    async def generate(
        self,
        notebook_id: str,
        topic: Optional[str] = None,
        duration_minutes: int = 10,
        skill_id: Optional[str] = None,
        host1_gender: str = "male",
        host2_gender: str = "female",
        accent: str = "us"
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
                is_two_host=is_two_host
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
        skill_id: Optional[str]
    ) -> str:
        """Generate podcast script from notebook sources.
        
        Scales source utilization and generation strategy with duration:
        - Short (1-10 min): single-pass, top sources
        - Medium (11-20 min): single-pass, more sources, larger context
        - Long (21-30 min): multi-pass sections for coherent long-form
        """
        
        # Scale source utilization with duration
        max_sources = min(len(await source_store.list(notebook_id)), 
                         3 if duration_minutes <= 5 else 8 if duration_minutes <= 15 else 15)
        chars_per_source = 2000 if duration_minutes <= 5 else 4000 if duration_minutes <= 15 else 6000
        
        sources = await source_store.list(notebook_id)
        content_parts = []
        for source in sources[:max_sources]:
            source_content = await source_store.get_content(notebook_id, source["id"])
            if source_content and source_content.get("content"):
                content_parts.append(
                    f"Source: {source.get('filename', 'Unknown')}\n"
                    f"{source_content['content'][:chars_per_source]}"
                )
        
        context = "\n\n---\n\n".join(content_parts)
        
        # Feynman curriculum uses a specialized teaching-style prompt
        is_feynman = skill_id == 'feynman_curriculum'
        
        if is_feynman:
            system_prompt = f"""You are writing a TEACHING podcast script that will be converted directly to audio via text-to-speech.
This is a Feynman Learning Curriculum — a progressive teaching conversation where one host teaches and the other learns.
Every single word you write will be spoken aloud. Write ONLY the words the hosts say.

CRITICAL RULES FOR TTS OUTPUT:
- NEVER write stage directions, sound effects, or music cues (no "[intro music]", no "*theme plays*", no "(laughs)")
- NEVER use markdown formatting (no #, **, ---, or bullet points)
- NEVER describe actions — only write spoken dialogue
- Use ONLY this format: Host A: [spoken words]  /  Host B: [spoken words]

FEYNMAN TEACHING STYLE:
- Host A is the TEACHER who has studied the material deeply. Host B is an eager LEARNER.
- Host A explains concepts simply using everyday analogies and examples (Feynman's "explain to a 12-year-old" principle)
- Host B asks genuine questions, requests clarification, and occasionally challenges Host A to explain more simply
- When Host B doesn't understand, Host A must find a simpler way — never just repeat the same explanation
- Include "check your understanding" moments where Host A quizzes Host B with quick questions
- Host B sometimes connects new ideas to things they already know — this validates learning
- Use concrete examples and analogies for every abstract concept
- Progress naturally from simple foundations to deeper understanding

GROUNDING: ONLY discuss facts and claims from the provided research. Do NOT invent statistics, quotes, or claims.

Target length: approximately {duration_minutes} minutes when read aloud (~{duration_minutes * 150} words).
Topic focus: {topic or 'the main topics and insights from the research'}"""
        else:
            # Standard two-host conversation
            system_prompt = f"""You are writing a podcast script that will be converted directly to audio via text-to-speech.
Every single word you write will be spoken aloud. Write ONLY the words the hosts say.

CRITICAL RULES FOR TTS OUTPUT:
- NEVER write stage directions, sound effects, or music cues (no "[intro music]", no "*theme plays*", no "(laughs)")
- NEVER use markdown formatting (no #, **, ---, or bullet points)
- NEVER describe actions — only write spoken dialogue
- NEVER start with a generic "Welcome to the show" opening
- Use ONLY this format: Host A: [spoken words]  /  Host B: [spoken words]

NATURAL CONVERSATION STYLE:
- Open mid-conversation or with a compelling hook
- Good openings: a surprising fact, a provocative question, a "you won't believe what I found" moment
- BAD openings: "Welcome to our podcast, I'm [name] and today we'll discuss..."
- Hosts interrupt each other occasionally, react genuinely ("Wait, really?", "That's wild", "Okay but here's the thing...")
- Use short sentences. People don't speak in paragraphs
- Include natural transitions ("So here's what gets me...", "Right, and building on that...", "Okay let's shift gears...")
- Reference sources naturally ("I was reading this piece that said...") — never "According to Source 1"
- End with a natural wind-down, a final thought, or a question for the listener

GROUNDING: ONLY discuss facts and claims from the provided research. Do NOT invent statistics, quotes, or claims.

Target length: approximately {duration_minutes} minutes when read aloud (~{duration_minutes * 150} words).
Topic focus: {topic or 'the main topics and insights from the research'}"""

        # Feynman always uses multi-pass with 4 curriculum parts
        if is_feynman:
            script = await self._generate_feynman_multipass(
                system_prompt, context, topic, duration_minutes
            )
        elif duration_minutes > 15:
            script = await self._generate_script_multipass(
                system_prompt, context, topic, duration_minutes
            )
        else:
            # Single-pass generation
            # Cap context to leave room for output in the context window
            max_context = min(len(context), 10000)
            prompt = f"""Based ONLY on the following research content, write a natural podcast conversation between Host A and Host B.
ONLY spoken words. No stage directions, no music cues, no markdown. Start with a hook, not a welcome.
Only discuss facts and claims that appear in the research below.

Research content:
{context[:max_context]}

Host A:"""
            
            audio_num_predict = max(2000, duration_minutes * 400)
            audio_num_ctx = max(4096, audio_num_predict + 3000)
            script = await rag_engine._call_ollama(
                system_prompt, prompt, 
                num_predict=audio_num_predict, num_ctx=audio_num_ctx
            )
        
        # Estimate duration from script length (~150 words/min, ~5 chars/word)
        word_count = len(script.split())
        est_minutes = word_count / 150
        print(f"[AudioGen] Script: {word_count} words, est. {est_minutes:.1f} min (target: {duration_minutes} min)")
        
        return script
    
    async def _generate_script_multipass(
        self,
        system_prompt: str,
        context: str,
        topic: Optional[str],
        duration_minutes: int
    ) -> str:
        """Generate a long-form script in sections for coherence.
        
        Splits the target duration into 5-8 minute sections, generates each
        with awareness of what came before.
        """
        section_minutes = 7  # Each section targets ~7 min
        num_sections = max(2, math.ceil(duration_minutes / section_minutes))
        minutes_per_section = duration_minutes / num_sections
        words_per_section = int(minutes_per_section * 150)
        
        print(f"[AudioGen] Multi-pass: {num_sections} sections × ~{minutes_per_section:.0f} min")
        
        sections = []
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
            
            # Include previous section ending for continuity
            prev_context = ""
            if sections:
                last_lines = sections[-1].strip().split('\n')[-6:]
                prev_context = f"\n\nPREVIOUS SECTION ENDED WITH:\n" + "\n".join(last_lines)
            
            max_context = min(len(context), 8000)
            prompt = f"""{section_inst}
Target: ~{words_per_section} words (~{minutes_per_section:.0f} minutes when spoken).
Topic: {topic or 'the main topics and insights'}
{prev_context}

Research content:
{context[:max_context]}

Host A:"""
            
            section_predict = max(1500, int(words_per_section * 1.5))
            section_ctx = max(4096, section_predict + 3000)
            
            section_script = await rag_engine._call_ollama(
                system_prompt, prompt,
                num_predict=section_predict, num_ctx=section_ctx
            )
            sections.append(section_script)
            print(f"   Section {s+1}/{num_sections}: {len(section_script.split())} words")
        
        # Join sections with a break marker for transition stingers
        SECTION_BREAK = "\n\n---SECTION_BREAK---\n\n"
        return SECTION_BREAK.join(sections)
    
    async def _generate_feynman_multipass(
        self,
        system_prompt: str,
        context: str,
        topic: Optional[str],
        duration_minutes: int
    ) -> str:
        """Generate a 4-part Feynman teaching podcast matching the curriculum structure.
        
        Parts:
        1. Foundation — explain it simply, like to a 12-year-old
        2. Building Understanding — deeper connections, examples, misconceptions
        3. First Principles — why things work, root mechanisms
        4. Mastery Synthesis — teach-back, challenges, synthesis
        """
        feynman_parts = [
            {
                "name": "Foundation",
                "instruction": (
                    "This is PART 1: FOUNDATION. Host A introduces the topic and explains the core concepts "
                    "as simply as possible — like explaining to a 12-year-old. Use everyday analogies. "
                    "Host B is a complete beginner and asks basic 'what is this?' and 'why should I care?' questions. "
                    "End with Host A giving Host B a quick check — 'So tell me in your own words, what is X?' — "
                    "and Host B attempts to explain it back."
                )
            },
            {
                "name": "Building Understanding",
                "instruction": (
                    "This is PART 2: BUILDING UNDERSTANDING. Now that Host B gets the basics, go deeper. "
                    "Host A shows how concepts connect to each other and gives real-world examples. "
                    "Host B starts making their own connections ('Oh, so it's kind of like when...'). "
                    "Address common misconceptions — Host B might voice one and Host A corrects it gently. "
                    "End with a slightly harder check question that tests whether Host B sees the connections."
                )
            },
            {
                "name": "First Principles",
                "instruction": (
                    "This is PART 3: FIRST PRINCIPLES. Go beyond what to WHY. Host A explains the root mechanisms "
                    "and underlying principles. Why does this work this way and not some other way? "
                    "Host B pushes back with 'but why?' and 'what if?' questions. Discuss edge cases and nuances. "
                    "Reference specific insights from the research. Host B should be noticeably more confident now. "
                    "End with an analysis question — 'Why does X happen instead of Y?'"
                )
            },
            {
                "name": "Mastery Synthesis",
                "instruction": (
                    "This is PART 4: MASTERY SYNTHESIS. The roles partially flip — Host B now tries to teach "
                    "the subject back to Host A (the Feynman test). Host A plays a skeptical student, "
                    "asking tough questions and poking holes. Host B synthesizes everything from earlier parts. "
                    "Discuss what's still unknown or debated. End with both hosts reflecting on what they learned "
                    "and Host A suggesting where to go next for deeper study."
                )
            }
        ]
        
        minutes_per_part = duration_minutes / 4
        words_per_part = int(minutes_per_part * 150)
        
        print(f"[AudioGen] Feynman multi-pass: 4 parts × ~{minutes_per_part:.0f} min")
        
        sections = []
        for i, part in enumerate(feynman_parts):
            prev_context = ""
            if sections:
                last_lines = sections[-1].strip().split('\n')[-6:]
                prev_context = f"\n\nPREVIOUS SECTION ENDED WITH:\n" + "\n".join(last_lines)
            
            max_context = min(len(context), 8000)
            prompt = f"""{part['instruction']}
Target: ~{words_per_part} words (~{minutes_per_part:.0f} minutes when spoken).
Topic: {topic or 'the main topics and insights'}
{prev_context}

Research content:
{context[:max_context]}

Host A:"""
            
            section_predict = max(1500, int(words_per_part * 1.5))
            section_ctx = max(4096, section_predict + 3000)
            
            section_script = await rag_engine._call_ollama(
                system_prompt, prompt,
                num_predict=section_predict, num_ctx=section_ctx
            )
            sections.append(section_script)
            print(f"   Part {i+1}/4 ({part['name']}): {len(section_script.split())} words")
        
        SECTION_BREAK = "\n\n---SECTION_BREAK---\n\n"
        return SECTION_BREAK.join(sections)

    def _is_audio_skill(self, skill_id: Optional[str]) -> bool:
        """Check if skill produces audio output - all skills produce audio"""
        return True

    def _clean_script_for_tts(self, script: str) -> str:
        """Strip non-spoken artifacts from script before sending to TTS.
        
        Removes stage directions, markdown formatting, speaker labels,
        and other text that should not be read aloud.
        """
        lines = script.split('\n')
        cleaned = []
        
        for line in lines:
            line = line.strip()
            if not line:
                continue
            
            # Remove stage directions: *[anything]*, [anything in brackets], (stage directions)
            line = re.sub(r'\*\[.*?\]\*', '', line)
            line = re.sub(r'\[.*?\]', '', line)
            line = re.sub(r'\((?:laughs?|chuckles?|sighs?|pauses?|music|theme|intro|outro|sfx|sound|transition|beat)[^)]*\)', '', line, flags=re.IGNORECASE)
            
            # Remove markdown formatting
            line = re.sub(r'^#{1,6}\s+', '', line)       # Headings
            line = re.sub(r'\*\*(.+?)\*\*', r'\1', line) # Bold
            line = re.sub(r'\*(.+?)\*', r'\1', line)     # Italic
            line = re.sub(r'^---+\s*$', '', line)         # Horizontal rules
            line = re.sub(r'^\s*[-*]\s+', '', line)       # Bullet points
            line = re.sub(r'^\s*\d+\.\s+', '', line)      # Numbered lists
            
            # Strip speaker labels (Host A:, Host B:, Speaker 1:, etc.)
            line = re.sub(r'^(?:Host|Speaker)\s*[AB12]?\s*:\s*', '', line, flags=re.IGNORECASE)
            
            # Remove leftover empty parens, brackets, asterisks
            line = re.sub(r'\(\s*\)', '', line)
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
        is_two_host: bool = True
    ):
        """Full background pipeline: script generation → audio synthesis.
        
        Runs entirely in the background so the API returns instantly.
        Updates audio_store status at each stage for frontend polling.
        """
        import traceback
        
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
                skill_id=skill_id
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
                is_two_host=is_two_host
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
        is_two_host: bool = True
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
                segments = self._parse_script_for_tts(script)
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

    def _parse_script_for_tts(self, script: str) -> List[tuple]:
        """Parse script into speaker turns, cleaned for TTS.
        
        Returns list of (speaker, clean_text) tuples where speaker is 'A' or 'B'
        and clean_text has stage directions, markdown, and labels stripped.
        Long turns are further chunked at sentence boundaries for reliable generation.
        """
        # First parse into raw speaker segments
        raw_segments = self._parse_script(script)
        
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
            
            # If a turn is very long (>750 chars), sub-chunk it but keep same speaker
            if len(clean) > 750:
                sub_chunks = self._sub_chunk_text(clean, max_chars=750)
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
        """Clean a single text segment for TTS (no speaker labels to strip)."""
        # Remove stage directions
        text = re.sub(r'\*\[.*?\]\*', '', text)
        text = re.sub(r'\[.*?\]', '', text)
        text = re.sub(r'\((?:laughs?|chuckles?|sighs?|pauses?|music|theme|intro|outro|sfx|sound|transition|beat)[^)]*\)', '', text, flags=re.IGNORECASE)
        # Remove markdown
        text = re.sub(r'^#{1,6}\s+', '', text, flags=re.MULTILINE)
        text = re.sub(r'\*\*(.+?)\*\*', r'\1', text)
        text = re.sub(r'\*(.+?)\*', r'\1', text)
        text = re.sub(r'^---+\s*$', '', text, flags=re.MULTILINE)
        # Clean leftover artifacts
        text = re.sub(r'\(\s*\)', '', text)
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
        """Concatenate multiple WAV files into one. Pure Python, no ffmpeg needed."""
        if not part_paths:
            return
        
        # If only one part, just copy it
        if len(part_paths) == 1:
            import shutil
            shutil.copy2(part_paths[0], output_path)
            return
        
        # Read all parts and concatenate raw audio data with silence gaps
        all_frames = b""
        params = None
        SILENCE_MS = 300  # milliseconds of silence between segments
        
        for idx, p in enumerate(part_paths):
            try:
                with wave.open(str(p), 'rb') as wf:
                    if params is None:
                        params = wf.getparams()
                    all_frames += wf.readframes(wf.getnframes())
                    # Add silence between segments (not after the last one)
                    if idx < len(part_paths) - 1 and params:
                        silence_frames = int(params.framerate * SILENCE_MS / 1000) * params.sampwidth
                        all_frames += b'\x00' * silence_frames
            except Exception as e:
                print(f"   Warning: couldn't read {p.name}: {e}")
                continue
        
        if params and all_frames:
            with wave.open(str(output_path), 'wb') as out:
                out.setparams(params)
                out.writeframes(all_frames)
    
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

    def _parse_script(self, script: str) -> List[tuple]:
        """Parse script into speaker segments.
        
        Handles various label formats the LLM might produce:
          Host A: ...       **Host A:** ...      *Host A:* ...
          Speaker A: ...    ## Host A: ...       A: ...
          Host 1: ...       Speaker 1: ...
        """
        # Regex: optional markdown prefix (**/*/##), then label, then colon
        speaker_re = re.compile(
            r'^[\s*#]*'                        # leading whitespace, *, #
            r'(?:host|speaker)?\s*'            # optional "host" / "speaker"
            r'([AaBb1-2])'                     # speaker identifier
            r'[\s*#]*:[\s*]*'                  # colon with optional surrounding markdown
            r'(.*)',                            # rest of line
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
            
            m = speaker_re.match(line)
            if m:
                # Save previous segment
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
