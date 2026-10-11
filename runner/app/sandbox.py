"""Run a repository's pytest suite in a throwaway, unprivileged, resource-limited process.

Threat model: the uploaded repo is hostile. It can execute arbitrary Python (that is
what a test suite is) as an unprivileged user, so the design assumes it will try to:
read secrets, escape its directory, read other runs' files, outlive its run, fill the
disk or memory, fork-bomb, and print forever. Defences, each exercised by the e2e tests:

  * No secrets live here to steal: the service holds only RUNNER_TOKEN, and the child
    runs under a DIFFERENT uid so it cannot read the parent's /proc/<pid>/environ.
  * Each concurrent run gets its own uid (BASE_UID + slot) and a 0700 directory, so two
    runs cannot read each other's files.
  * Hard limits via prlimit: CPU, address space, processes, file size, open files.
  * A wall-clock timeout, a disk-usage cap and an output cap, any of which kills the
    whole process group, and afterwards every process the uid owns is killed too.
  * Only `python -m pytest` is ever executed: the caller chooses a test path, never a
    command. Archives are unpacked by hand and reject symlinks, devices, absolute
    paths and `..`.

It cannot block outbound network on Render's free tier (no CAP_SYS_ADMIN), and it says
so in every response (`network_isolated: false`) rather than implying otherwise.
"""
from __future__ import annotations

import io
import os
import re
import shutil
import signal
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass
from queue import Empty, Queue

MAX_UPLOAD_BYTES = int(os.environ.get("RUNNER_MAX_UPLOAD_MB", "8")) * 1024 * 1024
MAX_UNPACKED_BYTES = int(os.environ.get("RUNNER_MAX_UNPACKED_MB", "40")) * 1024 * 1024
MAX_FILES = 4000
MAX_DISK_BYTES = int(os.environ.get("RUNNER_MAX_DISK_MB", "100")) * 1024 * 1024
MAX_OUTPUT_BYTES = 100_000
MAX_TIMEOUT = 120
MEM_LIMIT_BYTES = int(os.environ.get("RUNNER_MEM_MB", "400")) * 1024 * 1024
SLOTS = max(1, int(os.environ.get("RUNNER_SLOTS", "2")))
BASE_UID = int(os.environ.get("RUNNER_BASE_UID", "20000"))
RUNS_DIR = os.environ.get("RUNNER_RUNS_DIR", "/tmp/dp-runs")

_TEST_PATH_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_./:\[\]\-]*$")


