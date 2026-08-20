"""The model browser's judgement: origin, size, capability, fit.

Every assertion here is offline — the HF call is the only networked part and it is mocked.
A test that needed the Hub would fail on a plane and teach us nothing about our own logic.

The origin tests are the ones that matter most. CLAUDE.md forbids recommending or wiring in a
model from China or the Middle East, so `search()` WITHHOLDS them rather than labelling them —
showing a blocked model with a red flag next to a Download button is still recommending it.
"""
import pytest

from services import model_catalog as mc


# ── Origin: a hard policy, not a decoration ─────────────────────────────────────

@pytest.mark.parametrize("model_id,tags,vendor,allowed", [
    # The repo OWNER is a repackager; the base_model tag names who actually built it.
    ("mlx-community/gemma-4-e4b-it-4bit", ["base_model:google/gemma-4-E4B-it"], "Google", True),
    ("mlx-community/Phi-4-mini-instruct-4bit", ["base_model:microsoft/Phi-4-mini"], "Microsoft", True),
    ("mlx-community/Mistral-7B-v0.3-4bit", ["base_model:mistralai/Mistral-7B"], "Mistral AI", True),
    # Blocked origins, reached through the base_model tag…
    ("mlx-community/Qwen3-8B-4bit", ["base_model:Qwen/Qwen3-8B"], "Alibaba / Qwen", False),
    ("mlx-community/DeepSeek-R1-4bit", ["base_model:deepseek-ai/DeepSeek-R1"], "DeepSeek", False),
    # …and through the name, when the repackager published no base_model tag at all.
    ("mlx-community/Qwen3-14B-4bit", [], "Alibaba / Qwen", False),
    ("someone/falcon-40b-mlx", [], "TII (Falcon)", False),
    ("someone/jais-13b-mlx", [], "Core42 (Jais)", False),
])
def test_origin_resolution(model_id, tags, vendor, allowed):
    o = mc.origin_of(model_id, tags)
    assert o["vendor"] == vendor, o
    assert o["allowed"] is allowed, o
    assert o["flag"], "every origin needs a flag for the UI"


def test_blocked_models_are_withheld_not_merely_flagged(monkeypatch):
    """THE policy test. A blocked model must not reach the UI at all by default."""
    fake = [
        {"id": "mlx-community/Qwen3-8B-4bit", "tags": ["mlx", "base_model:Qwen/Qwen3-8B"],
         "pipeline_tag": "text-generation", "safetensors": {"parameters": {"BF16": 1_000_000}},
         "downloads": 999999, "likes": 999},
        {"id": "mlx-community/gemma-4-e4b-it-4bit", "tags": ["mlx", "base_model:google/gemma-4-E4B-it"],
         "pipeline_tag": "text-generation", "safetensors": {"parameters": {"BF16": 1_000_000}},
         "downloads": 10, "likes": 1},
    ]
    monkeypatch.setattr(mc, "_get_json", lambda *a, **k: fake)
    mc.reset_cache()
    res = mc.search()
    ids = [m["model_id"] for m in res["models"]]
    assert "mlx-community/Qwen3-8B-4bit" not in ids
    assert "mlx-community/gemma-4-e4b-it-4bit" in ids
    assert res["blocked_hidden"] == 1, "the withholding must be VISIBLE, not silent"


def test_blocked_models_can_be_revealed_deliberately(monkeypatch):
    """`include_blocked` exists for auditing what the policy is excluding. The card still
    carries allowed=False so nothing downstream can treat it as installable."""
    fake = [{"id": "mlx-community/Qwen3-8B-4bit", "tags": ["mlx"], "pipeline_tag": "text-generation",
             "safetensors": {"parameters": {"BF16": 1_000_000}}, "downloads": 1, "likes": 0}]
    monkeypatch.setattr(mc, "_get_json", lambda *a, **k: fake)
    mc.reset_cache()
    res = mc.search(include_blocked=True)
    assert len(res["models"]) == 1
    assert res["models"][0]["origin"]["allowed"] is False


# ── Size, from the real dtype breakdown ─────────────────────────────────────────

def test_size_uses_the_published_dtype_composition():
    """A 4-bit MLX checkpoint packs weights into U32 words, so counting parameters alone
    under-reports badly. Using HF's dtype map reproduces the real on-disk size: this is
    gemma-4-e4b's actual metadata, and 4.79 GB is what it occupies."""
    assert mc.size_gb_of({"parameters": {"BF16": 706135370, "U32": 933543936}}) == 4.79


def test_size_is_none_when_the_repo_publishes_nothing():
    assert mc.size_gb_of(None) is None
    assert mc.size_gb_of({}) is None


# ── Capabilities → role eligibility ─────────────────────────────────────────────

def test_an_embedder_is_never_offered_as_a_chat_model():
    """THE slotting bug in its newest form: an embedding model assigned to Main produces
    nothing at all, with no error."""
    caps = mc.capabilities_of("sentence-similarity", ["sentence-transformers", "mteb"])
    assert caps["embedding"] and not caps["text"]
    assert mc.roles_for(caps, 1.1) == ["embedding"]


