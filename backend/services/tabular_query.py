"""
Tabular Query — local text-to-SQL over the structured tabular store.

Given a natural-language question + the catalog schema (columns, types, and distinct
values for low-cardinality text columns), a local LLM writes ONE read-only SQLite SELECT.
We validate it (SELECT-only, no DML/DDL), execute it read-only, and format a deterministic
answer — the number always comes from SQL, never from the LLM.

This is the path that makes "how many accounts are located in Dallas, Texas" return the
exact count. Only reached for tabular sources + aggregate intent (see source_router +
rag_engine hook); any failure returns ok=False so the caller falls back to vector RAG.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from config import settings
from services.ollama_service import ollama_service
from storage import tabular_store

# Words that must never appear in generated SQL (single read-only SELECT only).
_FORBIDDEN = re.compile(
    r"\b(insert|update|delete|drop|alter|create|attach|detach|pragma|replace|"
    r"truncate|vacuum|reindex|grant|revoke|commit|rollback|begin)\b",
    re.IGNORECASE,
)
_SQL_FENCE = re.compile(r"```(?:sql)?\s*(.*?)```", re.IGNORECASE | re.DOTALL)
# How many low-cardinality values to show per column in the prompt (accuracy lever).
_MAX_PROMPT_VALUES = 60
# Rows rendered in a list/table answer.
_MAX_ANSWER_ROWS = 50

# US state full-name <-> abbreviation, so a question can use either form regardless of
# which form the column stores (users spell states out on ingest; models tend to abbreviate).
US_STATES = {
    "alabama": "AL", "alaska": "AK", "arizona": "AZ", "arkansas": "AR", "california": "CA",
    "colorado": "CO", "connecticut": "CT", "delaware": "DE", "florida": "FL", "georgia": "GA",
    "hawaii": "HI", "idaho": "ID", "illinois": "IL", "indiana": "IN", "iowa": "IA",
    "kansas": "KS", "kentucky": "KY", "louisiana": "LA", "maine": "ME", "maryland": "MD",
    "massachusetts": "MA", "michigan": "MI", "minnesota": "MN", "mississippi": "MS",
    "missouri": "MO", "montana": "MT", "nebraska": "NE", "nevada": "NV", "new hampshire": "NH",
    "new jersey": "NJ", "new mexico": "NM", "new york": "NY", "north carolina": "NC",
    "north dakota": "ND", "ohio": "OH", "oklahoma": "OK", "oregon": "OR", "pennsylvania": "PA",
    "rhode island": "RI", "south carolina": "SC", "south dakota": "SD", "tennessee": "TN",
    "texas": "TX", "utah": "UT", "vermont": "VT", "virginia": "VA", "washington": "WA",
    "west virginia": "WV", "wisconsin": "WI", "wyoming": "WY", "district of columbia": "DC",
}
_STATE_FULL = set(US_STATES.keys())
_STATE_ABBR = {v.lower() for v in US_STATES.values()}


def _state_form(values: List[str]) -> str:
    """Return 'full' / 'abbr' / '' — whether a column's values are US states, and in which form."""
    vals = [str(v).strip().lower() for v in values if str(v).strip()]
    if len(vals) < 2:
        return ""
    full_hits = sum(1 for v in vals if v in _STATE_FULL)
    abbr_hits = sum(1 for v in vals if v in _STATE_ABBR)
    if full_hits >= max(2, 0.6 * len(vals)):
        return "full"
    if abbr_hits >= max(2, 0.6 * len(vals)):
        return "abbr"
    return ""


_ABBR_TO_FULL = {v.lower(): k for k, v in US_STATES.items()}


def _resolve_states(question: str, schema: List[Dict[str, Any]]) -> List[Dict[str, str]]:
    """Deterministically resolve US-state mentions in the question to the EXACT value
    stored in a state column, regardless of whether the user or the data used the full
    name or the abbreviation. Returns [{col, value, term}] — used both to hint the prompt
    AND to post-correct the generated SQL (LLMs are unreliable at this mapping: phi4 mapped
    California→CA but not Texas→TX, so we neither ask nor trust it for a matched state).
    """
    out: List[Dict[str, str]] = []
    ql = question.lower()
    for t in schema:
        for c in t.get("columns", []):
            if not c.get("low_cardinality") or not c.get("values"):
                continue
            form = _state_form(c["values"])
            if not form:
                continue
            stored_by_lower = {str(v).strip().lower(): str(v).strip() for v in c["values"]}
            found: Dict[str, str] = {}  # stored value -> the term the user wrote

            # Full names (incl. multi-word: "new york") — case-insensitive whole-phrase.
            for full, abbr in US_STATES.items():
                if re.search(r"\b" + re.escape(full) + r"\b", ql):
                    key = full if form == "full" else abbr.lower()
                    sv = stored_by_lower.get(key.lower())
                    if sv:
                        found[sv] = full.title()
            # Abbreviations — ONLY uppercase standalone 2-letter tokens, to avoid matching
            # common words (in→IN, or→OR, me→ME) that aren't state references.
            for m in re.finditer(r"\b([A-Z]{2})\b", question):
                ab = m.group(1).lower()
                if ab in _ABBR_TO_FULL:
                    full = _ABBR_TO_FULL[ab]
                    key = full if form == "full" else ab
                    sv = stored_by_lower.get(key.lower())
                    if sv:
                        found.setdefault(sv, m.group(1))

            for sv, term in found.items():
                out.append({"col": c["sanitized"], "value": sv, "term": term})
    return out


