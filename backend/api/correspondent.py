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


def _validate_imap_login(host: str, port: int, user: str, password: str, use_ssl: bool) -> bool:
    """Best-effort login probe. Returns True if we can log in, False on
    any auth or network failure (caller raises 4xx)."""
    try:
        from imap_tools import MailBox
        with MailBox(host).login(user, password, initial_folder="INBOX"):
            return True
    except Exception as e:
        logger.info(f"[correspondent.validate_login] {user}@{host}:{port} failed: {e}")
        return False


@router.post("/accounts")
async def add_account(request: AddAccountRequest):
    from services.credential_locker import add_imap_account
    user = request.imap_user or request.email
    host = request.imap_host or _suggest_imap_host(request.email)
    if not host:
        raise HTTPException(status_code=400, detail="Could not determine IMAP host; please provide imap_host.")

    ok = await asyncio.to_thread(
        _validate_imap_login, host, request.imap_port, user, request.imap_password, request.use_ssl,
    )
    if not ok:
        raise HTTPException(status_code=401, detail="IMAP login failed. Check the app password and host/port.")

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
        imap_password=request.imap_password,
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
