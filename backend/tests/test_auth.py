"""Account security: lockout, password change/reset, session invalidation, deletion.

The Postgres layer is replaced by an in-memory fake with the same function
signatures, so these tests exercise the real routes, hashing, tokens, rate
limiters and session checks — only the SQL is faked.
"""
from __future__ import annotations

import types

import psycopg
import pytest
from fastapi.testclient import TestClient

from app.auth import db, mailer, ratelimit, session
from app.auth.security import (
    decode_token, hash_password, hash_reset_token, new_reset_token, password_problem,
)
from app.config import get_settings

GOOD_PW = "Tr1cky-horse-battery"


class FakeDB:
    def __init__(self):
        self.users: dict[int, dict] = {}
        self.resets: dict[str, dict] = {}
        self._next = 1

    def create_user(self, email, password_hash, name=""):
        if any(u["email"] == email for u in self.users.values()):
            raise psycopg.errors.UniqueViolation()
        uid, self._next = self._next, self._next + 1
        self.users[uid] = {"id": uid, "email": email, "password_hash": password_hash,
                           "name": name, "token_version": 0}
        return {**self.users[uid], "created_at": "2026-01-01T00:00:00"}

    def get_user_by_email(self, email):
        return next((dict(u) for u in self.users.values() if u["email"] == email), None)

    def get_user_by_id(self, user_id):
        u = self.users.get(user_id)
        return dict(u) if u else None

    def get_token_version(self, user_id):
        u = self.users.get(user_id)
        return u["token_version"] if u else None

    def set_password(self, user_id, password_hash):
        self.users[user_id]["password_hash"] = password_hash
        self.users[user_id]["token_version"] += 1
        return self.users[user_id]["token_version"]

    def delete_user(self, user_id):
        self.users.pop(user_id, None)

    def create_reset_token(self, user_id, token_hash, ttl_minutes):
        self.resets = {h: r for h, r in self.resets.items()
                       if r["user_id"] != user_id or r["used"]}
        self.resets[token_hash] = {"user_id": user_id, "used": False, "expired": False}

    def find_valid_reset(self, token_hash):
        r = self.resets.get(token_hash)
        return r["user_id"] if r and not r["used"] and not r["expired"] else None

    def consume_reset_token(self, token_hash):
        uid = self.find_valid_reset(token_hash)
        if uid is not None:
            self.resets[token_hash]["used"] = True
        return uid


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@localhost:5432/x")
    monkeypatch.setenv("BREVO_API_KEY", "test-brevo-key")
    monkeypatch.setenv("MAIL_FROM", "devpilot@example.com")
    monkeypatch.setenv("FRONTEND_URL", "https://app.example.com/")
    get_settings.cache_clear()

    fake = FakeDB()
    for name in ("create_user", "get_user_by_email", "get_user_by_id", "get_token_version",
                 "set_password", "delete_user", "create_reset_token",
                 "find_valid_reset", "consume_reset_token"):
        monkeypatch.setattr(db, name, getattr(fake, name))

    sent: list[dict] = []
    monkeypatch.setattr(mailer, "send", lambda to, subject, text, html_body=None:
                        sent.append({"to": to, "subject": subject, "text": text}) or True)

    wiped: dict = {"users": [], "chunks": []}
    monkeypatch.setattr("app.db.store.delete_all_user_data",
                        lambda uid: wiped["users"].append(uid) or ["repo-a", "repo-b"])
    fake_store = types.SimpleNamespace(delete_repo=lambda key: wiped["chunks"].append(key))
    monkeypatch.setattr("app.rag.vector_store.get_store", lambda: fake_store)

    session._cache.clear()
    ratelimit.reset_all()

    from app.main import app
    return types.SimpleNamespace(client=TestClient(app), fake=fake, sent=sent, wiped=wiped)


def _register(env, email="ada@example.com", name="Ada Lovelace", password=GOOD_PW, ip="1.1.1.1"):
    return env.client.post("/api/auth/register",
                           json={"email": email, "password": password, "name": name},
                           headers={"x-forwarded-for": ip})


def _login(env, email="ada@example.com", password=GOOD_PW, ip="1.1.1.1"):
    return env.client.post("/api/auth/login", json={"email": email, "password": password},
                           headers={"x-forwarded-for": ip})


def _bearer(token):
    return {"Authorization": f"Bearer {token}"}


# --------------------------------------------------------------------------- #
# Units
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("pw,fragment", [
    ("short", "at least 8"),
    ("aaaaaaaaaa", "repetitive"),
    ("password123", "too common"),
    ("ada@example.com", "email"),
    ("Ada Lovelace", "name"),
])
def test_password_policy_rejects(pw, fragment):
    problem = password_problem(pw, "ada@example.com", "Ada Lovelace")
    assert problem and fragment in problem


