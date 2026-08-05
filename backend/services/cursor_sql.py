"""
Cursor SQL — the DEDICATED text-to-SQL engine for Cursor Style notebooks.

A Cursor Style notebook points at a folder holding a monthly SQLite `.db` + governance
markdown (AGENTS.md / DATA_OVERVIEW.md / domain_guide.md). Questions are answered by
generating ONE read-only SELECT against that external `.db`, governed by the markdown.

WHY THIS IS A SEPARATE MODULE (isolation guarantee)
---------------------------------------------------
The daily-driver spreadsheet/CSV tabular engine is `tabular_query.answer_tabular`, kept
byte-for-byte identical to master. EVERY accuracy enhancement for Cursor Style notebooks
lives here and NOWHERE else, so it cannot affect standard notebooks:
  • schema-linking (27-table DB → only the relevant tables in the prompt)
  • value / entity linking (resolve "Jordan Lee" → the real stored value via a DB lookup)
  • sqlglot pre-execution validation (catch hallucinated columns before the 15s execution)
  • M-Schema schema representation + [Foreign Keys] join-path injection
  • governance (AGENTS.md) injected as authoritative operating rules
  • execution-guided self-repair (feed the DB/validation error back, retry once)
  • canonical VIEWs via a writable companion DB (the external .db stays untouched)

We REUSE the pure, side-effect-free helpers from `tabular_query` (value resolvers, SQL
safety, model call, answer rendering) — importing them changes nothing about the spreadsheet
path because that path calls its own functions.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional, Tuple

from config import settings
from storage import tabular_store

# Pure, shared helpers — safe to reuse (they do not carry cursor behavior into the xlsx path).
from services.tabular_query import (
    safe_sql,
    select_relevant_tables,
    _resolve_directives,
    _apply_directives_to_sql,
    _gen_sql,
    _render_answer,
    _maybe_chart,
)

logger = logging.getLogger(__name__)

# Tighter than the spreadsheet path: a Cursor DB can be wide (27 tables); a small model's SQL
# accuracy falls off a cliff on a bloated prompt. Fewer sample values per column keeps the
# schema-linked prompt inside a small context window (the deterministic resolvers below still
# map any value/entity the user names, so this list is only a secondary aid).
_MAX_PROMPT_VALUES = 12
# 12 so schema-linking can carry the many relevant v_ VIEWS a question may span (schema.html shows
# 5–10 view linkings) alongside 2 base tables. Still well within gemma's 16k-token window (only
# score>0 objects are linked, so it's usually far fewer than 12).
_MAX_LINKED_TABLES = 12

_SQL_MODEL_KEEP_ALIVE = "30m"


# ─────────────────────────────────────────────────────────────────────────────
# Phase 1b — value / entity linking (high-cardinality lookup)
# ─────────────────────────────────────────────────────────────────────────────
# The shared `_resolve_directives` grounds LOW-cardinality values (region, status, …) that are
# cached in the catalog. It cannot ground a HIGH-cardinality entity the user names — a person,
# an account, an id — because those aren't in the value lists. That's the "Jordan Lee" gap: the
# model, left to guess, invents owner_id='jlee'. Here we detect candidate entities in the
# question and resolve them to the EXACT stored value via a targeted read-only lookup, then hand
# the model the grounded fact so it never has to invent a literal.

# Column-name hints that a text column stores an identity/entity worth resolving.
_ENTITY_COL_HINT = re.compile(
    r"(name|rep|ae|owner|manager|employee|account|customer|client|contact|"
    r"person|agent|seller|title|team|company|vendor|email)",
    re.IGNORECASE,
)
# PERSON-name columns get TOP priority — the guide resolves people via employees.full_name → email_id,
# and on a 58-object schema there are 50+ name-ish columns, so a person-name column must be searched
# before generic ones (account_name/record_key) or the budget runs out first.
_PERSON_NAME_HINT = re.compile(
    r"(full_name|rep_name|employee_name|person_name|contact_name|manager_name|agent_name|"
    r"display_name|owner_name|ae_name|se_name|rsm_name)", re.IGNORECASE)
# Words that look capitalized but aren't entities (sentence starts / question words).
_ENTITY_STOPWORDS = {
    "how", "what", "which", "who", "when", "where", "why", "show", "list", "give", "tell",
    "find", "count", "total", "sum", "average", "many", "much", "the", "a", "an", "of", "for",
    "in", "on", "by", "and", "or", "all", "each", "per", "top", "me", "is", "are", "was", "were",
    "do", "does", "did", "get", "number", "have", "has",
}
# Business/role abbreviations + short generics that must NEVER be grounded — the "AE" → id='rlee'
# class of substring false-positive. Matched case-insensitively; an all-acronym phrase is dropped.
_ENTITY_ACRONYMS = {
    "ae", "vp", "svp", "evp", "rvp", "cx", "geo", "kpi", "ytd", "qtd", "mtd", "fy", "eoy",
    "q1", "q2", "q3", "q4", "h1", "h2", "arr", "mrr", "acv", "tcv", "poc", "sku", "roi", "sla",
    "crm", "us", "na", "emea", "apac", "amer", "gtm", "se", "csm", "sdr", "bdr", "mgr", "id",
    "west", "east", "north", "south", "central", "region", "team", "total", "new", "open", "won",
}


def _candidate_entities(question: str) -> List[str]:
    """Pull proper-noun-ish MULTI-WORD phrases (and quoted phrases) from the raw question —
    e.g. "Jordan Lee", "Sam Rivera", "Red River", '"Acme Corp"'. Deliberately CONSERVATIVE:
    single bare tokens (AE, West, Q1, a lone surname) are NOT grounded — they substring-match random
    ids/values and do more harm than good. A grounded value must be a confident, specific entity."""
    cands: List[str] = []
    # Quoted phrases (explicit user intent) — kept even if single-word.
    for m in re.finditer(r"[\"'“”‘’]([^\"'“”‘’]{2,60})[\"'“”‘’]", question):
        cands.append(m.group(1).strip())
    # Runs of ≥2 Capitalized tokens (allow internal &/-/.). Single tokens are intentionally skipped.
    for m in re.finditer(r"\b([A-Z][A-Za-z0-9.&/-]*(?:\s+[A-Z][A-Za-z0-9.&/-]*)+)\b", question):
        phrase = m.group(1).strip()
        toks = [t for t in re.split(r"\s+", phrase) if t]
        while toks and toks[0].lower() in _ENTITY_STOPWORDS:  # strip leading "Show"/"List"/…
            toks = toks[1:]
        if len(toks) >= 2 and len(" ".join(toks)) >= 5:
            cands.append(" ".join(toks))
    # Dedup, keep order, drop all-acronym / too-short phrases, cap.
    seen: set = set()
    out: List[str] = []
    for c in cands:
        toks_l = [t for t in re.split(r"\s+", c.lower().strip()) if t]
        # Skip if every token is a known acronym/generic or ≤2 chars (nothing specific to ground).
        if not toks_l or all(t in _ENTITY_ACRONYMS or len(t) <= 2 for t in toks_l):
            continue
        k = c.lower()
        if k not in seen and len(c) >= 2:
            seen.add(k)
            out.append(c)
    return out[:6]


def _resolve_entities(question: str, schema: List[Dict[str, Any]],
                      db_path: Optional[str]) -> List[Dict[str, str]]:
    """Ground high-cardinality entity mentions to the EXACT stored value via a targeted read-only
    lookup. Returns [{term, table, col, value}] hints for the prompt. Bounded (≤ a handful of
    lookups) and never raises — resolution is best-effort context, not correctness-critical."""
    hints: List[Dict[str, str]] = []
    try:
        cands = _candidate_entities(question)
        if not cands:
            return hints
        # A phrase that is itself a known LOW-cardinality value (e.g. an area/region like "Region WEST")
        # is a dimension filter, NOT an entity — grounding it against a name column mismatches it to a
        # placeholder ("TBH - Region West"). Skip those; _resolve_directives handles them.
        low_values: set = set()
        for t in schema:
            for c in t.get("columns", []):
                if c.get("low_cardinality"):
                    for v in (c.get("values") or []):
                        low_values.add(str(v).strip().lower())
        cands = [c for c in cands if c.strip().lower() not in low_values]
        if not cands:
            return hints
        logger.info(f"[cursor] entity candidates to resolve: {cands}")
        # Candidate (table, column) targets: HIGH-cardinality text columns, entity-named first.
        # Search the FULL schema (not just schema-linked tables) — the name column often lives in a
        # roster/employee table the linker didn't pick, which is why "Jordan Lee" was being guessed.
        cols_by_table: Dict[str, List[str]] = {
            str(t.get("table_name", "")): [str(c.get("sanitized", "")) for c in t.get("columns", [])]
            for t in schema}
        targets: List[Tuple[int, str, str]] = []
        for t in schema:
            tname = str(t.get("table_name", ""))
            for c in t.get("columns", []):
                if c.get("low_cardinality"):
                    continue  # low-card values are already grounded by _resolve_directives
                dtype = str(c.get("dtype", "")).lower()
                if dtype and dtype not in ("text", "str", "string", "object", "varchar", "char"):
                    continue
                col = str(c.get("sanitized", ""))
                if not col:
                    continue
                # Person-name columns first (0), then generic entity-name columns (1), then rest (2).
                if _PERSON_NAME_HINT.search(col):
                    priority = 0
                elif _ENTITY_COL_HINT.search(col):
                    priority = 1
                else:
                    priority = 2
                targets.append((priority, tname, col))
        targets.sort(key=lambda x: x[0])
        ordered = [(tname, col) for _p, tname, col in targets]

        budget = 48  # hard cap on lookups → bounds latency (indexed LIKE over a name column is ms)
        resolved_terms: set = set()
        for term in cands:
            if budget <= 0:
                break
            if term.lower() in resolved_terms:
                continue
            for tname, col in ordered:
                if budget <= 0:
                    break
                budget -= 1
                val = _lookup_value(db_path, tname, col, term)
                if val is not None:
                    # Phase 3 (integration guide): a person's DISPLAY NAME is not a join key — resolve
                    # it to the STABLE KEY (email_id / owner_id / …) in the same table, so joins use the
                    # key and never embed the display name. Falls back to the name value if no key.
                    key_col = _stable_key_col(cols_by_table.get(tname, []), col)
                    key_val = _lookup_stable_key(db_path, tname, col, val, key_col) if key_col else None
                    hints.append({"term": term, "table": tname, "col": col, "value": val,
                                  "key_col": key_col or "", "key_value": key_val or ""})
                    resolved_terms.add(term.lower())
                    break  # first (highest-priority) column that matches wins for this term
        unresolved = [c for c in cands if c.lower() not in resolved_terms]
        if unresolved:
            # Visible in the log: a named entity we could NOT match to any name column (so the model
            # is left to guess a literal — the display-name-in-key failure). Helps diagnose in the field.
            logger.info(f"[cursor] entities NOT grounded (no name-column match): {unresolved}")
    except Exception as e:
        # WARNING (not DEBUG) so a resolution bug is VISIBLE in the field log instead of silently
        # leaving the model to guess a literal.
        logger.warning(f"[cursor] entity resolution ERROR: {type(e).__name__}: {e}")
    return hints


# Placeholder / non-entity stored values a fuzzy LIKE can latch onto ("TBH - Region West",
# "Unassigned AE"). Grounding to one of these is worse than not grounding at all.
_PLACEHOLDER_VALUE = re.compile(
    r"^\s*(tbh\b|tbd\b|n/?a\b|none\b|null\b|unknown\b|unassigned\b|-+\s*$|to be hired)",
    re.IGNORECASE)


def _lookup_value(db_path: Optional[str], table: str, col: str, term: str) -> Optional[str]:
    """Read-only DISTINCT lookup: does `term` uniquely match a stored value in table.col?
    Returns the single exact stored value (case/spacing as stored), or None if 0 or >1 matches,
    or if the only match is a placeholder (TBH/Unassigned/…)."""
    try:
        qtable = '"' + str(table).replace('"', '""') + '"'
        qcol = '"' + str(col).replace('"', '""') + '"'
        sql = (f"SELECT DISTINCT {qcol} FROM {qtable} "
               f"WHERE {qcol} LIKE '%' || ? || '%' COLLATE NOCASE "
               f"AND {qcol} IS NOT NULL LIMIT 3")
        res = tabular_store.execute_readonly(sql, db_path=db_path, params=[term])
        if not res.get("ok"):
            return None
        rows = res.get("rows", [])
        vals = [str(r[0]) for r in rows if r and r[0] is not None
                and not _PLACEHOLDER_VALUE.match(str(r[0]))]
        if len(vals) == 1:
            return vals[0]
        # Multiple partial matches — accept only an exact (case-insensitive) hit if present.
        exact = [v for v in vals if v.strip().lower() == term.strip().lower()]
        if len(exact) == 1:
            return exact[0]
        return None
    except Exception:
        return None


# Stable-key columns a display name should resolve TO (integration guide Phase 3), preferred order.
_KEY_COL_PREF = ("email_id", "employee_id", "owner_id", "eng_id", "mgr_id", "record_key")
_KEY_COL_SUFFIX = re.compile(r"(_id|_key)$", re.IGNORECASE)


def _stable_key_col(columns: List[str], name_col: str) -> Optional[str]:
    """The stable-key column in a table that a display name should resolve to (email_id / owner_id / …),
    so joins use the key instead of the name. Never returns the name column itself."""
    others = [c for c in columns if c and c != name_col]
    lower = {c.lower(): c for c in others}
    for pref in _KEY_COL_PREF:
        if pref in lower:
            return lower[pref]
    for c in others:                       # else any *_id / *_key that isn't a name-ish column
        if _KEY_COL_SUFFIX.search(c) and "name" not in c.lower():
            return c
    return None


def _lookup_stable_key(db_path: Optional[str], table: str, name_col: str, name_val: str,
                       key_col: str) -> Optional[str]:
    """Resolve a matched display name → its stable key value (e.g. employees.full_name → email_id)."""
    try:
        qt = '"' + str(table).replace('"', '""') + '"'
        qn = '"' + str(name_col).replace('"', '""') + '"'
        qk = '"' + str(key_col).replace('"', '""') + '"'
        sql = f"SELECT DISTINCT {qk} FROM {qt} WHERE {qn} = ? AND {qk} IS NOT NULL LIMIT 2"
        res = tabular_store.execute_readonly(sql, db_path=db_path, params=[name_val])
        if not res.get("ok"):
            return None
        vals = [str(r[0]) for r in res.get("rows", []) if r and r[0] is not None
                and not _PLACEHOLDER_VALUE.match(str(r[0]))]
        return vals[0] if len(vals) == 1 else None
    except Exception:
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Phase 2 — M-Schema representation + [Foreign Keys] injection
# ─────────────────────────────────────────────────────────────────────────────
def _mschema_table(t: Dict[str, Any]) -> str:
    """One table in a compact M-Schema-style block: (col:dtype, examples) per column.
    M-Schema beats verbose DDL for small models (measured +2pp on BIRD) and is shorter."""
    name = t.get("table_name", "")
    if t.get("kind") == "view":
        header = f'# View: {name}  (PRE-JOINED — prefer this over base tables)'
    else:
        header = f'# Table: {name}  ({t.get("row_count", "?")} rows)'
    lines = [header, "["]
    cols = t.get("columns", [])
    for i, c in enumerate(cols):
        col = c.get("sanitized", "")
        dtype = c.get("dtype", "")
        piece = f'  ({col}:{dtype}'
        if c.get("low_cardinality") and c.get("values"):
            vals = c["values"][:_MAX_PROMPT_VALUES]
            shown = ", ".join(str(v) for v in vals)
            more = "" if len(c["values"]) <= _MAX_PROMPT_VALUES else ", …"
            piece += f", one of: {shown}{more}"
        elif c.get("samples"):
            piece += f', e.g. {", ".join(str(v) for v in c["samples"][:3])}'
        piece += ")" + ("," if i < len(cols) - 1 else "")
        lines.append(piece)
    lines.append("]")
    return "\n".join(lines)


def _fk_block(linked: List[Dict[str, Any]], relationships: List[Dict[str, Any]]) -> str:
    """A [Foreign Keys] block listing the join paths among the LINKED tables — the single biggest
    lever for join accuracy (models otherwise omit the FK columns needed to JOIN)."""
    if not relationships:
        return ""
    names = {str(t.get("table_name", "")) for t in linked}
    seen: set = set()
    lines: List[str] = []
    for r in relationships:
        ft, fc = r.get("from_table"), r.get("from_col")
        tt, tc = r.get("to_table"), r.get("to_col")
        if not (ft and fc and tt and tc):
            continue
        if ft not in names or tt not in names:
            continue  # only surface joins between tables actually in the prompt
        key = tuple(sorted([f"{ft}.{fc}", f"{tt}.{tc}"]))
        if key in seen:
            continue
        seen.add(key)
        kind = r.get("kind", "")
        tag = "" if kind == "declared" else "  -- inferred"
        lines.append(f"  {ft}.{fc} = {tt}.{tc}{tag}")
    if not lines:
        return ""
    return "[Foreign Keys]\n" + "\n".join(lines) + "\n\n"


# ─────────────────────────────────────────────────────────────────────────────
# Recipe layer — canonical request→approach patterns the data owner documents
# ─────────────────────────────────────────────────────────────────────────────
# AGENTS.md commonly ships a "Typical user requests" table mapping a kind of request to the
# canonical approach (which views/filters/joins to use). Those 20-ish recipes are already in the
# governance blob, but buried — a small model doesn't reliably connect a question to the right row.
# We parse them out and, per question, surface only the MOST RELEVANT recipe(s) prominently, right
# before the question. Relevance is token overlap (robust for short request phrases; zero latency).

_RECIPE_STOPWORDS = {
    "the", "a", "an", "of", "for", "in", "on", "by", "and", "or", "to", "with", "my", "our",
    "me", "show", "list", "find", "get", "what", "which", "who", "how", "is", "are", "all",
    "request", "approach", "use", "filter", "confirm", "via", "per", "each", "from", "that",
}


def _tokens(text: str) -> set:
    return {t for t in re.split(r"[^a-z0-9]+", str(text).lower())
            if len(t) >= 3 and t not in _RECIPE_STOPWORDS}


# A recipe table maps an intent → the canonical object/approach. Two documented shapes:
#   | Request | Approach |          (AGENTS.md "Typical user requests")
#   | Intent  | Primary object(s) | Notes |   (domain_guide integration guide "Phase 1 — Classify")
_RECIPE_REQ_HDR = ("request", "intent", "question", "need")
_RECIPE_APP_HDR = ("approach", "primary object", "object", "use this", "primary")


def _parse_recipes(governance: str) -> List[Dict[str, str]]:
    """Extract recipes from a markdown table that maps an intent to the canonical object/approach.
    Matches both `Request|Approach` and `Intent|Primary object(s)` headers (case-insensitive).
    Returns [{request, approach}]. Robust to extra columns / surrounding prose. Never raises."""
    recipes: List[Dict[str, str]] = []
    try:
        lines = (governance or "").splitlines()
        i = 0
        while i < len(lines):
            row = lines[i]
            cells = [c.strip() for c in row.split("|")]
            low = [c.lower() for c in cells]
            req_idx = next((k for k, c in enumerate(low) if any(h in c for h in _RECIPE_REQ_HDR)), None)
            app_idx = next((k for k, c in enumerate(low) if any(h in c for h in _RECIPE_APP_HDR)), None)
            # A header row naming both an intent column AND an object/approach column starts a table.
            if "|" in row and req_idx is not None and app_idx is not None and req_idx != app_idx:
                j = i + 1
                if j < len(lines) and set(lines[j].replace("|", "").strip()) <= set("-: "):
                    j += 1  # skip the |---|---| separator
                while j < len(lines) and "|" in lines[j]:
                    rc = [c.strip() for c in lines[j].split("|")]
                    if len(rc) > max(req_idx, app_idx):
                        request = rc[req_idx].strip().strip('"\'` ').strip()
                        approach = rc[app_idx].strip()
                        if request and approach:
                            recipes.append({"request": request, "approach": approach})
                    j += 1
                i = j
                continue
            i += 1
    except Exception as e:
        logger.debug(f"[cursor] recipe parse skipped: {type(e).__name__}: {e}")
    return recipes


def _match_recipes(question: str, recipes: List[Dict[str, str]], k: int = 3) -> List[Dict[str, str]]:
    """Rank recipes by token overlap of the question against each recipe's request (+ its approach,
    weighted lower). Uses PREFIX/stem matching so 'accounts'~'account' and 'own'~'ownership' count —
    exact-token overlap missed the guide's intents entirely (recipes=0 in the field). Best first."""
    if not recipes:
        return []
    qtok = _tokens(question)
    if not qtok:
        return []

    def _overlap(a: set, b: set) -> int:
        n = 0
        for x in a:
            for y in b:
                if x == y or (min(len(x), len(y)) >= 3 and
                              (x.startswith(y) or y.startswith(x))):  # prefix/stem match
                    n += 1
                    break
        return n

    scored = []
    for r in recipes:
        score = 2 * _overlap(qtok, _tokens(r["request"])) + _overlap(qtok, _tokens(r["approach"]))
        if score > 0:
            scored.append((score, r))
    scored.sort(key=lambda x: -x[0])
    return [r for _s, r in scored[:k]]


