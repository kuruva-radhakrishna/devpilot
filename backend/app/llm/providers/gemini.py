"""Gemini provider — wraps the existing gemini_client implementation.

All the Gemini-specific behavior (retry/backoff that respects the API's
retry_delay, per-day-quota fail-fast, tracing spans, token accounting) already
lives in gemini_client; this just presents it through the provider interface.
"""
from __future__ import annotations

from typing import Any, Callable

from app.llm import gemini_client as gc
from app.llm.providers.base import LLMProvider


class GeminiProvider(LLMProvider):
    name = "gemini"
    supports_tools = True

    def embed_texts(self, texts, *, task_type="retrieval_document"):
        return gc.embed_texts(texts, task_type=task_type)

    def generate(self, prompt, *, system=None, temperature=0.2):
        return gc.generate(prompt, system=system, temperature=temperature)

    def build_tool_model(self, system: str, tools: list[Callable[..., Any]]):
        return gc.build_tool_model(system, tools)

    def with_retry(self, fn, *, label="llm", max_retries: int = 6):
        return gc.with_retry(fn, label=label)

    def record_usage(self, resp: Any) -> None:
        gc._record_usage(resp)
