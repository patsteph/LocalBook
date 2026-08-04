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
    # Direction-agnostic (edges are stars to a home table): compare unordered endpoint pairs.
    edges = {frozenset([f"{r['from_table']}.{r['from_col']}", f"{r['to_table']}.{r['to_col']}"])
             for r in rels}
    assert frozenset(["record_roster.owner_id", "person_roster.owner_id"]) in edges       # Jordan Lee
    assert frozenset(["record_roster.account_id", "deals.account_id"]) in edges
    # No self-joins, no generic/attribute columns (region/name/amount/rep_name) linked.
    for r in rels:
        assert r["from_table"] != r["to_table"]
        assert r["from_col"].lower() not in {"region", "account_name", "amount", "rep_name"}


def test_fk_inference_name_based_hub_key():
    # record_key is a natural HUB key (no _id suffix) shared across many tables → star to its home
    # (the PK/dimension table), and attribute *_name columns must NOT be linked.
    from storage import tabular_store as ts
    table_cols = {
        "accounts": ["record_key", "account_name", "seller_name"],
        "record_assignments": ["record_key", "employee_id"],
        "bookings": ["record_key", "amount"],
        "person_roster": ["employee_id", "rep_name"],
    }
    pks = {"accounts": ["record_key"], "person_roster": ["employee_id"]}
    rels = ts._infer_relationships(table_cols, pks, declared=[])
    edges = {frozenset([f"{r['from_table']}.{r['from_col']}", f"{r['to_table']}.{r['to_col']}"])
             for r in rels}
    assert frozenset(["record_assignments.record_key", "accounts.record_key"]) in edges
    assert frozenset(["bookings.record_key", "accounts.record_key"]) in edges
    assert frozenset(["record_assignments.employee_id", "person_roster.employee_id"]) in edges
    # Attribute/person names never seed a join.
    for r in rels:
        assert r["from_col"].lower() not in {"rep_name", "seller_name", "account_name"}
        assert r["to_col"].lower() not in {"rep_name", "seller_name", "account_name"}


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


def test_entity_candidates_rejects_acronyms_and_single_tokens():
    # Regression: "AE" must NOT be grounded (it substring-matched employee_id='rlee' in the field).
    from services import cursor_sql as cs
    assert cs._candidate_entities("Who is the AE for the West region?") == []
    assert cs._candidate_entities("total bookings by region") == []
    # Multi-word names + quoted phrases still resolve.
    assert "Sam Rivera" in cs._candidate_entities("bookings for Sam Rivera in Q1")
    assert "Acme Corp" in cs._candidate_entities('accounts for "Acme Corp"')


def test_validate_sql_never_false_rejects_when_sqlglot_probe_fails(monkeypatch):
    # If sqlglot can't fully load (e.g. a packaging gap), validation must SKIP, never reject valid SQL.
    from services import cursor_sql as cs
    monkeypatch.setattr(cs, "_sqlglot_ready", lambda: False)
    assert cs._validate_sql("SELECT COUNT(id) FROM record_roster", _schema()) is None


def test_validate_rejects_undeclared_alias_prefix():
    # Regression (log queries 5 & 6): `T1.col` with no `AS T1` → "no such column" at runtime.
    from services import cursor_sql as cs
    err = cs._validate_sql("SELECT T1.account_id FROM record_roster WHERE T1.account_id = 1", _schema())
    assert err and "t1" in err.lower()
    assert cs._validate_sql("SELECT a.account_id FROM record_roster AS a", _schema()) is None
    assert cs._validate_sql("SELECT account_id FROM record_roster", _schema()) is None


def test_lookup_rejects_placeholder_values():
    from services import cursor_sql as cs
    assert cs._PLACEHOLDER_VALUE.match("TBH - Region West")
    assert cs._PLACEHOLDER_VALUE.match("Unassigned AE")
    assert cs._PLACEHOLDER_VALUE.match("N/A")
    assert not cs._PLACEHOLDER_VALUE.match("Jordan Lee")


def test_recipe_parse_and_match():
    from services import cursor_sql as cs
    gov = (
        "# Rules\nConfirm snapshot date.\n\n"
        "## Typical user requests (how to respond)\n\n"
        "| Request | Approach |\n"
        "|---------|----------|\n"
        "| \"My area's pipeline\" | Filter by `v_records.area` (FY27) or rollup `v_pipe_team` |\n"
        "| \"Pipeline for unassigned accounts\" | `v_pipe_account`; `owner_id = 'unassigned'` |\n"
        "| \"FY27 account changes\" | `v_records` FY26 vs FY27 diff on `record_key` |\n\n"
        "## Other\nnotes\n"
    )
    recipes = cs._parse_recipes(gov)
    assert len(recipes) == 3
    assert recipes[0]["request"] == "My area's pipeline"
    assert "v_records.area" in recipes[0]["approach"]
    # Relevant recipe is retrieved for a paraphrased question…
    m = cs._match_recipes("what is the pipeline for my area?", recipes)
    assert m and m[0]["request"] == "My area's pipeline"
    m2 = cs._match_recipes("show unassigned account pipeline", recipes)
    assert any(r["request"] == "Pipeline for unassigned accounts" for r in m2)
    # …and an unrelated question matches nothing (no noise injected).
    assert cs._match_recipes("how many bookings total", recipes) == []


def test_recipe_parse_handles_no_table():
    from services import cursor_sql as cs
    assert cs._parse_recipes("just prose, no recipe table here") == []
    assert cs._parse_recipes("") == []


def test_entities_skip_low_cardinality_dimension_values(sample_db, monkeypatch):
    # Regression (log query 6): "Region WEST" is an AREA value, not a person — must not be grounded to
    # a name column (it matched a placeholder "TBH - Region West"). A term equal to a low-card value is
    # dropped before any lookup.
    from services import cursor_sql as cs
    _, ext = sample_db
    schema = [
        {"table_name": "record_roster", "columns": [
            {"sanitized": "area", "low_cardinality": True, "values": ["Region WEST", "Region EAST"]},
            {"sanitized": "record_key", "low_cardinality": False, "dtype": "text"}]},
    ]
    # If any lookup were attempted it would raise (no such table); the low-value skip prevents it.
    monkeypatch.setattr(cs, "_lookup_value", lambda *a, **k: (_ for _ in ()).throw(AssertionError("should not lookup")))
    assert cs._resolve_entities("pipeline for Region WEST", schema, str(ext)) == []


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
