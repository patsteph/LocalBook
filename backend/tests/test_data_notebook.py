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
    from services import tabular_query as tq
    schema = [{"table_name": "sales", "filename": "sales.db", "sheet_name": "sales",
               "row_count": 3, "columns": [
                   {"sanitized": "region", "dtype": "text", "low_cardinality": True,
                    "values": ["East", "West"]},
                   {"sanitized": "amount", "dtype": "number", "samples": ["1200.0"]}]}]
    p = tq._build_prompt("total by region", schema, [], governance="ALWAYS filter status='active'.")
    assert "OPERATING RULES" in p and "ALWAYS filter status='active'." in p
    assert "Obey the OPERATING RULES" in p
    # No governance → no operating-rules block (byte-identical shipping path).
    p2 = tq._build_prompt("total by region", schema, [])
    assert "OPERATING RULES" not in p2


def test_locate_md_and_governance_excludes_readme(tmp_path):
    from services import data_notebook as dn
    (tmp_path / "AGENTS.md").write_text("always join sales to reps on rep_id")
    (tmp_path / "DATA_OVERVIEW.md").write_text("amount is in USD")
    (tmp_path / "README.md").write_text("refresh monthly")
    md = dn._locate_md(tmp_path)
    assert md["agents"] == "AGENTS.md" and md["data_overview"] == "DATA_OVERVIEW.md"
    assert md["readme"] == "README.md" and md["domain_guide"] is None

    gov = dn._read_governance(str(tmp_path), {k: v for k, v in md.items() if v})
    assert "always join sales to reps" in gov and "amount is in USD" in gov
    assert "refresh monthly" not in gov   # README is RAG-only, not authoritative governance


def test_read_governance_budgets(tmp_path):
    from services import data_notebook as dn
    (tmp_path / "AGENTS.md").write_text("X" * 10000)
    gov = dn._read_governance(str(tmp_path), {"agents": "AGENTS.md"})
    assert len(gov) <= dn._PER_FILE_BUDGET + 200 and "truncated" in gov


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
