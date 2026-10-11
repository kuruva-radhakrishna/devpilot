"""Regression tests for the security/correctness hardening pass.

Run from backend/:  python -m pytest tests -q
"""
from __future__ import annotations

import json
import os

import pytest

from app.agent import tools
from app.agent.tools import RepoContext, set_active_repo
from app.config import get_settings
from app.obs import store
from app.patch.patcher import FilePatch, Workspace, _is_test_path, apply_patch
from app.rag.chunker import iter_source_files
from app.sandbox.runner import local_execution_blocked, run_in_sandbox
from app.sandbox.tests import run_tests_in_workspace


def _symlink_or_skip(target, link):
    try:
        os.symlink(target, link)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks not permitted on this system")


def _dir_link_or_skip(target, link):
    """Directory symlink, or a junction on Windows when symlinks aren't permitted
    (os.walk follows junctions like ordinary folders, so they're a real escape route)."""
    try:
        os.symlink(target, link, target_is_directory=True)
        return
    except (OSError, NotImplementedError):
        pass
    if os.name == "nt":
        import subprocess

        r = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)],
                           capture_output=True)
        if r.returncode == 0:
            return
    pytest.skip("cannot create a directory link on this system")


def _repo_with_dir_leak(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.py").write_text("needle = 'top secret'\n")
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "ok.py").write_text("x = 1\n")
    _dir_link_or_skip(outside, repo / "linkdir")
    return repo


def test_safe_path_rejects_directory_link_escape(tmp_path):
    repo = _repo_with_dir_leak(tmp_path)
    set_active_repo(RepoContext(repo_id="r", repo_dir=str(repo)))
    with pytest.raises(ValueError):
        tools._safe_path("linkdir/secret.py")
    assert "x = 1" in tools.read_file("ok.py")


def test_ingest_does_not_follow_directory_links(tmp_path):
    repo = _repo_with_dir_leak(tmp_path)
    rels = [rel for _abs, rel, _lang in iter_source_files(str(repo))]
    assert rels == ["ok.py"]


def test_find_references_does_not_follow_directory_links(tmp_path):
    repo = _repo_with_dir_leak(tmp_path)
    set_active_repo(RepoContext(repo_id="r", repo_dir=str(repo)))
    assert "No references" in tools.find_references("needle")


def test_patch_cannot_write_through_directory_link(tmp_path):
    repo = _repo_with_dir_leak(tmp_path)
    outside_file = tmp_path / "outside" / "secret.py"
    patch = FilePatch(file="linkdir/secret.py", changes=[], function=None, new_source=None)
    patch.function, patch.new_source = "x", "def x():\n    return 0\n"
    res = apply_patch(Workspace("w", str(repo)), [patch])
    assert not res.applied
    assert "needle" in outside_file.read_text()


def _repo_with_leak(tmp_path):
    outside = tmp_path / "secret.txt"
    outside.write_text("needle top secret")
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "ok.py").write_text("x = 1\n")
    _symlink_or_skip(outside, repo / "leak.py")
    return repo


# --------------------------------------------------------------------------- #
# Symlink escapes
# --------------------------------------------------------------------------- #
def test_safe_path_rejects_symlink_escape(tmp_path):
    repo = _repo_with_leak(tmp_path)
    set_active_repo(RepoContext(repo_id="r", repo_dir=str(repo)))
    assert "x = 1" in tools.read_file("ok.py")
    with pytest.raises(ValueError):
        tools._safe_path("leak.py")


def test_ingest_skips_symlinked_files(tmp_path):
    repo = _repo_with_leak(tmp_path)
    rels = [rel for _abs, rel, _lang in iter_source_files(str(repo))]
    assert rels == ["ok.py"]


def test_find_references_does_not_read_through_symlinks(tmp_path):
    repo = _repo_with_leak(tmp_path)
    set_active_repo(RepoContext(repo_id="r", repo_dir=str(repo)))
    assert "No references" in tools.find_references("needle")


