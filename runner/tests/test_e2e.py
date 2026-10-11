"""Attack the runner for real: a hostile repo's tests try to break out.

Runs only when RUNNER_E2E_URL is set (CI builds the Docker image, starts it with
RUNNER_TOKEN=RUNNER_E2E_TOKEN, and points this at it). Each "repo" below is a tiny
pytest suite whose assertions are the SECURITY CLAIM: if the claim is false, the test
inside the sandbox fails and so does the test out here.
"""
from __future__ import annotations

import io
import os
import tarfile
import threading
import time

import httpx
import pytest

URL = os.environ.get("RUNNER_E2E_URL", "").rstrip("/")
TOKEN = os.environ.get("RUNNER_E2E_TOKEN", "")
pytestmark = pytest.mark.skipif(not URL, reason="set RUNNER_E2E_URL to attack a running runner")


def tar(files: dict[str, str]) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for name, body in files.items():
            data = body.encode()
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def run(files: dict[str, str], *, test_path: str = "", timeout: int = 30, token: str | None = TOKEN,
        read_timeout: int = 90):
    headers = {"Authorization": f"Bearer {token}"} if token is not None else {}
    return httpx.post(f"{URL}/run", params={"test_path": test_path, "timeout": timeout},
                      content=tar(files), headers=headers, timeout=read_timeout)


def run_ok(files, **kw) -> dict:
    r = run(files, **kw)
    assert r.status_code == 200, r.text
    return r.json()


def passes(files, **kw) -> dict:
    body = run_ok(files, **kw)
    assert body["returncode"] == 0, body["output"]
    return body


# --------------------------------------------------------------------------- #
def test_health_reports_real_isolation():
    h = httpx.get(f"{URL}/healthz", timeout=30).json()
    assert h["isolation"] is True and h["ready"] is True and h["network_isolated"] is False


def test_requires_the_token():
    assert run({"t/test_a.py": "def test_a(): pass"}, token=None).status_code == 401
    assert run({"t/test_a.py": "def test_a(): pass"}, token="wrong").status_code == 401


def test_runs_a_passing_suite():
    body = passes({"tests/test_ok.py": "def test_ok():\n    assert 1 + 1 == 2\n"})
    assert "1 passed" in body["output"] and body["timed_out"] is False


def test_reports_a_failing_suite():
    body = run_ok({"tests/test_bad.py": "def test_bad():\n    assert 1 == 2\n"})
    assert body["returncode"] == 1 and "1 failed" in body["output"]


def test_runs_only_the_requested_test_path():
    files = {"tests/test_a.py": "def test_a(): pass\n", "tests/test_b.py": "def test_b(): assert False\n"}
    assert "1 passed" in passes(files, test_path="tests/test_a.py")["output"]


def test_source_files_are_importable_by_the_tests():
    files = {"orders.py": "def city(o):\n    return o.get('city', 'unknown')\n",
             "conftest.py": "",
             "tests/test_orders.py": "from orders import city\ndef test_c():\n    assert city({}) == 'unknown'\n"}
    passes(files)


# ---- secrets and privilege ------------------------------------------------- #
def test_the_runner_token_is_unreachable():
    passes({"tests/test_secrets.py": '''
import glob, os
def test_not_in_env():
    assert not any("RUNNER" in k for k in os.environ)
def test_cannot_read_other_processes_environ():
    readable_with_token = []
    for path in glob.glob("/proc/[0-9]*/environ"):
        try:
            data = open(path, "rb").read()
        except OSError:
            continue
        if b"RUNNER_TOKEN" in data:
            readable_with_token.append(path)
    assert readable_with_token == []
def test_is_not_root():
    assert os.getuid() != 0 and os.geteuid() != 0 and os.getuid() >= 20000
def test_cannot_regain_root():
    try:
        os.setuid(0)
    except PermissionError:
        return
    raise AssertionError("setuid(0) succeeded")
'''})


def test_cannot_touch_the_system_or_the_service():
    passes({"tests/test_fs.py": '''
import os
def _denied(path, mode="w"):
    try:
        open(path, mode).close()
    except (PermissionError, FileNotFoundError, IsADirectoryError, OSError):
        return True
    return False
def test_cannot_write_system_files():
    assert _denied("/etc/passwd") and _denied("/etc/evil.conf") and _denied("/usr/local/bin/evil")
def test_cannot_modify_the_service_code():
    assert _denied("/srv/app/sandbox.py") and _denied("/srv/app/evil.py")
def test_cannot_list_other_runs():
    try:
        os.listdir("/tmp/dp-runs")
    except PermissionError:
        return
    raise AssertionError("could list /tmp/dp-runs")
'''})


# ---- containment ----------------------------------------------------------- #
def test_wall_clock_timeout_kills_a_hanging_run():
    started = time.time()
    body = run_ok({"tests/test_hang.py": "import time\ndef test_h():\n    time.sleep(120)\n"}, timeout=5)
    assert body["timed_out"] is True and body["killed_reason"] == "timeout"
    assert time.time() - started < 40


