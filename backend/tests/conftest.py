"""Session-wide test isolation from the user's PRODUCTION data.

On a dev machine `settings.data_dir` resolves to the real
`~/Library/Application Support/LocalBook/` — the live data dir, not a sandbox (see
COLLABORATION_NOTES, 2026-07-22, where a persistence test overwrote `user_preferences.json`).
Any test that reaches a state-mutating path therefore writes to the user's actual data.

That is not hypothetical. `test_canvas_api.test_edge_lifecycle` POSTs an edge with `state="user"`,
which runs the real feedback pipeline and appends a `user_connection` signal to the **production
Quality Signals ledger on every test run**. By 2026-08-12, 116 of the 128 signals in it (91%) were
test noise — polluting the Health "Rough Edges" panel and, worse, the recurrence counts that
QS Phase 2 uses to promote field edges into Evaluator regression cases. Synthetic signals recurring
across many days are exactly what that promoter is designed to escalate.

This fixture repoints `data_dir` at a temp directory for the whole session, so no test can write
production data by accident. It is autouse and session-scoped: correctness here must not depend on
individual tests remembering to opt in.

CI is unaffected (a fresh runner has no data dir anyway) — this protects the dev machines.
"""
import os as _os
_os.environ.setdefault("LOCALBOOK_NO_INTERACTIVE_AUTH", "1")   # never a Keychain/Touch ID prompt from a test
# Importing main.py applies the per-Mac model setup (services/model_profile). On a 16 GB dev
# Mac that is "compact" and rewrites settings.fast_model for every later test; tests that
# exercise the profile set it explicitly.
_os.environ.setdefault("LOCALBOOK_MODEL_PROFILE", "standard")

import os
from pathlib import Path

import pytest


