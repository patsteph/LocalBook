"""Notebooks kept on THIS Mac: they never sync with any other device.

One list, per Mac, never synced (it lives in sync.db). It applies to everything that could carry a notebook's
content off this Mac — other Macs (the sync engine's export and apply) and phones (the library endpoints). Notebooks
that were only "kept off phones" before this list existed keep exactly that meaning (see LEGACY_PHONE_KEY).

How it is enforced
  * export  a row belonging to a local notebook is not sent. The page says how far it looked ("seen"), so the peer
            does not ask for the same rows again.
  * apply   an incoming row belonging to a local notebook is dropped, with the same watermark.
  * release taking a notebook OFF the list makes this Mac's copy ship again and asks the peers for everything
            once more (their vv entries are forgotten), so both directions catch up.

What it cannot do: take back what another device already has. A notebook that was synced before it was marked local
stays on those devices.
"""

from __future__ import annotations

import json
from typing import Any, Callable, Dict, FrozenSet, Iterable, List, Optional, Set

KEY = "local_only_notebooks"              # kept on this device: syncs with no other device (Mac or phone)
LEGACY_PHONE_KEY = "phone_excluded_notebooks"   # the old "keep off phones" list: still honoured for phones ONLY,
                                                # so nothing that synced between Macs before changes behaviour

# Replica -> the notebook ids that must not leave / enter it. Production installs the sync.db-backed one
# (service.py); a bare engine (tests, tools) has none.
_provider: Callable[[Any], Iterable[str]] = lambda r: frozenset()


def install(provider: Callable[[Any], Iterable[str]]) -> None:
    global _provider
    _provider = provider


def blocked(r) -> FrozenSet[str]:
    return frozenset(_provider(r) or ())


# ── the stored list ──────────────────────────────────────────────────────────


def local_notebooks() -> List[str]:
    from services.sync import store
    return list(store.get(KEY, []) or [])


def legacy_phone_only() -> List[str]:
    from services.sync import store
    return list(store.get(LEGACY_PHONE_KEY, []) or [])


def hidden_from_phones() -> List[str]:
    """Everything phones must not read: the device-only list plus the older phones-only list."""
    return sorted(set(local_notebooks()) | set(legacy_phone_only()))


def set_local(notebook_ids: Iterable[str]) -> Dict[str, List[str]]:
    """Store the device-only list. Returns {"now": [...], "released": [...]} — `released` were device-only before and
    are not now. Older phones-only marks are left alone (they keep their meaning) except for notebooks that are
    now device-only, where the older mark is redundant."""
    from services.sync import store
    before = set(local_notebooks())
    now = sorted({str(i) for i in notebook_ids})
    store.put(KEY, now)
    store.put(LEGACY_PHONE_KEY, [i for i in legacy_phone_only() if i not in set(now)])
    released = sorted(before - set(now))
    if released:
        _note_release(released)
    return {"now": now, "released": released}


def clear_phones_only(notebook_ids: Iterable[str]) -> List[str]:
    """Let phones read these again (they keep syncing between Macs exactly as before)."""
    from services.sync import store
    drop = {str(i) for i in notebook_ids}
    keep = [i for i in legacy_phone_only() if i not in drop]
    store.put(LEGACY_PHONE_KEY, keep)
    return keep


# ── which notebook(s) a row belongs to ──────────────────────────────────────

# column that names the notebook, when it is not "notebook_id"
_COLUMN = {("recall", "archival_records"): "source_notebook_id", ("main", "routing_decisions"): "top_notebook_id"}
# documents: kind -> how its key names a notebook
_DOC_WHOLE_KEY = {"collector_config", "people_config", "approval_queue_imported", "quiz_cards_imported"}
_DOC_PREFIX = {"approval_item", "quiz_card", "quiz_review"}


def notebooks_of(db: str, tbl: str, pk: List[Any], pk_names: Iterable[str], value: Callable[[str], Any]) -> Set[str]:
    """Notebook ids a row belongs to. `value(name)` reads a column of the row (or of an incoming state)."""
    names = list(pk_names)
    if tbl == "notebooks":
        return {str(pk[0])} if pk else set()
    if tbl == "documents" and len(pk) >= 2:
        kind, key = str(pk[0]), str(pk[1])
        if kind in _DOC_WHOLE_KEY:
            return {key}
        if kind in _DOC_PREFIX:
            return {key.split("/", 1)[0]}
        if kind == "core_memory":                       # a memory learned in a device-only notebook's conversation
            raw = value("body_json")
            try:
                body = json.loads(raw) if isinstance(raw, str) else (raw or {})
            except Exception:
                body = {}
            return conversation_notebooks(body.get("source_conversation_id") if isinstance(body, dict) else None)
        return set()
    if tbl == "insights":
        raw = value("notebooks")
        try:
            ids = json.loads(raw) if isinstance(raw, str) else (raw or [])
        except Exception:
            ids = []
        return {str(i) for i in ids} if isinstance(ids, list) else set()
    col = _COLUMN.get((db, tbl), "notebook_id")
    got = value(col)
    if got in (None, "") and col in names:
        got = pk[names.index(col)] if len(pk) > names.index(col) else None
    return {str(got)} if got not in (None, "") else set()


# core-memory entries remember the conversation they came from, and a conversation belongs to a notebook (recall db).
_conversations: Callable[[str], Iterable[str]] = lambda conversation_id: ()


def install_conversations(lookup: Callable[[str], Iterable[str]]) -> None:
    global _conversations
    _conversations = lookup


def conversation_notebooks(conversation_id: Optional[str]) -> Set[str]:
    return {str(n) for n in (_conversations(conversation_id) or ())} if conversation_id else set()


def touches(block: FrozenSet[str], found: Set[str]) -> bool:
    return bool(block and found & block)


# ── taking a notebook off the list ───────────────────────────────────────────


def _note_release(ids: List[str]) -> None:
    """Remember to re-offer these notebooks; the sync loop does it on its next pass (it owns the connections)."""
    from services.sync import store
    pend = set(store.get("scope_released", []) or [])
    store.put("scope_released", sorted(pend | set(ids)))


def take_pending_release() -> List[str]:
    from services.sync import store
    pend = list(store.get("scope_released", []) or [])
    if pend:
        store.put("scope_released", [])
    return pend
