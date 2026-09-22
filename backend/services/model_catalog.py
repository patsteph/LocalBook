"""Browse the Hugging Face MLX catalog: what exists, what fits, what we're allowed to run.

The Locker shows models already on disk. This is the other half — discovery — and it answers
three questions the Locker cannot:

  1. **What exists?** Live HF search, sortable by downloads / likes / recency.
  2. **Will it fit THIS Mac?** Size from the checkpoint's real dtype breakdown, judged against
     the GPU's addressable working set via `model_sizing`, not against total RAM.
  3. **Are we allowed to run it?** Origin is a POLICY question here, not a flag decoration —
     see `ORIGIN_POLICY` below.

Everything is derived from HF's metadata API (no weights fetched): `safetensors.parameters`
gives a real dtype→count breakdown, `base_model:` tags name the upstream vendor, and
`pipeline_tag` plus the tag list settle capabilities.

⚠️ This is the ONE part of the app that legitimately needs the network. Every call degrades to
an empty result with a reason rather than raising, so an offline Mac gets a clear "can't reach
Hugging Face" instead of a broken panel.
"""
from __future__ import annotations

import logging
import re
import time
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

HF_API = "https://huggingface.co/api"
_TTL_S = 300.0
_CACHE: Dict[str, Tuple[float, Any]] = {}


# ── Origin ──────────────────────────────────────────────────────────────────────
# The browser shows EVERYTHING and labels where each model came from; filtering is the user's
# decision, not ours (user call, 2026-08-20 — this reverses an earlier default that withheld
# CN/AE models outright). `allowed` therefore no longer gates the listing; it drives the
# OPTIONAL origin filter and the label, and callers can still ask for a restricted set.
#
# TWO SEPARATE FACTS, NEVER MERGED (rewritten 2026-08-20 after a live report).
# The first cut collapsed "who published this checkpoint" and "what these weights derive
# from" into ONE country flag. `prism-ml/Bonsai-8B-mlx-1bit` is published by Prism ML, a US
# company, on a `qwen3` architecture — and the row rendered as 🇨🇳 Alibaba / Qwen. The flag
# stated something false about a real company, and it did so on the exact screen where users
# exclude models by country. So:
#
#   publisher — the HF account. A hard fact. We attach a COUNTRY ONLY when the account is in
#               the curated table below. There is no reliable publisher-country signal in HF
#               metadata (`region:` is a CDN region — it reads `region:us` on all 300 top MLX
#               repos, Qwen included), so an unknown account gets NO country claim at all.
#   lineage   — what the weights derive from, via the `base_model:` chain and then the
#               architecture. This is what an origin filter is actually about, and it is the
#               hardest signal to disguise: the architecture has to match the weights.
#
# The row leads with the publisher's flag when we know it and the lineage flag otherwise; the
# lineage always rides along as its own labelled chip. `allowed` follows LINEAGE, because
# republishing under a new account must not launder provenance.
#
# Keyed by the org that appears in a model id or a `base_model:` tag. The value is
# (display vendor, ISO country, flag, unrestricted).
VENDORS: Dict[str, Tuple[str, str, str, bool]] = {
    # Allowed
    "google":            ("Google", "US", "🇺🇸", True),
    "meta-llama":        ("Meta", "US", "🇺🇸", True),
    "facebook":          ("Meta", "US", "🇺🇸", True),
    "microsoft":         ("Microsoft", "US", "🇺🇸", True),
    "ibm-granite":       ("IBM", "US", "🇺🇸", True),
    "ibm":               ("IBM", "US", "🇺🇸", True),
    "mistralai":         ("Mistral AI", "FR", "🇫🇷", True),
    "bigcode":           ("BigCode / ServiceNow", "US", "🇺🇸", True),
    "servicenow":        ("ServiceNow", "US", "🇺🇸", True),
    "snowflake":         ("Snowflake", "US", "🇺🇸", True),
    "sentence-transformers": ("Sentence-Transformers", "DE", "🇩🇪", True),
    "baai":              ("BAAI", "CN", "🇨🇳", False),
    "nvidia":            ("NVIDIA", "US", "🇺🇸", True),
    "openai":            ("OpenAI", "US", "🇺🇸", True),
    "allenai":           ("Allen Institute", "US", "🇺🇸", True),
    "apple":             ("Apple", "US", "🇺🇸", True),
    "black-forest-labs": ("Black Forest Labs", "DE", "🇩🇪", True),
    "stabilityai":       ("Stability AI", "GB", "🇬🇧", True),
    "runpod":            ("RunPod", "US", "🇺🇸", True),
    "liquidai":          ("Liquid AI", "US", "🇺🇸", True),
    "huggingface":       ("Hugging Face", "US", "🇺🇸", True),
    "kyutai":            ("Kyutai", "FR", "🇫🇷", True),
    "cohereforai":       ("Cohere", "CA", "🇨🇦", True),
    "prism-ml":          ("Prism ML", "US", "🇺🇸", True),
    "prismml":           ("Prism ML", "US", "🇺🇸", True),

    # Blocked by policy (China / Middle East and derivatives)
    "qwen":              ("Alibaba / Qwen", "CN", "🇨🇳", False),
    "alibaba-nlp":       ("Alibaba", "CN", "🇨🇳", False),
    "deepseek-ai":       ("DeepSeek", "CN", "🇨🇳", False),
    "01-ai":             ("01.AI (Yi)", "CN", "🇨🇳", False),
    "internlm":          ("InternLM", "CN", "🇨🇳", False),
    "thudm":             ("Zhipu / GLM", "CN", "🇨🇳", False),
    "zhipuai":           ("Zhipu / GLM", "CN", "🇨🇳", False),
    "baichuan-inc":      ("Baichuan", "CN", "🇨🇳", False),
    "tiiuae":            ("TII (Falcon)", "AE", "🇦🇪", False),
    "core42":            ("Core42 (Jais)", "AE", "🇦🇪", False),
    "moonshotai":        ("Moonshot", "CN", "🇨🇳", False),
    "minimaxai":         ("MiniMax", "CN", "🇨🇳", False),
    "bytedance":         ("ByteDance (Seed)", "CN", "🇨🇳", False),
    "01ai":              ("01.AI (Yi)", "CN", "🇨🇳", False),
}

