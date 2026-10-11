"""Session validity: is this token's version still the account's current one?

Tokens are stateless JWTs, so on their own they stay valid until they expire even
after a password change or account deletion. Each token therefore carries the
account's `token_version`; changing/resetting the password bumps it and deleting
the account removes it, and every request checks the token against the current
value. The lookup is cached for a few seconds so this is not one DB hit per
request, and the cache is cleared immediately when the process itself changes a
version (so the acting user sees it at once; other instances catch up within TTL).
"""
from __future__ import annotations

import threading
import time

from app.auth import db

_TTL_SECONDS = 20.0
_cache: dict[int, tuple[int | None, float]] = {}
_lock = threading.Lock()


def get_version(user_id: int) -> int | None:
    """Current token_version for the user, or None if the account is gone."""
    now = time.monotonic()
    with _lock:
        hit = _cache.get(user_id)
        if hit and now - hit[1] < _TTL_SECONDS:
            return hit[0]
    version = db.get_token_version(user_id)
    with _lock:
        if len(_cache) > 5000:  # bounded: drop everything rather than track LRU order
            _cache.clear()
        _cache[user_id] = (version, now)
    return version


def invalidate(user_id: int) -> None:
    with _lock:
        _cache.pop(user_id, None)
