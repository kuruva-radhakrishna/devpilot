"""Ollama provider — run DevPilot against a local model, no API key or quota.

Requires an Ollama server (https://ollama.com) reachable at OLLAMA_HOST, with the
configured chat + embedding models pulled, e.g.:

    ollama pull qwen2.5-coder:7b
    ollama pull nomic-embed-text

Does NOT support native tool calling here, so the agent's QA tool-loop (run_agent)
is Gemini-only; the repair loop and retrieval work on any provider because they
use only generate() + embeddings. That keeps the benchmark's headline (repair
success) measurable across models.
"""
from __future__ import annotations

from typing import Any

from app.config import get_settings
from app.llm import usage
from app.llm.providers.base import LLMProvider
from app.obs import tracing


class OllamaProvider(LLMProvider):
    name = "ollama"
    supports_tools = False

    def __init__(self):
        import httpx  # imported lazily so Gemini users don't need it
        self._httpx = httpx
        self.retryable = (httpx.HTTPError, httpx.TimeoutException)
        s = get_settings()
        self._host = s.ollama_host.rstrip("/")
        self._model = s.ollama_model
        self._embed_model = s.ollama_embed_model
        self._dim = s.embed_dim

    def _post(self, path: str, payload: dict) -> dict:
        resp = self._httpx.post(self._host + path, json=payload, timeout=300)
        resp.raise_for_status()
        return resp.json()

    # --- embeddings ---
    def embed_texts(self, texts, *, task_type="retrieval_document"):
        vectors: list[list[float]] = []
        with tracing.span("llm.embed", model=self._embed_model, n=len(texts)):
            # /api/embed accepts a batch and returns {"embeddings": [[...], ...]}.
            data = self.with_retry(
                lambda: self._post("/api/embed", {"model": self._embed_model, "input": texts}),
                label="embed",
            )
            embs = data.get("embeddings")
            if embs is None and "embedding" in data:  # older single-item shape
                embs = [data["embedding"]]
            for v in embs or []:
                vectors.append([float(x) for x in v])
        return vectors

    # --- generation ---
    def generate(self, prompt, *, system=None, temperature=0.2):
        payload = {
            "model": self._model,
            "prompt": prompt,
            "system": system or "",
            "stream": False,
            "options": {"temperature": temperature},
        }
        with tracing.span("llm.generate", model=self._model, temperature=temperature):
            data = self.with_retry(lambda: self._post("/api/generate", payload), label="generate")
            self._record_usage(data)
        return data.get("response", "")

    def _record_usage(self, data: dict) -> None:
        pt = data.get("prompt_eval_count", 0) or 0
        ot = data.get("eval_count", 0) or 0
        usage.record(pt, ot, pt + ot)
        tracing.add_attrs(prompt_tokens=pt, output_tokens=ot, total_tokens=pt + ot)

    def record_usage(self, resp: Any) -> None:
        if isinstance(resp, dict):
            self._record_usage(resp)
