"""Cursor Tier-0 SQL assembler — build a correct read-only SELECT against a pre-built VIEW.

Given a ViewProfile (from the Routing Catalog) + slots extracted from the question, assemble the SQL
deterministically: FROM the view (never a model-picked table), SELECT the count-of-grain (or a
grouped metric), WHERE = the app-owned scope DEFAULTS + an optional person filter (on a resolved key,
never a display name) + an optional dimension filter. All literals are NAMED PARAMS (bound safely).

Pure + never-raises: person NAME→key resolution (a DB touch) happens in the caller and is passed in as
`person_key`; the assembler itself does no I/O. Returns None to DECLINE (→ the caller falls through to
the LLM tiers) rather than emit a wrong-but-valid query.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from services.cursor_catalog import _content_tokens


# Underscore-separated PARTS that mark an id column as a PERSON key (vs a record/entity id like
# order_id). Part-matching (not substring) so `team_leader_id` → {team, leader} matches 'leader'.
_ROLE_TOKENS = {
    "ae", "se", "rep", "owner", "manager", "mgr", "leader", "lead", "rsm", "sem", "rsd", "agent",
    "seller", "engineer", "eng", "customer", "client", "employee", "emp", "user", "contact", "person",
    "assignee", "sales", "sdr", "bdr", "csm",
}


def _key_parts(rk: str) -> set:
    base = re.sub(r"(_id|_key)$", "", rk, flags=re.IGNORECASE)
    return {p for p in re.split(r"[^a-z0-9]+", base.lower()) if p}


def _person_role_keys(role_keys: List[str]) -> List[str]:
    """Role keys that plausibly hold a PERSON (a part before `_id`/`_key` is a role token), so an
    entity/record id like `order_id` or `node_id` is never used as a person filter."""
    return [rk for rk in role_keys if _key_parts(rk) & _ROLE_TOKENS]


def _pick_role_key(question: str, role_keys: List[str]) -> Optional[str]:
    """Choose which PERSON role key to filter on, from the question's intent. Returns None if the view
    has no person-role key (→ the assembler declines the person filter → LLM tiers)."""
    person_keys = _person_role_keys(role_keys)
    if not person_keys:
        return None
    ql = (question or "").lower()

    def find(tokens):
        return next((rk for rk in person_keys if _key_parts(rk) & set(tokens)), None)

    if re.search(r"\bse\b|systems?\s+engineer|\bsystem\b", ql):
        rk = find(["se", "eng", "engineer"])
        if rk:
            return rk
    if re.search(r"\blead|manager|\brsm\b|\bsem\b|\brsd\b|\bmanage", ql):
        rk = find(["leader", "lead", "rsm", "sem", "rsd", "mgr", "manager"])
        if rk:
            return rk
    return find(["ae", "owner", "customer", "client", "rep"]) or person_keys[0]


def _detect_group_by(question: str, dimensions: List[str]) -> Optional[str]:
    """A 'by/per/for each <dimension>' phrasing whose dimension matches a view column → GROUP BY it."""
    if not re.search(r"\b(by|per|each)\b", (question or "").lower()):
        return None
    qt = _content_tokens(question)
    for d in dimensions:
        dt = _content_tokens(d)
        if dt and dt <= qt:
            return d
    return None


def _detect_dim_filters(question: str, dimensions: List[str],
                        low_card_values: Dict[str, List[str]]) -> Dict[str, str]:
    """A stored low-cardinality value named in the question → an equality filter on its dimension.
    Values must match as a WHOLE phrase (word boundaries), the LONGEST match wins, and each matched
    span is consumed — so a short value ('North') can't also filter a second dimension inside a longer
    value's span ('North West'), and one question value never fans out to multiple columns."""
    ql = (question or "").lower()
    cands = []   # (dim, value, start, end)
    for d in dimensions:
        for val in (low_card_values.get(d) or []):
            sval = str(val).strip()
            if len(sval) < 2:
                continue
            m = re.search(r"(?<![a-z0-9])" + re.escape(sval.lower()) + r"(?![a-z0-9])", ql)
            if m:
                cands.append((d, sval, m.start(), m.end()))
    cands.sort(key=lambda c: c[2] - c[3])   # longest span first
    out: Dict[str, str] = {}
    used_spans: List = []
    for d, sval, s, e in cands:
        if d in out:
            continue
        if any(s < ue and e > us for us, ue in used_spans):   # overlaps an already-consumed value
            continue
        out[d] = sval
        used_spans.append((s, e))
    return out