# Accounts whose business is republishing OTHER people's weights (quantising, converting to
# MLX). The account is a real, checkable fact, but reading a country off it would be
# meaningless — `mlx-community` hosts Google, Alibaba and Mistral conversions side by side.
# These never contribute a publisher country; their rows lead with the lineage flag.
REPACKAGERS = {
    "mlx-community", "lmstudio-community", "mflux-community", "mlxbits",
    "argmaxinc", "nightmedia", "inferencerlabs", "mlx-vision",
}

# ARCHITECTURE → origin. THE most reliable signal, and the one that closes the real hole:
# a third-party fine-tune keeps its base model's `model_type` (a Qwen fine-tune is still
# `qwen3`), but the repackager routinely drops the `base_model:` tag and publishes under
# their own account. Measured 2026-08-20: 24 of 31 unresolved models were Chinese-origin
# derivatives — Qwen fine-tunes, ByteDance Seed, MiniMax — every one of which was being
# offered as allowed because nothing identified them.
#
# Architecture is far harder to disguise than a repo name: it has to match the weights.
ARCH_ORIGIN = {
    # Blocked lineages
    "qwen2": "qwen", "qwen3": "qwen", "qwen3_5": "qwen", "qwen2_moe": "qwen",
    "qwen3_moe": "qwen", "qwen3_5_moe": "qwen", "qwen2_vl": "qwen", "qwen3_vl": "qwen",
    "deepseek_v2": "deepseek-ai", "deepseek_v3": "deepseek-ai", "deepseek_vl": "deepseek-ai",
    "minimax_m2": "minimaxai", "minimax": "minimaxai",
    "seed_oss": "bytedance",
    "glm4": "thudm", "glm4v": "thudm", "chatglm": "thudm",
    "internlm2": "internlm", "internlm3": "internlm",
    "baichuan": "baichuan-inc",
    "yi": "01-ai",
    # Allowed lineages
    "gemma": "google", "gemma2": "google", "gemma3": "google", "gemma4": "google",
    "llama": "meta-llama", "llama4": "meta-llama", "mllama": "meta-llama",
    "mistral": "mistralai", "mixtral": "mistralai",
    "phi3": "microsoft", "phi4": "microsoft", "phimoe": "microsoft",
    "granite": "ibm-granite", "granitemoe": "ibm-granite",
    "cohere": "cohereforai", "cohere2": "cohereforai",
    "olmo": "allenai", "olmo2": "allenai", "olmoe": "allenai",
    "smolvlm": "huggingface", "smollm": "huggingface", "smollm3": "huggingface",
    "starcoder2": "bigcode",
    "whisper": "openai", "gpt_oss": "openai",
    "xlm-roberta": "sentence-transformers", "bert": "sentence-transformers",
    "flux": "black-forest-labs",
}

