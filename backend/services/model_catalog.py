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
import time
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

HF_API = "https://huggingface.co/api"
_TTL_S = 300.0
_CACHE: Dict[str, Tuple[float, Any]] = {}


# ── Origin policy ───────────────────────────────────────────────────────────────
# CLAUDE.md: never recommend or wire in an LLM/embedding model originating from China or the
# Middle East, or anything derived from them. That is a HARD rule, so this module does not
# merely label origin — it withholds blocked models from results by default. Showing them with
# a red flag would still be recommending them.
#
# Keyed by the org that appears in a model id or a `base_model:` tag. The value is
# (display vendor, ISO country, flag, allowed).
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
    "01ai":              ("01.AI (Yi)", "CN", "🇨🇳", False),
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


def origin_of(model_id: str, tags: Optional[List[str]] = None) -> Dict[str, Any]:
    """Vendor + country for a model. Prefers the `base_model:` tag over the repo owner.

    `mlx-community/gemma-4-e4b-it-4bit` is owned by a repackager, so the id alone says nothing
    about who built the weights — the base_model tag (`base_model:google/gemma-4-E4B-it`) does.
    """
    org = ""
    for t in (tags or []):
        if t.startswith("base_model:"):
            ref = t.split(":", 2)[-1]
            if "/" in ref:
                org = ref.split("/", 1)[0].lower()
                if org in VENDORS:
                    break
                org = ""
    if not org:
        owner = (model_id.split("/", 1)[0] or "").lower()
        if owner in VENDORS:
            org = owner
    if not org:
        low = model_id.lower()
        for needle, key in NAME_HINTS:
            if needle in low:
                org = key
                break
    vendor, country, flag, allowed = VENDORS.get(org, UNKNOWN_ORIGIN)
    return {"vendor": vendor, "country": country, "flag": flag,
            "allowed": allowed, "org": org or ""}


def size_gb_of(safetensors: Optional[dict]) -> Optional[float]:
    """Estimated weight size from HF's dtype→parameter-count breakdown.

    Accurate in practice because HF reports the REAL dtype composition, including the U32
    words a 4-bit MLX checkpoint packs its weights into — gemma-4-e4b computes to 4.79 GB and
    occupies 4.79 GB on disk. Still flagged `size_is_estimate` to the UI: it counts weights
    only, so it excludes KV and activations, and a repo with no safetensors metadata falls
    back to a cruder guess. Once downloaded, `model_sizing.exact_weight_gb` is authoritative.
    """
    if not safetensors:
        return None
    params = safetensors.get("parameters") or {}
    if not params:
        total = safetensors.get("total")
        return round(total * 2 / 1024 ** 3, 2) if total else None
    b = sum(_DTYPE_BYTES.get(dt, 2) * n for dt, n in params.items() if isinstance(n, (int, float)))
    return round(b / 1024 ** 3, 2) if b else None


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
    # Headroom for KV + activations. `model_sizing.fit()` computes KV exactly for a downloaded
    # model; pre-download all we have is the weight estimate, so leave a deliberate margin.
    needed = size_gb * 1.25
    if needed <= budget * 0.75:
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


def _get_json(url: str, timeout: float = 20.0):
    """GET JSON from the Hub. Returns None on ANY failure — offline is a normal state here."""
    try:
        import httpx
        # Same SSL/proxy handling the model downloads use, so a machine that needs the
        # bypass to download can also browse.
        try:
            from services.hf_transport import install_hf_transport
            install_hf_transport()
        except Exception:
            pass
        with httpx.Client(timeout=timeout, follow_redirects=True) as c:
            r = c.get(url, headers={"User-Agent": "LocalBook/2.3"})
            if r.status_code != 200:
                logger.info(f"[model-catalog] HF returned {r.status_code} for {url[:80]}")
                return None
            return r.json()
    except Exception as e:
        logger.info(f"[model-catalog] HF unreachable ({type(e).__name__}) — offline?")
        return None


