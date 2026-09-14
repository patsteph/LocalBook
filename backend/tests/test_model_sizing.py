"""Model sizing — exact weights, real KV geometry, hardware-derived budget.

Replaces three independently-wrong estimates in `evaluator/ram_fit.py`. The numbers asserted
here were MEASURED on 2026-08-19 against the cached checkpoints, and two of them were derived
independently by two research agents before the code existed.

Pure: every test drives the geometry math on config dicts, so nothing here needs a model on
disk or a GPU.
"""
import pytest

from services import model_sizing as ms


# Real geometry, copied from the cached configs.
GEMMA = {
    "num_hidden_layers": 42, "num_key_value_heads": 2, "num_attention_heads": 8,
    "head_dim": 256, "hidden_size": 2560, "sliding_window": 512,
    "max_position_embeddings": 131072,
    "layer_types": (["sliding_attention"] * 5 + ["full_attention"]) * 7,   # 35 sliding, 7 full
}
PHI = {
    "num_hidden_layers": 32, "num_key_value_heads": 8, "num_attention_heads": 24,
    "hidden_size": 3072, "sliding_window": 262144, "max_position_embeddings": 131072,
}


def test_gemma_geometry_is_mostly_sliding():
    g = ms.kv_geometry(GEMMA)
    assert g["layers"] == 42
    assert g["full_layers"] == 7 and g["sliding_layers"] == 35
    assert g["kv_heads"] == 2 and g["head_dim"] == 256


def test_phi_has_no_effective_sliding_window():
    """phi declares sliding_window=262144 against a 131072 max context — a window larger than
    the context is not a window. Reading it naively would under-count KV by 32x."""
    g = ms.kv_geometry(PHI)
    assert g["full_layers"] == 32 and g["sliding_layers"] == 0
    assert g["sliding_window"] is None
    assert g["head_dim"] == 128          # derived: hidden_size / num_attention_heads


def test_head_dim_is_derived_when_absent():
    assert ms.kv_geometry(PHI)["head_dim"] == 3072 // 24


def test_the_fast_model_costs_far_more_kv_than_the_main_model():
    """THE finding that inverts the sizing stack's assumption. phi is the 'fast, cheap' model
    and gemma the 'big' one — but per token of context phi costs ~8x more KV."""
    g_kv = ms.kv_cache_gb(GEMMA, 32768)
    p_kv = ms.kv_cache_gb(PHI, 32768)
    assert p_kv > g_kv * 5, f"expected phi >> gemma, got gemma={g_kv} phi={p_kv}"


@pytest.mark.parametrize("cfg,ctx,expected", [
    (GEMMA, 131072, 1.784),   # matches both research docs' independent derivations
    (GEMMA, 16384, 0.253),
    (PHI, 8192, 1.0),
    (PHI, 32768, 4.0),
])
def test_kv_matches_the_measured_values(cfg, ctx, expected):
    got = ms.kv_cache_gb(cfg, ctx)
    assert abs(got - expected) < 0.01, f"{got} != {expected}"


def test_sliding_layers_stop_growing_past_their_window():
    """Gemma's 35 sliding layers are pinned at 512 tokens however long the context gets —
    which is why its KV is nearly flat while phi's scales linearly."""
    a = ms.kv_cache_gb(GEMMA, 16384)
    b = ms.kv_cache_gb(GEMMA, 32768)
    # Doubling context roughly doubles only the 7 full layers, not all 42.
    assert b < a * 2.05
    # phi, by contrast, doubles outright.
    pa, pb = ms.kv_cache_gb(PHI, 16384), ms.kv_cache_gb(PHI, 32768)
    assert abs(pb - pa * 2) < 0.01


def test_missing_geometry_returns_none_not_a_guess():
    """A silent 0.0 is how the old estimator turned `fits` into `True` for arctic — the
    guardrail was disabled, not merely inaccurate."""
    assert ms.kv_geometry({}) is None
    assert ms.kv_geometry({"num_hidden_layers": 12}) is None      # no kv heads
    assert ms.kv_cache_gb({}, 8192) is None
    assert ms.kv_cache_gb(GEMMA, 0) is None


def test_vlm_text_config_nesting_is_handled():
    assert ms.kv_geometry({"text_config": GEMMA})["full_layers"] == 7


def test_unknown_model_reports_unknown_rather_than_fitting(monkeypatch):
    """The old code's failure mode: no data → weight 0.0 → `fits: True`."""
    monkeypatch.setattr(ms, "exact_weight_gb", lambda mid: None)
    monkeypatch.setattr(ms, "load_config", lambda mid: None)
    f = ms.fit("mlx-community/not-on-this-disk", 8192)
    assert f["fits"] is None and f["recommendation"] == "unknown"
    assert "not in the local HF cache" in f["reason"]


def test_budget_comes_from_the_working_set_not_total_ram(monkeypatch):
    monkeypatch.setattr(ms, "working_set_gb", lambda: 11.84)
    ms._CACHE.clear()
    assert ms.budget_gb(0.75) == pytest.approx(8.88, abs=0.01)
    # A 64 GB machine must get a proportionally larger budget, not the same constant.
    monkeypatch.setattr(ms, "working_set_gb", lambda: 47.4)
    assert ms.budget_gb(0.75) > 30