def test_a_vision_model_covers_main_and_vision():
    caps = mc.capabilities_of("image-text-to-text", ["mlx-vlm"])
    assert caps["vision"] and caps["text"]
    assert set(mc.roles_for(caps, 4.8)) == {"main", "vision"}


def test_only_small_models_are_suggested_for_the_fast_slot():
    caps = mc.capabilities_of("text-generation", ["mlx-lm"])
    assert "fast" in mc.roles_for(caps, 2.0)
    assert "fast" not in mc.roles_for(caps, 14.0)


def test_a_model_that_fills_no_slot_is_not_listed(monkeypatch):
    """ASR models, re-rankers and depth estimators are all `mlx`-tagged. Listing one offers a
    download the app has nowhere to put."""
    fake = [{"id": "mlx-community/parakeet-tdt-0.6b-v2", "tags": ["mlx"],
             "pipeline_tag": "automatic-speech-recognition",
             "safetensors": {"parameters": {"F32": 617869958}}, "downloads": 2243785, "likes": 45}]
    monkeypatch.setattr(mc, "_get_json", lambda *a, **k: fake)
    mc.reset_cache()
    assert mc.search()["models"] == []


# ── Fit ─────────────────────────────────────────────────────────────────────────

def test_fit_is_judged_against_addressable_memory_not_total_ram():
    small, big = mc.fit_for(1.0), mc.fit_for(400.0)
    assert small["verdict"] == "fits"
    assert big["verdict"] == "over"
    assert big["budget_gb"] and big["budget_gb"] < 64, "budget must come from the working set"


def test_unknown_size_never_claims_to_fit():
    """Reporting 'fits' for a model whose size we do not know is the failure mode that made
    the old ram_fit useless — it read 0.00 GB as 'fits'."""
    assert mc.fit_for(None)["verdict"] == "unknown"


# ── Offline behaviour ───────────────────────────────────────────────────────────

def test_offline_returns_a_reason_rather_than_raising(monkeypatch):
    """The browser is the one networked surface. Losing the Hub must not break the panel or
    imply the user's downloaded models are gone."""
    monkeypatch.setattr(mc, "_get_json", lambda *a, **k: None)
    mc.reset_cache()
    res = mc.search()
    assert res["offline"] is True
    assert res["models"] == []
    assert "Hugging Face" in res["reason"]


# ── Architecture-based lineage (2026-08-20) ─────────────────────────────────────

@pytest.mark.parametrize("model_id,arch,vendor", [
    # A third-party fine-tune keeps its base model's architecture but publishes under the
    # fine-tuner's account with no base_model tag. Measured live: 24 of 31 unresolved models
    # were Chinese-origin derivatives being offered as allowed because nothing named them.
    ("prism-ml/Bonsai-8B-mlx-1bit",                    "qwen3",       "Alibaba / Qwen"),
    ("majentik/UI-Mate-27B-MLX-3bit",                  "qwen3_5",     "Alibaba / Qwen"),
    ("Shiftedx/ornith-1.0-35b-mxfp4-mtplx",            "qwen3_5_moe", "Alibaba / Qwen"),
    ("lmstudio-community/Seed-OSS-36B-Instruct-MLX",   "seed_oss",    "ByteDance (Seed)"),
    ("majentik/MiniMax-M2.7-TurboQuant-MLX-3bit",      "minimax_m2",  "MiniMax"),
])
def test_a_fine_tune_is_traced_through_its_architecture(model_id, arch, vendor):
    o = mc.origin_of(model_id, ["mlx", "safetensors", arch])
    assert o["vendor"] == vendor, o
    assert o["allowed"] is False, "a derivative of a blocked origin is itself blocked"
    assert o["verified"] is True


def test_architecture_outranks_the_publishing_account():
    """The owner of a fine-tune is the fine-tuner. Lineage is what the policy is about, so
    the architecture has to win — otherwise republishing under a new account launders it."""
    o = mc.origin_of("some-us-lab/friendly-name-mlx-4bit", ["mlx", "qwen3"])
    assert o["allowed"] is False


@pytest.mark.parametrize("arch,vendor", [
    ("gemma4", "Google"), ("llama", "Meta"), ("phi4", "Microsoft"),
    ("mistral", "Mistral AI"), ("smolvlm", "Hugging Face"),
])
def test_allowed_lineages_resolve_too(arch, vendor):
    assert mc.origin_of(f"repackager/thing-{arch}-mlx", ["mlx", arch])["vendor"] == vendor


def test_an_unattributable_model_names_its_lab_and_says_so():
    """When nothing identifies the lineage, name the publishing ACCOUNT — a checkable fact —
    and mark it unverified rather than implying it was cleared."""
    o = mc.origin_of("VertexAGI/prism-caption-1-micro", ["mlx", "safetensors"])
    assert o["lab"] == "VertexAGI"
    assert o["vendor"] == "VertexAGI"
    assert o["verified"] is False
    assert o["flag"], "still needs a placeholder flag so the row renders"
