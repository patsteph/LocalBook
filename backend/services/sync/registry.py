"""What syncs, what stays on this Mac, and what is rebuilt (LB-12, 12c/12g/12h).

The single classification the sync engine, the trigger installer and the tests
all read. Anything not listed here is not synced — and the coverage tests fail
on a table or data-dir path that nobody classified, so a new store cannot slip
out of (or into) sync by accident.

Per table:
  * `pk`       the identity columns (a row's key across machines).
  * `exclude`  columns never shipped: counters (recomputed locally, never
               merged), device-scoped values, and caches.
  * `content`  fields where a true concurrent edit keeps BOTH values and raises
               a review item. Every other shipped field is last-writer-wins.
  * `paths`    columns holding absolute paths under the data dir; shipped as
               `@data/<relative>` and expanded on arrival (12g).
Order matters: parents before children, so an applied batch never inserts a
child whose parent is still missing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Tuple


@dataclass(frozen=True)
class Table:
    name: str
    pk: Tuple[str, ...]
    exclude: Tuple[str, ...] = ()
    content: Tuple[str, ...] = ()
    paths: Tuple[str, ...] = ()


# ── localbook.db ─────────────────────────────────────────────────────────────

MAIN: List[Table] = [
    Table("notebook_sections", ("id",)),
    Table("notebooks", ("id",), exclude=("source_count",), content=("title", "description"),
          ),
    Table("skills", ("skill_id",), content=("system_prompt", "description")),
    Table("sources", ("id",), content=("content", "notes", "tags", "filename")),
    Table("highlights", ("highlight_id",), content=("annotation",)),
    Table("findings", ("id",), content=("title", "content_json")),
    Table("canvas_notes", ("id",), content=("title", "content_markdown", "content_blocknote_json")),
    Table("audio_generations", ("audio_id",), paths=("audio_file_path",)),
    Table("video_generations", ("video_id",), paths=("video_file_path",)),
    Table("content_generations", ("content_id",)),
    Table("quiz_generations", ("quiz_id",)),
    Table("visual_generations", ("visual_id",)),
    Table("infographic_generations", ("infographic_id",)),
    Table("article_sections", ("id",), exclude=("article_count",)),
    Table("articles", ("id",)),
    Table("routing_rules", ("id",), exclude=("hit_count", "last_hit_at")),
    Table("sender_settings", ("sender_email",)),
    Table("sender_blocklist", ("sender_email",)),
    Table("exploration_queries", ("id",)),
    Table("exploration_topics", ("notebook_id", "topic"), exclude=("count",)),
    Table("exploration_sources", ("notebook_id", "source_id"), exclude=("count",)),
    Table("canvas_nodes", ("id",)),
    Table("canvas_edges", ("id",)),
    Table("canvas_topics", ("id",), exclude=("member_count",)),
    Table("canvas_recall", ("notebook_id", "node_id")),
    Table("contradiction_scans", ("notebook_id",)),
    Table("contradictions", ("id",)),
    Table("documents", ("kind", "key"), content=("body_json",)),
    Table("sync_conflicts", ("id",)),
]

# Tables in localbook.db that stay on this Mac (12h) or are rebuilt locally.
MAIN_LOCAL: Dict[str, str] = {
    "folder_links": "per-machine: absolute paths of this Mac's folders (12g)",
    "folder_seen": "per-machine: this Mac's ingest ledger — merging it skips or re-ingests files",
    "folder_pending": "per-machine: suggestions for this Mac's folders",
    "companion_calls": "per-machine: LB-2 audit log",
    "migration_meta": "per-machine: this Mac's migration ledger",
    "_migrations": "per-machine: legacy migration markers",
    "canvas_viewport": "per-machine: where this Mac's canvas is scrolled",
    "voice_profile": "derived: rebuilt from voice observations",
    "newsletter_scorecards": "derived: rebuilt from correspondent events",
    "topic_clusters": "derived: rebuilt from articles",
    "pending_digest": "transient: mail waiting for this Mac's next digest",
    "pending_unsubscribes": "transient: short-lived unsubscribe tokens",
    "research_jobs": "per-machine: a companion's research job runs on the Mac it started on",
    # Append-only logs with AUTOINCREMENT ids — phase F syncs them by
    # (origin device, local id); until then they stay local.
    "activity_events": "log (phase F)",
    "correspondent_events": "log (phase F)",
    "unsubscribe_log": "log (phase F)",
    "routing_decisions": "log (phase F)",
    "voice_observations": "log (phase F)",
    "sqlite_sequence": "sqlite internal",
}

# ── memory/recall_memory.db ──────────────────────────────────────────────────

RECALL: List[Table] = [
    Table("recall_entries", ("id",), exclude=("is_summarized", "summary")),
    Table("conversation_summaries", ("id",)),
    Table("user_signals", ("id",)),
]
RECALL_LOCAL: Dict[str, str] = {
    "archival_fts": "derived: keyword index over archival memory",
    "archival_access": "per-machine: access counters",
}

DATABASES: Dict[str, Tuple[str, List[Table]]] = {
    "main": ("localbook.db", MAIN),
    "recall": ("memory/recall_memory.db", RECALL),
}


def tables(db: str) -> List[Table]:
    return DATABASES[db][1]


def table(db: str, name: str) -> Table:
    for t in tables(db):
        if t.name == name:
            return t
    raise KeyError(f"{name} is not a synced table in {db}")


def is_internal(name: str) -> bool:
    """The sync engine's own bookkeeping and SQLite/FTS internals."""
    return (name.startswith("_changes") or name.startswith("_sync_")
            or name.startswith("_remote_applied") or name.startswith("sqlite_")
            or "_fts" in name)


