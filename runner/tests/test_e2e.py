"""Attack the runner for real: a hostile repo's tests try to break out.

Runs only when RUNNER_E2E_URL is set (CI builds the Docker image, starts it with
RUNNER_TOKEN=RUNNER_E2E_TOKEN, and points this at it). Each "repo" below is a tiny
test suite whose assertions are the SECURITY CLAIM: if the claim is false, the test
inside the sandbox fails and so does the test out here.

The runner speaks a jobs API (submit, then poll), so `run()` hides that and hands back
something shaped like a single response.
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


class Reply:
    """Looks like an httpx.Response: the submit response if it was refused, otherwise the
    finished job (200 + result) or the job's error (400 + message)."""

    def __init__(self, status_code: int, body):
        self.status_code = status_code
        self._body = body
        self.text = body if isinstance(body, str) else str(body)

    def json(self):
        return self._body


def _headers(token):
    return {"Authorization": f"Bearer {token}"} if token is not None else {}


def submit_raw(content: bytes, *, lang="python", test_path="", timeout=30, install=False, token=TOKEN):
    return httpx.post(f"{URL}/jobs", params={"lang": lang, "test_path": test_path, "timeout": timeout,
                                              "install": "1" if install else "0"},
                      content=content, headers=_headers(token), timeout=60)


def finish(sub: httpx.Response, *, token=TOKEN, deadline_s: int = 240) -> Reply:
    if sub.status_code != 202:
        return Reply(sub.status_code, sub.text)
    job_id = sub.json()["job_id"]
    end = time.time() + deadline_s
    while time.time() < end:
        r = httpx.get(f"{URL}/jobs/{job_id}", headers=_headers(token), timeout=30)
        assert r.status_code == 200, r.text
        body = r.json()
        if body["status"] == "done":
            return Reply(200, body["result"])
        if body["status"] == "error":
            return Reply(400, body["error"])
        time.sleep(1)
    raise AssertionError("job did not finish in time")


def run(files: dict[str, str], *, lang="python", test_path="", timeout=30, install=False, token=TOKEN) -> Reply:
    return finish(submit_raw(tar(files), lang=lang, test_path=test_path, timeout=timeout, install=install,
                             token=token), token=token)


def run_ok(files, **kw) -> dict:
    r = run(files, **kw)
    assert r.status_code == 200, r.text
    return r.json()


def passes(files, **kw) -> dict:
    body = run_ok(files, **kw)
    # one line, so it survives into the CI annotation (which only keeps a failure's first line)
    assert body["returncode"] == 0, repr(body["output"][-900:]) + " install=" + repr(body.get("install"))[:300]
    return body


def healthy() -> bool:
    return httpx.get(f"{URL}/healthz", timeout=15).json()["ok"] is True


NODE_PKG = '{"name":"t","version":"1.0.0","scripts":{"test":"node --test"}}'
GO_MOD = "module example.com/t\n\ngo 1.22\n"


# --------------------------------------------------------------------------- #
def test_health_reports_real_isolation_and_toolchains():
    h = httpx.get(f"{URL}/healthz", timeout=30).json()
    assert h["isolation"] is True and h["ready"] is True and h["network_isolated"] is False
    assert h["toolchains"] == {"python": True, "node": True, "go": True}


def test_requires_the_token():
    files = {"t/test_a.py": "def test_a(): pass"}
    assert run(files, token=None).status_code == 401
    assert run(files, token="wrong").status_code == 401
    assert httpx.get(f"{URL}/jobs/anything", timeout=15).status_code == 401


def test_runs_a_passing_suite():
    body = passes({"tests/test_ok.py": "def test_ok():\n    assert 1 + 1 == 2\n"})
    assert "1 passed" in body["output"] and body["timed_out"] is False and body["lang"] == "python"


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


# ---- node and go: they run, and a failure is reported as a failure ---------- #
def test_node_runs_a_passing_and_a_failing_suite():
    good = {"package.json": NODE_PKG,
            "sum.js": "exports.sum = (a, b) => a + b;\n",
            "test/sum.test.js": "const t = require('node:test'); const assert = require('node:assert');\n"
                                "const { sum } = require('../sum');\n"
                                "t.test('adds', () => assert.strictEqual(sum(1, 2), 3));\n"}
    body = passes(good, lang="node")
    assert body["lang"] == "node" and "pass 1" in body["output"]
    bad = dict(good, **{"sum.js": "exports.sum = (a, b) => a - b;\n"})
    assert run_ok(bad, lang="node")["returncode"] != 0


