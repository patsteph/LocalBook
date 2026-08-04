"""CI-pure tests for the dedicated Cursor Style SQL engine (services/cursor_sql.py) + its
Phase 1/2/3 accuracy levers. Model-free — every test exercises deterministic code paths
(FK inference, entity extraction, sqlglot validation, temp-view materialization) and the
ISOLATION guarantee that none of it leaks into the shared spreadsheet engine.

Hard rule honored: settings.data_dir is redirected to a tmp dir so nothing touches production.
"""
import sqlite3
from pathlib import Path

import pytest

from config import settings


@pytest.fixture()
def sample_db(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    ext = tmp_path / "sales_2026_08.db"
    c = sqlite3.connect(str(ext))
    c.execute("CREATE TABLE person_roster (owner_id TEXT, rep_name TEXT, manager_id TEXT)")
    c.execute("CREATE TABLE record_roster (account_id TEXT, owner_id TEXT, account_name TEXT, region TEXT)")
    c.execute("CREATE TABLE deals (deal_id TEXT, account_id TEXT, amount REAL, stage TEXT)")
    c.executemany("INSERT INTO person_roster VALUES (?,?,?)",
                  [("rep001", "Jordan Lee", "m01"), ("rep002", "Jane Smith", "m01")])
    c.executemany("INSERT INTO record_roster VALUES (?,?,?,?)",
                  [("ac1", "rep001", "Acme", "West"), ("ac2", "rep001", "Beta", "West"),
                   ("ac3", "rep002", "Gamma", "East")])
    c.executemany("INSERT INTO deals VALUES (?,?,?,?)",
                  [("d1", "ac1", 1000.0, "won"), ("d2", "ac2", 2000.0, "open"),
                   ("d3", "ac3", 500.0, "won")])
    c.commit()
    c.close()
    return tmp_path, ext


# ── Phase 2 — FK / join-graph inference ────────────────────────────────────────
def test_fk_inference_shared_key_and_name_id():
    from storage import tabular_store as ts
    table_cols = {
        "record_roster": ["account_id", "owner_id", "account_name", "region"],
        "person_roster": ["owner_id", "rep_name", "manager_id"],
        "deals": ["deal_id", "account_id", "amount"],
        "managers": ["manager_id", "manager_name"],
    }
    pks = {"record_roster": ["account_id"], "person_roster": ["owner_id"],
           "deals": ["deal_id"], "managers": ["manager_id"]}
    rels = ts._infer_relationships(table_cols, pks, declared=[])
    pairs = {(r["from_table"], r["from_col"], r["to_table"], r["to_col"]) for r in rels}
    # The Jordan Lee join must be inferred.
    assert ("record_roster", "owner_id", "person_roster", "owner_id") in pairs
    assert ("record_roster", "account_id", "deals", "account_id") in pairs
    # No self-joins, no generic columns (region/name/amount) linked.
    for r in rels:
        assert r["from_table"] != r["to_table"]
        assert r["from_col"].lower() not in {"region", "account_name", "amount", "rep_name"}


def test_fk_inference_persisted_on_index(sample_db):
    _, ext = sample_db
    from storage import tabular_store as ts
    idx = ts.index_external_db("nbfk", "cursor:nbfk", str(ext))
    assert idx["ok"] and idx["relationships"] >= 2
    rels = ts.get_relationships("nbfk")
    pairs = {(r["from_table"], r["from_col"], r["to_table"], r["to_col"]) for r in rels}
    assert ("record_roster", "owner_id", "person_roster", "owner_id") in pairs


# ── Phase 1a — sqlglot pre-execution validation ────────────────────────────────
def _schema():
    return [
        {"table_name": "record_roster", "columns": [
            {"sanitized": "account_id"}, {"sanitized": "owner_id"},
            {"sanitized": "account_name"}, {"sanitized": "region"}]},
        {"table_name": "deals", "columns": [
            {"sanitized": "deal_id"}, {"sanitized": "account_id"}, {"sanitized": "amount"}]},
    ]


def test_validate_rejects_hallucinated_unqualified_column():
    from services import cursor_sql as cs
    err = cs._validate_sql("SELECT COUNT(id) FROM record_roster", _schema())
    assert err and "id" in err and "COUNT(*)" in err


def test_validate_rejects_bad_table_and_qualified_column():
    from services import cursor_sql as cs
    assert cs._validate_sql("SELECT * FROM nope", _schema())
    assert cs._validate_sql("SELECT a.bogus FROM record_roster a", _schema())


def test_validate_accepts_valid_join_and_alias():
    from services import cursor_sql as cs
    ok = cs._validate_sql(
        "SELECT a.region, SUM(d.amount) AS total FROM record_roster a "
        "JOIN deals d ON a.account_id = d.account_id GROUP BY a.region ORDER BY total DESC",
        _schema())
    assert ok is None


# ── Phase 1b — value / entity linking ─────────────────────────────────────────
def test_entity_candidates_extracts_proper_nouns():
    from services import cursor_sql as cs
    cands = cs._candidate_entities("How many accounts does Jordan Lee own in the West region?")
    assert "Jordan Lee" in cands


def test_entity_lookup_resolves_high_cardinality(sample_db):
    _, ext = sample_db
    from services import cursor_sql as cs
    assert cs._lookup_value(str(ext), "person_roster", "rep_name", "Jordan Lee") == "Jordan Lee"
    assert cs._lookup_value(str(ext), "person_roster", "rep_name", "Nobody Here") is None


# ── Phase 3 — canonical views via TEMP views ──────────────────────────────────
def test_store_and_query_canonical_view(sample_db):
    tmp, ext = sample_db
    from storage import tabular_store as ts
    from services import data_notebook as dn
    ts.index_external_db("nbv", "cursor:nbv", str(ext))
    (tmp / "views.sql").write_text(
        "CREATE VIEW v_acct_ae AS SELECT a.account_name, a.region, e.rep_name "
        "FROM record_roster a JOIN person_roster e ON a.owner_id = e.owner_id;")
    specs = dn._collect_view_specs(str(tmp), {})
    assert ts.store_views("nbv", str(ext), specs)["views"] == 1
    ddls = ts.get_view_ddls("nbv")
    assert ddls and ddls[0].upper().startswith("CREATE TEMP VIEW")
    r = ts.execute_readonly("SELECT rep_name, COUNT(*) n FROM v_acct_ae GROUP BY rep_name",
                            db_path=str(ext), temp_views=ddls)
    assert r["ok"] and sorted(r["rows"]) == [["Jane Smith", 1], ["Jordan Lee", 2]]


def test_view_collector_rejects_non_view_sql(tmp_path):
    from services import data_notebook as dn
    (tmp_path / "views.sql").write_text(
        "DELETE FROM record_roster;\nCREATE VIEW v_ok AS SELECT * FROM record_roster;")
    specs = dn._collect_view_specs(str(tmp_path), {})
    assert [s["name"] for s in specs] == ["v_ok"]  # the DELETE is dropped


# ── SECURITY — the external .db is never writable ─────────────────────────────
def test_external_db_write_blocked_even_with_temp_views(sample_db):
    _, ext = sample_db
    from storage import tabular_store as ts
    r = ts.execute_readonly("SELECT COUNT(*) FROM record_roster", db_path=str(ext),
                            temp_views=["CREATE TEMP VIEW z AS SELECT 1"])
    assert r["ok"] and r["rows"] == [[3]]
    # A write must still be rejected on the mode=ro handle.
    from pathlib import Path as _P
    conn = ts._connect_path(_P(str(ext)), read_only=True, query_only=False)
    with pytest.raises(sqlite3.OperationalError):
        conn.execute("INSERT INTO record_roster VALUES ('x','y','z','w')")
    conn.close()


# ── ISOLATION — none of the cursor logic leaks into the shared spreadsheet path ─
def test_shared_tabular_path_is_isolated():
    import inspect
    from services import tabular_query as tq
    # answer_tabular keeps master's 3-arg signature (no db_path / governance).
    params = set(inspect.signature(tq.answer_tabular).parameters)
    assert params == {"notebook_id", "question", "source_ids"}
    # _build_prompt takes no governance; spreadsheet value cap stays at the original 60.
    assert "governance" not in inspect.signature(tq._build_prompt).parameters
    assert tq._MAX_PROMPT_VALUES == 60
