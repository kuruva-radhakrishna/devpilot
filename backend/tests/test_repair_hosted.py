"""Hosted repair: the remote sandbox backend, the job API, safe sample ingestion.

The runner is replaced by tests/mock_runner.py, which unpacks the uploaded archive and
runs real pytest on it — so the repair tests here exercise the whole pipeline
(workspace -> tar -> runner -> results -> repair loop -> job API) with only the model
and the isolation faked.
"""
from __future__ import annotations

import io
import json
import os
import tarfile
import time
import types

import pytest
from fastapi.testclient import TestClient

from app.agent import jobs
from app.agent.repair import RepairIteration, RepairResult
from app.agent.report import repair_markdown, result_to_dict
from app.auth import ratelimit
from app.config import get_settings
from app.rag import ingest
from app.sandbox import runner as sandbox
from mock_runner import MockRunner

TOKEN = "t0ken-for-tests"
EXAMPLES = ingest._EXAMPLES_DIR

PATCH = {
    "root_cause": "get_order_city indexes order['address'], which is absent for that input.",
    "patches": [{
        "file": "orders.py", "function": "get_order_city",
        "new_source": 'def get_order_city(order):\n    addr = order.get("address")\n'
                      '    return addr.get("city", "unknown") if addr else "unknown"',
    }],
    "explanation": "Guard the missing address and default the city.",
}


@pytest.fixture
def fake_runner(monkeypatch):
    runner = MockRunner(token=TOKEN).start()
    monkeypatch.setenv("SANDBOX_BACKEND", "remote")
    monkeypatch.setenv("SANDBOX_URL", runner.url)
    monkeypatch.setenv("SANDBOX_TOKEN", TOKEN)
    monkeypatch.setattr(sandbox, "_runner_awake_until", 0.0)
    get_settings.cache_clear()
    yield runner
    runner.stop()


def _workspace(tmp_path, files: dict[str, str]):
    ws = tmp_path / "ws"
    for name, body in files.items():
        p = ws / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body)
    return str(ws)


PYTEST_CMD = ["python", "-m", "pytest", "-q"]
GOOD_WS = {"a.py": "def f():\n    return 1\n", "tests/test_a.py": "from a import f\ndef test_f():\n    assert f() == 1\n"}


# --------------------------------------------------------------------------- #
# The remote backend (client side)
# --------------------------------------------------------------------------- #
def test_remote_runs_pytest_and_maps_the_result(fake_runner, tmp_path):
    ws = _workspace(tmp_path, GOOD_WS)
    result = sandbox.run_in_sandbox(ws, PYTEST_CMD + ["tests/test_a.py"])
    assert result.ok and result.backend == "remote" and "1 passed" in result.stdout
    (upload,) = fake_runner.uploads()
    assert upload["auth"] == f"Bearer {TOKEN}"
    assert upload["query"]["test_path"] == "tests/test_a.py"


def test_remote_reports_real_failures_as_failures_not_skips(fake_runner, tmp_path):
    ws = _workspace(tmp_path, {"tests/test_a.py": "def test_x():\n    assert False\n"})
    result = sandbox.run_in_sandbox(ws, PYTEST_CMD)
    assert not result.ok and not result.skipped and result.returncode == 1


def test_the_uploaded_archive_has_only_plain_source_files(fake_runner, tmp_path):
    ws = _workspace(tmp_path, {**GOOD_WS, "__pycache__/x.pyc": "junk", ".git/config": "secret",
                               "node_modules/dep/i.js": "x", ".pytest_cache/v": "x"})
    try:
        os.symlink(os.path.join(ws, "a.py"), os.path.join(ws, "link.py"))
    except (OSError, NotImplementedError):
        pass  # symlinks need privileges on Windows; the exclusion is also covered on Linux CI
    sandbox.run_in_sandbox(ws, PYTEST_CMD)
    names = tarfile.open(fileobj=io.BytesIO(fake_runner.uploads()[0]["body"]), mode="r:gz").getnames()
    assert sorted(names) == ["a.py", "tests/test_a.py"]


@pytest.mark.parametrize("status,body,fragment", [
    (401, {"detail": "no"}, "rejected this server's token"),
    (503, {"detail": "All runners are busy. Try again in a moment."}, "busy"),
    (413, {"detail": "Workspace archive is too large."}, "too large"),
])
def test_runner_errors_become_not_executed_never_fake_results(fake_runner, tmp_path, status, body, fragment):
    fake_runner.execute = False
    fake_runner.run_status, fake_runner.run_body = status, body
    if status == 401:
        fake_runner.token = "a-different-token"
    result = sandbox.run_in_sandbox(_workspace(tmp_path, GOOD_WS), PYTEST_CMD)
    assert result.skipped and not result.ok and fragment in result.stdout