# Substring fallbacks for when the org is a repackager (mlx-community, lmstudio-community…)
# and the base_model tag is missing. Matched against the lowercased model id.
NAME_HINTS: List[Tuple[str, str]] = [
    ("qwen", "qwen"), ("deepseek", "deepseek-ai"), ("yi-", "01-ai"),
    ("internlm", "internlm"), ("glm", "thudm"), ("baichuan", "baichuan-inc"),
    ("falcon", "tiiuae"), ("jais", "core42"), ("kimi", "moonshotai"),
    ("gemma", "google"), ("llama", "meta-llama"), ("phi-", "microsoft"),
    ("mistral", "mistralai"), ("granite", "ibm-granite"), ("starcoder", "bigcode"),
    ("arctic", "snowflake"), ("flux", "black-forest-labs"), ("olmo", "allenai"),
    ("lfm2", "liquidai"), ("whisper", "openai"), ("parakeet", "nvidia"),
]

UNKNOWN_ORIGIN = ("Unknown", "", "🏳️", True)

# Bytes per weight for the dtypes HF reports, so a size estimate uses the checkpoint's real
# composition instead of a guess from the filename.
_DTYPE_BYTES = {"F64": 8, "F32": 4, "BF16": 2, "F16": 2, "F8_E4M3": 1, "F8_E5M2": 1,
                "I64": 8, "I32": 4, "U32": 4, "I16": 2, "U16": 2, "I8": 1, "U8": 1,
                "BOOL": 1, "I4": 0.5, "U4": 0.5}


def _base_model_orgs(tags: Optional[List[str]]) -> List[str]:
    """Every org named by a `base_model:` tag, in tag order.

    HF writes these three ways, and the meaningful part is always the last colon-segment:
        base_model:google/gemma-4-E4B-it
        base_model:finetune:prism-ml/Bonsai-8B-unpacked
        base_model:quantized:Qwen/Qwen3-8B
    """
    orgs: List[str] = []
    for t in (tags or []):
        if not t.startswith("base_model:"):
            continue
        ref = t.split(":")[-1]
        if "/" in ref:
            org = ref.split("/", 1)[0].lower()
            if org not in orgs:
                orgs.append(org)
    return orgs


def publisher_of(model_id: str, tags: Optional[List[str]] = None) -> Dict[str, Any]:
    """Who published THIS checkpoint — the HF account, plus a country only if we know it.

    The account is a hard fact. The country is not: there is no publisher-country signal in
    HF metadata, so we attach one only for accounts in the curated `VENDORS` table and make
    NO claim otherwise. Guessing here is what put a 🇨🇳 flag on a US company's model.
    """
    account = model_id.split("/", 1)[0] if "/" in model_id else ""
    low = account.lower()
    if low in REPACKAGERS:
        return {"account": account, "vendor": account, "country": "", "flag": "",
                "known": False, "repackager": True}
    if low in VENDORS:
        vendor, country, flag, allowed = VENDORS[low]
        return {"account": account, "vendor": vendor, "country": country, "flag": flag,
                "known": True, "repackager": False, "allowed": allowed}
    return {"account": account, "vendor": account or "Unknown", "country": "", "flag": "",
            "known": False, "repackager": False}


def lineage_of(model_id: str, tags: Optional[List[str]] = None) -> Dict[str, Any]:
    """What the weights derive from. Ordered by how hard the signal is to fake.

    `source` is reported so the UI can be honest about strength — an architecture match is a
    property of the weights, a name match is only a naming convention.
    """
    tags = tags or []
    publisher_org = (model_id.split("/", 1)[0] or "").lower()
    # 1) The publisher's own statement of what this is built on.
    for org in _base_model_orgs(tags):
        if org == publisher_org:
            # An intermediate repo in the publisher's OWN account (`prism-ml/Bonsai-8B-mlx-1bit`
            # → `base_model:prism-ml/Bonsai-8B-unpacked`) names a packaging step, not the
            # upstream these weights came from. Keep walking to the architecture — otherwise
            # any lab could mask a base model's lineage just by re-uploading it under its own
            # name first, which is the laundering the architecture check exists to stop.
            continue
        if org in VENDORS:
            vendor, country, flag, allowed = VENDORS[org]
            return {"vendor": vendor, "country": country, "flag": flag, "allowed": allowed,
                    "org": org, "source": "base_model", "known": True}
    # 2) ARCHITECTURE — the strongest signal, because it has to match the weights. A
    #    third-party fine-tune keeps its base model's `model_type` even when the repackager
    #    drops the base_model tag and publishes under their own account.
    for t in tags:
        key = ARCH_ORIGIN.get(t.lower())
        if key:
            vendor, country, flag, allowed = VENDORS[key]
            return {"vendor": vendor, "country": country, "flag": flag, "allowed": allowed,
                    "org": key, "source": "architecture", "known": True}
    # 3) Name hint — weakest. A convention, not a fact about the weights.
    low = model_id.lower()
    for needle, key in NAME_HINTS:
        if needle in low:
            vendor, country, flag, allowed = VENDORS[key]
            return {"vendor": vendor, "country": country, "flag": flag, "allowed": allowed,
                    "org": key, "source": "name", "known": True}
    return {"vendor": "", "country": "", "flag": "", "allowed": True,
            "org": "", "source": "", "known": False}


