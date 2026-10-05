"""Standard vs compact model setup, decided per Mac from the GPU budget (LB-1).

Model-agnostic: sizes come from the configured models, ids here are placeholders.
Real numbers (model_sizing, 2026-10-03): standard ~10.5 GB, compact ~7.1 GB.
"""
import pytest

from services import model_profile

W = {"main": 4.79, "fast": 2.01, "embed": 1.06}
KV = {"main": 0.25, "fast": 1.0}


@pytest.fixture
def env(monkeypatch):
    from config import settings
    monkeypatch.setattr(settings, "main_model", "main")
    monkeypatch.setattr(settings, "fast_model", "fast")
    monkeypatch.setattr(settings, "embedding_model", "embed")
    monkeypatch.setattr(settings, "model_profile", "auto")
    monkeypatch.setattr(model_profile, "_configured_fast", "")
    monkeypatch.setattr(model_profile, "_compact", False)
    monkeypatch.setattr("services.model_sizing.exact_weight_gb", lambda m: W.get(m))
    monkeypatch.setattr("services.model_sizing.load_config", lambda m: {"m": m})
    monkeypatch.setattr("services.model_sizing.kv_cache_gb", lambda cfg, ctx: KV.get(cfg["m"], 0))
    budget = {"gb": 10.0}
    monkeypatch.setattr("services.model_sizing.budget_gb", lambda: budget["gb"])
    return settings, budget


@pytest.mark.parametrize("gb,expected", [(9.3, "compact"), (10.8, "compact"), (15.3, "standard"), (33.0, "standard")])
def test_the_setup_follows_the_budget(env, gb, expected):
    settings, budget = env
    budget["gb"] = gb                                    # 16, 18, 24, 48 GB Macs
    d = model_profile.decide()
    assert d["profile"] == expected
    assert d["standard_gb"] == pytest.approx(10.48, abs=0.05) and d["compact_gb"] == pytest.approx(7.06, abs=0.05)


def test_a_choice_in_llm_studio_wins(env):
    settings, budget = env
    budget["gb"] = 9.0
    settings.model_profile = "standard"
    assert model_profile.decide()["profile"] == "standard"
    budget["gb"] = 40.0
    settings.model_profile = "compact"
    assert model_profile.decide()["profile"] == "compact"


def test_compact_points_the_fast_role_at_the_main_model(env):
    settings, budget = env
    budget["gb"] = 10.8
    model_profile.apply_at_startup()
    assert settings.fast_model == "main"
    # what the user configured is remembered, so the decision stays stable
    assert model_profile.decide()["fast_model"] == "fast"


def test_standard_leaves_the_roles_alone(env):
    settings, budget = env
    budget["gb"] = 20.0
    model_profile.apply_at_startup()
    assert settings.fast_model == "fast"


def test_compact_still_shows_the_chosen_fast_model(env):
    """LLM Studio marked no model as Fast on the mini: it read the runtime routing
    (= the main model), not the user's choice. Save-as-default read it too."""
    settings, budget = env
    budget["gb"] = 9.3
    model_profile.apply_at_startup()
    assert settings.fast_model == "main"                 # routing
    assert model_profile.configured_fast() == "fast"     # what the user chose
    assert model_profile.shares_fast() is True


def test_a_fast_swap_in_compact_changes_the_choice_not_the_routing(env):
    settings, budget = env
    budget["gb"] = 9.3
    model_profile.apply_at_startup()
    settings.fast_model = "other-fast"                   # what the Locker writes
    model_profile.after_swap({"fast_model": "other-fast"})
    assert settings.fast_model == "main"
    assert model_profile.configured_fast() == "other-fast"
    settings.main_model = "new-main"                     # a main swap: fast follows it
    model_profile.after_swap({"main_model": "new-main"})
    assert settings.fast_model == "new-main"


def test_standard_shows_and_swaps_the_fast_model_directly(env):
    settings, budget = env
    budget["gb"] = 20.0
    model_profile.apply_at_startup()
    settings.fast_model = "other-fast"
    model_profile.after_swap({"fast_model": "other-fast"})
    assert settings.fast_model == model_profile.configured_fast() == "other-fast"
    assert model_profile.shares_fast() is False
