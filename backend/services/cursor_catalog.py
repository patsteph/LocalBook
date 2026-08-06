"""Cursor Routing Catalog — derive the deterministic query layer from the folder's OWN artifacts.

A Cursor Style notebook folder ships a `schema.html` whose embedded `SCHEMA_CATALOG` JSON already
contains everything needed to route questions deterministically: all VIEW definitions (`ddl`), every
column + type, the FK / logical-association graph, and semantic categories. The 21 views ARE the
"recipes" (they pre-encode the canonical joins/filters). The intent routes + scope defaults live in
`AGENTS.md`. This module turns those two artifacts into a Routing Catalog so the app can answer known
questions by assembling SQL against the right pre-built view — no hand-authored `recipes.sql`, nothing
copied per monthly export, auto-refreshed.

Phase 0 (this file, initially): a CORRECT `SCHEMA_CATALOG` parser + a prompt summary renderer,
replacing the silently-broken `data_notebook._read_schema_summary` (it read `categories` as a dict —
the real shape is a list — and looked for join-hint keys that don't exist, so schema.html contributed
~nothing). Later phases add `build_routing_catalog` (ViewProfiles + RouteEntries + defaults).

Pure + never-raises: parsing is best-effort; a malformed catalog yields None / an empty summary, and
the engine falls back to `.db` introspection + the LLM path exactly as before.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_CATALOG_RE = re.compile(r"SCHEMA_CATALOG\s*=\s*(\{.*?\})\s*;", re.DOTALL)


def parse_schema_catalog(raw_html: str) -> Optional[Dict[str, Any]]:
    """Extract + parse the embedded `SCHEMA_CATALOG` JSON from a schema.html string.

    Returns the catalog dict — `{categories: [{label, names}], objects: [...], table_count,
    view_count}` — or None if absent/unparseable. Each object carries `name`, `kind` ('table'|'view'),
    `category`, `columns` [{name,type,not_null,pk,default}], `ddl`, `foreign_keys_in/out`,
    `logical_associations_in/out`, `indexes`, `row_count`. Never raises."""
    if not raw_html:
        return None
    try:
        from utils.json_repair import robust_json_parse
        m = _CATALOG_RE.search(raw_html)
        if not m:
            return None
        catalog = robust_json_parse(m.group(1))
        if not isinstance(catalog, dict) or not isinstance(catalog.get("objects"), list):
            return None
        return catalog
    except Exception as e:
        logger.debug(f"[cursor_catalog] parse skipped: {type(e).__name__}: {e}")
        return None


def _objects(catalog: Dict[str, Any]) -> List[Dict[str, Any]]:
    objs = catalog.get("objects")
    return [o for o in objs if isinstance(o, dict)] if isinstance(objs, list) else []


def iter_views(catalog: Dict[str, Any]) -> List[Dict[str, Any]]:
    """The view objects — the pre-built recipes."""
    return [o for o in _objects(catalog) if str(o.get("kind", "")).lower() == "view"]


def object_by_name(catalog: Dict[str, Any], name: str) -> Optional[Dict[str, Any]]:
    nl = (name or "").lower()
    return next((o for o in _objects(catalog) if str(o.get("name", "")).lower() == nl), None)


def _category_lines(catalog: Dict[str, Any], max_cats: int = 12, max_names: int = 14) -> List[str]:
    """Categories are a LIST of {label, names} (the old parser wrongly treated it as a dict)."""
    out: List[str] = []
    cats = catalog.get("categories")
    if not isinstance(cats, list):
        return out
    for c in cats[:max_cats]:
        if not isinstance(c, dict):
            continue
        label = c.get("label") or ""
        names = c.get("names")
        if label and isinstance(names, list) and names:
            out.append(f"- {label}: {', '.join(str(n) for n in names[:max_names])}")
    return out


def _join_hint_lines(catalog: Dict[str, Any], cap: int = 24) -> List[str]:
    """Join hints for the base-table path. Two real sources in SCHEMA_CATALOG:
    `logical_associations_out` (each has a human `join_note`) and formal `foreign_keys_out`
    (`from_columns → ref_table.to_columns`). The old parser looked for `hint`/`note`/`description`
    keys that don't exist — the actual key is `join_note` — so it emitted nothing."""
    seen: set = set()
    lines: List[str] = []
    for o in _objects(catalog):
        name = o.get("name")
        if not name:
            continue
        for a in (o.get("logical_associations_out") or []):
            if not isinstance(a, dict):
                continue
            fc, tt, tc = a.get("from_column"), a.get("to_table"), a.get("to_column")
            if not (fc and tt and tc):
                continue
            note = a.get("join_note") or ""
            key = (name, fc, tt, tc)
            if key in seen:
                continue
            seen.add(key)
            hint = f"- {name}.{fc} ↔ {tt}.{tc}"
            if note:
                hint += f" — {note}"
            lines.append(hint)
        for fk in (o.get("foreign_keys_out") or []):
            if not isinstance(fk, dict):
                continue
            frm, tt, to = fk.get("from_columns"), fk.get("ref_table"), fk.get("to_columns")
            if not (frm and tt and to):
                continue
            fcs, tcs = ", ".join(map(str, frm)), ", ".join(map(str, to))
            key = (name, fcs, tt, tcs)
            if key in seen:
                continue
            seen.add(key)
            lines.append(f"- {name}.({fcs}) → {tt}.({tcs})")
    return lines[:cap]