def origin_of(model_id: str, tags: Optional[List[str]] = None) -> Dict[str, Any]:
    """Publisher + lineage, and the single flag the row leads with.

    The two are kept as separate fields precisely so the UI never has to merge them again.
    """
    pub = publisher_of(model_id, tags)
    lin = lineage_of(model_id, tags)

    # Lead with the publisher's flag when the account is known; otherwise the lineage flag.
    # Never a placeholder country — an unknown publisher simply contributes no flag.
    flag = pub["flag"] if pub["known"] else lin["flag"]

    # `allowed` follows LINEAGE first: republishing under a new account must not launder
    # provenance. A known publisher that is itself restricted also disqualifies.
    allowed = bool(lin["allowed"]) and bool(pub.get("allowed", True))

    # Every country this model touches, for the origin filter. Both axes count, so excluding
    # CN hides a Qwen fine-tune however it was published.
    countries = {c for c in (lin.get("country"), pub.get("country") if pub["known"] else "") if c}

    return {
        "publisher": pub,
        "lineage": lin,
        "flag": flag,
        "allowed": allowed,
        "countries": sorted(countries),
        # Back-compat single-label fields. The label prefers the lineage vendor, since that is
        # what "where did this model come from" means; the publisher is shown alongside it.
        "vendor": lin["vendor"] or pub["vendor"],
        "country": lin.get("country") or (pub.get("country") if pub["known"] else ""),
        "org": lin.get("org") or pub["account"].lower(),
        "lab": pub["account"],
        "verified": bool(lin["known"] or pub["known"]),
    }


# MLX packs quantized weights into U32 words, and HF reports the LOGICAL parameter
# count against that dtype — not the number of words. Multiplying by 4 bytes
# therefore overstates a 4-bit checkpoint by ~7×: Qwen3-30B-A3B-4bit came out at
# 113.74 GB against a real 16.00 GB, and gemma-4-e4b at 28.70 GB against 4.79 GB.
# It also made 4-bit and 8-bit indistinguishable, since both report the same
# count under the same dtype.
#
# The true cost of an MLX quantized tensor is the weights PLUS a scale and a bias
# (fp16 each) per group:
#
#     bytes/param = bits/8 + 2 × 2 / group_size
#
# Measured against real file sizes for gemma-4-e4b-4bit, Qwen3-30B-A3B at 4- and
# 8-bit, Qwen3-32B-4bit, Phi-4-mini-4bit and arctic-embed-bf16: **0.0% error on
# every one**. This is arithmetic, not a heuristic.
_MLX_DEFAULT_GROUP_SIZE = 64
_QUANT_SUFFIX = re.compile(r"-(\d+)bit\b", re.IGNORECASE)


def quant_bits_of(model_id: str, config: Optional[dict]) -> Optional[int]:
    """Bits per weight, from the config if present, else the repo name.

    mlx-community names checkpoints `...-4bit` / `...-8bit`, which is a real
    signal and the only one available when a repo omits `quantization_config`.
    """
    q = (config or {}).get("quantization_config") or (config or {}).get("quantization") or {}
    bits = q.get("bits")
    if isinstance(bits, int) and 1 <= bits <= 16:
        return bits
    m = _QUANT_SUFFIX.search(model_id or "")
    if m:
        try:
            b = int(m.group(1))
            if 1 <= b <= 16:
                return b
        except ValueError:
            pass
    return None


