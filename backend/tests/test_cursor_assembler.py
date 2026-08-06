"""CI-pure tests for Tier-0 (Phase 2): view/route selection, slot extraction, SQL assembly, and the
end-to-end deterministic answer executed against a tiny fixture .db. Generic orders/customers schema —
no deployment identifiers.
"""
import sqlite3

import pytest

from services import cursor_catalog as cc
from services import cursor_assembler as ca
from services import cursor_sql

# ── a SCHEMA_CATALOG-shaped dict matching the fixture .db below ──────────────────
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
_AGENTS = """
## Typical user requests
| Request | Approach |
|---------|----------|
| How many orders does {person} have | Use v_orders filtered by customer_id |
| Orders by region | Use v_orders grouped by region |
| How many orders are there | Use v_orders |

## Global defaults
| Dimension | Default |
|-----------|---------|
| Planning year | `order_year = 2025` |
"""


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
        INSERT INTO customers VALUES ('c1','Jordan Lee'),('c2','Sam Rivera');
        CREATE TABLE orders(order_ref TEXT, customer_id TEXT, region TEXT, tier TEXT,
                            order_year INTEGER, amount REAL);
        INSERT INTO orders VALUES
          ('o1','c1','West','Gold',2025,10),
          ('o2','c1','West','Gold',2025,20),
          ('o3','c2','East','Silver',2025,30),
          ('o4','c1','East','Gold',2024,40);   -- 2024 → excluded by the order_year=2025 default
        CREATE VIEW v_orders AS SELECT * FROM orders;
        """
    )
    conn.commit()
    conn.close()
    return str(p)


_SCHEMA = [{"filename": "orders.db", "source_id": "cursor:nb",
            "columns": [{"sanitized": "region", "low_cardinality": True, "values": ["West", "East"]},
                        {"sanitized": "tier", "low_cardinality": True, "values": ["Gold", "Silver"]}]}]


# ── selection + slots (pure) ────────────────────────────────────────────────────
def test_select_target_routes_to_view(catalog):
    assert cc.select_target("how many orders does Jordan Lee have", catalog)[0] == "v_orders"
    assert cc.select_target("orders by region", catalog)[0] == "v_orders"


def test_extract_slots_person_and_role(catalog):
    vp = catalog["views"]["v_orders"]
    s = ca.extract_slots("how many orders does Jordan Lee have", vp)
    assert s["person"] == "Jordan Lee"
    assert s["role_key"] == "customer_id"        # the person key, never a record id
    assert s["group_by"] is None


def test_extract_slots_group_by_and_dim(catalog):
    vp = catalog["views"]["v_orders"]
    low = {"region": ["West", "East"]}
    assert ca.extract_slots("orders by region", vp, low)["group_by"] == "region"
    assert ca.extract_slots("how many orders in West", vp, low)["dim_filters"] == {"region": "West"}


# ── end-to-end deterministic answers (fixture .db) ──────────────────────────────
def _tier0(monkeypatch, catalog, db, question):
    monkeypatch.setattr("storage.cursor_catalog_store.get_catalog", lambda nb: catalog)
    return cursor_sql._tier0_answer("nb", question, _SCHEMA, db)


def test_person_count_end_to_end(monkeypatch, catalog, db):
    r = _tier0(monkeypatch, catalog, db, "how many orders does Jordan Lee have")
    assert r and r["ok"]
    assert r["rows"][0][0] == 2                   # o1,o2 (2025); o4 is 2024, excluded
    assert "customer_id" in r["sql"] and "order_year" in r["sql"]


def test_group_by_end_to_end(monkeypatch, catalog, db):
    r = _tier0(monkeypatch, catalog, db, "orders by region")
    assert r and r["ok"]
    counts = {row[0]: row[1] for row in r["rows"]}
    assert counts == {"West": 2, "East": 1}       # 2025 only


def test_total_count_end_to_end(monkeypatch, catalog, db):
    r = _tier0(monkeypatch, catalog, db, "how many orders are there")
    assert r and r["ok"] and r["rows"][0][0] == 3


def test_dim_filter_direct(db):
    # terse dimension-filter routing is Phase 3's job; assemble + execute directly here
    cat = cc.build_routing_catalog(_SYN, _AGENTS)
    vp = cat["views"]["v_orders"]
    slots = ca.extract_slots("how many orders in West", vp, {"region": ["West", "East"]})
    built = ca.assemble(vp, slots)
    from storage import tabular_store
    res = tabular_store.execute_readonly(built["sql"], db_path=db, params=built["params"])
    assert res["ok"] and res["rows"][0][0] == 2   # West + 2025


def test_declines_unresolved_person(monkeypatch, catalog, db):
    assert _tier0(monkeypatch, catalog, db, "how many orders does Nobody Here have") is None


def test_declines_when_no_route(monkeypatch, catalog, db):
    assert _tier0(monkeypatch, catalog, db, "what is the meaning of life") is None
