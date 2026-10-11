"""Auth endpoints: register, login, whoami, password change/reset, account
deletion, and Gemini-key validation."""
from __future__ import annotations

import re
from functools import lru_cache

import psycopg
from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Request
from pydantic import BaseModel, Field

from app.auth import db, mailer, ratelimit, session
from app.auth.deps import get_current_user
from app.auth.security import (
    hash_password, hash_reset_token, make_token, new_reset_token,
    password_problem, verify_password,
)
from app.config import get_settings
from app.llm.client import describe_error
from app.llm.keyctx import reset_request_key, set_request_key

router = APIRouter(prefix="/api/auth", tags=["auth"])

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class Credentials(BaseModel):
    email: str = Field(..., max_length=254)
    password: str = Field(..., min_length=8, max_length=128)


class RegisterRequest(Credentials):
    name: str = Field(..., min_length=1, max_length=100)


class ForgotRequest(BaseModel):
    email: str = Field(..., max_length=254)


class ResetRequest(BaseModel):
    token: str = Field(..., min_length=20, max_length=200)
    password: str = Field(..., min_length=8, max_length=128)


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(..., min_length=1, max_length=128)
    new_password: str = Field(..., min_length=8, max_length=128)


class DeleteAccountRequest(BaseModel):
    password: str = Field(..., min_length=1, max_length=128)


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


def _require_real_user(user: dict) -> None:
    if user.get("anonymous") or user.get("id") is None:
        raise HTTPException(400, "Accounts aren't enabled on this server.")


def _norm(email: str) -> str:
    return email.strip().lower()


def _issue(user: dict, token_version: int | None = None) -> AuthResponse:
    version = user.get("token_version", 0) if token_version is None else token_version
    return AuthResponse(
        token=make_token(user["id"], user["email"], user["name"], version),
        email=user["email"], name=user["name"],
    )


@lru_cache(maxsize=1)
def _dummy_hash() -> str:
    """A real bcrypt hash to check against when the email is unknown, so a login
    for a missing account takes as long as one for an existing account."""
    return hash_password("not-a-real-password")


# --------------------------------------------------------------------------- #
# Sign up / sign in
# --------------------------------------------------------------------------- #
@router.post("/register", response_model=AuthResponse)
def register(creds: RegisterRequest, request: Request):
    _require_auth_configured()
    ip = ratelimit.client_ip(request)
    retry = ratelimit.register_ip.retry_after(ip)
    if retry:
        raise ratelimit.too_many(retry, "sign-up attempts")
    ratelimit.register_ip.hit(ip)

    email = _norm(creds.email)
    name = creds.name.strip()
    if not _EMAIL_RE.match(email):
        raise HTTPException(400, "Enter a valid email address.")
    if not name:
        raise HTTPException(400, "Enter your name.")
    problem = password_problem(creds.password, email, name)
    if problem:
        raise HTTPException(400, problem)
    try:
        user = db.create_user(email, hash_password(creds.password), name)
    except psycopg.errors.UniqueViolation:
        raise HTTPException(409, "An account with that email already exists.")
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(500, f"Could not create account: {exc}")
    return _issue(user)


@router.post("/login", response_model=AuthResponse)
def login(creds: Credentials, request: Request):
    _require_auth_configured()
    email = _norm(creds.email)
    ip = ratelimit.client_ip(request)
    pair_key = f"{email}|{ip}"
    limiters = (
        (ratelimit.login_fail_pair, pair_key),
        (ratelimit.login_fail_email, email),
        (ratelimit.login_fail_ip, ip),
    )
    for limiter, key in limiters:
        retry = limiter.retry_after(key)
        if retry:
            raise ratelimit.too_many(retry, "login attempts")

    user = db.get_user_by_email(email)
    ok = verify_password(creds.password, user["password_hash"] if user else _dummy_hash())
    if not user or not ok:
        for limiter, key in limiters:
            limiter.hit(key)
        raise HTTPException(401, "Incorrect email or password.")
    ratelimit.login_fail_pair.clear(pair_key)
    return _issue(user)


@router.get("/me")
def me(user: dict = Depends(get_current_user)):
    return {"email": user["email"], "name": user.get("name", ""), "anonymous": user.get("anonymous", False)}


# --------------------------------------------------------------------------- #
# Forgot / reset password
# --------------------------------------------------------------------------- #
def _send_reset_email(email: str) -> None:
    """Runs AFTER the response is sent, so the caller can't tell from response
    time whether the address belongs to an account."""
    s = get_settings()
    try:
        user = db.get_user_by_email(email)
        if not user:
            return
        token, token_hash = new_reset_token()
        db.create_reset_token(user["id"], token_hash, s.password_reset_minutes)
        link = f"{s.reset_base_url}/#reset={token}"
        subject, text, body = mailer.reset_email(user["name"], link, s.password_reset_minutes)
        if not mailer.send(user["email"], subject, text, body):
            print("[auth] a password-reset email could not be delivered")
    except Exception as exc:  # noqa: BLE001 — nothing to report to a caller who has already left
        print(f"[auth] password-reset email failed: {type(exc).__name__}")