# --------------------------------------------------------------------------- #
# Patches may not edit tests
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("path,expected", [
    ("tests/test_a.py", True),
    ("pkg/tests/helpers.py", True),
    ("test_a.py", True),
    ("a_test.py", True),
    ("conftest.py", True),
    ("web/button.test.ts", True),
    ("orders.py", False),
    ("shopcore/stats.py", False),
    ("latest.py", False),
    ("contest.py", False),
])
def test_is_test_path(path, expected):
    assert _is_test_path(path) is expected


def _fn_patch(file, name):
    return FilePatch(file=file, changes=[], function=name,
                     new_source=f"def {name}():\n    return 2\n")


def test_patch_cannot_modify_test_files(tmp_path):
    (tmp_path / "tests").mkdir()
    test_file = tmp_path / "tests" / "test_a.py"
    test_file.write_text("def test_x():\n    assert False\n")
    res = apply_patch(Workspace("w", str(tmp_path)), [_fn_patch("tests/test_a.py", "test_x")])
    assert not res.applied and "test files" in res.error
    assert "assert False" in test_file.read_text()


def test_patch_can_modify_source_files(tmp_path):
    src = tmp_path / "a.py"
    src.write_text("def f():\n    return 1\n")
    res = apply_patch(Workspace("w", str(tmp_path)), [_fn_patch("a.py", "f")])
    assert res.applied and "return 2" in src.read_text()


# --------------------------------------------------------------------------- #
# Skipped tests are never a pass
# --------------------------------------------------------------------------- #
def test_disabled_sandbox_is_not_a_pass(monkeypatch, tmp_path):
    monkeypatch.setenv("SANDBOX_BACKEND", "disabled")
    result = run_in_sandbox(str(tmp_path), ["python", "-c", "print(1)"])
    assert result.skipped and not result.ok
    report = run_tests_in_workspace(str(tmp_path))
    assert report.skipped and not report.ok and report.errors == 0


def test_repair_stops_before_llm_when_tests_cannot_run(monkeypatch, tmp_path):
    from app.agent import repair

    monkeypatch.setenv("SANDBOX_BACKEND", "disabled")
    monkeypatch.setenv("WORKSPACE_DIR", str(tmp_path / "ws"))
    monkeypatch.setattr(repair, "generate", lambda *a, **k: pytest.fail("LLM was called"))
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "a.py").write_text("def f():\n    return 1\n")
    result = repair.run_repair("r", str(repo), "f is wrong")
    assert not result.success and result.note == "tests_not_executed"


