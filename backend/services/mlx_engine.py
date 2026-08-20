"""MLX in-process engine adapter (Wave 9 — dual-engine Ollama|MLX).

Wired into the `llm_service` seam per role via `config.*_engine` (default "ollama").
- **Fast** (phi) → `mlx-lm` (Wave 9.1).
- **Main** (gemma) → `mlx-vlm` text path (Wave 9.2) — the SAME load that serves vision
  (9.3) and structured (9.2b), so gemma is loaded ONCE (the ~½-RAM win). We never load
  gemma on both mlx-lm and mlx-vlm.

Design invariants:
- **Ollama-shaped output.** generate() returns / stream_generate() yields the SAME dict
  shape the Ollama path emits (`response` / `message.content` / `prompt_eval_count` /
  `eval_count` / `eval_duration`), so callers + `_record_ollama_tokens` are unchanged.
- **Everything lazy.** mlx_lm / mlx_vlm imported inside methods — importing this module
  (and the whole backend) works with or without the MLX deps present.
- **Memory safety.** `mx.set_memory_limit` cap + a per-model lock (serialize the heavy
  model) + the single-engine-per-family invariant (loading MLX gemma evicts Ollama gemma).
"""
from __future__ import annotations

import asyncio
import contextlib
import functools
import logging
import os
import shutil
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, AsyncIterator, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


# ─── Role → engine resolver (grows per wave) ─────────────────────────────────────
def mlx_model_for_role(model: str) -> Optional[str]:
    """Identity, retained as a seam.

    This used to map an Ollama role key to its MLX twin, gated on that role's `*_engine`
    flag — `settings.ollama_model` held "gemma4:e4b" and `settings.mlx_main_model` held the
    checkpoint id. The v2.3.0 collapse put the checkpoint id in the role attribute itself, so
    the mapping has nothing left to do.

    Kept (rather than deleted across ~15 call sites) because it is the ONE place that would
    reacquire meaning if a role ever needs to resolve to something other than its configured
    id — a per-task override, a quantisation swap, an A/B. Returning None still means
    "nothing can serve this", which every caller already handles.
    """
    return model or None


def mlx_vision_model_if_enabled() -> Optional[str]:
    """The configured vision checkpoint, or None if none is set.

    The `if_enabled` in the name is historical: vision had its own engine flag, so this could
    return None for a configured model. It now only returns None when no vision model is
    configured at all.
    """
    try:
        from config import settings
    except Exception:
        return None
    return getattr(settings, "vision_model", None) or None


def _combine(system: Optional[str], prompt: str) -> str:
    return f"{system}\n\n{prompt}" if system else prompt


# ─── Vision-only sanitize shim (Wave 9.3) — proven in the doc-26 spike ───────────
# The mlx-community Gemma-4 E4B checkpoint bundles an audio tower whose conv-weight
# layout trips mlx-vlm's loader (a bug in an audio path LocalBook never uses). Nulling
# `audio_config` + dropping the audio weights loads a clean VISION+TEXT-only model.
_AUDIO_WEIGHT_PREFIXES = ("audio_tower", "embed_audio")


@contextlib.contextmanager
def offline_if_cached(model_id: str):
    """Load `model_id` WITHOUT contacting huggingface.co when it is already on disk.

    `mlx_lm.load` / `mlx_vlm.get_model_path` / `mlx_embeddings.load` all resolve through the
    Hub, which revalidates the revision over the network even for a fully cached model. Three
    consequences, all observed in the backend log (11 occurrences since 2026-08-19, most
    recently 11:51:18 while loading phi):

      · an "unauthenticated requests to the HF Hub" warning on every cold load;
      · **model loading depends on network reachability** — on a flaky link the load stalls
        behind an HTTP timeout before touching a single local byte;
      · a request leaves the machine, in an app whose premise is that nothing does.

    Only engaged when the weights are ALREADY present. An uncached model still resolves
    normally so a genuine first download can proceed — acquisition is the download manager's
    job, and offline mode would only turn that into a confusing LocalEntryNotFoundError.

    Both the env var and `constants.HF_HUB_OFFLINE` are set: the constant is captured at
    import, so the env var alone is too late to matter here. Verified — a runtime flip
    suppresses the network call AND is genuinely enforced (an uncached id raises
    LocalEntryNotFoundError rather than downloading).

    Restores prior state in `finally`. Every load already runs on the single `_exec` thread
    under `_load_lock`, so this process-wide toggle is serialised in practice.
    """
    try:
        from services.model_presence import is_present
        cached = is_present(model_id)
    except Exception:
        cached = False
    if not cached:
        yield
        return

    import huggingface_hub.constants as _hc
    prev_env = os.environ.get("HF_HUB_OFFLINE")
    prev_const = getattr(_hc, "HF_HUB_OFFLINE", False)
    os.environ["HF_HUB_OFFLINE"] = "1"
    try:
        _hc.HF_HUB_OFFLINE = True
    except Exception:
        pass
    try:
        yield
    finally:
        if prev_env is None:
            os.environ.pop("HF_HUB_OFFLINE", None)
        else:
            os.environ["HF_HUB_OFFLINE"] = prev_env
        try:
            _hc.HF_HUB_OFFLINE = prev_const
        except Exception:
            pass


def install_gemma_vision_only_shim() -> None:
    """Monkeypatch mlx_vlm's gemma4 Model.sanitize to drop audio-tower weights. Idempotent."""
    try:
        from mlx_vlm.models.gemma4 import gemma4 as _g4  # lazy
    except Exception as e:
        logger.debug(f"[mlx-engine] vision shim skipped ({e})")
        return
    if getattr(_g4.Model.sanitize, "_lb_vision_only", False):
        return
    _orig = _g4.Model.sanitize

    def _sanitize_no_audio(self, weights):
        w = _orig(self, weights)
        return {k: v for k, v in w.items() if not k.startswith(_AUDIO_WEIGHT_PREFIXES)}

    _sanitize_no_audio._lb_vision_only = True  # type: ignore[attr-defined]
    _g4.Model.sanitize = _sanitize_no_audio  # type: ignore[assignment]


