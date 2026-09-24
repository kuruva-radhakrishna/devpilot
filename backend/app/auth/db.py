"""Postgres-backed user store (psycopg 3).

Short-lived connections per query — fine at demo volume and avoids a pool
dependency. The `users` table is created lazily on first use / at startup.
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
-- (and the existing table, if it already lacks this column) stay valid.
ALTER TABLE users ADD COLUMN IF NOT EXISTS name TEXT NOT NULL DEFAULT '';
"""


def _connect() -> psycopg.Connection:
    return psycopg.connect(get_settings().auth_dsn)


def init_db() -> None:
    """Create the users table if it doesn't exist. Safe to call repeatedly."""
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(_CREATE_USERS)
        conn.commit()


def create_user(email: str, password_hash: str, name: str = "") -> dict[str, Any]:
    """Insert a user, returning its row. Raises psycopg.errors.UniqueViolation
    if the email already exists."""
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO users (email, password_hash, name) VALUES (%s, %s, %s) "
            "RETURNING id, email, name, created_at",
            (email, password_hash, name),
        )
        row = cur.fetchone()
        conn.commit()
    return {"id": row[0], "email": row[1], "name": row[2], "created_at": row[3].isoformat()}


def get_user_by_email(email: str) -> dict[str, Any] | None:
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT id, email, password_hash, name FROM users WHERE email = %s",
            (email,),
        )
        row = cur.fetchone()
    if not row:
        return None
    return {"id": row[0], "email": row[1], "password_hash": row[2], "name": row[3]}
