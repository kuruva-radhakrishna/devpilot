"""Per-request LLM API key (BYOK — "bring your own key").

A visitor can paste their own Gemini API key in the UI; it rides along on each
request as the `X-Gemini-Key` header and is used *only* for that request. It is
never persisted, never logged, and never leaves the process. When absent, the
Gemini client falls back to the server's configured GEMINI_API_KEY.

Why this matters: the free Gemini tier is ~20 generate calls/day *per key*. With
one shared server key, every visitor drains the same tiny quota. Letting each
visitor bring their own key means the public demo keeps working — each person
spends their own quota, and nobody sees anyone else's key.
"""
from __future__ import annotations

import contextvars

# Holds the current request's Gemini key (or None). Read by gemini_client.
_request_key: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "devpilot_request_gemini_key", default=None
)


def get_request_key() -> str | None:
    return _request_key.get()


def set_request_key(value: str | None) -> contextvars.Token:
    return _request_key.set(value or None)


def reset_request_key(token: contextvars.Token) -> None:
    _request_key.reset(token)
