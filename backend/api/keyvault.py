"""Recovery-phrase setup and key recovery.

K-1 of the v2.5.0 plan. This is the half that makes the rest of K-1 safe.

Without a recovery key, LocalBook's move off the old machine-derived key is a
DOWNGRADE in durability, not an upgrade: the old key could always be re-derived
from the hostname and username, so a wiped Keychain cost nothing. The new key is
random and lives only in the Keychain, so a wiped Keychain costs the data. What
buys that back is a wrapped copy on disk, and the only thing that can unwrap it
is the 24-word phrase the user writes down.

The flow is deliberately two calls:

  POST /keyvault/recovery/begin    → generate a phrase, show it, DO NOT store it
  POST /keyvault/recovery/confirm  → the user types 4 of the words back, and only
                                     THEN is the public key stored and every
                                     existing key wrapped

`begin` persisting nothing is the point. A phrase the user never actually wrote
down, silently accepted, is the failure this flow exists to prevent — it would
read as "recovery configured" on every screen while being worth nothing.

⚠️ The phrase crosses the wire once, on a loopback-only API, and is never
logged or written to disk. The private key derived from it exists only for the
microseconds needed to compute the public half.
"""

import asyncio
import logging
import secrets
from typing import Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from services import key_escrow, keyvault
from services.keyvault import KeyVaultError

router = APIRouter()
logger = logging.getLogger(__name__)

# How many of the 24 words the user types back. Enough that skipping the write-
# down is not survivable; few enough that it is not a punishment.
VERIFY_WORD_COUNT = 3


class BeginResponse(BaseModel):
    phrase: str
    words: List[str]
    verify_indices: List[int] = Field(
        description="1-based positions the user must type back to confirm"
    )
    already_configured: bool


class ConfirmRequest(BaseModel):
    phrase: str
    # {position (1-based, as shown): the word the user typed}
    answers: Dict[int, str]


class RestoreRequest(BaseModel):
    phrase: str
    purpose: Optional[str] = None
    device: Optional[str] = None
    # Another Mac's set only: use its credentials/backup key over this Mac's own.
    # Its volume password and sync identity never replace this Mac's.
    replace: bool = False


class PhraseOnly(BaseModel):
    phrase: str


@router.get("/keyvault/status")
async def status():
    """What is protected, what is not, and on which device.

    `fully_protected` is the only field worth trusting at a glance: having a
    recovery key configured is NOT the same as every key having a wrapped copy,
    because a key created before setup stays unwrapped until wrap_all runs.
    """
    try:
        return keyvault.status()
    except KeyVaultError as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/keyvault/recovery/begin", response_model=BeginResponse)
async def begin_recovery_setup():
    """Generate a phrase and show it once. NOTHING is stored by this call.

    Calling it again simply generates a different phrase; only `confirm`
    commits, so an abandoned setup leaves no trace and no false assurance.
    """
    phrase = keyvault.generate_recovery_phrase()
    words = phrase.split()
    # secrets, not random: this picks which words prove the user wrote them down.
    indices = sorted(secrets.SystemRandom().sample(range(1, len(words) + 1), VERIFY_WORD_COUNT))
    return BeginResponse(
        phrase=phrase,
        words=words,
        verify_indices=indices,
        already_configured=keyvault.has_recovery_key(),
    )


