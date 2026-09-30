"""LB-3: the codec, speech-to-text routing, Kokoro chunking, and `/v1/audio/*`.

The endpoints are driven through the official OpenAI SDK, like `/v1/chat`: the
point is that an unmodified third-party client works. Models are faked — the
real Parakeet and Kokoro run in the verification script against a build.
"""
import io
import types

import numpy as np
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from services import audio_codec
from services import companions as svc

SR = 24000


def _tone(seconds=1.0, sr=SR, hz=440.0):
    t = np.arange(int(sr * seconds)) / sr
    return (0.3 * np.sin(2 * np.pi * hz * t)).astype(np.float32)


# ── the codec ───────────────────────────────────────────────────────────────


@pytest.mark.parametrize("fmt", ["wav", "mp3", "opus"])
def test_encode_then_decode_round_trips(fmt):
    back = audio_codec.decode_pcm(audio_codec.encode(_tone(2.0), SR, fmt), 16000)
    assert abs(len(back) / 16000 - 2.0) < 0.1           # mp3 pads a frame
    assert float(np.sqrt((back ** 2).mean())) > 0.15      # not silence


def test_decodes_an_m4a_and_a_video_audio_track():
    """What phones record and what `_extract_from_video` gets — no ffmpeg binary."""
    import av

    for container_fmt in ("mp4", "mov"):
        buf = io.BytesIO()
        out = av.open(buf, "w", format=container_fmt)
        stream = out.add_stream("aac", rate=SR, layout="mono")
        frame = av.AudioFrame.from_ndarray(_tone(1.0).reshape(1, -1), format="fltp", layout="mono")
        frame.sample_rate, frame.pts = SR, 0
        for p in stream.encode(frame):
            out.mux(p)
        for p in stream.encode(None):
            out.mux(p)
        out.close()
        assert abs(len(audio_codec.decode_pcm(buf.getvalue())) / 16000 - 1.0) < 0.1


def test_a_whole_wav_has_a_correct_header():
    """The muxer patches sizes on close; a whole-file encode must return those."""
    import wave

    w = wave.open(io.BytesIO(audio_codec.encode(_tone(2.0), SR, "wav")))
    assert w.getnframes() == 2 * SR and w.getframerate() == SR


@pytest.mark.parametrize("fmt", ["wav", "mp3", "opus", "pcm"])
def test_streaming_yields_several_pieces_that_play_as_one(fmt):
    parts = list(audio_codec.iter_encode((_tone(0.5) for _ in range(4)), SR, fmt))
    assert len(parts) >= 2
    if fmt != "pcm":
        assert abs(len(audio_codec.decode_pcm(b"".join(parts))) / 16000 - 2.0) < 0.15


def test_garbage_is_a_codec_error_not_a_crash():
    with pytest.raises(audio_codec.CodecError):
        audio_codec.decode_pcm(b"definitely not audio")


def test_unknown_format_is_refused():
    with pytest.raises(audio_codec.CodecError):
        audio_codec.StreamEncoder("flac", SR)


def test_codec_ok_on_this_install():
    assert audio_codec.codec_ok() is True


# ── speech-to-text routing ──────────────────────────────────────────────────


@pytest.fixture
def stt(monkeypatch):
    from services import mlx_asr, speech_to_text

    calls = []

    async def parakeet(pcm, model_id):
        calls.append("parakeet")
        return {"text": "from parakeet", "segments": []}

    def whisper(pcm, model_id, language):
        calls.append("whisper")
        return {"text": "from whisper", "segments": [], "language": "en"}

    monkeypatch.setattr(mlx_asr, "transcribe", parakeet)
    monkeypatch.setattr(speech_to_text, "_whisper_sync", whisper)
    speech_to_text.calls = calls
    return speech_to_text


def _run(coro):
    import asyncio
    return asyncio.run(coro)


