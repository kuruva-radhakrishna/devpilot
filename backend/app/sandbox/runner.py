"""Sandboxed command execution — the isolation layer for running repo tests.

Running a repository's test suite means executing code we don't control, so it
must be isolated. This module runs a command against a *workspace copy* of a
repo with three backends selected by SANDBOX_BACKEND:

  "docker"   -> a container with: no network, memory + CPU + PID limits, a
                wall-clock timeout, a non-root user, and only the workspace
                mounted. This is the real thing and the one to demo/talk about.
  "local"    -> plain subprocess on the host. NOT isolated. Dev convenience only,
                for when Docker isn't available; prints a loud warning.
  "remote"   -> a separate, secret-free runner service (see /runner) that runs
                the repo's tests (pytest, npm test or go test) on an uploaded copy of
                the workspace, after installing its dependencies. This is how the hosted
                demo runs repairs: a PaaS has no Docker daemon, so isolation comes
                from the runner's unprivileged uid + resource limits instead.
  "disabled" -> returns a stub (original skeleton behavior).

The design mirrors the vector-store's pluggable backends: one interface, swap by
config. The Code Arena Docker-isolated-compilation experience is the same idea
applied to an LLM agent.
"""
from __future__ import annotations

import io
import os
import shutil
import subprocess
import tarfile
import threading
import time
import uuid
from dataclasses import dataclass

import httpx

from app.config import get_settings


LOCAL_BLOCKED_MESSAGE = (
    "Test execution is blocked on this hosted deployment: the 'local' sandbox would "
    "run untrusted repository code with no isolation, next to the server's secrets. "
    "Run DevPilot yourself with SANDBOX_BACKEND=docker to use the repair loop."
)


def local_execution_blocked() -> bool:
    """True when the unisolated 'local' backend is configured on a hosted,
    multi-user deployment (DATABASE_URL set) without an explicit opt-in."""
    s = get_settings()
    return (
        s.sandbox_backend.lower() == "local"
        and s.auth_available
        and not s.allow_unsandboxed_local
    )


@dataclass
class SandboxResult:
    returncode: int
    stdout: str
    stderr: str
    timed_out: bool
    backend: str
    # True when no tests actually ran (sandbox disabled/blocked). A skipped run is
    # never "ok" — it must not be mistaken for a passing test suite.
    skipped: bool = False
    # Dependency installation (remote runner only): a one-line description, and whether it
    # failed. A failed install means the tests ran without their packages.
    install_note: str = ""
    install_failed: bool = False

    @property
    def ok(self) -> bool:
        return self.returncode == 0 and not self.timed_out and not self.skipped

    def combined(self, limit: int = 6000) -> str:
        out = (self.stdout or "") + ("\n" + self.stderr if self.stderr else "")
        if self.timed_out:
            out += "\n[sandbox: execution timed out]"
        return out[-limit:]


def docker_available() -> bool:
    if not shutil.which("docker"):
        return False
    try:
        r = subprocess.run(["docker", "info"], capture_output=True, timeout=10)
        return r.returncode == 0
    except Exception:
        return False


def image_exists(image: str) -> bool:
    try:
        r = subprocess.run(
            ["docker", "image", "inspect", image], capture_output=True, timeout=10
        )
        return r.returncode == 0
    except Exception:
        return False


def run_in_sandbox(
    workspace_dir: str,
    command: list[str],
    *,
    timeout: int | None = None,
    allow_network: bool = False,
    lang: str = "python",
    test_path: str = "",
    install: bool = False,
) -> SandboxResult:
    """Run `command` with CWD = workspace_dir under the configured sandbox.

    allow_network is used only for an optional dependency-install phase; the test
    phase always runs with the network disabled.

    `lang`, `test_path` and `install` matter to the remote backend, which is told the
    language and test target rather than a command (it runs one fixed command per
    language) and can install the repo's dependencies first.
    """
    s = get_settings()
    backend = s.sandbox_backend.lower()
    timeout = timeout or s.sandbox_timeout

    if backend == "disabled":
        return SandboxResult(
            returncode=1,
            stdout="[sandbox disabled] set SANDBOX_BACKEND=docker (or local) to run tests.",
            stderr="", timed_out=False, backend="disabled", skipped=True,
        )
    if backend == "local":
        if local_execution_blocked():
            return SandboxResult(
                returncode=1, stdout=LOCAL_BLOCKED_MESSAGE, stderr="",
                timed_out=False, backend="blocked", skipped=True,
            )
        return _run_local(workspace_dir, command, timeout)
    if backend == "remote":
        return _run_remote(workspace_dir, command, timeout, lang=lang, test_path=test_path, install=install)
    if lang != "python":
        return SandboxResult(
            1, f"The Docker sandbox image only runs Python tests. Use SANDBOX_BACKEND=remote "
               f"(the DevPilot runner) for {lang} repositories.", "",
            False, "docker", skipped=True,
        )
    return _run_docker(workspace_dir, command, timeout, allow_network)


