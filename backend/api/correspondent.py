"""Correspondent API — Phase 6 of v2-information-cortex.

Endpoints for managing IMAP accounts, triggering syncs, and handling the
low-confidence approval queue.

  POST   /correspondent/accounts           — add (validates IMAP login)
  GET    /correspondent/accounts           — list (no passwords)
  DELETE /correspondent/accounts/{email}   — remove
  POST   /correspondent/sync               — poll all enabled accounts now
  GET    /correspondent/status             — last poll, counts, errors
  GET    /correspondent/queue              — pending low-confidence routes
  POST   /correspondent/queue/{item}/approve   — approve + ingest
  POST   /correspondent/queue/{item}/dismiss   — drop
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

router = APIRouter()


class AddAccountRequest(BaseModel):
    email: str
    imap_host: str
    imap_port: int = 993
    imap_user: Optional[str] = None  # defaults to email if omitted
    imap_password: str
    use_ssl: bool = True
    # Phase 8 — outbound SMTP
    smtp_host: Optional[str] = None
    smtp_port: int = 465
    smtp_use_tls: bool = True
    send_confirmations: bool = True
    default_forward_notebook_id: Optional[str] = None


class UpdateAccountRequest(BaseModel):
    """Patch fields on an existing account."""
    send_confirmations: Optional[bool] = None
    default_forward_notebook_id: Optional[str] = None
    weekly_journal_enabled: Optional[bool] = None


class ApproveRequest(BaseModel):
    notebook_id: Optional[str] = Field(default=None, description="Override the auto-suggested notebook")


def _suggest_imap_host(email: str) -> Optional[str]:
    """Best-effort default for the IMAP host based on the email domain.
    Used as a hint; the user can always type a custom host."""
    domain = (email.split("@", 1)[-1] or "").lower()
    return {
        "gmail.com": "imap.gmail.com",
        "googlemail.com": "imap.gmail.com",
        "fastmail.com": "imap.fastmail.com",
        "fastmail.fm": "imap.fastmail.com",
        "icloud.com": "imap.mail.me.com",
        "me.com": "imap.mail.me.com",
        "mac.com": "imap.mail.me.com",
        "outlook.com": "outlook.office365.com",
        "hotmail.com": "outlook.office365.com",
        "live.com": "outlook.office365.com",
    }.get(domain)


def _suggest_smtp(email: str) -> Optional[Dict[str, Any]]:
    """Best-effort SMTP host/port/TLS defaults for known providers."""
    domain = (email.split("@", 1)[-1] or "").lower()
    table: Dict[str, Dict[str, Any]] = {
        "gmail.com": {"host": "smtp.gmail.com", "port": 465, "use_tls": True},
        "googlemail.com": {"host": "smtp.gmail.com", "port": 465, "use_tls": True},
        "fastmail.com": {"host": "smtp.fastmail.com", "port": 465, "use_tls": True},
        "fastmail.fm": {"host": "smtp.fastmail.com", "port": 465, "use_tls": True},
        "icloud.com": {"host": "smtp.mail.me.com", "port": 587, "use_tls": True},
        "me.com": {"host": "smtp.mail.me.com", "port": 587, "use_tls": True},
        "mac.com": {"host": "smtp.mail.me.com", "port": 587, "use_tls": True},
        "outlook.com": {"host": "smtp.office365.com", "port": 587, "use_tls": True},
        "hotmail.com": {"host": "smtp.office365.com", "port": 587, "use_tls": True},
        "live.com": {"host": "smtp.office365.com", "port": 587, "use_tls": True},
    }
    return table.get(domain)


@router.get("/smtp-hint")
async def smtp_hint(email: str):
    """Return SMTP host/port/TLS defaults for an email domain.

    Used by the frontend to auto-fill the SMTP fields the same way the
    IMAP fields are pre-populated.
    """
    hint = _suggest_smtp(email)
    if not hint:
        return {"found": False}
    return {"found": True, **hint}


def _validate_imap_login(host: str, port: int, user: str, password: str, use_ssl: bool) -> tuple[bool, Optional[str]]:
    """Best-effort login probe. Returns (ok, error_message) — error_message
    is None on success or the actual provider exception text on failure.
    The caller surfaces error_message in the 401 detail so the UI can show
    a useful toast instead of a generic "login failed."""
    try:
        from imap_tools import MailBox
        with MailBox(host).login(user, password, initial_folder="INBOX"):
            return True, None
    except Exception as e:
        logger.info(f"[correspondent.validate_login] {user}@{host}:{port} failed: {e}")
        # The imap_tools / imaplib exception message often includes the
        # raw server response (e.g. "AUTHENTICATIONFAILED: Invalid
        # credentials" from Gmail). Pass it through so the user sees
        # which gate actually failed.
        return False, str(e)[:200]