def load_gemma_vision_only(model_id_or_path: str):
    """Load a Gemma-4 VLM checkpoint text+vision-only (audio tower skipped)."""
    from mlx_vlm.utils import get_model_path  # lazy
    import mlx_vlm  # lazy
    import json as _json

    install_gemma_vision_only_shim()
    path = Path(get_model_path(model_id_or_path))
    cfgp = path / "config.json"
    bak = path / "config.json.lb_vision_only.bak"
    if not bak.exists():
        shutil.copy2(cfgp, bak)
    try:
        cfg = _json.load(open(cfgp))
        if cfg.get("audio_config") is not None:
            cfg["audio_config"] = None
            _json.dump(cfg, open(cfgp, "w"))
        return mlx_vlm.load(str(path))
    finally:
        if bak.exists():
            os.replace(str(bak), str(cfgp))


# ─── Ollama-shaped result helper (the seam contract) ─────────────────────────────
def _ollama_shaped_generate_result(
    text: str, *, prompt_tokens: int = 0, eval_tokens: int = 0, eval_ns: int = 0,
    model: str = "",
) -> Dict[str, Any]:
    return {
        "response": text,
        "message": {"role": "assistant", "content": text},
        "model": model,
        "done": True,
        "prompt_eval_count": prompt_tokens,
        "eval_count": eval_tokens,
        "eval_duration": eval_ns,
    }


# ─── Grammar-constrained JSON (Wave 9.6, Path B) ─────────────────────────────────
# MLX has no native `format=json` grammar like Ollama, so JSON used to be only prompt-nudged
# → gemma-MLX returned blank/truncated/mis-typed JSON on demanding structured outputs (empty
# visuals, the chat 'list'.lower crash). mlx-vlm ships `build_json_schema_logits_processor`
# (backed by llguidance, a single Rust wheel) which FORCES tokens to a JSON schema — the same
# guarantee Ollama's JSON mode gives. Applied via the `logits_processors` hook both mlx-lm and
# mlx-vlm honor. Permissive default ({"type":"object"}) guarantees a valid JSON object even when
# the caller has no explicit schema.
_PERMISSIVE_JSON_SCHEMA: Dict[str, Any] = {"type": "object"}


def _tokenizer_candidates(tok_or_proc):
    """Yield tokenizer objects to try with llguidance, most-likely-accepted first. The accepted
    object differs by engine AND some wrappers report is_fast=True yet are rejected (mlx-lm's
    TokenizerWrapper), so we TRY each rather than guess: mlx-lm wrapper `._tokenizer`
    (TokenizersBackend) works; mlx-vlm `processor.tokenizer` (GemmaTokenizer) works."""
    proc_tok = getattr(tok_or_proc, "tokenizer", None)      # mlx-vlm processor → GemmaTokenizer
    seen = set()
    for c in (getattr(tok_or_proc, "_tokenizer", None),     # mlx-lm wrapper → TokenizersBackend (accepted)
              getattr(proc_tok, "_tokenizer", None),
              proc_tok,                                       # mlx-vlm processor.tokenizer (accepted)
              tok_or_proc):
        if c is not None and id(c) not in seen:
            seen.add(id(c))
            yield c


def _json_logits_processor(tok_or_proc, schema):
    """Build a fresh (stateful) JSON-schema logits processor, or None if llguidance can't accept any
    tokenizer candidate (→ caller keeps the prompt-nudge fallback). Never raises. from_tokenizer is
    the cheap gate — it raises for a bad tokenizer before the grammar compile — so trying candidates
    is inexpensive."""
    try:
        from mlx_vlm.structured import build_json_schema_logits_processor  # lazy
    except Exception as e:
        logger.warning(f"[mlx-engine] grammar JSON unavailable ({type(e).__name__}: {e}); prompt-nudge fallback")
        return None
    last = None
    for raw in _tokenizer_candidates(tok_or_proc):
        try:
            return build_json_schema_logits_processor(raw, schema or _PERMISSIVE_JSON_SCHEMA)
        except Exception as e:
            last = e
    logger.warning(f"[mlx-engine] grammar JSON unavailable (no accepted fast tokenizer: {last}); prompt-nudge fallback")
    return None


# ─── Decoding config (Wave 9.6) ──────────────────────────────────────────────────
# The MLX path used to run GREEDY (no sampler) with NO repetition penalty — the textbook
# recipe for repetition-loop degeneration on long output (the `<pad>어서어서…` garbage seen
# under memory pressure). Apply sane sampling (temp + top_p) + a repetition penalty, composed
# with any grammar (llguidance) processor. engine = "lm" (mlx-lm) | "vlm" (mlx-vlm).
_MLX_TOP_P = 0.95
_MLX_REP_PENALTY = 1.15
_MLX_REP_CONTEXT = 64


def _decode_kwargs(engine: str, temperature: Optional[float], grammar_lps) -> Dict[str, Any]:
    """{sampler, logits_processors} for stream_generate: proper sampling + repetition penalty,
    composed with any grammar processor (rep-penalty first, then the grammar mask). Never raises."""
    out: Dict[str, Any] = {}
    try:
        if engine == "vlm":
            from mlx_vlm.sample_utils import make_sampler, make_logits_processors
        else:
            from mlx_lm.sample_utils import make_sampler, make_logits_processors
        temp = max(float(temperature if temperature is not None else 0.3), 0.0)
        out["sampler"] = make_sampler(temp=temp, top_p=_MLX_TOP_P)
        procs = list(make_logits_processors(repetition_penalty=_MLX_REP_PENALTY,
                                            repetition_context_size=_MLX_REP_CONTEXT))
        if grammar_lps:
            procs = procs + list(grammar_lps)
        if procs:
            out["logits_processors"] = procs
    except Exception as e:  # fall back to just the grammar processor
        logger.debug(f"[mlx-engine] decode kwargs fallback ({type(e).__name__}: {e})")
        if grammar_lps:
            out["logits_processors"] = list(grammar_lps)
    return out