def size_gb_of(safetensors: Optional[dict], *, model_id: str = "",
               config: Optional[dict] = None) -> Optional[float]:
    """Weight size in GiB from HF's dtype→parameter breakdown.

    Quantization-aware: see the note above on why the raw dtype width is wrong
    for MLX checkpoints. Weights only — KV and activations are the caller's
    problem, and `model_sizing.exact_weight_gb` is authoritative once the model
    is on disk.
    """
    if not safetensors:
        return None
    params = safetensors.get("parameters") or {}
    if not params:
        total = safetensors.get("total")
        return round(total * 2 / 1024 ** 3, 2) if total else None

    bits = quant_bits_of(model_id, config)
    group = ((config or {}).get("quantization_config") or {}).get("group_size") \
        or _MLX_DEFAULT_GROUP_SIZE
    total_bytes = 0.0
    for dt, n in params.items():
        if not isinstance(n, (int, float)):
            continue
        if dt in ("U32", "I32") and bits:
            # Packed quantized weights: real cost is bits/8 plus scale + bias
            # per group, NOT the 4 bytes of the container word.
            total_bytes += n * (bits / 8 + 2 * 2 / group)
        else:
            total_bytes += n * _DTYPE_BYTES.get(dt, 2)
    return round(total_bytes / 1024 ** 3, 2) if total_bytes else None


def capabilities_of(pipeline_tag: Optional[str], tags: Optional[List[str]]) -> Dict[str, bool]:
    """What roles this model could fill, from HF metadata alone (no download)."""
    t = {x.lower() for x in (tags or [])}
    pt = (pipeline_tag or "").lower()
    embedding = bool(
        pt in {"sentence-similarity", "feature-extraction"}
        or {"sentence-transformers", "mteb"} & t
    )
    vision = bool(
        pt in {"image-text-to-text", "visual-question-answering", "image-to-text"}
        or {"mlx-vlm", "vision"} & t
    )
    image_gen = bool(pt in {"text-to-image"} or {"diffusers", "flux", "mflux"} & t)
    audio = bool(pt in {"automatic-speech-recognition", "text-to-speech", "audio-to-audio"})
    # Text generation is the default for a causal LM, but an embedder or an ASR model is NOT
    # a chat model — saying otherwise is how a model lands in the Main slot and produces
    # nothing.
    text = bool(pt in {"text-generation", "image-text-to-text"} or "mlx-lm" in t) and not embedding
    return {"text": text, "vision": vision, "embedding": embedding,
            "image": image_gen, "audio": audio}


def roles_for(caps: Dict[str, bool], size_gb: Optional[float]) -> List[str]:
    """Which LocalBook role slots this model is eligible for."""
    roles: List[str] = []
    if caps.get("embedding"):
        return ["embedding"]
    if caps.get("image"):
        return ["image"]
    if caps.get("text"):
        # Size is the only signal for main-vs-fast, and it is a suggestion: the user assigns
        # roles explicitly in the Locker.
        roles.append("main")
        if size_gb is not None and size_gb <= 4.0:
            roles.append("fast")
    if caps.get("vision"):
        roles.append("vision")
    return roles


def fit_for(size_gb: Optional[float]) -> Dict[str, Any]:
    """Will it fit THIS Mac? Judged against the GPU's addressable working set."""
    try:
        from services import model_sizing
        budget = model_sizing.budget_gb()
        working = model_sizing.working_set_gb()
    except Exception:
        budget = working = 0.0
    if not size_gb or not budget:
        return {"verdict": "unknown", "budget_gb": budget or None, "working_set_gb": working or None}
    # Headroom for KV + activations. The list API carries no layer geometry, so
    # KV cannot be computed here the way `model_sizing.fit()` does once a model
    # is downloaded — 1.2× is the standard allowance (HF accelerate, EleutherAI)
    # and covers a GQA model at a normal context.
    #
    # The threshold is applied ONCE. Multiplying by 1.25 and then requiring 75%
    # of a budget that was itself 75% of Apple's safe ceiling meant three
    # independent safety margins stacked on one number.
    needed = size_gb * 1.2
    if needed <= budget * 0.9:
        verdict = "fits"
    elif needed <= budget:
        verdict = "tight"
    else:
        verdict = "over"
    return {"verdict": verdict, "needed_gb": round(needed, 2),
            "budget_gb": round(budget, 2), "working_set_gb": round(working, 2)}


def _cached(key: str):
    hit = _CACHE.get(key)
    if hit and (time.time() - hit[0]) < _TTL_S:
        return hit[1]
    return None


def _reason_for_status(code: int) -> str:
    """What an HTTP status from the Hub means, in the user's terms."""
    if code == 429:
        return "Hugging Face is rate-limiting this machine (HTTP 429). Try again in a minute."
    if code in (401, 403):
        return (f"Hugging Face refused the request (HTTP {code}). A gated or private model "
                "needs an access token.")
    if code == 404:
        return "Hugging Face has no such model (HTTP 404)."
    if code >= 500:
        return f"Hugging Face is having trouble (HTTP {code}). Try again shortly."
    return f"Hugging Face returned HTTP {code}."


