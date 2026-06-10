"""list_unsubscribe — Phase 5 Tier 2 / F follow-up (2026-06-10).

RFC 2369 / RFC 8058 List-Unsubscribe action with hard safety guards.

Two-step confirmation flow:
  1. `try_unsubscribe(sender)` — looks up the most recent source from
     this sender, extracts a List-Unsubscribe target, validates it,
     creates a 5-minute pending token, returns a preview the user can
     verify before executing.
  2. `execute(token)` — looks up the pending row, validates the token
     is still alive, performs the action (POST or mailto), and logs
     the attempt to the audit table.

Validation rules (per design F):
  - URL must be https://
  - URL hostname must end with the sender's email domain (no cross-
    domain, no IP-only URLs, no subdomain takeover via unrelated origins)
  - mailto: target must end with the sender's domain
  - mailto: subject is the literal "unsubscribe", body empty
  - HTTPS POST body is empty per RFC 8058; no query params we control

Even on success the local blocklist still fires so we don't ingest
anything that slips through.
"""
from __future__ import annotations

import json
import logging
import re
import uuid
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

PENDING_TOKEN_TTL_SECONDS = 5 * 60
HTTPS_REQUEST_TIMEOUT_SECONDS = 15.0


def _sender_domain(sender_email: str) -> str:
    if not sender_email or "@" not in sender_email:
        return ""
    # Handle 'Display Name <addr@example.com>' form
    addr = sender_email
    if "<" in addr and ">" in addr:
        addr = addr[addr.index("<") + 1: addr.index(">")]
    return addr.rsplit("@", 1)[-1].strip().lower()


def _suffix_match(hostname: str, base_domain: str) -> bool:
    """True if hostname is base_domain or a sub-domain of base_domain.
    Does NOT match unrelated domains that happen to contain the suffix
    string ('attackernews.io' is not a sub-domain of 'news.io')."""
    hostname = (hostname or "").lower().strip(".")
    base_domain = (base_domain or "").lower().strip(".")
    if not hostname or not base_domain:
        return False
    if hostname == base_domain:
        return True
    return hostname.endswith("." + base_domain)


def parse_list_unsubscribe(header: str) -> List[Tuple[str, str]]:
    """Split a `List-Unsubscribe` header value into typed targets.

    RFC 2369 format: `<https://...>, <mailto:...>` (each angle-bracketed).
    Returns list of (kind, target) where kind ∈ {'https', 'mailto'}.
    """
    if not header:
        return []
    out: List[Tuple[str, str]] = []
    for m in re.finditer(r"<([^>]+)>", header):
        raw = m.group(1).strip()
        low = raw.lower()
        if low.startswith("https://"):
            out.append(("https", raw))
        elif low.startswith("mailto:"):
            out.append(("mailto", raw[len("mailto:"):]))
        # Skip http:// (RFC 8058 mandates https). Skip unknown schemes.
    return out


