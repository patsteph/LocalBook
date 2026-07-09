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


def _build_prompt(question: str, schema: List[Dict[str, Any]]) -> str:
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

    return (
        "Translate the user's question into ONE read-only SQLite SELECT over the tables below.\n\n"
        f"{schema_text}\n"
        "Rules:\n"
        "- Output ONLY the SQL. No explanation, no markdown fences.\n"
        "- Use exactly the table and column names shown (snake_case).\n"
        "- Map natural-language values to the EXACT values listed for a column "
        "(e.g. \"Texas\" -> state = 'TX'; \"located in Dallas\" -> city = 'Dallas'). "
        "Value matching is case-sensitive — use the listed spelling.\n"
        "- \"how many\" / \"number of\" -> SELECT COUNT(*). Totals -> SUM(...). Averages -> AVG(...).\n"
        "- A single read-only SELECT only. Never modify data.\n\n"
        f"Question: {question}\nSQL:"
    )


def _render_answer(question: str, sql: str, filename: str, result: Dict[str, Any]) -> str:
    cols = result.get("columns", [])
    rows = result.get("rows", [])
    prov = f"\n\n_Computed directly from **{filename}** — `{sql}`_"

    if not rows:
        return f"No rows matched.{prov}"

    # Scalar (single value) — the count/sum/avg case.
    if len(rows) == 1 and len(cols) == 1:
        val = rows[0][0]
        return f"**{val}**{prov}"

    # Single row, multiple columns.
    if len(rows) == 1:
        pairs = ", ".join(f"{c} = **{v}**" for c, v in zip(cols, rows[0]))
        return f"{pairs}{prov}"

    # Multi-row list/table.
    shown = rows[:_MAX_ANSWER_ROWS]
    header = "| " + " | ".join(str(c) for c in cols) + " |"
    sep = "| " + " | ".join("---" for _ in cols) + " |"
    body = "\n".join("| " + " | ".join(str(v) for v in r) + " |" for r in shown)
    more = "" if len(rows) <= _MAX_ANSWER_ROWS else f"\n\n_…and {len(rows) - _MAX_ANSWER_ROWS} more rows._"
    return f"**{len(rows)} result(s):**\n\n{header}\n{sep}\n{body}{more}{prov}"


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

    prompt = _build_prompt(question, schema)
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