def render_schema_summary(catalog: Optional[Dict[str, Any]]) -> str:
    """A compact structural block for the (Tier-2) SQL prompt: the category routing + real join hints.
    This is what `schema.html` was always meant to contribute and previously didn't. Empty string if
    there's nothing usable (the caller adds its own header/pointer)."""
    if not catalog:
        return ""
    lines: List[str] = []
    lines.extend(_category_lines(catalog))
    hints = _join_hint_lines(catalog)
    if hints:
        lines.append("Join hints (use when NO view fits and you must join base tables):")
        lines.extend(hints)
    return "\n".join(lines)


# ══════════════════════════════════════════════════════════════════════════════════════════════
# Phase 1 — the Routing Catalog: turn SCHEMA_CATALOG + AGENTS.md into a deterministic query layer.
# Everything below is DERIVED (no hardcoded deployment terms) so it survives monthly re-exports and
# works for any deployment whose schema.html follows the same SCHEMA_CATALOG shape.
# ══════════════════════════════════════════════════════════════════════════════════════════════

_ID_COL = re.compile(r"(_id|_key)$", re.IGNORECASE)
_NAME_COL = re.compile(r"name$", re.IGNORECASE)
_PERSON_NAME_COL = re.compile(
    r"(full_name|employee_name|person_name|contact_name|manager_name|rep_name|agent_name|"
    r"owner_name|preferred_name|display_name)$", re.IGNORECASE)
# Temporal / scope-ish columns — excluded from the record grain and from metrics. Generic patterns
# (`_year` also catches a fiscal_year); a deployment's own scope defaults come from parse_defaults.
_SCOPE_COL = re.compile(r"(_year|\byear\b|\bhalf\b|quarter|month|snapshot|as_of)", re.IGNORECASE)
_NUMERIC_TYPE = re.compile(r"(INT|REAL|FLOA|DOUB|NUMERIC|DECIMAL)", re.IGNORECASE)


def _colname(c: Dict[str, Any]) -> str:
    return str(c.get("name", ""))


def _is_id_col(name: str) -> bool:
    return bool(_ID_COL.search(name)) or name.lower() == "id"


def _all_column_names(catalog: Dict[str, Any]) -> set:
    out: set = set()
    for o in _objects(catalog):
        for c in (o.get("columns") or []):
            n = _colname(c)
            if n:
                out.add(n)
    return out


def derive_record_grain(catalog: Dict[str, Any]) -> Optional[str]:
    """The record identity column for COUNT(DISTINCT …) — derived as the most-referenced FK/association
    key column that isn't a scope/temporal column (e.g. a record hub-key column). Generic: reads the
    graph, hardcodes nothing."""
    from collections import Counter
    counts: Counter = Counter()
    for o in _objects(catalog):
        for fk in (o.get("foreign_keys_out") or []):
            for c in (fk.get("from_columns") or []) + (fk.get("to_columns") or []):
                counts[str(c)] += 1
        for a in (o.get("logical_associations_out") or []):
            for k in ("from_column", "to_column"):
                if a.get(k):
                    counts[str(a[k])] += 1
    for col, _n in counts.most_common():
        if col and not _SCOPE_COL.search(col) and not _is_id_col(col):
            return col
    return None


def derive_person_convention(catalog: Dict[str, Any]) -> Optional[Dict[str, str]]:
    """Where a person's DISPLAY NAME resolves to a stable key — derived from the FK graph: the dominant
    FK TARGET table (e.g. `employees.email_id`, referenced by every role key) whose table also has a
    person-name column. Returns {name_table, name_col, key_col} or None. Generic + durable."""
    from collections import Counter
    targets: Counter = Counter()
    for o in _objects(catalog):
        for fk in (o.get("foreign_keys_out") or []):
            tt, tc = fk.get("ref_table"), fk.get("to_columns") or []
            if tt and len(tc) == 1:
                targets[(str(tt), str(tc[0]))] += 1
    for (table, key_col), _n in targets.most_common():
        obj = object_by_name(catalog, table)
        if not obj:
            continue
        name_col = next((_colname(c) for c in (obj.get("columns") or [])
                         if _PERSON_NAME_COL.search(_colname(c))), None)
        if name_col:
            return {"name_table": table, "name_col": name_col, "key_col": key_col}
    return None