@router.post("/forgot-password")
def forgot_password(req: ForgotRequest, request: Request, background: BackgroundTasks):
    _require_auth_configured()
    if not mailer.enabled():
        raise HTTPException(503, "Password reset email isn't set up on this server.")
    ip = ratelimit.client_ip(request)
    retry = ratelimit.forgot_ip.retry_after(ip)
    if retry:
        raise ratelimit.too_many(retry, "reset requests")
    ratelimit.forgot_ip.hit(ip)

    reply = {"ok": True, "message": "If an account exists for that email, a reset link is on its way."}
    email = _norm(req.email)
    if not _EMAIL_RE.match(email) or ratelimit.forgot_email.retry_after(email):
        return reply  # same answer either way; never reveal which emails have accounts
    ratelimit.forgot_email.hit(email)
    background.add_task(_send_reset_email, email)
    return reply


@router.post("/reset-password", response_model=AuthResponse)
def reset_password(req: ResetRequest, request: Request):
    _require_auth_configured()
    ip = ratelimit.client_ip(request)
    retry = ratelimit.reset_ip.retry_after(ip)
    if retry:
        raise ratelimit.too_many(retry, "reset attempts")
    ratelimit.reset_ip.hit(ip)

    invalid = HTTPException(400, "This reset link is invalid or has expired. Request a new one.")
    token_hash = hash_reset_token(req.token.strip())
    user_id = db.find_valid_reset(token_hash)
    if user_id is None:
        raise invalid
    user = db.get_user_by_id(user_id)
    if not user:
        raise invalid
    # Check the password BEFORE spending the one-shot token, so a weak choice
    # doesn't force the user to request another email.
    problem = password_problem(req.password, user["email"], user["name"])
    if problem:
        raise HTTPException(400, problem)
    if db.consume_reset_token(token_hash) is None:  # lost a race with another request
        raise invalid
    version = db.set_password(user_id, hash_password(req.password))
    session.invalidate(user_id)
    return _issue(user, version)


# --------------------------------------------------------------------------- #
# Account management (logged in)
# --------------------------------------------------------------------------- #
def _verify_current(user: dict, password: str) -> dict:
    """Load the caller's row and check their password, with a per-user cap on
    wrong guesses (a stolen session token alone shouldn't be enough to guess the
    password or wipe the account). Returns the row."""
    key = f"user:{user['id']}"
    retry = ratelimit.sensitive_fail.retry_after(key)
    if retry:
        raise ratelimit.too_many(retry, "password attempts")
    row = db.get_user_by_id(user["id"])
    if not row:
        raise HTTPException(401, "Session expired. Please log in again.")
    if not verify_password(password, row["password_hash"]):
        ratelimit.sensitive_fail.hit(key)
        # 400, not 401: the frontend treats 401 as "session over" and logs out.
        raise HTTPException(400, "Current password is incorrect.")
    ratelimit.sensitive_fail.clear(key)
    return row


@router.post("/change-password", response_model=AuthResponse)
def change_password(req: ChangePasswordRequest, user: dict = Depends(get_current_user)):
    _require_real_user(user)
    row = _verify_current(user, req.current_password)
    problem = password_problem(req.new_password, row["email"], row["name"])
    if problem:
        raise HTTPException(400, problem)
    if verify_password(req.new_password, row["password_hash"]):
        raise HTTPException(400, "Your new password must be different from the current one.")
    version = db.set_password(row["id"], hash_password(req.new_password))
    session.invalidate(row["id"])
    # Every other device is now signed out; hand this one a fresh token.
    return _issue(row, version)


@router.post("/delete-account")
def delete_account(req: DeleteAccountRequest, user: dict = Depends(get_current_user)):
    """Permanently delete the account, every repo they ingested and all chat
    history. Embedded chunks are removed too unless another user shares them."""
    _require_real_user(user)
    row = _verify_current(user, req.password)

    from app.db import store as pstore
    orphaned = pstore.delete_all_user_data(row["id"])
    if orphaned:
        from app.rag.vector_store import get_store
        store = get_store()
        for repo_key in orphaned:
            try:
                store.delete_repo(repo_key)
            except Exception as exc:  # noqa: BLE001 — leftover chunks only waste space
                print(f"[auth] could not delete chunks for {repo_key}: {type(exc).__name__}")
    db.delete_user(row["id"])
    session.invalidate(row["id"])
    return {"deleted": True}


# --------------------------------------------------------------------------- #
# Gemini key check
# --------------------------------------------------------------------------- #
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
        return {"valid": False, "detail": describe_error(exc)}
    finally:
        reset_request_key(token)
