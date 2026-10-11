"""Postgres-backed user store (psycopg 3).

Short-lived connections per query — fine at demo volume and avoids a pool
dependency. Tables are created lazily on first use / at startup.
"""
from __future__ import annotations

from typing import Any

import psycopg

from app.config import get_settings

_CREATE_USERS = """
CREATE TABLE IF NOT EXISTS users (
    id            BIGSERIAL PRIMARY KEY,
    email         TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
-- Added after the initial launch — IF NOT EXISTS + a default so existing rows
-- (and the existing table, if it already lacks the column) stay valid.
ALTER TABLE users ADD COLUMN IF NOT EXISTS name TEXT NOT NULL DEFAULT '';
-- Bumped whenever the password changes or is reset. Session tokens carry the
-- version they were issued under, so bumping it signs out every other device.
ALTER TABLE users ADD COLUMN IF NOT EXISTS token_version INTEGER NOT NULL DEFAULT 0;

-- Only a SHA-256 of each reset token is stored, never the token itself.
CREATE TABLE IF NOT EXISTS password_resets (
    id          BIGSERIAL PRIMARY KEY,
    user_id     BIGINT NOT NULL,
    token_hash  TEXT UNIQUE NOT NULL,
    expires_at  TIMESTAMPTZ NOT NULL,
    used_at     TIMESTAMPTZ,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_password_resets_user ON password_resets (user_id);
"""


def _connect() -> psycopg.Connection:
    return psycopg.connect(get_settings().auth_dsn)


def init_db() -> None:
    """Create the auth tables if they don't exist. Safe to call repeatedly."""
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(_CREATE_USERS)
        conn.commit()


def _user(row) -> dict[str, Any]:
    return {"id": row[0], "email": row[1], "password_hash": row[2],
            "name": row[3], "token_version": row[4]}


def create_user(email: str, password_hash: str, name: str = "") -> dict[str, Any]:
    """Insert a user, returning its row. Raises psycopg.errors.UniqueViolation
    if the email already exists."""
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO users (email, password_hash, name) VALUES (%s, %s, %s) "
            "RETURNING id, email, name, created_at, token_version",
            (email, password_hash, name),
        )
        row = cur.fetchone()
        conn.commit()
    return {"id": row[0], "email": row[1], "name": row[2],
            "created_at": row[3].isoformat(), "token_version": row[4]}


_USER_COLUMNS = "id, email, password_hash, name, token_version"


def get_user_by_email(email: str) -> dict[str, Any] | None:
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(f"SELECT {_USER_COLUMNS} FROM users WHERE email = %s", (email,))
        row = cur.fetchone()
    return _user(row) if row else None


def get_user_by_id(user_id: int) -> dict[str, Any] | None:
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(f"SELECT {_USER_COLUMNS} FROM users WHERE id = %s", (user_id,))
        row = cur.fetchone()
    return _user(row) if row else None


def get_token_version(user_id: int) -> int | None:
    """Current session version, or None if the account no longer exists."""
    with _connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT token_version FROM users WHERE id = %s", (user_id,))
        row = cur.fetchone()
    return row[0] if row else None


def set_password(user_id: int, password_hash: str) -> int:
    """Store a new password hash and bump the session version (signing out every
    existing session). Returns the new version."""
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE users SET password_hash = %s, token_version = token_version + 1 "
            "WHERE id = %s RETURNING token_version",
            (password_hash, user_id),
        )
        row = cur.fetchone()
        conn.commit()
    if not row:
        raise LookupError("user not found")
    return row[0]


def delete_user(user_id: int) -> None:
    with _connect() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM password_resets WHERE user_id = %s", (user_id,))
        cur.execute("DELETE FROM users WHERE id = %s", (user_id,))
        conn.commit()


def create_reset_token(user_id: int, token_hash: str, ttl_minutes: int) -> None:
    """Store a new reset token, voiding any earlier unused ones for this user."""
    with _connect() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM password_resets WHERE user_id = %s AND used_at IS NULL", (user_id,))
        cur.execute(
            "INSERT INTO password_resets (user_id, token_hash, expires_at) "
            "VALUES (%s, %s, now() + make_interval(mins => %s))",
            (user_id, token_hash, ttl_minutes),
        )
        conn.commit()


def find_valid_reset(token_hash: str) -> int | None:
    """The user a still-usable reset token belongs to, without consuming it —
    lets the caller validate the new password before the one-shot token is spent."""
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT user_id FROM password_resets "
            "WHERE token_hash = %s AND used_at IS NULL AND expires_at > now()",
            (token_hash,),
        )
        row = cur.fetchone()
    return row[0] if row else None


def consume_reset_token(token_hash: str) -> int | None:
    """Atomically mark a valid, unused, unexpired token as used. Returns the
    owning user id, or None. Single UPDATE so two racing requests can't both win."""
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE password_resets SET used_at = now() "
            "WHERE token_hash = %s AND used_at IS NULL AND expires_at > now() "
            "RETURNING user_id",
            (token_hash,),
        )
        row = cur.fetchone()
        conn.commit()
    return row[0] if row else None
