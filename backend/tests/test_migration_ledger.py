"""LB-10 item 1: the migration ledger.

The properties that matter are the failure ones. A migration framework that
works on the happy path and mis-records a failure is worse than none, because
everything downstream — LB-11 moving the data dir onto an encrypted volume,
LB-12 rewriting ids for sync — reads the head to decide whether it is safe to
proceed.
"""

import json
import sqlite3

import pytest

from services import migration_ledger as ledger
from services.migration_ledger import Migration, MigrationContext


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    return c


@pytest.fixture
def registry(monkeypatch):
    """Swap the real migration list for one the test controls."""
    def _set(*migrations):
        monkeypatch.setattr(ledger, "_MIGRATIONS", list(migrations))
        return migrations
    return _set


def _m(number, name="step", version="9.9.9", body=None):
    return Migration(number, name, version, body or (lambda ctx: None))


# ── the shipped registry ────────────────────────────────────────────────────


def test_the_real_registry_is_numbered_uniquely_and_in_order():
    numbers = [m.number for m in ledger.migrations()]
    assert numbers == sorted(numbers)
    assert len(numbers) == len(set(numbers))


def test_registering_a_duplicate_number_is_refused():
    """Two migrations sharing a number means one of them silently never runs."""
    with pytest.raises(ValueError, match="already registered"):
        ledger.register(1, "clash", "9.9.9")(lambda ctx: None)


def test_the_baseline_records_where_existing_installs_already_are(conn, tmp_path):
    ledger.run_pending(conn, tmp_path)
    assert ledger.head(conn) >= 1
    assert ledger.schema_version(conn) == ledger.BASELINE_VERSION


# ── running ─────────────────────────────────────────────────────────────────


def test_pending_migrations_run_in_number_order(conn, tmp_path, registry):
    order = []
    registry(
        _m(2, "second", body=lambda ctx: order.append(2)),
        _m(1, "first", body=lambda ctx: order.append(1)),
        _m(3, "third", body=lambda ctx: order.append(3)),
    )
    ledger.run_pending(conn, tmp_path)
    assert order == [1, 2, 3]


def test_a_second_run_does_nothing(conn, tmp_path, registry):
    runs = []
    registry(_m(1, "once", body=lambda ctx: runs.append(1)))

    first = ledger.run_pending(conn, tmp_path)
    second = ledger.run_pending(conn, tmp_path)

    assert runs == [1]
    assert first["ran"] == ["0001 once"]
    assert second["ran"] == []


def test_only_the_new_migration_runs_on_upgrade(conn, tmp_path, registry):
    runs = []
    registry(_m(1, "old", body=lambda ctx: runs.append(1)))
    ledger.run_pending(conn, tmp_path)

    registry(
        _m(1, "old", body=lambda ctx: runs.append(1)),
        _m(2, "new", body=lambda ctx: runs.append(2)),
    )
    ledger.run_pending(conn, tmp_path)

    assert runs == [1, 2]


def test_a_migration_gets_both_the_connection_and_the_data_dir(conn, tmp_path, registry):
    """LocalBook's state is not all in SQLite — notebook_store, collector.yaml
    and the approval queue are files, and a framework covering only tables would
    quietly leave half the data behind."""
    seen = {}

    def body(ctx: MigrationContext):
        seen["has_conn"] = ctx.conn is not None
        seen["data_dir"] = ctx.data_dir
        ctx.conn.execute("CREATE TABLE touched (x INT)")
        (ctx.data_dir / "touched.json").write_text("{}")

    registry(_m(1, "both", body=body))
    ledger.run_pending(conn, tmp_path)

    assert seen["has_conn"] is True
    assert seen["data_dir"] == tmp_path
    assert (tmp_path / "touched.json").exists()
    assert conn.execute("SELECT name FROM sqlite_master WHERE name='touched'").fetchone()


# ── failure: the part that matters ──────────────────────────────────────────


def test_a_failed_migration_does_not_advance_the_head(conn, tmp_path, registry):
    def boom(ctx):
        raise RuntimeError("column already exists")

    registry(_m(1, "ok"), _m(2, "broken", body=boom))

    result = ledger.run_pending(conn, tmp_path)

    assert result["failed"] == "0002 broken"
    assert "column already exists" in result["error"]
    assert ledger.head(conn) == 1


def test_a_failure_stops_later_migrations_running(conn, tmp_path, registry):
    """Continuing past a failure applies later steps to data the earlier one did
    not finish transforming."""
    ran = []
    registry(
        _m(1, "ok", body=lambda ctx: ran.append(1)),
        _m(2, "broken", body=lambda ctx: (_ for _ in ()).throw(RuntimeError("nope"))),
        _m(3, "later", body=lambda ctx: ran.append(3)),
    )

    ledger.run_pending(conn, tmp_path)

    assert ran == [1]
    assert ledger.head(conn) == 1


def test_a_failed_migrations_sqlite_work_is_rolled_back(conn, tmp_path, registry):
    def half_done(ctx):
        ctx.conn.execute("CREATE TABLE halfway (x INT)")
        raise RuntimeError("failed after the DDL")

    registry(_m(1, "half", body=half_done))
    ledger.run_pending(conn, tmp_path)

    assert conn.execute(
        "SELECT name FROM sqlite_master WHERE name='halfway'"
    ).fetchone() is None


def test_a_failed_migration_is_retried_on_the_next_run(conn, tmp_path, registry):
    """Which is exactly why each migration must also be written idempotent: a
    file-touching migration can fail after the files changed."""
    attempts = []

    def flaky(ctx):
        attempts.append(1)
        if len(attempts) == 1:
            raise RuntimeError("transient")

    registry(_m(1, "flaky", body=flaky))

    assert ledger.run_pending(conn, tmp_path)["failed"] is not None
    assert ledger.run_pending(conn, tmp_path)["failed"] is None
    assert len(attempts) == 2
    assert ledger.head(conn) == 1


