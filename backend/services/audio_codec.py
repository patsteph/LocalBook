"""The one audio codec helper (LB-3).

PyAV — ffmpeg's libraries inside a Python wheel, in-process — so speech works on
a Mac with no Homebrew (the MDM machine) and there is no executable to find on
PATH or to sign. Used only at the edges: DECODE incoming audio for speech-to-text
and ENCODE mp3/opus/wav for `/v1/audio/speech`. Kokoro's own generation and WAV
writing (`audio_llm._save_wav`), podcasts, jingles and video generation are
deliberately untouched.

`av` is imported lazily: it is only needed when audio actually moves.
"""

from __future__ import annotations

import io
import logging
from pathlib import Path
from typing import Iterator, Optional, Union

import numpy as np

logger = logging.getLogger(__name__)

Source = Union[bytes, str, Path]

# response_format → (container, codec, sample format the encoder takes)
_FORMATS = {
    "mp3": ("mp3", "libmp3lame", "fltp"),
    "opus": ("ogg", "libopus", "flt"),
    "wav": ("wav", "pcm_s16le", "s16"),
}
FORMATS = tuple(_FORMATS) + ("pcm",)
MEDIA_TYPES = {"mp3": "audio/mpeg", "opus": "audio/ogg", "wav": "audio/wav",
               "pcm": "audio/L16"}
# libopus only takes these rates; Kokoro's 24 kHz is one of them.
_OPUS_RATES = (8000, 12000, 16000, 24000, 48000)


class CodecError(RuntimeError):
    """Audio could not be decoded or encoded."""


def codec_ok() -> bool:
    """Can this install decode and encode speech? Cheap: no audio is touched."""
    try:
        import av

        for name in ("libmp3lame", "libopus"):
            av.codec.Codec(name, "w")
        for name in ("mp3", "opus", "aac"):
            av.codec.Codec(name, "r")
        return True
    except Exception as exc:
        logger.debug("[codec] unavailable: %s", exc)
        return False


def decode_pcm(source: Source, sample_rate: int = 16000) -> np.ndarray:
    """Any audio (or a video's audio track) → float32 mono at `sample_rate`.

    wav, mp3, m4a/aac, ogg/opus, flac, and the audio of mp4/mov/webm.
    """
    import av

    handle = io.BytesIO(source) if isinstance(source, (bytes, bytearray)) else str(source)
    try:
        container = av.open(handle, mode="r")
    except Exception as exc:
        raise CodecError(f"not a readable audio file: {exc}") from exc
    try:
        stream = next((s for s in container.streams if s.type == "audio"), None)
        if stream is None:
            raise CodecError("the file has no audio track")
        resampler = av.AudioResampler(format="flt", layout="mono", rate=sample_rate)
        parts = []
        for frame in container.decode(stream):
            for out in resampler.resample(frame):
                parts.append(out.to_ndarray().reshape(-1))
        for out in resampler.resample(None):          # flush
            parts.append(out.to_ndarray().reshape(-1))
    except CodecError:
        raise
    except Exception as exc:
        raise CodecError(f"could not decode audio: {exc}") from exc
    finally:
        container.close()
    if not parts:
        return np.zeros(0, dtype=np.float32)
    return np.concatenate(parts).astype(np.float32, copy=False)


def _pcm16(pcm: np.ndarray) -> bytes:
    return (np.clip(pcm, -1.0, 1.0) * 32767.0).astype("<i2").tobytes()


class StreamEncoder:
    """Encode float32 mono chunks into one container, yielding bytes as they are
    ready — for `stream: true`, where the first sentence should play while the
    rest is still being synthesised.

        enc = StreamEncoder("mp3", 24000)
        for chunk in kokoro_chunks: yield enc.feed(chunk)
        yield enc.close()
    """

    def __init__(self, fmt: str, sample_rate: int):
        if fmt not in FORMATS:
            raise CodecError(f"unsupported format {fmt!r}; use one of {', '.join(FORMATS)}")
        self.fmt = fmt
        self.sample_rate = sample_rate
        self._buf = io.BytesIO()
        self._sent = 0
        self._pts = 0
        self._container = None
        self._stream = None
        if fmt == "pcm":
            return
        import av

        container_fmt, codec, self._sample_fmt = _FORMATS[fmt]
        rate = sample_rate
        if fmt == "opus" and rate not in _OPUS_RATES:
            raise CodecError(f"opus cannot encode {rate} Hz")
        self._container = av.open(self._buf, mode="w", format=container_fmt)
        self._stream = self._container.add_stream(codec, rate=rate, layout="mono")
        if fmt == "mp3":
            self._stream.bit_rate = 128_000
        elif fmt == "opus":
            self._stream.bit_rate = 48_000

    def _drain(self) -> bytes:
        data = self._buf.getvalue()[self._sent:]
        self._sent += len(data)
        return data

    def feed(self, pcm: np.ndarray) -> bytes:
        if pcm is None or len(pcm) == 0:
            return b""          # libav rejects an empty frame (EINVAL)
        if self.fmt == "pcm":
            return _pcm16(pcm)
        import av

        samples = np.asarray(pcm, dtype=np.float32).reshape(1, -1)
        if self._sample_fmt == "s16":
            frame = av.AudioFrame.from_ndarray(
                (np.clip(samples, -1.0, 1.0) * 32767.0).astype(np.int16), format="s16", layout="mono")
        else:
            frame = av.AudioFrame.from_ndarray(samples, format=self._sample_fmt, layout="mono")
        frame.sample_rate = self.sample_rate
        frame.pts = self._pts
        self._pts += samples.shape[1]
        for packet in self._stream.encode(frame):
            self._container.mux(packet)
        return self._drain()

    def close(self) -> bytes:
        if self.fmt == "pcm" or self._container is None:
            return b""
        for packet in self._stream.encode(None):
            self._container.mux(packet)
        self._container.close()
        self._container = None
        return self._drain()


def encode(pcm: np.ndarray, sample_rate: int, fmt: str) -> bytes:
    """Whole-clip encode: float32 mono → `fmt` bytes.

    Returns the WHOLE buffer, not the drained pieces: the wav and mp3 muxers go
    back and patch their headers (sizes, Xing/LAME tag) on close, and a streamed
    copy has already sent the unpatched ones. Players tolerate that in a stream;
    a file should be exact.
    """
    enc = StreamEncoder(fmt, sample_rate)
    data = enc.feed(pcm)
    tail = enc.close()
    return enc._buf.getvalue() if fmt != "pcm" else data + tail


def iter_encode(chunks: Iterator[np.ndarray], sample_rate: int, fmt: str) -> Iterator[bytes]:
    enc = StreamEncoder(fmt, sample_rate)
    for chunk in chunks:
        data = enc.feed(chunk)
        if data:
            yield data
    tail = enc.close()
    if tail:
        yield tail


def duration_seconds(pcm: Optional[np.ndarray], sample_rate: int) -> float:
    return 0.0 if pcm is None else round(len(pcm) / float(sample_rate), 3)
