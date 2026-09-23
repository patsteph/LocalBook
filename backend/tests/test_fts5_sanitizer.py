"""The FTS5 MATCH expression builder must never produce unparseable SQL.

This is a regression test for a fault that ran silently for months: the
sanitizer was a DENYLIST stripping a fixed set of special characters, so every
character it did not anticipate reached the parser. `?` and `,` were not on the
list, which meant essentially every natural-language question raised
`fts5: syntax error`, memory keyword search returned {}, and hybrid recall
degraded to vector-only with nothing visible in the logs.

The tests run each expression against a REAL FTS5 table rather than asserting on
the string, because the only thing that matters is whether SQLite accepts it —
checking the shape of the text would repeat the original mistake of verifying a
step instead of the artifact.
"""
import re
import sqlite3

import pytest

from storage.memory_store import MemoryStore, _FTS_MAX_TERMS

sanitize = MemoryStore._sanitize_fts_query


@pytest.fixture
def fts():
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE VIRTUAL TABLE t USING fts5(content)")
    conn.execute(
        "INSERT INTO t VALUES "
        "('embedding model dimensions localbook NEAR state of the art café')"
    )
    yield conn
    conn.close()


# The three that actually failed in the v2.4.0 release evaluator, plus the
# punctuation that a denylist would have to enumerate to survive.
HOSTILE = [
    "What embedding model does LocalBook use and what are its dimensions?",
    "For a 16GB machine, which models fit, and why?",
    "Can you summarize that in 3 bullet points?",
    'He said "hello" -- then (left); really?!',
    "state-of-the-art",
    "NEAR AND OR NOT",
    "café naïve 日本語",
    "MATCH * ^ { } [ ] : ( )",
    "100% — really? {yes}",
    "a/b\\c|d~e`f",
    "'; DROP TABLE t; --",
    " ".join(f"word{i}" for i in range(200)),
]


@pytest.mark.parametrize("query", HOSTILE)
def test_sanitized_query_is_parseable(fts, query):
    """Whatever comes out must be something FTS5 will actually run."""
    expr = sanitize(query)
    if not expr:
        return  # legitimately nothing to search for
    fts.execute("SELECT 1 FROM t WHERE t MATCH ?", (expr,)).fetchall()


@pytest.mark.parametrize("query", ["", "   ", "?", "!!!", "🎉🎉🎉", "a"])
def test_queries_with_nothing_to_search_return_empty(query):
    """No tokens (or a single character) means no search, not a broken one."""
    assert sanitize(query) == ""


def test_operators_are_literals_not_syntax(fts):
    """Quoting each token disarms AND/OR/NOT/NEAR without stripping them.

    The old version deleted these words, so a memory that genuinely said "NEAR"
    was unfindable. Quoted, it is just a word.
    """
    rows = fts.execute(
        "SELECT 1 FROM t WHERE t MATCH ?", (sanitize("NEAR"),)
    ).fetchall()
    assert len(rows) == 1


def test_terms_are_ored_not_anded(fts):
    """FTS5 defaults to AND, under which a natural question matches nothing.

    Measured during the v2.4.0 release run: all three evaluator questions
    returned 0 hits under AND even once they parsed. BM25 ranking is built for
    OR — this is what makes the feature useful rather than merely non-crashing.
    """
    expr = sanitize("What embedding model does LocalBook use?")
    assert " OR " in expr
    rows = fts.execute("SELECT 1 FROM t WHERE t MATCH ?", (expr,)).fetchall()
    assert len(rows) == 1, "a partially-matching question should still hit"


def test_expression_is_bounded():
    """A pathological query must not reach SQLite's expression-depth limit."""
    expr = sanitize(" ".join(f"w{i}" for i in range(500)))
    assert expr.count(" OR ") + 1 == _FTS_MAX_TERMS


def test_sanitizer_is_an_allowlist_not_a_denylist():
    """The structural guarantee, asserted directly.

    A denylist is what failed here, and it fails again the moment SQLite gains
    syntax or a user types a character nobody enumerated. Only quoted \\w+ tokens
    and the OR separator may appear in the output.
    """
    noisy = "what? a,b (c) *d* ^e ~f `g| h/i \\j 100% café — 日本語 🎉"
    expr = sanitize(noisy)
    assert re.fullmatch(r'"\w+"(?: OR "\w+")*', expr), expr
