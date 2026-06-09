"""Correspondent agent — Phase 6 of v2-information-cortex.

IMAP-based passive-input channel. Reads an existing inbox via app password,
classifies each new message (newsletter / personal / transactional),
auto-routes newsletters to the best-fit notebook (capability L), and
queues low-confidence routings for user approval.

Design notes:
- IMAP via `imap-tools` (sync), run inside `asyncio.to_thread()`.
- Per-account state (last_uid, last_polled_at) persists in
  `credential_locker` so the poller is resumable across restarts.
- Approval queue persists to a single JSON file in the data dir.
- The agent runs ONE background task; on startup it loads enabled
  accounts and polls each on its own cadence.
- Personal mail is moved to `LocalBook/Personal` and **never** ingested.
- Transactional mail is left in INBOX as read.
- IDLE support is deferred to Phase 7.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from uuid import uuid4

logger = logging.getLogger(__name__)

# Poll interval: 8 hours (2026-06-08). Newsletters arrive irregularly; the
# 5-minute default was wasteful and hammered the user's inbox. 3 syncs a day
# is plenty for the use case.
DEFAULT_POLL_INTERVAL_MINUTES = 8 * 60
PERSONAL_FOLDER = "LocalBook/Personal"


def _data_dir() -> Path:
    base = Path(os.path.expanduser("~/Library/Application Support/LocalBook"))
    p = base / "correspondent"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _queue_file() -> Path:
    return _data_dir() / "approval_queue.json"


def _status_file() -> Path:
    return _data_dir() / "status.json"


def _subscription_queue_file() -> Path:
    return _data_dir() / "subscription_queue.json"


# ---------------------------------------------------------------------------
# Persistent approval queue (low-confidence routes waiting on the user)
# ---------------------------------------------------------------------------


def _load_queue() -> List[Dict[str, Any]]:
    p = _queue_file()
    if not p.exists():
        return []
    try:
        return json.loads(p.read_text()) or []
    except Exception:
        return []


def _save_queue(items: List[Dict[str, Any]]) -> None:
    _queue_file().write_text(json.dumps(items, indent=2))


def _append_queue_item(queue_item: Dict[str, Any]) -> bool:
    """H2 (2026-06-08) — append to the approval queue, deduping on
    `message_id`. Belt-and-suspenders dedup for the queue surface — F4
    catches dups at INGEST, but items can pile up in the queue (waiting
    for user approval) and concurrent polls would otherwise double-queue
    them. Returns True if appended, False if a same-Message-ID item was
    already pending."""
    items = _load_queue()
    mid = (queue_item.get("message_id") or "").strip()
    if mid:
        for existing in items:
            if (existing.get("message_id") or "").strip() == mid:
                logger.info(
                    f"[correspondent] queue dedup hit by message_id "
                    f"({mid[:40]}) — skipping duplicate insert"
                )
                return False
    items.append(queue_item)
    _save_queue(items)
    return True


def _load_subscriptions() -> List[Dict[str, Any]]:
    p = _subscription_queue_file()
    if not p.exists():
        return []
    try:
        return json.loads(p.read_text()) or []
    except Exception:
        return []


def _save_subscriptions(items: List[Dict[str, Any]]) -> None:
    _subscription_queue_file().write_text(json.dumps(items, indent=2))


def _load_status() -> Dict[str, Any]:
    p = _status_file()
    if not p.exists():
        return {"accounts": {}, "last_error": None}
    try:
        return json.loads(p.read_text()) or {"accounts": {}}
    except Exception:
        return {"accounts": {}}


def _save_status(status: Dict[str, Any]) -> None:
    _status_file().write_text(json.dumps(status, indent=2))


# ---------------------------------------------------------------------------
# F5b (2026-06-08) — sender→notebook routing learning. Persisted to JSON so
# manual corrections feed back into the auto-router on subsequent syncs.
# Schema: {normalized_sender: {notebook_id: correction_count}}
# Normalization: lowercase, take only the angle-bracketed email if present
# ("Alice <alice@news.io>" → "alice@news.io").
# ---------------------------------------------------------------------------


def _sender_routing_file() -> Path:
    return _data_dir() / "sender_routing.json"


def _normalize_sender(sender: str) -> str:
    s = (sender or "").strip()
    if not s:
        return ""
    if "<" in s and ">" in s:
        s = s[s.index("<") + 1 : s.index(">")]
    return s.lower()


def _load_sender_routing() -> Dict[str, Dict[str, int]]:
    p = _sender_routing_file()
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text()) or {}
    except Exception:
        return {}


def _save_sender_routing(routing: Dict[str, Dict[str, int]]) -> None:
    _sender_routing_file().write_text(json.dumps(routing, indent=2))


def _record_sender_routing(sender: str, notebook_id: str) -> None:
    """Increment the (sender, notebook) correction count. Called from
    approve_queued so the user's choice biases future auto-routing."""
    norm = _normalize_sender(sender)
    if not norm or not notebook_id:
        return
    routing = _load_sender_routing()
    bucket = routing.setdefault(norm, {})
    bucket[notebook_id] = int(bucket.get(notebook_id, 0)) + 1
    _save_sender_routing(routing)