@router.post("/accounts")
async def add_account(request: AddAccountRequest):
    from services.credential_locker import add_imap_account
    user = request.imap_user or request.email
    host = request.imap_host or _suggest_imap_host(request.email)
    if not host:
        raise HTTPException(status_code=400, detail="Could not determine IMAP host; please provide imap_host.")

    # Strip ALL whitespace from the app password. Gmail/Fastmail/iCloud
    # display app passwords with spaces (`abcd efgh ijkl mnop`) and users
    # paste them as-is — IMAP servers reject the spaced form. Doing this
    # server-side means every client gets the fix; the user's saved value
    # is the clean one too.
    cleaned_password = "".join((request.imap_password or "").split())
    if not cleaned_password:
        raise HTTPException(status_code=400, detail="App password is required.")

    ok, login_err = await asyncio.to_thread(
        _validate_imap_login, host, request.imap_port, user, cleaned_password, request.use_ssl,
    )
    if not ok:
        detail = (
            f"IMAP login failed: {login_err}"
            if login_err
            else "IMAP login failed. Check the app password and host/port."
        )
        raise HTTPException(status_code=401, detail=detail)

    # SMTP host: fall back to per-provider default when not supplied.
    smtp_host = request.smtp_host
    smtp_port = request.smtp_port
    smtp_use_tls = request.smtp_use_tls
    if not smtp_host:
        smtp_hint_data = _suggest_smtp(request.email)
        if smtp_hint_data:
            smtp_host = smtp_hint_data["host"]
            smtp_port = smtp_hint_data["port"]
            smtp_use_tls = smtp_hint_data["use_tls"]

    cred = await add_imap_account(
        email=request.email,
        imap_host=host,
        imap_port=request.imap_port,
        imap_user=user,
        imap_password=cleaned_password,
        use_ssl=request.use_ssl,
        smtp_host=smtp_host,
        smtp_port=smtp_port,
        smtp_use_tls=smtp_use_tls,
        send_confirmations=request.send_confirmations,
        default_forward_notebook_id=request.default_forward_notebook_id,
    )
    return {"ok": True, "account": cred.model_dump()}


@router.patch("/accounts/{email}")
async def update_account(email: str, request: UpdateAccountRequest):
    """Patch mutable fields on an existing account."""
    from services.credential_locker import update_imap_state
    ok = await update_imap_state(
        email=email,
        send_confirmations=request.send_confirmations,
        default_forward_notebook_id=request.default_forward_notebook_id,
        weekly_journal_enabled=request.weekly_journal_enabled,
    )
    if not ok:
        raise HTTPException(status_code=404, detail="Account not found")
    return {"ok": True}


@router.get("/accounts")
async def list_accounts():
    from services.credential_locker import list_imap_accounts
    accounts = await list_imap_accounts()
    return {"accounts": [a.model_dump() for a in accounts]}


@router.delete("/accounts/{email}")
async def delete_account(email: str):
    from services.credential_locker import delete_imap_account
    ok = await delete_imap_account(email)
    if not ok:
        raise HTTPException(status_code=404, detail="Account not found")
    return {"ok": True}


@router.post("/sync")
async def sync_now():
    from agents.correspondent import correspondent_agent
    summary = await correspondent_agent.poll_all()
    return {"ok": True, "summary": summary}


@router.get("/status")
async def status():
    from agents.correspondent import correspondent_agent
    return correspondent_agent.status()


@router.get("/queue")
async def list_queue():
    from agents.correspondent import correspondent_agent
    return {"items": correspondent_agent.list_queue()}


