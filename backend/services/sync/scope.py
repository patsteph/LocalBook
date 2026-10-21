"""Notebooks kept on THIS Mac: they never sync with any other device.

One list, per Mac, never synced (it lives in sync.db). It applies to everything that could carry a notebook's
content off this Mac — other Macs (the sync engine's export and apply) and phones (the library endpoints). The list
used to be phones-only ("Keep off phones"); the key keeps its old name so existing choices carry over.

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

KEY = "phone_excluded_notebooks"          # historical name; now covers every device

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


def set_local(notebook_ids: Iterable[str]) -> Dict[str, List[str]]:
    """Store the list. Returns {"now": [...], "released": [...]} — `released` were local before and are not now."""
    from services.sync import store
    before = set(local_notebooks())
    now = sorted({str(i) for i in notebook_ids})
    store.put(KEY, now)
    released = sorted(before - set(now))
    if released:
        _note_release(released)
    return {"now": now, "released": released}


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
