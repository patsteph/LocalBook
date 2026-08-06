"""CI-pure tests for Tier-1 constrained view-pick (Phase 3): validation against the catalog, the
mocked-model pick_view path, and _tier1_answer end-to-end (LLM picks a view → app assembles it).
Generic orders/customers schema — no deployment identifiers.
"""
import asyncio
import sqlite3

import pytest

from services import cursor_catalog as cc
from services import cursor_view_pick as vpk
from services import cursor_sql

_SYN = {
    "view_count": 1, "table_count": 2,
    "categories": [{"label": "Views", "names": ["v_orders"]}],
    "objects": [
        {"name": "v_orders", "kind": "view", "category": "Views",
         "ddl": "CREATE VIEW v_orders AS SELECT * FROM orders",
         "columns": [{"name": "order_ref", "type": "TEXT"}, {"name": "customer_id", "type": "TEXT"},
                     {"name": "region", "type": "TEXT"}, {"name": "tier", "type": "TEXT"},
                     {"name": "order_year", "type": "INTEGER"}, {"name": "amount", "type": "REAL"}],
         "foreign_keys_out": [], "logical_associations_out": []},
        {"name": "orders", "kind": "table", "category": "Other",
         "ddl": "CREATE TABLE orders (order_ref TEXT, customer_id TEXT)",
         "columns": [{"name": "order_ref", "type": "TEXT"}, {"name": "customer_id", "type": "TEXT"}],
         "foreign_keys_out": [{"from_columns": ["customer_id"], "ref_table": "customers",
                               "to_columns": ["customer_id"]}],
         "logical_associations_out": [{"from_column": "order_ref", "to_table": "orders",
                                       "to_column": "order_ref", "join_note": "grain"}]},
        {"name": "customers", "kind": "table", "category": "People & org",
         "ddl": "CREATE TABLE customers (customer_id TEXT, full_name TEXT)",
         "columns": [{"name": "customer_id", "type": "TEXT"}, {"name": "full_name", "type": "TEXT"}],
         "foreign_keys_out": [], "logical_associations_out": []},
    ],
}
_AGENTS = ("## Global defaults\n| Dimension | Default |\n|--|--|\n| Year | `order_year = 2025` |\n")


@pytest.fixture(scope="module")
def catalog():
    return cc.build_routing_catalog(_SYN, _AGENTS)


@pytest.fixture()
def db(tmp_path):
    p = tmp_path / "orders.db"
    conn = sqlite3.connect(str(p))
    conn.executescript(
        """
        CREATE TABLE customers(customer_id TEXT, full_name TEXT);
        INSERT INTO customers VALUES ('c1','Jordan Lee');
        CREATE TABLE orders(order_ref TEXT, customer_id TEXT, region TEXT, tier TEXT,
                            order_year INTEGER, amount REAL);
        INSERT INTO orders VALUES ('o1','c1','West','Gold',2025,10),('o2','c1','East','Gold',2025,20),
                                  ('o3','c1','West','Gold',2024,30);
        CREATE VIEW v_orders AS SELECT * FROM orders;
        """
    )
    conn.commit()
    conn.close()
    return str(p)


_SCHEMA = [{"filename": "orders.db", "source_id": "cursor:nb", "columns": []}]


# ── validation (pure) ───────────────────────────────────────────────────────────
def test_validate_keeps_only_catalog_supported(catalog):
    ok = vpk._validate({"view": "v_orders", "group_by": "region",
                        "filters": [{"col": "tier", "value": "Gold"},
                                    {"col": "not_a_col", "value": "x"}],
                        "person": "Jordan Lee"}, catalog)
    assert ok["view"] == "v_orders" and ok["group_by"] == "region"
    assert ok["filters"] == [{"col": "tier", "value": "Gold"}]      # bogus column dropped
    assert ok["person"] == "Jordan Lee"


def test_validate_rejects_unknown_view(catalog):
    assert vpk._validate({"view": "v_nope"}, catalog) is None
    assert vpk._validate({"view": "v_orders", "group_by": "not_a_dim", "person": "null"},
                         catalog)["group_by"] is None


def test_pick_view_parses_model_json(monkeypatch, catalog):
    async def _fake_gen(**kw):
        return {"response": 'noise... {"view":"v_orders","group_by":"region","filters":[],"person":null} tail'}
    monkeypatch.setattr("services.ollama_service.ollama_service.generate", _fake_gen)
    pick = asyncio.run(vpk.pick_view("orders grouped somehow", catalog, "m"))
    assert pick["view"] == "v_orders" and pick["group_by"] == "region" and pick["person"] is None


def test_pick_view_none_on_garbage(monkeypatch, catalog):
    async def _fake_gen(**kw):
        return {"response": "I cannot help with that."}
    monkeypatch.setattr("services.ollama_service.ollama_service.generate", _fake_gen)
    assert asyncio.run(vpk.pick_view("q", catalog, "m")) is None


# ── _tier1_answer end-to-end (LLM pick → app assembles) ─────────────────────────
def test_tier1_answer_group_by(monkeypatch, catalog, db):
    monkeypatch.setattr("storage.cursor_catalog_store.get_catalog", lambda nb: catalog)

    async def _fake_pick(question, cat, model, timeout=25.0):
        return {"view": "v_orders", "group_by": "region", "filters": [], "person": None}
    monkeypatch.setattr("services.cursor_view_pick.pick_view", _fake_pick)

    r = asyncio.run(cursor_sql._tier1_answer("nb", "some paraphrase", _SCHEMA, db))
    assert r and r["ok"]
    counts = {row[0]: row[1] for row in r["rows"]}
    assert counts == {"West": 1, "East": 1}       # 2025 only (order_year default), grouped by region


def test_tier1_answer_person(monkeypatch, catalog, db):
    monkeypatch.setattr("storage.cursor_catalog_store.get_catalog", lambda nb: catalog)

    async def _fake_pick(question, cat, model, timeout=25.0):
        return {"view": "v_orders", "group_by": None, "filters": [], "person": "Jordan Lee"}
    monkeypatch.setattr("services.cursor_view_pick.pick_view", _fake_pick)

    r = asyncio.run(cursor_sql._tier1_answer("nb", "orders for that person", _SCHEMA, db))
    assert r and r["ok"] and r["rows"][0][0] == 2   # Jordan Lee, 2025 (o1,o2); o3 is 2024
    assert "customer_id" in r["sql"]
