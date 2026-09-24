"""Persistent store for repo ownership + chat history (Postgres).

Only used when DATABASE_URL is configured (see config.auth_available) — the
same database that already backs user accounts. Callers fall back entirely to
the ephemeral in-memory registry.py / client-only chat state when it isn't;
see routes_repo.py / routes_agent.py for that branch.

Design note: repo *content* (the embedded chunks) lives in the vector store
(rag/vector_store.py, active when VECTOR_BACKEND=pgvector) keyed by repo_key
alone — shared across users who ingest the same source, so a second user
ingesting a repo someone else already indexed reuses those embeddings instead
of re-spending Gemini quota (see find_matching_repo). Repo *ownership* (what
shows up in "my repos") and chat history are scoped per user_id here.
"""
from __future__ import annotations

from typing import Any

import psycopg

from app.config import get_settings
from app.rag.ingest import default_display_name

# code_chunks is created here too (not just docker-compose's schema.sql),
# since a managed host like Supabase never runs that file automatically.
_SCHEMA = """
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS code_chunks (
    chunk_id    TEXT PRIMARY KEY,
    repo_id     TEXT NOT NULL,
    path        TEXT NOT NULL,
    language    TEXT NOT NULL,
    start_line  INTEGER NOT NULL,
    end_line    INTEGER NOT NULL,
    symbol      TEXT,
    content     TEXT NOT NULL,
    embedding   vector(768) NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_code_chunks_repo ON code_chunks (repo_id);

CREATE TABLE IF NOT EXISTS repos (
    user_id      BIGINT NOT NULL,
    repo_key     TEXT NOT NULL,
    source       TEXT NOT NULL,
    repo_dir     TEXT NOT NULL,
    files        INTEGER NOT NULL DEFAULT 0,
    chunks       INTEGER NOT NULL DEFAULT 0,
    note         TEXT,
    provider     TEXT,
    embedding_model      TEXT,
    embedding_dimensions INTEGER,
    display_name TEXT,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, repo_key)
);
-- Added after the initial launch — IF NOT EXISTS so an existing repos table
-- (rows already present) stays valid; NULL means "not renamed yet", falling
-- back to default_display_name(source) when read.
ALTER TABLE repos ADD COLUMN IF NOT EXISTS display_name TEXT;

CREATE TABLE IF NOT EXISTS messages (
    id          BIGSERIAL PRIMARY KEY,
    user_id     BIGINT NOT NULL,
    repo_key    TEXT NOT NULL,
    role        TEXT NOT NULL,
    content     TEXT NOT NULL,
    tool_names  TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_messages_user_repo ON messages (user_id, repo_key, created_at);
"""


def _connect() -> psycopg.Connection:
    return psycopg.connect(get_settings().auth_dsn)


def init_store() -> None:
    """Create the repos/messages/code_chunks tables (+ pgvector extension) if
    they don't exist yet. Safe to call on every boot."""
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(_SCHEMA)
        conn.commit()


_REPO_COLUMNS = """repo_key, source, repo_dir, files, chunks, note,
                    provider, embedding_model, embedding_dimensions, display_name"""


def _row_to_repo(row) -> dict[str, Any]:
    return {
        "repo_id": row[0], "source": row[1], "repo_dir": row[2],
        "files": row[3], "chunks": row[4], "note": row[5],
        "provider": row[6], "embedding_model": row[7], "embedding_dimensions": row[8],
        # Never NULL from here — the frontend always gets something displayable,
        # whether or not the user has renamed it.
        "display_name": row[9] or default_display_name(row[1]),
    }


def list_user_repos(user_id: int) -> dict[str, dict]:
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(
            f"SELECT {_REPO_COLUMNS} FROM repos WHERE user_id = %s ORDER BY created_at",
            (user_id,),
        )
        rows = cur.fetchall()
    return {r[0]: _row_to_repo(r) for r in rows}


