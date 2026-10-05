"""The video compositor renders in-process (PyAV) — no ffmpeg binary needed."""

import asyncio
import wave

import numpy as np
from PIL import Image

from services.video_compositor import VideoCompositor


def _narration(path, seconds, rate=24000):
    t = np.arange(int(rate * seconds)) / rate
    pcm = (0.2 * np.sin(2 * np.pi * 220 * t) * 32767).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm.tobytes())


def test_slides_and_narration_become_an_mp4(tmp_path, monkeypatch):
    import av
    import shutil

    monkeypatch.setattr(shutil, "which", lambda name: None)      # no ffmpeg anywhere
    slides = []
    for i, colour in enumerate(("navy", "maroon")):
        p = tmp_path / f"s{i}.png"
        Image.new("RGB", (1280, 720), colour).save(p)             # not 1080p: resized
        slides.append(p)
    _narration(tmp_path / "n.wav", 9.0)
    scenes = [{"narration": "one two three", "visual": {"ken_burns": "zoom_in"}},
              {"narration": "four five six", "visual": {"ken_burns": "not-an-effect"}}]

    vc = VideoCompositor()
    out = asyncio.run(vc.compose(slides, tmp_path / "n.wav", scenes, tmp_path / "v.mp4"))

    with av.open(str(out)) as c:
        kinds = {s.type: s for s in c.streams}
        assert kinds["video"].codec_context.name == "h264"
        assert kinds["video"].codec_context.width == 1920
        assert kinds["audio"].codec_context.name == "aac"
        assert kinds["video"].frames == 270                        # 9 s at 30 fps, no drift
    assert abs(vc.get_audio_duration(out) - 9.0) < 0.1
    assert not list(tmp_path.glob("*.part.mp4"))


def test_a_ken_burns_zoom_moves_and_static_does_not(tmp_path):
    p = tmp_path / "grid.png"
    img = np.full((1080, 1920, 3), 255, np.uint8)
    img[:, ::100] = 0
    Image.fromarray(img).save(p)

    def drift(effect):
        frames = [f.to_ndarray(format="gray").astype(int)
                  for f in VideoCompositor._ken_burns(p, effect, 60)]
        assert len(frames) == 60
        return np.abs(frames[0] - frames[-1]).mean()

    assert drift("zoom_in") > 1
    assert drift("none") == 0
