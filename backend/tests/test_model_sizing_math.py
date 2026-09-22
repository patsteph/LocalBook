"""How much memory a model actually needs, and whether it fits this Mac.

Rebuilt 2026-09-22 after a field report: a 30B MoE checkpoint was reported as
needing **105 GB** on a 48 GB machine, when the same model is routinely run on
32 GB Macs. Three separate errors compounded.

**1. Quantized weights were sized as if unquantized.** MLX packs quantized
weights into U32 words, and HuggingFace reports the LOGICAL parameter count
against that dtype — not the number of words. Multiplying by 4 bytes overstated
a 4-bit checkpoint by ~7×:

    Qwen3-30B-A3B-4bit    113.74 GB computed   vs   16.00 GB real
    gemma-4-e4b-it-4bit    28.70 GB computed   vs    4.79 GB real

It also made 4-bit and 8-bit indistinguishable — both report the same count
under the same dtype — so the browser could not tell a 16 GB checkpoint from a
30 GB one.

The true cost of an MLX quantized tensor is the weights PLUS a scale and a bias
(fp16 each) per quantization group:

    bytes per parameter = bits/8 + 2 × 2 / group_size

Measured against real file sizes for six checkpoints spanning 4-bit, 8-bit and
bf16, dense and MoE: **0.0% error on every one.** The numbers below are those
measurements, so a regression in the formula fails here rather than in the field.

**2. Safety margins were stacked three deep.** Weights × 1.25, compared against
75% of a budget that was itself 75% of Apple's already-safe working set. A 48 GB
Mac was budgeted 26.6 GB against the 35.5 GB Apple says is addressable.

**3. MoE was misunderstood — by users, and nearly by us.** "30B with 3B active"
reads like a 3B memory footprint. Under stock mlx-lm every expert is resident;
only the COMPUTE is sparse. Expert streaming exists in third-party forks, not in
the engine LocalBook runs.
"""
import pytest

from services import model_sizing
from services.model_catalog import fit_for, quant_bits_of, size_gb_of


# Real metadata and real on-disk sizes, measured from the Hub 2026-09-22.
# (repo, safetensors parameters, quantization config, actual GiB of weights)
MEASURED = [
    ("mlx-community/gemma-4-e4b-it-4bit",
     {"BF16": 472_749_386, "U32": 7_468_351_488}, {"bits": 4, "group_size": 64}, 4.79),
    ("mlx-community/Qwen3-30B-A3B-4bit",
     {"U32": 30_531_911_680, "BF16": 210_944}, {"bits": 4, "group_size": 64}, 16.00),
    ("mlx-community/Qwen3-30B-A3B-8bit",
     {"U32": 30_531_911_680, "BF16": 210_944}, {"bits": 8, "group_size": 64}, 30.21),
    ("mlx-community/Qwen3-32B-4bit",
     {"U32": 32_761_446_400, "BF16": 676_864}, {"bits": 4, "group_size": 64}, 17.16),
    ("mlx-community/Phi-4-mini-instruct-4bit",
     {"U32": 3_835_822_080, "F16": 199_680}, {"bits": 4, "group_size": 64}, 2.01),
]


@pytest.mark.parametrize("repo,params,quant,real_gb", MEASURED)
def test_quantized_weights_match_the_bytes_on_disk(repo, params, quant, real_gb):
    """The formula is arithmetic, not a heuristic — within 2% of real files."""
    got = size_gb_of({"parameters": params}, model_id=repo,
                     config={"quantization_config": quant})
    assert got == pytest.approx(real_gb, rel=0.02), \
        f"{repo}: computed {got} GB against {real_gb} GB on disk"


def test_the_bug_that_started_this_is_gone():
    """113.74 GB for a model that occupies 16.00 GB."""
    repo, params, quant, real = MEASURED[1]
    naive = sum(4 * n if dt == "U32" else 2 * n for dt, n in params.items()) / 1024 ** 3
    assert naive > 100, "the fixture no longer reproduces the original error"
    assert size_gb_of({"parameters": params}, model_id=repo,
                      config={"quantization_config": quant}) < real * 1.02


def test_four_bit_and_eight_bit_are_told_apart():
    """Both report the same parameter count under the same dtype. Without the
    quantization config they are the same number — a 16 GB model and a 30 GB one
    indistinguishable, which is the difference between fits and does not."""
    _, params, _, _ = MEASURED[1]
    four = size_gb_of({"parameters": params}, model_id="x-4bit",
                      config={"quantization_config": {"bits": 4, "group_size": 64}})
    eight = size_gb_of({"parameters": params}, model_id="x-8bit",
                       config={"quantization_config": {"bits": 8, "group_size": 64}})
    assert eight > four * 1.8


