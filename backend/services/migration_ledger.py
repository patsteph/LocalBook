"""Numbered, forward-only, idempotent data migrations.

LB-10 item 1 of the v2.5.0 plan, and a prerequisite for everything after it:
LB-11 moves the whole data directory onto an encrypted volume and LB-12 rewrites
ids for sync. Neither may touch a format until there is a record of what shape
the data is actually in.

What was here before was not a migration framework. `migration_manager` detects
a version by sniffing LanceDB vector dimensions and, if it disagrees with the
build, triggers a full re-index — useful once, in 2026, for the 768→1024
embedding change. It cannot express "add a column", it has no record of what ran,
and `migration_meta` held exactly one key/value pair (`json_migrated`).

The model here is the boring one, because the boring one is auditable:

  * **Numbered.** 0001, 0002, … Order is the number, not import order or a
    dict's iteration order.
  * **Forward-only.** There are no down migrations. A down migration is a
    promise to reconstruct information that was deliberately discarded, and it
    is a promise nobody keeps. The escape hatch is a restore from backup
    (LB-10) or the export.
  * **Idempotent.** The ledger stops a migration running twice, but each one is
    ALSO written to be safe if it does — because a migration that touches files
    can fail halfway, and the ledger only stamps on success, so the next launch
    re-runs it.
  * **A failure does not advance the head.** The stamp is written after the
    body returns, in the same transaction for SQLite work. A migration that
    raises leaves the ledger exactly where it was, and the app refuses to
    pretend otherwise.

**Both stores.** A migration receives the SQLite connection AND the data
directory, because LocalBook's state is not all in SQLite — `notebook_store`,
`collector.yaml`, the approval queue and the preference files all matter, and a
framework that covers only tables would quietly leave half the data behind.

**The head is public** for LB-12's version-skew handshake: two peers compare
ledger heads, and the one that is behind pauses sync rather than applying
records it does not understand.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

TABLE = "migration_meta"
KEY_PREFIX = "migration:"

# What a data directory that predates the ledger is considered to be. Every
# install before v2.5.0 is this, and migration 0001 records it without touching
# anything.
BASELINE_VERSION = "0.6.5"


@dataclass(frozen=True)
class Migration:
    """One numbered step.

    `schema_version` is the version the data is AT once this has run — which is
    what makes the effective version derivable from the ledger rather than from
    a constant somebody forgot to bump.
    """

    number: int
    name: str
    schema_version: str
    run: Callable[["MigrationContext"], None]

    @property
    def key(self) -> str:
        return f"{KEY_PREFIX}{self.number:04d}"


@dataclass
class MigrationContext:
    """What a migration is handed. Both stores, never one."""

    conn: object          # sqlite3.Connection
    data_dir: Path


# ── the registry ────────────────────────────────────────────────────────────

_MIGRATIONS: List[Migration] = []


def register(number: int, name: str, schema_version: str):
    """Decorator to add a migration. Numbers must be unique and are the order."""

    def _wrap(fn: Callable[[MigrationContext], None]):
        if any(m.number == number for m in _MIGRATIONS):
            raise ValueError(f"migration {number} is already registered")
        _MIGRATIONS.append(Migration(number, name, schema_version, fn))
        _MIGRATIONS.sort(key=lambda m: m.number)
        return fn

    return _wrap


def migrations() -> List[Migration]:
    return list(_MIGRATIONS)


# ── the ledger table ────────────────────────────────────────────────────────


def _ensure_table(conn) -> None:
    """Create the ledger, or widen the pre-ledger key/value table to fit it.

    `migration_meta` already existed as `(key, value)` holding `json_migrated`.
    That row is left alone — it is read by the health portal and by
    `migrate_json_to_sqlite`, and deleting it would silently re-run a
    JSON→SQLite import over live data.
    """
    conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {TABLE} (
            key   TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
        """
    )
    cols = {row[1] for row in conn.execute(f"PRAGMA table_info({TABLE})")}
    if "applied_at" not in cols:
        conn.execute(f"ALTER TABLE {TABLE} ADD COLUMN applied_at TEXT")
    if "schema_version" not in cols:
        conn.execute(f"ALTER TABLE {TABLE} ADD COLUMN schema_version TEXT")
    conn.commit()


def _connection():
    from storage.database import Database

    return Database().get_connection()


