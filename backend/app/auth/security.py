"""Password hashing (bcrypt) and session tokens (JWT).

Kept deliberately tiny and dependency-light: `bcrypt` for hashing, `PyJWT` for
signed, stateless sessions. No secrets are stored beyond the bcrypt hash.
"""
from __future__ import annotations

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


def make_token(user_id: int, email: str) -> str:
    s = get_settings()
    now = int(time.time())
    payload = {
        "sub": str(user_id),
        "email": email,
        "iat": now,
        "exp": now + s.jwt_expire_hours * 3600,
    }
    return jwt.encode(payload, s.jwt_secret, algorithm=_ALGO)


def decode_token(token: str) -> dict:
    """Decode + verify a session token. Raises jwt.PyJWTError on any problem."""
    return jwt.decode(token, get_settings().jwt_secret, algorithms=[_ALGO])
