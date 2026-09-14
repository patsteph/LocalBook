"""Prefs migration — the file that decides which engine actually runs.

Stage 4 Phase 1. `main.py` restores `default_combo` over `config.py` with a truthy check, so a
saved `"ollama"` beats a new default on EVERY launch. Flipping `config.py` alone would have been
a no-op on every existing install; this migration is what makes the cutover take effect.

Every test writes to a tmp_path. The dev venv resolves `settings.data_dir` to the REAL production
data dir, and this module rewrites user data — it must never be pointed at the default path from
a test. (Learned the hard way: an `import main` smoke check ran this against the real file.)
"""
import json

import pytest

from storage import migrate_prefs_v2 as mig


def _write(tmp_path, payload):
    p = tmp_path / "user_preferences.json"
    p.write_text(json.dumps(payload))
    return str(p)


MLX = {
    "main": "mlx-community/gemma-4-e4b-it-4bit",
    "fast": "mlx-community/Phi-4-mini-instruct-4bit",
    "vision": "mlx-community/gemma-4-e4b-it-4bit",
    "embed": "mlx-community/snowflake-arctic-embed-l-v2.0-bf16",
    "image": "mlx-community/klein-4bit",
}


def _v1(**combo):
    """A pre-cutover prefs file: every role is a PAIR (an Ollama name + an `mlx_*` id) with an
    engine flag choosing between them. This is literally what is on disk for every existing
    install, so the key names here are LEGACY ON PURPOSE — do not "modernise" them, or the
    migration is only ever tested against data it has already migrated."""
    base = {
        "main_engine": "ollama", "fast_engine": "ollama", "vision_engine": "ollama",
        "embed_engine": "ollama", "image_engine": "ollama",
        "main_model": "gemma4:e4b", "fast_model": "phi4-mini:latest",
        "vision_model": "granite3.2-vision:2b", "embeddings": "snowflake-arctic-embed2",
        "image_model": "klein",
        "mlx_" + "main_model": MLX["main"],
        "mlx_" + "fast_model": MLX["fast"],
        "mlx_" + "vision_model": MLX["vision"],
        "mlx_" + "embedding_model": MLX["embed"],
        "mlx_" + "image_model": MLX["image"],
    }
    base.update(combo)
    return {"default_combo": base}


@pytest.fixture
def all_present(monkeypatch):
    monkeypatch.setattr("services.model_presence.is_present", lambda m: bool(m))


# ── The promotion: what makes the cutover real ──────────────────────────────────

def test_each_role_collapses_to_one_key_holding_the_checkpoint_id(tmp_path, all_present):
    """THE v4 migration. `main.py`'s restore loop writes `default_combo` straight into
    settings, so a file still holding the Ollama NAME under `main_model` would point the main
    role at something nothing can load."""
    p = _write(tmp_path, _v1())
    mig.run(p)
    combo = json.loads(open(p).read())["default_combo"]
    assert combo["main_model"] == MLX["main"]
    assert combo["fast_model"] == MLX["fast"]
    assert combo["vision_model"] == MLX["vision"]
    assert combo["embedding_model"] == MLX["embed"]


def test_the_engine_flags_and_mlx_duplicates_are_dropped(tmp_path, all_present):
    """They selected between two halves of a pair that no longer exists. Leaving them would
    let a stale flag contradict the collapsed value."""
    p = _write(tmp_path, _v1())
    mig.run(p)
    combo = json.loads(open(p).read())["default_combo"]
    for gone in ("main_engine", "fast_engine", "vision_engine", "image_engine", "embed_engine",
                 "mlx_main_model", "mlx_fast_model", "mlx_vision_model",
                 "mlx_embedding_model", "mlx_image_model"):
        assert gone not in combo, f"{gone} survived the collapse"


def test_image_collapses_like_every_other_role(tmp_path, all_present):
    """Image was excluded here for a while on the belief that Klein wasn't downloaded. It
    was — `is_present` reported it absent because `exact_weight_gb` only looked for weights
    at the snapshot ROOT, and diffusion checkpoints keep theirs in transformer/ text_encoder/
    vae/. No role needs a hardcoded exception; the presence gate is the real protection."""
    p = _write(tmp_path, _v1())
    mig.run(p)
    assert json.loads(open(p).read())["default_combo"]["image_model"] == MLX["image"]


def test_a_role_whose_checkpoint_is_absent_keeps_its_saved_value(tmp_path, monkeypatch):
    """Presence gates the PROMOTION. Collapse still folds the id in — the file has to end up
    in the new shape either way — but a machine without the weights is not told it has them."""
    monkeypatch.setattr("services.model_presence.is_present",
                        lambda m: "klein" not in m.lower())
    p = _write(tmp_path, _v1())
    out = mig.run(p)
    assert "image" not in out["promoted"]


def test_a_role_whose_mlx_model_is_absent_is_left_alone(tmp_path, monkeypatch):
    """THE safety property. Promoting a role to a model that is not on disk trades a working
    Ollama path for a broken MLX one."""
    monkeypatch.setattr("services.model_presence.is_present",
                        lambda m: "Phi-4" not in m)
    p = _write(tmp_path, _v1())
    out = mig.run(p)
    combo = json.loads(open(p).read())["default_combo"]
    assert "fast" not in out["promoted"], "absent weights must not be adopted"
    assert "main" in out["promoted"]


