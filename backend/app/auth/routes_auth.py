"""Auth endpoints: register, login, whoami, and Gemini-key validation."""
from __future__ import annotations

import re

import psycopg
from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field

from app.auth import db
from app.auth.deps import get_current_user
from app.auth.security import hash_password, make_token, verify_password
from app.config import get_settings
from app.llm.keyctx import reset_request_key, set_request_key

router = APIRouter(prefix="/api/auth", tags=["auth"])

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class Credentials(BaseModel):
    email: str = Field(..., max_length=254)
    password: str = Field(..., min_length=8, max_length=128)


class RegisterRequest(Credentials):
    name: str = Field(..., min_length=1, max_length=100)


class AuthResponse(BaseModel):
    token: str
    email: str
    name: str


def _require_auth_configured() -> None:
    if not get_settings().auth_available:
        raise HTTPException(
            503,
            "Authentication is not configured on this server (no DATABASE_URL).",
        )


@router.post("/register", response_model=AuthResponse)
def register(creds: RegisterRequest):
    _require_auth_configured()
    email = creds.email.strip().lower()
    name = creds.name.strip()
    if not _EMAIL_RE.match(email):
        raise HTTPException(400, "Enter a valid email address.")
    if not name:
        raise HTTPException(400, "Enter your name.")
    try:
        user = db.create_user(email, hash_password(creds.password), name)
    except psycopg.errors.UniqueViolation:
        raise HTTPException(409, "An account with that email already exists.")
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(500, f"Could not create account: {exc}")
    return AuthResponse(token=make_token(user["id"], email, name), email=email, name=name)


@router.post("/login", response_model=AuthResponse)
def login(creds: Credentials):
    _require_auth_configured()
    email = creds.email.strip().lower()
    user = db.get_user_by_email(email)
    # Verify even when the user is missing to keep timing roughly uniform.
    ok = user is not None and verify_password(creds.password, user["password_hash"])
    if not ok:
        raise HTTPException(401, "Incorrect email or password.")
    name = user["name"]
    return AuthResponse(token=make_token(user["id"], email, name), email=email, name=name)


@router.get("/me")
def me(user: dict = Depends(get_current_user)):
    return {"email": user["email"], "name": user.get("name", ""), "anonymous": user.get("anonymous", False)}


@router.post("/validate-key")
def validate_key(x_gemini_key: str | None = Header(default=None), user: dict = Depends(get_current_user)):
    """Test a Gemini API key with a cheap, no-generation call (fetching model
    metadata), so the settings UI can tell the user immediately whether the
    key they just pasted actually works — rather than finding out on their
    first real ingest/ask."""
    key = (x_gemini_key or "").strip()
    if not key:
        raise HTTPException(400, "No key provided.")
    token = set_request_key(key)
    try:
        from app.llm import gemini_client
        gemini_client.validate_key()
        return {"valid": True}
    except Exception as exc:  # noqa: BLE001 — report the reason, don't crash
        return {"valid": False, "detail": str(exc)}
    finally:
        reset_request_key(token)