class RunnerError(Exception):
    """A request the runner refuses; `status` is the HTTP status to answer with."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


# --------------------------------------------------------------------------- #
# Input validation
# --------------------------------------------------------------------------- #
def validate_test_path(test_path: str) -> str:
    """A relative pytest target. Never starts with '-' (option injection) or '/',
    never contains '..'."""
    test_path = (test_path or "").strip()
    if not test_path:
        return ""
    if (len(test_path) > 300 or ".." in test_path.split("/") or ".." in test_path
            or not _TEST_PATH_RE.match(test_path)):
        raise RunnerError("Invalid test path.")
    return test_path


def clamp_timeout(value: float) -> int:
    return int(max(5, min(float(value), MAX_TIMEOUT)))


def safe_extract(data: bytes, dest: str) -> int:
    """Unpack a gzipped tar into `dest` by hand. Returns the file count. Raises
    RunnerError for anything that is not plain files and directories inside dest."""
    if len(data) > MAX_UPLOAD_BYTES:
        raise RunnerError("Workspace archive is too large.", 413)
    dest_real = os.path.realpath(dest)
    files = 0
    total = 0
    try:
        tf = tarfile.open(fileobj=io.BytesIO(data), mode="r:gz")
    except (tarfile.TarError, OSError, EOFError):
        raise RunnerError("That is not a valid .tar.gz archive.")
    with tf:
        for member in tf:
            name = member.name.replace("\\", "/")
            if not (member.isfile() or member.isdir()):
                raise RunnerError(f"Unsupported entry type in archive: {name}")
            if name.startswith("/") or ".." in name.split("/") or "\x00" in name:
                raise RunnerError(f"Unsafe path in archive: {name}")
            target = os.path.realpath(os.path.join(dest_real, name))
            if target != dest_real and not target.startswith(dest_real + os.sep):
                raise RunnerError(f"Unsafe path in archive: {name}")
            if member.isdir():
                os.makedirs(target, exist_ok=True)
                continue
            files += 1
            total += member.size
            if files > MAX_FILES:
                raise RunnerError("Archive contains too many files.", 413)
            if total > MAX_UNPACKED_BYTES:
                raise RunnerError("Archive is too large once unpacked.", 413)
            os.makedirs(os.path.dirname(target), exist_ok=True)
            src = tf.extractfile(member)
            if src is None:
                raise RunnerError(f"Could not read {name} from the archive.")
            with open(target, "wb") as out:
                shutil.copyfileobj(src, out, 1 << 16)
    return files


# --------------------------------------------------------------------------- #
# Isolation
# --------------------------------------------------------------------------- #
def isolation_available() -> bool:
    """True when this process can run children under another uid (needs root).
    Probed for real rather than inferred from geteuid()."""
    if os.name != "posix" or not hasattr(os, "geteuid") or os.geteuid() != 0:
        return False
    try:
        out = subprocess.run(
            ["id", "-u"], user=BASE_UID, group=BASE_UID, extra_groups=[],
            capture_output=True, text=True, timeout=10,
        )
        return out.returncode == 0 and out.stdout.strip() == str(BASE_UID)
    except (OSError, subprocess.SubprocessError, ValueError):
        return False


_slots: "Queue[int]" = Queue()
for _i in range(SLOTS):
    _slots.put(_i)


def acquire_slot(wait: float = 10.0) -> int:
    try:
        return _slots.get(timeout=wait)
    except Empty:
        raise RunnerError("All runners are busy. Try again in a moment.", 503)


def release_slot(slot: int) -> None:
    _slots.put(slot)


def _kill_everything_owned_by(uid: int) -> None:
    """Kill every process the uid owns (daemons that escaped the process group), so a
    later run reusing this uid inherits nothing. Runs as that uid: kill(-1) signals all
    processes the caller may signal, i.e. only its own."""
    try:
        subprocess.run(
            [sys.executable, "-c", "import os,signal; os.kill(-1, signal.SIGKILL)"],
            user=uid, group=uid, extra_groups=[], timeout=10,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    except (OSError, subprocess.SubprocessError):
        pass


def _dir_size(path: str, cap: int) -> int:
    """Bytes used under path, stopping early once it exceeds cap."""
    total = 0
    stack = [path]
    while stack:
        current = stack.pop()
        try:
            with os.scandir(current) as it:
                for entry in it:
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            stack.append(entry.path)
                        else:
                            total += entry.stat(follow_symlinks=False).st_size
                    except OSError:
                        continue
                    if total > cap:
                        return total
        except OSError:
            continue
    return total


def _chown_tree(root: str, uid: int) -> None:
    os.chown(root, uid, uid)
    for dirpath, dirnames, filenames in os.walk(root):
        for name in dirnames + filenames:
            try:
                os.lchown(os.path.join(dirpath, name), uid, uid)
            except OSError:
                pass


@dataclass
class RunResult:
    returncode: int
    output: str
    timed_out: bool
    duration_s: float
    killed_reason: str | None
    network_isolated: bool = False

    def as_dict(self) -> dict:
        return self.__dict__.copy()


def _read_tail(path: str, limit: int) -> str:
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as fh:
            if size > limit:
                fh.seek(size - limit)
                data = b"[... output truncated ...]\n" + fh.read()
            else:
                data = fh.read()
    except OSError:
        return ""
    return data.decode("utf-8", errors="replace")


def run_pytest(archive: bytes, test_path: str, timeout: float, *, isolated: bool) -> RunResult:
    """Unpack the archive and run its pytest suite. `isolated` must be True in any real
    deployment; False exists only so tests can exercise the plumbing without root."""
    test_path = validate_test_path(test_path)
    timeout_s = clamp_timeout(timeout)
    slot = acquire_slot()
    uid = BASE_UID + slot
    run_root = os.path.join(RUNS_DIR, uuid.uuid4().hex)
    ws = os.path.join(run_root, "ws")
    out_dir = tempfile.mkdtemp(prefix="dp-out-")  # root-only; the child writes via an inherited fd
    out_path = os.path.join(out_dir, "output.txt")
    try:
        os.makedirs(ws, exist_ok=True)
        os.chmod(RUNS_DIR, 0o711)
        os.chmod(run_root, 0o711)
        safe_extract(archive, ws)
        os.makedirs(os.path.join(ws, ".tmp"), exist_ok=True)

        if isolated:
            _chown_tree(ws, uid)
            os.chmod(ws, 0o700)

        env = {
            "PATH": "/usr/local/bin:/usr/bin:/bin",
            "HOME": ws,
            "TMPDIR": os.path.join(ws, ".tmp"),
            "LANG": "C.UTF-8",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONUNBUFFERED": "1",
        }
        pytest_cmd = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"]
        if test_path:
            pytest_cmd += ["--", test_path]
        cmd = pytest_cmd
        kwargs: dict = {}
        if isolated:
            cmd = [
                "prlimit", f"--cpu={timeout_s + 5}", f"--as={MEM_LIMIT_BYTES}", "--nproc=64",
                "--fsize=20971520", "--nofile=256", "--core=0", "--", *pytest_cmd,
            ]
            kwargs = {"user": uid, "group": uid, "extra_groups": []}

        started = time.monotonic()
        killed: str | None = None
        with open(out_path, "wb") as out:
            proc = subprocess.Popen(
                cmd, cwd=ws, env=env, stdin=subprocess.DEVNULL,
                stdout=out, stderr=subprocess.STDOUT, start_new_session=True, **kwargs,
            )
            last_disk_check = 0.0
            while proc.poll() is None:
                now = time.monotonic()
                if now - started > timeout_s:
                    killed = "timeout"
                elif now - last_disk_check > 0.25:  # fast writers get little head start
                    last_disk_check = now
                    if _dir_size(ws, MAX_DISK_BYTES) > MAX_DISK_BYTES:
                        killed = "disk"
                    elif os.path.getsize(out_path) > 20 * 1024 * 1024:
                        killed = "output"
                if killed:
                    try:
                        os.killpg(proc.pid, signal.SIGKILL)
                    except (ProcessLookupError, PermissionError):
                        proc.kill()
                    break
                time.sleep(0.1)
            proc.wait()
        if isolated:
            _kill_everything_owned_by(uid)

        text = _read_tail(out_path, MAX_OUTPUT_BYTES)
        if killed == "timeout":
            text += f"\n[runner: killed after {timeout_s}s]"
        elif killed == "disk":
            text += "\n[runner: killed, the run exceeded its disk limit]"
        elif killed == "output":
            text += "\n[runner: killed, the run printed too much output]"
        return RunResult(
            returncode=124 if killed == "timeout" else (1 if killed else proc.returncode),
            output=text, timed_out=killed == "timeout",
            duration_s=round(time.monotonic() - started, 2), killed_reason=killed,
        )
    finally:
        shutil.rmtree(run_root, ignore_errors=True)
        shutil.rmtree(out_dir, ignore_errors=True)
        release_slot(slot)
