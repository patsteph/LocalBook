"""Cursor Recipe Templates (Canvas/Cursor 2.2.0) — DETERMINISTIC, validated SQL for known questions.

Free-form LLM SQL over a wide, many-object schema is a guess: a small model flip-flops which key is
the owner, stuffs display names into id columns, and drops standard scope filters — differently every
run, and slowly (~20s generation + a model reload each time). For a FIXED schema + a KNOWN set of
questions (the notebook's documented routing rules), the right answer is to NOT generate: match the
question to an owner-authored, pre-validated **template**, fill its parameters, and execute. Correct by
construction, and sub-second (no model call).

Templates live in the notebook's folder as `recipes.sql` (owner data, alongside `views.sql`). Format —
a directive comment block per recipe, then the parameterized SQL:

    -- recipe: orders_for_customer
    -- when: how many orders does {customer} have; {customer}'s order count; orders for {customer}
    SELECT COUNT(*) AS order_count
    FROM orders
    WHERE customer_id = (SELECT customer_id FROM customers WHERE full_name LIKE '%' || :customer || '%')
      AND order_year = 2025 AND status = 'active';

`{param}` in a `when:` phrase becomes a captured value; `:param` in the SQL is bound safely (named
sqlite param). Matching is case-insensitive + whitespace-flexible, so lowercase questions work. If no
template matches, the caller falls back to LLM generation.

Pure parse/match (unit-tested); execution is the caller's (safe named-param binding, read-only).
"""
from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

_SAFE_SQL = re.compile(
    r"\b(insert|update|delete|drop|alter|attach|detach|pragma|replace|truncate|vacuum|reindex|"
    r"grant|revoke|create)\b", re.IGNORECASE)


def _phrase_to_regex(phrase: str) -> Optional["re.Pattern"]:
    """Turn a `when:` phrase into a case-insensitive regex. Literal words match with flexible
    whitespace; `{param}` becomes a non-greedy named capture. Punctuation is loosened."""
    phrase = phrase.strip()
    if not phrase:
        return None
    parts = re.split(r"(\{[a-zA-Z_][a-zA-Z0-9_]*\})", phrase)
    last_idx = max((i for i, p in enumerate(parts) if p.strip()), default=-1)
    out = []
    for i, p in enumerate(parts):
        m = re.fullmatch(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}", p)
        if m:
            # A TRAILING param captures to the end of the question (greedy); a param followed by more
            # literal text stays non-greedy so it stops at that literal.
            quant = "+" if i == last_idx else "+?"
            out.append(f"(?P<{m.group(1)}>.{quant})")
        elif p.strip():
            esc = re.escape(p.strip())
            esc = re.sub(r"(\\\s)+", r"\\s+", esc)
            out.append(esc)
    try:
        return re.compile(r"\b" + r"\s*".join(out), re.IGNORECASE)
    except re.error:
        return None


def parse_recipe_templates(text: str) -> List[Dict[str, Any]]:
    """Parse `recipes.sql` → [{name, patterns:[regex], sql}]. Rejects any non-SELECT SQL. Never raises."""
    recipes: List[Dict[str, Any]] = []
    try:
        # Split into blocks on `-- recipe:` markers.
        blocks = re.split(r"(?im)^\s*--\s*recipe\s*:", text or "")
        for blk in blocks[1:]:
            lines = blk.splitlines()
            name = lines[0].strip() if lines else ""
            when_phrases: List[str] = []
            sql_lines: List[str] = []
            for ln in lines[1:]:
                m = re.match(r"\s*--\s*when\s*:\s*(.+)$", ln, re.IGNORECASE)
                if m:
                    when_phrases.extend(p.strip() for p in m.group(1).split(";") if p.strip())
                elif re.match(r"\s*--", ln):
                    continue  # other comment / directive
                else:
                    sql_lines.append(ln)
            sql = "\n".join(sql_lines).strip().rstrip(";").strip()
            if not sql or not re.match(r"^\s*(select|with)\b", sql, re.IGNORECASE):
                continue
            if _SAFE_SQL.search(sql):
                continue  # read-only only
            patterns = [p for p in (_phrase_to_regex(w) for w in when_phrases) if p]
            if name and patterns and sql:
                recipes.append({"name": name, "patterns": patterns, "sql": sql})
    except Exception as e:
        logger.debug(f"[cursor_recipes] parse skipped: {type(e).__name__}: {e}")
    return recipes


def match(question: str, recipes: List[Dict[str, Any]]) -> Optional[Tuple[str, Dict[str, str], str]]:
    """Match `question` to a recipe → (sql, {param: value}, recipe_name), or None. The longest
    parameter capture wins ties (a more specific phrase). Params are trimmed of trailing filler."""
    q = (question or "").strip()
    best: Optional[Tuple[int, str, Dict[str, str], str]] = None
    for r in recipes:
        for pat in r["patterns"]:
            m = pat.search(q)
            if not m:
                continue
            params = {k: _clean_param(v) for k, v in (m.groupdict() or {}).items() if v}
            # score: prefer matches that captured params + covered more of the question
            score = (m.end() - m.start()) + 5 * len(params)
            if best is None or score > best[0]:
                best = (score, r["sql"], params, r["name"])
    if best:
        return best[1], best[2], best[3]
    return None


_PARAM_TAIL = re.compile(
    r"\b(own|owns|have|has|manage|manages|handle|handles|cover|covers|in|for|during|"
    r"this|the|fy\d*|year|please|now|currently)\b.*$", re.IGNORECASE)


def _clean_param(v: str) -> str:
    v = (v or "").strip().strip("?.!,'\"")
    v = _PARAM_TAIL.sub("", v).strip()   # drop a trailing verb/filler the greedy capture grabbed
    return v.strip("?.!,'\" ")
