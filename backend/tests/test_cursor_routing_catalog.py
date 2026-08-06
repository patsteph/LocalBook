"""CI-pure tests for the derived Routing Catalog (Phase 1): build_routing_catalog + the store.

Route-binding + defaults parsing are tested with a SYNTHETIC generic schema (orders/customers) so no
deployment identifiers live in this tracked file. The real sanitized schema.html drives a second,
schema-AGNOSTIC test of the graph-derived parts (views, person convention, record grain, profiles).
"""
import pathlib

import pytest

from services import cursor_catalog as cc
from storage import cursor_catalog_store as store

_FIXTURE = pathlib.Path(__file__).resolve().parents[2] / "READFIRST" / "planning" / "schema_sanitized.html"

# ── synthetic generic catalog (no deployment names) ─────────────────────────────
_SYN = {
    "view_count": 1, "table_count": 2,
    "categories": [{"label": "Views", "names": ["v_orders"]}],
    "objects": [
        {"name": "v_orders", "kind": "view", "category": "Views",
         "ddl": "CREATE VIEW v_orders AS SELECT * FROM orders",
         "columns": [{"name": "order_id", "type": "INTEGER"}, {"name": "customer_id", "type": "TEXT"},
                     {"name": "region", "type": "TEXT"}, {"name": "tier", "type": "TEXT"},
                     {"name": "order_year", "type": "INTEGER"}, {"name": "amount", "type": "REAL"},
                     {"name": "order_ref", "type": "TEXT"}],
         "foreign_keys_out": [], "logical_associations_out": []},
        {"name": "orders", "kind": "table", "category": "Other",
         "ddl": "CREATE TABLE orders (order_ref TEXT, customer_id TEXT)",
         "columns": [{"name": "order_ref", "type": "TEXT"}, {"name": "customer_id", "type": "TEXT"}],
         "foreign_keys_out": [{"from_columns": ["customer_id"], "ref_table": "customers",
                               "to_columns": ["customer_id"]}],
         "logical_associations_out": [{"from_column": "order_ref", "to_table": "orders",
                                       "to_column": "order_ref", "join_note": "self"}]},
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

## Global defaults
| Dimension | Default |
|-----------|---------|
| Planning year | `order_year = 2025` |
"""


def test_build_synthetic_routes_defaults_person():
    cat = cc.build_routing_catalog(_SYN, _AGENTS)
    assert cat is not None
    # person convention derived from the FK graph (customers.customer_id, name col full_name)
    assert cat["person_convention"] == {"name_table": "customers", "name_col": "full_name",
                                         "key_col": "customer_id"}
    # a record grain was derived (non-scope, non-id FK/assoc key)
    assert cat["record_grain"]
    # global default parsed + grounded to a real column
    assert {"col": "order_year", "op": "=", "value": "2025"} in cat["defaults"]
    # both intent routes bound to the real view
    assert cat["route_count"] == 2
    for r in cat["routes"]:
        assert r["target_view"] == "v_orders" and r["tier0_ready"]


def test_parse_defaults_only_from_default_table():
    md = """
## Typical user requests
| Request | Approach |
|---------|----------|
| Unassigned orders | Use v_orders where customer_id = 'unassigned' |

Prose note: examples often show order_year = 1999 which is NOT a default.

## Global defaults
| Dimension | Default |
|-----------|---------|
| Planning year | `order_year = 2025` |
| Active | `status = 'open'` |
| Region grain | Four regions; exclude rollups |
"""
    known = {"order_year", "status", "customer_id", "region"}
    defs = cc.parse_defaults(md, known)
    cols = {d["col"] for d in defs}
    assert cols == {"order_year", "status"}                       # NOT customer_id (intent example)
    assert next(d for d in defs if d["col"] == "order_year")["value"] == "2025"  # table, not prose 1999


def test_view_profile_classification():
    cat = cc.build_routing_catalog(_SYN, _AGENTS)
    vp = cat["views"]["v_orders"]
    assert set(vp["role_keys"]) == {"order_id", "customer_id"}      # *_id columns
    assert "region" in vp["dimensions"] and "tier" in vp["dimensions"]
    assert vp["metrics"] == ["amount"]                              # numeric, non-id/scope/name
    assert vp["grain"] == cat["record_grain"]                       # grain attached to the view
    assert {"col": "order_year", "op": "=", "value": "2025"} in vp["scope_filters"]


def test_family_route_is_tier1_only():
    extra = [{"name": n, "kind": "view", "category": "Views",
              "ddl": f"CREATE VIEW {n} AS SELECT * FROM v_orders",
              "columns": [{"name": "order_ref", "type": "TEXT"}],
              "foreign_keys_out": [], "logical_associations_out": []}
             for n in ("v_orders_open", "v_orders_closed")]
    syn = {**_SYN, "objects": _SYN["objects"] + extra, "view_count": 3}
    # a wildcard matching MULTIPLE views → the route stays Tier-1-only (candidate set), never guesses one
    agents = "## reqs\n| Request | Approach |\n|--|--|\n| Show open vs closed | Use the v_orders_* views |\n"
    cat = cc.build_routing_catalog(syn, agents)
    fam = [r for r in cat["routes"] if not r["tier0_ready"]]
    assert fam and set(fam[0]["candidate_views"]) == {"v_orders_open", "v_orders_closed"}
    assert fam[0]["target_view"] is None


def test_build_over_real_schema_agnostic():
    if not _FIXTURE.exists():
        pytest.skip("sanitized schema.html fixture not present")
    sc = cc.parse_schema_catalog(_FIXTURE.read_text())
    cat = cc.build_routing_catalog(sc, "")   # no AGENTS.md available → 0 routes, schema parts still derived
    assert cat["view_count"] == sc["view_count"]
    pc = cat["person_convention"]
    assert pc and pc["name_table"] and pc["name_col"] and pc["key_col"]
    assert cat["record_grain"]
    # at least one view profile has multiple role keys + a dimension + a grain
    rich = [v for v in cat["views"].values() if len(v["role_keys"]) >= 2 and v["dimensions"] and v["grain"]]
    assert rich


def test_store_roundtrip(monkeypatch, tmp_path):
    from config import settings
    monkeypatch.setattr(settings, "data_dir", tmp_path, raising=False)
    cat = cc.build_routing_catalog(_SYN, _AGENTS)
    assert store.store_catalog("nbX", cat, "fp1")
    got = store.get_catalog("nbX")
    assert got and got["route_count"] == 2 and got["person_convention"]["name_table"] == "customers"
    store.drop_catalog("nbX")
    assert store.get_catalog("nbX") is None
