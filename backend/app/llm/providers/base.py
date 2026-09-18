"""The LLM provider interface.

The whole DevPilot stack — retriever, agent runtime, repair loop, evaluation,
tracing — talks to a provider through this interface, so the same agent
architecture can be evaluated across models (Gemini, a local Ollama model, …)
by changing one setting. That separation of provider from runtime is the point:
the benchmark measures the SYSTEM, not one vendor's API.
"""
from __future__ import annotations

import time
from typing import Any, Callable


class LLMProvider:
    name: str = "base"
    supports_tools: bool = False           # native tool/function calling?
    retryable: tuple = ()                   # exception types worth retrying

    # --- embeddings ---
    def embed_texts(self, texts: list[str], *, task_type: str = "retrieval_document") -> list[list[float]]:
        raise NotImplementedError

    def embed_query(self, text: str) -> list[float]:
        return self.embed_texts([text], task_type="retrieval_query")[0]

    # --- generation ---
    def generate(self, prompt: str, *, system: str | None = None, temperature: float = 0.2) -> str:
        raise NotImplementedError

    def build_tool_model(self, system: str, tools: list[Callable[..., Any]]):
        raise NotImplementedError(f"provider '{self.name}' does not support tool calling")

    # --- shared retry/backoff (providers set `retryable`) ---
    def with_retry(self, fn: Callable[[], Any], *, label: str = "llm", max_retries: int = 5) -> Any:
        backoff = 2.0
        for attempt in range(max_retries + 1):
            try:
                return fn()
            except self.retryable as exc:  # type: ignore[misc]
                if attempt == max_retries:
                    raise
                print(f"[{label}] transient error ({type(exc).__name__}); "
                      f"retrying in {backoff:.0f}s (attempt {attempt + 1}/{max_retries})")
                time.sleep(backoff)
                backoff = min(backoff * 2, 30.0)

    # --- token accounting hook (no-op unless overridden) ---
    def record_usage(self, resp: Any) -> None:
        pass
