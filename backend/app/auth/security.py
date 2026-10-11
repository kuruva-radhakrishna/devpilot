"""Password hashing (bcrypt), session tokens (JWT), password policy, reset tokens.

Kept deliberately tiny and dependency-light: `bcrypt` for hashing, `PyJWT` for
signed sessions. No secrets are stored beyond the bcrypt hash and the SHA-256 of
single-use reset tokens.
"""
from __future__ import annotations

import hashlib
import secrets
import time

import bcrypt
import jwt

from app.config import get_settings

_ALGO = "HS256"
# bcrypt hashes at most the first 72 bytes of the input; encode + clamp so long
# passwords don't error out and behave consistently.
_MAX_PW_BYTES = 72


def hash_password(password: str) -> str:
    pw = password.encode("utf-8")[:_MAX_PW_BYTES]
    return bcrypt.hashpw(pw, bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    pw = password.encode("utf-8")[:_MAX_PW_BYTES]
    try:
        return bcrypt.checkpw(pw, password_hash.encode("utf-8"))
    except (ValueError, TypeError):
        return False


def make_token(user_id: int, email: str, name: str = "", token_version: int = 0) -> str:
    s = get_settings()
    now = int(time.time())
    payload = {
        "sub": str(user_id),
        "email": email,
        "name": name,
        "tv": token_version,  # must match users.token_version, or the session is dead
        "iat": now,
        "exp": now + s.jwt_expire_hours * 3600,
    }
    return jwt.encode(payload, s.jwt_secret, algorithm=_ALGO)


def decode_token(token: str) -> dict:
    """Decode + verify a session token. Raises jwt.PyJWTError on any problem."""
    return jwt.decode(token, get_settings().jwt_secret, algorithms=[_ALGO])


# --------------------------------------------------------------------------- #
# Password policy
# --------------------------------------------------------------------------- #
_COMMON = {
    "password", "password1", "password12", "password123", "passw0rd", "12345678",
    "123456789", "1234567890", "11111111", "00000000", "qwerty123", "qwertyuiop",
    "iloveyou", "abc12345", "abcd1234", "letmein1", "welcome1", "admin123",
    "changeme", "football", "baseball", "sunshine", "princess", "monkey123",
}


def password_problem(password: str, email: str = "", name: str = "") -> str | None:
    """Why this password is unacceptable, or None if it's fine. Length is also
    enforced by the request models; this adds the rules they can't express."""
    if len(password) < 8:
        return "Use at least 8 characters."
    if len(set(password)) < 4:
        return "That password is too repetitive. Mix in more different characters."
    low = password.lower()
    if low in _COMMON:
        return "That password is too common. Pick something less guessable."
    local = email.split("@")[0].lower() if email else ""
    if email and (low == email.lower() or (len(local) >= 4 and low == local)):
        return "Your password can't be your email address."
    if name and len(name) >= 4 and low == name.strip().lower():
        return "Your password can't be your name."
    return None


# --------------------------------------------------------------------------- #
# Password-reset tokens
# --------------------------------------------------------------------------- #
def new_reset_token() -> tuple[str, str]:
    """(token to email to the user, hash to store). 256 bits of randomness, so the
    stored hash can't be brute-forced back into a usable token."""
    token = secrets.token_urlsafe(32)
    return token, hash_reset_token(token)


def hash_reset_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()