def get_sender_routing_bias(sender: str, notebook_id: str) -> float:
    """Public helper — returns the similarity bonus to apply to
    `notebook_id` for `sender`. +0.25 per prior correction, capped at
    +0.50 so two corrections lock the sender to the chosen notebook.
    Imported by services/notebook_router."""
    norm = _normalize_sender(sender)
    if not norm:
        return 0.0
    routing = _load_sender_routing()
    count = int((routing.get(norm) or {}).get(notebook_id, 0))
    return min(0.50, 0.25 * count)


# ---------------------------------------------------------------------------
# IMAP fetch — sync (run inside asyncio.to_thread)
# ---------------------------------------------------------------------------


def _imap_fetch_new(
    *,
    host: str,
    port: int,
    user: str,
    password: str,
    use_ssl: bool,
    last_uid: int,
    personal_folder: str,
    limit: int = 50,
) -> Dict[str, Any]:
    """Connect, fetch new messages (UID > last_uid), close. Returns a dict
    with `messages` (list of dicts with uid, raw_bytes, flags) and the new
    `max_uid` seen. Personal-folder creation is best-effort."""
    from imap_tools import MailBox, AND
    from imap_tools.errors import MailboxFolderCreateError, MailboxFolderSelectError

    out: Dict[str, Any] = {"messages": [], "max_uid": last_uid}
    if not host or not user:
        return out

    box = MailBox(host).login(user, password, initial_folder="INBOX")
    try:
        # Best-effort: ensure the personal folder exists. Many providers
        # require nested syntax; mail box.folder.create is forgiving on
        # AlreadyExists.
        try:
            existing = {f.name for f in box.folder.list()}
            if personal_folder not in existing:
                box.folder.create(personal_folder)
        except MailboxFolderCreateError:
            pass
        except Exception as _e:
            logger.debug(f"[correspondent.imap] folder bootstrap skipped: {_e}")

        # Fetch UNSEEN newer than last_uid. Bounded to `limit` to avoid
        # bursts on first sync of a giant inbox.
        criteria = AND(seen=False)
        max_uid = last_uid
        count = 0
        for msg in box.fetch(criteria, mark_seen=False, bulk=True):
            try:
                uid = int(msg.uid or 0)
            except Exception:
                uid = 0
            if uid and uid <= last_uid:
                continue
            raw = msg.obj.as_bytes() if hasattr(msg, "obj") else b""
            out["messages"].append({
                "uid": uid,
                "raw_bytes": raw,
                "subject": msg.subject or "",
                "from": msg.from_ or "",
            })
            if uid > max_uid:
                max_uid = uid
            count += 1
            if count >= limit:
                break
        out["max_uid"] = max_uid
    finally:
        try:
            box.logout()
        except Exception:
            pass
    return out


def _imap_move(
    *, host: str, port: int, user: str, password: str, use_ssl: bool,
    uid: int, dest_folder: str,
) -> bool:
    from imap_tools import MailBox
    try:
        with MailBox(host).login(user, password, initial_folder="INBOX") as box:
            box.move([str(uid)], dest_folder)
        return True
    except Exception as e:
        logger.debug(f"[correspondent.imap] move failed: {e}")
        return False


