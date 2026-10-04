"""Video Compositor — slide PNGs + narration audio → MP4, in-process (PyAV).

Handles:
- Ken Burns effects (zoom/pan) on still slides — ffmpeg's own zoompan filter,
  run through PyAV's filter graph, so the motion is unchanged
- Audio/slide timing synchronization
- H.264 MP4 with AAC audio, faststart

This used to shell out to an `ffmpeg` binary from Homebrew, which a Finder-
launched app's PATH lacks and the MDM Mac does not have at all. PyAV ships with
the app and carries libx264, aac and zoompan.
"""

import asyncio
import logging
from fractions import Fraction
from pathlib import Path
from typing import List

logger = logging.getLogger(__name__)


# =============================================================================
# KEN BURNS FILTER EXPRESSIONS
# =============================================================================

# Each effect is an FFmpeg zoompan filter expression.
# Variables: d=total frames for this clip, s=output size
# The image is rendered at 1920x1080 but we render slightly larger (2200x1237)
# and pan/zoom within that for Ken Burns headroom.
# Actually — we render at 1920x1080 and use zoompan's built-in zoom on the PNG.

KEN_BURNS_FILTERS = {
    # --- Zoom effects (center-focused — viewport always centered, no edge clipping) ---
    # Standard zoom: 1.0 → 1.08 over ~5s then holds
    "zoom_in": (
        "zoompan=z='min(1.0+0.0005*on,1.08)'"
        ":x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
        ":d={frames}:s=1920x1080:fps=30"
    ),
    "zoom_out": (
        "zoompan=z='if(eq(on,0),1.08,max(1.0,zoom-0.0005))'"
        ":x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
        ":d={frames}:s=1920x1080:fps=30"
    ),
    # Slow zoom: 1.0 → 1.04 — cinematic, barely perceptible (great for quotes, titles)
    "zoom_in_slow": (
        "zoompan=z='min(1.0+0.0002*on,1.04)'"
        ":x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
        ":d={frames}:s=1920x1080:fps=30"
    ),
    "zoom_out_slow": (
        "zoompan=z='if(eq(on,0),1.04,max(1.0,zoom-0.0002))'"
        ":x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
        ":d={frames}:s=1920x1080:fps=30"
    ),
    # --- Pan effects (center-biased — stays within safe content margins) ---
    # zoom=1.06, total pan range=109px. Travel restricted to 50px centered (x: 30→80)
    # so content at slide edges is never pushed off-screen.
    "pan_right": (
        "zoompan=z='1.06'"
        ":x='30+min(on*50.0/{frames},50)'"
        ":y='ih/2-(ih/zoom/2)'"
        ":d={frames}:s=1920x1080:fps=30"
    ),
    "pan_left": (
        "zoompan=z='1.06'"
        ":x='max(80-on*50.0/{frames},30)'"
        ":y='ih/2-(ih/zoom/2)'"
        ":d={frames}:s=1920x1080:fps=30"
    ),
    # --- Drift effects (very subtle lateral motion, safe margins) ---
    # zoom=1.03, total range=56px. Travel restricted to 28px centered (x: 14→42)
    "drift_right": (
        "zoompan=z='1.03'"
        ":x='14+min(on*28.0/{frames},28)'"
        ":y='ih/2-(ih/zoom/2)'"
        ":d={frames}:s=1920x1080:fps=30"
    ),
    "drift_left": (
        "zoompan=z='1.03'"
        ":x='max(42-on*28.0/{frames},14)'"
        ":y='ih/2-(ih/zoom/2)'"
        ":d={frames}:s=1920x1080:fps=30"
    ),
    # --- Static — no motion ---
    "none": (
        "zoompan=z='1.0'"
        ":x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
        ":d={frames}:s=1920x1080:fps=30"
    ),
}


# =============================================================================
# COMPOSITOR
# =============================================================================

FPS = 30
WIDTH, HEIGHT = 1920, 1080
AUDIO_RATE = 48000


