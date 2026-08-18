"""The one-shot Cursor Style residue purge (v2.3.0 removal).

The interesting row type is `_tabular_catalog` `cursor:%`: `tabular_store.has_tables()` has no
`db_path IS NULL` filter, so those rows keep routing every aggregate-ish question in an
ex-cursor notebook into the structured engine, which spends a model call on a phantom schema and
then falls back to vector RAG. Silent, permanent, per-query.
"""
import importlib

import pytest


@pytest.fixture
def mig(monkeypatch, tmp_path):
    from config import settings
    # Path, never str — stores do `data_dir / "x.db"`.
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    import storage.database as db
    importlib.reload(db)
    import storage.migrate_purge_cursor as m
    importlib.reload(m)
    return m, db


def _seed(db):
    c = db.Database().get_connection()
    c.execute("CREATE TABLE IF NOT EXISTS _tabular_catalog (source_id TEXT, notebook_id TEXT, db_path TEXT)")
    c.execute("CREATE TABLE IF NOT EXISTS notebooks (id TEXT PRIMARY KEY, type TEXT, config_json TEXT)")
    c.execute("CREATE TABLE IF NOT EXISTS _tabular_relationships (notebook_id TEXT)")
    c.execute("CREATE TABLE IF NOT EXISTS _cursor_views (x TEXT)")
    # Named columns: Database() has already created the REAL schema (notebooks has 11 columns),
    # so the CREATE IF NOT EXISTS above are no-ops and positional INSERTs would fail. Running
    # against the real tables is the better test anyway.
    ins = "INSERT INTO _tabular_catalog (source_id, notebook_id, db_path) VALUES (?,?,?)"
    c.execute(ins, ("cursor:nb1", "nb1", "/ext/sales.db"))
    c.execute(ins, ("cursor:nb1", "nb1", "/ext/sales.db"))
    # A REAL spreadsheet row — uploaded xlsx, internal db. Must survive untouched.
    c.execute(ins, ("src-abc", "nb2", None))
    # created_at / updated_at are NOT NULL in the real schema.
    nb_ins = ("INSERT INTO notebooks (id, title, type, config_json, created_at, updated_at) "
              "VALUES (?,?,?,?,datetime('now'),datetime('now'))")
    c.execute(nb_ins, ("nb1", "Ex-cursor notebook", "cursor", '{"db_path":"/ext/sales.db"}'))
    c.execute(nb_ins, ("nb2", "Spreadsheet notebook", "standard", "{}"))
    c.execute("INSERT INTO _tabular_relationships (notebook_id) VALUES (?)", ("nb1",))
    c.commit()
    return c


def test_purges_the_phantom_rows_and_converts_the_notebook(mig):
    m, db = mig
    c = _seed(db)
    out = m.run()
    assert out["ran"] is True
    assert out["catalog_rows"] == 2
    assert out["notebooks"] == 1
    assert c.execute("SELECT COUNT(*) FROM _tabular_catalog WHERE source_id LIKE 'cursor:%'").fetchone()[0] == 0
    assert c.execute("SELECT type FROM notebooks WHERE id='nb1'").fetchone()[0] == "standard"
    assert c.execute("SELECT config_json FROM notebooks WHERE id='nb1'").fetchone()[0] == "{}"


def test_leaves_real_spreadsheet_rows_alone(mig):
    """THE thing that must not break: an uploaded .xlsx notebook keeps working."""
    m, db = mig
    c = _seed(db)
    m.run()
    rows = c.execute("SELECT source_id, notebook_id FROM _tabular_catalog").fetchall()
    assert [tuple(r) for r in rows] == [("src-abc", "nb2")]
    assert c.execute("SELECT type FROM notebooks WHERE id='nb2'").fetchone()[0] == "standard"


def test_is_idempotent(mig):
    m, db = mig
    c = _seed(db)
    assert m.run()["ran"] is True
    second = m.run()
    assert second["ran"] is False           # marker short-circuits
    assert second["catalog_rows"] == 0
    # ...and a row added later is NOT re-purged, proving the marker held.
    c.execute("INSERT INTO _tabular_catalog VALUES ('cursor:nb9','nb9','/x.db')")
    c.commit()
    m.run()
    assert c.execute("SELECT COUNT(*) FROM _tabular_catalog WHERE source_id LIKE 'cursor:%'").fetchone()[0] == 1


def test_drops_cursor_only_tables(mig):
    m, db = mig
    c = _seed(db)
    m.run()
    assert c.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='_cursor_views'"
    ).fetchone() is None


def test_never_raises_on_a_virgin_database(mig):
    """A fresh install has none of these tables. Must be a clean no-op, not a boot failure."""
    m, _ = mig
    assert m.run()["ran"] is True