def test_an_unreachable_runner_is_reported_not_crashed_on(tmp_path, monkeypatch):
    monkeypatch.setenv("SANDBOX_BACKEND", "remote")
    monkeypatch.setenv("SANDBOX_URL", "http://127.0.0.1:9")  # nothing listens here
    monkeypatch.setenv("SANDBOX_TOKEN", TOKEN)
    monkeypatch.setenv("SANDBOX_WAKE_SECONDS", "1")
    monkeypatch.setattr("app.sandbox.runner.time.sleep", lambda s: None)
    get_settings.cache_clear()
    result = sandbox.run_in_sandbox(_workspace(tmp_path, GOOD_WS), PYTEST_CMD)
    assert result.skipped and "wake up" in result.stdout


def test_a_cold_runner_is_woken_before_the_upload(fake_runner, tmp_path, monkeypatch):
    fake_runner.health_failures = 2
    monkeypatch.setattr("app.sandbox.runner.time.sleep", lambda s: None)
    result = sandbox.run_in_sandbox(_workspace(tmp_path, GOOD_WS), PYTEST_CMD)
    assert result.ok
    assert [r["method"] for r in fake_runner.requests[:3]] == ["GET", "GET", "GET"]


def test_a_runner_that_cannot_isolate_is_not_used(fake_runner, tmp_path):
    fake_runner.ready = False
    result = sandbox.run_in_sandbox(_workspace(tmp_path, GOOD_WS), PYTEST_CMD)
    assert result.skipped and "not ready" in result.stdout and not fake_runner.uploads()


def test_unconfigured_or_unsupported_requests_are_refused_locally(fake_runner, tmp_path, monkeypatch):
    ws = _workspace(tmp_path, GOOD_WS)
    assert "only runs pytest" in sandbox.run_in_sandbox(ws, ["npm", "test"]).stdout
    monkeypatch.setenv("SANDBOX_TOKEN", "")
    get_settings.cache_clear()
    assert "isn't configured" in sandbox.run_in_sandbox(ws, PYTEST_CMD).stdout
    assert not fake_runner.uploads()


def test_oversized_workspaces_are_not_uploaded(fake_runner, tmp_path, monkeypatch):
    monkeypatch.setattr(sandbox, "_REMOTE_MAX_UPLOAD", 200)
    ws = _workspace(tmp_path, {"big.py": os.urandom(5000).hex()})
    assert "too large" in sandbox.run_in_sandbox(ws, PYTEST_CMD).stdout and not fake_runner.uploads()


def test_repair_status_describes_each_backend(monkeypatch):
    def status(**env):
        for k in ("SANDBOX_BACKEND", "SANDBOX_URL", "SANDBOX_TOKEN", "DATABASE_URL", "ALLOW_UNSANDBOXED_LOCAL"):
            monkeypatch.delenv(k, raising=False)
        for k, v in env.items():
            monkeypatch.setenv(k, v)
        get_settings.cache_clear()
        return sandbox.repair_status()

    assert status(SANDBOX_BACKEND="remote", SANDBOX_URL="http://r", SANDBOX_TOKEN="x") == (True, "remote", "")
    assert status(SANDBOX_BACKEND="remote", SANDBOX_URL="http://r")[0] is False
    assert status(SANDBOX_BACKEND="local")[:2] == (True, "local")
    assert status(SANDBOX_BACKEND="local", DATABASE_URL="postgresql://x")[0] is False
    assert status(SANDBOX_BACKEND="disabled")[0] is False
    assert status(SANDBOX_BACKEND="docker")[:2] == (True, "docker")


# --------------------------------------------------------------------------- #
# The repair loop through the remote runner
# --------------------------------------------------------------------------- #
def _fake_model(monkeypatch, replies):
    from app.agent import repair
    calls = {"n": 0}

    def generate(*a, **k):
        reply = replies[min(calls["n"], len(replies) - 1)]
        calls["n"] += 1
        return reply() if callable(reply) else reply

    monkeypatch.setattr(repair, "generate", generate)
    monkeypatch.setattr(repair, "_candidate_files", lambda *a, **k: ["orders.py", "tests/test_orders.py"])
    return calls