def _imap_mark_seen(
    *, host: str, port: int, user: str, password: str, use_ssl: bool, uid: int,
) -> bool:
    from imap_tools import MailBox
    try:
        with MailBox(host).login(user, password, initial_folder="INBOX") as box:
            box.flag([str(uid)], "\\Seen", True)
        return True
    except Exception as e:
        logger.debug(f"[correspondent.imap] mark-seen failed: {e}")
        return False


def _imap_delete(
    *, host: str, port: int, user: str, password: str, use_ssl: bool, uid: int,
) -> bool:
    """Delete a single message by UID. Called after successful ingest so
    the next sync doesn't re-process the same message.

    Gmail-aware (2026-06-08, G3): plain IMAP delete on Gmail removes the
    "Inbox" label but the message remains in "All Mail" — and depending
    on the user's view, it may still appear in their inbox-like surfaces.
    Moving explicitly to [Gmail]/Trash matches user expectation that
    "deleted = gone." iCloud has an analogous "Deleted Messages" folder.
    Other providers fall through to the standard delete+expunge.

    Best-effort: returns False on any failure but never raises — the
    source has already been ingested either way."""
    from imap_tools import MailBox
    host_lower = (host or "").lower()
    # Provider-specific trash folder. None → fall through to plain delete.
    trash_folder: Optional[str] = None
    if "gmail" in host_lower or "googlemail" in host_lower:
        trash_folder = "[Gmail]/Trash"
    elif "me.com" in host_lower or "mac.com" in host_lower or "icloud" in host_lower:
        trash_folder = "Deleted Messages"

    try:
        with MailBox(host).login(user, password, initial_folder="INBOX") as box:
            if trash_folder:
                # Try the trash-move first. If the folder name is wrong for
                # this user's locale (e.g. localized Gmail folders), fall
                # back to plain delete so we still get the email out of
                # the inbox view.
                try:
                    box.move([str(uid)], trash_folder)
                    return True
                except Exception as move_err:
                    logger.debug(
                        f"[correspondent.imap] move to {trash_folder} failed for "
                        f"uid={uid} ({move_err}); falling back to delete"
                    )
            box.delete([str(uid)])
        return True
    except Exception as e:
        logger.debug(f"[correspondent.imap] delete failed for uid={uid}: {e}")
        return False


# ---------------------------------------------------------------------------
# Agent
# ---------------------------------------------------------------------------


