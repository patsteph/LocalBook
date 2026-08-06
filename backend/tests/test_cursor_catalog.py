"""CI-pure tests for the SCHEMA_CATALOG parser (Phase 0) over the real sanitized schema.html fixture.

The old inline parser in data_notebook was silently broken (read `categories` as a dict — it's a
list — and looked for join-hint keys `hint`/`note` that don't exist; the real key is `join_note`), so
schema.html contributed nothing to the prompt. These tests lock the corrected shapes.

Assertions are SCHEMA-AGNOSTIC (self-consistent from the catalog itself) — no deployment-specific
object/column names, so this test carries no private schema identifiers.
"""
import pathlib

import pytest

from services import cursor_catalog as cc

_FIXTURE = pathlib.Path(__file__).resolve().parents[2] / "READFIRST" / "planning" / "schema_sanitized.html"


@pytest.fixture(scope="module")
def catalog():
    if not _FIXTURE.exists():
        pytest.skip("sanitized schema.html fixture not present (gitignored READFIRST)")
    return cc.parse_schema_catalog(_FIXTURE.read_text())


def test_parses_full_catalog(catalog):
    assert catalog is not None
    objs = catalog["objects"]
    views = cc.iter_views(catalog)
    tables = [o for o in objs if str(o.get("kind", "")).lower() == "table"]
    # self-consistent with the catalog's own counts (no hardcoded deployment numbers)
    assert len(views) == catalog.get("view_count")
    assert len(tables) == catalog.get("table_count")
    assert len(objs) == len(views) + len(tables)
    assert len(views) >= 1


def test_every_object_has_ddl_and_columns(catalog):
    for o in catalog["objects"]:
        assert o.get("ddl"), f"{o.get('name')} missing ddl"
        assert isinstance(o.get("columns"), list) and o["columns"], f"{o.get('name')} missing columns"


def test_views_carry_create_view_ddl(catalog):
    for v in cc.iter_views(catalog):
        assert "CREATE VIEW" in str(v["ddl"]).upper()
    # at least one view exposes multiple *_id role-key columns (the person-filterable keys Phase 1 uses)
    id_heavy = [v for v in cc.iter_views(catalog)
                if sum(1 for c in v["columns"] if str(c["name"]).endswith("_id")) >= 2]
    assert id_heavy, "expected at least one view with multiple *_id role-key columns"


def test_object_lookup_roundtrips(catalog):
    first = catalog["objects"][0]["name"]
    assert cc.object_by_name(catalog, first)["name"] == first
    assert cc.object_by_name(catalog, "does-not-exist") is None


def test_summary_renders_categories_and_join_hints(catalog):
    summary = cc.render_schema_summary(catalog)
    assert summary
    # category routing (list-shaped categories) now reaches the prompt — many lines, not ~0
    assert len([l for l in summary.splitlines() if l.startswith("- ")]) >= 5
    # join hints come from logical_associations_out.join_note + foreign_keys_out
    assert "Join hints" in summary
    assert "→" in summary or "↔" in summary


def test_parse_handles_garbage():
    assert cc.parse_schema_catalog("") is None
    assert cc.parse_schema_catalog("<html>no catalog here</html>") is None
    assert cc.render_schema_summary(None) == ""
