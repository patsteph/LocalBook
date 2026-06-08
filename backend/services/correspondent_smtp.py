"""correspondent_smtp — Phase 8 of v2-information-cortex.

Outbound SMTP for Correspondent. First use: confirmation replies after
reply-to-ingest. Future uses: Phase 13 weekly auto-journal.

`aiosmtplib` (MIT) — async; no thread pool needed.

Failure semantics:
- Never raises out of `send_message`. Returns False on any failure and
  logs at warning. Callers fire-and-forget.
- Retries 3× with 1-second backoff to absorb transient network blips.
"""
from __future__ import annotations

import asyncio
import logging
from email.message import EmailMessage
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)


async def send_message(
    account: Dict[str, Any],
    *,
    to: str,
    subject: str,
    body_text: str,
    reply_to_message_id: Optional[str] = None,
) -> bool:
    """Send a plain-text email via the account's configured SMTP host.

    `account` is the dict returned by `credential_locker.get_imap_account`
    (which includes smtp_host / smtp_port / smtp_use_tls / imap_user /
    imap_password — most providers share creds across IMAP + SMTP).

    Returns True on success, False on any failure (logged).
    """
    if not account:
        return False
    host = account.get("smtp_host")
    if not host:
        logger.debug("[correspondent_smtp] no smtp_host configured for account")
        return False
    if not to:
        logger.debug("[correspondent_smtp] no recipient")
        return False

    from_addr = account.get("email") or account.get("imap_user") or ""
    if not from_addr:
        logger.debug("[correspondent_smtp] no from address")
        return False

    msg = EmailMessage()
    msg["From"] = from_addr
    msg["To"] = to
    msg["Subject"] = subject or "(no subject)"
    if reply_to_message_id:
        # Bare angle-bracketed Message-ID per RFC 5322 §3.6.4.
        ref = reply_to_message_id if reply_to_message_id.startswith("<") else f"<{reply_to_message_id}>"
        msg["In-Reply-To"] = ref
        msg["References"] = ref
    msg.set_content(body_text or "")

    port = int(account.get("smtp_port") or 465)
    use_tls = bool(account.get("smtp_use_tls", True))
    user = account.get("imap_user") or from_addr
    password = account.get("imap_password") or ""

    try:
        import aiosmtplib
    except Exception as e:
        logger.warning(f"[correspondent_smtp] aiosmtplib unavailable: {e}")
        return False

    # 465 → implicit TLS (use_tls=True at connect). 587 → STARTTLS.
    implicit_tls = (port == 465) and use_tls
    start_tls = (port != 465) and use_tls

    for attempt in range(3):
        try:
            await aiosmtplib.send(
                msg,
                hostname=host,
                port=port,
                username=user,
                password=password,
                use_tls=implicit_tls,
                start_tls=start_tls,
                timeout=30,
            )
            return True
        except Exception as e:
            logger.warning(f"[correspondent_smtp] send attempt {attempt+1} failed: {e}")
            if attempt == 2:
                return False
            await asyncio.sleep(1.0)
    return False