def classify_table(db: str, name: str) -> str:
    """synced / local / internal / UNCLASSIFIED — the coverage test's question."""
    if is_internal(name):
        return "internal"
    if any(t.name == name for t in tables(db)):
        return "synced"
    local = MAIN_LOCAL if db == "main" else RECALL_LOCAL
    if name in local:
        return "local"
    return "UNCLASSIFIED"


# ── the data directory (12h, every-file test) ────────────────────────────────
# First matching rule wins. A path matching nothing is UNCLASSIFIED.
#   record  — synced as SQLite records (the DB files themselves are never copied)
#   blob    — synced as content-addressed files (phase E)
#   local   — stays on this Mac
#   derived — rebuilt locally from synced records
#   transient — scratch
PATH_RULES: List[Tuple[str, str]] = [
    ("localbook.db", "record"), ("tabular.db", "local"),
    ("memory/recall_memory.db", "record"), ("curator_brain/", "local"),
    ("memory/core_memory.json", "record"), ("memory/archival_memory/", "record"),
    ("memory/events/", "local"), ("memory/", "derived"),
    ("user_profile.json", "record"), ("app_preferences.json", "record"),
    ("curator_config.yaml", "record"), ("schedule_overrides.json", "local"),
    ("notebooks/", "record"), ("quizzes/", "record"), ("correspondent/", "local"),
    ("audio/jingles/", "derived"), ("audio/", "blob"), ("video/", "blob"),
    ("pptx_templates/", "blob"),
    ("audio_output/", "transient"), ("images/", "derived"),
    ("lancedb/", "derived"), ("knowledge_graph/", "derived"), ("topic_model/", "derived"),
    ("models/", "derived"),
    ("user_preferences.json", "local"), ("user_preferences.json.", "local"),
    ("collection_scheduler_state.json", "local"), ("companion_", "local"),
    ("credentials.", "local"), ("auth/", "local"), (".env", "local"),
    (".app_token", "local"), (".clean_shutdown", "local"), (".version", "local"),
    ("version.json", "local"), (".volume_id", "local"), (".restore-pending.json", "local"),
    (".activity_ledger_backfilled", "local"), (".shallow_", "local"),
    (".rag_v3_upgraded", "local"), (".write_test", "local"),
    ("restore_drills.json", "local"), ("capture_hashes.json", "local"),
    ("sync/", "local"), ("LocalBook.keys/", "local"),
    ("embedding_cache.json", "derived"), ("answer_cache.json", "derived"),
    ("rag_metrics.json", "derived"), ("entity_graph.json", "derived"),
    ("entities.json", "derived"), ("communities.json", "derived"),
    ("communities_backup_", "derived"),
    ("signals/", "local"), ("eval_results/", "transient"), ("eval/", "local"),
    ("diagnostics", "transient"), ("backups/", "transient"), ("lancedb_backup", "transient"),
    ("localbook.db.backup", "transient"), ("tmp/", "transient"),
    # Legacy JSON stores — input to the JSON→SQLite migration only.
    ("notebooks.json", "transient"), ("sources.json", "transient"),
    ("highlights.json", "transient"), ("skills.json", "transient"),
    ("audio_generations.json", "transient"), ("video_generations.json", "transient"),
    ("content_generations.json", "transient"), ("exploration.json", "transient"),
    ("findings/", "transient"), ("curator_insights.json", "transient"),
]
SUFFIX_RULES: List[Tuple[str, str]] = [
    ("-wal", "transient"), ("-shm", "transient"), (".tmp", "transient"),
    (".DS_Store", "transient"), (".pre-keyvault", "transient"),
]


def classify_path(rel: str) -> str:
    rel = rel.replace("\\", "/").lstrip("/")
    for suffix, cls in SUFFIX_RULES:
        if rel.endswith(suffix):
            return cls
    for prefix, cls in PATH_RULES:
        if rel == prefix.rstrip("/") or rel.startswith(prefix):
            return cls
    return "UNCLASSIFIED"