async def find_unsub_target(sender_email: str) -> Optional[Dict[str, Any]]:
    """Look up the most recent source from this sender that includes a
    List-Unsubscribe header. Returns the first valid (https or mailto)
    target with full validation context."""
    if not sender_email:
        return None
    sender_dom = _sender_domain(sender_email)
    if not sender_dom:
        return None

    try:
        from storage.source_store import source_store
        all_by_nb = await source_store.list_all() or {}
    except Exception as e:
        logger.debug(f"[list_unsubscribe.find_unsub_target] source list failed: {e}")
        return None

    # Collect candidates with timestamps
    candidates: List[Dict[str, Any]] = []
    for nb_id, sources in all_by_nb.items():
        for s in sources or []:
            fmt = (s.get("format") or "").lower()
            if fmt not in ("email", "forward"):
                continue
            src_sender = (s.get("sender") or s.get("original_sender") or "")
            if sender_email.lower() not in src_sender.lower():
                continue
            header = s.get("list_unsubscribe") or ""
            meta = s.get("metadata") or {}
            if not header and isinstance(meta, dict):
                header = meta.get("list_unsubscribe") or ""
            if not header:
                continue
            post_flag = s.get("list_unsubscribe_post") or ""
            if not post_flag and isinstance(meta, dict):
                post_flag = meta.get("list_unsubscribe_post") or ""
            candidates.append({
                "source_id": s.get("id"),
                "notebook_id": nb_id,
                "created_at": s.get("created_at") or "",
                "header": header,
                "post_flag": post_flag,
            })

    if not candidates:
        return None
    # Most recent first
    candidates.sort(key=lambda c: c["created_at"], reverse=True)
    recent = candidates[0]

    targets = parse_list_unsubscribe(recent["header"])
    if not targets:
        return None

    # Try https first (RFC 8058 one-click), then mailto, with domain
    # validation. Skip any that fail validation.
    for kind, raw in targets:
        if kind == "https":
            try:
                parsed_url = urlparse(raw)
            except Exception:
                continue
            if (parsed_url.scheme or "").lower() != "https":
                continue
            if not _suffix_match(parsed_url.hostname or "", sender_dom):
                continue
            return {
                "kind": "https_post",
                "target": raw,
                "sender_email": sender_email,
                "sender_domain": sender_dom,
                "one_click": bool(recent.get("post_flag")),
                "source_id": recent["source_id"],
            }
        elif kind == "mailto":
            # mailto target itself must be on sender's domain
            mail_addr = raw.split("?", 1)[0]
            if not _suffix_match(_sender_domain(mail_addr), sender_dom):
                continue
            return {
                "kind": "mailto",
                "target": mail_addr,  # strip any query so we control body
                "sender_email": sender_email,
                "sender_domain": sender_dom,
                "source_id": recent["source_id"],
            }
    return None


def create_pending(target_info: Dict[str, Any]) -> str:
    """Stash the target under a 5-minute token. Returns the token."""
    from storage.database import get_db
    token = uuid.uuid4().hex[:12]
    now = datetime.utcnow()
    expires = now + timedelta(seconds=PENDING_TOKEN_TTL_SECONDS)
    try:
        conn = get_db().get_connection()
        conn.execute(
            """INSERT INTO pending_unsubscribes
               (token, sender_email, target, target_type, created_at, expires_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                token,
                target_info["sender_email"],
                target_info["target"],
                target_info["kind"],
                now.isoformat(),
                expires.isoformat(),
            ),
        )
        conn.commit()
    except Exception as e:
        logger.warning(f"[list_unsubscribe.create_pending] {e}")
        return ""
    return token


def get_pending(token: str) -> Optional[Dict[str, Any]]:
    if not token:
        return None
    try:
        from storage.database import get_db
        row = get_db().get_connection().execute(
            "SELECT * FROM pending_unsubscribes WHERE token = ?",
            (token,),
        ).fetchone()
        if not row:
            return None
        d = dict(row)
        if d["expires_at"] < datetime.utcnow().isoformat():
            # Expired — clean up and return None
            try:
                get_db().get_connection().execute(
                    "DELETE FROM pending_unsubscribes WHERE token = ?",
                    (token,),
                )
                get_db().get_connection().commit()
            except Exception:
                pass
            return None
        return d
    except Exception:
        return None


def delete_pending(token: str) -> None:
    try:
        from storage.database import get_db
        conn = get_db().get_connection()
        conn.execute("DELETE FROM pending_unsubscribes WHERE token = ?", (token,))
        conn.commit()
    except Exception:
        pass


def log_attempt(
    *,
    sender_email: str,
    target: str,
    target_type: str,
    result: str,
    result_detail: Optional[str] = None,
) -> None:
    try:
        from storage.database import get_db
        conn = get_db().get_connection()
        conn.execute(
            """INSERT INTO unsubscribe_log
               (ts, sender_email, target, target_type, result, result_detail)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                datetime.utcnow().isoformat(),
                sender_email,
                target[:500],
                target_type,
                result,
                (result_detail or "")[:500] or None,
            ),
        )
        conn.commit()
    except Exception as e:
        logger.warning(f"[list_unsubscribe.log_attempt] {e}")


