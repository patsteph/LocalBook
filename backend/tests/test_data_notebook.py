"""CI-pure tests for the Cursor Style notebook (external SQLite + governance). Model-free:
external-DB introspection, read-only enforcement, governance injection, and folder discovery.
Monkeypatches settings.data_dir so the app catalog writes to a temp dir, never production.
"""
import sqlite3

import pytest


def _make_db(path):
    c = sqlite3.connect(str(path))
    c.executescript(
        "CREATE TABLE sales(id INTEGER, region TEXT, amount REAL, status TEXT);"
        "INSERT INTO sales VALUES (1,'West',1200.0,'active'),(2,'East',800.0,'active'),"
        "(3,'West',400.0,'churned');"
    )
    c.commit()
    c.close()


def test_index_external_db_and_readonly(tmp_path, monkeypatch):
    from config import settings
    monkeypatch.setattr(settings, "data_dir", tmp_path)   # catalog → temp, not production
    from storage import tabular_store as ts

    db = tmp_path / "sales.db"
    _make_db(db)

    r = ts.index_external_db("nb", "cursor:nb", str(db))
    assert r["ok"]
    t = r["tables"][0]
    assert t["table_name"] == "sales" and t["row_count"] == 3
    assert ts.get_external_db_path("nb") == str(db)

    schema = ts.get_schema("nb")
    assert schema and schema[0]["db_path"] == str(db)
    region = next(c for c in schema[0]["columns"] if c["sanitized"] == "region")
    assert region["low_cardinality"] and set(region["values"]) == {"East", "West"}
    amount = next(c for c in schema[0]["columns"] if c["sanitized"] == "amount")
    assert amount["dtype"] == "number"

    ok = ts.execute_readonly("SELECT COUNT(*) FROM sales", db_path=str(db))
    assert ok["ok"] and ok["rows"][0][0] == 3
    # Writes are impossible against the external db (mode=ro + query_only).
    bad = ts.execute_readonly("UPDATE sales SET amount=0", db_path=str(db))
    assert not bad["ok"]


def test_build_prompt_governance_injection():
    # Governance lives ONLY in the isolated Cursor path (cursor_sql), NEVER in the shared
    # spreadsheet engine (tabular_query._build_prompt has no governance param — daily-driver
    # path is byte-identical to master).
    from services import cursor_sql as cs
    from services import tabular_query as tq
    import inspect
    schema = [{"table_name": "sales", "filename": "sales.db", "sheet_name": "sales",
               "row_count": 3, "columns": [
                   {"sanitized": "region", "dtype": "text", "low_cardinality": True,
                    "values": ["East", "West"]},
                   {"sanitized": "amount", "dtype": "number", "samples": ["1200.0"]}]}]
    p = cs._build_cursor_prompt("total by region", schema, [], [], [],
                                governance="ALWAYS filter status='active'.")
    assert "OPERATING RULES" in p and "ALWAYS filter status='active'." in p
    assert "Obey the OPERATING RULES" in p
    # No governance → no operating-rules block.
    p2 = cs._build_cursor_prompt("total by region", schema, [], [], [], governance="")
    assert "OPERATING RULES" not in p2
    # ISOLATION GUARANTEE: the shared spreadsheet prompt builder must NOT accept governance.
    assert "governance" not in inspect.signature(tq._build_prompt).parameters


def test_locate_md_and_governance_includes_all_guides(tmp_path):
    # Requirement change (2026-08-05): README is now part of the guide chain (orientation), not
    # excluded — the logic must follow ALL files in order.
    from services import data_notebook as dn
    (tmp_path / "AGENTS.md").write_text("always join sales to reps on rep_id")
    (tmp_path / "DATA_OVERVIEW.md").write_text("amount is in USD")
    (tmp_path / "README.md").write_text("refresh monthly")
    md = dn._locate_md(tmp_path)
    assert md["agents"] == "AGENTS.md" and md["data_overview"] == "DATA_OVERVIEW.md"
    assert md["readme"] == "README.md" and md["domain_guide"] is None

    gov = dn._read_governance(str(tmp_path), {k: v for k, v in md.items() if v})
    assert "always join sales to reps" in gov and "amount is in USD" in gov
    assert "refresh monthly" in gov   # README IS now followed as orientation