def applied(conn=None) -> Dict[int, Dict[str, Optional[str]]]:
    """{number: {name, applied_at, schema_version}} for everything that ran."""
    conn = conn or _connection()
    _ensure_table(conn)
    out: Dict[int, Dict[str, Optional[str]]] = {}
    for row in conn.execute(
        f"SELECT key, value, applied_at, schema_version FROM {TABLE} WHERE key LIKE ?",
        (f"{KEY_PREFIX}%",),
    ):
        try:
            number = int(str(row[0])[len(KEY_PREFIX):])
        except ValueError:
            continue
        out[number] = {
            "name": row[1],
            "applied_at": row[2],
            "schema_version": row[3],
        }
    return out


def head(conn=None) -> int:
    """The highest applied migration number. 0 means nothing has run.

    Exposed for LB-12's handshake: a peer whose head is lower pauses sync rather
    than applying records written by a schema it does not understand.
    """
    done = applied(conn)
    return max(done) if done else 0


def schema_version(conn=None) -> str:
    """The effective data schema version, DERIVED from the ledger.

    Not a constant. `version.py` carrying a hand-maintained `DATA_SCHEMA_VERSION`
    is how a build comes to claim a version its data has never been migrated to.
    """
    done = applied(conn)
    if not done:
        return BASELINE_VERSION
    return done[max(done)].get("schema_version") or BASELINE_VERSION


def pending(conn=None) -> List[Migration]:
    done = set(applied(conn))
    # Sorted HERE, not just in `register`. Order is the number — anything that
    # populates the registry another way must not be able to change it.
    return sorted((m for m in migrations() if m.number not in done),
                  key=lambda m: m.number)


# ── running ─────────────────────────────────────────────────────────────────


def _stamp(conn, migration: Migration) -> None:
    conn.execute(
        f"INSERT OR REPLACE INTO {TABLE} (key, value, applied_at, schema_version) "
        f"VALUES (?, ?, ?, ?)",
        (
            migration.key,
            migration.name,
            datetime.now(timezone.utc).isoformat(),
            migration.schema_version,
        ),
    )


def run_pending(conn=None, data_dir: Optional[Path] = None) -> Dict[str, object]:
    """Apply every migration that has not run, in order.

    Stops at the FIRST failure and does not stamp it. Continuing past a failed
    migration would apply later steps to data the earlier one did not finish
    transforming, which is how a half-migrated store becomes an unrecoverable
    one.
    """
    conn = conn or _connection()
    if data_dir is None:
        from config import settings

        data_dir = Path(settings.data_dir)

    _ensure_table(conn)
    ctx = MigrationContext(conn=conn, data_dir=Path(data_dir))

    # Python's sqlite3 opens an implicit transaction before INSERT/UPDATE/DELETE
    # but NOT before DDL, so a migration that ran CREATE TABLE and then raised
    # left the table behind and `rollback()` was a no-op. Manage transactions
    # explicitly for the duration of the run.
    prior_isolation = getattr(conn, "isolation_level", "")
    try:
        conn.isolation_level = None
    except Exception:
        pass

    ran: List[str] = []
    for migration in pending(conn):
        try:
            conn.execute("BEGIN")
            migration.run(ctx)
            _stamp(conn, migration)
            conn.execute("COMMIT")
        except Exception as exc:
            try:
                conn.execute("ROLLBACK")
            except Exception:
                pass
            logger.error(
                "[migrations] %04d %s FAILED: %s — ledger left at %d",
                migration.number, migration.name, exc, head(conn),
            )
            _restore_isolation(conn, prior_isolation)
            return {
                "ran": ran,
                "failed": f"{migration.number:04d} {migration.name}",
                "error": str(exc),
                "head": head(conn),
                "schema_version": schema_version(conn),
            }
        ran.append(f"{migration.number:04d} {migration.name}")
        logger.info("[migrations] applied %04d %s", migration.number, migration.name)

    _restore_isolation(conn, prior_isolation)
    result = {
        "ran": ran,
        "failed": None,
        "error": None,
        "head": head(conn),
        "schema_version": schema_version(conn),
    }
    write_version_file(data_dir, result["schema_version"], result["head"])
    return result


def _restore_isolation(conn, prior) -> None:
    try:
        conn.isolation_level = prior
    except Exception:
        pass


