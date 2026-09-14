"""Capability → role slotting, and the RAM-fit arithmetic under it.

MIGRATED 2026-08-20 from `_probe_test.py` (subprocess wrapper). The capability-READING
assertions were dropped as duplicates — `test_model_presence.py` already covers
vision/native_ctx being read from the checkpoint. What came across is what nothing else
tests: how capabilities become ROLE ELIGIBILITY, and the `ram_fit` maths.

THE TWO BUGS THIS GUARDS, both real:
  · "Qwen vision model told to install granite" — capabilities defaulted to off for any
    uncurated model, so a vision model looked text-only.
  · "5 fresh models all slotted into Main" — slotting keyed off DISK SIZE rather than what
    the model can do, so an embedder landed in the chat slot and produced nothing at all.
"""
import pytest

from evaluator import ram_fit
from evaluator.capability_probe import MLXCapabilityProbe, ProbedCapabilities


def probe(model_id):
    return MLXCapabilityProbe().probe(model_id)


# ── Role eligibility follows capability, never size ─────────────────────────────

def test_a_vision_capable_model_is_vision_eligible():
    caps = ProbedCapabilities(model="x", text=True, vision=True)
    assert "vision_model" in caps.roles()


def test_a_pure_embedder_never_lands_in_main():
    """The failure is silent: an embedding model in the Main slot returns vectors where the
    app expects prose, so chat produces nothing and raises nothing."""
    caps = ProbedCapabilities(model="y", text=True, embedding=True)
    assert caps.roles() == ["embedding_model"]


def test_a_text_model_is_eligible_for_both_text_slots():
    """Main vs Fast is the user's choice — size is a hint, not a gate."""
    caps = ProbedCapabilities(model="z", text=True)
    assert set(caps.roles()) == {"main_model", "fast_model"}


# ── Against the real cached checkpoints ─────────────────────────────────────────

def test_real_models_slot_where_they_belong():
    g = probe("mlx-community/gemma-4-e4b-it-4bit")
    p = probe("mlx-community/Phi-4-mini-instruct-4bit")
    e = probe("mlx-community/snowflake-arctic-embed-l-v2.0-bf16")
    assert g and p and e, "these are the shipped defaults; they must be probeable"
    assert set(g.roles()) == {"main_model", "fast_model", "vision_model"}
    assert set(p.roles()) == {"main_model", "fast_model"}
    assert e.roles() == ["embedding_model"]


def test_an_uncached_model_probes_to_none_without_reaching_the_network():
    """Probing must be a filesystem question. A network fallback here would make the Locker
    depend on connectivity and leak a request off-machine."""
    assert probe("mlx-community/definitely-not-downloaded") is None


# ── ram_fit arithmetic ──────────────────────────────────────────────────────────

def test_weight_maths_for_a_quantized_model():
    fit = ram_fit.ram_fit(8.0, "Q4_K_M", 16.0, context_tokens=8192)
    assert abs(fit["weight_gb"] - 4.88) < 0.05
    assert fit["fits"] and fit["budget_gb"] == 9.6


def test_a_70b_does_not_fit_a_16gb_mac():
    fit = ram_fit.ram_fit(70.0, "Q4_K_M", 16.0)
    assert fit["fits"] is False
    assert fit["recommendation"] == "over"


@pytest.mark.parametrize("quant,expected", [("F16", 2.0)])
def test_bytes_per_weight(quant, expected):
    assert ram_fit.bytes_per_weight(quant) == expected


def test_an_unknown_quant_falls_back_conservatively():
    """Guessing LOW here is how a model that cannot fit gets marked 'fits' — the exact way the
    old estimator disabled itself by reporting 0.00 GB."""
    assert ram_fit.bytes_per_weight("IQ4_XS?") in (0.55, 1.0)


def test_param_count_parsing():
    assert abs(ram_fit.parse_param_count_b("566.70M") - 0.567) < 0.001
