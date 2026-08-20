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


def _v1(**combo):
    base = {
        "main_engine": "ollama", "fast_engine": "ollama", "vision_engine": "ollama",
        "embed_engine": "ollama", "image_engine": "ollama",
        "main_model": "gemma4:e4b", "fast_model": "phi4-mini:latest",
        "vision_model": "granite3.2-vision:2b", "embeddings": "snowflake-arctic-embed2",
        "image_model": "klein", "mlx_main_model": "mlx-community/gemma-4-e4b-it-4bit",
        "mlx_fast_model": "mlx-community/Phi-4-mini-instruct-4bit",
        "mlx_vision_model": "mlx-community/gemma-4-e4b-it-4bit",
        "mlx_embedding_model": "mlx-community/snowflake-arctic-embed-l-v2.0-bf16",
        "mlx_image_model": "mlx-community/klein-4bit",
    }
    base.update(combo)
    return {"default_combo": base}


@pytest.fixture
def all_present(monkeypatch):
    monkeypatch.setattr("services.model_presence.is_present", lambda m: bool(m))


# ── The promotion: what makes the cutover real ──────────────────────────────────

def test_saved_ollama_roles_are_promoted_to_mlx(tmp_path, all_present):
    p = _write(tmp_path, _v1())
    out = mig.run(p)
    combo = json.loads(open(p).read())["default_combo"]
    assert combo["main_engine"] == "mlx"
    assert combo["fast_engine"] == "mlx"
    assert combo["embed_engine"] == "mlx"
    assert set(out["promoted"]) == {"main", "fast", "vision", "embed"}


def test_image_is_never_promoted(tmp_path, all_present):
    """The MLX image model is not downloaded. Repointing a role at absent weights is how a
    first run stalls with no error — image stays on Ollama until its model ships."""
    p = _write(tmp_path, _v1())
    mig.run(p)
    assert json.loads(open(p).read())["default_combo"]["image_engine"] == "ollama"


def test_a_role_whose_mlx_model_is_absent_is_left_alone(tmp_path, monkeypatch):
    """THE safety property. Promoting a role to a model that is not on disk trades a working
    Ollama path for a broken MLX one."""
    monkeypatch.setattr("services.model_presence.is_present",
                        lambda m: "Phi-4" not in m)
    p = _write(tmp_path, _v1())
    out = mig.run(p)
    combo = json.loads(open(p).read())["default_combo"]
    assert combo["fast_engine"] == "ollama", "absent weights must not be adopted"
    assert combo["main_engine"] == "mlx"
    assert "fast" not in out["promoted"]


def test_a_deliberate_non_ollama_choice_is_preserved(tmp_path, all_present):
    """Only `"ollama"` (the old default) is promoted. Anything else is a user decision."""
    p = _write(tmp_path, _v1(main_engine="llama_server"))
    mig.run(p)
    assert json.loads(open(p).read())["default_combo"]["main_engine"] == "llama_server"


# ── Conservatism: this rewrites user data ───────────────────────────────────────

def test_no_key_is_ever_deleted(tmp_path, all_present):
    original = _v1()
    original["some_unrelated_setting"] = {"keep": "me"}
    p = _write(tmp_path, original)
    mig.run(p)
    after = json.loads(open(p).read())
    assert after["some_unrelated_setting"] == {"keep": "me"}
    assert set(original["default_combo"]) <= set(after["default_combo"])


def test_a_backup_is_written_before_any_change(tmp_path, all_present):
    p = _write(tmp_path, _v1())
    out = mig.run(p)
    assert out["backup"] and json.load(open(out["backup"]))["default_combo"]["main_engine"] == "ollama"


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
    assert json.loads(open(p).read())["default_combo"]["main_engine"] == "mlx"


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

    assert settings.llm_provider == "ollama"
    assert settings.main_engine == "mlx"