@router.get("/aside")
async def aside():
    """I5 (2026-06-09) — proactive aside for ChatInterface.

    Returns a short prompt the user can see after any chat turn:
      - queue ≥ 3 items
      - any pending subscription/entity proposal
      - any inbox in last_error state

    Returns `{aside: null}` when nothing is worth surfacing — caller
    quietly drops it (no toast, no UI bump). Anti-nag throttling is
    intentionally NOT here; the cost of building the reply is one JSON
    file read so the frontend dropping it is fine.
    """
    from agents.correspondent import correspondent_agent
    queue = correspondent_agent.list_queue()
    subs = correspondent_agent.list_subscription_queue()
    status = correspondent_agent.status() or {}
    accounts = status.get("accounts") or {}

    # Priority: errors first, then queue threshold, then proposals
    errored = [(em, info) for em, info in accounts.items() if info.get("last_error")]
    if errored:
        em, info = errored[0]
        return {
            "aside": f"📬 Correspondent: `{em}` is in an error state — `{(info.get('last_error') or '')[:80]}`. Check Settings → Correspondent.",
            "kind": "correspondent_error",
            "curator_name": "Correspondent",
        }
    if len(queue) >= 3:
        return {
            "aside": f"📬 Correspondent: **{len(queue)}** newsletter(s) waiting for routing. Say `@correspondent show queue` to triage.",
            "kind": "correspondent_queue",
            "curator_name": "Correspondent",
        }
    if subs:
        kinds = sum(1 for s in subs if s.get("kind") == "entity")
        subscriptions_n = len(subs) - kinds
        bits = []
        if subscriptions_n:
            bits.append(f"{subscriptions_n} newsletter proposal{'s' if subscriptions_n != 1 else ''}")
        if kinds:
            bits.append(f"{kinds} entity watch{'es' if kinds != 1 else ''}")
        return {
            "aside": f"📬 Correspondent has {' and '.join(bits)} waiting. Say `@correspondent show subscriptions`.",
            "kind": "correspondent_subscription",
            "curator_name": "Correspondent",
        }
    # P3.5 (2026-06-10) — proactive unsubscribe-candidate surface.
    # Per F.1 (locked): one at a time. We pick the worst candidate and
    # surface only one per chat turn. Anti-nag throttling lives in the
    # frontend by reusing the existing curator-aside slot (same-message
    # priority means errors + queue + subs preempt this).
    try:
        from services.unsubscribe_suggestions import list_candidates
        candidates = await list_candidates()
        if candidates:
            top = candidates[0]
            sender = top.get("sender_email", "?")
            grade = top.get("grade") or "—"
            return {
                "aside": (
                    f"📬 Correspondent: `{sender}` has scored **{grade}** with "
                    f"{top.get('lifetime_emails', 0)} email(s) ingested. "
                    f"Worth dropping? Say `unsubscribe {sender}` or `unsubscribe {sender} snooze 30`."
                ),
                "kind": "correspondent_unsubscribe_candidate",
                "curator_name": "Correspondent",
            }
    except Exception as _ce:
        logger.debug(f"[correspondent.aside] unsubscribe candidate scan skipped: {_ce}")

    return {"aside": None, "kind": None, "curator_name": "Correspondent"}


@router.post("/queue/{item_id}/approve")
async def approve_queue_item(item_id: str, request: ApproveRequest):
    from agents.correspondent import correspondent_agent
    result = await correspondent_agent.approve_queued(item_id, notebook_id=request.notebook_id)
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("reason", "approve failed"))
    return result


@router.post("/queue/{item_id}/dismiss")
async def dismiss_queue_item(item_id: str):
    from agents.correspondent import correspondent_agent
    result = await correspondent_agent.dismiss_queued(item_id)
    if not result.get("ok"):
        raise HTTPException(status_code=404, detail=result.get("reason", "not found"))
    return result


class BatchApproveRequest(BaseModel):
    """P3.1 (2026-06-10) — bulk approve. notebook_id optional; when set,
    all items route there. When omitted, each item uses its own
    top_candidate."""
    item_ids: List[str]
    notebook_id: Optional[str] = None


@router.post("/queue/batch-approve")
async def batch_approve_queue(request: BatchApproveRequest):
    from agents.correspondent import correspondent_agent
    if not request.item_ids:
        raise HTTPException(status_code=400, detail="item_ids must not be empty")
    return await correspondent_agent.approve_queued_batch(
        item_ids=request.item_ids,
        notebook_id=request.notebook_id,
    )


# ---------------------------------------------------------------------------
# Phase 7 — subscription proposals (sister-newsletter auto-subscribe)
# ---------------------------------------------------------------------------


@router.get("/subscriptions")
async def list_subscriptions():
    from agents.correspondent import correspondent_agent
    return {"items": correspondent_agent.list_subscription_queue()}


@router.post("/subscriptions/{item_id}/approve")
async def approve_subscription(item_id: str, request: ApproveRequest):
    from agents.correspondent import correspondent_agent
    result = await correspondent_agent.approve_subscription(item_id, notebook_id=request.notebook_id)
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("reason", "approve failed"))
    return result


@router.post("/subscriptions/{item_id}/dismiss")
async def dismiss_subscription(item_id: str):
    from agents.correspondent import correspondent_agent
    result = await correspondent_agent.dismiss_subscription(item_id)
    if not result.get("ok"):
        raise HTTPException(status_code=404, detail=result.get("reason", "not found"))
    return result