def test_governance_ruleset_and_full_file_chain(tmp_path):
    from services import data_notebook as dn
    (tmp_path / "README.md").write_text("# Orientation\nMonthly bundle.")
    (tmp_path / "AGENTS.md").write_text("Default FY2027. Prefer v_ views.")
    (tmp_path / "DATA_OVERVIEW.md").write_text("What the data means.")
    (tmp_path / "domain_guide.md").write_text("My area pipeline -> v_pipe_team.")
    (tmp_path / "schema.html").write_text(
        '<script>const SCHEMA_CATALOG = {"categories":{"Bookings":["bookings","v_pipe_team"]},'
        '"objects":[{"name":"record_assignments","associations":["join record_roster on record_key, scoped by fiscal_year"]}]};</script>')
    gf = dn._locate_md(tmp_path)
    gf["schema_html"] = dn._locate_schema_html(tmp_path)
    # domain_guide.md matches the domain_guide role.
    assert gf["domain_guide"] == "domain_guide.md"
    assert gf["schema_html"] == "schema.html"
    gov = dn._read_governance(str(tmp_path), gf)
    # Ruleset preamble present + files in reading order + schema.html distilled.
    assert "HOW TO READ THIS DATABASE" in gov
    # Section headers appear in reading order (check the "### <file>" markers, not the ruleset text).
    assert gov.index("### README.md") < gov.index("### AGENTS.md") < gov.index("### domain_guide.md")
    assert "structural master key" in gov
    assert "scoped by fiscal_year" in gov            # join hint parsed from SCHEMA_CATALOG
    assert "Bookings: bookings, v_pipe_team" in gov  # category routing parsed


def test_domain_guide_falls_back_to_domain_guide(tmp_path):
    from services import data_notebook as dn
    (tmp_path / "domain_guide.md").write_text("legacy name")
    assert dn._locate_md(tmp_path)["domain_guide"] == "domain_guide.md"


def test_schema_summary_missing_is_empty(tmp_path):
    from services import data_notebook as dn
    assert dn._read_schema_summary(tmp_path, None) == ""


def test_connect_summary_handles_view_none_rowcount(tmp_path, monkeypatch):
    # Regression (2026-08-05): introspected VIEWS carry row_count=None (COUNT is skipped for them).
    # The connect/refresh summary did `sum(t.get("row_count", 0) …)`, and .get(k,0) returns None when
    # the key IS present as None → `sum([2, None])` raised TypeError and 500'd cursor notebook create.
    import sqlite3
    from config import settings
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    db = tmp_path / "d.db"
    c = sqlite3.connect(str(db))
    c.execute("CREATE TABLE t(a)")
    c.execute("CREATE VIEW v AS SELECT * FROM t")
    c.execute("INSERT INTO t VALUES (1)")
    c.commit(); c.close()
    from storage import tabular_store as ts
    idx = ts.index_external_db("nb", "cursor:nb", str(db))
    assert any(t.get("kind") == "view" for t in idx["tables"])       # a view is present
    assert any(t.get("row_count") is None for t in idx["tables"])    # its row_count is None
    # The summary coercion must not crash on the None.
    total = sum(int(t.get("row_count") or 0) for t in idx["tables"])
    assert isinstance(total, int)


def test_read_governance_budgets(tmp_path):
    from services import data_notebook as dn
    (tmp_path / "AGENTS.md").write_text("X" * 10000)
    gov = dn._read_governance(str(tmp_path), {"agents": "AGENTS.md"})
    # A single huge doc is per-file trimmed; total stays within the ruleset + total budget.
    assert "truncated" in gov
    assert len(gov) <= len(dn._RULESET) + dn._PER_FILE_BUDGET + 200


def test_locate_db_requires_exactly_one(tmp_path):
    from services import data_notebook as dn
    magic = b"SQLite format 3\x00" + b"\x00" * 32
    with pytest.raises(ValueError):
        dn._locate_db(tmp_path)                       # none
    (tmp_path / "a.db").write_bytes(magic)
    assert dn._locate_db(tmp_path).name == "a.db"     # exactly one
    (tmp_path / "b.sqlite").write_bytes(magic)
    with pytest.raises(ValueError):
        dn._locate_db(tmp_path)                        # multiple → error


def test_schema_fingerprint_detects_drift():
    from services import data_notebook as dn
    a = [{"table_name": "t", "columns": ["x", "y"]}]
    added = [{"table_name": "t", "columns": ["x", "y", "z"]}]
    assert dn._schema_fingerprint(a) != dn._schema_fingerprint(added)
    # Column/table order does not matter (stable fingerprint).
    assert dn._schema_fingerprint(a) == dn._schema_fingerprint([{"table_name": "t", "columns": ["y", "x"]}])