def test_go_runs_a_passing_and_a_failing_suite():
    good = {"go.mod": GO_MOD,
            "calc/calc.go": "package calc\n\nfunc Add(a, b int) int { return a + b }\n",
            "calc/calc_test.go": 'package calc\n\nimport "testing"\n\n'
                                 'func TestAdd(t *testing.T) {\n\tif Add(1, 2) != 3 {\n\t\tt.Fatal("bad")\n\t}\n}\n'}
    body = passes(good, lang="go", test_path="./calc", timeout=90)
    assert body["lang"] == "go" and "PASS" in body["output"]
    bad = dict(good, **{"calc/calc.go": "package calc\n\nfunc Add(a, b int) int { return a - b }\n"})
    failed = run_ok(bad, lang="go", timeout=90)
    assert failed["returncode"] != 0 and "FAIL" in failed["output"], repr(failed["output"][-900:])


def test_a_toolchain_the_runner_does_not_have_or_an_unknown_language_is_refused():
    assert run({"a.rb": "1"}, lang="ruby").status_code == 400
    assert run({"a": "1"}, lang="python; id").status_code == 400


# ---- dependency installs ---------------------------------------------------- #
def test_python_dependencies_are_installed_once_and_then_cached():
    files = {"requirements.txt": "six==1.16.0\n",
             "tests/test_six.py": "import six\ndef test_six():\n    assert six.PY3\n"}
    first = passes(files, install=True, timeout=60)
    assert first["install"]["ran"] and first["install"]["ok"], first["install"]
    second = passes(files, install=True, timeout=60)
    assert second["install"]["cached"] is True, second["install"]


def test_python_install_ignores_options_urls_and_vcs_lines():
    files = {"requirements.txt": "six==1.16.0\n--index-url https://evil.example/simple\n-e .\n"
                                 "git+https://example.com/x.git\nhttps://example.com/x.whl\n",
             "setup.py": "open('setup-ran.txt', 'w').write('x')\n",
             "tests/test_clean.py": "import os, six\ndef test_nothing_executed():\n"
                                    "    assert not os.path.exists('setup-ran.txt')\n"}
    body = passes(files, install=True, timeout=60)
    assert body["install"]["ok"], body["install"]


def test_a_missing_python_package_is_reported_not_hidden():
    body = run_ok({"requirements.txt": "this-package-does-not-exist-zzz==9.9.9\n",
                   "tests/test_a.py": "def test_a(): pass\n"}, install=True, timeout=60)
    assert body["install"]["ran"] and body["install"]["ok"] is False and body["install"]["failed"]


def test_node_dependencies_install_without_running_any_scripts():
    files = {"package.json": '{"name":"t","version":"1.0.0","scripts":'
                             '{"postinstall":"node -e \\"require(\'fs\').writeFileSync(\'postinstall.marker\',\'x\')\\"",'
                             '"test":"node --test"},"dependencies":{"left-pad":"1.3.0"}}',
             "test/pad.test.js": "const t = require('node:test'); const assert = require('node:assert');\n"
                                 "const fs = require('node:fs');\nconst pad = require('left-pad');\n"
                                 "t.test('dep works', () => assert.strictEqual(pad('a', 3), '  a'));\n"
                                 "t.test('no install script ran', () => assert.ok(!fs.existsSync('postinstall.marker')));\n"}
    body = passes(files, lang="node", install=True, timeout=90)
    assert body["install"]["ran"] and body["install"]["ok"], body["install"]


def test_go_dependencies_are_downloaded_for_an_offline_test_run():
    files = {"go.mod": "module example.com/t\n\ngo 1.22\n\nrequire github.com/google/uuid v1.6.0\n",
             "go.sum": "github.com/google/uuid v1.6.0 h1:NIvaJDMOsjHA8n1jAhLSgzrAzy1Hgr+hNrb57e+94F0=\n"
                       "github.com/google/uuid v1.6.0/go.mod h1:TIyPZe4MgqvfeYDBFedMoGGpEZXEzp8m6ErZw8vaOm0=\n",
             "id_test.go": 'package t\n\nimport (\n\t"testing"\n\n\t"github.com/google/uuid"\n)\n\n'
                           'func TestID(t *testing.T) {\n\tif uuid.New().String() == "" {\n\t\tt.Fatal("empty")\n\t}\n}\n'}
    body = passes(files, lang="go", install=True, timeout=120)
    assert body["install"]["ran"] and body["install"]["ok"], body["install"]


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


