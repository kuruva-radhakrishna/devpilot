"""In-memory sliding-window rate limiting for the auth endpoints.

Scope, stated plainly: this lives in ONE process's memory, so it is exact on a
single instance (the Render free tier) and per-instance if you scale out; a
multi-instance deployment would need Redis. It exists to stop password guessing
and email-bombing, not to be a general API gateway.
"""
from __future__ import annotations

import threading
import time
from collections import deque

from fastapi import HTTPException, Request


class SlidingWindowLimiter:
    def __init__(self, max_events: int, window_seconds: float):
        self.max_events = max_events
        self.window = window_seconds
        self._events: dict[str, deque] = {}
        self._lock = threading.Lock()

    def _prune(self, key: str, now: float) -> deque:
        q = self._events.setdefault(key, deque())
        while q and now - q[0] >= self.window:
            q.popleft()
        return q

    def retry_after(self, key: str) -> int:
        """Seconds until `key` may act again; 0 if it is under the limit."""
        now = time.monotonic()
        with self._lock:
            q = self._prune(key, now)
            if len(q) < self.max_events:
                return 0
            return max(1, int(self.window - (now - q[0])) + 1)

    def hit(self, key: str) -> None:
        now = time.monotonic()
        with self._lock:
            if len(self._events) > 20000:  # bound memory under a key-flooding attack
                self._events.clear()
            self._prune(key, now).append(now)

    def clear(self, key: str) -> None:
        with self._lock:
            self._events.pop(key, None)

    def reset(self) -> None:
        with self._lock:
            self._events.clear()


MIN = 60.0
HOUR = 3600.0

# Failed logins: per (email, ip), per email overall, and per ip overall.
login_fail_pair = SlidingWindowLimiter(5, 15 * MIN)
login_fail_email = SlidingWindowLimiter(30, 15 * MIN)
login_fail_ip = SlidingWindowLimiter(30, 15 * MIN)
# Wrong current-password attempts on change-password / delete-account, per user.
sensitive_fail = SlidingWindowLimiter(5, 15 * MIN)
register_ip = SlidingWindowLimiter(10, HOUR)
forgot_email = SlidingWindowLimiter(3, HOUR)
forgot_ip = SlidingWindowLimiter(10, HOUR)
reset_ip = SlidingWindowLimiter(20, HOUR)

_ALL = [login_fail_pair, login_fail_email, login_fail_ip, sensitive_fail,
        register_ip, forgot_email, forgot_ip, reset_ip]


def reset_all() -> None:
    """For tests."""
    for limiter in _ALL:
        limiter.reset()


def client_ip(request: Request) -> str:
    """Best-effort caller IP. Behind Render's proxy the first X-Forwarded-For entry
    is the client; it can be spoofed, which is why per-email limits exist too."""
    fwd = request.headers.get("x-forwarded-for", "")
    first = fwd.split(",")[0].strip()
    if first:
        return first
    return request.client.host if request.client else "unknown"


def too_many(seconds: int, what: str = "attempts") -> HTTPException:
    minutes = max(1, round(seconds / 60))
    return HTTPException(
        429,
        f"Too many {what}. Try again in about {minutes} minute{'s' if minutes != 1 else ''}.",
        headers={"Retry-After": str(seconds)},
    )