def _build_cursor_prompt(question: str, linked: List[Dict[str, Any]],
                         directives: List[Dict[str, str]], entity_hints: List[Dict[str, str]],
                         relationships: List[Dict[str, Any]], governance: str,
                         views: Optional[List[Dict[str, Any]]] = None,
                         recipes: Optional[List[Dict[str, str]]] = None) -> str:
    """The Cursor Style SQL prompt: matched recipes + authoritative governance + M-Schema + FK graph
    + grounded value/entity directives + read-only SELECT rules tuned for a wide enterprise schema."""
    recipe_block = ""
    if recipes:
        recipe_block = (
            "MATCHED RECIPE(S) for this kind of request — follow the approach (tables/views/filters) "
            "it prescribes:\n"
            + "\n".join(f'- Request like "{r["request"]}" → {r["approach"]}' for r in recipes)
            + "\n\n"
        )
    governance_block = ""
    if governance and governance.strip():
        governance_block = (
            "OPERATING RULES (AUTHORITATIVE — follow exactly; they define canonical joins, "
            "required filters, and metric definitions for THIS database):\n"
            f"{governance.strip()}\n\n"
        )

    schema_text = "\n\n".join(_mschema_table(t) for t in linked)
    fk_text = _fk_block(linked, relationships)

    views_block = ""
    if views:
        vlines = [f'  {v["name"]}  -- {v.get("description", "").strip() or "pre-joined view"}'
                  for v in views]
        views_block = (
            "\nPRE-BUILT VIEWS (prefer these — they pre-join the canonical relationships so you "
            "do NOT have to write the joins yourself):\n" + "\n".join(vlines) + "\n"
        )

    directive_block = ""
    lines: List[str] = [f"- \"{d['term']}\" → {d['col']} = '{d['value']}'" for d in directives]
    for h in entity_hints:
        if h.get("key_value") and h.get("key_col"):
            # A display name resolved to a STABLE KEY — instruct joining on the key (guide Phase 3).
            lines.append(
                f"- \"{h['term']}\" → {h['table']}.{h['key_col']} = '{h['key_value']}' "
                f"(STABLE KEY — filter/JOIN on this; NEVER put the display name '{h['value']}' in a "
                f"WHERE/JOIN)")
        else:
            lines.append(f"- \"{h['term']}\" → {h['table']}.{h['col']} = '{h['value']}'")
    if lines:
        directive_block = (
            "\nGROUNDED VALUES — the question's phrases map to these EXACT stored values (use them "
            "verbatim; do NOT invent or abbreviate a literal):\n" + "\n".join(lines) + "\n"
        )

    obey_governance = (
        "- Obey the OPERATING RULES above: apply their required filters and canonical joins even "
        "when the question does not restate them, and use their metric definitions.\n"
        if governance_block else ""
    )
    return (
        "Translate the user's question into ONE read-only SQLite SELECT over the schema below.\n\n"
        f"{recipe_block}"
        f"{governance_block}"
        f"{schema_text}\n\n"
        f"{fk_text}"
        f"{views_block}"
        f"{directive_block}"
        "Rules:\n"
        f"{obey_governance}"
        "- Output ONLY the SQL. No explanation, no markdown fences.\n"
        "- Use ONLY the exact table + column names shown. Quote names with spaces/capitals in "
        'double quotes (e.g. "Sales Amount").\n'
        "- NEVER reference a column that is not listed. To count rows use COUNT(*), never COUNT(id) "
        "unless an 'id' column is explicitly listed.\n"
        "- PREFER a purpose-built VIEW (an object named v_…, marked '# View') when one matches the "
        "question — it already pre-joins the base tables, so query it DIRECTLY instead of writing the "
        "joins yourself. Only join base tables when no view fits.\n"
        "- Use the [Foreign Keys] join paths above when a question spans base tables — JOIN on those "
        "exact key pairs; do not guess join columns.\n"
        "- To filter by a PERSON'S NAME or other entity, use the GROUNDED VALUES above if provided; "
        "otherwise JOIN to the table that stores that name and match the name column there — NEVER "
        "invent an id/code from a name.\n"
        "- Filter using ONLY the EXACT listed/grounded values; copy the spelling verbatim. Add a "
        "WHERE clause ONLY for a constraint the question explicitly states.\n"
        "- Do NOT prefix a column with a table alias (e.g. T1./a.) unless you actually declared that "
        "alias with AS in a FROM/JOIN. For a single-table query, use BARE column names (write "
        "`fiscal_year`, not `T1.fiscal_year`).\n"
        "- Column ALIASES must be a single snake_case word or double-quoted — never a bare "
        "multi-word alias.\n"
        "- For a metric \"by\"/\"per\"/\"for each\" <dimension>, SELECT that dimension alongside the "
        "metric and GROUP BY it, ordered sensibly.\n"
        "- A single read-only SELECT only. Never modify data.\n\n"
        f"Question: {question}\nSQL:"
    )


