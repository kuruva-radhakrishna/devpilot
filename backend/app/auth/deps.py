"""Request dependencies: current-user auth guard + BYOK key binding.

Both are cheap FastAPI dependencies attached to the protected routers.
"""
from __future__ import annotations

from typing import Any, Iterator

import jwt
from fastapi import Depends, Header, HTTPException

from app.auth.security import decode_token
from app.config import get_settings
from app.llm.keyctx import reset_request_key, set_request_key


def bind_gemini_key(
    x_gemini_key: str | None = Header(default=None),
) -> Iterator[str | None]:
    """Bind the request's BYOK Gemini key (if any) for the duration of the call,
    then clear it. Attached to routers so downstream LLM calls pick it up."""
    token = set_request_key((x_gemini_key or "").strip() or None)
    try:
        yield x_gemini_key
    finally:
        reset_request_key(token)


def get_current_user(
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    """Resolve the caller from a `Bearer <jwt>` header.

    When auth isn't configured (no DATABASE_URL), the app runs open and this
    returns an anonymous principal so local dev needs no login.
    """
    if not get_settings().auth_available:
        return {"id": None, "email": "anonymous", "anonymous": True}

    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(401, "Not authenticated. Log in to use DevPilot.")
    token = authorization.split(" ", 1)[1].strip()
    try:
        claims = decode_token(token)
    except jwt.ExpiredSignatureError:
        raise HTTPException(401, "Session expired. Please log in again.")
    except jwt.PyJWTError:
        raise HTTPException(401, "Invalid session token.")
    return {"id": int(claims["sub"]), "email": claims.get("email", ""), "anonymous": False}


# Convenience: routers list these in `dependencies=[...]`.
CurrentUser = Depends(get_current_user)
BoundKey = Depends(bind_gemini_key)