# ─── Degeneration guard (Wave 9.6) ───────────────────────────────────────────────
def _looks_degenerate(text: str) -> bool:
    """Cheap detector for the memory-pressure garbage output (repeated non-Latin tokens, <pad>,
    runaway word repetition). Used to trigger one clean-cache retry."""
    if not text or len(text) < 40:
        return False
    n = len(text)
    nonascii = sum(1 for c in text if ord(c) > 0x2000) / n
    if nonascii > 0.15:
        return True
    if text.count("<pad>") >= 3 or text.count("�") >= 3:
        return True
    words = text.split()
    if len(words) > 24:
        reps = sum(1 for i in range(len(words) - 1) if words[i] == words[i + 1])
        if reps / len(words) > 0.30:
            return True
    return False


def _streaming_degenerate(text: str) -> bool:
    """HARD degeneration signals that are SAFE to fire mid-stream — a stricter subset of
    _looks_degenerate. Deliberately EXCLUDES the non-ASCII-ratio heuristic: on a live chat
    stream that would false-positive on legitimate non-English answers (Chinese/Japanese/…)
    and truncate them. Only the unambiguous memory-corruption signatures remain:
      • repeated <pad> / replacement (�) tokens (the Metal-buffer-eviction signature), or
      • a run of ≥8 identical consecutive tokens (no natural language does this).
    Callers pass a RECENT window (degeneration is a tail phenomenon), so cost stays ~0."""
    if not text or len(text) < 60:
        return False
    if text.count("<pad>") >= 4 or text.count("�") >= 4:
        return True
    words = text.split()
    if len(words) >= 12:
        run = best = 1
        for i in range(1, len(words)):
            if words[i] == words[i - 1]:
                run += 1
                if run > best:
                    best = run
            else:
                run = 1
        if best >= 8:
            return True
    return False


def _first_json_value(text: str) -> Optional[str]:
    """Return the substring spanning the FIRST complete top-level JSON value (object or array),
    or None if none is found. Drops trailing markdown fences / prose that a non-grammar-clamped
    model appends after valid JSON — MLX `format=json` isn't hard-clamped like Ollama's grammar,
    so phi4 e.g. emits `{...}\\n\\n```\\n\\nThe confidence level…` (user report 2026-07-23). The
    streaming `json_stop` heuristic misses this when a token glues the closing brace to following
    chars; this is the post-hoc safety net. String-aware so braces inside quoted strings don't
    unbalance the scan. Returns None (not a partial) when no value closes, so callers keep the raw
    text for their own repair/logging."""
    if not text:
        return None
    obj_i, arr_i = text.find("{"), text.find("[")
    starts = sorted(s for s in ((obj_i, "{", "}"), (arr_i, "[", "]")) if s[0] != -1)
    for start, open_c, close_c in starts:
        depth = 0
        in_str = esc = False
        for i in range(start, len(text)):
            ch = text[i]
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
                continue
            if ch == '"':
                in_str = True
            elif ch == open_c:
                depth += 1
            elif ch == close_c:
                depth -= 1
                if depth == 0:
                    return text[start:i + 1]
    return None


def _json_complete(text: str) -> bool:
    """True if `text` is a complete parseable JSON value. Used to STOP grammar-constrained
    generation the instant the value closes — llguidance forces EOS after it, but MLX doesn't
    always halt on that token and otherwise emits hundreds of trailing <|endoftext|> pad tokens
    (pure wasted latency). Only attempts a parse when the text already ends in }/] (cheap)."""
    t = text.rstrip()
    if not t or t[-1] not in "}]":
        return False
    try:
        import json as _j
        _j.loads(t)
        return True
    except Exception:
        return False


# ─── Blocking generation helpers (run via thread) ────────────────────────────────
def _lm_generate_sync(model, tokenizer, prompt_str, *, max_tokens, temperature, stop,
                      logits_processors=None, json_stop=False):
    """mlx-lm non-streaming (accumulate). Returns (text, prompt_tokens, gen_tokens, gen_ns).
    gen_ns is decode-only time (first→last token) for tokens/sec parity with Ollama."""
    from mlx_lm import stream_generate  # lazy
    kwargs: Dict[str, Any] = {"max_tokens": max_tokens}
    kwargs.update(_decode_kwargs("lm", temperature, logits_processors))
    text = ""
    ptoks = gtoks = 0
    t_first = None
    for resp in stream_generate(model, tokenizer, prompt_str, **kwargs):
        if t_first is None:
            t_first = time.perf_counter()
        text += resp.text
        ptoks = getattr(resp, "prompt_tokens", ptoks) or ptoks
        gtoks = getattr(resp, "generation_tokens", gtoks) or gtoks
        if stop:
            cut = min((text.find(s) for s in stop if s and s in text), default=-1)
            if cut != -1:
                return text[:cut], ptoks, gtoks, _since(t_first)
        if json_stop and resp.text and resp.text.rstrip()[-1:] in "}]" and _json_complete(text):
            return text, ptoks, gtoks, _since(t_first)
    return text, ptoks, gtoks, _since(t_first)


def _vlm_generate_sync(model, processor, config, prompt_str, *, max_tokens, stop,
                       temperature=0.3, logits_processors=None, json_stop=False):
    """mlx-vlm text-only non-streaming (gemma). Returns (text, prompt_tokens, gen_tokens, gen_ns)."""
    from mlx_vlm import stream_generate  # lazy
    from mlx_vlm.prompt_utils import apply_chat_template
    formatted = apply_chat_template(processor, config, prompt_str, num_images=0)
    vkwargs: Dict[str, Any] = {"image": [], "max_tokens": max_tokens}
    vkwargs.update(_decode_kwargs("vlm", temperature, logits_processors))
    text = ""
    ptoks = gtoks = 0
    t_first = None
    for resp in stream_generate(model, processor, formatted, **vkwargs):
        if t_first is None:
            t_first = time.perf_counter()
        text += resp.text
        ptoks = getattr(resp, "prompt_tokens", ptoks) or ptoks
        gtoks = getattr(resp, "generation_tokens", gtoks) or gtoks
        if stop:
            cut = min((text.find(s) for s in stop if s and s in text), default=-1)
            if cut != -1:
                return text[:cut], ptoks, gtoks, _since(t_first)
        if json_stop and resp.text and resp.text.rstrip()[-1:] in "}]" and _json_complete(text):
            return text, ptoks, gtoks, _since(t_first)
    return text, ptoks, gtoks, _since(t_first)


