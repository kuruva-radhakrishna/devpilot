"""Thin wrapper around the Google Gemini SDK.

Two responsibilities:
  1. Embeddings  -> embed_texts()  (used by the RAG pipeline)
  2. Generation  -> generate() and generate_with_tools() (used by the agent)

Keeping all Gemini-specific code here means swapping providers later only
touches this one file (see README "Swapping the LLM provider").
"""
from __future__ import annotations

import re
import time
from typing import Any, Callable

import google.generativeai as genai
from google.api_core import exceptions as gexc

from app.config import get_settings
from app.llm import usage
from app.obs import tracing

_configured = False

# Transient errors worth retrying with backoff.
_RETRYABLE = (
    gexc.ResourceExhausted,   # 429 rate limit / quota
    gexc.ServiceUnavailable,  # 503
    gexc.InternalServerError, # 500
    gexc.DeadlineExceeded,    # 504
)
_MAX_RETRIES = 6
_MAX_BACKOFF = 65.0  # seconds; free-tier 429s often ask for ~60s


def _suggested_delay(exc: Exception) -> float | None:
    """Pull the server-suggested retry delay out of a Gemini error, if present."""
    msg = str(exc)
    m = re.search(r"retry in ([\d.]+)s", msg) or re.search(r"seconds:\s*(\d+)", msg)
    return float(m.group(1)) if m else None


def with_retry(fn: Callable[[], Any], *, label: str = "gemini") -> Any:
    """Call fn(), retrying transient errors (esp. 429) with backoff.

    Respects the API's own suggested retry_delay when it provides one — the free
    tier caps generate_content at ~5 req/min, so the harness leans on this to
    finish a full benchmark without manual pacing.
    """
    backoff = 3.0
    for attempt in range(_MAX_RETRIES + 1):
        try:
            return fn()
        except _RETRYABLE as exc:
            # A per-DAY quota won't recover for hours — don't waste minutes
            # retrying it; surface it immediately so the caller can degrade.
            if "PerDay" in str(exc) or "RequestsPerDay" in str(exc):
                raise
            if attempt == _MAX_RETRIES:
                raise
            wait = _suggested_delay(exc) or backoff
            wait = min(wait + 0.5, _MAX_BACKOFF)  # small cushion over the suggestion
            print(f"[{label}] transient error ({type(exc).__name__}); "
                  f"retrying in {wait:.0f}s (attempt {attempt + 1}/{_MAX_RETRIES})")
            time.sleep(wait)
            backoff = min(backoff * 2, _MAX_BACKOFF)


def _record_usage(resp) -> None:
    """Best-effort token accounting from a Gemini response (no-op unless the eval
    harness is tracking)."""
    um = getattr(resp, "usage_metadata", None)
    if um is None:
        return
    pt = getattr(um, "prompt_token_count", 0)
    ot = getattr(um, "candidates_token_count", 0)
    tt = getattr(um, "total_token_count", 0)
    usage.record(pt, ot, tt)
    tracing.add_attrs(prompt_tokens=pt, output_tokens=ot, total_tokens=tt)


def _ensure_configured() -> None:
    global _configured
    if _configured:
        return
    settings = get_settings()
    if not settings.gemini_api_key:
        raise RuntimeError(
            "GEMINI_API_KEY is not set. Copy .env.example to .env and add your key "
            "from https://aistudio.google.com/app/apikey"
        )
    genai.configure(api_key=settings.gemini_api_key)
    _configured = True


# --------------------------------------------------------------------------- #
# Embeddings
# --------------------------------------------------------------------------- #
def embed_texts(texts: list[str], *, task_type: str = "retrieval_document") -> list[list[float]]:
    """Embed a batch of texts. Returns one vector (list[float]) per input.

    task_type is a Gemini feature: "retrieval_document" when indexing chunks,
    "retrieval_query" when embedding a user's question. Matching them improves
    retrieval quality.
    """
    _ensure_configured()
    settings = get_settings()
    vectors: list[list[float]] = []
    # The SDK accepts a list, but we chunk to stay well under request limits.
    BATCH = 100
    for i in range(0, len(texts), BATCH):
        batch = texts[i : i + BATCH]
        with tracing.span("llm.embed", model=settings.gemini_embed_model,
                          n=len(batch), task_type=task_type):
            resp = with_retry(
                lambda: genai.embed_content(
                    model=f"models/{settings.gemini_embed_model}",
                    content=batch,
                    task_type=task_type,
                    output_dimensionality=settings.embed_dim,
                ),
                label="embed",
            )
        # SDK returns {"embedding": [...]} for single, {"embedding": [[...], ...]} for batch
        emb = resp["embedding"]
        if batch and isinstance(emb[0], (int, float)):
            vectors.append(list(emb))  # single-item batch
        else:
            vectors.extend([list(v) for v in emb])
    return vectors


def embed_query(text: str) -> list[float]:
    return embed_texts([text], task_type="retrieval_query")[0]


# --------------------------------------------------------------------------- #
# Generation
# --------------------------------------------------------------------------- #
def generate(prompt: str, *, system: str | None = None, temperature: float = 0.2) -> str:
    """Single-shot text generation (no tools)."""
    _ensure_configured()
    settings = get_settings()
    model = genai.GenerativeModel(
        settings.gemini_model,
        system_instruction=system,
        generation_config={"temperature": temperature},
    )
    with tracing.span("llm.generate", model=settings.gemini_model, temperature=temperature):
        resp = with_retry(lambda: model.generate_content(prompt), label="generate")
        _record_usage(resp)
    return resp.text or ""


def build_tool_model(system: str, tools: list[Callable[..., Any]]):
    """Create a Gemini model with automatic function-calling enabled.

    `tools` are plain Python callables with type hints and docstrings — the SDK
    reads those to build the function schema Gemini sees. The agent runtime
    starts a chat with enable_automatic_function_calling=True and Gemini decides
    when to call them.
    """
    _ensure_configured()
    settings = get_settings()
    return genai.GenerativeModel(
        settings.gemini_model,
        system_instruction=system,
        tools=tools,
        generation_config={"temperature": 0.1},
    )