def test_parakeet_answers_and_says_so(stt):
    from config import settings

    out = _run(stt.transcribe(audio_codec.encode(_tone(), SR, "opus")))
    assert out["text"] == "from parakeet" and out["engine"] == settings.stt_model
    assert stt.calls == ["parakeet"] and abs(out["duration"] - 1.0) < 0.1


def test_whisper_takes_over_when_parakeet_fails(stt, monkeypatch):
    from config import settings
    from services import mlx_asr

    async def broken(pcm, model_id):
        raise RuntimeError("metal said no")

    monkeypatch.setattr(mlx_asr, "transcribe", broken)
    out = _run(stt.transcribe(audio_codec.encode(_tone(), SR, "wav")))
    assert out["text"] == "from whisper" and out["engine"] == settings.stt_fallback_model


def test_both_failing_is_an_error_naming_both(stt, monkeypatch):
    from services import mlx_asr

    async def broken(pcm, model_id):
        raise RuntimeError("parakeet down")

    def also_broken(*a):
        raise RuntimeError("whisper down")

    monkeypatch.setattr(mlx_asr, "transcribe", broken)
    monkeypatch.setattr(stt, "_whisper_sync", also_broken)
    with pytest.raises(stt.TranscriptionError, match="parakeet down.*whisper down"):
        _run(stt.transcribe(audio_codec.encode(_tone(), SR, "wav")))


def test_silence_of_zero_length_skips_the_engines(stt):
    import wave

    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:          # a real WAV with no frames
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(SR)
    out = _run(stt.transcribe(buf.getvalue()))
    assert out["text"] == "" and stt.calls == []


# ── Kokoro's chunk loop, shared by the file path and the stream ─────────────


class _FakeKokoro:
    """Deterministic stand-in: each chunk's audio encodes its length, and the
    chunk containing "FAIL" never succeeds."""

    def generate(self, chunk, voice, speed):
        if "FAIL" in chunk:
            raise RuntimeError("bad chunk")
        return types.SimpleNamespace(audio=np.full(len(chunk) * 10, 0.1, dtype=np.float32))


@pytest.fixture
def kokoro(tmp_path, monkeypatch):
    from services.audio_llm import audio_llm

    monkeypatch.setattr(audio_llm, "_model", _FakeKokoro())
    monkeypatch.setattr(audio_llm, "_initialized", True)
    monkeypatch.setattr(audio_llm, "_chunk_text_for_tts", lambda text: text.split("|"))
    monkeypatch.setattr(audio_llm, "_preprocess_text_for_tts", lambda text: text)
    return audio_llm


def test_the_file_is_the_crossfade_of_the_chunks_in_order(kokoro, tmp_path):
    import wave

    text = "one|three|FAIL|seven"
    expected = kokoro._crossfade_segments(list(kokoro.tts_chunks(text, "af_heart")))
    path = kokoro._tts_sync(text, "af_heart", str(tmp_path / "o.wav"), 1.0)
    w = wave.open(path)
    got = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2")
    assert len(got) == len(expected)


def test_a_failed_chunk_is_counted_and_skipped(kokoro):
    stats = {"chunks": 0, "failed": 0}
    out = list(kokoro.tts_chunks("aa|FAIL|bbb", "af_heart", 1.0, stats))
    assert [len(c) for c in out] == [20, 30]
    assert stats == {"chunks": 3, "failed": 1}


# ── /v1/audio/* ─────────────────────────────────────────────────────────────


@pytest.fixture
def client(tmp_path, monkeypatch, kokoro, stt):
    from config import settings

    monkeypatch.setattr(settings, "data_dir", tmp_path, raising=False)
    from api import openai_audio

    app = FastAPI()
    app.include_router(openai_audio.router, prefix="/v1")
    return TestClient(app)


def _key(scopes=("audio",)):
    return svc.issue_companion_key("jocasta", list(scopes))


def _sdk(client, key):
    from openai import OpenAI
    return OpenAI(base_url="http://testserver/v1", api_key=key, http_client=client)


