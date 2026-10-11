"""Thin wrapper around the Google Gen AI SDK (`google-genai`).

Two responsibilities:
  1. Embeddings  -> embed_texts()  (used by the RAG pipeline)
  2. Generation  -> generate() and build_tool_model() (used by the agent)

Why `google-genai` and not the older `google-generativeai`: the old library is
deprecated, and it rejected the newer "AQ." style API keys. `google-genai` sends
every key as an `x-goog-api-key` header over plain REST — the same way the n8n
project calls Gemini — so both key formats work. It also gives each key its own
client object, which removes the old process-global "configure()" key that could
interleave between concurrent BYOK requests.

Keeping all Gemini-specific code here means swapping providers only touches this
file (and providers/gemini.py).
"""
from __future__ import annotations

import re
import time
from functools import lru_cache
from typing import Any, Callable

from google import genai
from google.genai import errors as gerrors
from google.genai import types

from app.config import get_settings
from app.llm import usage
from app.obs import tracing

# HTTP status codes worth retrying with backoff (rate limit + transient server errors).
_RETRYABLE_CODES = {429, 500, 502, 503, 504}
_MAX_RETRIES = 6
_MAX_BACKOFF = 65.0  # seconds; free-tier 429s often ask for ~60s
_TIMEOUT_MS = 180_000  # per HTTP request; big repair prompts can take a while


def _is_retryable(exc: Exception) -> bool:
    return isinstance(exc, gerrors.APIError) and getattr(exc, "code", None) in _RETRYABLE_CODES


def _suggested_delay(exc: Exception) -> float | None:
    """Pull the server-suggested retry delay out of a Gemini error, if present."""
    msg = str(exc)
    m = (re.search(r"retry in ([\d.]+)s", msg)
         or re.search(r"retryDelay['\"]?:\s*['\"]?([\d.]+)s", msg)
         or re.search(r"seconds:\s*(\d+)", msg))
    return float(m.group(1)) if m else None


def with_retry(fn: Callable[[], Any], *, label: str = "gemini") -> Any:
    """Call fn(), retrying transient errors (esp. 429) with backoff.

    Respects the API's own suggested retry delay when it provides one — the free
    tier caps generate_content at ~5 req/min, so the harness leans on this to
    finish a full benchmark without manual pacing.
    """
    backoff = 3.0
    for attempt in range(_MAX_RETRIES + 1):
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001 — re-raised unless transient
            if not _is_retryable(exc):
                raise
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
    pt = getattr(um, "prompt_token_count", 0) or 0
    ot = getattr(um, "candidates_token_count", 0) or 0
    tt = getattr(um, "total_token_count", 0) or 0
    usage.record(pt, ot, tt)
    tracing.add_attrs(prompt_tokens=pt, output_tokens=ot, total_tokens=tt)


def _effective_key() -> str:
    """The key to use for this call: the request's BYOK key if present, else the
    server's configured GEMINI_API_KEY."""
    from app.llm.keyctx import get_request_key

    key = (get_request_key() or get_settings().gemini_api_key or "").strip()
    if not key:
        raise RuntimeError(
            "No Gemini API key available. Either set GEMINI_API_KEY on the server, "
            "or paste your own key in the app (Settings → API key). Get one free at "
            "https://aistudio.google.com/app/apikey"
        )
    return key


@lru_cache(maxsize=16)
def _client_for(key: str, base_url: str) -> genai.Client:
    """One client per key, so concurrent requests with different BYOK keys can
    never see each other's credentials. `base_url` is only set for tests/proxies."""
    opts = types.HttpOptions(timeout=_TIMEOUT_MS, base_url=base_url or None)
    return genai.Client(api_key=key, http_options=opts)


def _client() -> genai.Client:
    return _client_for(_effective_key(), get_settings().gemini_base_url)


