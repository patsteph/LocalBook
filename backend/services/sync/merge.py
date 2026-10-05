"""The merge (LB-12e): pure functions, no database.

Every synced field is a register holding

    {"v": value, "c": clock, "h": [clock, older clock, ...]}   (newest first)

`c` is the HLC of the write that produced `v`; `h` is that write's ancestry —
the clocks it replaced, capped at CHAIN. The whole row also carries a `__del__`
register, so a delete is just another write (a tombstone).

Merging one field, local L with remote R:

    same clock            → same write                       → keep
    L.c is in R.h         → R was written on top of L        → take R
    R.c is in L.h         → R is older than what we have     → keep L
    otherwise             → written concurrently:
        equal values      → no conflict; keep the higher clock, merge ancestry
        different values  → the higher clock wins on EVERY Mac, and for a
                            content field a conflict item keeps the other value

"Higher clock wins" is a total order (the device id breaks ties), and the merged
ancestry is the same set on both sides, so two Macs merging each other's
versions in either order reach the same register — which is what the
three-replica property harness checks.
"""

from __future__ import annotations

import base64
import hashlib
import json
from typing import Any, Dict, List, Optional, Tuple

CHAIN = 16
DEL = "__del__"


def encode(value: Any) -> Any:
    """SQLite value → JSON-safe (bytes become {"$b": base64})."""
    if isinstance(value, (bytes, bytearray, memoryview)):
        return {"$b": base64.b64encode(bytes(value)).decode("ascii")}
    return value


def decode(value: Any) -> Any:
    if isinstance(value, dict) and set(value) == {"$b"}:
        return base64.b64decode(value["$b"])
    return value


def vhash(value: Any) -> str:
    """Hash of an (encoded) value, type-aware: 1 and "1" and 1.0 differ."""
    enc = encode(value)
    tag = type(value).__name__ if not isinstance(value, (bytes, bytearray, memoryview)) else "bytes"
    raw = json.dumps([tag, enc], sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def _merged_history(a: List[str], b: List[str]) -> List[str]:
    return sorted(set(a) | set(b), reverse=True)[:CHAIN]


def merge_register(local: Optional[dict], remote: Optional[dict]) -> Tuple[Optional[dict], bool]:
    """(merged register, true_conflict). Commutative in its result."""
    if local is None:
        return remote, False
    if remote is None:
        return local, False
    lc, rc = local["c"], remote["c"]
    if lc == rc:
        return local, False
    if lc in remote.get("h", ()):
        return remote, False
    if rc in local.get("h", ()):
        return local, False
    winner = local if lc > rc else remote
    merged = {"v": winner["v"], "c": winner["c"],
              "h": _merged_history(local.get("h", [lc]), remote.get("h", [rc]))}
    if winner.get("nv"):
        merged["nv"] = True        # a deleted row's register: the clock, no value
    if local.get("nv") or remote.get("nv"):
        return merged, False       # one side has no value to compare
    conflict = vhash(decode(local["v"])) != vhash(decode(remote["v"]))
    return merged, conflict


def conflict_id(tbl: str, pk: str, field: str, c1: str, c2: str) -> str:
    """The same conflict gets the same id on every Mac that detects it."""
    lo, hi = sorted((c1, c2))
    return hashlib.sha256(f"{tbl}\x1f{pk}\x1f{field}\x1f{lo}\x1f{hi}".encode()).hexdigest()[:32]


def merge_row(tbl: str, pk: str, local: Dict[str, dict], remote: Dict[str, dict],
              content_fields=()) -> Tuple[Dict[str, dict], List[dict]]:
    """Merge two row states field by field. Returns (merged, conflicts).

    A conflict is recorded for a content field with two different concurrent
    values, and for a delete racing an edit (`__del__`): the loser's value is
    kept in the item so nothing is silently lost.
    """
    merged: Dict[str, dict] = {}
    conflicts: List[dict] = []
    for name in sorted(set(local) | set(remote)):
        l, r = local.get(name), remote.get(name)
        m, conflict = merge_register(l, r)
        merged[name] = m
        if conflict and (name in content_fields or name == DEL):
            loser = r if m["c"] == l["c"] else l
            conflicts.append({
                "id": conflict_id(tbl, pk, name, l["c"], r["c"]),
                "tbl": tbl, "pk": pk, "field": name,
                "kept_value": m["v"], "other_value": loser["v"],
                "kept_clock": m["c"], "other_clock": loser["c"],
            })
    # A delete racing an edit: the delete wins (deterministically), but the edit
    # is not lost — both Macs raise the same item holding the edited value. The
    # deleting side's registers carry no values (the row is gone there), so the
    # item always takes the value from the side that still has it.
    if is_deleted(merged):
        del_side, other = (local, remote) if (local.get(DEL) or {}).get("c") == merged[DEL]["c"] \
            else (remote, local)
        for name in content_fields:
            d, o = del_side.get(name), other.get(name)
            if not o or o.get("nv") or (d and d["c"] == o["c"]):
                continue
            m, _ = merge_register(d, o)
            if m["c"] == o["c"]:
                conflicts.append({
                    "id": conflict_id(tbl, pk, f"{DEL}:{name}", merged[DEL]["c"], o["c"]),
                    "tbl": tbl, "pk": pk, "field": name,
                    "kept_value": None, "other_value": o["v"],
                    "kept_clock": merged[DEL]["c"], "other_clock": o["c"],
                    "kind": "edited-while-deleted",
                })
    return merged, conflicts


def is_deleted(state: Dict[str, dict]) -> bool:
    reg = state.get(DEL)
    return bool(reg and reg["v"])