def test_repair_goes_red_to_green_through_the_remote_runner(fake_runner, monkeypatch, tmp_path):
    from app.agent.repair import run_repair

    monkeypatch.setenv("WORKSPACE_DIR", str(tmp_path / "wk"))
    get_settings.cache_clear()
    _fake_model(monkeypatch, [json.dumps(PATCH)])
    events: list[dict] = []
    result = run_repair("sample", os.path.join(EXAMPLES, "sample_app"), "missing address crashes",
                        test_path="tests/test_orders.py", on_progress=events.append)

    assert result.success and result.files_changed == ["orders.py"]
    assert "1 failed" in result.tests_before and "0 failed" in result.tests_after
    assert [e["stage"] for e in events] == [
        "baseline", "baseline_done", "retrieval", "retrieval_done",
        "attempt", "patch", "verify", "verify_done"]
    assert len(fake_runner.uploads()) == 2  # baseline run + verification run


def test_a_progress_callback_that_raises_cannot_break_a_repair(fake_runner, monkeypatch, tmp_path):
    from app.agent.repair import run_repair

    monkeypatch.setenv("WORKSPACE_DIR", str(tmp_path / "wk"))
    get_settings.cache_clear()
    _fake_model(monkeypatch, [json.dumps(PATCH)])

    def boom(event):
        raise RuntimeError("ui exploded")

    assert run_repair("sample", os.path.join(EXAMPLES, "sample_app"), "bug in orders",
                      test_path="tests/test_orders.py", on_progress=boom).success


def test_repair_stops_cleanly_when_the_runner_cannot_run_the_tests(fake_runner, monkeypatch, tmp_path):
    from app.agent.repair import run_repair

    monkeypatch.setenv("WORKSPACE_DIR", str(tmp_path / "wk"))
    get_settings.cache_clear()
    fake_runner.execute = False
    fake_runner.run_status, fake_runner.run_body = 503, {"detail": "All runners are busy."}
    calls = _fake_model(monkeypatch, [json.dumps(PATCH)])
    result = run_repair("sample", os.path.join(EXAMPLES, "sample_app"), "bug in orders")
    assert not result.success and result.note == "tests_not_executed" and calls["n"] == 0
    assert "busy" in repair_markdown(result)


# --------------------------------------------------------------------------- #
# Reports
# --------------------------------------------------------------------------- #
def _result(**kw) -> RepairResult:
    base = dict(success=True, root_cause="Missing guard.", explanation="Added a guard.",
                files_changed=["a.py"], diff="--- a/a.py\n+++ b/a.py\n@@ -1 +1 @@\n-x\n+y\n",
                test_output="", iterations=[RepairIteration(1, "r", ["a.py"], "2 passed, 0 failed, 0 errors", True)],
                tests_before="1 passed, 1 failed, 0 errors", tests_after="2 passed, 0 failed, 0 errors")
    base.update(kw)
    return RepairResult(**base)


def test_markdown_for_a_verified_fix_includes_the_diff():
    md = repair_markdown(_result())
    assert md.startswith("**Fix verified.**") and "```diff" in md and "+y" in md
    assert "1 passed, 1 failed" in md and "2 passed, 0 failed" in md and "Missing guard." in md


def test_markdown_for_a_failed_repair_lists_the_attempts():
    md = repair_markdown(_result(success=False, tests_after="1 passed, 1 failed, 0 errors", iterations=[
        RepairIteration(1, "r", [], "patch invalid: bad range", False),
        RepairIteration(2, "r", ["a.py"], "1 passed, 1 failed, 0 errors", False)]))
    assert md.startswith("**No verified fix.**") and "1. patch invalid: bad range" in md and "2. 1 passed" in md


def test_markdown_for_the_other_outcomes():
    assert "could not be run" in repair_markdown(_result(note="tests_not_executed", test_output="runner busy"))
    assert "runner busy" in repair_markdown(_result(note="tests_not_executed", test_output="runner busy"))
    assert "Nothing to fix" in repair_markdown(_result(note="baseline_passing"))
    assert result_to_dict(_result())["markdown"].startswith("**Fix verified.**")


# --------------------------------------------------------------------------- #
# Job registry
# --------------------------------------------------------------------------- #
def _wait(job: jobs.Job, timeout=20):
    end = time.time() + timeout
    while job.status == "running" and time.time() < end:
        time.sleep(0.02)
    return job