# --------------------------------------------------------------------------- #
# Embeddings
# --------------------------------------------------------------------------- #
def embed_texts(texts: list[str], *, task_type: str = "retrieval_document") -> list[list[float]]:
    """Embed a batch of texts. Returns one vector (list[float]) per input.

    task_type is a Gemini feature: "retrieval_document" when indexing chunks,
    "retrieval_query" when embedding a user's question. Matching them improves
    retrieval quality.
    """
    client = _client()
    settings = get_settings()
    vectors: list[list[float]] = []
    BATCH = 100  # stay well under per-request limits
    for i in range(0, len(texts), BATCH):
        batch = texts[i : i + BATCH]
        with tracing.span("llm.embed", model=settings.gemini_embed_model,
                          n=len(batch), task_type=task_type):
            resp = with_retry(
                lambda: client.models.embed_content(
                    model=settings.gemini_embed_model,
                    contents=batch,
                    config=types.EmbedContentConfig(
                        task_type=task_type.upper(),
                        output_dimensionality=settings.embed_dim,
                    ),
                ),
                label="embed",
            )
        vectors.extend([list(e.values) for e in (resp.embeddings or [])])
    return vectors


def embed_query(text: str) -> list[float]:
    return embed_texts([text], task_type="retrieval_query")[0]


def validate_key() -> None:
    """Cheaply verify the effective key (request BYOK key or server key) is a
    real, working Gemini credential. Fetches model metadata rather than
    generating anything, so it doesn't spend generate/embed quota — raises on
    an invalid key exactly like any other Gemini call would."""
    _client().models.get(model=get_settings().gemini_embed_model)


# --------------------------------------------------------------------------- #
# Generation
# --------------------------------------------------------------------------- #
def generate(prompt: str, *, system: str | None = None, temperature: float = 0.2) -> str:
    """Single-shot text generation (no tools)."""
    client = _client()
    settings = get_settings()
    config = types.GenerateContentConfig(system_instruction=system, temperature=temperature)
    with tracing.span("llm.generate", model=settings.gemini_model, temperature=temperature):
        resp = with_retry(
            lambda: client.models.generate_content(
                model=settings.gemini_model, contents=prompt, config=config),
            label="generate",
        )
        _record_usage(resp)
    return resp.text or ""


class _ToolChat:
    """One tool-using conversation. Gemini decides which tools to call; the SDK
    runs them (automatic function calling) and feeds results back until the model
    writes a final answer, or `maximum_remote_calls` tool calls have been made."""

    def __init__(self, client: genai.Client, model: str, config: types.GenerateContentConfig):
        self._chat = client.chats.create(model=model, config=config)
        self.history: list = []

    def send_message(self, text: str):
        resp = self._chat.send_message(text)
        afc = getattr(resp, "automatic_function_calling_history", None)
        self.history = list(afc) if afc else list(self._chat.get_history())
        return resp


class _ToolModel:
    def __init__(self, client: genai.Client, model: str, config: types.GenerateContentConfig):
        self._client, self._model, self._config = client, model, config

    def start_chat(self) -> _ToolChat:
        return _ToolChat(self._client, self._model, self._config)


def build_tool_model(system: str, tools: list[Callable[..., Any]]) -> _ToolModel:
    """Create a Gemini model with automatic function-calling enabled.

    `tools` are plain Python callables with type hints and docstrings — the SDK
    reads those to build the function schema Gemini sees. The cap on tool calls
    is real: the SDK stops running tools after AGENT_MAX_STEPS of them.
    """
    settings = get_settings()
    config = types.GenerateContentConfig(
        system_instruction=system,
        tools=list(tools),
        temperature=0.1,
        automatic_function_calling=types.AutomaticFunctionCallingConfig(
            # The SDK counts MODEL calls, not tool runs: N tool calls need N+1
            # model calls (the last one writes the answer).
            maximum_remote_calls=settings.agent_max_steps + 1),
    )
    return _ToolModel(_client(), settings.gemini_model, config)