class VideoCompositor:
    """Composites slide PNGs and narration audio into a final MP4 video."""

    def get_audio_duration(self, audio_path: Path) -> float:
        """Audio (or video) file duration in seconds; 0.0 when unreadable."""
        try:
            import av

            with av.open(str(audio_path)) as container:
                if container.duration:
                    return container.duration / 1_000_000
                stream = next((s for s in container.streams if s.type == "audio"), None)
                if stream is not None and stream.duration and stream.time_base:
                    return float(stream.duration * stream.time_base)
        except Exception as e:
            logger.warning(f"[Compositor] duration probe failed: {e}")
        return 0.0

    def calculate_scene_durations(
        self,
        scenes: list,
        total_audio_duration: float,
    ) -> List[float]:
        """Calculate per-scene durations proportional to narration word count.

        Returns list of durations in seconds, one per scene.
        Enforces a minimum of 4 seconds per slide to prevent rapid flashing.
        """
        word_counts = []
        for scene in scenes:
            narration = scene.narration if hasattr(scene, 'narration') else scene.get("narration", "")
            word_counts.append(max(1, len(narration.split())))

        total_words = sum(word_counts)
        avg_per_slide = total_audio_duration / max(len(scenes), 1)

        if avg_per_slide < 5.0:
            logger.warning(
                f"[Compositor] Very short average slide duration: {avg_per_slide:.1f}s "
                f"({total_audio_duration:.0f}s audio / {len(scenes)} slides). "
                f"Consider fewer scenes or longer narration."
            )

        # Distribute total audio duration proportionally
        durations = []
        for wc in word_counts:
            proportion = wc / total_words
            dur = proportion * total_audio_duration
            # Minimum 4 seconds per slide (prevents flash), max 45
            dur = max(4.0, min(45.0, dur))
            durations.append(dur)

        # Scale to match total duration exactly
        scale = total_audio_duration / max(sum(durations), 0.1)
        durations = [d * scale for d in durations]

        return durations

    async def compose(
        self,
        slide_paths: List[Path],
        audio_path: Path,
        scenes: list,
        output_path: Path,
        fade_duration: float = 0.5,
    ) -> Path:
        """Compose slides + audio into final MP4.

        One pass: each slide is run through its Ken Burns zoompan and encoded
        straight into the output (cut transitions — the motion already gives
        visual flow), with the narration encoded alongside it.

        Args:
            slide_paths: Ordered list of PNG paths (one per scene)
            audio_path: Path to narration WAV/MP3
            scenes: Scene objects for Ken Burns and timing info
            output_path: Where to write the final MP4
            fade_duration: Unused (kept for callers); transitions are cuts

        Returns:
            Path to the final MP4 file
        """
        if len(slide_paths) != len(scenes):
            raise ValueError(f"Mismatch: {len(slide_paths)} slides vs {len(scenes)} scenes")

        output_path.parent.mkdir(parents=True, exist_ok=True)

        total_duration = self.get_audio_duration(audio_path)
        if total_duration <= 0:
            raise RuntimeError(f"Could not determine audio duration for {audio_path}")

        durations = self.calculate_scene_durations(scenes, total_duration)
        effects = []
        for scene in scenes:
            visual = scene.visual if hasattr(scene, 'visual') else scene.get("visual", {})
            effects.append(visual.ken_burns if hasattr(visual, 'ken_burns') else visual.get("ken_burns", "zoom_in"))
        logger.info(f"[Compositor] {len(scenes)} slides, total {total_duration:.1f}s audio")

        tmp = output_path.with_name(output_path.stem + ".part.mp4")
        try:
            await asyncio.to_thread(self._render, slide_paths, durations, effects,
                                    audio_path, total_duration, tmp)
            tmp.replace(output_path)
        finally:
            tmp.unlink(missing_ok=True)

        logger.info(f"[Compositor] Final video: {output_path} ({total_duration:.1f}s)")
        return output_path

    def _render(self, slide_paths: List[Path], durations: List[float], effects: List[str],
                audio_path: Path, total_duration: float, out_path: Path) -> None:
        import av
        import numpy as np

        from services.audio_codec import decode_pcm

        pcm = decode_pcm(audio_path, AUDIO_RATE)[: int(total_duration * AUDIO_RATE)]
        pcm = np.ascontiguousarray(pcm, dtype=np.float32)

        # Frame counts from cumulative time, so rounding never drifts from the audio.
        bounds = [0]
        acc = 0.0
        for d in durations:
            acc += d
            bounds.append(max(bounds[-1] + FPS, round(acc * FPS)))   # at least 1 s per slide

        with av.open(str(out_path), mode="w", options={"movflags": "+faststart"}) as out:
            video = self._video_stream(out)
            audio = out.add_stream("aac", rate=AUDIO_RATE, layout="mono")
            audio.bit_rate = 192_000

            pts = 0
            sent = 0                                   # audio samples encoded so far
            for i, (slide, kb) in enumerate(zip(slide_paths, effects)):
                frames = bounds[i + 1] - bounds[i]
                for frame in self._ken_burns(slide, kb, frames):
                    frame.pts = pts
                    pts += 1
                    out.mux(video.encode(frame))
                # Narration up to where the video now is.
                upto = min(len(pcm), round(pts * AUDIO_RATE / FPS))
                if upto > sent:
                    sent = self._encode_audio(out, audio, pcm[sent:upto], sent)
                if (i + 1) % 5 == 0 or i == len(slide_paths) - 1:
                    logger.info(f"[Compositor] Rendered clip {i+1}/{len(slide_paths)}")

            if sent < len(pcm):
                self._encode_audio(out, audio, pcm[sent:], sent)
            out.mux(video.encode(None))
            out.mux(audio.encode(None))

    @staticmethod
    def _video_stream(out):
        import av

        try:
            av.codec.Codec("libx264", "w")
            stream = out.add_stream("libx264", rate=FPS, options={"preset": "fast", "crf": "23"})
        except Exception:                              # a PyAV built without x264
            stream = out.add_stream("h264_videotoolbox", rate=FPS)
            stream.bit_rate = 6_000_000
        stream.width, stream.height, stream.pix_fmt = WIDTH, HEIGHT, "yuv420p"
        stream.time_base = Fraction(1, FPS)
        return stream

    @staticmethod
    def _encode_audio(out, stream, samples, start: int) -> int:
        import av

        frame = av.AudioFrame.from_ndarray(samples.reshape(1, -1), format="flt", layout="mono")
        frame.sample_rate = AUDIO_RATE
        frame.pts = start
        frame.time_base = Fraction(1, AUDIO_RATE)
        out.mux(stream.encode(frame))
        return start + len(samples)

    @staticmethod
    def _ken_burns(slide_path: Path, ken_burns: str, frames: int):
        """The slide's frames, `frames` long, through the zoompan filter."""
        import av
        from PIL import Image

        template = KEN_BURNS_FILTERS.get(ken_burns, KEN_BURNS_FILTERS["zoom_in"])
        name, args = template.format(frames=frames).split("=", 1)
        with Image.open(slide_path) as im:
            image = im.convert("RGB")
        if image.size != (WIDTH, HEIGHT):
            image = image.resize((WIDTH, HEIGHT), Image.LANCZOS)

        graph = av.filter.Graph()
        src = graph.add_buffer(width=WIDTH, height=HEIGHT, format="rgb24", time_base=Fraction(1, FPS))
        chain = [src, graph.add(name, args), graph.add("format", "yuv420p"), graph.add("buffersink")]
        for a, b in zip(chain, chain[1:]):
            a.link_to(b)
        graph.configure()
        graph.push(av.VideoFrame.from_image(image))
        made, frame = 0, None
        while made < frames:
            try:
                frame = graph.pull()
            except (av.BlockingIOError, av.EOFError):
                break
            made += 1
            yield frame
        if frame is None:
            raise RuntimeError(f"Ken Burns produced no frames for {slide_path.name}")
        # zoompan emits `d` frames per input; pad if it ever comes up short.
        while made < frames:
            yield frame
            made += 1


# Singleton
video_compositor = VideoCompositor()