def _apply_directives_to_sql(sql: str, directives: List[Dict[str, str]]) -> str:
    """Force the generated SQL to use the deterministically-resolved value for a resolved
    column, overriding whatever literal the model chose (handles `col = 'x'`, `col='x'`,
    and `LOWER(col) = 'x'`). This is the enforcement that makes state matching exact."""
    for d in directives:
        col, val = d["col"], d["value"].replace("'", "''")
        pattern = (
            r"(?:LOWER\s*\(\s*)?\b" + re.escape(col) + r"\b\s*\)?\s*"
            r"=\s*(?:LOWER\s*\(\s*)?'[^']*'\)?"
        )
        sql = re.sub(pattern, f"{col} = '{val}'", sql, flags=re.IGNORECASE)
    return sql


def _extract_sql(text: str) -> str:
    """Pull a single SQL statement out of an LLM response (handles ``` fences, prose)."""
    if not text:
        return ""
    m = _SQL_FENCE.search(text)
    if m:
        text = m.group(1)
    text = text.strip()
    # Grab from the first SELECT/WITH onward (drop any leading prose).
    m = re.search(r"\b(select|with)\b", text, re.IGNORECASE)
    if m:
        text = text[m.start():]
    # Keep only the first statement.
    text = text.split(";")[0].strip()
    return text


def safe_sql(raw: str) -> Dict[str, Any]:
    """Validate that `raw` is a single read-only SELECT. Returns {ok, sql|reason}."""
    sql = _extract_sql(raw)
    if not sql:
        return {"ok": False, "reason": "no SQL produced"}
    if not re.match(r"^\s*(select|with)\b", sql, re.IGNORECASE):
        return {"ok": False, "reason": "not a SELECT"}
    if _FORBIDDEN.search(sql):
        return {"ok": False, "reason": "contains a forbidden (non-read-only) keyword"}
    if ";" in sql:  # _extract_sql already split, but double-check for embedded statements
        return {"ok": False, "reason": "multiple statements"}
    return {"ok": True, "sql": sql}


def _build_prompt(question: str, schema: List[Dict[str, Any]], directives: List[Dict[str, str]]) -> str:
    lines: List[str] = []
    for t in schema:
        cols = t["columns"]
        lines.append(
            f'Table "{t["table_name"]}" (from file "{t["filename"]}", '
            f'sheet "{t["sheet_name"]}", {t["row_count"]} rows):'
        )
        for c in cols:
            desc = f'  - {c["sanitized"]} ({c["dtype"]})'
            if c.get("low_cardinality") and c.get("values"):
                vals = c["values"][:_MAX_PROMPT_VALUES]
                shown = ", ".join(str(v) for v in vals)
                more = "" if len(c["values"]) <= _MAX_PROMPT_VALUES else ", …"
                desc += f" — one of: {shown}{more}"
            elif c.get("samples"):
                desc += f' — e.g. {", ".join(str(v) for v in c["samples"])}'
            lines.append(desc)
        lines.append("")
    schema_text = "\n".join(lines)

    directive_block = ""
    if directives:
        directive_block = (
            "\nValue directives — use these EXACT filters (already resolved for you):\n"
            + "\n".join(f"- the question's \"{d['term']}\" means {d['col']} = '{d['value']}'"
                        for d in directives) + "\n"
        )

    return (
        "Translate the user's question into ONE read-only SQLite SELECT over the tables below.\n\n"
        f"{schema_text}\n"
        f"{directive_block}"
        "Rules:\n"
        "- Output ONLY the SQL. No explanation, no markdown fences.\n"
        "- Use exactly the table and column names shown (snake_case).\n"
        "- CRITICAL: filter using ONLY the EXACT values listed for a column. Do not abbreviate, "
        "expand, reword, or guess a value — copy the listed spelling verbatim "
        "(e.g. if the column lists 'Texas', write state = 'Texas', never 'TX'). Obey any value "
        "directive above verbatim.\n"
        "- For case-insensitive matching on OTHER text columns, compare LOWER(col) = LOWER('value').\n"
        "- \"how many\" / \"number of\" -> SELECT COUNT(*). Totals -> SUM(...). Averages -> AVG(...).\n"
        "- A single read-only SELECT only. Never modify data.\n\n"
        f"Question: {question}\nSQL:"
    )


