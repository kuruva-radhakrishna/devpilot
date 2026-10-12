"""Run a repository's tests (Python, Node or Go) in a throwaway, unprivileged,
resource-limited process, after optionally installing its dependencies.

Threat model: the uploaded repo is hostile. It can execute arbitrary code (that is what a
test suite is) as an unprivileged user, so the design assumes it will try to: read secrets,
escape its directory, read other runs' files, outlive its run, fill the disk or memory,
fork-bomb, and print forever. Defences, each exercised by the e2e tests:

  * No secrets live here to steal: the service holds only RUNNER_TOKEN, and the child runs
    under a DIFFERENT uid so it cannot read the parent's /proc/<pid>/environ.
  * Each concurrent run gets its own uid (BASE_UID + slot) and a 0700 directory, so two runs
    cannot read each other's files.
  * Hard limits via prlimit: CPU, memory, processes, file size, open files.
  * A wall-clock timeout, a disk-usage cap and an output cap, any of which kills the whole
    process group, and afterwards every process the uid owns is killed too.
  * The caller picks a language and a test path, never a command. Exactly one fixed command
    per language is ever run: `python -m pytest`, `npm test`, or `go test`. Archives are
    unpacked by hand and reject symlinks, devices, absolute paths and `..`.
  * Dependency installs (see deps.py) run before the tests, as the same unprivileged uid,
    with install scripts and build steps disabled, into a read-only shared cache.

It cannot block outbound network on Render's free tier (no CAP_SYS_ADMIN), and it says so in
every response (`network_isolated: false`) rather than implying otherwise.
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
import time
import uuid
from queue import Empty, Queue
from typing import Callable

from app import deps as deps_mod

LANGS = ("python", "node", "go")

MAX_UPLOAD_BYTES = int(os.environ.get("RUNNER_MAX_UPLOAD_MB", "8")) * 1024 * 1024
MAX_UNPACKED_BYTES = int(os.environ.get("RUNNER_MAX_UNPACKED_MB", "40")) * 1024 * 1024
MAX_FILES = 4000
MAX_DISK_BYTES = int(os.environ.get("RUNNER_MAX_DISK_MB", "100")) * 1024 * 1024
MAX_OUTPUT_BYTES = 100_000
MAX_TIMEOUT = 120
MEM_LIMIT_BYTES = int(os.environ.get("RUNNER_MEM_MB", "400")) * 1024 * 1024
NODE_MEM_BYTES = int(os.environ.get("RUNNER_NODE_MEM_MB", "700")) * 1024 * 1024
GO_MEM_BYTES = int(os.environ.get("RUNNER_GO_MEM_MB", "800")) * 1024 * 1024
SLOTS = max(1, int(os.environ.get("RUNNER_SLOTS", "2")))
BASE_UID = int(os.environ.get("RUNNER_BASE_UID", "20000"))
RUNS_DIR = os.environ.get("RUNNER_RUNS_DIR", "/tmp/dp-runs")
# A Go build cache with the standard library already compiled (built into the image). Without
# it every run recompiles the stdlib, which is slow on a small host and fills the disk cap.
GO_CACHE_SEED = os.environ.get("RUNNER_GO_CACHE_SEED", "/opt/gocache-seed")

_TEST_PATH_RE = re.compile(r"^[A-Za-z0-9_@][A-Za-z0-9_./:\[\]@+\-]*$")
_GO_PKG_RE = re.compile(r"^\./[A-Za-z0-9_./\-]*(\.\.\.)?$")


class RunnerError(Exception):
    """A request the runner refuses; `status` is the HTTP status to answer with."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


# --------------------------------------------------------------------------- #
# Input validation
# --------------------------------------------------------------------------- #
def validate_lang(lang: str) -> str:
    lang = (lang or "python").strip().lower()
    if lang not in LANGS:
        raise RunnerError(f"Unsupported language '{lang}'. Supported: {', '.join(LANGS)}.")
    return lang


def validate_test_path(lang: str, test_path: str) -> str:
    """A relative test target. Never starts with '-' (option injection) or '/', never
    contains '..'. For Go it must be a package pattern like ./calc or ./...; a bare
    directory name is normalised to ./name."""
    test_path = (test_path or "").strip()
    if not test_path:
        return ""
    if len(test_path) > 300 or ".." in test_path.split("/") or "\\" in test_path:
        raise RunnerError("Invalid test path.")
    if lang == "go":
        if test_path.startswith(("-", "/")):
            raise RunnerError("Invalid test path.")
        if not test_path.startswith("./") and test_path != ".":
            test_path = "./" + test_path
        if test_path == "." or not _GO_PKG_RE.match(test_path):
            raise RunnerError("Invalid test path.")
        return test_path
    if not _TEST_PATH_RE.match(test_path):
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


