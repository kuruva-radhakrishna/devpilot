"""The SQL, against a REAL Postgres (with the pgvector extension).

Skipped unless TEST_DATABASE_URL is set — CI provides a throwaway database (see
.github/workflows/backend-tests.yml). Everything else in this suite fakes the
database, so this file is what proves the actual queries and migrations work.

WARNING: it DROPS the app's tables first. Point it only at a disposable database.
"""
from __future__ import annotations

import os
import types

import psycopg
import pytest
from fastapi.testclient import TestClient

from app.config import get_settings

DSN = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DSN, reason="set TEST_DATABASE_URL to run against a real Postgres")

GOOD_PW = "Tr1cky-horse-battery"


def _drop_all():
    with psycopg.connect(DSN) as conn:
        conn.execute("DROP TABLE IF EXISTS password_resets, users, repos, messages, code_chunks CASCADE")
        conn.commit()


def _scalar(sql, *params):
    with psycopg.connect(DSN) as conn:
        return conn.execute(sql, params).fetchone()[0]


@pytest.fixture
def pg(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", DSN)
    get_settings.cache_clear()
    _drop_all()
    from app.auth import db, ratelimit, session
    from app.db import store

    db.init_db()
    store.init_store()
    session._cache.clear()
    ratelimit.reset_all()
    return types.SimpleNamespace(db=db, store=store)


# --------------------------------------------------------------------------- #
# Schema
# --------------------------------------------------------------------------- #
def test_init_is_idempotent(pg):
    pg.db.init_db()
    pg.db.init_db()
    pg.store.init_store()


def test_existing_users_table_is_upgraded_in_place(monkeypatch):
    """The live database already has a users table from before this release."""
    monkeypatch.setenv("DATABASE_URL", DSN)
    get_settings.cache_clear()
    _drop_all()
    with psycopg.connect(DSN) as conn:
        conn.execute("""CREATE TABLE users (id BIGSERIAL PRIMARY KEY, email TEXT UNIQUE NOT NULL,
                        password_hash TEXT NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT now())""")
        conn.execute("INSERT INTO users (email, password_hash) VALUES ('old@example.com', 'x')")
        conn.commit()
    from app.auth import db

    db.init_db()
    user = db.get_user_by_email("old@example.com")
    assert user["name"] == "" and user["token_version"] == 0
    assert db.get_token_version(user["id"]) == 0


# --------------------------------------------------------------------------- #
# Users + sessions
# --------------------------------------------------------------------------- #
def test_user_lifecycle(pg):
    u = pg.db.create_user("ada@example.com", "hash1", "Ada")
    assert u["token_version"] == 0
    with pytest.raises(psycopg.errors.UniqueViolation):
        pg.db.create_user("ada@example.com", "hash2", "Ada Again")

    assert pg.db.get_user_by_email("ada@example.com")["password_hash"] == "hash1"
    assert pg.db.get_user_by_id(u["id"])["name"] == "Ada"
    assert pg.db.get_user_by_id(99999) is None

    assert pg.db.set_password(u["id"], "hash3") == 1
    assert pg.db.set_password(u["id"], "hash4") == 2
    assert pg.db.get_token_version(u["id"]) == 2
    with pytest.raises(LookupError):
        pg.db.set_password(99999, "x")

    pg.db.delete_user(u["id"])
    assert pg.db.get_token_version(u["id"]) is None


# --------------------------------------------------------------------------- #
# Reset tokens
# --------------------------------------------------------------------------- #
def test_reset_token_lifecycle(pg):
    uid = pg.db.create_user("ada@example.com", "h", "Ada")["id"]
    pg.db.create_reset_token(uid, "hash-a", 30)

    assert pg.db.find_valid_reset("hash-a") == uid
    assert pg.db.find_valid_reset("nope") is None
    assert pg.db.consume_reset_token("hash-a") == uid
    assert pg.db.consume_reset_token("hash-a") is None  # single use
    assert pg.db.find_valid_reset("hash-a") is None


def test_expired_reset_token_is_refused(pg):
    uid = pg.db.create_user("ada@example.com", "h", "Ada")["id"]
    pg.db.create_reset_token(uid, "hash-old", 30)
    with psycopg.connect(DSN) as conn:
        conn.execute("UPDATE password_resets SET expires_at = now() - interval '1 minute'")
        conn.commit()
    assert pg.db.find_valid_reset("hash-old") is None
    assert pg.db.consume_reset_token("hash-old") is None


def test_ttl_is_applied_in_minutes(pg):
    uid = pg.db.create_user("ada@example.com", "h", "Ada")["id"]
    pg.db.create_reset_token(uid, "hash-ttl", 30)
    minutes = _scalar("SELECT extract(epoch FROM (expires_at - now())) / 60 FROM password_resets")
    assert 29 < float(minutes) <= 30


def test_new_reset_token_voids_the_previous_unused_one(pg):
    uid = pg.db.create_user("ada@example.com", "h", "Ada")["id"]
    pg.db.create_reset_token(uid, "first", 30)
    pg.db.create_reset_token(uid, "second", 30)
    assert pg.db.find_valid_reset("first") is None
    assert pg.db.find_valid_reset("second") == uid


def test_deleting_a_user_removes_their_reset_tokens(pg):
    uid = pg.db.create_user("ada@example.com", "h", "Ada")["id"]
    pg.db.create_reset_token(uid, "tok", 30)
    pg.db.delete_user(uid)
    assert _scalar("SELECT count(*) FROM password_resets") == 0


# --------------------------------------------------------------------------- #
# Repo store (previously only covered with mocked SQL)
# --------------------------------------------------------------------------- #
def _meta(n=3):
    return {"source": "https://github.com/x/y", "repo_dir": "/tmp/y", "files": n, "chunks": n * 2,
            "provider": "gemini", "embedding_model": "gemini-embedding-001",
            "embedding_dimensions": 768}


def test_repo_store_round_trip(pg):
    s = pg.store
    s.upsert_user_repo(1, "y-abc", _meta())
    s.upsert_user_repo(1, "y-abc", _meta(5))  # re-ingest updates, keeps one row
    repos = s.list_user_repos(1)
    assert list(repos) == ["y-abc"] and repos["y-abc"]["files"] == 5
    assert repos["y-abc"]["display_name"] == "y"  # default derived from the source

    assert s.rename_user_repo(1, "y-abc", "My project") is True
    s.upsert_user_repo(1, "y-abc", _meta(7))  # a re-ingest must not clobber the rename
    assert s.get_user_repo(1, "y-abc")["display_name"] == "My project"
    assert s.rename_user_repo(2, "y-abc", "Not mine") is False

    s.save_message(1, "y-abc", "user", "hello")
    s.save_message(1, "y-abc", "assistant", "hi", ["search_code", "read_file"])
    msgs = s.list_messages(1, "y-abc")
    assert [m["role"] for m in msgs] == ["user", "assistant"]
    assert msgs[1]["tool_names"] == ["search_code", "read_file"]

    sig = {"provider": "gemini", "embedding_model": "gemini-embedding-001", "embedding_dimensions": 768}
    assert s.find_matching_repo("y-abc", sig)["repo_id"] == "y-abc"
    assert s.find_matching_repo("y-abc", {**sig, "embedding_dimensions": 512}) is None


def test_delete_all_user_data_reports_only_unshared_repos(pg):
    s = pg.store
    s.upsert_user_repo(1, "solo", _meta())
    s.upsert_user_repo(1, "shared", _meta())
    s.upsert_user_repo(2, "shared", _meta())
    s.save_message(1, "solo", "user", "a")
    s.save_message(1, "shared", "user", "b")
    s.save_message(2, "shared", "user", "c")

    orphaned = s.delete_all_user_data(1)

    assert orphaned == ["solo"]  # "shared" still belongs to user 2
    assert s.list_user_repos(1) == {}
    assert list(s.list_user_repos(2)) == ["shared"]
    assert s.list_messages(1, "solo") == [] and len(s.list_messages(2, "shared")) == 1


# --------------------------------------------------------------------------- #
# The whole account lifecycle over HTTP, on the real database
# --------------------------------------------------------------------------- #
def test_full_account_lifecycle(pg, monkeypatch):
    monkeypatch.setenv("BREVO_API_KEY", "k")
    monkeypatch.setenv("MAIL_FROM", "devpilot@example.com")
    monkeypatch.setenv("FRONTEND_URL", "https://app.example.com")
    get_settings.cache_clear()
    from app.auth import mailer

    sent: list[str] = []
    monkeypatch.setattr(mailer, "send", lambda to, subject, text, html_body=None: sent.append(text) or True)
    removed: list[str] = []
    monkeypatch.setattr("app.rag.vector_store.get_store",
                        lambda: types.SimpleNamespace(delete_repo=removed.append))
    from app.main import app

    c = TestClient(app)
    reg = c.post("/api/auth/register", json={"email": "ada@example.com", "password": GOOD_PW, "name": "Ada"})
    assert reg.status_code == 200
    auth = {"Authorization": f"Bearer {reg.json()['token']}"}
    uid = pg.db.get_user_by_email("ada@example.com")["id"]
    pg.store.upsert_user_repo(uid, "proj", _meta())
    pg.store.save_message(uid, "proj", "user", "hi")

    assert c.get("/api/repos", headers=auth).status_code == 200

    changed = c.post("/api/auth/change-password", headers=auth,
                     json={"current_password": GOOD_PW, "new_password": "Second-solid-pass-2"})
    assert changed.status_code == 200
    assert c.get("/api/repos", headers=auth).status_code == 401  # old session is dead
    auth = {"Authorization": f"Bearer {changed.json()['token']}"}
    assert c.get("/api/repos", headers=auth).status_code == 200

    assert c.post("/api/auth/forgot-password", json={"email": "ada@example.com"}).status_code == 200
    token = sent[-1].split("#reset=")[1].split()[0]
    reset = c.post("/api/auth/reset-password", json={"token": token, "password": "Third-solid-pass-3"})
    assert reset.status_code == 200
    assert c.get("/api/repos", headers=auth).status_code == 401
    auth = {"Authorization": f"Bearer {reset.json()['token']}"}
    assert c.post("/api/auth/reset-password", json={"token": token, "password": "Fourth-pass-4"}).status_code == 400

    gone = c.post("/api/auth/delete-account", headers=auth, json={"password": "Third-solid-pass-3"})
    assert gone.status_code == 200
    assert removed == ["proj"]
    assert _scalar("SELECT count(*) FROM users") == 0
    assert _scalar("SELECT count(*) FROM repos") == 0
    assert _scalar("SELECT count(*) FROM messages") == 0
    assert _scalar("SELECT count(*) FROM password_resets") == 0
    assert c.get("/api/repos", headers=auth).status_code == 401