def _pretty(name: str) -> str:
    """Human-friendly column header for the table (num_accounts -> Num Accounts)."""
    return str(name).replace("_", " ").strip().title()


def _cell(v: Any) -> str:
    return "" if v is None else str(v)


def _render_answer(question: str, sql: str, filename: str, result: Dict[str, Any]) -> str:
    """Clean, user-facing answer — NO SQL / "computed from" line (that lives in the
    expandable source citation). Scalars stand alone; multi-row results render as a
    GFM markdown table."""
    cols = result.get("columns", [])
    rows = result.get("rows", [])

    if not rows:
        return "No matching rows found."

    # Scalar (single value) — the count/sum/avg case.
    if len(rows) == 1 and len(cols) == 1:
        return f"**{_cell(rows[0][0])}**"

    # Single row, multiple columns.
    if len(rows) == 1:
        return ", ".join(f"{_pretty(c)}: **{_cell(v)}**" for c, v in zip(cols, rows[0]))

    # Multi-row → GFM table.
    shown = rows[:_MAX_ANSWER_ROWS]
    header = "| " + " | ".join(_pretty(c) for c in cols) + " |"
    sep = "| " + " | ".join("---" for _ in cols) + " |"
    body = "\n".join("| " + " | ".join(_cell(v) for v in r) + " |" for r in shown)
    more = "" if len(rows) <= _MAX_ANSWER_ROWS else f"\n\n_…and {len(rows) - _MAX_ANSWER_ROWS} more rows._"
    return f"{header}\n{sep}\n{body}{more}"


async def answer_tabular(
    notebook_id: str,
    question: str,
    source_ids: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Answer a question via text-to-SQL over the structured store.

    Returns {ok, answer, sql, source_id, filename, columns, rows} on success,
    or {ok: False, reason} so the caller can fall back to vector RAG.
    """
    schema = tabular_store.get_schema(notebook_id, source_ids)
    if not schema:
        return {"ok": False, "reason": "no structured tables for this notebook"}

    directives = _resolve_states(question, schema)
    prompt = _build_prompt(question, schema, directives)
    # Use the fast model (phi4) by default: it's warm + light (no 9.6GB gemma load on a
    # 16GB box) and reliable for filter/count/aggregate SQL. Loading gemma for this timed
    # out at 60s under a background-ingest flood (2026-07-09). Overridable if a complex
    # schema needs stronger SQL. Tight timeout so a contended box falls back to RAG fast.
    sql_model = settings.tabular_sql_model or settings.ollama_fast_model
    try:
        result = await ollama_service.generate(
            prompt=prompt,
            model=sql_model,
            temperature=0.1,
            num_predict=400,
            think=False,                   # no thinking tokens polluting the SQL
            timeout=25.0,
        )
        raw = (result or {}).get("response", "")
    except Exception as e:
        print(f"[tabular-sql] LLM error: {type(e).__name__}: {e}")
        return {"ok": False, "reason": f"llm error: {e}"}

    check = safe_sql(raw)
    if not check["ok"]:
        print(f"[tabular-sql] REJECTED unsafe/empty ({check['reason']}): {raw[:200]!r}")
        return {"ok": False, "reason": check["reason"]}
    sql = check["sql"]
    print(f"[tabular-sql] generated: {sql}")

    # Enforce the deterministically-resolved state values over whatever literal the model chose.
    if directives:
        corrected = _apply_directives_to_sql(sql, directives)
        if corrected != sql:
            print(f"[tabular-sql] directive-corrected: {corrected}")
            sql = corrected

    exec_res = tabular_store.execute_readonly(sql)
    if not exec_res.get("ok"):
        print(f"[tabular-sql] execution error: {exec_res.get('error')}")
        return {"ok": False, "reason": exec_res.get("error", "execution error")}

    rows = exec_res.get("rows", [])
    print(f"[tabular-sql] executed OK rows={len(rows)}"
          + (f" scalar={rows[0][0]!r}" if len(rows) == 1 and len(exec_res.get('columns', [])) == 1 else ""))

    # Provenance → first matching table's source/file.
    filename = schema[0]["filename"]
    source_id = schema[0]["source_id"]
    answer = _render_answer(question, sql, filename, exec_res)

    return {
        "ok": True,
        "answer": answer,
        "sql": sql,
        "source_id": source_id,
        "filename": filename,
        "columns": exec_res.get("columns", []),
        "rows": rows,
    }