def repair_status() -> tuple[bool, str, str]:
    """(available, runner label, reason when unavailable) for the repair feature.

    Repair executes a repo's tests, so it is only offered where that is safe: an
    isolated runner (remote or docker), or the unisolated local backend on a machine
    that isn't a hosted multi-user deployment."""
    s = get_settings()
    backend = s.sandbox_backend.lower()
    if backend == "disabled":
        return False, "off", "Repair is turned off on this server."
    if backend == "local":
        if local_execution_blocked():
            return False, "local", LOCAL_BLOCKED_MESSAGE
        return True, "local", ""
    if backend == "remote":
        if not (s.sandbox_url and s.sandbox_token):
            return False, "remote", "The remote test runner isn't configured on this server."
        return True, "remote", ""
    return True, "docker", ""


# --------------------------------------------------------------------------- #
# Remote backend: an isolated runner service (see /runner)
# --------------------------------------------------------------------------- #
_REMOTE_EXCLUDE_DIRS = {".git", "__pycache__", ".pytest_cache", "node_modules", ".venv", "venv", ".tmp"}
_REMOTE_MAX_UPLOAD = 7 * 1024 * 1024  # a little under the runner's own 8 MB limit
_runner_awake_until = 0.0
_wake_lock = threading.Lock()


def _tar_workspace(workspace_dir: str) -> bytes | None:
    """The workspace as a .tar.gz of plain files (no .git, no symlinks), or None if it
    would be too large to upload."""
    buf = io.BytesIO()
    root = os.path.abspath(workspace_dir)
    with tarfile.open(fileobj=buf, mode="w:gz", compresslevel=6) as tf:
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in _REMOTE_EXCLUDE_DIRS]
            for name in filenames:
                full = os.path.join(dirpath, name)
                if os.path.islink(full) or name.endswith(".pyc"):
                    continue
                tf.add(full, arcname=os.path.relpath(full, root).replace(os.sep, "/"), recursive=False)
        if buf.tell() > _REMOTE_MAX_UPLOAD:
            return None
    data = buf.getvalue()
    return None if len(data) > _REMOTE_MAX_UPLOAD else data


def _wake_runner(base_url: str, wait_seconds: int) -> str | None:
    """Make sure the runner answers /healthz, waiting out a free-tier cold start.
    Returns an error message, or None when it is up and able to isolate code."""
    global _runner_awake_until
    with _wake_lock:
        if time.monotonic() < _runner_awake_until:
            return None
        deadline = time.monotonic() + wait_seconds
        last = "no answer"
        while True:
            try:
                r = httpx.get(f"{base_url}/healthz", timeout=15)
                if r.status_code == 200:
                    info = r.json()
                    if not info.get("ready"):
                        return ("The test runner is up but not ready (it needs a token and the ability "
                                "to isolate code), so it refuses to run tests.")
                    _runner_awake_until = time.monotonic() + 600
                    return None
                last = f"HTTP {r.status_code}"
            except (httpx.HTTPError, ValueError) as exc:
                last = type(exc).__name__
            if time.monotonic() >= deadline:
                return f"The test runner didn't wake up in time ({last}). Try again in a minute."
            time.sleep(3)


_REMOTE_COMMANDS = {"python": ["python", "-m", "pytest"], "node": ["npm", "test"], "go": ["go", "test"]}
_INSTALL_BUDGET = 150   # the runner's own install limit, so our deadline outlasts it
_POLL_EVERY = 1.5


def _install_note(info: dict | None) -> tuple[str, bool]:
    """(one-line description of the dependency install, whether it failed)."""
    if not isinstance(info, dict) or not info.get("ran"):
        return "", False
    summary = str(info.get("summary") or "").strip()
    if info.get("ok", True):
        return summary, False
    failed = info.get("failed") or []
    extra = f" ({', '.join(str(f) for f in failed[:8])})" if failed else ""
    log = str(info.get("log") or "").strip()
    return summary + extra + (f"\n{log[-1200:]}" if log else ""), True