def toolchains() -> dict:
    """Which language toolchains this image actually has (reported by /healthz)."""
    return {"python": True, "node": shutil.which("node") is not None, "go": shutil.which("go") is not None}


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
    """Kill every process the uid owns (daemons that escaped the process group), so a later
    run reusing this uid inherits nothing. Runs as that uid: kill(-1) signals all processes
    the caller may signal, i.e. only its own."""
    try:
        subprocess.run(
            [sys.executable, "-c", "import os,signal; os.kill(-1, signal.SIGKILL)"],
            user=uid, group=uid, extra_groups=[], timeout=10,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    except (OSError, subprocess.SubprocessError):
        pass


def _dir_size(path: str, cap: int) -> int:
    """Bytes used under path, stopping early once it exceeds cap. Symlinks are never
    followed (a cached dependency tree linked into the workspace isn't counted)."""
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


_seed_bytes: int | None = None


def _go_cache_seed_bytes() -> int:
    global _seed_bytes
    if _seed_bytes is None:
        _seed_bytes = _dir_size(GO_CACHE_SEED, 10**12) if os.path.isdir(GO_CACHE_SEED) else 0
    return _seed_bytes


def _prepare_go_cache(run_root: str, uid: int, isolated: bool) -> str:
    """A private, writable copy of the pre-warmed Go build cache for this run ("" when the
    image has none). It lives beside the workspace, not in it, and is owned by the run's uid."""
    if not os.path.isdir(GO_CACHE_SEED):
        return ""
    dest = os.path.join(run_root, "gocache")
    shutil.copytree(GO_CACHE_SEED, dest, symlinks=True)
    if isolated:
        _chown_tree(dest, uid)
    return dest


def _link_dependencies(target: str, link: str) -> None:
    """Expose a cached, read-only dependency tree inside the workspace. `node_modules` becomes
    a real directory of per-package symlinks rather than one symlink, because tools such as
    vitest and babel write caches into `node_modules/.vite` or `.cache` and would otherwise
    fail on the read-only cache. Packages still resolve to the cache by real path."""
    if os.path.lexists(link):
        return
    if os.path.basename(link) != "node_modules":
        os.symlink(target, link)
        return
    os.makedirs(link)
    for name in os.listdir(target):
        os.symlink(os.path.join(target, name), os.path.join(link, name))


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


# --------------------------------------------------------------------------- #
# Per-language commands, environments and limits
# --------------------------------------------------------------------------- #
def test_command(lang: str, test_path: str) -> list[str]:
    if lang == "node":
        return ["npm", "test", "--silent", *(["--", test_path] if test_path else [])]
    if lang == "go":
        return ["go", "test", "-v", "-count=1", test_path or "./..."]
    return [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
            *(["--", test_path] if test_path else [])]


def limit_args(lang: str, phase: str, timeout_s: int) -> list[str]:
    """prlimit arguments. Node and Go reserve huge virtual address ranges up front, so an
    address-space limit would break them; they are limited by data segment instead."""
    cpu = f"--cpu={timeout_s + 10}"
    install = phase == "install"
    if lang == "python":
        return [cpu, f"--as={1024 * 1024 * 1024 if install else MEM_LIMIT_BYTES}", "--nproc=64",
                f"--fsize={157286400 if install else 20971520}", "--nofile=512", "--core=0"]
    mem = (943718400 if install else (NODE_MEM_BYTES if lang == "node" else GO_MEM_BYTES))
    return [cpu, f"--data={mem}", "--nproc=128", f"--fsize={157286400 if install else 104857600}",
            "--nofile=1024", "--core=0"]


_BASE_PATH = "/usr/local/bin:/usr/local/go/bin:/usr/bin:/bin"


def test_env(lang: str, ws: str, deps: "deps_mod.DepsResult", gocache: str = "") -> dict:
    env = {
        "PATH": _BASE_PATH, "HOME": ws, "TMPDIR": os.path.join(ws, ".tmp"),
        "LANG": "C.UTF-8", "CI": "true", "NO_COLOR": "1",
    }
    if lang == "python":
        env.update({"PYTHONDONTWRITEBYTECODE": "1", "PYTHONUNBUFFERED": "1"})
        paths = [p for p in (deps.env.get("PYTHONPATH"),
                             os.path.join(ws, "src") if os.path.isdir(os.path.join(ws, "src")) else "") if p]
        if paths:
            env["PYTHONPATH"] = os.pathsep.join(paths)
    elif lang == "node":
        env.update({"NODE_ENV": "test", "FORCE_COLOR": "0", "npm_config_cache": os.path.join(ws, ".npm"),
                    "npm_config_update_notifier": "false", "npm_config_fund": "false",
                    "NODE_OPTIONS": f"--max-old-space-size={max(128, NODE_MEM_BYTES // (1024 * 1024) // 2)}"})
    else:
        env.update({
            "GOCACHE": gocache or os.path.join(ws, ".gocache"), "GOPATH": os.path.join(ws, ".gopath"),
            "GOMODCACHE": deps.env.get("GOMODCACHE", os.path.join(ws, ".gopath", "pkg", "mod")),
            "GOFLAGS": "-buildvcs=false -p=1", "GOPROXY": "off", "GOSUMDB": "off", "GOTOOLCHAIN": "local",
            "CGO_ENABLED": "0", "GOMAXPROCS": "2", "GOMEMLIMIT": f"{GO_MEM_BYTES // (1024 * 1024) // 2}MiB",
        })
    return env


# --------------------------------------------------------------------------- #
# Supervised execution (one implementation for installs and for tests)
# --------------------------------------------------------------------------- #
def _supervise(cmd: list[str], *, cwd: str, env: dict, timeout: int, out_path: str, watch_dir: str,
               max_disk: int, phase: str, uid: int, isolated: bool, lang: str) -> tuple[int, str | None, float]:
    """Run `cmd` to completion (or until a limit trips). Returns (returncode, killed_reason,
    seconds). Output goes to `out_path`; the child writes through an inherited descriptor, so
    its file-size limit applies and it cannot touch the file itself."""
    kwargs: dict = {}
    if isolated:
        cmd = ["prlimit", *limit_args(lang, phase, timeout), "--", *cmd]
        kwargs = {"user": uid, "group": uid, "extra_groups": []}
    started = time.monotonic()
    killed: str | None = None
    with open(out_path, "wb") as out:
        proc = subprocess.Popen(
            cmd, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
            stdout=out, stderr=subprocess.STDOUT, start_new_session=True, **kwargs,
        )
        last_disk_check = 0.0
        while proc.poll() is None:
            now = time.monotonic()
            if now - started > timeout:
                killed = "timeout"
            elif now - last_disk_check > 0.25:  # fast writers get little head start
                last_disk_check = now
                if _dir_size(watch_dir, max_disk) > max_disk:
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
    return proc.returncode, killed, time.monotonic() - started


def run_job(*, slot: int, archive: bytes, lang: str, test_path: str, timeout: float, install: bool,
            isolated: bool, set_stage: Callable[[str], None] = lambda _s: None) -> dict:
    """Unpack the archive, optionally install its dependencies, run its test suite, and
    return the result as a plain dict. The caller has already acquired `slot` and validated
    `lang`/`test_path`; the slot is released here."""
    uid = BASE_UID + slot
    timeout_s = clamp_timeout(timeout)
    run_root = os.path.join(RUNS_DIR, uuid.uuid4().hex)
    ws = os.path.join(run_root, "ws")
    out_dir = tempfile.mkdtemp(prefix="dp-out-")  # root-only; the child writes via an inherited fd
    out_path = os.path.join(out_dir, "output.txt")
    try:
        set_stage("extracting")
        os.makedirs(ws, exist_ok=True)
        os.chmod(RUNS_DIR, 0o711)
        os.chmod(run_root, 0o711)
        safe_extract(archive, ws)
        os.makedirs(os.path.join(ws, ".tmp"), exist_ok=True)

        installed = deps_mod.DepsResult(summary="dependency install not requested")
        if install:
            set_stage("installing")

            def supervise(cmd, **kw):
                return _supervise(cmd, uid=uid, isolated=isolated, lang=lang, **kw)

            installed = deps_mod.prepare(lang, ws, uid=uid, isolated=isolated, supervise=supervise)
            for target, link in installed.links:
                _link_dependencies(target, link)

        if isolated:
            _chown_tree(ws, uid)
            os.chmod(ws, 0o700)

        gocache = _prepare_go_cache(run_root, uid, isolated) if lang == "go" else ""
        set_stage("testing")
        # With a Go cache beside the workspace, watch both and allow for the seed's own size.
        watch, allowed = (run_root, MAX_DISK_BYTES + _go_cache_seed_bytes()) if gocache else (ws, MAX_DISK_BYTES)
        rc, killed, seconds = _supervise(
            test_command(lang, test_path), cwd=ws, env=test_env(lang, ws, installed, gocache),
            timeout=timeout_s, out_path=out_path, watch_dir=watch, max_disk=allowed,
            phase="test", uid=uid, isolated=isolated, lang=lang)

        text = _read_tail(out_path, MAX_OUTPUT_BYTES)
        if killed == "timeout":
            text += f"\n[runner: killed after {timeout_s}s]"
        elif killed == "disk":
            text += "\n[runner: killed, the run exceeded its disk limit]"
        elif killed == "output":
            text += "\n[runner: killed, the run printed too much output]"
        return {
            "returncode": 124 if killed == "timeout" else (1 if killed else rc),
            "output": text, "timed_out": killed == "timeout", "duration_s": round(seconds, 2),
            "killed_reason": killed, "network_isolated": False, "lang": lang,
            "install": installed.as_dict(),
        }
    finally:
        shutil.rmtree(run_root, ignore_errors=True)
        shutil.rmtree(out_dir, ignore_errors=True)
        release_slot(slot)