def _reason_for_exception(e: Exception, detail: str) -> str:
    """What a transport failure means. The three cases below are NOT the same problem."""
    low = detail.lower()
    if "certificate" in low or "ssl" in low or "tls" in low:
        # The one that cost six diagnostic commands on 2026-09-14. macOS trusts the
        # intercepting root, Python doesn't, so curl succeeds while the app fails — and
        # "check your connection" sends the user hunting in exactly the wrong place.
        return ("Could not verify Hugging Face's certificate. This network appears to inspect "
                "HTTPS traffic; its root certificate has to be trusted by this Mac.")
    if "timeout" in type(e).__name__.lower() or "timed out" in low:
        return "Timed out reaching Hugging Face. The connection may be slow or blocked."
    return "Could not reach Hugging Face. The browser needs a connection."


def _get_json(url: str, timeout: float = 20.0) -> Tuple[Optional[Any], Optional[str]]:
    """GET JSON from the Hub. Returns `(data, None)` or `(None, reason)`.

    Failure is a normal state here, but the KINDS of failure are not interchangeable and
    this used to flatten all of them to `None` → "could not reach Hugging Face". A 429, a
    403 and a rejected certificate all read as an unplugged cable, and the log kept only
    `type(e).__name__` — which is `ConnectError` for a refused socket AND for a failed TLS
    handshake. Diagnosing one required reproducing it outside the app. So: keep `str(e)`,
    and say which of the three it was.
    """
    try:
        import httpx
        # Shares the app's TLS trust: `install_hf_transport` injects the system trust store,
        # which the client below inherits through `ssl`. (Before 2026-09-14 this call
        # configured huggingface_hub's client only — never this one — while the comment
        # claimed otherwise, so a machine needing the bypass could download but not browse.)
        try:
            from services.hf_transport import install_hf_transport
            install_hf_transport()
        except Exception:
            pass
        with httpx.Client(timeout=timeout, follow_redirects=True) as c:
            r = c.get(url, headers={"User-Agent": "LocalBook/2.3"})
            if r.status_code != 200:
                logger.warning(f"[model-catalog] HF returned {r.status_code} for {url[:120]}")
                return None, _reason_for_status(r.status_code)
            return r.json(), None
    except Exception as e:
        detail = str(e).strip() or type(e).__name__
        logger.warning(f"[model-catalog] HF request failed "
                       f"({type(e).__name__}: {detail[:200]}) for {url[:120]}")
        return None, _reason_for_exception(e, detail)


def _enrich(raw: dict, installed: set) -> dict:
    mid = raw.get("id") or raw.get("modelId") or ""
    tags = raw.get("tags") or []
    caps = capabilities_of(raw.get("pipeline_tag"), tags)
    cfg = raw.get("config") or {}
    size = size_gb_of(raw.get("safetensors"), model_id=mid, config=cfg)
    org = origin_of(mid, tags)
    experts = cfg.get("num_experts") or cfg.get("num_local_experts")
    active = cfg.get("num_experts_per_tok")
    moe = ({"experts": int(experts), "active_per_token": int(active) if active else None}
           if experts else None)
    lic = next((t.split(":", 1)[1] for t in tags if t.startswith("license:")), "")
    return {
        "model_id": mid,
        "name": mid.split("/", 1)[-1],
        "owner": mid.split("/", 1)[0] if "/" in mid else "",
        "downloads": raw.get("downloads") or 0,
        "likes": raw.get("likes") or 0,
        "updated": raw.get("lastModified") or "",
        "created": raw.get("createdAt") or "",
        "gated": bool(raw.get("gated")),
        "trending": raw.get("trendingScore") or 0,
        "license": lic,
        "pipeline_tag": raw.get("pipeline_tag") or "",
        "size_gb": size,
        "size_is_estimate": True,
        "quant_bits": quant_bits_of(mid, cfg),
        # A Mixture-of-Experts model keeps EVERY expert resident under stock
        # mlx-lm — only the compute is sparse. Expert streaming exists, but in
        # third-party forks, not the engine LocalBook runs. Surfaced because
        # "30B with 3B active" reads like a 3B memory footprint and is not one.
        "moe": moe,
        "capabilities": caps,
        "roles": roles_for(caps, size),
        "origin": org,
        "installed": mid in installed,
        "tags": [t for t in tags if not t.startswith(("base_model:", "license:", "region:"))][:12],
    }