def _run_remote(workspace_dir: str, command: list[str], timeout: int, *, lang: str = "python",
                test_path: str = "", install: bool = False) -> SandboxResult:
    s = get_settings()

    def skipped(message: str) -> SandboxResult:
        # Not executed: reported as such, never as a (fake) test failure or pass.
        return SandboxResult(1, message, "", False, "remote", skipped=True)

    if not (s.sandbox_url and s.sandbox_token):
        return skipped("The remote test runner isn't configured on this server.")
    expected = _REMOTE_COMMANDS.get(lang)
    if expected is None or command[:len(expected)] != expected:
        return skipped("The remote runner only runs pytest, npm test or go test suites.")

    archive = _tar_workspace(workspace_dir)
    if archive is None:
        return skipped("This repository is too large to send to the test runner.")

    base = s.sandbox_url.rstrip("/")
    problem = _wake_runner(base, s.sandbox_wake_seconds)
    if problem:
        return skipped(problem)

    run_timeout = min(int(timeout), 120)
    headers = {"Authorization": f"Bearer {s.sandbox_token}"}
    try:
        r = httpx.post(
            f"{base}/jobs",
            params={"lang": lang, "test_path": test_path, "timeout": run_timeout,
                    "install": "1" if install else "0"},
            content=archive, headers=headers,
            timeout=httpx.Timeout(connect=15, read=60, write=60, pool=15),
        )
    except httpx.HTTPError as exc:
        return skipped(f"Couldn't reach the test runner ({type(exc).__name__}).")

    if r.status_code != 202:
        try:
            detail = str(r.json().get("detail", ""))
        except ValueError:
            detail = ""
        if r.status_code in (401, 403):
            return skipped("The test runner rejected this server's token. Check SANDBOX_TOKEN.")
        if r.status_code == 503:
            return skipped(detail or "The test runner is busy. Try again in a moment.")
        return skipped(f"The test runner refused the run (HTTP {r.status_code}): {detail}".strip())

    try:
        job_id = str(r.json()["job_id"])
    except (ValueError, KeyError, TypeError):
        return skipped("The test runner sent back something unreadable.")

    # Poll. No single request is ever long, so no proxy's idle timeout can cut it off.
    deadline = time.monotonic() + run_timeout + (_INSTALL_BUDGET if install else 0) + 60
    failures = 0
    while time.monotonic() < deadline:
        time.sleep(_POLL_EVERY)
        try:
            p = httpx.get(f"{base}/jobs/{job_id}", headers=headers, timeout=20)
        except httpx.HTTPError:
            failures += 1
            if failures >= 6:
                return skipped("Lost contact with the test runner while the tests were running.")
            continue
        failures = 0
        if p.status_code == 404:
            return skipped("The test runner lost this run (it probably restarted). Try again.")
        if p.status_code != 200:
            return skipped(f"The test runner failed while running the tests (HTTP {p.status_code}).")
        try:
            body = p.json()
            status = body["status"]
        except (ValueError, KeyError, TypeError):
            return skipped("The test runner sent back something unreadable.")
        if status == "running":
            continue
        if status == "error":
            return skipped(str(body.get("error") or "The test runner failed to run this repository."))
        try:
            res = body["result"]
            note, install_failed = _install_note(res.get("install"))
            return SandboxResult(int(res["returncode"]), str(res.get("output", "")), "",
                                 bool(res.get("timed_out")), "remote",
                                 install_note=note, install_failed=install_failed)
        except (KeyError, TypeError, ValueError, AttributeError):
            return skipped("The test runner sent back something unreadable.")
    return skipped("The test runner took too long to answer. Try again in a moment.")


# --------------------------------------------------------------------------- #
# Docker backend
# --------------------------------------------------------------------------- #
def _run_docker(workspace_dir, command, timeout, allow_network) -> SandboxResult:
    s = get_settings()
    if not docker_available():
        return SandboxResult(
            1, "", "Docker is not available. Start Docker Desktop, or set "
            "SANDBOX_BACKEND=local in .env for an (unisolated) dev fallback.",
            False, "docker",
        )
    if not image_exists(s.sandbox_image):
        return SandboxResult(
            1, "",
            f"Sandbox image '{s.sandbox_image}' not found. Build it first:\n"
            f"  docker build -t {s.sandbox_image} backend/app/sandbox",
            False, "docker",
        )

    name = f"devpilot-sbx-{uuid.uuid4().hex[:10]}"
    abs_ws = os.path.abspath(workspace_dir)
    docker_cmd = [
        "docker", "run", "--rm", "--name", name,
        "--memory", s.sandbox_memory,
        "--cpus", s.sandbox_cpus,
        "--pids-limit", str(s.sandbox_pids_limit),
        "--network", "bridge" if allow_network else "none",
        # Mount the workspace copy read-write (it's a throwaway copy, not the source).
        "-v", f"{abs_ws}:/app:rw",
        "-w", "/app",
        s.sandbox_image,
        *command,
    ]
    try:
        r = subprocess.run(
            docker_cmd, capture_output=True, text=True, timeout=timeout + 10
        )
        return SandboxResult(r.returncode, r.stdout, r.stderr, False, "docker")
    except subprocess.TimeoutExpired as e:
        # Kill the container so it doesn't linger past the client timeout.
        subprocess.run(["docker", "rm", "-f", name], capture_output=True)
        return SandboxResult(
            124, e.stdout or "", (e.stderr or "") + "\n[timed out]", True, "docker"
        )


# --------------------------------------------------------------------------- #
# Local backend (NOT isolated — dev only)
# --------------------------------------------------------------------------- #
def _run_local(workspace_dir, command, timeout) -> SandboxResult:
    print(
        "\n[!!] SANDBOX_BACKEND=local — running repo tests directly on the HOST "
        "with no isolation. Use only for repos you trust.\n"
    )
    try:
        r = subprocess.run(
            command, cwd=workspace_dir, capture_output=True, text=True, timeout=timeout
        )
        return SandboxResult(r.returncode, r.stdout, r.stderr, False, "local")
    except subprocess.TimeoutExpired as e:
        return SandboxResult(
            124, e.stdout or "", (e.stderr or "") + "\n[timed out]", True, "local"
        )
    except FileNotFoundError as e:
        return SandboxResult(127, "", str(e), False, "local")