async def execute(token: str) -> Dict[str, Any]:
    """Look up the pending row, perform the action, log the result.

    Returns:
      {ok: bool, result: 'sent' | 'failed' | 'expired', detail: str,
       sender_email: str, target: str, target_type: str}
    """
    pending = get_pending(token)
    if not pending:
        return {"ok": False, "result": "expired", "detail": "no pending unsubscribe for that token (or it expired)"}

    sender_email = pending["sender_email"]
    target = pending["target"]
    target_type = pending["target_type"]

    # Defense in depth: re-validate the target before acting. The pending
    # row is trusted (we wrote it), but if our validation logic later
    # tightens, the new rules should apply at execute time too.
    sender_dom = _sender_domain(sender_email)
    ok_to_send = False
    detail = ""
    if target_type == "https_post":
        try:
            parsed_url = urlparse(target)
            if (parsed_url.scheme or "").lower() == "https" and _suffix_match(parsed_url.hostname or "", sender_dom):
                ok_to_send = True
            else:
                detail = "domain validation failed at execute time"
        except Exception as ve:
            detail = f"url parse failed: {ve}"
    elif target_type == "mailto":
        mail_addr = target.split("?", 1)[0]
        if _suffix_match(_sender_domain(mail_addr), sender_dom):
            ok_to_send = True
        else:
            detail = "mailto domain validation failed at execute time"
    else:
        detail = f"unknown target type: {target_type}"

    if not ok_to_send:
        log_attempt(sender_email=sender_email, target=target,
                    target_type=target_type, result="failed", result_detail=detail)
        delete_pending(token)
        return {"ok": False, "result": "failed", "detail": detail,
                "sender_email": sender_email, "target": target, "target_type": target_type}

    # Execute
    if target_type == "https_post":
        try:
            import httpx
            async with httpx.AsyncClient(timeout=HTTPS_REQUEST_TIMEOUT_SECONDS, follow_redirects=False) as client:
                response = await client.post(target, content=b"", headers={"Content-Type": "text/plain"})
            sent_ok = 200 <= response.status_code < 400
            detail = f"HTTP {response.status_code}"
        except Exception as e:
            sent_ok = False
            detail = f"request failed: {e}"
    else:  # mailto
        try:
            from services.correspondent_smtp import send_message
            # Need an inbox configured with SMTP. Use any account that has
            # outbound SMTP configured. The unsubscribe email comes from
            # the user's own address.
            from services.credential_locker import list_imap_accounts
            accounts = await list_imap_accounts()
            account = None
            for acc in accounts:
                full = acc.model_dump() if hasattr(acc, "model_dump") else dict(acc)
                if full.get("smtp_host"):
                    account = full
                    break
            if not account:
                sent_ok = False
                detail = "no inbox with SMTP configured to send the mailto unsubscribe"
            else:
                sent_ok = await send_message(
                    account=account,
                    to=target,
                    subject="unsubscribe",
                    body_text="",
                )
                detail = "mailto sent" if sent_ok else "mailto send failed"
        except Exception as e:
            sent_ok = False
            detail = f"mailto failed: {e}"

    result = "sent" if sent_ok else "failed"
    log_attempt(sender_email=sender_email, target=target,
                target_type=target_type, result=result, result_detail=detail)
    delete_pending(token)

    # Always add to local blocklist as a safety net, even on success.
    try:
        from services.unsubscribe_suggestions import add_to_blocklist
        add_to_blocklist(sender_email=sender_email,
                         reason=f"List-Unsubscribe action: {result}")
    except Exception:
        pass

    return {
        "ok": sent_ok,
        "result": result,
        "detail": detail,
        "sender_email": sender_email,
        "target": target,
        "target_type": target_type,
    }


def get_recent_log(*, limit: int = 30) -> List[Dict[str, Any]]:
    try:
        from storage.database import get_db
        rows = get_db().get_connection().execute(
            "SELECT * FROM unsubscribe_log ORDER BY ts DESC LIMIT ?",
            (int(limit),),
        ).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []
