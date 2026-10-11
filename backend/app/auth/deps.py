"""Request dependencies: the current-user auth guard.

BYOK key binding used to live here too, as a `yield`-based dependency. It's
NOT — FastAPI runs a sync route's dependency-enter, the route body, and a
yield-dependency's exit as separate `anyio.to_thread.run_sync` calls, which
are not guaranteed to land on the same worker thread. A ContextVar's set()
in one thread and reset() of that token in another raises
`ValueError: token was created in a different Context` — confirmed in
production logs as the actual cause of a 500 on every BYOK-protected route.
The fix (see routes_repo.py / routes_agent.py): set/reset the key directly
inside each route body, which FastAPI dispatches as a single call on one
thread, instead of via a separate dependency.
"""
from __future__ import annotations

from typing import Any

import jwt
import psycopg
from fastapi import Depends, Header, HTTPException

from app.auth import session
from app.auth.security import decode_token
from app.config import get_settings


def get_current_user(
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    """Resolve the caller from a `Bearer <jwt>` header.

    When auth isn't configured (no DATABASE_URL), the app runs open and this
    returns an anonymous principal so local dev needs no login.
    """
    if not get_settings().auth_available:
        return {"id": None, "email": "anonymous", "name": "", "anonymous": True}

    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(401, "Not authenticated. Log in to use DevPilot.")
    token = authorization.split(" ", 1)[1].strip()
    try:
        claims = decode_token(token)
        user_id = int(claims["sub"])
        token_version = int(claims.get("tv", 0))  # tokens from before this field existed are v0
    except jwt.ExpiredSignatureError:
        raise HTTPException(401, "Session expired. Please log in again.")
    except (jwt.PyJWTError, KeyError, ValueError):
        raise HTTPException(401, "Invalid session token.")

    # A password change/reset or account deletion kills every older session.
    try:
        current = session.get_version(user_id)
    except psycopg.Error:
        raise HTTPException(503, "Sign-in is temporarily unavailable. Please try again shortly.")
    if current is None or current != token_version:
        raise HTTPException(401, "Session expired. Please log in again.")

    return {
        "id": user_id,
        "email": claims.get("email", ""),
        "name": claims.get("name", ""),
        "anonymous": False,
    }


# Convenience: routers list this in `dependencies=[...]`. Plain function
# dependency (no yield), so it isn't subject to the threadpool-thread
# mismatch described above.
CurrentUser = Depends(get_current_user)