def extract_slots(question: str, view_profile: Dict[str, Any],
                  low_card_values: Optional[Dict[str, List[str]]] = None) -> Dict[str, Any]:
    """Pull the query slots for `question` against a chosen view: an optional person mention + which
    role key it filters, a group-by dimension, and dimension equality filters. Never raises."""
    slots: Dict[str, Any] = {"person": None, "role_key": None, "group_by": None, "dim_filters": {}}
    try:
        from services.cursor_sql import _candidate_entities
        cands = _candidate_entities(question)
        dims = view_profile.get("dimensions", []) or []
        low = low_card_values or {}
        # a candidate that isn't itself a low-card dimension VALUE is treated as a person mention
        dim_vals = {str(v).lower() for vs in low.values() for v in (vs or [])}
        person = next((c for c in cands if c.strip().lower() not in dim_vals), None)
        if person and view_profile.get("role_keys"):
            slots["person"] = person
            slots["role_key"] = _pick_role_key(question, view_profile["role_keys"])
        slots["group_by"] = _detect_group_by(question, dims)
        slots["dim_filters"] = _detect_dim_filters(question, dims, low)
    except Exception:
        pass
    return slots


def _coerce(value: str) -> Any:
    v = str(value).strip()
    if re.fullmatch(r"-?\d+", v):
        return int(v)
    if re.fullmatch(r"-?\d+\.\d+", v):
        return float(v)
    return v


def _q(ident: str) -> str:
    return '"' + str(ident).replace('"', '""') + '"'


def assemble(view_profile: Dict[str, Any], slots: Dict[str, Any],
             person_key: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Build `{sql, params}` for the view+slots, or None to decline. Scope defaults are ALWAYS applied;
    a person filter uses the resolved `person_key` (never the display name) and declines if unresolved."""
    view = view_profile.get("name")
    if not view:
        return None
    grain = view_profile.get("grain")
    metric = f"COUNT(DISTINCT {_q(grain)})" if grain else "COUNT(*)"
    where: List[str] = []
    params: Dict[str, Any] = {}

    for i, f in enumerate(view_profile.get("scope_filters") or []):
        col, op = f.get("col"), (f.get("op") or "=")
        if not col:
            continue
        p = f"scope_{i}"
        where.append(f"{_q(col)} {op} :{p}")
        params[p] = _coerce(f.get("value", ""))

    if slots.get("person"):
        rk = slots.get("role_key")
        if not rk or person_key is None:
            return None   # a person was named but we couldn't ground it → decline to the LLM tiers
        where.append(f"{_q(rk)} = :person_key")
        params["person_key"] = person_key

    scope_cols = {str(f.get("col", "")).lower() for f in view_profile.get("scope_filters") or []}
    for i, (col, val) in enumerate((slots.get("dim_filters") or {}).items()):
        if str(col).lower() in scope_cols:
            continue   # already constrained by a scope default — don't duplicate the column
        p = f"dim_{i}"
        where.append(f"{_q(col)} = :{p}")
        params[p] = val

    gb = slots.get("group_by")
    select = f"{_q(gb)}, {metric} AS result" if gb else f"{metric} AS result"
    sql = f"SELECT {select} FROM {_q(view)}"
    if where:
        sql += " WHERE " + " AND ".join(where)
    if gb:
        sql += f" GROUP BY {_q(gb)} ORDER BY result DESC"

    try:
        from services.tabular_query import safe_sql
        if not safe_sql(sql):
            return None
    except Exception:
        pass
    return {"sql": sql, "params": params}