_DEFAULT_RE = re.compile(
    r"`?\b([a-z_][a-z0-9_]*)\b`?\s*(=|>=|<=|<>|!=|>|<)\s*('[^']*'|\"[^\"]*\"|-?\d+(?:\.\d+)?|[a-z_][a-z0-9_]*)",
    re.IGNORECASE)


def parse_defaults(governance_md: str, known_cols: set) -> List[Dict[str, str]]:
    """Parse GLOBAL scope defaults (e.g. year=2025, status='active', a scope flag=1, …) from the
    AGENTS.md 'Global defaults' table. Grounded to REAL column names (so prose/example filters aren't
    mistaken for defaults) — robust to the exact markdown format. Returns [{col, op, value}], deduped
    by col. Empty when none documented (the caller logs it; Tier 0 still works, minus auto-scope)."""
    if not governance_md:
        return []
    text = governance_md
    # Prefer a "default(s)" section if present (tighter); else scan the whole doc grounded to columns.
    m = re.search(r"(?is)^#{1,6}[^\n]*\bdefault", governance_md, re.MULTILINE)
    if m:
        text = governance_md[m.start(): m.start() + 2000]
    found: Dict[str, Dict[str, str]] = {}
    for mm in _DEFAULT_RE.finditer(text):
        col, op, val = mm.group(1), mm.group(2), mm.group(3)
        if col.lower() not in {c.lower() for c in known_cols}:
            continue
        if col.lower() in found:
            continue
        found[col.lower()] = {"col": col, "op": op, "value": val.strip("'\"")}
    return list(found.values())


def _is_metric(c: Dict[str, Any]) -> bool:
    name, typ = _colname(c), str(c.get("type", ""))
    if _is_id_col(name) or _SCOPE_COL.search(name) or _NAME_COL.search(name):
        return False
    return bool(_NUMERIC_TYPE.search(typ))


def _is_dimension(c: Dict[str, Any]) -> bool:
    name, typ = _colname(c), str(c.get("type", "")).upper()
    if _is_id_col(name) or _NAME_COL.search(name) or _SCOPE_COL.search(name):
        return False
    return ("TEXT" in typ) or ("CHAR" in typ) or (typ == "")


def _view_profile(obj: Dict[str, Any], defaults: List[Dict[str, str]],
                  grain: Optional[str]) -> Dict[str, Any]:
    cols = obj.get("columns") or []
    colnames = [_colname(c) for c in cols]
    colset = {n.lower() for n in colnames}
    role_keys = [n for n in colnames if _is_id_col(n)]
    dimensions = [_colname(c) for c in cols if _is_dimension(c)]
    metrics = [_colname(c) for c in cols if _is_metric(c)]
    view_grain = grain if (grain and grain.lower() in colset) else None
    if not view_grain:  # fall back to a single non-person entity *_name column
        ents = [n for n in colnames if _NAME_COL.search(n) and not _PERSON_NAME_COL.search(n)
                and n.lower() != "name"]
        view_grain = ents[0] if len(ents) == 1 else None
    scope_filters = [d for d in defaults if d["col"].lower() in colset]
    return {
        "name": obj.get("name"), "kind": obj.get("kind"), "category": obj.get("category"),
        "columns": [{"name": _colname(c), "type": str(c.get("type", ""))} for c in cols],
        "role_keys": role_keys, "dimensions": dimensions, "metrics": metrics,
        "grain": view_grain, "scope_filters": scope_filters, "ddl": obj.get("ddl", ""),
    }


_WILDCARD_RE = re.compile(r"\bv_[a-z0-9_]*\*", re.IGNORECASE)


def _bind_routes(governance_md: str, view_names: List[str]) -> List[Dict[str, Any]]:
    """Turn the AGENTS.md intent routes (Request|Approach / Intent|Primary object tables, parsed by the
    reused `cursor_sql._parse_recipes`) into RouteEntries bound to real views. A route that binds to
    exactly one concrete view is Tier-0-ready; a view FAMILY (`v_<family>_*`) or an ambiguous multi-view
    approach is Tier-1-only (candidate set) — never fabricate a single target."""
    try:
        from services.cursor_sql import _parse_recipes
        recipes = _parse_recipes(governance_md or "")
    except Exception as e:
        logger.debug(f"[cursor_catalog] route parse skipped: {type(e).__name__}: {e}")
        return []
    vset = {v.lower(): v for v in view_names}
    routes: List[Dict[str, Any]] = []
    for i, r in enumerate(recipes):
        request, approach = r.get("request", ""), r.get("approach", "")
        al = approach.lower()
        concrete = [v for vl, v in vset.items() if re.search(r"\b" + re.escape(vl) + r"\b", al)]
        for wc in _WILDCARD_RE.findall(al):               # a v_<family>_* wildcard → all matching views
            pref = wc.rstrip("*")
            concrete += [v for vl, v in vset.items() if vl.startswith(pref) and v not in concrete]
        concrete = list(dict.fromkeys(concrete))          # dedupe, keep order
        if not concrete:
            continue                                       # nothing to route to → Tier 2 handles it
        one = len(concrete) == 1
        routes.append({
            "intent_id": re.sub(r"[^a-z0-9]+", "_", request.lower()).strip("_") or f"route_{i}",
            "trigger_phrases": [request] if request else [],
            "target_view": concrete[0] if one else None,
            "candidate_views": [] if one else concrete,
            "tier0_ready": one,
        })
    return routes


