"""Detect a repo's language and how to run its tests, run them in the sandbox, and parse
the result.

Kept separate from runner.py so the raw isolation layer stays generic and this holds the
test-framework-specific knowledge: pytest for Python, `npm test` for JavaScript/TypeScript
(whatever runner the repo's own `test` script uses: node:test, jest, vitest, mocha) and
`go test` for Go.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass

from app.sandbox.runner import SandboxResult, run_in_sandbox

LANGUAGES = ("python", "node", "go")
LANGUAGE_LABELS = {"python": "Python", "node": "JavaScript/TypeScript", "go": "Go"}

_SKIP_DIRS = {".git", "node_modules", "venv", ".venv", "__pycache__", "vendor", "dist", "build", ".tox"}
_PY_TEST_RE = re.compile(r"(^test_.*\.py$)|(_test\.py$)")


@dataclass
class TestReport:
    __test__ = False  # not a pytest test class, despite the name

    passed: int
    failed: int
    errors: int
    returncode: int
    timed_out: bool
    raw_output: str
    command: list[str]
    skipped: bool = False  # tests never ran (sandbox disabled/blocked)
    lang: str = "python"
    install_note: str = ""       # what the dependency install did ("" when it didn't run)
    install_failed: bool = False  # the packages the tests need could not be installed

    @property
    def ok(self) -> bool:
        return self.returncode == 0 and not self.timed_out and not self.skipped

    def summary(self) -> str:
        if self.skipped:
            return "tests not executed (sandbox disabled or blocked)"
        if self.timed_out:
            return "tests timed out"
        return f"{self.passed} passed, {self.failed} failed, {self.errors} errors"


# --------------------------------------------------------------------------- #
# Detection
# --------------------------------------------------------------------------- #
def _walk_files(root: str, max_depth: int = 3):
    base_depth = root.rstrip(os.sep).count(os.sep)
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
        if dirpath.count(os.sep) - base_depth >= max_depth:
            dirnames[:] = []
        for name in filenames:
            yield dirpath, name


def _node_has_test_script(root: str) -> bool:
    try:
        with open(os.path.join(root, "package.json"), encoding="utf-8", errors="ignore") as fh:
            scripts = (json.load(fh) or {}).get("scripts") or {}
    except (OSError, ValueError, AttributeError):
        return False
    test = scripts.get("test") if isinstance(scripts, dict) else None
    return isinstance(test, str) and bool(test.strip()) and "no test specified" not in test


def detect_language(workspace_dir: str) -> str:
    """The language whose tests to run. Concrete evidence beats config files: Python test
    files win, then Go test files, then an npm test script. Python is the fallback."""
    root = os.path.abspath(workspace_dir)
    has_py_tests = has_go_tests = False
    for _dirpath, name in _walk_files(root):
        if _PY_TEST_RE.search(name):
            has_py_tests = True
        elif name.endswith("_test.go"):
            has_go_tests = True
    if has_py_tests:
        return "python"
    if has_go_tests and os.path.isfile(os.path.join(root, "go.mod")):
        return "go"
    if _node_has_test_script(root):
        return "node"
    if os.path.isfile(os.path.join(root, "go.mod")):
        return "go"
    return "python"


def detect_test_command(workspace_dir: str, test_path: str = "", lang: str | None = None) -> list[str]:
    """The command that runs this repo's tests (used directly by the local/docker backends;
    the remote runner is told the language and target instead and builds its own)."""
    lang = lang or detect_language(workspace_dir)
    if lang == "node":
        return ["npm", "test", "--silent", *(["--", test_path] if test_path else [])]
    if lang == "go":
        return ["go", "test", "-v", "-count=1", test_path or "./..."]
    cmd = ["python", "-m", "pytest", "-q"]
    if test_path:
        cmd.append(test_path)
    return cmd


# --------------------------------------------------------------------------- #
# Parsing
# --------------------------------------------------------------------------- #
def _parse_pytest(output: str) -> tuple[int, int, int]:
    """Pull passed/failed/errors from a pytest summary line."""
    passed = failed = errors = 0
    for kind, target in (("passed", "passed"), ("failed", "failed"), ("error", "errors")):
        m = re.search(rf"(\d+)\s+{kind}", output)
        if m:
            val = int(m.group(1))
            if target == "passed": passed = val
            elif target == "failed": failed = val
            else: errors = val
    return passed, failed, errors


def _count(pattern: str, text: str, flags: int = re.M) -> int | None:
    m = re.search(pattern, text, flags)
    return int(m.group(1)) if m else None


def _parse_node(output: str) -> tuple[int, int, int] | None:
    """node:test (TAP or spec summary), jest, vitest and mocha."""
    # node:test: "# pass 2" / "# fail 1" (TAP) or "ℹ pass 2" (spec reporter)
    p = _count(r"^\s*(?:#|ℹ)\s*pass\s+(\d+)", output)
    f = _count(r"^\s*(?:#|ℹ)\s*fail\s+(\d+)", output)
    if p is not None or f is not None:
        return p or 0, f or 0, 0
    # jest / vitest: "Tests:  1 failed, 2 passed, 3 total"  |  "Tests  1 failed | 2 passed (3)"
    m = re.search(r"^\s*Tests:?\s+(.*)$", output, re.M)
    if m and re.search(r"\d+\s+(passed|failed)", m.group(1)):
        line = m.group(1)
        return (_count(r"(\d+)\s+passed", line, 0) or 0, _count(r"(\d+)\s+failed", line, 0) or 0, 0)
    # mocha: "  2 passing (12ms)" / "  1 failing"
    p = _count(r"^\s*(\d+)\s+passing", output)
    f = _count(r"^\s*(\d+)\s+failing", output)
    if p is not None or f is not None:
        return p or 0, f or 0, 0
    return None


def _parse_go(output: str) -> tuple[int, int, int] | None:
    passed = len(re.findall(r"^\s*--- PASS:", output, re.M))
    failed = len(re.findall(r"^\s*--- FAIL:", output, re.M))
    errors = len(re.findall(r"\[(?:build|setup) failed\]", output))
    if passed or failed or errors:
        return passed, failed, errors
    if re.search(r"^ok\s", output, re.M):
        return 1, 0, 0
    return None


def parse_results(lang: str, output: str) -> tuple[int, int, int]:
    if lang == "node":
        found = _parse_node(output)
    elif lang == "go":
        found = _parse_go(output)
    else:
        return _parse_pytest(output)
    return found or (0, 0, 0)


# --------------------------------------------------------------------------- #
def run_tests_in_workspace(workspace_dir: str, test_path: str = "", lang: str | None = None) -> TestReport:
    lang = lang or detect_language(workspace_dir)
    cmd = detect_test_command(workspace_dir, test_path, lang)
    # install=True: the remote runner installs the repo's dependencies first (and caches
    # them per manifest, so the verification runs after the baseline are fast). Other
    # backends ignore it.
    result: SandboxResult = run_in_sandbox(workspace_dir, cmd, lang=lang, test_path=test_path, install=True)
    output = result.combined(limit=8000)
    passed, failed, errors = parse_results(lang, output)
    # If parsing found nothing but the run failed, surface it as an error.
    if (passed == failed == errors == 0 and not result.ok
            and not result.timed_out and not result.skipped):
        errors = 1
    return TestReport(
        passed=passed, failed=failed, errors=errors,
        returncode=result.returncode, timed_out=result.timed_out,
        raw_output=output, command=cmd, skipped=result.skipped,
        lang=lang, install_note=result.install_note, install_failed=result.install_failed,
    )