def get_user_repo(user_id: int, repo_key: str) -> dict | None:
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(
            f"SELECT {_REPO_COLUMNS} FROM repos WHERE user_id = %s AND repo_key = %s",
            (user_id, repo_key),
        )
        row = cur.fetchone()
    return _row_to_repo(row) if row else None


def find_matching_repo(repo_key: str, sig: dict) -> dict | None:
    """Any user's row for this exact source + embedding signature — used to
    skip re-embedding a repo someone else already indexed. A signature
    mismatch (different provider/model) must NOT reuse it — different vector
    spaces, meaningless similarity."""
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(
            f"""SELECT {_REPO_COLUMNS} FROM repos
                WHERE repo_key = %s AND provider = %s
                      AND embedding_model = %s AND embedding_dimensions = %s
                LIMIT 1""",
            (repo_key, sig.get("provider"), sig.get("embedding_model"), sig.get("embedding_dimensions")),
        )
        row = cur.fetchone()
    return _row_to_repo(row) if row else None


def upsert_user_repo(user_id: int, repo_key: str, meta: dict) -> None:
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(
            """INSERT INTO repos
                   (user_id, repo_key, source, repo_dir, files, chunks, note,
                    provider, embedding_model, embedding_dimensions, display_name)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
               ON CONFLICT (user_id, repo_key) DO UPDATE SET
                   source = EXCLUDED.source, repo_dir = EXCLUDED.repo_dir,
                   files = EXCLUDED.files, chunks = EXCLUDED.chunks,
                   note = EXCLUDED.note, provider = EXCLUDED.provider,
                   embedding_model = EXCLUDED.embedding_model,
                   embedding_dimensions = EXCLUDED.embedding_dimensions
                   -- display_name is deliberately NOT touched on conflict —
                   -- a re-ingest of an already-owned repo must not clobber a
                   -- name the user already gave it.""",
            (
                user_id, repo_key, meta.get("source", ""), meta.get("repo_dir", ""),
                meta.get("files", 0), meta.get("chunks", 0), meta.get("note"),
                meta.get("provider"), meta.get("embedding_model"), meta.get("embedding_dimensions"),
                None,  # only ever set on first insert; renamed later via rename_user_repo
            ),
        )
        conn.commit()


def rename_user_repo(user_id: int, repo_key: str, display_name: str) -> bool:
    """Set a custom display name for the caller's own copy of a repo. Returns
    False if the caller doesn't own this repo_key (nothing to rename)."""
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE repos SET display_name = %s WHERE user_id = %s AND repo_key = %s",
            (display_name.strip()[:200] or None, user_id, repo_key),
        )
        updated = cur.rowcount > 0
        conn.commit()
    return updated


def delete_user_repo(user_id: int, repo_key: str) -> bool:
    """Remove a user's ownership row + their chat history for a repo. Returns
    True if any *other* user still owns this repo_key — the caller uses that
    to decide whether the underlying embedded chunks (shared across owners)
    are safe to delete too, or still in use."""
    with _connect() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM repos WHERE user_id = %s AND repo_key = %s", (user_id, repo_key))
        cur.execute("DELETE FROM messages WHERE user_id = %s AND repo_key = %s", (user_id, repo_key))
        cur.execute("SELECT 1 FROM repos WHERE repo_key = %s LIMIT 1", (repo_key,))
        other_owner_left = cur.fetchone() is not None
        conn.commit()
    return other_owner_left


def save_message(user_id: int, repo_key: str, role: str, content: str, tool_names: list[str] | None = None) -> None:
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO messages (user_id, repo_key, role, content, tool_names) VALUES (%s,%s,%s,%s,%s)",
            (user_id, repo_key, role, content, ",".join(tool_names) if tool_names else None),
        )
        conn.commit()


def list_messages(user_id: int, repo_key: str) -> list[dict]:
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT role, content, tool_names FROM messages
               WHERE user_id = %s AND repo_key = %s ORDER BY id""",
            (user_id, repo_key),
        )
        rows = cur.fetchall()
    return [
        {"role": r[0], "content": r[1], "tool_names": r[2].split(",") if r[2] else []}
        for r in rows
    ]
