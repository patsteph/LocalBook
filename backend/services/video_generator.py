"""Video Generation Orchestrator — Full pipeline from notebook to MP4.

Coordinates the video generation pipeline:
1. Generate storyboard (LLM → JSON scenes)
2. Generate narration script + TTS audio
3. Render slides (Playwright → PNG)
4. Composite final video (FFmpeg → MP4)

Follows the same background-task pattern as audio_generator.py:
- Returns immediately with a 'pending' record
- Full pipeline runs in background via asyncio.create_task
- Progress updates stored in video_store

This module is completely independent — it does NOT modify any existing services.
"""

import asyncio
import json
import logging
import random
import shutil
import time
import traceback
from pathlib import Path
from typing import Dict, Optional

from config import settings
from storage.video_store import video_store

logger = logging.getLogger(__name__)


class VideoGenerator:
    """Generate explainer videos from notebooks."""

    # Voice pools per accent × gender — same as audio_generator for consistency
    VOICE_POOLS = {
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
            "male":   ["ff_siwis"],
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
            "male":   ["jf_alpha"],
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

    def __init__(self):
        self.video_dir = settings.data_dir / "video"
        self.video_dir.mkdir(parents=True, exist_ok=True)
        self._background_tasks = set()

    def _resolve_narrator_voice(self, narrator_gender: str, accent: str, voice_override: Optional[str] = None) -> str:
        """Resolve narrator_gender + accent to a random Kokoro voice ID.

        If voice_override is provided (legacy direct Kokoro ID), use that instead.
        """
        if voice_override:
            from services.audio_llm import resolve_voice
            return resolve_voice(voice_override)
        accent_pool = self.VOICE_POOLS.get(accent, self.VOICE_POOLS["us"])
        gender_pool = accent_pool.get(narrator_gender, accent_pool.get("female", ["af_heart"]))
        return random.choice(gender_pool)

    async def generate(
        self,
        notebook_id: str,
        topic: Optional[str] = None,
        duration_minutes: int = 5,
        visual_style: str = "classic",
        narrator_gender: str = "female",
        accent: str = "us",
        voice: Optional[str] = None,
        format_type: str = "explainer",
        chat_context: Optional[str] = None,
    ) -> Dict:
        """Generate a video with narration.

        Args:
            notebook_id: Source notebook ID
            topic: Optional focus topic
            duration_minutes: Target length (1-10)
            visual_style: Slide visual style (classic, dark, whiteboard, etc.)
            narrator_gender: "male" or "female"
            accent: "us", "uk", "es", "fr", etc.
            voice: Legacy override — direct Kokoro voice ID. None = resolve from gender+accent.
            format_type: "explainer" or "brief"

        Returns:
            Video generation record with video_id and status='pending'
        """
        # Clamp duration
        duration_minutes = max(1, min(10, duration_minutes))

        # Resolve narrator voice from gender + accent (or legacy override)
        resolved_voice = self._resolve_narrator_voice(narrator_gender, accent, voice)
        logger.info(f"[VideoGen] Narrator: gender={narrator_gender}, accent={accent} → voice={resolved_voice}")

        # Create record FIRST — return instantly to frontend
        generation = await video_store.create(
            notebook_id=notebook_id,
            topic=topic or "the research content",
            duration_minutes=duration_minutes,
            visual_style=visual_style,
            voice=resolved_voice,
            format_type=format_type,
        )

        video_id = generation["video_id"]
        print(f"🎬 Starting background video pipeline for {video_id}")

        # Start full pipeline in background
        from utils.tasks import safe_create_task
        task = safe_create_task(
            self._full_pipeline(
                video_id=video_id,
                notebook_id=notebook_id,
                topic=topic,
                duration_minutes=duration_minutes,
                visual_style=visual_style,
                voice=resolved_voice,
                format_type=format_type,
                chat_context=chat_context,
            ),
            name=f"video-pipeline-{video_id}"
        )
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)

        return generation

    async def _full_pipeline(
        self,
        video_id: str,
        notebook_id: str,
        topic: Optional[str],
        duration_minutes: int,
        visual_style: str,
        voice: str,
        format_type: str,
        chat_context: Optional[str] = None,
    ):
        """Full background pipeline: storyboard → TTS → slides → composite."""
        pipeline_start = time.time()

        try:
            # ── Pre-flight: Check audio model availability ──
            from services.audio_llm import audio_llm
            if not audio_llm.is_available:
                await audio_llm.initialize()
            if not audio_llm.is_available:
                err = audio_llm._init_error or "unknown error"
                await video_store.update(video_id, {
                    "status": "failed",
                    "error_message": (
                        "Kokoro TTS not available — required for video narration. "
                        "Check Health Portal for details. "
                        f"Detail: {str(err)[:200]}"
                    ),
                })
                return

            # ── Stage 1: Generate Storyboard ──
            await video_store.update(video_id, {
                "status": "processing",
                "error_message": "Generating storyboard..."
            })

            from services.video_storyboard import video_storyboard
            storyboard = await video_storyboard.generate(
                notebook_id=notebook_id,
                topic=topic,
                duration_minutes=duration_minutes,
                format_type=format_type,
                chat_context=chat_context,
            )

            scene_count = len(storyboard.scenes)
            await video_store.update(video_id, {
                "storyboard": storyboard.to_json(),
                "slide_count": scene_count,
                "error_message": f"Storyboard ready: {scene_count} scenes. Generating narration..."
            })
            print(f"📋 Storyboard: {scene_count} scenes for {video_id}")

            # ── Stage 2: Generate Narration Audio ──
            narration_text = self._build_narration_script(storyboard)

            await video_store.update(video_id, {
                "narration_script": narration_text,
                "error_message": f"Generating narration audio ({len(narration_text.split())} words)..."
            })

            audio_path = await self._generate_narration_audio(
                video_id=video_id,
                narration_text=narration_text,
                voice=voice,
            )

            # ── Duration sanity check ──
            from services.video_compositor import video_compositor
            audio_duration = video_compositor.get_audio_duration(audio_path)
            target_seconds = duration_minutes * 60
            word_count = len(narration_text.split())
            logger.info(
                f"[VideoGen] Narration: {word_count} words, "
                f"audio={audio_duration:.0f}s, target={target_seconds}s"
            )
            if audio_duration > 0 and audio_duration < target_seconds * 0.40:
                logger.warning(
                    f"[VideoGen] ⚠️ Audio duration ({audio_duration:.0f}s) is only "
                    f"{audio_duration/target_seconds*100:.0f}% of target ({target_seconds}s). "
                    f"Narration script may be too short ({word_count} words for {duration_minutes}min)."
                )

            print(f"🎤 Narration audio: {audio_path} ({audio_duration:.0f}s)")

            # ── Stage 3: Render Slides ──
            await video_store.update(video_id, {
                "error_message": f"Rendering {scene_count} slides ({visual_style} style)..."
            })

            from services.video_slide_renderer import video_slide_renderer
            slides_dir = self.video_dir / f"{video_id}_slides"
            slide_paths = await video_slide_renderer.render_slides(
                scenes=storyboard.scenes,
                style_name=visual_style,
                output_dir=slides_dir,
            )
            print(f"🖼️  Slides rendered: {len(slide_paths)} PNGs")

            # ── Stage 4: Composite Video ──
            await video_store.update(video_id, {
                "error_message": "Compositing final video..."
            })

            from services.video_compositor import video_compositor
            output_path = self.video_dir / f"{video_id}.mp4"

            await video_compositor.compose(
                slide_paths=slide_paths,
                audio_path=audio_path,
                scenes=storyboard.scenes,
                output_path=output_path,
            )

            # Get final duration
            duration_seconds = int(video_compositor.get_audio_duration(output_path))

            elapsed = int(time.time() - pipeline_start)
            await video_store.update(video_id, {
                "status": "completed",
                "video_file_path": str(output_path),
                "duration_seconds": duration_seconds,
                "error_message": None,
            })
            print(f"✅ Video complete: {video_id} → {output_path} "
                  f"({duration_seconds}s, pipeline took {elapsed}s)")

        except Exception as e:
            logger.error(f"[VideoGen] Pipeline failed for {video_id}: {e}")
            traceback.print_exc()
            await video_store.update(video_id, {
                "status": "failed",
                "error_message": str(e)[:500]
            })

        finally:
            # Clean up temp directories
            for suffix in ["_slides", "_parts"]:
                temp = self.video_dir / f"{video_id}{suffix}"
                if temp.exists():
                    shutil.rmtree(temp, ignore_errors=True)

    def _build_narration_script(self, storyboard) -> str:
        """Combine all scene narrations into a single TTS script.

        Each scene's narration becomes a paragraph, separated by brief pauses
        (double newline = natural pause in TTS).
        """
        parts = []
        for scene in storyboard.scenes:
            narration = scene.narration.strip()
            if narration:
                parts.append(narration)

        return "\n\n".join(parts)

    async def _generate_narration_audio(
        self,
        video_id: str,
        narration_text: str,
        voice: str,
    ) -> Path:
        """Generate TTS audio for the narration script.

        Uses Kokoro-82M TTS service — same as podcast generation.
        Processes chunks individually with per-chunk timeouts and progress updates
        to prevent indefinite stalls.
        """
        import gc
        import wave
        import array as _array

        from services.audio_llm import audio_llm

        # Initialize audio model if needed
        await audio_llm.initialize()

        if not audio_llm.is_available:
            detail = audio_llm._init_error or "unknown error"
            raise RuntimeError(f"Kokoro TTS not available: {detail}")

        # Split narration into TTS-friendly chunks (same method the audio model uses)
        chunks = audio_llm._chunk_text_for_tts(narration_text, max_chunk_chars=350)
        total_chunks = len(chunks)
        logger.info(f"[VideoGen] Narration TTS: {total_chunks} chunks from {len(narration_text)} chars")

        output_path = self.video_dir / f"{video_id}_narration.wav"
        temp_dir = self.video_dir / f"{video_id}_parts"
        temp_dir.mkdir(parents=True, exist_ok=True)

        part_paths: list[Path] = []
        last_error = None
        chunks_failed = 0
        chunks_attempted = 0
        gen_start = time.time()

        for idx, chunk_text in enumerate(chunks):
            if not chunk_text.strip():
                continue

            chunks_attempted += 1
            part_path = temp_dir / f"part_{idx:04d}.wav"

            # Progress update with ETA
            elapsed = time.time() - gen_start
            if idx > 0 and elapsed > 0:
                avg = elapsed / idx
                remaining = avg * (total_chunks - idx)
                eta_min, eta_sec = int(remaining // 60), int(remaining % 60)
                eta_str = f" — ~{eta_min}m {eta_sec}s left" if eta_min > 0 else f" — ~{eta_sec}s left"
            else:
                eta_str = ""

            await video_store.update(video_id, {
                "error_message": f"Generating narration audio: chunk {idx + 1}/{total_chunks}{eta_str}"
            })

            # Per-chunk timeout: ~60s base + scale with text length
            chunk_timeout = max(120, int(len(chunk_text) / 500 * 60))

            try:
                await asyncio.wait_for(
                    audio_llm.text_to_speech(
                        text=chunk_text,
                        voice=voice,
                        output_path=str(part_path),
                    ),
                    timeout=chunk_timeout,
                )
                if part_path.exists() and part_path.stat().st_size > 500:
                    part_paths.append(part_path)
                    logger.info(f"[VideoGen] ✓ Chunk {idx + 1}/{total_chunks}: {len(chunk_text)} chars → {part_path.name}")
                else:
                    logger.warning(f"[VideoGen] Chunk {idx + 1} produced no usable file, skipping")
                    chunks_failed += 1
            except asyncio.TimeoutError:
                last_error = f"Chunk {idx + 1} timed out after {chunk_timeout}s"
                logger.warning(f"[VideoGen] ⚠ {last_error}, skipping")
                chunks_failed += 1
            except Exception as seg_err:
                last_error = f"Chunk {idx + 1}: {seg_err}"
                logger.warning(f"[VideoGen] ⚠ Chunk {idx + 1} failed: {seg_err}, skipping")
                chunks_failed += 1

            # Early abort: if >70% of attempted chunks have failed, stop
            if chunks_attempted >= 4 and chunks_failed / chunks_attempted > 0.7:
                logger.error(f"[VideoGen] ❌ ABORTING: {chunks_failed}/{chunks_attempted} chunks failed (>70%). TTS engine may be broken.")
                break

            # Resource cleanup between chunks — prevent thermal throttling
            gc.collect()
            try:
                import mlx.core as mx
                mx.clear_cache()
            except Exception:
                pass
            await asyncio.sleep(0.3)

        if chunks_failed > 0:
            logger.warning(f"[VideoGen] 📊 TTS summary: {len(part_paths)} succeeded, {chunks_failed} failed out of {total_chunks} total")

        if not part_paths:
            detail = f" Last error: {last_error}" if last_error else ""
            raise RuntimeError(f"No narration audio chunks generated successfully ({chunks_failed}/{total_chunks} failed).{detail}")

        # Concatenate WAV parts into final narration file
        await video_store.update(video_id, {
            "error_message": f"Assembling narration ({len(part_paths)} segments)..."
        })
        self._concatenate_wav_parts(part_paths, output_path)
        logger.info(f"[VideoGen] Assembled {len(part_paths)} narration segments → {output_path}")

        # Clean up temp parts
        shutil.rmtree(temp_dir, ignore_errors=True)

        if not output_path.exists() or output_path.stat().st_size < 1000:
            raise RuntimeError("Narration audio generation failed — file too small or missing")

        return output_path

    @staticmethod
    def _concatenate_wav_parts(part_paths: list[Path], output_path: Path):
        """Concatenate WAV files with crossfading for seamless narration."""
        import wave
        import array as _array

        if not part_paths:
            return
        if len(part_paths) == 1:
            shutil.copy2(part_paths[0], output_path)
            return

        segments = []
        params = None
        for p in part_paths:
            try:
                with wave.open(str(p), 'rb') as wf:
                    if params is None:
                        params = wf.getparams()
                    raw = wf.readframes(wf.getnframes())
                    if raw and params.sampwidth == 2:
                        samples = _array.array('h', raw)
                        # Per-segment peak normalization to -3 dB
                        peak = max(abs(s) for s in samples) if samples else 0
                        if peak > 0:
                            target = 32767 * 0.708
                            gain = target / peak
                            if gain < 0.8 or gain > 1.3:
                                for i in range(len(samples)):
                                    samples[i] = max(-32767, min(32767, int(samples[i] * gain)))
                        segments.append(samples)
            except Exception as e:
                logger.warning(f"[VideoGen] Couldn't read {p.name}: {e}")

        if not segments or params is None:
            return

        # Crossfade (30ms) + natural pause (100ms) between segments
        crossfade_len = int(params.framerate * 0.03)
        pause_len = int(params.framerate * 0.10)
        pause_samples = _array.array('h', [0] * pause_len)

        merged = segments[0]
        for j in range(1, len(segments)):
            nxt = segments[j]
            merged.extend(pause_samples)
            if len(merged) > crossfade_len and len(nxt) > crossfade_len:
                for k in range(crossfade_len):
                    fade_out = 1.0 - (k / crossfade_len)
                    fade_in = k / crossfade_len
                    blended = int(merged[-(crossfade_len - k)] * fade_out + nxt[k] * fade_in)
                    merged[-(crossfade_len - k)] = max(-32767, min(32767, blended))
                merged.extend(nxt[crossfade_len:])
            else:
                merged.extend(nxt)

        with wave.open(str(output_path), 'wb') as wf:
            wf.setparams(params)
            wf.writeframes(merged.tobytes())

    async def list(self, notebook_id: str):
        """List video generations for a notebook."""
        return await video_store.list(notebook_id)

    async def get(self, video_id: str):
        """Get a video generation by ID."""
        return await video_store.get(video_id)

    async def delete(self, video_id: str) -> bool:
        """Delete a video generation and its files."""
        gen = await video_store.get(video_id)
        if gen:
            # Delete video file
            video_path = gen.get("video_file_path")
            if video_path and Path(video_path).exists():
                Path(video_path).unlink(missing_ok=True)
            # Delete narration audio
            narration_path = self.video_dir / f"{video_id}_narration.wav"
            narration_path.unlink(missing_ok=True)

        return await video_store.delete(video_id)


# Singleton
video_generator = VideoGenerator()