def test_a_job_records_progress_and_its_result():
    store = jobs.JobStore()

    def target(job):
        job.add({"stage": "baseline", "message": "go"})
        return {"ok": True}

    job = _wait(store.start("u1", "repo", target))
    snap = job.snapshot()
    assert snap["status"] == "done" and snap["result"] == {"ok": True}
    assert snap["events"][0]["stage"] == "baseline" and "t" in snap["events"][0]


def test_a_crashing_job_becomes_an_error_not_a_hang():
    store = jobs.JobStore()

    def target(job):
        raise RuntimeError("model exploded")

    snap = _wait(store.start("u1", "repo", target)).snapshot()
    assert snap["status"] == "error" and "model exploded" in snap["error"]


def test_job_limits_per_owner_and_overall():
    store = jobs.JobStore()
    gate = __import__("threading").Event()
    slow = lambda job: gate.wait(5) or {}
    first = store.start("u1", "r", slow)
    with pytest.raises(jobs.JobRefused, match="already running"):
        store.start("u1", "r", slow)
    second = store.start("u2", "r", slow)
    with pytest.raises(jobs.JobRefused, match="busy"):
        store.start("u3", "r", slow)
    gate.set()
    _wait(first), _wait(second)
    store.start("u3", "r", lambda job: {})  # capacity is free again


def test_jobs_are_private_to_their_owner():
    store = jobs.JobStore()
    job = _wait(store.start("u1", "r", lambda j: {}))
    assert store.get(job.id, "u1") is job
    assert store.get(job.id, "u2") is None and store.get("nope", "u1") is None