# ── Which HF pipelines serve which role ─────────────────────────────────────────
# The browser MUST query per-pipeline. A single global page sorted by downloads is
# overwhelmingly chat models — measured 2026-08-20 across the top 300 `mlx` repos: 150
# text-generation and 84 image-text-to-text, but only 2 sentence-similarity, 2
# feature-extraction and 3 text-to-image. Filtering that page client-side returned ONE
# embedding model and ZERO image models, so both roles were effectively invisible even
# though the Hub has plenty of each.
ROLE_PIPELINES: Dict[str, Tuple[str, ...]] = {
    "embedding": ("sentence-similarity", "feature-extraction"),
    "image":     ("text-to-image",),
    "vision":    ("image-text-to-text",),
    "main":      ("text-generation", "image-text-to-text"),
    "fast":      ("text-generation",),
}

# The "All roles" view fans out across every pipeline we can place, so the default page
# represents the catalog instead of just its most-downloaded corner.
DEFAULT_PIPELINES: Tuple[str, ...] = (
    "text-generation", "image-text-to-text", "sentence-similarity",
    "feature-extraction", "text-to-image",
)

# Slots reserved per pipeline in the "All roles" page before the rest is filled by rank.
# Without this, a global sort re-buries the small categories the fan-out just surfaced.
_GUARANTEE_PER_PIPELINE = 3

_SORT_FIELD = {
    "trendingScore": "trending", "downloads": "downloads", "likes": "likes",
    "lastModified": "updated", "createdAt": "created",
}


def _fetch_pipeline(pipeline: str, query: str, sort: str, limit: int):
    """One HF page for a single pipeline. Returns `(rows, reason)`; caller decides offline."""
    # `filter=mlx` (the TAG), not `library=mlx`. The library form matches loosely and returns
    # plain sentence-transformers/BERT repos that this engine cannot load at all — verified:
    # its top results were all `mlx_tag=False`. The tag is what a genuine MLX conversion sets.
    parts = [
        f"{HF_API}/models?filter=mlx&sort={sort}&direction=-1&limit={limit}",
        "expand[]=downloads", "expand[]=likes", "expand[]=safetensors",
        "expand[]=config",
        "expand[]=tags", "expand[]=pipeline_tag", "expand[]=gated",
        "expand[]=lastModified", "expand[]=createdAt", "expand[]=trendingScore",
    ]
    if pipeline:
        parts.append(f"pipeline_tag={pipeline}")
    if query:
        from urllib.parse import quote
        parts.insert(1, f"search={quote(query)}")
    return _get_json("&".join(parts))


