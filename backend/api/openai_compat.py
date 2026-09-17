"""An OpenAI-compatible endpoint, so local tools can borrow LocalBook's engine.

Mounted at `/v1`. This exists for one concrete reason: companion tools that
already speak the OpenAI API should not have to run a second inference server.

The motivating case is Meeting Notes (github.com/kvango/Meeting-Summarizer),
which takes `--base-url` and `--llm` and otherwise installs llama.cpp and loads
its own copy of gemma. On a 16 GB Mac that means two gemmas resident while a
meeting is summarised — and v2.3.0 removed exactly that class of second
inference server on purpose. Pointing it here instead means one model, one
download, one set of weights, and the summaries come out in whatever model the
user actually chose in LLM Studio.

**Scope is deliberately narrow.** This is not an OpenAI emulator. It supports
what a local summariser sends: `chat/completions` with a system + user message,
a temperature, an optional max_tokens, and optional streaming. Anything else
(tools, functions, images, logprobs, n>1) is not implemented and says so rather
than silently ignoring the request and returning something that looks fine.

**Auth is a separate, stable key.** The app token rotates every launch, so it
cannot be written into a companion's config file once. `/v1` instead takes a
long-lived companion key that LocalBook generates, writes into the companion's
own config, and can revoke on its own — a narrower grant than the app token,
covering only this endpoint.
"""
from __future__ import annotations

import json
import logging
import time
import uuid
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from config import settings

router = APIRouter()
logger = logging.getLogger(__name__)

# Generous default: meeting notes with five sections do not fit in 500 tokens,
# and a silently truncated summary reads as a broken tool.
_DEFAULT_MAX_TOKENS = 2048


class ChatMessage(BaseModel):
    role: str
    content: Any          # str, or the list-of-parts form some clients send


class ChatRequest(BaseModel):
    model: Optional[str] = None
    messages: List[ChatMessage] = []
    temperature: Optional[float] = None
    max_tokens: Optional[int] = None
    stream: bool = False
    # Accepted-and-ignored is a lie when it changes the answer, so anything
    # meaningful we cannot honour is rejected in `_reject_unsupported`.
    tools: Optional[Any] = None
    functions: Optional[Any] = None
    n: Optional[int] = None


def _require_companion_key(authorization: Optional[str]) -> None:
    from services.companions import verify_companion_key
    token = ""
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization[7:].strip()
    if not verify_companion_key(token):
        raise HTTPException(
            status_code=401,
            detail="Invalid API key. LocalBook issues a companion key per tool — "
                   "connect the tool from Settings → Companions.",
        )