@router.post("/keyvault/recovery/confirm")
async def confirm_recovery_setup(req: ConfirmRequest):
    """Check the typed-back words, store the PUBLIC key, wrap every existing key."""
    words = req.phrase.split()
    if len(words) != 24:
        raise HTTPException(status_code=400, detail="that is not a 24-word phrase")

    if len(req.answers) < VERIFY_WORD_COUNT:
        raise HTTPException(
            status_code=400,
            detail=f"type back all {VERIFY_WORD_COUNT} requested words",
        )

    wrong = []
    for position, typed in req.answers.items():
        if not (1 <= position <= len(words)):
            raise HTTPException(status_code=400, detail=f"word {position} does not exist")
        if (typed or "").strip().lower() != words[position - 1].lower():
            wrong.append(position)
    if wrong:
        # Which ones, not just "wrong" — the user is copying from paper and a
        # bare rejection makes them start the whole thing again.
        raise HTTPException(
            status_code=400,
            detail=f"word {' and '.join(str(w) for w in sorted(wrong))} did not match",
        )

    rotated = keyvault.has_recovery_key()
    try:
        keyvault.set_recovery_key(req.phrase)
        wrapped = keyvault.wrap_all()
    except KeyVaultError as exc:
        raise HTTPException(status_code=500, detail=str(exc))

    # Setting it up proves it; and every paired Mac now holds the new copies
    # (replacing the old phrase's, so an exposed old phrase opens nothing current).
    key_escrow.mark_phrase_checked()
    key_escrow.publish()
    logger.info("[keyvault] recovery key %s; wrapped %s",
                "rotated" if rotated else "configured", wrapped.get("wrapped"))
    out = {"ok": True, **wrapped, "rotated": rotated, "status": keyvault.status()}
    if rotated:
        out["note"] = ("Backups taken before today were sealed to your previous phrase — keep it "
                       "until they have aged out, or take a fresh backup now.")
    return out


@router.post("/keyvault/recovery/check")
async def check_phrase(req: PhraseOnly):
    """Confirm a phrase still matches this install, without changing anything.

    For the quarterly "do you still have it?" prompt. Compares only the derived
    PUBLIC key, so a wrong phrase reveals nothing beyond being wrong.
    """
    if not keyvault.has_recovery_key():
        raise HTTPException(status_code=400, detail="no recovery key is configured yet")
    try:
        derived = keyvault.recovery_public_key_from_phrase(req.phrase)
    except KeyVaultError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    import base64

    stored = keyvault._recovery_pub_path().read_text().strip()
    matches = base64.b64encode(derived).decode() == stored
    if matches:
        key_escrow.mark_phrase_checked()
    return {"matches": matches, "phrase_check": key_escrow.phrase_check()}


@router.post("/keyvault/recovery/restore")
async def restore(req: RestoreRequest):
    """Put keys back in this Keychain from their wrapped copies.

    `purpose` restores one; omitting it restores everything wrapped for the
    device. `device` names another machine's key set — that is how a replacement
    Mac recovers from a backup.
    """
    try:
        result = await asyncio.to_thread(
            key_escrow.restore, req.phrase, req.device,
            [req.purpose] if req.purpose else None, req.replace)
    except KeyVaultError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    restored, kept, failed = result["restored"], result["kept"], result["failed"]

    if not restored and failed:
        # Everything failed: almost always the wrong phrase, and a 200 with an
        # empty list would look like success.
        raise HTTPException(
            status_code=400,
            detail="nothing could be restored — " + "; ".join(failed.values()),
        )
    return {"restored": restored, "kept": kept, "failed": failed, "status": keyvault.status()}


@router.get("/keyvault/key-sets")
async def key_sets():
    """Whose keys could be restored on this Mac — this Mac's, a restored backup's,
    and every paired Mac's escrowed set. Names and purposes only; nothing secret."""
    sets = await asyncio.to_thread(key_escrow.key_sets)
    return {"sets": sets, "phrase_check": key_escrow.phrase_check()}


@router.post("/keyvault/rewrap")
async def rewrap():
    """Wrap any key that does not yet have a copy. Safe to call repeatedly."""
    if not keyvault.has_recovery_key():
        raise HTTPException(status_code=400, detail="no recovery key is configured yet")
    try:
        out = {"ok": True, **keyvault.wrap_all(), "status": keyvault.status()}
        key_escrow.publish()
        return out
    except KeyVaultError as exc:
        raise HTTPException(status_code=500, detail=str(exc))