# --------------------------------------------------------------------------- #
# HTTP: start a repair, watch it, fetch the result
# --------------------------------------------------------------------------- #
@pytest.fixture
def api(fake_runner, monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@localhost:5432/x")
    monkeypatch.setenv("WORKSPACE_DIR", str(tmp_path / "wk"))
    monkeypatch.setenv("TRACES_DIR", str(tmp_path / "traces"))
    get_settings.cache_clear()
    monkeypatch.setattr("app.auth.session.get_version", lambda user_id: 0)

    saved: list[tuple] = []
    meta = {"repo_id": "sample-sample_app", "source": "sample:sample_app", "files": 2, "chunks": 2,
            "repo_dir": os.path.join(EXAMPLES, "sample_app"), "display_name": "sample_app (sample)"}
    monkeypatch.setattr("app.db.store.get_user_repo", lambda uid, key: meta if key == meta["repo_id"] else None)
    monkeypatch.setattr("app.db.store.save_message",
                        lambda uid, key, role, content, tools=None: saved.append((uid, role, content, tools)))
    jobs.store.clear()
    ratelimit.reset_all()

    from app.main import app
    return types.SimpleNamespace(client=TestClient(app), saved=saved, meta=meta)


def _auth(user_id=1):
    from app.auth.security import make_token
    return {"Authorization": f"Bearer {make_token(user_id, 'u@example.com', 'U')}"}


def _start(api, user_id=1, **body):
    payload = {"repo_id": api.meta["repo_id"], "bug_report": "Orders without an address crash with a KeyError.",
               "test_path": "tests/test_orders.py", **body}
    return api.client.post("/api/agent/debug", json=payload, headers=_auth(user_id))


def _poll(api, job_id, user_id=1, timeout=60):
    end = time.time() + timeout
    while time.time() < end:
        r = api.client.get(f"/api/agent/debug/{job_id}", headers=_auth(user_id))
        assert r.status_code == 200, r.text
        if r.json()["status"] != "running":
            return r.json()
        time.sleep(0.1)
    raise AssertionError("repair job never finished")


def test_http_repair_runs_in_the_background_and_reports_progress(api, monkeypatch):
    _fake_model(monkeypatch, [json.dumps(PATCH)])
    started = _start(api)
    assert started.status_code == 202 and started.json()["status"] == "running"

    done = _poll(api, started.json()["job_id"])
    assert done["status"] == "done" and done["result"]["success"] is True
    assert done["result"]["markdown"].startswith("**Fix verified.**")
    assert "```diff" in done["result"]["markdown"]
    stages = [e["stage"] for e in done["events"]]
    assert stages[0] == "baseline" and stages[-1] == "verify_done"
    # history: the question first, then the full write-up
    assert [(role) for (_uid, role, _c, _t) in api.saved] == ["user", "assistant"]
    assert api.saved[0][2].startswith("[repair] Orders without an address")
    assert api.saved[1][2] == done["result"]["markdown"] and api.saved[1][3] == ["repair"]


def test_a_second_repair_while_one_runs_is_refused(api, monkeypatch):
    import threading
    gate = threading.Event()
    _fake_model(monkeypatch, [lambda: gate.wait(10) and json.dumps(PATCH)])
    first = _start(api)
    assert first.status_code == 202
    again = _start(api)
    assert again.status_code == 429 and "already running" in again.json()["detail"]
    gate.set()
    _poll(api, first.json()["job_id"])


def test_repairs_are_rate_limited_per_user(api, monkeypatch):
    _fake_model(monkeypatch, [json.dumps(PATCH)])
    for _ in range(6):  # six runs already used this hour
        ratelimit.repair_user.hit("user:1")
    limited = _start(api)
    assert limited.status_code == 429 and "repair runs" in limited.json()["detail"]
    other = _start(api, user_id=2)  # someone else is unaffected
    assert other.status_code == 202
    _poll(api, other.json()["job_id"], user_id=2)


def test_job_status_is_private_to_the_user_who_started_it(api, monkeypatch):
    _fake_model(monkeypatch, [json.dumps(PATCH)])
    job_id = _start(api).json()["job_id"]
    _poll(api, job_id)
    assert api.client.get(f"/api/agent/debug/{job_id}", headers=_auth(2)).status_code == 404
    assert api.client.get(f"/api/agent/debug/{job_id}").status_code == 401


def test_a_model_failure_is_reported_on_the_job_and_in_the_history(api, monkeypatch):
    def broken():
        raise RuntimeError("quota exhausted")

    _fake_model(monkeypatch, [broken])
    done = _poll(api, _start(api).json()["job_id"])
    assert done["status"] == "error" and "quota exhausted" in done["error"]
    assert [r for (_u, r, _c, _t) in api.saved] == ["user", "error"]


def test_bad_repair_requests_are_refused_early(api):
    assert _start(api, bug_report="hi").status_code == 400
    assert _start(api, bug_report="x" * 5000).status_code == 400
    assert _start(api, repo_id="not-mine").status_code == 404


def test_repair_is_refused_when_no_safe_runner_is_configured(api, monkeypatch):
    monkeypatch.setenv("SANDBOX_TOKEN", "")
    get_settings.cache_clear()
    r = _start(api)
    assert r.status_code == 403 and "isn't configured" in r.json()["detail"]
    health = api.client.get("/api/health").json()
    assert health["repair_enabled"] is False and health["repair_runner"] == "remote"
    assert "isn't configured" in health["repair_reason"]
    assert health["repair_wake_url"] == ""  # never advertise a runner we won't use


def test_health_advertises_repair_when_the_runner_is_configured(api):
    health = api.client.get("/api/health").json()
    assert health["repair_enabled"] is True and health["repair_runner"] == "remote"
    assert health["repair_wake_url"].startswith("http://127.0.0.1:")  # lets the browser wake the runner


# --------------------------------------------------------------------------- #
# Safe ingestion
# --------------------------------------------------------------------------- #
def test_bundled_samples_can_be_indexed_anywhere(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@localhost:5432/x")
    get_settings.cache_clear()
    path = ingest.prepare_repo_dir("sample:bank_app", "any-id")
    assert os.path.samefile(path, os.path.join(EXAMPLES, "bank_app"))
    assert ingest.default_display_name("sample:bank_app") == "bank_app (sample)"


@pytest.mark.parametrize("bad", ["sample:../backend", "sample:", "sample:Bank_App", "sample:nope", "sample:a/b"])
def test_sample_names_cannot_escape_the_examples_folder(bad):
    with pytest.raises(ValueError):
        ingest.prepare_repo_dir(bad, "x")


def test_local_paths_are_refused_on_a_hosted_deployment(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@localhost:5432/x")
    get_settings.cache_clear()
    with pytest.raises(ValueError, match="hosted demo"):
        ingest.prepare_repo_dir(str(tmp_path), "x")
    with pytest.raises(ValueError, match="hosted demo"):
        ingest.prepare_repo_dir("/etc", "x")


def test_local_paths_still_work_in_local_dev_or_when_explicitly_allowed(monkeypatch, tmp_path):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    get_settings.cache_clear()
    assert ingest.prepare_repo_dir(str(tmp_path), "x") == os.path.abspath(str(tmp_path))
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@localhost:5432/x")
    monkeypatch.setenv("ALLOW_LOCAL_PATHS", "true")
    get_settings.cache_clear()
    assert ingest.prepare_repo_dir(str(tmp_path), "x") == os.path.abspath(str(tmp_path))