def _flatten(content: Any) -> str:
    """Accept both the plain-string and the list-of-parts content forms."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        out = []
        for part in content:
            if isinstance(part, dict) and part.get("type") == "text":
                out.append(str(part.get("text") or ""))
            elif isinstance(part, str):
                out.append(part)
        return "\n".join(out)
    return str(content or "")


def _reject_unsupported(req: ChatRequest) -> None:
    if req.tools or req.functions:
        raise HTTPException(status_code=400,
                            detail="Tool/function calling is not supported by this endpoint.")
    if req.n and req.n > 1:
        raise HTTPException(status_code=400, detail="n > 1 is not supported.")


def _split_messages(messages: List[ChatMessage]) -> tuple[str, str]:
    """Collapse the message list into the (system, prompt) pair the seam takes.

    Assistant turns are kept in the prompt so a multi-turn client still reads
    coherently, rather than having its own prior answers silently dropped.
    """
    system_parts, convo = [], []
    for m in messages:
        text = _flatten(m.content)
        if not text:
            continue
        role = (m.role or "user").lower()
        if role == "system":
            system_parts.append(text)
        elif role == "assistant":
            convo.append(f"Assistant: {text}")
        else:
            convo.append(text if len(messages) <= 2 else f"User: {text}")
    return "\n\n".join(system_parts), "\n\n".join(convo)


@router.get("/models")
async def list_models(authorization: Optional[str] = Header(None)):
    """What a companion may ask for. Reports the real configured checkpoints."""
    _require_companion_key(authorization)
    now = int(time.time())
    seen, data = set(), []
    for role, model in (("main", settings.main_model), ("fast", settings.fast_model)):
        if not model or model in seen:
            continue
        seen.add(model)
        data.append({"id": model, "object": "model", "created": now,
                     "owned_by": "localbook", "localbook_role": role})
    return {"object": "list", "data": data}


@router.post("/chat/completions")
async def chat_completions(req: ChatRequest, request: Request,
                           authorization: Optional[str] = Header(None)):
    _require_companion_key(authorization)
    _reject_unsupported(req)

    if not req.messages:
        raise HTTPException(status_code=400, detail="messages is required")

    system_prompt, prompt = _split_messages(req.messages)
    if not prompt.strip():
        raise HTTPException(status_code=400, detail="no user content in messages")

    # An unknown model name maps to the user's main model rather than failing:
    # a companion's config may name a model from whatever server it used before,
    # and refusing would be pedantry. The response reports what actually ran.
    model = req.model if req.model in (settings.main_model, settings.fast_model) else settings.main_model
    if req.model and req.model != model:
        logger.info(f"[openai-compat] requested '{req.model}' → serving '{model}'")

    max_tokens = int(req.max_tokens or _DEFAULT_MAX_TOKENS)
    approx_tokens = (len(system_prompt) + len(prompt)) // 4
    if approx_tokens > 14000:
        logger.warning(
            f"[openai-compat] prompt is ~{approx_tokens} tokens — close to the "
            f"deployed context limit; the model may truncate the input."
        )

    from services.llm_service import generate_text, stream_text
    created, cid = int(time.time()), f"chatcmpl-{uuid.uuid4().hex[:24]}"

    if req.stream:
        async def _events():
            first = {"id": cid, "object": "chat.completion.chunk", "created": created,
                     "model": model,
                     "choices": [{"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}]}
            yield f"data: {json.dumps(first)}\n\n"
            try:
                # stream_text selects by ROLE, not checkpoint name — it takes
                # use_fast_model rather than a model id, and names its
                # temperature override differently from generate_text.
                async for piece in stream_text(system_prompt, prompt,
                                               num_predict=max_tokens,
                                               temperature_override=req.temperature,
                                               use_fast_model=(model == settings.fast_model),
                                               voice_modifier=False):
                    if not piece:
                        continue
                    chunk = {"id": cid, "object": "chat.completion.chunk",
                             "created": created, "model": model,
                             "choices": [{"index": 0, "delta": {"content": piece},
                                          "finish_reason": None}]}
                    yield f"data: {json.dumps(chunk)}\n\n"
            except Exception as e:
                logger.warning(f"[openai-compat] stream failed: {e}")
            done = {"id": cid, "object": "chat.completion.chunk", "created": created,
                    "model": model,
                    "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]}
            yield f"data: {json.dumps(done)}\n\n"
            yield "data: [DONE]\n\n"

        return StreamingResponse(_events(), media_type="text/event-stream")

    text = await generate_text(system_prompt, prompt, model=model,
                               num_predict=max_tokens,
                               temperature=req.temperature,
                               voice_modifier=False)
    if not text:
        # The seam returns empty rather than raising when generation fails.
        # Surfacing that as a 503 tells the companion the truth; returning an
        # empty completion would have it write an empty notes file.
        raise HTTPException(
            status_code=503,
            detail="LocalBook's model produced no output. It may still be loading, "
                   "or the request may be too long for its context window.")

    return {
        "id": cid, "object": "chat.completion", "created": created, "model": model,
        "choices": [{"index": 0, "finish_reason": "stop",
                     "message": {"role": "assistant", "content": text}}],
        "usage": {
            "prompt_tokens": approx_tokens,
            "completion_tokens": len(text) // 4,
            "total_tokens": approx_tokens + len(text) // 4,
        },
    }
