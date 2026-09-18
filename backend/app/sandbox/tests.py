"""Detect how to run a repo's tests, run them in the sandbox, parse the result.

Kept separate from runner.py so the raw isolation layer stays generic and this
holds the test-framework-specific knowledge (currently pytest-first, with a
generic fallback).
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass

from app.sandbox.runner import SandboxResult, run_in_sandbox


@dataclass
class TestReport:
    passed: int
    failed: int
    errors: int
    returncode: int
    timed_out: bool
    raw_output: str
    command: list[str]

    @property
    def ok(self) -> bool:
        return self.returncode == 0 and not self.timed_out

    def summary(self) -> str:
        if self.timed_out:
            return "tests timed out"
        return f"{self.passed} passed, {self.failed} failed, {self.errors} errors"


def detect_test_command(workspace_dir: str, test_path: str = "") -> list[str]:
    """Best-effort detection of the test command for a repo."""
    root = os.path.abspath(workspace_dir)
    has = lambda p: os.path.exists(os.path.join(root, p))

    # Python / pytest
    if any(has(p) for p in ("pytest.ini", "pyproject.toml", "setup.cfg", "tox.ini", "tests", "conftest.py")):
        cmd = ["python", "-m", "pytest", "-q"]
        if test_path:
            cmd.append(test_path)
        return cmd

    # Node / npm
    if has("package.json"):
        return ["npm", "test", "--silent"]

    # Fallback: try pytest anyway (works for many simple python repos).
    cmd = ["python", "-m", "pytest", "-q"]
    if test_path:
        cmd.append(test_path)
    return cmd


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


def run_tests_in_workspace(workspace_dir: str, test_path: str = "") -> TestReport:
    cmd = detect_test_command(workspace_dir, test_path)
    result: SandboxResult = run_in_sandbox(workspace_dir, cmd)
    output = result.combined(limit=8000)
    passed, failed, errors = _parse_pytest(output)
    # If parsing found nothing but the run failed, surface it as an error.
    if passed == failed == errors == 0 and not result.ok and not result.timed_out:
        errors = 1
    return TestReport(
        passed=passed, failed=failed, errors=errors,
        returncode=result.returncode, timed_out=result.timed_out,
        raw_output=output, command=cmd,
    )