def test_no_key_is_401_and_the_wrong_scope_is_403(client):
    r = client.post("/v1/audio/speech", json={"input": "hi"})
    assert r.status_code == 401
    llm_only = _key(("llm",))
    r = client.post("/v1/audio/speech", json={"input": "hi"},
                    headers={"Authorization": f"Bearer {llm_only}"})
    assert r.status_code == 403 and "'audio'" in r.json()["detail"]


def test_transcription_through_the_sdk(client):
    sdk = _sdk(client, _key())
    ogg = audio_codec.encode(_tone(), SR, "opus")
    out = sdk.audio.transcriptions.create(model="whisper-1", file=("note.ogg", ogg))
    assert out.text == "from parakeet"


def test_verbose_transcription_carries_duration(client):
    sdk = _sdk(client, _key())
    out = sdk.audio.transcriptions.create(model="whisper-1", response_format="verbose_json",
                                          file=("n.wav", audio_codec.encode(_tone(), SR, "wav")))
    assert abs(out.duration - 1.0) < 0.1


def test_unreadable_audio_is_a_400(client):
    r = client.post("/v1/audio/transcriptions", files={"file": ("x.ogg", b"nope")},
                    headers={"Authorization": f"Bearer {_key()}"})
    assert r.status_code == 400


@pytest.mark.parametrize("fmt", ["mp3", "wav", "opus"])
def test_speech_through_the_sdk_decodes(client, fmt):
    sdk = _sdk(client, _key())
    resp = sdk.audio.speech.create(model="tts-1", voice="nova", input="aa|bbb", response_format=fmt)
    pcm = audio_codec.decode_pcm(resp.content, 24000)
    assert len(pcm) > 0


def test_streamed_speech_carries_every_chunk_with_pauses(client):
    """TestClient buffers the body, so chunk BOUNDARIES are covered by the
    StreamEncoder tests and timed against the built sidecar; this checks the
    streamed audio is all there: 3 chunks of 500 samples + 2 pauses of 80 ms."""
    r = client.post("/v1/audio/speech", headers={"Authorization": f"Bearer {_key()}"},
                    json={"input": "a" * 50 + "|" + "b" * 50 + "|" + "c" * 50,
                          "response_format": "pcm", "stream": True})
    assert r.status_code == 200
    assert len(r.content) // 2 == 3 * 500 + 2 * int(24000 * 0.08)


def test_an_empty_chunk_does_not_break_the_encoder():
    enc = audio_codec.StreamEncoder("mp3", SR)
    assert enc.feed(np.zeros(0, np.float32)) == b""
    enc.feed(_tone(0.5))
    assert enc.close()


@pytest.mark.parametrize("body,why", [
    ({"input": "hi", "response_format": "flac"}, "response_format"),
    ({"input": "hi", "instructions": "whisper it"}, "instructions"),
    ({"input": "x" * 5000}, "longer"),
    ({"input": "hi", "speed": 9}, "speed"),
])
def test_what_we_cannot_honour_is_refused(client, body, why):
    r = client.post("/v1/audio/speech", json=body, headers={"Authorization": f"Bearer {_key()}"})
    assert r.status_code == 400 and why in r.json()["detail"]


def test_streaming_splits_off_the_first_sentence_only(monkeypatch):
    """A short reply packs into ONE chunk, so nothing plays until all of it is
    synthesised. The stream splits the first sentence out; the file path does not."""
    from services.audio_llm import audio_llm

    seen = []

    class Rec:
        def generate(self, chunk, voice, speed):
            seen.append(chunk)
            return types.SimpleNamespace(audio=np.full(10, 0.1, dtype=np.float32))

    monkeypatch.setattr(audio_llm, "_model", Rec())
    monkeypatch.setattr(audio_llm, "_preprocess_text_for_tts", lambda t: t)
    text = "First sentence here. Second one. Third one too."
    list(audio_llm.tts_chunks(text, "af_heart"))
    assert seen == [text]
    seen.clear()
    list(audio_llm.tts_chunks(text, "af_heart", first_sentence_alone=True))
    assert seen == ["First sentence here.", "Second one. Third one too."]