def test_node_and_go_tests_are_unprivileged_and_cannot_see_the_token_either():
    node = {"package.json": NODE_PKG, "test/sec.test.js": '''
const t = require('node:test'); const assert = require('node:assert'); const fs = require('node:fs');
t.test('not root', () => assert.ok(process.getuid() >= 20000));
t.test('no runner env', () => assert.ok(!Object.keys(process.env).some((k) => k.includes('RUNNER'))));
t.test('cannot read other environs', () => {
  for (const p of fs.readdirSync('/proc').filter((n) => /^[0-9]+$/.test(n))) {
    let data = '';
    try { data = fs.readFileSync(`/proc/${p}/environ`).toString('latin1'); } catch (e) { continue; }
    assert.ok(!data.includes('RUNNER_TOKEN'), p);
  }
});
t.test('cannot write system files', () => assert.throws(() => fs.writeFileSync('/etc/evil.conf', 'x')));
'''}
    passes(node, lang="node")
    go = {"go.mod": GO_MOD, "sec_test.go": '''package sec

import (
	"os"
	"strings"
	"testing"
)

func TestUnprivileged(t *testing.T) {
	if os.Getuid() < 20000 {
		t.Fatalf("uid %d", os.Getuid())
	}
	for _, kv := range os.Environ() {
		if strings.Contains(kv, "RUNNER") {
			t.Fatal("runner env visible")
		}
	}
	if err := os.WriteFile("/etc/evil.conf", []byte("x"), 0o644); err == nil {
		t.Fatal("wrote a system file")
	}
}
'''}
    passes(go, lang="go", timeout=90)


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
def test_cannot_modify_the_dependency_cache():
    assert _denied("/tmp/dp-deps/evil.txt")
'''})


# ---- containment ----------------------------------------------------------- #
def test_wall_clock_timeout_kills_a_hanging_run():
    started = time.time()
    body = run_ok({"tests/test_hang.py": "import time\ndef test_h():\n    time.sleep(120)\n"}, timeout=5)
    assert body["timed_out"] is True and body["killed_reason"] == "timeout"
    assert time.time() - started < 40


def test_a_hanging_node_run_is_killed_too():
    body = run_ok({"package.json": NODE_PKG,
                   "test/hang.test.js": "require('node:test').test('h', () => new Promise(() => setInterval(() => {}, 1000)));\n"},
                  lang="node", timeout=5)
    assert body["timed_out"] is True and body["killed_reason"] == "timeout"
    assert healthy()


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
    assert healthy()
    passes({"tests/test_after.py": "def test_ok(): pass\n"})


def test_a_memory_bomb_hits_the_limit():
    passes({"tests/test_mem.py": '''
import pytest
def test_cannot_allocate_a_gigabyte():
    with pytest.raises(MemoryError):
        bytearray(1024 * 1024 * 1024)
'''})


def test_a_node_memory_bomb_is_stopped_and_the_runner_survives():
    started = time.time()
    body = run_ok({"package.json": NODE_PKG, "test/mem.test.js": '''
const t = require('node:test');
t.test('allocate far past the limit', () => {
  const hold = [];
  for (let i = 0; i < 64; i++) hold.push(Buffer.alloc(100 * 1024 * 1024, 1));
});
'''}, lang="node", timeout=30)
    assert body["returncode"] != 0, "a 6 GB allocation must not succeed"
    assert time.time() - started < 60
    assert healthy()
    passes({"package.json": NODE_PKG, "test/ok.test.js": "require('node:test').test('ok', () => {});\n"}, lang="node")


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
@pytest.mark.parametrize("lang,bad", [
    ("python", "--rootdir=/"), ("python", "-p no:x"), ("python", "../../etc"), ("python", "/etc/passwd"),
    ("python", "a b"), ("python", "$(id)"),
    ("node", "--require=/etc/passwd"), ("node", "../x.test.js"),
    ("go", "-run=x"), ("go", "./a;id"), ("go", "../x"),
])
def test_option_injection_and_traversal_in_test_path_are_refused(lang, bad):
    r = run({"tests/test_ok.py": "def test_ok(): pass"}, lang=lang, test_path=bad)
    assert r.status_code == 400, (lang, bad, r.text)


def _raw_tar(entries):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for info, data in entries:
            tf.addfile(info, io.BytesIO(data) if data is not None else None)
    return buf.getvalue()


def test_malicious_archives_are_rejected():
    link = tarfile.TarInfo("evil")
    link.type = tarfile.SYMTYPE
    link.linkname = "/etc/passwd"
    assert finish(submit_raw(_raw_tar([(link, None)]))).status_code == 400

    esc = tarfile.TarInfo("../../tmp/escape.txt")
    esc.size = 1
    assert finish(submit_raw(_raw_tar([(esc, b"x")]))).status_code == 400

    assert finish(submit_raw(b"this is not a tarball")).status_code == 400
    assert submit_raw(os.urandom(9 * 1024 * 1024)).status_code == 413
    assert healthy()


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