# ── derived version + head ──────────────────────────────────────────────────


def test_the_schema_version_comes_from_the_ledger_not_a_constant(conn, tmp_path, registry):
    """A hand-maintained DATA_SCHEMA_VERSION is how a build comes to claim a
    version its data has never been migrated to."""
    registry(_m(1, "a", version="0.6.5"), _m(2, "b", version="0.7.0"))
    ledger.run_pending(conn, tmp_path)
    assert ledger.schema_version(conn) == "0.7.0"


def test_a_partial_run_reports_the_version_it_actually_reached(conn, tmp_path, registry):
    registry(
        _m(1, "a", version="0.6.5"),
        _m(2, "b", version="0.7.0", body=lambda ctx: (_ for _ in ()).throw(RuntimeError("x"))),
    )
    ledger.run_pending(conn, tmp_path)
    assert ledger.schema_version(conn) == "0.6.5"


def test_an_empty_ledger_reports_the_baseline(conn):
    assert ledger.head(conn) == 0
    assert ledger.schema_version(conn) == ledger.BASELINE_VERSION


def test_each_applied_migration_carries_a_stamp(conn, tmp_path, registry):
    registry(_m(1, "stamped"))
    ledger.run_pending(conn, tmp_path)

    record = ledger.applied(conn)[1]
    assert record["name"] == "stamped"
    assert record["applied_at"]
    assert record["schema_version"] == "9.9.9"


# ── living alongside the pre-ledger table ───────────────────────────────────


def test_the_existing_json_migrated_row_survives(conn, tmp_path, registry):
    """It is read by the health portal and by migrate_json_to_sqlite — dropping
    it would silently re-run a JSON import over live data."""
    conn.execute(
        f"CREATE TABLE {ledger.TABLE} (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
    )
    conn.execute(
        f"INSERT INTO {ledger.TABLE} (key, value) VALUES ('json_migrated', 'true')"
    )
    conn.commit()

    registry(_m(1, "after"))
    ledger.run_pending(conn, tmp_path)

    still = conn.execute(
        f"SELECT value FROM {ledger.TABLE} WHERE key='json_migrated'"
    ).fetchone()
    assert still[0] == "true"
    assert ledger.head(conn) == 1


def test_a_non_migration_row_is_not_mistaken_for_one(conn, tmp_path, registry):
    conn.execute(
        f"CREATE TABLE {ledger.TABLE} (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
    )
    conn.execute(f"INSERT INTO {ledger.TABLE} (key, value) VALUES ('json_migrated', 'true')")
    conn.commit()

    registry()
    ledger.run_pending(conn, tmp_path)
    assert ledger.applied(conn) == {}
    assert ledger.head(conn) == 0


def test_the_table_is_widened_without_losing_rows(conn, tmp_path, registry):
    conn.execute(
        f"CREATE TABLE {ledger.TABLE} (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
    )
    conn.execute(f"INSERT INTO {ledger.TABLE} (key, value) VALUES ('json_migrated', 'true')")
    conn.commit()

    ledger._ensure_table(conn)
    cols = {row[1] for row in conn.execute(f"PRAGMA table_info({ledger.TABLE})")}
    assert {"key", "value", "applied_at", "schema_version"} <= cols
    assert conn.execute(f"SELECT COUNT(*) FROM {ledger.TABLE}").fetchone()[0] == 1


def test_widening_twice_is_harmless(conn):
    ledger._ensure_table(conn)
    ledger._ensure_table(conn)
    cols = [row[1] for row in conn.execute(f"PRAGMA table_info({ledger.TABLE})")]
    assert len(cols) == len(set(cols))


# ── version.json ────────────────────────────────────────────────────────────


def test_version_json_is_actually_written(conn, tmp_path, registry):
    """The plan's note: it was never written. It is not the source of truth, but
    it is the one artefact a human or a restore can read without SQLite."""
    registry(_m(1, "a", version="0.7.0"))
    ledger.run_pending(conn, tmp_path)

    payload = json.loads((tmp_path / "version.json").read_text())
    assert payload["schema_version"] == "0.7.0"
    assert payload["ledger_head"] == 1
    assert payload["updated_at"]


def test_version_json_tracks_a_later_migration(conn, tmp_path, registry):
    registry(_m(1, "a", version="0.6.5"))
    ledger.run_pending(conn, tmp_path)

    registry(_m(1, "a", version="0.6.5"), _m(2, "b", version="0.7.0"))
    ledger.run_pending(conn, tmp_path)

    assert json.loads((tmp_path / "version.json").read_text())["schema_version"] == "0.7.0"


def test_version_json_is_not_written_past_a_failure(conn, tmp_path, registry):
    """It must never claim a version the data did not reach."""
    registry(_m(1, "broken", version="0.7.0",
                body=lambda ctx: (_ for _ in ()).throw(RuntimeError("x"))))
    ledger.run_pending(conn, tmp_path)
    assert not (tmp_path / "version.json").exists()


# ── status, for Data Health and the LB-12 handshake ─────────────────────────


def test_status_separates_applied_from_pending(conn, tmp_path, registry):
    registry(_m(1, "done"), _m(2, "waiting"))
    conn_state = ledger.applied(conn)
    assert conn_state == {}

    registry(_m(1, "done"))
    ledger.run_pending(conn, tmp_path)

    registry(_m(1, "done"), _m(2, "waiting"))
    st = ledger.status(conn)

    assert st["head"] == 1
    assert [a["number"] for a in st["applied"]] == [1]
    assert [p["number"] for p in st["pending"]] == [2]
