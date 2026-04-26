"""Flash Cards API endpoints.

Flash Cards reuse the existing quiz generator and grader — this module only
adds the thin plumbing specific to interactive study:

    GET  /flashcards/tutor/{notebook_id}  — read the notebook's tutor-voice profile
    PUT  /flashcards/tutor/{notebook_id}  — update the tutor-voice profile
    POST /flashcards/speak                — one-shot TTS of a short line in the tutor voice

Card generation and grading go through /quiz/* (generate, grade, stats, due).
Answer-by-voice uses /voice/transcribe-quick (already stateless).
"""
from __future__ import annotations

import logging
import uuid
from typing import Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from services.audio_llm import KOKORO_VOICES, VOICE_ALIASES, DEFAULT_VOICE
from storage.notebook_store import notebook_store

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/flashcards", tags=["flashcards"])


# ─── Tutor voice profile ───────────────────────────────────────────────────

_ALLOWED_GENDERS = {"female", "male"}
_ALLOWED_ACCENTS = {"us", "uk"}  # matches the app's podcast voice UX surface

# Sensible defaults — a warm US female voice ("Heart") reads feedback if the
# notebook has no explicit tutor configured.
_DEFAULT_TUTOR = {
    "gender": "female",
    "accent": "us",
    "persona": "",           # e.g. "Nora" — shown in UI; does not affect TTS
    "voice_id": DEFAULT_VOICE,  # concrete Kokoro ID used for TTS
    "speed": 1.0,
    "autoplay": True,        # auto-play feedback on wrong answer
}


class TutorProfile(BaseModel):
    """Per-notebook tutor voice profile."""
    gender: str = Field(default="female")
    accent: str = Field(default="us")
    persona: str = Field(default="")
    voice_id: Optional[str] = None        # optional explicit Kokoro ID override
    speed: float = Field(default=1.0, ge=0.5, le=1.5)
    autoplay: bool = Field(default=True)


class TutorUpdate(BaseModel):
    """Partial update — every field is optional so the UI can patch piecemeal."""
    gender: Optional[str] = None
    accent: Optional[str] = None
    persona: Optional[str] = None
    voice_id: Optional[str] = None
    speed: Optional[float] = Field(default=None, ge=0.5, le=1.5)
    autoplay: Optional[bool] = None


def _resolve_voice_for(gender: str, accent: str, explicit: Optional[str] = None) -> str:
    """Pick a concrete Kokoro voice ID for a (gender, accent) pair.

    If `explicit` is a known Kokoro voice, it wins. Otherwise we fall back
    through legacy aliases so the tutor voice lines up with the podcast
    voice map the user already knows.
    """
    if explicit and explicit in KOKORO_VOICES:
        return explicit
    alias = f"{accent}_{gender}"
    return VOICE_ALIASES.get(alias, DEFAULT_VOICE)


def _read_tutor(nb: dict) -> dict:
    """Merge stored tutor profile with defaults so the UI always gets a full record."""
    stored = (nb or {}).get("tutor_voice") or {}
    merged = {**_DEFAULT_TUTOR, **stored}
    # Always re-derive voice_id from gender+accent unless the user pinned one
    if not stored.get("voice_id"):
        merged["voice_id"] = _resolve_voice_for(merged["gender"], merged["accent"])
    return merged


@router.get("/tutor/{notebook_id}")
async def get_tutor(notebook_id: str):
    """Return the tutor-voice profile for this notebook (with defaults filled in)."""
    nb = await notebook_store.get(notebook_id)
    if not nb:
        raise HTTPException(status_code=404, detail="Notebook not found")
    return _read_tutor(nb)


@router.put("/tutor/{notebook_id}")
async def update_tutor(notebook_id: str, body: TutorUpdate):
    """Patch the tutor profile. Only provided fields are changed."""
    nb = await notebook_store.get(notebook_id)
    if not nb:
        raise HTTPException(status_code=404, detail="Notebook not found")

    current = _read_tutor(nb)
    patch = {k: v for k, v in body.model_dump().items() if v is not None}

    # Validate enums with clear messages
    if "gender" in patch and patch["gender"] not in _ALLOWED_GENDERS:
        raise HTTPException(status_code=400, detail=f"gender must be one of {sorted(_ALLOWED_GENDERS)}")
    if "accent" in patch and patch["accent"] not in _ALLOWED_ACCENTS:
        raise HTTPException(status_code=400, detail=f"accent must be one of {sorted(_ALLOWED_ACCENTS)}")
    if "voice_id" in patch and patch["voice_id"] and patch["voice_id"] not in KOKORO_VOICES:
        raise HTTPException(status_code=400, detail=f"Unknown Kokoro voice_id '{patch['voice_id']}'")

    merged = {**current, **patch}
    # Re-derive voice_id from (gender, accent) if the user changed either and
    # did not explicitly pin a voice_id in this same request.
    if ("gender" in patch or "accent" in patch) and "voice_id" not in patch:
        merged["voice_id"] = _resolve_voice_for(merged["gender"], merged["accent"])

    await notebook_store.update(notebook_id, {"tutor_voice": merged})
    return merged