class CorrespondentAgent:
    """Singleton coordinator. Polls each enabled IMAP account on its cadence,
    classifies messages, routes newsletters, queues low-confidence cases."""

    def __init__(self) -> None:
        self._task: Optional[asyncio.Task] = None
        self._running = False
        # H1 (2026-06-08) — per-account asyncio.Lock so the auto-poller
        # and the manual /sync endpoint can't both fetch the same UIDs
        # at the same time. Without this, concurrent polls each saw
        # `uid > last_uid` against the SAME stale last_uid, ingested
        # the same emails twice, and queued duplicate cards for the user.
        self._account_locks: Dict[str, asyncio.Lock] = {}
        self.poll_interval_seconds = DEFAULT_POLL_INTERVAL_MINUTES * 60
        self.personal_folder = PERSONAL_FOLDER

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._loop(), name="correspondent-poller")
        logger.info("[correspondent] poller started")

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        logger.info("[correspondent] poller stopped")

    async def _loop(self) -> None:
        while self._running:
            try:
                await self.poll_all()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning(f"[correspondent] poll cycle failed: {e}")
            try:
                await asyncio.sleep(self.poll_interval_seconds)
            except asyncio.CancelledError:
                break

    # ------------------------------------------------------------------
    # Polling
    # ------------------------------------------------------------------

    async def poll_all(self) -> Dict[str, Any]:
        """Poll every enabled account once. Returns a summary."""
        from services.credential_locker import list_imap_accounts, get_imap_account
        summary: Dict[str, Any] = {"accounts": {}, "totals": {"ingested": 0, "queued": 0, "personal": 0, "transactional": 0}}
        accounts = await list_imap_accounts()
        for acc in accounts:
            if not acc.enabled:
                continue
            full = await get_imap_account(acc.email)
            if not full:
                continue
            result = await self.poll_account(full)
            summary["accounts"][acc.email] = result
            for k in ("ingested", "queued", "personal", "transactional"):
                summary["totals"][k] += result.get(k, 0)
        return summary

    async def poll_account(self, account: Dict[str, Any]) -> Dict[str, Any]:
        """Poll one account. Returns counts."""
        from services.credential_locker import update_imap_state
        email = account["email"]
        result = {"ingested": 0, "queued": 0, "personal": 0, "transactional": 0, "forwards": 0, "errors": 0}

        # H1 — serialize concurrent polls for the same account. If the
        # lock is already held, we exit immediately rather than queueing
        # — duplicate /sync clicks shouldn't pile up syncs in the
        # background; the user expects "click sync" → "syncs now or no-op."
        lock = self._account_locks.setdefault(email, asyncio.Lock())
        if lock.locked():
            logger.info(f"[correspondent.poll_account] {email}: already syncing — skip")
            return {**result, "skipped": True}

        async with lock:
            return await self._poll_account_impl(account, email, result, update_imap_state)

    async def _poll_account_impl(
        self,
        account: Dict[str, Any],
        email: str,
        result: Dict[str, Any],
        update_imap_state,
    ) -> Dict[str, Any]:
        try:
            fetch = await asyncio.to_thread(
                _imap_fetch_new,
                host=account["imap_host"],
                port=account["imap_port"],
                user=account["imap_user"],
                password=account["imap_password"],
                use_ssl=account.get("use_ssl", True),
                last_uid=int(account.get("last_uid", 0) or 0),
                personal_folder=self.personal_folder,
            )
        except Exception as e:
            logger.warning(f"[correspondent.poll_account] {email}: IMAP fetch failed: {e}")
            result["errors"] += 1
            status = _load_status()
            status.setdefault("accounts", {})[email] = {
                "last_error": str(e),
                "last_polled_at": datetime.utcnow().isoformat(),
            }
            _save_status(status)
            return result

        messages = fetch.get("messages") or []
        new_max_uid = int(fetch.get("max_uid") or account.get("last_uid", 0))

        for msg in messages:
            try:
                await self._handle_message(email=email, account=account, msg=msg, counts=result)
            except Exception as e:
                logger.warning(f"[correspondent.poll_account] message handler error: {e}")
                result["errors"] += 1

        # Persist progress regardless of partial failures.
        await update_imap_state(
            email=email,
            last_uid=new_max_uid,
            last_polled_at=datetime.utcnow().isoformat(),
        )
        status = _load_status()
        status.setdefault("accounts", {})[email] = {
            "last_polled_at": datetime.utcnow().isoformat(),
            "last_uid": new_max_uid,
            "last_error": None,
            "last_result": result,
        }
        _save_status(status)
        return result

    async def _handle_message(self, *, email: str, account: Dict[str, Any], msg: Dict[str, Any], counts: Dict[str, int]) -> None:
        from services.correspondent_processor import (
            parse_email, classify_email, ingest_newsletter,
            is_forward_candidate, extract_forwarded_content, resolve_forward_notebook,
            ingest_forward,
        )
        from services.notebook_router import route as route_email

        raw = msg.get("raw_bytes") or b""
        parsed = parse_email(raw)
        if not parsed:
            return

        # Dedupe by Message-ID. The source store does not have a global
        # lookup, but a recently-ingested message should be visible across
        # notebooks. Approximation: check the queue + a small in-memory
        # cache. Cheap and good-enough for v1.
        if _seen_message_id(parsed.message_id):
            counts["transactional"] += 1  # treat as already-handled
            return

        # Phase 8 — forwards bypass the LLM classifier. The heuristic is
        # cheap and forwards are explicit user intent: we'd rather ingest
        # the user's forward as a user-supplied source than spend an LLM
        # call to classify the obvious. Confirmation reply is fire-and-
        # forget per account preference.
        if is_forward_candidate(parsed):
            payload = extract_forwarded_content(parsed)
            decision = await resolve_forward_notebook(parsed, payload)
            target_nb = decision.get("notebook_id")

            # Fallback to the account's default forward notebook if the
            # router couldn't decide and the user has configured one.
            if not target_nb and decision.get("decision") != "route":
                target_nb = account.get("default_forward_notebook_id")

            if target_nb:
                source_id = await ingest_forward(target_nb, parsed, payload)
                if source_id:
                    counts["forwards"] += 1
                    asyncio.create_task(self._send_forward_confirmation(
                        account=account, parsed=parsed, payload=payload,
                        notebook_id=target_nb, source_filename=payload.original_subject or "forwarded email",
                    ))
                    # F3 (2026-06-08) — delete from IMAP after successful
                    # ingest so the next sync doesn't re-process. Fire-and-
                    # forget on a worker thread; never blocks the loop.
                    asyncio.create_task(asyncio.to_thread(
                        _imap_delete,
                        host=account["imap_host"], port=account["imap_port"],
                        user=account["imap_user"], password=account["imap_password"],
                        use_ssl=account.get("use_ssl", True),
                        uid=msg["uid"],
                    ))
                else:
                    counts["errors"] += 1
            else:
                # Queue for user notebook pick. Reuses the routing queue
                # with kind='forward' so the UI can render distinctly.
                queue_item = {
                    "item_id": str(uuid4()),
                    "kind": "forward",
                    "email_account": email,
                    "message_uid": msg["uid"],
                    "message_id": parsed.message_id,
                    "sender": payload.original_sender or parsed.sender,
                    "subject": payload.original_subject or parsed.subject,
                    "summary": (payload.original_body or "")[:300],
                    "topic_tags": [],
                    "top_candidate": (
                        {"notebook_id": decision["notebook_id"], "notebook_name": decision["notebook_name"],
                         "confidence": decision["confidence"]}
                        if decision.get("notebook_id") else None
                    ),
                    "alternatives": decision.get("alternatives") or [],
                    "decision_reason": decision.get("reason") or "",
                    "raw_bytes_b64": _b64(msg.get("raw_bytes") or b""),
                    "created_at": datetime.utcnow().isoformat(),
                }
                if _append_queue_item(queue_item):
                    counts["queued"] += 1

            _remember_message_id(parsed.message_id)
            return

        classification = await classify_email(parsed)
        if classification.kind == "personal":
            ok = await asyncio.to_thread(
                _imap_move,
                host=account["imap_host"], port=account["imap_port"],
                user=account["imap_user"], password=account["imap_password"],
                use_ssl=account.get("use_ssl", True),
                uid=msg["uid"], dest_folder=self.personal_folder,
            )
            counts["personal"] += 1
            if not ok:
                logger.debug(f"[correspondent] personal move failed for uid={msg['uid']}")
            _remember_message_id(parsed.message_id)
            return

        if classification.kind == "transactional":
            await asyncio.to_thread(
                _imap_mark_seen,
                host=account["imap_host"], port=account["imap_port"],
                user=account["imap_user"], password=account["imap_password"],
                use_ssl=account.get("use_ssl", True),
                uid=msg["uid"],
            )
            counts["transactional"] += 1
            _remember_message_id(parsed.message_id)
            return

        # newsletter
        decision = await route_email(
            classification_summary=classification.summary,
            topic_tags=classification.topic_tags,
            sender=parsed.sender,
        )

        if decision.decision == "route" and decision.top:
            source_id = await ingest_newsletter(decision.top.notebook_id, parsed, classification)
            if source_id:
                counts["ingested"] += 1
                # F3 (2026-06-08) — delete from IMAP after successful
                # ingest. Same fire-and-forget pattern as the forward path.
                asyncio.create_task(asyncio.to_thread(
                    _imap_delete,
                    host=account["imap_host"], port=account["imap_port"],
                    user=account["imap_user"], password=account["imap_password"],
                    use_ssl=account.get("use_ssl", True),
                    uid=msg["uid"],
                ))
            else:
                counts["errors"] += 1
        else:
            # Either explicit 'queue' or 'no_match' — both go to the queue
            # so the user can pick a notebook (or dismiss).
            queue_item = {
                "item_id": str(uuid4()),
                "email_account": email,
                "message_uid": msg["uid"],
                "message_id": parsed.message_id,
                "sender": parsed.sender,
                "subject": parsed.subject,
                "summary": classification.summary,
                "topic_tags": classification.topic_tags,
                "top_candidate": _candidate_to_dict(decision.top) if decision.top else None,
                "alternatives": [_candidate_to_dict(c) for c in (decision.alternatives or [])],
                "decision_reason": decision.reason,
                "raw_bytes_b64": _b64(msg.get("raw_bytes") or b""),
                "created_at": datetime.utcnow().isoformat(),
            }
            if _append_queue_item(queue_item):
                counts["queued"] += 1

        _remember_message_id(parsed.message_id)

    # ------------------------------------------------------------------
    # Queue actions (used by the REST endpoints)
    # ------------------------------------------------------------------

    async def approve_queued(self, item_id: str, notebook_id: Optional[str] = None) -> Dict[str, Any]:
        from services.correspondent_processor import (
            parse_email, classify_email, ingest_newsletter,
            extract_forwarded_content, ingest_forward,
        )
        items = _load_queue()
        item = next((i for i in items if i["item_id"] == item_id), None)
        if not item:
            return {"ok": False, "reason": "not found"}
        raw = _b64d(item.get("raw_bytes_b64") or "")
        parsed = parse_email(raw)
        target_nb = notebook_id or (item.get("top_candidate") or {}).get("notebook_id")
        if not target_nb:
            return {"ok": False, "reason": "no target notebook"}

        # Forward items route through ingest_forward; newsletter items
        # take the existing classify+ingest path. Both kinds share the
        # same queue file.
        if item.get("kind") == "forward":
            payload = extract_forwarded_content(parsed)
            source_id = await ingest_forward(target_nb, parsed, payload)
        else:
            classification = await classify_email(parsed)
            source_id = await ingest_newsletter(target_nb, parsed, classification)

        if not source_id:
            return {"ok": False, "reason": "ingest failed"}
        items = [i for i in items if i["item_id"] != item_id]
        _save_queue(items)

        # F5b (2026-06-08) — record this routing as a learning signal.
        # The notebook the user picked at approval time wins future
        # routing for this sender. _record_sender_routing is a no-op
        # if sender is empty.
        try:
            _record_sender_routing(
                sender=item.get("sender") or "",
                notebook_id=target_nb,
            )
        except Exception as _e:
            logger.debug(f"[correspondent.approve_queued] sender routing record skipped: {_e}")

        # F3 + G4 (2026-06-08) — delete from IMAP after successful approval.
        # Awaited (not fire-and-forget) so we can return delete success to
        # the UI. Approval got slightly slower but the user gets honest
        # feedback that the email left both queue AND inbox.
        imap_deleted: Optional[bool] = None
        try:
            from services.credential_locker import get_imap_account
            account = await get_imap_account(item.get("email_account") or "")
            if account and item.get("message_uid"):
                imap_deleted = await asyncio.to_thread(
                    _imap_delete,
                    host=account["imap_host"], port=account["imap_port"],
                    user=account["imap_user"], password=account["imap_password"],
                    use_ssl=account.get("use_ssl", True),
                    uid=int(item["message_uid"]),
                )
        except Exception as _e:
            logger.debug(f"[correspondent.approve_queued] IMAP delete skipped: {_e}")
            imap_deleted = False

        return {
            "ok": True,
            "source_id": source_id,
            "notebook_id": target_nb,
            "imap_deleted": imap_deleted,
        }

    async def _send_forward_confirmation(
        self,
        *,
        account: Dict[str, Any],
        parsed: "Any",
        payload: "Any",
        notebook_id: str,
        source_filename: str,
    ) -> None:
        """Fire-and-forget confirmation reply to whoever forwarded the email.

        No-ops silently when:
          - the account has `send_confirmations` disabled
          - the account has no SMTP host configured
          - SMTP send fails (logged at warning)
        """
        if not account.get("send_confirmations", True):
            return
        if not account.get("smtp_host"):
            return
        try:
            from services.correspondent_smtp import send_message
            from storage.notebook_store import notebook_store

            notebook = await notebook_store.get(notebook_id)
            notebook_name = (notebook or {}).get("title", notebook_id)
            original_subject = getattr(payload, "original_subject", "") or "your forwarded message"
            forwarder = getattr(payload, "forwarded_by", "") or getattr(parsed, "sender", "")
            if not forwarder:
                return
            subject = f"Re: {original_subject}"[:200]
            body = (
                f"Got it — ingested into {notebook_name} as source \"{source_filename}\".\n\n"
                f"— LocalBook Correspondent"
            )
            await send_message(
                account,
                to=forwarder,
                subject=subject,
                body_text=body,
                reply_to_message_id=getattr(parsed, "message_id", None),
            )
        except Exception as e:
            logger.debug(f"[correspondent.send_forward_confirmation] skipped: {e}")

    async def dismiss_queued(self, item_id: str) -> Dict[str, Any]:
        items = _load_queue()
        before = len(items)
        items = [i for i in items if i["item_id"] != item_id]
        if len(items) == before:
            return {"ok": False, "reason": "not found"}
        _save_queue(items)
        return {"ok": True}

    def list_queue(self) -> List[Dict[str, Any]]:
        items = _load_queue()
        # Strip raw_bytes_b64 from the listing — too large for the UI.
        # Phase 14.G1 (2026-06-08) — also surface the sender_corrections
        # count so the UI can show "after 1 more approval this sender will
        # auto-route." Cheap: one JSON read shared across all items.
        routing = _load_sender_routing()
        out: List[Dict[str, Any]] = []
        for i in items:
            slim = {k: v for k, v in i.items() if k != "raw_bytes_b64"}
            sender_norm = _normalize_sender(i.get("sender") or "")
            sender_bucket = routing.get(sender_norm) or {}
            slim["sender_corrections"] = (
                int(sender_bucket.get(i.get("top_candidate", {}).get("notebook_id") or "", 0))
                if i.get("top_candidate") else 0
            )
            slim["sender_correction_total"] = sum(int(v) for v in sender_bucket.values())
            out.append(slim)
        return out

    def status(self) -> Dict[str, Any]:
        return _load_status()

    # ------------------------------------------------------------------
    # Phase 7 — subscription proposals (sister-newsletter auto-subscribe)
    # ------------------------------------------------------------------

    async def propose_subscriptions(
        self,
        *,
        notebook_id: str,
        source_email: Dict[str, Any],
        candidates: List[Dict[str, Any]],
    ) -> int:
        """Add classified+resolved subscription candidates to the queue.

        Dedupes against:
          - the existing subscription queue (same feed_url)
          - the notebook's Collector rss_feeds config

        Returns the number of new items actually persisted.
        """
        if not candidates:
            return 0
        try:
            from agents.collector import get_collector
            collector = get_collector(notebook_id)
            existing_feeds = set((collector.config.sources or {}).get("rss_feeds", []) or [])
        except Exception:
            existing_feeds = set()

        items = _load_subscriptions()
        queued_feeds = {i.get("feed_url") for i in items if i.get("status") == "pending"}

        added = 0
        for c in candidates:
            feed = c.get("feed_url")
            if not feed or feed in existing_feeds or feed in queued_feeds:
                continue
            items.append({
                "id": str(uuid4()),
                "kind": "subscription",
                "status": "pending",
                "title": c.get("title") or c.get("channel_name") or feed,
                "url": c.get("url"),
                "feed_url": feed,
                "source_type": c.get("source_type") or "rss_feed",
                "channel_name": c.get("channel_name"),
                "default_schedule": c.get("default_schedule") or "weekly",
                "kind_label": c.get("kind") or "newsletter",
                "suggested_notebook_id": notebook_id,
                "source_email": {
                    "message_id": source_email.get("message_id"),
                    "subject": source_email.get("subject"),
                    "sender": source_email.get("sender"),
                },
                "created_at": datetime.utcnow().isoformat(),
            })
            queued_feeds.add(feed)
            added += 1

        if added:
            _save_subscriptions(items)
            try:
                from services.curator_event_bus import event_bus
                event_bus.emit_now(
                    actor="@correspondent",
                    action="subscription_proposed",
                    notebook_id=notebook_id,
                    payload={"count": added, "source_message_id": source_email.get("message_id")},
                    outcome="success",
                )
            except Exception:
                pass
        return added

    def list_subscription_queue(self) -> List[Dict[str, Any]]:
        return [i for i in _load_subscriptions() if i.get("status") == "pending"]

    async def approve_subscription(
        self,
        item_id: str,
        notebook_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Accept a proposal. Two paths:

        - `kind='subscription'` (Phase 7) — add the feed_url to the
          notebook's Collector rss_feeds.
        - `kind='entity'` (Phase 13) — create a small `format='entity-watch'`
          source in the notebook. v1 stops there; web-fetch on approval can
          come in a polish pass.
        """
        items = _load_subscriptions()
        item = next((i for i in items if i.get("id") == item_id), None)
        if not item:
            return {"ok": False, "reason": "not found"}
        target_nb = notebook_id or item.get("suggested_notebook_id")
        if not target_nb:
            return {"ok": False, "reason": "no target notebook"}

        kind = item.get("kind") or "subscription"

        if kind == "entity":
            # Phase 13 source-graph expansion: stash the entity as a tiny
            # source. User can later research it or attach content.
            try:
                from storage.source_store import source_store
                entity_name = item.get("entity_name") or item.get("title") or "Entity"
                entity_type = item.get("entity_type") or "person"
                context = ((item.get("source_email") or {}).get("summary") or "")[:600]
                src = await source_store.create(
                    notebook_id=target_nb,
                    filename=f"{entity_name} (watch)",
                    metadata={
                        "type": "entity-watch",
                        "format": "entity-watch",
                        "collected_by": "correspondent_entity",
                        "entity_name": entity_name,
                        "entity_type": entity_type,
                        "status": "completed",
                        "chunks": 0,
                        "characters": len(context),
                        "content": context,
                    },
                )
                items = [i for i in items if i.get("id") != item_id]
                _save_subscriptions(items)
                try:
                    from services.curator_event_bus import event_bus
                    event_bus.emit_now(
                        actor="@correspondent",
                        action="entity_subscription_approved",
                        notebook_id=target_nb,
                        payload={"entity_name": entity_name, "source_id": src.get("id")},
                        outcome="success",
                    )
                except Exception:
                    pass
                return {"ok": True, "entity_name": entity_name, "source_id": src.get("id"), "notebook_id": target_nb}
            except Exception as e:
                logger.warning(f"[correspondent.approve_subscription] entity create failed: {e}")
                return {"ok": False, "reason": f"entity source create failed: {e}"}

        # Default: feed_url subscription path.
        feed_url = item.get("feed_url")
        if not feed_url:
            return {"ok": False, "reason": "no feed url"}
        try:
            from agents.collector import get_collector
            collector = get_collector(target_nb)
            sources = dict(collector.config.sources or {})
            current = list(sources.get("rss_feeds") or [])
            if feed_url not in current:
                current.append(feed_url)
            sources["rss_feeds"] = current
            collector.update_config({"sources": sources})
        except Exception as e:
            logger.warning(f"[correspondent.approve_subscription] collector update failed: {e}")
            return {"ok": False, "reason": f"collector update failed: {e}"}
        # Drop the item from the queue.
        items = [i for i in items if i.get("id") != item_id]
        _save_subscriptions(items)
        try:
            from services.curator_event_bus import event_bus
            event_bus.emit_now(
                actor="@correspondent",
                action="subscription_approved",
                notebook_id=target_nb,
                payload={"feed_url": feed_url, "title": item.get("title")},
                outcome="success",
            )
        except Exception:
            pass
        return {"ok": True, "feed_url": feed_url, "notebook_id": target_nb}

    async def dismiss_subscription(self, item_id: str) -> Dict[str, Any]:
        items = _load_subscriptions()
        before = len(items)
        items = [i for i in items if i.get("id") != item_id]
        if len(items) == before:
            return {"ok": False, "reason": "not found"}
        _save_subscriptions(items)
        return {"ok": True}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _candidate_to_dict(c) -> Dict[str, Any]:
    if c is None:
        return {}
    return {"notebook_id": c.notebook_id, "notebook_name": c.notebook_name, "confidence": c.confidence}


def _b64(b: bytes) -> str:
    import base64
    return base64.b64encode(b).decode("ascii") if b else ""


def _b64d(s: str) -> bytes:
    import base64
    if not s:
        return b""
    try:
        return base64.b64decode(s)
    except Exception:
        return b""


# Cheap in-process dedupe cache. Resets across restarts; the persistent
# de-dupe is implicit (last_uid moves forward). Bounded to avoid memory
# growth on long runs.
_SEEN_IDS: List[str] = []
_SEEN_MAX = 500


def _seen_message_id(mid: str) -> bool:
    return bool(mid) and mid in _SEEN_IDS


def _remember_message_id(mid: str) -> None:
    if not mid:
        return
    _SEEN_IDS.append(mid)
    if len(_SEEN_IDS) > _SEEN_MAX:
        del _SEEN_IDS[: len(_SEEN_IDS) - _SEEN_MAX]


# Singleton
correspondent_agent = CorrespondentAgent()
