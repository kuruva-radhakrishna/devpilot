"""Per-task LLM usage accounting (tokens + call count).

The eval harness calls reset() before a task and snapshot() after, so it can
report token usage and cost per benchmark case. In normal app runs nothing calls
reset(), so record() is a cheap no-op — zero overhead outside evaluation.

Uses a ContextVar so concurrent tasks don't clobber each other's counters.
"""
from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass

# Approx Gemini 2.5 Flash pricing (USD per 1M tokens) — override as needed. These
# are only used to give a rough cost estimate in the report, not for billing.
PRICE_PER_1M_INPUT = 0.30
PRICE_PER_1M_OUTPUT = 2.50


@dataclass
class Usage:
    calls: int = 0
    prompt_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0

    def cost_usd(self) -> float:
        return (
            self.prompt_tokens / 1_000_000 * PRICE_PER_1M_INPUT
            + self.output_tokens / 1_000_000 * PRICE_PER_1M_OUTPUT
        )


_acc: ContextVar[Usage | None] = ContextVar("llm_usage", default=None)


def reset() -> None:
    _acc.set(Usage())


def record(prompt_tokens: int, output_tokens: int, total_tokens: int) -> None:
    u = _acc.get()
    if u is None:
        return  # not tracking (normal app run)
    u.calls += 1
    u.prompt_tokens += prompt_tokens or 0
    u.output_tokens += output_tokens or 0
    u.total_tokens += total_tokens or 0


def snapshot() -> Usage:
    return _acc.get() or Usage()