def search(
    query: str = "",
    sort: str = "downloads",
    limit: int = 40,
    role: str = "",
    include_blocked: bool = True,
    fits_only: bool = False,
    exclude_countries: str = "",
) -> Dict[str, Any]:
    """Search the MLX catalog on Hugging Face.

    sort: trendingScore | downloads | likes | lastModified | createdAt
    role: main | fast | vision | embedding | image  (queries that role's HF pipelines)
    exclude_countries: comma-separated ISO codes to hide, e.g. "CN,AE". Matched against
        BOTH the lineage and a known publisher country, so a Qwen fine-tune is hidden
        however it was republished. Empty shows every origin — the default, because the
        browser's job is to show what exists and let the user decide.
    """
    sort = sort if sort in _SORT_FIELD else "downloads"
    pipelines = ROLE_PIPELINES.get(role) or DEFAULT_PIPELINES
    per = min(max(limit * 2, 60), 200)

    ck = (f"search::{query}::{sort}::{role}::{include_blocked}::{fits_only}"
          f"::{limit}::{exclude_countries}")
    hit = _cached(ck)
    if hit is not None:
        return hit

    fetched = {p: _fetch_pipeline(p, query, sort, per) for p in pipelines}
    pages = {p: rows for p, (rows, _) in fetched.items()}
    if all(rows is None for rows in pages.values()):
        # Every pipeline failed the same way in practice — they are the same host, one after
        # another — so the first reason is the reason. The panel appends its own "models you
        # already downloaded still work" line, so this must not repeat it.
        reason = next((r for _, r in fetched.values() if r), "Could not reach Hugging Face.")
        return {"models": [], "offline": True, "reason": reason}

    try:
        from services.model_presence import enumerate_cached
        installed = {m["model_id"] for m in enumerate_cached()}
    except Exception:
        installed = set()

    excluded = {c.strip().upper() for c in exclude_countries.split(",") if c.strip()}
    ranked: Dict[str, List[dict]] = {}
    seen: set = set()
    restricted_n = filtered_n = 0

    for pipeline in pipelines:
        keep: List[dict] = []
        for r in (pages.get(pipeline) or []):
            card = _enrich(r, installed)
            mid = card["model_id"]
            if not mid or mid in seen:
                continue
            # Claim the id up front, so the counts below tally MODELS rather than
            # occurrences — the pipelines are queried separately and a model that is
            # filtered out must not be re-counted once per pipeline.
            seen.add(mid)
            if not card["origin"]["allowed"]:
                restricted_n += 1
                if not include_blocked:
                    continue
            if excluded and {c.upper() for c in card["origin"].get("countries", [])} & excluded:
                filtered_n += 1
                continue
            if not card["roles"]:
                # Eligible for no slot — an ASR model, a re-ranker, a depth estimator.
                # Listing it would offer a download the app has nowhere to put.
                continue
            if role and role not in card["roles"]:
                continue
            card["fit"] = fit_for(card["size_gb"])
            if fits_only and card["fit"]["verdict"] in ("over", "unknown"):
                continue
            keep.append(card)
        ranked[pipeline] = keep

    # Reserve a few slots per pipeline, then fill the rest strictly by rank. A purely global
    # sort would re-bury the small categories this fan-out exists to surface: embedders lose
    # to chat models by three orders of magnitude in downloads. With a single role selected
    # there is nothing to balance, so the quota is off.
    #
    # The quota decides WHICH models make the page, never where they sit on it — the final
    # sort below keeps display order exactly the one the user asked for.
    field = _SORT_FIELD[sort]
    def _rank(m: dict):
        return m.get(field) or 0

    guarantee = 0 if role else _GUARANTEE_PER_PIPELINE
    out: List[dict] = []
    for pipeline in pipelines:
        out.extend(ranked[pipeline][:guarantee])
    chosen = {m["model_id"] for m in out}
    rest = [m for p in pipelines for m in ranked[p] if m["model_id"] not in chosen]
    rest.sort(key=_rank, reverse=True)
    out.extend(rest[:max(0, limit - len(out))])
    out.sort(key=_rank, reverse=True)

    res = {
        "models": out[:limit],
        "offline": False,
        "sort": sort,
        "pipelines": list(pipelines),
        # How many carry a restricted-origin label, whether or not they were shown — so the
        # UI can say "12 from CN/AE" rather than the user having to count flags.
        "restricted_count": restricted_n,
        "hidden_by_filter": filtered_n,
        "blocked_hidden": 0 if include_blocked else restricted_n,
    }
    _CACHE[ck] = (time.time(), res)
    return res


def card(model_id: str) -> Dict[str, Any]:
    """Full detail for one model, including its README, for the model-card popup."""
    if not model_id:
        return {"error": "no model id"}
    ck = f"card::{model_id}"
    hit = _cached(ck)
    if hit is not None:
        return hit

    meta, reason = _get_json(
        f"{HF_API}/models/{model_id}?expand[]=downloads&expand[]=likes&expand[]=safetensors"
        f"&expand[]=tags&expand[]=pipeline_tag&expand[]=gated&expand[]=lastModified"
        f"&expand[]=createdAt&expand[]=cardData&expand[]=siblings&expand[]=config"
    )
    if meta is None:
        return {"error": reason or "Could not reach Hugging Face.", "offline": True}

    try:
        from services.model_presence import enumerate_cached
        installed = {m["model_id"] for m in enumerate_cached()}
    except Exception:
        installed = set()

    out = _enrich(meta, installed)
    out["fit"] = fit_for(out["size_gb"])
    out["card_data"] = meta.get("cardData") or {}
    out["files"] = [s.get("rfilename") for s in (meta.get("siblings") or [])][:60]

    # README, trimmed — the popup renders markdown, and some cards are enormous.
    readme = None
    try:
        import httpx
        with httpx.Client(timeout=20.0, follow_redirects=True) as c:
            r = c.get(f"https://huggingface.co/{model_id}/raw/main/README.md")
            if r.status_code == 200:
                readme = r.text
    except Exception:
        readme = None
    if readme:
        # Strip the YAML front-matter — it duplicates metadata we already show as chips.
        if readme.startswith("---"):
            end = readme.find("\n---", 3)
            if end != -1:
                readme = readme[end + 4:]
        out["readme"] = readme[:20000]
    out["url"] = f"https://huggingface.co/{model_id}"

    _CACHE[ck] = (time.time(), out)
    return out


def reset_cache() -> None:
    _CACHE.clear()