# ─── One-shot TTS (tutor reads feedback aloud) ─────────────────────────────

class SpeakRequest(BaseModel):
    notebook_id: Optional[str] = None  # if provided, loads tutor from this notebook
    text: str = Field(min_length=1, max_length=4000)
    # Inline overrides — useful for the ChatActionBar where no notebook is selected
    voice_id: Optional[str] = None
    gender: Optional[str] = None
    accent: Optional[str] = None
    speed: float = Field(default=1.0, ge=0.5, le=1.5)


@router.post("/speak")
async def speak(req: SpeakRequest):
    """Render `text` as a short .wav in the tutor voice and stream it back.

    Resolution order for the voice:
      1. `voice_id` on the request (if a valid Kokoro voice)
      2. stored tutor_voice.voice_id on the notebook
      3. (gender, accent) from the request, merged with the notebook's tutor
      4. defaults (US female)
    """
    # Load notebook tutor if available
    tutor = dict(_DEFAULT_TUTOR)
    if req.notebook_id:
        nb = await notebook_store.get(req.notebook_id)
        if nb:
            tutor = _read_tutor(nb)

    # Apply request overrides
    if req.gender and req.gender in _ALLOWED_GENDERS:
        tutor["gender"] = req.gender
    if req.accent and req.accent in _ALLOWED_ACCENTS:
        tutor["accent"] = req.accent
    if req.voice_id and req.voice_id in KOKORO_VOICES:
        tutor["voice_id"] = req.voice_id
    else:
        # Re-derive from possibly-updated gender/accent
        tutor["voice_id"] = _resolve_voice_for(
            tutor.get("gender", "female"),
            tutor.get("accent", "us"),
            tutor.get("voice_id"),
        )

    speed = float(req.speed or tutor.get("speed", 1.0))

    # Synthesize — audio_llm lazy-loads Kokoro on first call
    try:
        from services.audio_llm import audio_llm
        if not audio_llm.is_available:
            await audio_llm.initialize()
        if not audio_llm.is_available:
            raise HTTPException(
                status_code=503,
                detail="Tutor voice (Kokoro TTS) is not available. Check audio-llm status.",
            )

        out_path = await audio_llm.text_to_speech(
            text=req.text,
            voice=tutor["voice_id"],
            speed=speed,
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"[flashcards.speak] TTS failed: {e}")
        raise HTTPException(status_code=500, detail=f"TTS failed: {e}")

    return FileResponse(
        out_path,
        media_type="audio/wav",
        filename=f"tutor-{uuid.uuid4().hex[:8]}.wav",
        headers={
            "X-Tutor-Voice": tutor["voice_id"],
            "X-Tutor-Persona": tutor.get("persona", "") or "",
            "Cache-Control": "no-store",
        },
    )


# Note: answer-by-voice uses the existing /voice/transcribe-quick endpoint —
# it's already stateless (does NOT create a source) and returns {text, language,
# segments}. No reason to duplicate that plumbing here.


# ─── Smoke tests ───────────────────────────────────────────────────────────

def _run_smoke_tests():
    """Minimal invariants — run with `python -m api.flashcards`.

    We avoid side effects on disk/network; only the pure helpers are checked.
    """
    # _resolve_voice_for: maps every (gender, accent) to a valid Kokoro voice
    for g in _ALLOWED_GENDERS:
        for a in _ALLOWED_ACCENTS:
            vid = _resolve_voice_for(g, a)
            assert vid in KOKORO_VOICES, (g, a, vid)

    # Explicit override wins
    assert _resolve_voice_for("female", "us", "am_adam") == "am_adam"

    # Bogus override falls back
    assert _resolve_voice_for("female", "us", "does-not-exist") in KOKORO_VOICES

    # _read_tutor fills defaults even for an empty notebook
    t = _read_tutor({})
    assert t["gender"] == "female"
    assert t["accent"] == "us"
    assert t["voice_id"] in KOKORO_VOICES

    # _read_tutor preserves stored fields
    stored_nb = {"tutor_voice": {"gender": "male", "accent": "uk", "persona": "Miles"}}
    t2 = _read_tutor(stored_nb)
    assert t2["gender"] == "male"
    assert t2["accent"] == "uk"
    assert t2["persona"] == "Miles"
    assert t2["voice_id"] == VOICE_ALIASES["uk_male"]

    # Pinned voice_id overrides derivation
    stored_pinned = {"tutor_voice": {"gender": "female", "accent": "us", "voice_id": "am_adam"}}
    t3 = _read_tutor(stored_pinned)
    assert t3["voice_id"] == "am_adam"

    print("[api.flashcards] smoke tests passed.")


if __name__ == "__main__":
    _run_smoke_tests()