def test_stray_background_processes_are_killed_after_the_run():
    marker = "31337"
    run_ok({"tests/test_daemon.py": f'''
import subprocess
def test_spawn_detached():
    subprocess.Popen(["sleep", "{marker}"], start_new_session=True,
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
'''})
    time.sleep(1)
    passes({"tests/test_look.py": f'''
import glob
def test_no_leftover_sleep():
    for path in glob.glob("/proc/[0-9]*/cmdline"):
        try:
            cmd = open(path, "rb").read()
        except OSError:
            continue
        assert b"sleep\\x00{marker}" not in cmd, path
'''})


def test_a_fork_bomb_is_contained_and_the_runner_survives():
    body = run_ok({"tests/test_fork.py": '''
import os, time
def test_fork_limit():
    children = []
    try:
        for _ in range(400):
            pid = os.fork()
            if pid == 0:
                time.sleep(20)
                os._exit(0)
            children.append(pid)
    except OSError:
        pass
    finally:
        for pid in children:
            try:
                os.kill(pid, 9)
            except OSError:
                pass
    assert len(children) < 100, len(children)
'''}, timeout=30)
    assert body["returncode"] == 0, body["output"]
    assert httpx.get(f"{URL}/healthz", timeout=15).json()["ok"] is True
    passes({"tests/test_after.py": "def test_ok(): pass\n"})


def test_a_memory_bomb_hits_the_limit():
    passes({"tests/test_mem.py": '''
import pytest
def test_cannot_allocate_a_gigabyte():
    with pytest.raises(MemoryError):
        bytearray(1024 * 1024 * 1024)
'''})


def test_a_giant_file_is_refused():
    passes({"tests/test_big.py": '''
import os
def test_file_size_limit():
    try:
        with open("big.bin", "wb") as fh:
            for _ in range(64):
                fh.write(b"x" * (1024 * 1024))
    except OSError:
        return
    assert os.path.getsize("big.bin") <= 20 * 1024 * 1024
'''})


def test_filling_the_disk_gets_killed():
    body = run_ok({"tests/test_disk.py": '''
def test_fill_disk():
    for i in range(400):
        with open(f"chunk{i}.bin", "wb") as fh:
            fh.write(b"x" * (10 * 1024 * 1024))
'''}, timeout=60)
    assert body["killed_reason"] == "disk", body


def test_endless_output_is_capped():
    body = run_ok({"tests/test_noise.py": '''
import sys
def test_noise():
    line = "y" * 1000 + "\\n"
    while True:
        sys.stdout.write(line)
'''}, timeout=30)
    # Contained one of three ways: our output cap, the wall clock, or the kernel's file-size
    # limit stopping pytest's own capture file (which ends the run with no "killed" reason).
    assert body["duration_s"] < 30
    assert len(body["output"]) < 400_000
    assert body["returncode"] != 0


# ---- hostile requests ------------------------------------------------------ #
@pytest.mark.parametrize("bad", ["--rootdir=/", "-p no:x", "../../etc", "/etc/passwd", "a b", "$(id)"])
def test_option_injection_and_traversal_in_test_path_are_refused(bad):
    assert run({"tests/test_ok.py": "def test_ok(): pass"}, test_path=bad).status_code == 400


def _raw_tar(entries):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for info, data in entries:
            tf.addfile(info, io.BytesIO(data) if data is not None else None)
    return buf.getvalue()


def _post_raw(content: bytes):
    return httpx.post(f"{URL}/run", content=content, headers={"Authorization": f"Bearer {TOKEN}"}, timeout=60)


def test_malicious_archives_are_rejected():
    link = tarfile.TarInfo("evil")
    link.type = tarfile.SYMTYPE
    link.linkname = "/etc/passwd"
    assert _post_raw(_raw_tar([(link, None)])).status_code == 400

    esc = tarfile.TarInfo("../../tmp/escape.txt")
    esc.size = 1
    assert _post_raw(_raw_tar([(esc, b"x")])).status_code == 400

    assert _post_raw(b"this is not a tarball").status_code == 400
    assert _post_raw(os.urandom(9 * 1024 * 1024)).status_code == 413


def test_when_every_slot_is_busy_the_runner_says_so_instead_of_queueing_forever():
    slots = httpx.get(f"{URL}/healthz", timeout=15).json()["slots"]
    hang = {"tests/test_hang.py": "import time\ndef test_h():\n    time.sleep(40)\n"}
    results: list = []

    def go():
        results.append(run(hang, timeout=20).status_code)

    threads = [threading.Thread(target=go) for _ in range(slots)]
    for t in threads:
        t.start()
    time.sleep(2)
    started = time.time()
    r = run({"tests/test_ok.py": "def test_ok(): pass"})
    waited = time.time() - started
    for t in threads:
        t.join()
    assert r.status_code == 503 and "busy" in r.text.lower()
    assert 5 < waited < 25
    assert results == [200] * slots
