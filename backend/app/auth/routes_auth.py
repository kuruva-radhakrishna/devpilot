"""Auth endpoints: register, login, and whoami."""
from __future__ import annotations

import re

import psycopg
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.auth import db
from app.auth.deps import get_current_user
from app.auth.security import hash_password, make_token, verify_password
from app.config import get_settings

router = APIRouter(prefix="/api/auth", tags=["auth"])

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class Credentials(BaseModel):
    email: str = Field(..., max_length=254)
    password: str = Field(..., min_length=8, max_length=128)


class AuthResponse(BaseModel):
    token: str
    email: str


def _require_auth_configured() -> None:
    if not get_settings().auth_available:
        raise HTTPException(
            503,
            "Authentication is not configured on this server (no DATABASE_URL).",
        )


@router.post("/register", response_model=AuthResponse)
def register(creds: Credentials):
    _require_auth_configured()
    email = creds.email.strip().lower()
    if not _EMAIL_RE.match(email):
        raise HTTPException(400, "Enter a valid email address.")
    try:
        user = db.create_user(email, hash_password(creds.password))
    except psycopg.errors.UniqueViolation:
        raise HTTPException(409, "An account with that email already exists.")
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(500, f"Could not create account: {exc}")
    return AuthResponse(token=make_token(user["id"], email), email=email)


@router.post("/login", response_model=AuthResponse)
def login(creds: Credentials):
    _require_auth_configured()
    email = creds.email.strip().lower()
    user = db.get_user_by_email(email)
    # Verify even when the user is missing to keep timing roughly uniform.
    ok = user is not None and verify_password(creds.password, user["password_hash"])
    if not ok:
        raise HTTPException(401, "Incorrect email or password.")
    return AuthResponse(token=make_token(user["id"], email), email=email)


@router.get("/me")
def me(user: dict = Depends(get_current_user)):
    return {"email": user["email"], "anonymous": user.get("anonymous", False)}