def test_password_policy_accepts_a_good_password():
    assert password_problem(GOOD_PW, "ada@example.com", "Ada Lovelace") is None


def test_reset_tokens_are_random_and_only_their_hash_is_kept():
    t1, h1 = new_reset_token()
    t2, h2 = new_reset_token()
    assert t1 != t2 and h1 != h2
    assert hash_reset_token(t1) == h1 and t1 not in h1 and len(t1) >= 40


def test_sliding_window_limiter_expires_old_events(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr("app.auth.ratelimit.time.monotonic", lambda: now[0])
    lim = ratelimit.SlidingWindowLimiter(2, 60)
    lim.hit("k"); lim.hit("k")
    assert lim.retry_after("k") > 0
    now[0] += 61
    assert lim.retry_after("k") == 0


# --------------------------------------------------------------------------- #
# Register / login / lockout
# --------------------------------------------------------------------------- #
def test_register_enforces_password_policy_and_issues_a_versioned_token(env):
    assert _register(env, password="password123").status_code == 400
    r = _register(env)
    assert r.status_code == 200
    claims = decode_token(r.json()["token"])
    assert claims["tv"] == 0 and claims["email"] == "ada@example.com"
    assert _register(env).status_code == 409  # duplicate email


def test_register_is_rate_limited_per_ip(env):
    for i in range(10):
        _register(env, email=f"u{i}@example.com", ip="9.9.9.9")
    assert _register(env, email="late@example.com", ip="9.9.9.9").status_code == 429
    assert _register(env, email="late@example.com", ip="8.8.8.8").status_code == 200


def test_login_locks_out_after_five_failures_from_one_address(env):
    _register(env)
    for _ in range(5):
        assert _login(env, password="wrong-password-1").status_code == 401
    locked = _login(env, password=GOOD_PW)  # even the right password is refused now
    assert locked.status_code == 429 and int(locked.headers["Retry-After"]) > 0
    assert _login(env, ip="2.2.2.2").status_code == 200  # a different address isn't locked


def test_unknown_email_looks_exactly_like_a_wrong_password(env):
    _register(env)
    a = _login(env, email="nobody@example.com")
    b = _login(env, password="wrong-password-1")
    assert a.status_code == b.status_code == 401 and a.json() == b.json()


def test_successful_login_clears_the_failure_count(env):
    _register(env)
    for _ in range(4):
        _login(env, password="wrong-password-1")
    assert _login(env).status_code == 200
    for _ in range(4):
        assert _login(env, password="wrong-password-1").status_code == 401


# --------------------------------------------------------------------------- #
# Change password (and what it does to other sessions)
# --------------------------------------------------------------------------- #
def _change(env, token, current=GOOD_PW, new="An0ther-solid-pass"):
    return env.client.post("/api/auth/change-password", headers=_bearer(token),
                           json={"current_password": current, "new_password": new})


def test_change_password_signs_out_other_sessions_but_not_this_one(env):
    old = _register(env).json()["token"]
    other_device = _login(env, ip="3.3.3.3").json()["token"]
    r = _change(env, old)
    assert r.status_code == 200
    fresh = r.json()["token"]

    assert env.client.get("/api/auth/me", headers=_bearer(old)).status_code == 401
    assert env.client.get("/api/auth/me", headers=_bearer(other_device)).status_code == 401
    assert env.client.get("/api/auth/me", headers=_bearer(fresh)).status_code == 200
    assert _login(env, password="An0ther-solid-pass").status_code == 200
    assert _login(env, password=GOOD_PW, ip="4.4.4.4").status_code == 401


def test_wrong_current_password_is_a_400_not_a_logout(env):
    token = _register(env).json()["token"]
    r = _change(env, token, current="not-my-password")
    assert r.status_code == 400  # a 401 would make the app log the user out
    assert "incorrect" in r.json()["detail"].lower()


def test_change_password_rejects_weak_or_unchanged_passwords(env):
    token = _register(env).json()["token"]
    assert _change(env, token, new="password123").status_code == 400
    assert "different" in _change(env, token, new=GOOD_PW).json()["detail"]


def test_change_password_guessing_is_capped(env):
    token = _register(env).json()["token"]
    for _ in range(5):
        assert _change(env, token, current="guess-guess-1").status_code == 400
    assert _change(env, token, current=GOOD_PW).status_code == 429


# --------------------------------------------------------------------------- #
# Forgot / reset password
# --------------------------------------------------------------------------- #
def _forgot(env, email="ada@example.com", ip="1.1.1.1"):
    return env.client.post("/api/auth/forgot-password", json={"email": email},
                           headers={"x-forwarded-for": ip})


def _emailed_token(env) -> str:
    text = env.sent[-1]["text"]
    return text.split("#reset=")[1].split()[0]


def test_forgot_password_emails_a_one_time_link_in_the_url_fragment(env):
    _register(env)
    r = _forgot(env)
    assert r.status_code == 200 and r.json()["ok"] is True
    assert len(env.sent) == 1 and env.sent[0]["to"] == "ada@example.com"
    assert "https://app.example.com/#reset=" in env.sent[0]["text"]  # fragment: never hits a server log


def test_forgot_password_never_reveals_whether_an_email_has_an_account(env):
    _register(env)
    known = _forgot(env)
    unknown = _forgot(env, email="nobody@example.com", ip="5.5.5.5")
    assert known.status_code == unknown.status_code == 200 and known.json() == unknown.json()
    assert [m["to"] for m in env.sent] == ["ada@example.com"]


def test_forgot_password_is_limited_per_email(env):
    _register(env)
    for i in range(5):
        assert _forgot(env, ip=f"7.7.7.{i}").status_code == 200
    assert len(env.sent) == 3  # 4th and 5th were swallowed silently


def test_forgot_password_reports_when_email_is_not_configured(env, monkeypatch):
    monkeypatch.setenv("BREVO_API_KEY", "")
    get_settings.cache_clear()
    assert _forgot(env).status_code == 503
    assert env.client.get("/api/health").json()["email_enabled"] is False


def _reset(env, token, password="Fresh-and-strong-9"):
    return env.client.post("/api/auth/reset-password", json={"token": token, "password": password})


def test_reset_password_flow_works_once_and_kills_old_sessions(env):
    old = _register(env).json()["token"]
    _forgot(env)
    token = _emailed_token(env)

    r = _reset(env, token)
    assert r.status_code == 200
    assert env.client.get("/api/auth/me", headers=_bearer(r.json()["token"])).status_code == 200
    assert env.client.get("/api/auth/me", headers=_bearer(old)).status_code == 401
    assert _login(env, password="Fresh-and-strong-9").status_code == 200
    assert _reset(env, token).status_code == 400  # single use


def test_weak_new_password_does_not_burn_the_reset_link(env):
    _register(env)
    _forgot(env)
    token = _emailed_token(env)
    assert _reset(env, token, password="password123").status_code == 400
    assert _reset(env, token).status_code == 200


def test_expired_or_bogus_reset_tokens_are_refused(env):
    _register(env)
    _forgot(env)
    token = _emailed_token(env)
    env.fake.resets[hash_reset_token(token)]["expired"] = True
    assert _reset(env, token).status_code == 400
    assert _reset(env, "x" * 43).status_code == 400


def test_a_newer_reset_link_voids_the_older_one(env):
    _register(env)
    _forgot(env)
    first = _emailed_token(env)
    _forgot(env, ip="6.6.6.6")
    second = _emailed_token(env)
    assert first != second
    assert _reset(env, first).status_code == 400
    assert _reset(env, second).status_code == 200


# --------------------------------------------------------------------------- #
# Delete account
# --------------------------------------------------------------------------- #
def test_delete_account_wipes_data_and_ends_every_session(env):
    token = _register(env).json()["token"]
    other = _login(env, ip="3.3.3.3").json()["token"]

    bad = env.client.post("/api/auth/delete-account", headers=_bearer(token),
                          json={"password": "not-my-password"})
    assert bad.status_code == 400 and env.wiped["users"] == []

    ok = env.client.post("/api/auth/delete-account", headers=_bearer(token),
                         json={"password": GOOD_PW})
    assert ok.status_code == 200 and ok.json() == {"deleted": True}
    assert env.wiped["users"] == [1]
    assert env.wiped["chunks"] == ["repo-a", "repo-b"]  # orphaned shared chunks removed too
    assert env.fake.users == {}
    assert env.client.get("/api/auth/me", headers=_bearer(token)).status_code == 401
    assert env.client.get("/api/auth/me", headers=_bearer(other)).status_code == 401
    assert _login(env).status_code == 401


def test_account_endpoints_need_a_real_logged_in_user(env, monkeypatch):
    assert env.client.post("/api/auth/change-password", json={
        "current_password": "x", "new_password": "y" * 9}).status_code == 401
    monkeypatch.delenv("DATABASE_URL")
    get_settings.cache_clear()
    anon = env.client.post("/api/auth/delete-account", json={"password": "x"})
    assert anon.status_code == 400 and "aren't enabled" in anon.json()["detail"]


def test_tokens_from_before_session_versions_existed_still_work(env, monkeypatch):
    """Sessions issued by the previous release have no `tv` claim; they must keep
    working (as version 0) rather than logging every existing user out."""
    import jwt as pyjwt
    uid = _register(env).json()
    claims = decode_token(uid["token"])
    claims.pop("tv")
    legacy = pyjwt.encode(claims, get_settings().jwt_secret, algorithm="HS256")
    assert env.client.get("/api/auth/me", headers=_bearer(legacy)).status_code == 200