# --------------------------------------------------------------------------- #
# Unisolated execution is refused on a hosted deployment
# --------------------------------------------------------------------------- #
def test_local_backend_blocked_when_hosted(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@localhost:5432/x")
    monkeypatch.setenv("SANDBOX_BACKEND", "local")
    assert local_execution_blocked()
    result = run_in_sandbox(str(tmp_path), ["python", "-c", "print('ran')"])
    assert result.skipped and not result.ok and result.backend == "blocked"
    assert "ran" not in result.stdout.replace("Test execution", "")


def test_local_backend_allowed_with_opt_in_or_local_dev(monkeypatch, tmp_path):
    monkeypatch.setenv("SANDBOX_BACKEND", "local")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    get_settings.cache_clear()
    assert not local_execution_blocked()  # plain local dev: no DATABASE_URL

    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@localhost:5432/x")
    monkeypatch.setenv("ALLOW_UNSANDBOXED_LOCAL", "true")
    get_settings.cache_clear()
    assert not local_execution_blocked()
    result = run_in_sandbox(str(tmp_path), ["python", "-c", "print('ran')"])
    assert result.ok and "ran" in result.stdout


# --------------------------------------------------------------------------- #
# Trace storage: ownership + id validation
# --------------------------------------------------------------------------- #
def _write_trace(d, trace_id, owner=None):
    d.mkdir(exist_ok=True)
    body = {"trace_id": trace_id, "name": "agent.run", "status": "ok",
            "duration_ms": 1.0, "spans": []}
    if owner is not None:
        body["owner"] = owner
    (d / f"{trace_id}.json").write_text(json.dumps(body))


A, B, C = "a" * 16, "b" * 16, "c" * 16


def test_trace_store_scoping(monkeypatch, tmp_path):
    d = tmp_path / "traces"
    monkeypatch.setenv("TRACES_DIR", str(d))
    _write_trace(d, A, owner=1)
    _write_trace(d, B, owner=2)
    _write_trace(d, C)  # no owner (CLI / eval run)

    mine = [t["trace_id"] for t in store.list_traces(10, owner=1, restrict=True)]
    assert mine == [A]
    assert {t["trace_id"] for t in store.list_traces(10)} == {A, B, C}  # CLI sees all
    assert store.get_trace(B, owner=1, restrict=True) is None
    assert store.get_trace(C, owner=1, restrict=True) is None
    assert store.get_trace(A, owner=1, restrict=True)["trace_id"] == A


def test_trace_ids_are_validated(monkeypatch, tmp_path):
    monkeypatch.setenv("TRACES_DIR", str(tmp_path / "traces"))
    assert store.get_trace("../../etc/passwd") is None
    assert store.get_trace("") is None


def test_tag_owner_stamps_trace(monkeypatch, tmp_path):
    d = tmp_path / "traces"
    monkeypatch.setenv("TRACES_DIR", str(d))
    _write_trace(d, A)
    store.tag_owner(A, 7)
    assert json.loads((d / f"{A}.json").read_text())["owner"] == 7
    store.tag_owner(None, 7)  # no-ops, must not raise
    store.tag_owner("not-a-trace-id", 7)


# --------------------------------------------------------------------------- #
# HTTP layer: auth required, scoped, gated
# --------------------------------------------------------------------------- #
@pytest.fixture
def hosted_client(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient

    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@localhost:5432/x")
    monkeypatch.setenv("SANDBOX_BACKEND", "local")
    monkeypatch.setenv("TRACES_DIR", str(tmp_path / "traces"))
    get_settings.cache_clear()
    # Sessions are checked against the account's current version in Postgres;
    # these tests aren't about that, so pretend every account is at version 0.
    monkeypatch.setattr("app.auth.session.get_version", lambda user_id: 0)
    from app.main import app

    return TestClient(app)


def _auth(user_id=1):
    from app.auth.security import make_token

    return {"Authorization": f"Bearer {make_token(user_id, 'u@example.com', 'U')}"}


def test_endpoints_require_login(hosted_client):
    assert hosted_client.get("/api/traces").status_code == 401
    assert hosted_client.get(f"/api/traces/{A}").status_code == 401
    assert hosted_client.post("/api/review/diff", json={"diff": "x"}).status_code == 401
    assert hosted_client.post(
        "/api/agent/debug", json={"repo_id": "r", "bug_report": "b"}
    ).status_code == 401


def test_traces_are_private_per_user(hosted_client, tmp_path):
    d = tmp_path / "traces"
    _write_trace(d, A, owner=1)
    _write_trace(d, B, owner=2)
    listed = hosted_client.get("/api/traces", headers=_auth(1)).json()["traces"]
    assert [t["trace_id"] for t in listed] == [A]
    assert hosted_client.get(f"/api/traces/{A}", headers=_auth(1)).status_code == 200
    assert hosted_client.get(f"/api/traces/{B}", headers=_auth(1)).status_code == 404


def test_debug_refused_when_hosted_with_local_sandbox(hosted_client):
    r = hosted_client.post(
        "/api/agent/debug", json={"repo_id": "r", "bug_report": "b"}, headers=_auth()
    )
    assert r.status_code == 403 and "blocked" in r.json()["detail"]


def test_review_works_when_logged_in_and_caps_diff_size(hosted_client, monkeypatch):
    monkeypatch.setattr("app.api.routes_review.review_diff", lambda diff, v: [])
    ok = hosted_client.post("/api/review/diff", json={"diff": "x"}, headers=_auth())
    assert ok.status_code == 200 and ok.json()["count"] == 0
    too_big = hosted_client.post(
        "/api/review/diff", json={"diff": "x" * 100_001}, headers=_auth()
    )
    assert too_big.status_code == 422
