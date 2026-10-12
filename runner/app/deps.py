"""Install a repository's dependencies before its tests run, safely and only once.

What "safely" means here, per ecosystem:

  python  `pip install --only-binary=:all:` — wheels only, so no package's build script
          (setup.py) ever executes at install time. Requirements are parsed by us and only
          plain `name[extras] <specifier> ; marker` lines are accepted: option lines
          (`--index-url`, `-r`, `-e`), URLs, VCS refs and local paths are skipped and reported.
  node    `npm ci --ignore-scripts` (or `npm install --ignore-scripts` without a lockfile):
          no package's install/postinstall script ever runs. The repo's own `scripts`
          are stripped from the manifest we install from.
  go      `go mod download`, which only fetches source archives; nothing is compiled or run.

All of it runs as the run's unprivileged uid under prlimit limits, with a time limit and a
disk cap, writing into a scratch directory. Success is promoted into a shared, read-only
cache keyed by a hash of the manifests, so the baseline run, the verification run and every
retry of one repair install once. The cache is root-owned, so test code can read it but
cannot modify what later runs will use.

It cannot block outbound network on Render's free tier (no CAP_SYS_ADMIN), so installs reach
the public registries; the response says so (`network_isolated: false`).
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sys
import threading
import time
import tomllib
from dataclasses import dataclass, field
from typing import Callable

DEPS_DIR = os.environ.get("RUNNER_DEPS_DIR", "/tmp/dp-deps")
INSTALL_TIMEOUT = int(os.environ.get("RUNNER_INSTALL_TIMEOUT", "150"))
MAX_DEPS_BYTES = int(os.environ.get("RUNNER_MAX_DEPS_MB", "300")) * 1024 * 1024
MAX_CACHE_BYTES = int(os.environ.get("RUNNER_MAX_CACHE_MB", "800")) * 1024 * 1024
MAX_REQUIREMENTS = 60

# name[extras] <specifiers> ; marker   — nothing else is ever passed to pip
_REQ_RE = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9._-]*"
    r"(\[[A-Za-z0-9,._-]+\])?"
    r"\s*((~=|==|!=|<=|>=|<|>|===)\s*[A-Za-z0-9.*+!_-]+(\s*,\s*(~=|==|!=|<=|>=|<|>|===)\s*[A-Za-z0-9.*+!_-]+)*)?"
    r"\s*(;\s*[A-Za-z0-9 .'\"()<>=!~_,*+-]+)?$"
)
_TEST_GROUPS = {"test", "tests", "testing", "dev", "develop", "development"}
_REQUIREMENT_FILES = (
    "requirements.txt", "requirements-dev.txt", "requirements_dev.txt", "requirements-test.txt",
    "requirements_test.txt", "test-requirements.txt", "dev-requirements.txt",
)

Supervise = Callable[..., tuple]   # (returncode, killed_reason, seconds), see sandbox._supervise


@dataclass
class DepsResult:
    ran: bool = False
    ok: bool = True
    cached: bool = False
    summary: str = ""
    failed: list[str] = field(default_factory=list)
    seconds: float = 0.0
    log: str = ""
    env: dict = field(default_factory=dict)         # added to the test run's environment
    links: list = field(default_factory=list)       # (target, link path) symlinks to create

    def as_dict(self) -> dict:
        return {"ran": self.ran, "ok": self.ok, "cached": self.cached, "summary": self.summary,
                "failed": self.failed, "seconds": round(self.seconds, 1),
                "log": "" if self.ok else self.log[-1500:], "network_isolated": False}


# --------------------------------------------------------------------------- #
# Python
# --------------------------------------------------------------------------- #
def _req_name(req: str) -> str:
    return re.split(r"[\[\s<>=!~;]", req, maxsplit=1)[0].lower().replace("_", "-")


def parse_requirements(text: str) -> tuple[list[str], list[str]]:
    """(accepted requirement strings, human-readable reasons for the ones skipped)."""
    accepted: list[str] = []
    skipped: list[str] = []
    for raw in text.splitlines():
        line = raw.split(" #")[0].strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("-") or "://" in line or " @ " in line or line.startswith((".", "/", "git+")):
            skipped.append(line[:60])
            continue
        if not _REQ_RE.match(line):
            skipped.append(line[:60])
            continue
        accepted.append(line)
    return accepted, skipped


def _pyproject_requirements(text: str) -> list[str]:
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError:
        return []
    reqs: list[str] = []
    project = data.get("project", {})
    reqs += [r for r in project.get("dependencies", []) if isinstance(r, str)]
    for group, items in (project.get("optional-dependencies", {}) or {}).items():
        if group.lower() in _TEST_GROUPS:
            reqs += [r for r in items if isinstance(r, str)]
    for group, items in (data.get("dependency-groups", {}) or {}).items():
        if group.lower() in _TEST_GROUPS:
            reqs += [r for r in items if isinstance(r, str)]
    return reqs


def plan_python(ws: str) -> tuple[list[str], list[str]]:
    """Requirements to install for this workspace, and the lines we declined."""
    collected: list[str] = []
    skipped: list[str] = []
    for name in _REQUIREMENT_FILES:
        path = os.path.join(ws, name)
        if os.path.isfile(path):
            ok, bad = parse_requirements(open(path, encoding="utf-8", errors="ignore").read())
            collected += ok
            skipped += bad
    pyproject = os.path.join(ws, "pyproject.toml")
    if os.path.isfile(pyproject):
        ok, bad = parse_requirements("\n".join(
            _pyproject_requirements(open(pyproject, encoding="utf-8", errors="ignore").read())))
        collected += ok
        skipped += bad
    seen: dict[str, str] = {}
    for req in collected:
        name = _req_name(req)
        if name in ("pytest", "pip", "setuptools", "wheel"):
            continue  # pytest is the runner's own; the rest would shadow the image's tooling
        seen.setdefault(name, req)
    return list(seen.values())[:MAX_REQUIREMENTS], skipped


# --------------------------------------------------------------------------- #
# Cache
# --------------------------------------------------------------------------- #
_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()
_evict_lock = threading.Lock()


def _lock_for(key: str) -> threading.Lock:
    with _locks_guard:
        return _locks.setdefault(key, threading.Lock())


def _key(*parts: str) -> str:
    h = hashlib.sha256()
    for p in parts:
        h.update(p.encode("utf-8", errors="replace"))
        h.update(b"\0")
    return h.hexdigest()[:24]


def _dir_bytes(path: str) -> int:
    total = 0
    for dirpath, _dirs, files in os.walk(path):
        for f in files:
            try:
                total += os.lstat(os.path.join(dirpath, f)).st_size
            except OSError:
                pass
    return total


def _evict_if_needed() -> None:
    with _evict_lock:
        try:
            entries = [os.path.join(DEPS_DIR, e) for e in os.listdir(DEPS_DIR)
                       if ".tmp-" not in e and os.path.isdir(os.path.join(DEPS_DIR, e))]
        except OSError:
            return
        sized = sorted(((os.path.getmtime(p), p, _dir_bytes(p)) for p in entries))
        total = sum(s for _m, _p, s in sized)
        for _mtime, path, size in sized:  # oldest first
            if total <= MAX_CACHE_BYTES:
                break
            shutil.rmtree(path, ignore_errors=True)
            total -= size


def _own(path: str, uid: int, isolated: bool) -> None:
    if not isolated:
        return
    os.chown(path, uid, uid)
    for dirpath, dirnames, filenames in os.walk(path):
        for n in dirnames + filenames:
            try:
                os.lchown(os.path.join(dirpath, n), uid, uid)
            except OSError:
                pass


def _freeze(path: str, isolated: bool) -> None:
    """Hand a finished install to root and make it read-only, so test code can use it
    but never alter what later runs will be given."""
    if isolated:
        os.chown(path, 0, 0)
        for dirpath, dirnames, filenames in os.walk(path):
            for n in dirnames + filenames:
                try:
                    os.lchown(os.path.join(dirpath, n), 0, 0)
                except OSError:
                    pass
    for dirpath, dirnames, filenames in os.walk(path):
        for n in dirnames:
            try:
                os.chmod(os.path.join(dirpath, n), 0o755)
            except OSError:
                pass
        for n in filenames:
            full = os.path.join(dirpath, n)
            if not os.path.islink(full):
                try:
                    os.chmod(full, (os.stat(full).st_mode & 0o755) | 0o444)
                except OSError:
                    pass


def _remove(path: str) -> None:
    if os.path.isdir(path) and not os.path.islink(path):
        shutil.rmtree(path, ignore_errors=True)
    elif os.path.lexists(path):
        os.remove(path)


def _tail(path: str, limit: int = 4000) -> str:
    try:
        with open(path, "rb") as fh:
            fh.seek(0, os.SEEK_END)
            fh.seek(max(0, fh.tell() - limit))
            return fh.read().decode("utf-8", errors="replace")
    except OSError:
        return ""


# --------------------------------------------------------------------------- #
# Per-language preparation
# --------------------------------------------------------------------------- #
def _install_env(tmp: str, extra: dict | None = None) -> dict:
    env = {
        "PATH": "/usr/local/bin:/usr/local/go/bin:/usr/bin:/bin",
        "HOME": tmp, "TMPDIR": os.path.join(tmp, ".tmp"), "LANG": "C.UTF-8", "CI": "true",
    }
    env.update(extra or {})
    return env


def _prepare_python(ws, uid, isolated, supervise, deadline) -> DepsResult:
    reqs, skipped = plan_python(ws)
    if not reqs:
        note = f" (skipped {len(skipped)} unsupported line(s): {', '.join(skipped[:3])})" if skipped else ""
        return DepsResult(summary="no Python dependencies to install" + note)
    key = _key("py", f"{sys.version_info.major}.{sys.version_info.minor}", *sorted(reqs))
    target = os.path.join(DEPS_DIR, key)
    started = time.monotonic()
    with _lock_for(key):
        if os.path.isdir(target):
            os.utime(target, None)
            return DepsResult(ran=True, cached=True, summary=f"{len(reqs)} Python package(s) (cached)",
                              env={"PYTHONPATH": target})
        tmp = f"{target}.tmp-{os.getpid()}-{int(time.time() * 1000)}"
        os.makedirs(os.path.join(tmp, ".tmp"), exist_ok=True)
        _own(tmp, uid, isolated)
        out = os.path.join(tmp, ".install.log")
        open(out, "wb").close()
        os.chmod(out, 0o666)
        site = os.path.join(tmp, "site")
        os.makedirs(site, exist_ok=True)
        _own(site, uid, isolated)

        def pip(args: list[str], budget: float) -> tuple:
            cmd = [sys.executable, "-m", "pip", "install", "--disable-pip-version-check", "--no-input",
                   "--only-binary=:all:", "--no-compile", "--progress-bar", "off", "--target", site, *args]
            env = _install_env(tmp, {"PIP_CACHE_DIR": os.path.join(tmp, ".pipcache")})
            return supervise(cmd, cwd=tmp, env=env, timeout=max(10, int(budget)), out_path=out,
                             watch_dir=tmp, max_disk=MAX_DEPS_BYTES, phase="install")

        rc, killed, _ = pip(reqs, deadline - time.monotonic())
        failed: list[str] = []
        if rc != 0:
            # One unusable requirement (no wheel, bad pin) shouldn't sink the rest.
            for req in reqs:
                budget = deadline - time.monotonic()
                if budget < 15:
                    failed.append(req)
                    continue
                rc1, _k, _s = pip([req], budget)
                if rc1 != 0:
                    failed.append(req)
        log = _tail(out)
        installed = len(reqs) - len(failed)
        seconds = time.monotonic() - started
        if installed == 0:
            shutil.rmtree(tmp, ignore_errors=True)
            why = "timed out" if killed == "timeout" else "no package could be installed from a wheel"
            return DepsResult(ran=True, ok=False, summary=f"dependency install failed: {why}",
                              failed=reqs, seconds=seconds, log=log)
        _freeze(site, isolated)
        os.replace(site, target)            # the cache entry IS the site-packages directory
        shutil.rmtree(tmp, ignore_errors=True)
        _evict_if_needed()
        summary = f"{installed} of {len(reqs)} Python package(s) installed"
        if failed:
            summary += f"; could not install: {', '.join(failed)}"
        if skipped:
            summary += f"; skipped {len(skipped)} unsupported requirement line(s)"
        return DepsResult(ran=True, ok=not failed, summary=summary, failed=failed, seconds=seconds,
                          log=log, env={"PYTHONPATH": target})


def _node_manifest(ws: str) -> tuple[dict | None, str]:
    path = os.path.join(ws, "package.json")
    try:
        data = json.loads(open(path, encoding="utf-8", errors="ignore").read())
    except (OSError, ValueError):
        return None, ""
    lock = ""
    for name in ("package-lock.json", "npm-shrinkwrap.json"):
        p = os.path.join(ws, name)
        if os.path.isfile(p):
            lock = name
            break
    return data, lock


def _prepare_node(ws, uid, isolated, supervise, deadline) -> DepsResult:
    data, lock = _node_manifest(ws)
    if not isinstance(data, dict):
        return DepsResult(summary="no package.json to install from")
    deps = {**(data.get("dependencies") or {}), **(data.get("devDependencies") or {})}
    if not deps:
        return DepsResult(summary="no npm dependencies to install")
    lock_bytes = open(os.path.join(ws, lock), "rb").read().decode("utf-8", errors="replace") if lock else ""
    key = _key("node", lock_bytes or json.dumps(deps, sort_keys=True))
    target = os.path.join(DEPS_DIR, key)
    started = time.monotonic()
    with _lock_for(key):
        if os.path.isdir(target):
            os.utime(target, None)
            return DepsResult(ran=True, cached=True, summary=f"{len(deps)} npm package(s) (cached)",
                              links=[(os.path.join(target, "node_modules"), os.path.join(ws, "node_modules"))])
        tmp = f"{target}.tmp-{os.getpid()}-{int(time.time() * 1000)}"
        os.makedirs(os.path.join(tmp, ".tmp"), exist_ok=True)
        # Install from a stripped manifest: dependencies only, never the repo's own scripts.
        keep = {k: data[k] for k in ("name", "version", "dependencies", "devDependencies",
                                      "optionalDependencies", "peerDependencies", "overrides", "engines")
                if k in data}
        keep.setdefault("name", "dp-install")
        keep.setdefault("version", "0.0.0")
        with open(os.path.join(tmp, "package.json"), "w", encoding="utf-8") as fh:
            json.dump(keep, fh)
        if lock:
            shutil.copy(os.path.join(ws, lock), os.path.join(tmp, "package-lock.json"))
        _own(tmp, uid, isolated)
        out = os.path.join(tmp, ".install.log")
        open(out, "wb").close()
        os.chmod(out, 0o666)
        common = ["--ignore-scripts", "--no-audit", "--no-fund", "--loglevel=error"]
        cmd = (["npm", "ci", *common] if lock
               else ["npm", "install", *common, "--legacy-peer-deps", "--package-lock=false"])
        env = _install_env(tmp, {"npm_config_cache": os.path.join(tmp, ".npm"),
                                 "npm_config_update_notifier": "false",
                                 "NODE_OPTIONS": "--max-old-space-size=400"})
        rc, killed, _ = supervise(cmd, cwd=tmp, env=env, timeout=max(10, int(deadline - time.monotonic())),
                                  out_path=out, watch_dir=tmp, max_disk=MAX_DEPS_BYTES, phase="install")
        log = _tail(out)
        seconds = time.monotonic() - started
        if rc != 0 or not os.path.isdir(os.path.join(tmp, "node_modules")):
            shutil.rmtree(tmp, ignore_errors=True)
            why = {"timeout": "timed out", "disk": "exceeded the size limit"}.get(killed or "", "npm reported an error")
            return DepsResult(ran=True, ok=False, summary=f"dependency install failed: {why}",
                              failed=sorted(deps)[:20], seconds=seconds, log=log)
        for junk in (".tmp", ".npm", ".install.log", "package.json", "package-lock.json"):
            _remove(os.path.join(tmp, junk))
        _freeze(tmp, isolated)
        os.replace(tmp, target)
        _evict_if_needed()
        return DepsResult(ran=True, summary=f"{len(deps)} npm package(s) installed (scripts disabled)",
                          seconds=seconds, log=log,
                          links=[(os.path.join(target, "node_modules"), os.path.join(ws, "node_modules"))])


def _prepare_go(ws, uid, isolated, supervise, deadline) -> DepsResult:
    gomod = os.path.join(ws, "go.mod")
    if not os.path.isfile(gomod):
        return DepsResult(summary="no go.mod to install from")
    text = open(gomod, encoding="utf-8", errors="ignore").read()
    if not re.search(r"^\s*require\b", text, re.M):
        return DepsResult(summary="no Go module dependencies to download")
    gosum = os.path.join(ws, "go.sum")
    sum_text = open(gosum, encoding="utf-8", errors="ignore").read() if os.path.isfile(gosum) else ""
    key = _key("go", text, sum_text)
    target = os.path.join(DEPS_DIR, key)
    started = time.monotonic()
    with _lock_for(key):
        if os.path.isdir(target):
            os.utime(target, None)
            return DepsResult(ran=True, cached=True, summary="Go modules (cached)",
                              env={"GOMODCACHE": os.path.join(target, "mod")})
        tmp = f"{target}.tmp-{os.getpid()}-{int(time.time() * 1000)}"
        os.makedirs(os.path.join(tmp, ".tmp"), exist_ok=True)
        shutil.copy(gomod, os.path.join(tmp, "go.mod"))
        if sum_text:
            shutil.copy(gosum, os.path.join(tmp, "go.sum"))
        _own(tmp, uid, isolated)
        out = os.path.join(tmp, ".install.log")
        open(out, "wb").close()
        os.chmod(out, 0o666)
        env = _install_env(tmp, {
            "GOMODCACHE": os.path.join(tmp, "mod"), "GOCACHE": os.path.join(tmp, ".gocache"),
            "GOPATH": os.path.join(tmp, ".gopath"), "GOFLAGS": "-modcacherw", "GOTOOLCHAIN": "local",
            "CGO_ENABLED": "0"})
        rc, killed, _ = supervise(["go", "mod", "download"], cwd=tmp, env=env,
                                  timeout=max(10, int(deadline - time.monotonic())), out_path=out,
                                  watch_dir=tmp, max_disk=MAX_DEPS_BYTES, phase="install")
        log = _tail(out)
        seconds = time.monotonic() - started
        if rc != 0:
            shutil.rmtree(tmp, ignore_errors=True)
            why = {"timeout": "timed out", "disk": "exceeded the size limit"}.get(killed or "", "go reported an error")
            return DepsResult(ran=True, ok=False, summary=f"dependency install failed: {why}",
                              seconds=seconds, log=log)
        for junk in (".tmp", ".gocache", ".gopath", ".install.log", "go.mod", "go.sum"):
            _remove(os.path.join(tmp, junk))
        _freeze(tmp, isolated)
        os.replace(tmp, target)
        _evict_if_needed()
        return DepsResult(ran=True, summary="Go modules downloaded", seconds=seconds, log=log,
                          env={"GOMODCACHE": os.path.join(target, "mod")})


_PREPARERS = {"python": _prepare_python, "node": _prepare_node, "go": _prepare_go}


def prepare(lang: str, ws: str, *, uid: int, isolated: bool, supervise: Supervise) -> DepsResult:
    """Install this workspace's dependencies (or reuse a cached install). Never raises:
    a failure is returned as `ok=False` with the reason and the tail of the log."""
    os.makedirs(DEPS_DIR, exist_ok=True)
    os.chmod(DEPS_DIR, 0o755)
    deadline = time.monotonic() + INSTALL_TIMEOUT
    try:
        return _PREPARERS[lang](ws, uid, isolated, supervise, deadline)
    except Exception as exc:  # noqa: BLE001 — an install problem must not crash the job
        return DepsResult(ran=True, ok=False, summary=f"dependency install failed: {type(exc).__name__}: {exc}")