# ─────────────────────────────────────────────────────────────────────────────
# Phase 1a — sqlglot pre-execution validation
# ─────────────────────────────────────────────────────────────────────────────
def _schema_maps(linked: List[Dict[str, Any]],
                 views: Optional[List[Dict[str, Any]]] = None) -> Tuple[set, Dict[str, set]]:
    """Return (set of valid table/view names lowercased, {table_lower: set(col_lower)})."""
    tables: set = set()
    cols: Dict[str, set] = {}
    for t in linked:
        tn = str(t.get("table_name", "")).lower()
        tables.add(tn)
        cols[tn] = {str(c.get("sanitized", "")).lower() for c in t.get("columns", [])}
    for v in (views or []):
        vn = str(v.get("name", "")).lower()
        tables.add(vn)
        cols[vn] = {str(c).lower() for c in v.get("columns", [])}
    return tables, cols


_SQLGLOT_OK: Optional[bool] = None


def _sqlglot_ready() -> bool:
    """One-time probe that sqlglot is FULLY importable (incl. the sqlite dialect submodule, which is
    lazily imported — and which a PyInstaller build can miss). If it isn't, validation is skipped
    entirely so a packaging gap NEVER turns valid SQL into a false 'rewrite it' repair loop."""
    global _SQLGLOT_OK
    if _SQLGLOT_OK is None:
        try:
            import sqlglot
            sqlglot.parse_one("SELECT 1 FROM t", read="sqlite")  # forces the dialect import
            _SQLGLOT_OK = True
        except Exception as e:
            _SQLGLOT_OK = False
            logger.warning(f"[cursor] sqlglot unavailable ({type(e).__name__}) — SQL validation "
                           f"disabled (execution-guided repair still active)")
    return _SQLGLOT_OK


def _validate_sql(sql: str, linked: List[Dict[str, Any]],
                  views: Optional[List[Dict[str, Any]]] = None) -> Optional[str]:
    """Parse `sql` with sqlglot and check every referenced table + column exists in the linked
    schema. Returns a DIRECTED error string naming the first hallucinated identifier (for a repair
    retry), or None if the SQL is valid OR sqlglot is unavailable. Never raises, never false-rejects
    on a packaging/import problem."""
    if not _sqlglot_ready():
        return None
    import sqlglot
    from sqlglot import exp
    from sqlglot.errors import ParseError
    try:
        tree = sqlglot.parse_one(sql, read="sqlite")
    except ParseError:
        return "the SQL is not valid SQLite; rewrite it as ONE valid read-only SELECT"
    except Exception:
        return None  # any non-parse failure (import/runtime) → skip, never a false rejection
    if tree is None:
        return None
    valid_tables, valid_cols = _schema_maps(linked, views)
    alias_to_table: Dict[str, str] = {}   # alias → base table name (both lowercased)

    # Tables referenced.
    referenced: List[str] = []
    for tbl in tree.find_all(exp.Table):
        tname = (tbl.name or "").lower()
        if tbl.alias and tname:
            alias_to_table[tbl.alias.lower()] = tname
        if tname:
            referenced.append(tname)
    for tname in referenced:
        if tname and tname not in valid_tables:
            allowed = ", ".join(sorted(valid_tables))
            return (f"table '{tname}' does not exist. Use ONLY these tables: {allowed}")

    # Identifiers a BARE column may legitimately be, beyond real columns: SELECT-list aliases and
    # CTE names. Union of all real columns across the referenced tables/views is the ground truth.
    union_cols: set = set().union(*valid_cols.values()) if valid_cols else set()
    select_aliases = {a.alias.lower() for a in tree.find_all(exp.Alias) if a.alias}
    cte_names = {c.alias.lower() for c in tree.find_all(exp.CTE) if c.alias}
    benign = union_cols | select_aliases | cte_names

    for c in tree.find_all(exp.Column):
        tbl_ref = (c.table or "").lower()
        col = (c.name or "").lower()
        if not col or col == "*":
            continue
        if tbl_ref:
            # An UNDECLARED qualifier — the model wrote `T1.col` but never `... AS T1` (a frequent
            # gemma slip) — resolves to no table/alias/CTE. Reject with a directed fix.
            if (tbl_ref not in alias_to_table and tbl_ref not in valid_tables
                    and tbl_ref not in cte_names):
                return (f"'{tbl_ref}' is not a declared table or alias — you used '{tbl_ref}.{col}' "
                        f"without writing 'AS {tbl_ref}'. Either declare the alias in FROM/JOIN or "
                        f"use bare column names.")
            base = alias_to_table.get(tbl_ref, tbl_ref)  # resolve an alias to its base table
            if base in valid_cols and col not in valid_cols[base]:
                allowed = ", ".join(sorted(valid_cols[base])) or "(none listed)"
                return (f"column '{col}' does not exist on table '{base}'. "
                        f"Columns on '{base}' are: {allowed}")
            # base not a known table (e.g. a CTE alias) → can't validate cheaply; skip.
        else:
            # Unqualified column — catches the classic COUNT(id) hallucination when no table has it.
            if union_cols and col not in benign:
                allowed = ", ".join(sorted(union_cols))
                return (f"column '{col}' does not exist in any listed table (to count rows use "
                        f"COUNT(*)). Available columns: {allowed}")
    return None


# ─────────────────────────────────────────────────────────────────────────────
# Main entry
# ─────────────────────────────────────────────────────────────────────────────
async def answer(
    notebook_id: str,
    question: str,
    source_ids: Optional[List[str]] = None,
    db_path: Optional[str] = None,
    governance: Optional[str] = None,
    recipes: Optional[List[Dict[str, str]]] = None,
) -> Dict[str, Any]:
    """Cursor Style text-to-SQL: governed, schema-linked, value/entity-grounded, sqlglot-validated,
    self-repairing read-only SQL over an external .db. Returns the same envelope as
    `tabular_query.answer_tabular` (ok/answer/sql/source_id/filename/columns/rows) so the caller
    falls back to vector RAG on ok=False."""
    governance = governance or ""
    schema = tabular_store.get_schema(notebook_id, source_ids)
    if not schema:
        logger.info(f"[cursor] nb={notebook_id}: no structured tables — vector RAG fallback")
        return {"ok": False, "reason": "no structured tables for this notebook"}

    # External .db path (read-only, in place) — carried on the catalog rows.
    if db_path is None:
        db_path = next((t["db_path"] for t in schema if t.get("db_path")), None)

    # Owner-authored canonical VIEWs (if any) are materialized as TEMP views in the read-only
    # external connection at query time — they pre-join the canonical relationships so the model
    # can query a simple view instead of writing joins. Inert (view_ddls=None) when none authored.
    views = tabular_store.get_views(notebook_id) if hasattr(tabular_store, "get_views") else []
    view_ddls = (tabular_store.get_view_ddls(notebook_id)
                 if views and hasattr(tabular_store, "get_view_ddls") else None)

    relationships = (tabular_store.get_relationships(notebook_id)
                     if hasattr(tabular_store, "get_relationships") else [])

    # Schema-link a wide DB down to the tables relevant to the question.
    linked = select_relevant_tables(question, schema, governance, max_tables=_MAX_LINKED_TABLES)
    if len(linked) < len(schema):
        logger.info(f"[cursor] nb={notebook_id} schema-linked {len(schema)}→{len(linked)} tables: "
                    f"{[t.get('table_name') for t in linked]}")

    directives = _resolve_directives(question, linked)          # low-cardinality (shared resolver)
    entity_hints = _resolve_entities(question, schema, db_path)  # high-cardinality, FULL schema
    if entity_hints:
        logger.info(f"[cursor] nb={notebook_id} grounded entities: "
                    f"{[(h['term'], h['table'] + '.' + h['col'], h['value']) for h in entity_hints]}")

    # Surface the most relevant owner-documented recipe(s) prominently for this question.
    # Recipes come pre-parsed from the FULL guide files (never budget-truncated); fall back to parsing
    # the governance text for older callers / notebooks.
    all_recipes = recipes if recipes is not None else _parse_recipes(governance or "")
    matched_recipes = _match_recipes(question, all_recipes)
    if matched_recipes:
        logger.info(f"[cursor] nb={notebook_id} matched recipes: "
                    f"{[r['request'] for r in matched_recipes]}")

    prompt = _build_cursor_prompt(question, linked, directives, entity_hints,
                                  relationships, governance, views, matched_recipes)
    logger.info(f"[cursor] nb={notebook_id} route=text-to-SQL db={db_path} "
                f"tables={len(linked)}/{len(schema)} fks={len(relationships)} views={len(views)} "
                f"recipes={len(matched_recipes)} governance={len(governance)} chars "
                f"prompt={len(prompt)} chars — generating SQL")

    primary = settings.tabular_sql_model or settings.ollama_model
    fast = settings.ollama_fast_model

    async def _generate(p: str) -> Optional[str]:
        s = await _gen_sql(p, primary, 25.0, keep_alive=_SQL_MODEL_KEEP_ALIVE)
        if s is None and primary != fast:
            logger.info(f"[cursor] nb={notebook_id} primary ({primary}) failed -> retry {fast}")
            s = await _gen_sql(p, fast, 20.0, keep_alive=_SQL_MODEL_KEEP_ALIVE)
        return s

    sql = await _generate(prompt)
    if sql is None:
        logger.warning(f"[cursor] nb={notebook_id} SQL generation FAILED (both models) — RAG fallback")
        return {"ok": False, "reason": "sql generation failed"}

    if directives:
        sql = _apply_directives_to_sql(sql, directives)
    logger.info(f"[cursor] nb={notebook_id} SQL: {sql}")

    # Phase 1a — validate BEFORE the (~15s) execution; a directed error repairs far better than a
    # replayed prompt. One validation-repair round, then execution-guided repair below.
    verr = _validate_sql(sql, linked, views)
    if verr:
        logger.info(f"[cursor] nb={notebook_id} sqlglot rejected SQL: {verr} — repairing")
        repair = (prompt + f"\n\nYOUR PREVIOUS SQL was invalid:\n{sql}\nProblem: {verr}\n"
                  "Rewrite it as ONE valid SQLite SELECT using ONLY the listed tables/columns. "
                  "Output ONLY the corrected SQL.")
        sql2 = await _generate(repair)
        if sql2:
            if directives:
                sql2 = _apply_directives_to_sql(sql2, directives)
            if not _validate_sql(sql2, linked, views):
                logger.info(f"[cursor] nb={notebook_id} validation-repaired SQL: {sql2}")
                sql = sql2

    exec_res = tabular_store.execute_readonly(sql, db_path=db_path, temp_views=view_ddls)
    # Execution-guided self-repair — feed the exact SQLite error back, retry once.
    if not exec_res.get("ok"):
        err = exec_res.get("error", "")
        logger.warning(f"[cursor] nb={notebook_id} execution FAILED: {err} — retrying with feedback")
        repair = (prompt + f"\n\nATTEMPT 1 SQL (it FAILED):\n{sql}\nSQLite error: {err}\n"
                  "Rewrite the SQL to fix that error. Use ONLY the exact table + column names listed "
                  "above (do not invent columns like 'id'). To count rows use COUNT(*). "
                  "Output ONLY the corrected SQL.")
        sql2 = await _generate(repair)
        if sql2:
            if directives:
                sql2 = _apply_directives_to_sql(sql2, directives)
            logger.info(f"[cursor] nb={notebook_id} repair SQL: {sql2}")
            retry = tabular_store.execute_readonly(sql2, db_path=db_path, temp_views=view_ddls)
            if retry.get("ok"):
                sql, exec_res = sql2, retry
        if not exec_res.get("ok"):
            logger.warning(f"[cursor] nb={notebook_id} repair also failed "
                           f"({exec_res.get('error')}) — vector RAG fallback")
            return {"ok": False, "reason": exec_res.get("error", "execution error")}

    rows = exec_res.get("rows", [])
    cols = exec_res.get("columns", [])
    logger.info(f"[cursor] nb={notebook_id} executed OK rows={len(rows)}")

    filename = schema[0]["filename"]
    source_id = schema[0]["source_id"]
    answer_md = _render_answer(question, sql, filename, exec_res) + _maybe_chart(question, cols, rows)

    return {
        "ok": True,
        "answer": answer_md,
        "sql": sql,
        "source_id": source_id,
        "filename": filename,
        "columns": cols,
        "rows": rows,
    }