def build_routing_catalog(schema_catalog: Optional[Dict[str, Any]],
                          governance_md: str) -> Optional[Dict[str, Any]]:
    """Derive the full Routing Catalog from the folder's OWN artifacts. Never raises → returns None on
    failure so answering falls back to the LLM path exactly as before. `generated_at` is stamped by the
    persistence layer (this stays pure)."""
    if not schema_catalog:
        return None
    try:
        grain = derive_record_grain(schema_catalog)
        person = derive_person_convention(schema_catalog)
        known_cols = _all_column_names(schema_catalog)
        defaults = parse_defaults(governance_md, known_cols)
        views = {str(v.get("name")): _view_profile(v, defaults, grain) for v in iter_views(schema_catalog)}
        routes = _bind_routes(governance_md, list(views.keys()))
        return {
            "views": views,
            "routes": routes,
            "defaults": defaults,
            "person_convention": person,
            "record_grain": grain,
            "view_count": len(views),
            "route_count": len(routes),
        }
    except Exception as e:
        logger.warning(f"[cursor_catalog] build_routing_catalog failed: {type(e).__name__}: {e}")
        return None


# ── Tier-0 target selection: which VIEW answers this question (deterministic; embeddings = Phase 3) ──
_STOP = {
    "how", "many", "much", "what", "which", "who", "when", "where", "why", "show", "list", "give",
    "tell", "find", "count", "total", "sum", "average", "the", "for", "and", "are", "was", "were",
    "does", "did", "get", "number", "have", "has", "with", "per", "each", "all", "that", "this",
    "there", "from", "into", "our", "your", "their", "his", "her", "its", "view", "table",
}


def _stem(tok: str) -> str:
    """Cheap stemmer so 'accounts'↔'account', 'owns'↔'own', 'bookings'↔'booking' match."""
    for suf in ("es", "s"):
        if len(tok) > 4 and tok.endswith(suf):
            return tok[: -len(suf)]
    return tok


def _content_tokens(text: str) -> set:
    return {_stem(t) for t in re.split(r"[^a-z0-9]+", (text or "").lower())
            if len(t) >= 3 and t not in _STOP}


def _route_score(qtokens: set, phrases: List[str]) -> float:
    best = 0.0
    for p in phrases:
        pt = _content_tokens(re.sub(r"\{[^}]*\}", " ", p))   # drop {param} placeholders
        if pt:
            best = max(best, len(qtokens & pt) / len(pt))     # fraction of the intent's content covered
    return best


def _view_tokens(vp: Dict[str, Any]) -> set:
    toks = _content_tokens(str(vp.get("name", "")))
    toks |= _content_tokens(str(vp.get("category", "")))
    for d in vp.get("dimensions", []):
        toks |= _content_tokens(d)
    return toks


def select_target(question: str, catalog: Optional[Dict[str, Any]],
                  route_thr: float = 0.6, view_thr: int = 2) -> "tuple":
    """Pick the VIEW that answers `question`. Returns (view_name|None, confidence, source). Documented
    intent routes win (trigger-phrase overlap ≥ route_thr); else the nearest view by keyword overlap
    (≥ view_thr matched content tokens). Deterministic — the embedding refinement is Phase 3."""
    if not catalog:
        return (None, 0.0, "none")
    q = _content_tokens(question)
    if not q:
        return (None, 0.0, "none")
    best_route, best_rs = None, 0.0
    for r in catalog.get("routes", []):
        if not r.get("tier0_ready") or not r.get("target_view"):
            continue
        s = _route_score(q, r.get("trigger_phrases", []))
        if s > best_rs:
            best_rs, best_route = s, r
    if best_route and best_rs >= route_thr:
        return (best_route["target_view"], round(best_rs, 3), "route")
    best_view, best_vs = None, 0
    for name, vp in (catalog.get("views") or {}).items():
        overlap = len(q & _view_tokens(vp))
        if overlap > best_vs:
            best_vs, best_view = overlap, name
    if best_view and best_vs >= view_thr:
        return (best_view, float(best_vs), "view")
    return (None, 0.0, "none")