def _since(t_first: Optional[float]) -> int:
    """Nanoseconds since the first-token timestamp (0 if no tokens produced)."""
    return int((time.perf_counter() - t_first) * 1e9) if t_first else 0


def _resolve_image(image_path_or_b64: str):
    """Callers pass either a filesystem path or a base64-encoded image (the Ollama vision
    convention). Return a path (as-is) or a decoded PIL Image for mlx-vlm."""
    s = image_path_or_b64
    try:
        if os.path.exists(s):
            return s
    except Exception:
        pass
    try:
        import base64 as _b64, io as _io
        from PIL import Image  # lazy
        return Image.open(_io.BytesIO(_b64.b64decode(s))).convert("RGB")
    except Exception:
        return s  # let mlx-vlm try to interpret it (URL/path)


def _vlm_vision_sync(model, processor, config, prompt_str, image, *, max_tokens,
                     temperature=0.3, logits_processors=None):
    """mlx-vlm vision (gemma, one image). Returns (text, prompt_tokens, gen_tokens)."""
    from mlx_vlm import generate as vl_generate  # lazy
    from mlx_vlm.prompt_utils import apply_chat_template
    formatted = apply_chat_template(processor, config, prompt_str, num_images=1)
    gkw = _decode_kwargs("vlm", temperature, logits_processors)  # sampling + rep penalty (+ grammar)
    out = vl_generate(model, processor, formatted, [image], max_tokens=max_tokens, verbose=False, **gkw)
    if isinstance(out, str):
        return out, 0, 0
    text = getattr(out, "text", str(out))
    return text, getattr(out, "prompt_tokens", 0) or 0, getattr(out, "generation_tokens", 0) or 0


def _embed_on_thread(engine, texts, model_id, batch_size, max_length):
    """Load (cache) the MLX embedding model and encode `texts` → list[list[float]].
    Runs ONLY on the single MLX executor thread (both async embed() and sync
    embed_sync() route here), so the resident-cache check/set needs no lock. Raises
    on any error → the caller's Ollama fallback.

    Pools with the [CLS] token + L2-normalize — NOT the lib's default `text_embeds`,
    which is MEAN-pooled. arctic-embed-l-v2.0 (XLM-RoBERTa) is trained with CLS
    pooling, and CLS matches the Ollama `arctic-embed2` vectors EXACTLY (verified
    cosine 1.0000 on identical text), which is what makes the MLX swap a
    same-vector-space, ZERO-re-index change. Mean pooling drifts ~0.84 vs the stored
    Ollama vectors and would silently degrade retrieval. If a future embedding model
    is mean-pooled, gate the pooling on model_id — do not blindly switch to
    text_embeds."""
    import mlx.core as mx
    pair = engine._embed_resident.get(model_id)
    if pair is None:
        engine._ensure_memory_limit()
        from mlx_embeddings import load as _eload
        logger.info(f"[mlx-engine] loading embedding model {model_id} …")
        _t0 = time.perf_counter()
        with offline_if_cached(model_id):
            pair = _eload(model_id)
        engine._embed_resident[model_id] = pair
        engine._last_used[model_id] = time.monotonic()
        logger.info(f"[mlx-engine] loaded embedding model {model_id} in {time.perf_counter() - _t0:.1f}s")
    model, tokenizer = pair
    out: List[List[float]] = []
    for i in range(0, len(texts), batch_size):
        chunk = texts[i:i + batch_size]
        inputs = tokenizer.batch_encode_plus(
            chunk, return_tensors="mlx", padding=True, truncation=True, max_length=max_length)
        res = model(inputs["input_ids"], attention_mask=inputs.get("attention_mask"))
        lhs = getattr(res, "last_hidden_state", None)
        if lhs is None:
            lhs = res[0]
        embs = lhs[:, 0, :]  # CLS pooling (arctic / XLM-RoBERTa) — matches Ollama exactly
        embs = embs / mx.linalg.norm(embs, axis=-1, keepdims=True)
        mx.eval(embs)
        out.extend([[float(v) for v in row] for row in embs.tolist()])
    return out


# ─── The engine ──────────────────────────────────────────────────────────────────
# The embedding model's REAL context, not a guess. arctic-embed-l-v2.0 supports 8194 while the
# old hardcoded default truncated at 2048 — an asymmetry with the Ollama path, which applies no
# client cap. Currently LATENT: 0 of 5,366 real chunks measured on 2026-08-19 exceed 2048
# (max ~1512), so this bites long queries or a larger chunk_size, not today's index.
_EMBED_MAXLEN_CACHE: Dict[str, int] = {}


def _embed_max_length(model_id: str) -> int:
    env = os.environ.get("LOCALBOOK_MLX_EMBED_MAX_LENGTH")
    if env:
        try:
            return int(env)
        except ValueError:
            pass
    hit = _EMBED_MAXLEN_CACHE.get(model_id)
    if hit:
        return hit
    val = 2048
    try:
        import json as _json
        from huggingface_hub import try_to_load_from_cache
        p = try_to_load_from_cache(model_id, "config.json")
        if isinstance(p, str):
            cfg = _json.load(open(p))
            mp = cfg.get("max_position_embeddings")
            if isinstance(mp, int) and mp > 0:
                val = mp
    except Exception:
        pass
    _EMBED_MAXLEN_CACHE[model_id] = val
    return val