def write_version_file(data_dir: Path, version: str, ledger_head: int) -> Path:
    """Write `version.json`, which the plan notes was never actually written.

    It is not the source of truth — the ledger is — but it is the one artefact a
    human, a backup manifest or a restore can read without opening SQLite.
    """
    path = Path(data_dir) / "version.json"
    try:
        from config import settings

        payload = {
            "schema_version": version,
            "ledger_head": ledger_head,
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "embedding_model": getattr(settings, "embedding_model", None),
            "embedding_dim": getattr(settings, "embedding_dim", None),
        }
    except Exception:
        payload = {
            "schema_version": version,
            "ledger_head": ledger_head,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n")
    return path


def status(conn=None) -> Dict[str, object]:
    """For Data Health and the LB-12 handshake."""
    conn = conn or _connection()
    done = applied(conn)
    return {
        "head": max(done) if done else 0,
        "schema_version": schema_version(conn),
        "applied": [
            {"number": n, **done[n]} for n in sorted(done)
        ],
        "pending": [
            {"number": m.number, "name": m.name, "schema_version": m.schema_version}
            for m in pending(conn)
        ],
    }


# ── the migrations ──────────────────────────────────────────────────────────


@register(1, "baseline", BASELINE_VERSION)
def _m0001_baseline(ctx: MigrationContext) -> None:
    """Record where every pre-ledger install already is. Changes nothing.

    Existing data is at 0.6.5 by definition — it is whatever the shipped
    migrations up to v2.4.0 left behind. Stamping it explicitly means the head
    is meaningful from the first run rather than reading as "no migrations have
    ever applied", which is indistinguishable from a corrupt ledger.
    """
    return None


SYNC_SCHEMA_VERSION = "0.7.0"


def text_hash(content) -> str:
    """The identity of a source's TEXT across machines (LB-12 genesis dedupe, D16).

    Hash of the extracted text, whitespace-trimmed — the same document added on
    two Macs yields the same value. Not the file-bytes hash some ingest paths
    keep in `metadata_json["content_hash"]` (folder_watcher, correspondent):
    that one keeps its own meaning and its own lookup.
    """
    import hashlib

    return hashlib.sha256((content or "").strip().encode("utf-8")).hexdigest()


def _add_column(conn, table: str, column: str, decl: str) -> None:
    cols = {r[1] for r in conn.execute(f'PRAGMA table_info("{table}")')}
    if column not in cols:
        conn.execute(f'ALTER TABLE "{table}" ADD COLUMN {column} {decl}')


@register(2, "sync-schema", SYNC_SCHEMA_VERSION)
def _m0002_sync_schema(ctx: MigrationContext) -> None:
    """LB-12d — the one schema change sync needs, for everyone (D1, D11).

    * `sources.content_hash` (indexed, backfilled from `content`) — genesis
      matches the same document across Macs by it (D16).
    * `sources.updated_at` — editing content left no trace before.
    * timestamps on `skills`.
    * `documents` — the irreplaceable JSON/YAML move here (phase D).
    * `sync_conflicts` — the review queue; a synced record, so a conflict
      resolved on one Mac is resolved on all.
    Pure SQL + hashing: no model calls, safe inside the migration transaction.
    """
    conn = ctx.conn
    _add_column(conn, "sources", "content_hash", "TEXT")
    _add_column(conn, "sources", "updated_at", "TEXT")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_sources_content_hash ON sources(content_hash)")
    rows = conn.execute("SELECT id, content, created_at FROM sources "
                        "WHERE content_hash IS NULL OR updated_at IS NULL").fetchall()
    for sid, content, created in rows:
        conn.execute("UPDATE sources SET content_hash = ?, updated_at = COALESCE(updated_at, ?) "
                     "WHERE id = ?", (text_hash(content), created, sid))

    _add_column(conn, "skills", "created_at", "TEXT")
    _add_column(conn, "skills", "updated_at", "TEXT")
    conn.execute("UPDATE skills SET created_at = COALESCE(created_at, CURRENT_TIMESTAMP), "
                 "updated_at = COALESCE(updated_at, CURRENT_TIMESTAMP)")

    conn.execute("""CREATE TABLE IF NOT EXISTS documents (
        kind TEXT NOT NULL,
        key TEXT NOT NULL,
        uuid TEXT NOT NULL,
        body_json TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        PRIMARY KEY (kind, key))""")
    conn.execute("""CREATE TABLE IF NOT EXISTS sync_conflicts (
        id TEXT PRIMARY KEY,
        tbl TEXT NOT NULL,
        pk TEXT NOT NULL,
        field TEXT NOT NULL,
        kind TEXT NOT NULL,
        kept_value TEXT,
        other_value TEXT,
        kept_clock TEXT,
        other_clock TEXT,
        status TEXT NOT NULL DEFAULT 'open',
        resolution TEXT,
        created_at TEXT,
        resolved_at TEXT)""")
