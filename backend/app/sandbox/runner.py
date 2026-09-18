"""Sandboxed command execution — the isolation layer for running repo tests.

Running a repository's test suite means executing code we don't control, so it
must be isolated. This module runs a command against a *workspace copy* of a
repo with three backends selected by SANDBOX_BACKEND:

  "docker"   -> a container with: no network, memory + CPU + PID limits, a
                wall-clock timeout, a non-root user, and only the workspace
                mounted. This is the real thing and the one to demo/talk about.
  "local"    -> plain subprocess on the host. NOT isolated. Dev convenience only,
                for when Docker isn't available; prints a loud warning.
  "disabled" -> returns a stub (original skeleton behavior).

The design mirrors the vector-store's pluggable backends: one interface, swap by
config. The Code Arena Docker-isolated-compilation experience is the same idea
applied to an LLM agent.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import uuid
from dataclasses import dataclass

from app.config import get_settings


@dataclass
class SandboxResult:
    returncode: int
    stdout: str
    stderr: str
    timed_out: bool
    backend: str

    @property
    def ok(self) -> bool:
        return self.returncode == 0 and not self.timed_out

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
) -> SandboxResult:
    """Run `command` with CWD = workspace_dir under the configured sandbox.

    allow_network is used only for an optional dependency-install phase; the test
    phase always runs with the network disabled.
    """
    s = get_settings()
    backend = s.sandbox_backend.lower()
    timeout = timeout or s.sandbox_timeout

    if backend == "disabled":
        return SandboxResult(
            returncode=0,
            stdout="[sandbox disabled] set SANDBOX_BACKEND=docker (or local) to run tests.",
            stderr="", timed_out=False, backend="disabled",
        )
    if backend == "local":
        return _run_local(workspace_dir, command, timeout)
    return _run_docker(workspace_dir, command, timeout, allow_network)


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