def _enrich(raw: dict, installed: set) -> dict:
    mid = raw.get("id") or raw.get("modelId") or ""
    tags = raw.get("tags") or []
    caps = capabilities_of(raw.get("pipeline_tag"), tags)
    size = size_gb_of(raw.get("safetensors"))
    org = origin_of(mid, tags)
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
        "license": lic,
        "pipeline_tag": raw.get("pipeline_tag") or "",
        "size_gb": size,
        "size_is_estimate": True,
        "capabilities": caps,
        "roles": roles_for(caps, size),
        "origin": org,
        "installed": mid in installed,
        "tags": [t for t in tags if not t.startswith(("base_model:", "license:", "region:"))][:12],
    }


def search(
    query: str = "",
    sort: str = "downloads",
    limit: int = 40,
    role: str = "",
    include_blocked: bool = False,
    fits_only: bool = False,
) -> Dict[str, Any]:
    """Search the MLX catalog on Hugging Face.

    sort: downloads | likes | lastModified | createdAt
    role: main | fast | vision | embedding | image  (filters by eligibility)
    """
    sort = sort if sort in {"downloads", "likes", "lastModified", "createdAt"} else "downloads"
    # `filter=mlx` (the TAG), not `library=mlx`. The library form matches loosely and returns
    # plain sentence-transformers/BERT repos that this engine cannot load at all — verified:
    # its top results were all `mlx_tag=False`. The tag is what a genuine MLX conversion sets.
    #
    # Over-fetch, because policy + role + fit filtering all happen client-side of the API.
    fetch = min(max(limit * 4, 80), 300)
    parts = [
        f"{HF_API}/models?filter=mlx&sort={sort}&direction=-1&limit={fetch}",
        "expand[]=downloads", "expand[]=likes", "expand[]=safetensors",
        "expand[]=tags", "expand[]=pipeline_tag", "expand[]=gated",
        "expand[]=lastModified", "expand[]=createdAt",
    ]
    if query:
        from urllib.parse import quote
        parts.insert(1, f"search={quote(query)}")
    url = "&".join(parts)

    ck = f"search::{url}::{role}::{include_blocked}::{fits_only}::{limit}"
    hit = _cached(ck)
    if hit is not None:
        return hit

    raw = _get_json(url)
    if raw is None:
        return {"models": [], "offline": True,
                "reason": "Could not reach Hugging Face. The browser needs a connection; "
                          "models already downloaded still work offline."}

    try:
        from services.model_presence import enumerate_cached
        installed = {m["model_id"] for m in enumerate_cached()}
    except Exception:
        installed = set()

    out, blocked_n = [], 0
    for r in raw:
        card = _enrich(r, installed)
        if not card["model_id"]:
            continue
        if not card["origin"]["allowed"]:
            blocked_n += 1
            if not include_blocked:
                continue
        if not card["roles"]:
            # Eligible for no slot — an ASR model, a re-ranker, a depth estimator. Listing it
            # would offer a download the app has nowhere to put.
            continue
        if role and role not in card["roles"]:
            continue
        card["fit"] = fit_for(card["size_gb"])
        if fits_only and card["fit"]["verdict"] in ("over", "unknown"):
            continue
        out.append(card)

    res = {
        "models": out[:limit],
        "offline": False,
        "sort": sort,
        "blocked_hidden": blocked_n if not include_blocked else 0,
        "policy_note": ("Models from China and the Middle East are excluded by policy. "
                        "See the origin rule in CLAUDE.md."),
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

    meta = _get_json(
        f"{HF_API}/models/{model_id}?expand[]=downloads&expand[]=likes&expand[]=safetensors"
        f"&expand[]=tags&expand[]=pipeline_tag&expand[]=gated&expand[]=lastModified"
        f"&expand[]=createdAt&expand[]=cardData&expand[]=siblings"
    )
    if meta is None:
        return {"error": "Could not reach Hugging Face", "offline": True}

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
