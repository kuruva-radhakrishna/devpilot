"""Provider-neutral LLM entry point.

The rest of the codebase imports generate/embed_texts/embed_query/etc. from here
and never touches a specific provider. The active provider is chosen by
LLM_PROVIDER (config) or an explicit set_provider() override (e.g. the eval CLI's
--provider flag).
"""
from __future__ import annotations

from functools import lru_cache
from typing import Any, Callable

from app.config import get_settings
from app.llm.providers.base import LLMProvider

_override: str | None = None


@lru_cache(maxsize=None)
def _build(name: str) -> LLMProvider:
    name = (name or "gemini").lower()
    if name == "ollama":
        from app.llm.providers.ollama import OllamaProvider
        return OllamaProvider()
    from app.llm.providers.gemini import GeminiProvider
    return GeminiProvider()


def get_provider() -> LLMProvider:
    return _build(_override or get_settings().llm_provider)


def set_provider(name: str | None) -> None:
    """Override the configured provider (used by the eval CLI). None resets."""
    global _override
    _override = name


# --- delegating convenience functions (what the rest of the app imports) ---
def generate(prompt: str, *, system: str | None = None, temperature: float = 0.2) -> str:
    return get_provider().generate(prompt, system=system, temperature=temperature)


def embed_texts(texts: list[str], *, task_type: str = "retrieval_document") -> list[list[float]]:
    return get_provider().embed_texts(texts, task_type=task_type)


def embed_query(text: str) -> list[float]:
    return get_provider().embed_query(text)


def build_tool_model(system: str, tools: list[Callable[..., Any]]):
    return get_provider().build_tool_model(system, tools)


def with_retry(fn: Callable[[], Any], *, label: str = "llm") -> Any:
    return get_provider().with_retry(fn, label=label)


def record_usage(resp: Any) -> None:
    get_provider().record_usage(resp)


def supports_tools() -> bool:
    return get_provider().supports_tools


def provider_name() -> str:
    return get_provider().name


def embed_signature() -> dict:
    """Identity of the CURRENT embedding configuration. Persisted with each
    ingested repo so a provider/model change (which puts vectors in a different
    space) triggers re-ingestion instead of silently corrupting retrieval."""
    s = get_settings()
    p = get_provider().name
    model = s.ollama_embed_model if p == "ollama" else s.gemini_embed_model
    return {"provider": p, "embedding_model": model, "embedding_dimensions": s.embed_dim}