@pytest.fixture(scope="session", autouse=True)
def _isolate_production_data_dir(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("localbook-data")

    from config import settings
    original = settings.data_dir
    # Must stay a Path — stores do `settings.data_dir / "file.json"`.
    settings.data_dir = Path(tmp)

    # Sinks that cache a path at construction must be repointed explicitly — reading `settings`
    # later is not enough. `quality_signals` is a singleton built at import time.
    signals = None
    original_signals_dir = None
    try:
        from services.quality_signals import quality_signals as signals
        original_signals_dir = signals.signals_dir
        signals.signals_dir = Path(tmp) / "signals"
        signals.signals_dir.mkdir(parents=True, exist_ok=True)
    except Exception:  # sink unavailable in a slim env — nothing to isolate
        pass

    # The field-edge promoter appends to a REPO-COMMITTED fixture
    # (`evaluator/test_fixtures/field_edge_cases.json`). It is data-not-code by design, so in
    # production it should be written — but a test run must never leave synthetic cases in the
    # working tree. Same lesson as the signals ledger, one directory over.
    # `field_edge_cases_path()` is already env-overridable — set the env var rather than replacing
    # the function, so individual tests can still point it somewhere else with monkeypatch.setenv.
    cases_env = "LOCALBOOK_FIELD_EDGE_CASES"
    original_cases_env = os.environ.get(cases_env)
    os.environ[cases_env] = str(Path(tmp) / "field_edge_cases.json")

    yield tmp

    settings.data_dir = original
    if signals is not None and original_signals_dir is not None:
        signals.signals_dir = original_signals_dir
    if original_cases_env is None:
        os.environ.pop(cases_env, None)
    else:
        os.environ[cases_env] = original_cases_env


# --- Tests that must not run by accident ------------------------------------------------------
# 1. KEYCHAIN. These modules drive the REAL macOS login keychain through `security` (see the
#    docstring of test_keyvault.py: that is deliberate, a mock would prove nothing). Run
#    unattended or from an agent session, a run of ~400 of them put constant "Reset to Defaults"
#    keychain dialogs on a developer's Mac on 2026-10-22: every add/delete can raise a
#    SecurityAgent prompt, the code deliberately leaves a timed-out `security` running (killing
#    it crashes securityd), so the prompts pile up. So they are OFF by default on every platform and
#    run only when someone at the keyboard opts in:
#
#        LOCALBOOK_RUN_KEYCHAIN_TESTS=1 pytest backend/tests/test_keyvault.py
#
#    CI (ubuntu) has no Keychain and skips them for the same reason.
_TOUCHES_THE_REAL_KEYCHAIN = {
    "test_backup_scheduler",
    "test_backup_service",
    "test_credential_locker_migration",
    "test_data_health",
    "test_encryption_migration",
    "test_encryption_rollback",
    "test_key_escrow",
    "test_keychain_manager_bundle",
    "test_keyvault",
    "test_keyvault_api",
    "test_lb11_matrix",
    "test_restore_service",
    "test_volume_gate",
    "test_volume_service",
}

# 2. MLX / MODEL CACHE. Individual tests in otherwise-portable modules that need MLX, BERTopic, a
#    local model cache, or whose behaviour on Linux has not been triaged. They run on a Mac and are
#    skipped elsewhere, which keeps the ubuntu CI job (red since at least 2026-09-23) green.
import sys as _sys

_NEEDS_MLX_OR_MODEL_CACHE = {
    "test_capability_slotting": (
        "test_real_models_slot_where_they_belong",
    ),
    "test_kv_quantization": (
        "test_a_model_without_a_sliding_window_still_gets_the_saving",
        "test_short_exchanges_are_untouched",
        "test_the_configured_settings_reach_the_engine",
        "test_the_geometry_check_matches_the_real_configs",
    ),
    "test_mlx_budget": (
        "test_budget_comes_from_the_working_set_minus_a_named_reserve",
        "test_eviction_fires_when_the_incoming_model_does_not_fit",
        "test_resident_cost_is_exact_not_estimated",
        "test_the_fast_model_is_the_expensive_one_at_context",
    ),
    "test_mlx_unload": (
        "test_resident_reports_what_is_held",
    ),
    "test_model_catalog": (
        "test_fit_is_judged_against_addressable_memory_not_total_ram",
    ),
    "test_model_presence": (
        "test_a_diffusion_checkpoint_reports_its_real_weight",
        "test_enumerate_finds_real_models_with_sizes",
        "test_every_cached_model_reports_a_size",
        "test_is_present_is_a_filesystem_fact_not_a_network_call",
        "test_loading_a_cached_model_never_contacts_the_hub",
        "test_non_safetensors_weight_formats_are_counted",
        "test_readiness_names_the_blocking_roles",
        "test_sizing_still_works_for_ordinary_sharded_checkpoints",
        "test_the_model_browser_lists_the_diffusion_model",
        "test_vision_and_context_are_read_from_the_checkpoint",
        "test_vision_is_not_blocking",
    ),
    "test_onnx_reranker": (
        "test_falls_back_to_flashrank_when_the_model_file_is_missing",
    ),
    "test_shared_gpu_budget": (
        "test_an_explicit_env_override_still_wins",
        "test_the_buffer_cache_is_bounded",
        "test_the_mlx_limit_never_drops_below_one_gb",
        "test_the_mlx_memory_limit_subtracts_the_reserve",
    ),
    "test_startup_and_locker_mlx_only": (
        "test_no_script_pulls_or_starts_ollama",
        "test_the_locker_lists_only_downloaded_mlx_models",
    ),
    "test_sync_peer": (
        "test_an_unpaired_mac_cannot_get_past_tls",
    ),
    "test_topic_incremental_assignment": (
        "test_against_real_bertopic_not_a_fake",
        "test_topic_fit_is_reproducible",
    ),
}


def pytest_collection_modifyitems(config, items):
    keychain_ok = os.environ.get("LOCALBOOK_RUN_KEYCHAIN_TESTS") == "1" and _sys.platform == "darwin"
    for item in items:
        module = item.module.__name__.rsplit(".", 1)[-1]
        name = item.name.split("[", 1)[0]
        if module in _TOUCHES_THE_REAL_KEYCHAIN and not keychain_ok:
            item.add_marker(pytest.mark.skip(
                reason="touches the real macOS Keychain; set LOCALBOOK_RUN_KEYCHAIN_TESTS=1 to run on a Mac"))
        elif _sys.platform != "darwin" and name in _NEEDS_MLX_OR_MODEL_CACHE.get(module, ()):
            item.add_marker(pytest.mark.skip(reason="needs macOS (MLX / local model cache)"))
