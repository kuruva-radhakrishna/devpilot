"""The repair loop — DevPilot's killer feature.

    bug report
        |
   [workspace] copy repo + git baseline
        |
   run tests  ------------------------------> already passing? stop.
        |  (failing output)
   retrieve relevant files (RAG) + read full source
        |
   LLM -> STRUCTURED patch (root cause + line edits)   <----------+
        |                                                          |
   apply patch (backend, validated)                                |
        |                                                          |
   run tests in sandbox                                            |
        |                                                          |
   passing? --no--> feed failure back, revise patch (<= N iters) --+
        | yes
   return: root cause, files changed, unified diff, test results, iterations

The LLM only ever emits structured patches; the backend validates + applies +
tests them. That "propose -> apply -> validate -> retry" cycle is what turns
DevPilot from a code chatbot into an agent that can investigate AND verify fixes.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field

from app.agent.runtime import load_prompt
from app.config import get_settings
from app.llm.client import generate
from app.llm.json_utils import extract_json
from app.obs import tracing
from app.patch.patcher import (
    FilePatch, Workspace, apply_patch, create_workspace, destroy_workspace,
    git_diff, parse_patch,
)
from app.rag.retriever import retrieve
from app.sandbox.tests import TestReport, run_tests_in_workspace

MAX_FILE_LINES = 400  # cap per file sent to the LLM


@dataclass
class RepairIteration:
    attempt: int
    root_cause: str
    files_changed: list[str]
    test_summary: str
    passed: bool


@dataclass
class RepairResult:
    success: bool
    root_cause: str
    explanation: str
    files_changed: list[str]
    diff: str
    test_output: str
    iterations: list[RepairIteration] = field(default_factory=list)
    note: str | None = None
    # Pipeline signals for evaluation (did each stage happen at all?).
    patch_generated: bool = False   # the LLM produced a parseable patch
    patch_applied: bool = False     # a patch was successfully applied to the workspace
    trace_id: str | None = None     # observability trace for this repair run
    tests_before: str = ""          # test summary before any patch (the failure)
    tests_after: str = ""           # test summary after the final patch


def _read_numbered(workspace: Workspace, rel_path: str) -> str | None:
    abs_path = os.path.join(workspace.path, rel_path)
    if not os.path.isfile(abs_path):
        return None
    with open(abs_path, "r", encoding="utf-8", errors="ignore") as fh:
        lines = fh.readlines()
    truncated = len(lines) > MAX_FILE_LINES
    lines = lines[:MAX_FILE_LINES]
    body = "".join(f"{i}\t{ln.rstrip(os.linesep)}\n" for i, ln in enumerate(lines, 1))
    header = f"### FILE: {rel_path}" + (" (truncated)" if truncated else "")
    return f"{header}\n{body}"


def _candidate_files(repo_id: str, bug_report: str, workspace: Workspace, limit: int = 5) -> list[str]:
    """Relevant source files via RAG, plus any test files (they name the target)."""
    hits = retrieve(repo_id, bug_report, top_k=12).hits
    ordered: list[str] = []
    for h in hits:
        if h.path not in ordered:
            ordered.append(h.path)
    # Prioritise non-test source but keep at least one test file for context.
    source = [p for p in ordered if "test" not in p.lower()]
    tests = [p for p in ordered if "test" in p.lower()]
    picked = source[:limit] + tests[:2]
    return picked[: limit + 2]


def _patch_signature(patches: list[FilePatch]) -> str:
    """Stable fingerprint of a patch, to detect the model repeating itself."""
    parts = []
    for fp in patches:
        if fp.function:
            parts.append(f"{fp.file}:fn:{fp.function}:{fp.new_source}")
        for c in fp.changes:
            parts.append(f"{fp.file}:{c.start_line}-{c.end_line}:{c.replacement}")
    return "||".join(sorted(parts))


def run_repair(
    repo_id: str,
    repo_dir: str,
    bug_report: str,
    *,
    test_path: str = "",
    prompt_version: str = "v4",  # whole-function format (validated: 83%->97%)
    retrieval: bool = True,
) -> RepairResult:
    """Diagnose and fix a failing test. When retrieval=False (the evaluation
    baseline), the model gets no repository context — only the failing test
    output — so the benchmark can measure how much code-aware RAG actually adds."""
    settings = get_settings()
    system = load_prompt("patch", prompt_version)
    mode = "baseline" if not retrieval else prompt_version

    with tracing.start_trace("repair.run", repo_id=repo_id, bug=bug_report,
                             prompt_version=prompt_version, mode=mode) as _tr:
        trace_id = _tr.trace_id if _tr else None
        ws = create_workspace(repo_dir, repo_id)
        try:
            # 1. Baseline test run — confirm there's actually a failure to fix.
            with tracing.span("run_tests", phase="baseline"):
                report: TestReport = run_tests_in_workspace(ws.path, test_path)
                tracing.add_attrs(summary=report.summary(), passed=report.ok)
            tests_before = report.summary()
            if report.ok:
                return RepairResult(
                    success=True, root_cause="No failing tests to repair.",
                    explanation="The test suite already passes.",
                    files_changed=[], diff="", test_output=report.raw_output,
                    note="baseline_passing", trace_id=trace_id,
                    tests_before=tests_before, tests_after=tests_before,
                )

            # retrieval=False is the no-RAG baseline: give the model no source.
            candidates = _candidate_files(repo_id, bug_report, ws) if retrieval else []
            iterations: list[RepairIteration] = []
            last_root_cause = last_explanation = ""
            last_error: str | None = None
            seen_sigs: set[str] = set()
            any_generated = any_applied = False

            for attempt in range(1, settings.repair_max_iters + 1):
                with tracing.span("repair.iteration", attempt=attempt):
                    file_blocks = [b for p in candidates if (b := _read_numbered(ws, p))]
                    prompt = _build_prompt(bug_report, report, file_blocks, attempt, last_error)

                    raw = generate(prompt, system=system, temperature=0.1)
                    obj = extract_json(raw, kind="object")
                    last_root_cause = obj.get("root_cause") or last_root_cause
                    last_explanation = obj.get("explanation") or last_explanation

                    patches = parse_patch(obj)
                    if not patches:
                        # Common real-LLM miss: prose instead of JSON. Nudge harder.
                        last_error = (
                            "Your previous response did not contain a valid JSON patch "
                            "object with a 'patches' array. Return ONLY the JSON object, "
                            "no prose, no markdown fences."
                        )
                        iterations.append(RepairIteration(
                            attempt, last_root_cause, [], "no valid patch produced", False))
                        tracing.add_attrs(outcome="no_patch")
                        continue

                    any_generated = True
                    sig = _patch_signature(patches)
                    if sig in seen_sigs:
                        iterations.append(RepairIteration(
                            attempt, last_root_cause, [], "identical patch repeated — stopping", False))
                        tracing.add_attrs(outcome="repeated_patch")
                        break
                    seen_sigs.add(sig)

                    with tracing.span("apply_patch", files=[fp.file for fp in patches]):
                        pres = apply_patch(ws, patches)
                    if not pres.applied:
                        last_error = (
                            f"Your previous patch could not be applied: {pres.error}. "
                            "Re-check the file paths and that start_line/end_line are exact, "
                            "1-indexed, and within the file shown."
                        )
                        iterations.append(RepairIteration(
                            attempt, last_root_cause, [], f"patch invalid: {pres.error}", False))
                        tracing.add_attrs(outcome="apply_failed", error=pres.error)
                        _reset_workspace(ws)  # keep line numbers stable for the retry
                        continue

                    # A patch that changes nothing wastes a test run and would loop.
                    if not git_diff(ws).strip():
                        last_error = (
                            "Your previous patch did not change any code. Make a concrete "
                            "edit that fixes the failing test."
                        )
                        iterations.append(RepairIteration(
                            attempt, last_root_cause, [], "patch was a no-op", False))
                        tracing.add_attrs(outcome="noop")
                        _reset_workspace(ws)
                        continue

                    any_applied = True
                    with tracing.span("run_tests", phase="verify"):
                        report = run_tests_in_workspace(ws.path, test_path)
                        tracing.add_attrs(summary=report.summary(), passed=report.ok)
                    iterations.append(RepairIteration(
                        attempt, last_root_cause, pres.files_changed,
                        report.summary(), report.ok))
                    tracing.add_attrs(outcome="passed" if report.ok else "still_failing",
                                      files_changed=pres.files_changed)

                    if report.ok:
                        return RepairResult(
                            success=True, root_cause=last_root_cause,
                            explanation=last_explanation, files_changed=pres.files_changed,
                            diff=git_diff(ws), test_output=report.raw_output,
                            iterations=iterations,
                            patch_generated=any_generated, patch_applied=any_applied,
                            trace_id=trace_id,
                            tests_before=tests_before, tests_after=report.summary(),
                        )
                    # Still failing: keep edits and feed the failure back next attempt.
                    last_error = None

            return RepairResult(
                success=False, root_cause=last_root_cause,
                explanation=last_explanation,
                files_changed=iterations[-1].files_changed if iterations else [],
                diff=git_diff(ws), test_output=report.raw_output,
                iterations=iterations,
                note=f"gave up after {settings.repair_max_iters} attempts",
                patch_generated=any_generated, patch_applied=any_applied,
                trace_id=trace_id,
                tests_before=tests_before, tests_after=report.summary(),
            )
        finally:
            # Keep the workspace for inspection during a demo; comment out to clean up.
            # destroy_workspace(ws)
            pass


def _reset_workspace(ws: Workspace) -> None:
    import subprocess
    subprocess.run(["git", "reset", "-q", "--hard", "HEAD"], cwd=ws.path,
                   capture_output=True)
    subprocess.run(["git", "clean", "-qfd"], cwd=ws.path, capture_output=True)


def _build_prompt(bug: str, report: TestReport, file_blocks: list[str],
                  attempt: int, last_error: str | None) -> str:
    header = f"BUG REPORT:\n{bug}\n\n"
    if attempt > 1:
        header += (
            f"This is repair attempt #{attempt}. Your previous patch did not fix "
            "the failing tests. Use the latest test output below to correct it.\n\n"
        )
    if last_error:
        header += f"IMPORTANT — fix this problem with your last response:\n{last_error}\n\n"
    header += f"CURRENT FAILING TEST OUTPUT:\n{report.raw_output[-4000:]}\n\n"
    header += "RELEVANT SOURCE FILES (1-indexed):\n\n" + "\n\n".join(file_blocks)
    header += "\n\nProduce the structured JSON patch now."
    return header