def test_a_deliberate_non_ollama_choice_is_not_promoted(tmp_path, all_present):
    """Only `"ollama"` (the old default) is promoted — anything else was a user decision. The
    collapse still runs, because the file must reach the new shape regardless."""
    p = _write(tmp_path, _v1(main_engine="llama_server"))
    out = mig.run(p)
    assert "main" not in out["promoted"]


# ── Conservatism: this rewrites user data ───────────────────────────────────────

def test_unrelated_settings_are_never_touched(tmp_path, all_present):
    """v4 DOES delete keys — the engine flags and `mlx_*` duplicates, deliberately. What it
    must never touch is anything outside the role combo."""
    original = _v1()
    original["some_unrelated_setting"] = {"keep": "me"}
    original["another"] = [1, 2, 3]
    p = _write(tmp_path, original)
    mig.run(p)
    after = json.loads(open(p).read())
    assert after["some_unrelated_setting"] == {"keep": "me"}
    assert after["another"] == [1, 2, 3]


def test_the_backup_holds_the_pre_migration_file(tmp_path, all_present):
    """The whole point of the backup: it must capture the ORIGINAL, or a bad migration is
    unrecoverable."""
    p = _write(tmp_path, _v1())
    out = mig.run(p)
    saved = json.load(open(out["backup"]))["default_combo"]
    assert saved["main_model"] == "gemma4:e4b"
    assert saved["mlx_" + "main_model"] == MLX["main"]


def test_a_backup_is_written_before_any_change(tmp_path, all_present):
    p = _write(tmp_path, _v1())
    out = mig.run(p)
    assert out["backup"] and json.load(open(out["backup"]))["default_combo"]["main_model"] == "gemma4:e4b"


def test_it_is_idempotent(tmp_path, all_present):
    p = _write(tmp_path, _v1())
    mig.run(p)
    first = open(p).read()
    second = mig.run(p)
    assert second["ran"] is False and "already" in second["reason"]
    assert open(p).read() == first


def test_a_v2_file_still_receives_the_promotion(tmp_path, all_present):
    """v2 files exist in the wild that predate the promotion. If the version had stayed at 2
    those installs would never flip — the bump to 3 is what re-opens them."""
    stale = _v1()
    stale["schema_version"] = 2
    stale["resolved_roles"] = {"main": {"engine": "ollama", "model": "gemma4:e4b"}}
    p = _write(tmp_path, stale)
    assert mig.run(p)["ran"] is True
    assert json.loads(open(p).read())["default_combo"]["main_model"] == MLX["main"]


def test_a_corrupt_file_is_left_untouched(tmp_path):
    p = tmp_path / "user_preferences.json"
    p.write_text("{not json")
    out = mig.run(str(p))
    assert out["ran"] is False and "unreadable" in out["reason"]
    assert p.read_text() == "{not json"


def test_a_missing_file_is_not_an_error(tmp_path):
    """Fresh install. A migration that raises here stops the app booting."""
    out = mig.run(str(tmp_path / "nope.json"))
    assert out["ran"] is False and "fresh install" in out["reason"]


def test_presence_failure_does_not_block_the_migration(tmp_path, monkeypatch):
    """A broken presence check must degrade to 'promote nothing', never to a failed boot."""
    def boom(_m):
        raise RuntimeError("cache unreadable")
    monkeypatch.setattr("services.model_presence.is_present", boom)
    p = _write(tmp_path, _v1())
    out = mig.run(p)
    assert out["ran"] is True and out["promoted"] == {}
    assert json.loads(open(p).read())["schema_version"] == mig.SCHEMA_VERSION


# ── Startup ordering ────────────────────────────────────────────────────────────

def test_the_migration_runs_before_the_restore_loop_reads_the_combo():
    """Ordering is the whole point. The migration promotes saved engines to MLX; the restore
    loop copies the combo into `settings`. Migration second = the promotion lands a launch
    late, and the first launch on a new build comes up all-Ollama. It shipped that way.

    Read as SOURCE, never imported: importing `main` under the dev venv resolves
    `settings.data_dir` to the real production data dir and migrates the user's own file.
    """
    import os

    src = open(os.path.join(os.path.dirname(__file__), "..", "main.py")).read()
    migrate_at = src.index("_migrate_prefs()")
    restore_at = src.index('_prefs.get("default_combo"')
    assert migrate_at < restore_at, "the prefs migration must precede the restore loop"


# ── The frontend sentinel this migration must NOT be confused with ──────────────

def test_llm_provider_is_not_an_engine_name():
    """`config.llm_provider` and the frontend's `llmProvider` are the PROVIDER axis
    (ollama/openai/anthropic), NOT the engine axis. `LLMSelector` treats any value other than
    `"ollama"` as cloud mode, so 'flipping the frontend default to mlx' as part of the cutover
    would open the Locker in cloud mode on every launch. The engine lives in `*_engine`."""
    from config import settings

    assert not hasattr(settings, "llm_provider"), "the provider axis is gone from config"
    assert settings.main_model.startswith("mlx-community/")