class MLXEngine:
    def __init__(self) -> None:
        self._resident: Dict[str, Any] = {}              # model_id -> (model, tokenizer/processor)
        self._embed_resident: Dict[str, Any] = {}        # embedding model_id -> (model, tokenizer)
        self._last_used: Dict[str, float] = {}           # model_id -> monotonic ts (LRU order)
        self._vlm_config: Dict[str, Any] = {}            # model_id -> config (vlm only)
        self._kind: Dict[str, str] = {}                  # model_id -> "lm" | "vlm"
        self._model_locks: Dict[str, asyncio.Lock] = {}  # per-model serialization
        self._load_lock = asyncio.Lock()
        self._mem_limit_set = False
        # ALL MLX work (load + generate + stream producer) runs on this ONE thread.
        # mlx-lm uses thread-local GPU streams — if a model loads on pool-thread A and a
        # later generate runs on pool-thread B, MLX raises "There is no Stream(gpu, N) in
        # current thread" and the call fails (→ Ollama fallback, and the streaming eval
        # test scores 0). Pinning to a single worker keeps the thread-local stream
        # consistent. Serialization is fine: one GPU + the memory-safety invariant already
        # want one model computing at a time.
        self._exec = ThreadPoolExecutor(max_workers=1, thread_name_prefix="mlx-engine")

    async def _run(self, fn, *args, **kwargs):
        """Run a blocking MLX callable on the single dedicated MLX thread."""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(self._exec, functools.partial(fn, *args, **kwargs))

    @staticmethod
    def available() -> bool:
        try:
            import importlib.util
            return all(importlib.util.find_spec(m) is not None
                       for m in ("mlx", "mlx_lm", "mlx_vlm"))
        except Exception:
            return False

    # -- scheduler internals -----------------------------------------------------
    def _ensure_memory_limit(self) -> None:
        if self._mem_limit_set:
            return
        self._mem_limit_set = True
        try:
            import mlx.core as mx
            # HARDWARE-DERIVED, not a flat constant. The old default was 12 GB on every
            # machine — on this 16 GB M4 that is ABOVE Apple's own recommended working set
            # (11.84 GiB), so the "limit" could never bind before the system was already past
            # the ceiling: an inert guardrail. On a 64 GB machine the same constant needlessly
            # capped MLX at 12. Derive from the GPU's addressable working set instead; the env
            # var remains an explicit override.
            _env = os.environ.get("LOCALBOOK_MLX_MEMORY_LIMIT_GB")
            if _env:
                limit_gb = float(_env)
                _src = "env override"
            else:
                from services.model_sizing import working_set_gb
                _ws = working_set_gb()
                # 90 % of the working set: mlx-lm warns above this, and the ecosystem's
                # posture is to refuse rather than warn (mlx-lm#883 — wired memory blocks
                # Jetsam, so exhaustion panics the driver instead of killing the process).
                limit_gb = round(_ws * 0.90, 2) if _ws > 0 else 12.0
                _src = f"90% of {_ws:.2f} GiB working set" if _ws > 0 else "fallback"
            mx.set_memory_limit(int(limit_gb * 1024 ** 3))
            logger.info(f"[mlx-engine] memory limit {limit_gb} GB ({_src})")
        except Exception as e:
            logger.debug(f"[mlx-engine] could not set memory limit: {e}")

    def _model_kind(self, model_id: str) -> str:
        """'vlm' (mlx-vlm, gemma) or 'lm' (mlx-lm, phi). Cached. Cheap config.json fetch
        with a name heuristic fallback (never downloads the whole model)."""
        if model_id in self._kind:
            return self._kind[model_id]
        kind = "vlm" if "gemma" in model_id.lower() else "lm"
        try:
            # `hf_hub_download` was the original here and it REVALIDATES against
            # huggingface.co even for a cached file — this call, not the load itself, is what
            # emitted the "unauthenticated requests to the HF Hub" warning on every cold
            # start. It also runs BEFORE the load lock, so it is not covered by
            # `offline_if_cached`. `load_config` reads the cached snapshot off disk.
            from services.model_sizing import load_config
            cfg = load_config(model_id) or {}
            if cfg:
                kind = "vlm" if cfg.get("vision_config") is not None else "lm"
        except Exception:
            pass
        self._kind[model_id] = kind
        return kind

    # -- resident budget (Stage 3.2) ---------------------------------------------
    def _resident_cost_gb(self) -> float:
        """What the currently-resident set costs — weights only, exactly.

        Deliberately NOT an estimate: `model_sizing.exact_weight_gb` reads
        `metadata.total_size` from the checkpoint index. The old estimator was wrong by −17 %
        to +89 %, and reported 0.00 GB for both arctic builds, which `ram_fit` read as
        "fits" — a guardrail that was disabled rather than merely inaccurate.
        """
        try:
            from services.model_sizing import exact_weight_gb
        except Exception:
            return 0.0
        total = 0.0
        for mid in list(self._resident) + list(self._embed_resident):
            w = exact_weight_gb(mid)
            if w:
                total += w
        return round(total, 3)

    def _budget_gb(self) -> float:
        """The ceiling for resident weights + the incoming model's KV.

        Derived from Apple's own per-device `max_recommended_working_set_size`, not a constant
        and not a fraction of total RAM — on this 16 GB M4 the working set is 11.84 GiB, so
        "60 % of RAM" and "75 % of the working set" are different numbers and only the latter
        tracks what the GPU can address on any given machine.
        """
        try:
            from services.model_sizing import budget_gb
            return budget_gb()
        except Exception:
            return 0.0

    async def _make_room_for(self, model_id: str) -> None:
        """Evict LRU models until the incoming one fits the budget. Never raises.

        Counts the incoming model's KV at its DEPLOYED context, not its native one: phi
        declares a 262144 window it cannot use and costs ~8× gemma per token of KV (32 kv-head
        layers vs 7), so judging by weights alone under-counts the model that actually hurts.
        """
        try:
            from services.model_sizing import exact_weight_gb, kv_cache_gb, load_config
            budget = self._budget_gb()
            if budget <= 0:
                return
            incoming_w = exact_weight_gb(model_id) or 0.0
            if incoming_w <= 0:
                return          # unknown size — do not evict on a guess
            cfg = load_config(model_id)
            ctx = int(os.environ.get("LOCALBOOK_MLX_BUDGET_CTX", "16384"))
            incoming_kv = (kv_cache_gb(cfg, ctx) if cfg else None) or 0.0
            need = incoming_w * 1.2 + incoming_kv        # ×1.2 for activations/scratch

            resident = self._resident_cost_gb()
            if resident + need <= budget:
                return

            # LRU first — the model used longest ago is the cheapest to lose.
            order = sorted(
                (m for m in list(self._resident) + list(self._embed_resident) if m != model_id),
                key=lambda m: self._last_used.get(m, 0.0),
            )
            logger.info(f"[mlx-engine] budget: resident {resident} GB + incoming {round(need,2)} GB "
                        f"> {budget} GB — evicting LRU to make room")
            for victim in order:
                if await self.unload(victim, wait=1.0):
                    self._last_used.pop(victim, None)
                    resident = self._resident_cost_gb()
                    if resident + need <= budget:
                        return
            if resident + need > budget:
                # Proceed anyway rather than refuse the user's request — but say so, because
                # this is the condition that precedes swap-death on a tight machine.
                logger.warning(
                    f"[mlx-engine] budget EXCEEDED after eviction: resident {resident} GB + "
                    f"incoming {round(need,2)} GB > {budget} GB. Loading anyway; expect "
                    f"memory pressure.")
                try:
                    from services.quality_signals import record_signal
                    record_signal("degraded", "mlx_engine",
                                  f"resident budget exceeded loading {model_id} "
                                  f"({resident}+{round(need,2)} > {budget} GB)",
                                  severity="warn", key="mlx_budget_exceeded")
                except Exception:
                    pass
        except Exception as e:
            logger.debug(f"[mlx-engine] budget check skipped: {e}")

    async def _load(self, model_id: str) -> Tuple[Any, Any]:
        """Load (cache) an MLX model — mlx-vlm for gemma, mlx-lm for phi. Loads run
        one-at-a-time; gemma load evicts the Ollama twin first."""
        if model_id in self._resident:
            return self._resident[model_id]
        kind = self._model_kind(model_id)
        async with self._load_lock:
            if model_id in self._resident:
                return self._resident[model_id]
            self._ensure_memory_limit()
            await self._make_room_for(model_id)
            logger.info(f"[mlx-engine] loading {model_id} ({kind}) …")
            t0 = time.perf_counter()

            def _load():
                with offline_if_cached(model_id):
                    if kind == "vlm":
                        from mlx_vlm.utils import get_model_path, load_config
                        pair = load_gemma_vision_only(model_id)
                        self._vlm_config[model_id] = load_config(str(get_model_path(model_id)))
                        return pair
                    from mlx_lm import load
                    return load(model_id)

            pair = await self._run(_load)
            self._resident[model_id] = pair
            self._last_used[model_id] = time.monotonic()
            logger.info(f"[mlx-engine] loaded {model_id} in {time.perf_counter() - t0:.1f}s")
            return pair

    # -- unload / eviction (Stage 3.1) -------------------------------------------
    def resident(self) -> Dict[str, Any]:
        """What is currently held in memory, and what MLX says it costs.

        The MLX twin of Ollama's `/api/ps`. Without it neither the resident budget nor any
        eviction sweep is falsifiable — you cannot prove a free happened.
        """
        out: Dict[str, Any] = {
            "text": sorted(self._resident.keys()),
            "embed": sorted(self._embed_resident.keys()),
        }
        try:
            import mlx.core as mx
            out["active_gb"] = round(mx.get_active_memory() / 1024 ** 3, 3)
            out["peak_gb"] = round(mx.get_peak_memory() / 1024 ** 3, 3)
            try:
                out["cache_gb"] = round(mx.get_cache_memory() / 1024 ** 3, 3)
            except Exception:
                pass
        except Exception:
            pass
        return out

    async def unload(self, model_id: str, *, wait: float = 2.0) -> bool:
        """Drop one model's weights and reclaim the memory. Returns True if it was freed.

        MEASURED JUSTIFICATION (2026-08-19): an evaluation run ended holding **7.64 GB** of MLX
        weights — exactly the sum of gemma 4.793 + phi 2.010 + arctic 1.058 GiB — because
        nothing ever cleared `_resident`. KV and activations churn normally; the WEIGHTS never
        came back until the process exited. On a 16 GB box that is most of the working set.

        Safety, per the ecosystem prior art:
        · NEVER free weights out from under a live generation — take that model's lock, and
          SKIP (return False) rather than block forever if it is busy. A skipped eviction is a
          missed optimisation; a freed-mid-stream model is a crash.
        · Free on the MLX thread (`_exec`), the same thread that allocated.
        · `gc.collect()` BEFORE `clear_cache()` — the buffers are only reclaimable once the
          last Python reference is gone, and dropping the dict entry is not enough on its own.
        · Short-circuit when nothing is loaded: touching Metal to free nothing still costs.
        """
        if model_id not in self._resident and model_id not in self._embed_resident:
            return False

        lock = self._model_locks.setdefault(model_id, asyncio.Lock())
        try:
            await asyncio.wait_for(lock.acquire(), timeout=wait)
        except asyncio.TimeoutError:
            logger.info(f"[mlx-engine] unload({model_id}) SKIPPED — model busy "
                        f"(a live generation outranks reclaiming memory)")
            return False
        try:
            before = self._active_gb()
            self._resident.pop(model_id, None)
            self._embed_resident.pop(model_id, None)
            self._vlm_config.pop(model_id, None)

            def _free() -> None:
                import gc
                gc.collect()          # must precede clear_cache — see docstring
                try:
                    import mlx.core as mx
                    mx.clear_cache()
                except Exception:
                    pass

            await self._run(_free)
            after = self._active_gb()
            freed = None if (before is None or after is None) else round(before - after, 3)
            logger.info(f"[mlx-engine] unloaded {model_id} — active {before} → {after} GB "
                        f"(freed {freed})")
            return True
        finally:
            lock.release()

    async def unload_all(self, *, keep: Optional[List[str]] = None, wait: float = 2.0) -> List[str]:
        """Unload every resident model except `keep`. Returns what was actually freed."""
        keep_set = set(keep or [])
        targets = [m for m in list(self._resident) + list(self._embed_resident)
                   if m not in keep_set]
        if not targets:
            return []
        freed: List[str] = []
        for m in targets:
            if await self.unload(m, wait=wait):
                freed.append(m)
        return freed

    def _active_gb(self) -> Optional[float]:
        try:
            import mlx.core as mx
            return round(mx.get_active_memory() / 1024 ** 3, 3)
        except Exception:
            return None

    # -- text / structured (fast 9.1 · main 9.2 · structured 9.2b) ---------------
    async def generate(
        self, prompt: str, *, model: str, system: Optional[str] = None,
        temperature: float = 0.3, num_predict: int = 500, num_ctx: Optional[int] = None,
        format: Optional[str] = None, stop: Optional[List[str]] = None,
        images: Optional[List[str]] = None, **kwargs: Any,
    ) -> Dict[str, Any]:
        """Non-streaming text generate → Ollama-shaped dict. Routes gemma→mlx-vlm, phi→mlx-lm."""
        kind = self._model_kind(model)
        pair = await self._load(model)
        lock = self._model_locks.setdefault(model, asyncio.Lock())
        self._last_used[model] = time.monotonic()   # LRU: real usage, not load order
        # Grammar-constrained JSON (Path B): force schema-compliant JSON via llguidance — but ONLY
        # when the caller passes an explicit `json_schema`. A permissive `{"type":"object"}` grammar
        # is a trap: the model can satisfy it with an empty `{}` and skip every field, which broke the
        # schema-less idiom picker (it got `{}` → failed → fell to the template path). So schema-less
        # `format=json` keeps the prompt-nudge (its long-proven behaviour); grammar is opt-in per schema.
        lps = None
        if format == "json":
            schema = kwargs.get("json_schema")
            if schema:
                lp = _json_logits_processor(pair[1], schema)
                if lp is not None:
                    lps = [lp]
            if lps is None:
                prompt = f"{prompt}\n\nOutput ONLY valid JSON — no prose, no markdown code fences."
        t0 = time.perf_counter()

        _json_stop = (format == "json")  # halt at the first complete JSON (no wasted trailing EOS pad)

        async def _gen(_temp, _lps):
            if kind == "vlm":
                mobj, processor = pair
                cfg = self._vlm_config.get(model)
                return await self._run(
                    _vlm_generate_sync, mobj, processor, cfg, _combine(system, prompt),
                    max_tokens=num_predict, stop=stop, temperature=_temp, logits_processors=_lps,
                    json_stop=_json_stop)
            mobj, tok = pair
            messages = ([{"role": "system", "content": system}] if system else []) + \
                       [{"role": "user", "content": prompt}]
            try:
                prompt_str = tok.apply_chat_template(messages, add_generation_prompt=True, tokenize=False)
            except Exception:
                prompt_str = _combine(system, prompt)
            return await self._run(
                _lm_generate_sync, mobj, tok, prompt_str,
                max_tokens=num_predict, temperature=_temp, stop=stop, logits_processors=_lps,
                json_stop=_json_stop)

        async with lock:
            text, ptoks, gtoks, gen_ns = await _gen(temperature, lps)
            # Degeneration guard (item 4): the memory-pressure garbage. One clean-cache retry with a
            # little more temperature usually recovers. Grammar processors are stateful, so rebuild a
            # fresh one for the retry.
            if _looks_degenerate(text):
                logger.warning(f"[mlx-engine] degenerate output for {model} ({len(text)} chars); "
                               f"clearing cache + retrying once")
                try:
                    from services.quality_signals import record_signal
                    record_signal(
                        "degraded", "mlx_engine", "degeneration guard tripped",
                        severity="warn", key="degeneration",
                    )
                except Exception:
                    pass
                try:
                    import mlx.core as mx
                    (getattr(mx, "clear_cache", None) or getattr(getattr(mx, "metal", None), "clear_cache", lambda: None))()
                except Exception:
                    pass
                _lps2 = None
                if format == "json" and kwargs.get("json_schema"):
                    _lp = _json_logits_processor(pair[1], kwargs.get("json_schema"))
                    _lps2 = [_lp] if _lp is not None else None
                text, ptoks, gtoks, gen_ns = await _gen(min(1.0, (temperature or 0.3) + 0.3), _lps2)
        # Safety net for format=json: strip any trailing markdown/prose the model appended after
        # the JSON value (the streaming json_stop can miss it on glued tokens). No-op on clean
        # (grammar-wired) output; leaves non-JSON output untouched for downstream repair.
        if format == "json":
            _trimmed = _first_json_value(text)
            if _trimmed is not None and _trimmed != text.strip():
                text = _trimmed
        # Prefer decode-only time (Ollama parity for tokens/sec); fall back to total wall-clock.
        eval_ns = gen_ns or int((time.perf_counter() - t0) * 1e9)
        return _ollama_shaped_generate_result(
            text, prompt_tokens=ptoks, eval_tokens=gtoks, eval_ns=eval_ns, model=model)

    async def stream_generate(
        self, prompt: str, *, model: str, system: Optional[str] = None,
        temperature: float = 0.3, num_predict: int = 500, num_ctx: Optional[int] = None,
        stop: Optional[List[str]] = None, **kwargs: Any,
    ) -> AsyncIterator[Dict[str, Any]]:
        """Streaming text generate → yields Ollama-shaped chunks. Wave 9.2 (main/gemma via
        mlx-vlm; fast/phi via mlx-lm). Bridges the blocking MLX generator to async via an
        asyncio.Queue fed with call_soon_threadsafe (no per-token thread round-trip)."""
        kind = self._model_kind(model)
        pair = await self._load(model)
        lock = self._model_locks.setdefault(model, asyncio.Lock())
        self._last_used[model] = time.monotonic()   # LRU: real usage, not load order
        loop = asyncio.get_running_loop()
        q: asyncio.Queue = asyncio.Queue()
        _SENTINEL = object()

        def _producer():
            try:
                if kind == "vlm":
                    mobj, processor = pair
                    cfg = self._vlm_config.get(model)
                    from mlx_vlm import stream_generate as _sg
                    from mlx_vlm.prompt_utils import apply_chat_template
                    formatted = apply_chat_template(processor, cfg, _combine(system, prompt), num_images=0)
                    # Was GREEDY (no sampler / no repetition penalty) — the loop-trigger that garbled
                    # long chat answers. Apply the same decoding config as the non-streaming path.
                    gen = _sg(mobj, processor, formatted, image=[], max_tokens=num_predict,
                              **_decode_kwargs("vlm", temperature, None))
                else:
                    mobj, tok = pair
                    from mlx_lm import stream_generate as _sg
                    messages = ([{"role": "system", "content": system}] if system else []) + \
                               [{"role": "user", "content": prompt}]
                    try:
                        prompt_str = tok.apply_chat_template(messages, add_generation_prompt=True, tokenize=False)
                    except Exception:
                        prompt_str = _combine(system, prompt)
                    gen = _sg(mobj, tok, prompt_str, max_tokens=num_predict,
                              **_decode_kwargs("lm", temperature, None))
                acc = ""
                ptoks = gtoks = 0
                since_check = 0
                degenerate = False      # streaming guard: set if we abort on garbage
                t_first = None          # decode start = first token (parity with Ollama eval_duration)
                for resp in gen:
                    if t_first is None:
                        t_first = time.perf_counter()
                    tok_text = resp.text
                    acc += tok_text
                    ptoks = getattr(resp, "prompt_tokens", ptoks) or ptoks
                    gtoks = getattr(resp, "generation_tokens", gtoks) or gtoks
                    if stop:
                        cut = min((acc.find(s) for s in stop if s and s in acc), default=-1)
                        if cut != -1:
                            # emit only the part before the stop marker in this token
                            keep = tok_text[: max(0, len(tok_text) - (len(acc) - cut))]
                            if keep:
                                loop.call_soon_threadsafe(q.put_nowait, {"response": keep, "done": False})
                            break
                    loop.call_soon_threadsafe(q.put_nowait, {"response": tok_text, "done": False})
                    # Streaming degeneration guard: if the answer has clearly gone to garbage
                    # (memory-pressure corruption), ABORT rather than stream hundreds of junk
                    # tokens. Checked every 32 tokens on a recent window (cost ~0). Proper
                    # decoding already makes this rare; this is the safety net for the tail.
                    since_check += 1
                    if since_check >= 32:
                        since_check = 0
                        if _streaming_degenerate(acc[-600:]):
                            degenerate = True
                            logger.warning(
                                "[mlx-engine] streaming ABORTED — degeneration detected "
                                f"(model={model}, ~{gtoks} tokens in). Likely memory pressure; "
                                "output truncated at onset instead of emitting garbage.")
                            break
                # eval_duration = generation-only ns (first→last token), matching Ollama's field so
                # tokens/sec computes identically across engines (was hardcoded 0 → blank MLX stats).
                gen_ns = int((time.perf_counter() - t_first) * 1e9) if t_first else 0
                loop.call_soon_threadsafe(q.put_nowait, {
                    "response": "", "done": True, "degenerate": degenerate,
                    "prompt_eval_count": ptoks, "eval_count": gtoks, "eval_duration": gen_ns})
            except Exception as e:
                loop.call_soon_threadsafe(q.put_nowait, {"__error__": f"{type(e).__name__}: {e}"})
            finally:
                loop.call_soon_threadsafe(q.put_nowait, _SENTINEL)

        async with lock:
            fut = loop.run_in_executor(self._exec, _producer)
            try:
                while True:
                    item = await q.get()
                    if item is _SENTINEL:
                        break
                    if "__error__" in item:
                        raise RuntimeError(item["__error__"])
                    yield item
            finally:
                await fut

    # -- vision (Wave 9.3) -------------------------------------------------------
    async def vision_describe(
        self, image_path_or_b64: str, prompt: str, *, model: str,
        system: Optional[str] = None, num_predict: int = 400,
        format: Optional[str] = None, json_schema: Optional[Dict[str, Any]] = None,
        temperature: float = 0.3, **kwargs: Any,
    ) -> Dict[str, Any]:
        """Image → text/JSON via mlx-vlm (gemma vision). Same one gemma load as text/structured —
        keeps the visual CRITIC on MLX so a second (Ollama) gemma never loads (the 2×-gemma memory
        doubling that caused degeneration). Grammar-constrains JSON when a schema is given. → Ollama-shaped."""
        pair = await self._load(model)          # vlm (gemma)
        mobj, processor = pair
        cfg = self._vlm_config.get(model)
        img = _resolve_image(image_path_or_b64)
        lock = self._model_locks.setdefault(model, asyncio.Lock())
        self._last_used[model] = time.monotonic()   # LRU: real usage, not load order
        lps = None
        if format == "json":
            if json_schema:
                _lp = _json_logits_processor(processor, json_schema)
                lps = [_lp] if _lp is not None else None
            if lps is None:
                prompt = f"{prompt}\n\nOutput ONLY valid JSON — no prose, no markdown code fences."
        t0 = time.perf_counter()
        async with lock:
            text, ptoks, gtoks = await self._run(
                _vlm_vision_sync, mobj, processor, cfg, _combine(system, prompt), img,
                max_tokens=num_predict, temperature=temperature, logits_processors=lps)
        dur_ns = int((time.perf_counter() - t0) * 1e9)
        return _ollama_shaped_generate_result(
            text, prompt_tokens=ptoks, eval_tokens=gtoks, eval_ns=dur_ns, model=model)

    # -- embeddings (Wave 9.6 — arctic-embed-l-v2.0 native on MLX) ----------------
    # Same model + same 1024 dim as Ollama arctic-embed2 → NO re-index (same vector
    # space). Uses the mlx-embeddings lib (XLM-RoBERTa is first-class), a SEPARATE
    # model cache from the LLM roster, and the same single MLX thread for GPU-stream
    # consistency. Both entrypoints RAISE on any failure so callers fall back to
    # Ollama embeddings (retrieval never breaks). Prefix discipline (arctic is
    # asymmetric) is the CALLER's job — embed() embeds text exactly as given.

    async def embed(self, texts: List[str], *, model: str,
                    batch_size: int = 32, max_length: Optional[int] = None,
                    **kwargs: Any) -> List[List[float]]:
        if not texts:
            return []
        ml = max_length or _embed_max_length(model)
        return await self._run(_embed_on_thread, self, list(texts), model, batch_size, ml)

    def embed_sync(self, texts: List[str], *, model: str,
                   batch_size: int = 32, max_length: Optional[int] = None) -> List[List[float]]:
        """Blocking variant for the raw-`requests` sync embed helpers in rag_embeddings.
        Submits to the single MLX thread and waits — same blocking contract those callers
        already have. Must NOT be called from the MLX thread itself."""
        if not texts:
            return []
        ml = max_length or _embed_max_length(model)
        return self._exec.submit(_embed_on_thread, self, list(texts), model, batch_size, ml).result()


mlx_engine = MLXEngine()