def test_bits_come_from_the_config_first_then_the_name():
    """mlx-community names checkpoints `-4bit`/`-8bit`, which is the only signal
    when a repo omits quantization_config."""
    assert quant_bits_of("anything", {"quantization_config": {"bits": 8}}) == 8
    assert quant_bits_of("mlx-community/Qwen3-30B-A3B-4bit", None) == 4
    assert quant_bits_of("mlx-community/Qwen3-30B-A3B-4bit",
                         {"quantization_config": {"bits": 8}}) == 8   # config wins
    assert quant_bits_of("mlx-community/arctic-embed-l-v2.0-bf16", None) is None


def test_an_unquantized_model_is_unaffected():
    """bf16 was never wrong and must not become so."""
    assert size_gb_of({"parameters": {"BF16": 567_754_752}},
                      model_id="mlx-community/snowflake-arctic-embed-l-v2.0-bf16") \
        == pytest.approx(1.06, rel=0.02)


# ── MoE ─────────────────────────────────────────────────────────────────────

def test_a_moe_model_costs_all_of_its_experts():
    """The misconception this whole investigation started from. Qwen3-30B-A3B
    activates ~3B parameters per token and occupies 16 GB at 4-bit, not 1.6 GB:
    under stock mlx-lm every expert is resident and only the compute is sparse.
    """
    _, params, quant, real = MEASURED[1]
    total = size_gb_of({"parameters": params}, model_id="x",
                       config={"quantization_config": quant})
    assert total == pytest.approx(real, rel=0.02)
    active_fraction = 3.0 / 30.5
    assert total > real * 0.9, "sized as if only the active experts were resident"
    assert total > (real * active_fraction) * 5


# ── KV cache ────────────────────────────────────────────────────────────────

QWEN_MOE_CFG = {"num_hidden_layers": 48, "num_attention_heads": 32,
                "num_key_value_heads": 4, "head_dim": 128,
                "hidden_size": 2048, "max_position_embeddings": 40960}


def test_kv_respects_grouped_query_attention():
    """Qwen3-30B-A3B has 32 attention heads but only 4 KV heads. Sizing KV by
    attention heads would overstate it eightfold — 96 GB instead of 12 GB at
    128k, which is its own route to a bogus "needs 105 GB"."""
    with_gqa = model_sizing.kv_cache_gb(QWEN_MOE_CFG, 131072)
    without = model_sizing.kv_cache_gb(dict(QWEN_MOE_CFG, num_key_value_heads=32), 131072)
    assert with_gqa == pytest.approx(12.0, rel=0.05)
    assert without == pytest.approx(with_gqa * 8, rel=0.05)


def test_kv_is_judged_at_the_deployed_context_not_the_native_one():
    """40k native costs 3.75 GB; the 16k LocalBook deploys costs 1.5 GB. Judging
    a model by a context it will never run at is how a usable one is rejected."""
    assert model_sizing.kv_cache_gb(QWEN_MOE_CFG, 16384) < \
        model_sizing.kv_cache_gb(QWEN_MOE_CFG, 40960) / 2


# ── the budget ──────────────────────────────────────────────────────────────

@pytest.fixture
def machine(monkeypatch):
    def _set(ram_gb: float):
        model_sizing._CACHE["working_set"] = ram_gb * 0.74   # Apple's own ratio
    yield _set
    model_sizing._CACHE.pop("working_set", None)


def test_the_reserve_is_named_rather_than_a_bare_fraction():
    """"× 0.75" is not a reason. The reserve is what LocalBook keeps resident
    anyway — the embedding model and app overhead — and can be argued with."""
    assert model_sizing.RESIDENT_RESERVE_GB > 0
    model_sizing._CACHE["working_set"] = 35.5
    try:
        assert model_sizing.budget_gb() == pytest.approx(
            35.5 - model_sizing.RESIDENT_RESERVE_GB, rel=0.01)
    finally:
        model_sizing._CACHE.pop("working_set", None)


def test_safety_margins_are_not_stacked(machine):
    """Weights × 1.25, then 75% of a budget that was 75% of Apple's ceiling, is
    three margins on one number — which is how a 48 GB Mac got a 26.6 GB budget
    against the 35.5 GB Apple says is addressable."""
    machine(48)
    assert model_sizing.budget_gb() > 48 * 0.6


@pytest.mark.parametrize("ram_gb,expected", [
    (16, "over"),      # a 16 GB Mac cannot hold a 16 GB model plus its KV
    (24, "over"),
    (32, "tight"),     # reported in the wild at usable speeds
    (48, "fits"),      # the machine from the report
    (64, "fits"),
])
def test_the_verdict_for_a_30b_moe_matches_reality(machine, ram_gb, expected):
    machine(ram_gb)
    assert fit_for(16.0)["verdict"] == expected


def test_the_reported_machine_no_longer_rejects_the_model(machine):
    """The whole complaint: 48 GB, told it needed 105 GB."""
    machine(48)
    verdict = fit_for(16.0)
    assert verdict["verdict"] in ("fits", "tight")
    assert verdict["needed_gb"] < 25
