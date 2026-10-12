"""Turn a RepairResult into what the API returns and what the chat history stores."""
from __future__ import annotations

from app.agent.repair import RepairResult


def result_to_dict(result: RepairResult) -> dict:
    return {
        "success": result.success,
        "root_cause": result.root_cause,
        "explanation": result.explanation,
        "files_changed": result.files_changed,
        "diff": result.diff,
        "test_output": result.test_output,
        "note": result.note,
        "trace_id": result.trace_id,
        "tests_before": result.tests_before,
        "tests_after": result.tests_after,
        "lang": result.lang,
        "install_note": result.install_note,
        "iterations": [
            {
                "attempt": it.attempt,
                "root_cause": it.root_cause,
                "files_changed": it.files_changed,
                "test_summary": it.test_summary,
                "passed": it.passed,
            }
            for it in result.iterations
        ],
        "markdown": repair_markdown(result),
    }


def repair_markdown(r: RepairResult) -> str:
    """A self-contained markdown write-up. It is what gets saved to the chat history,
    so a reloaded conversation shows the outcome (with its diff) exactly as it was."""
    if r.note == "tests_not_executed":
        why = (r.test_output or "").strip()
        return "**The tests could not be run, so nothing was changed.**\n\n" + (why or "No reason was given.")
    if r.note == "dependencies_unavailable":
        return ("**The repo's dependencies couldn't be installed, so nothing was changed.** "
                "Without its packages the tests fail for reasons that have nothing to do with the "
                "code, and a patch would only be a guess.\n\n```\n"
                + ((r.install_note or r.test_output or "").strip()[-1500:] or "No details were given.")
                + "\n```")
    if r.note == "baseline_passing":
        return "**Nothing to fix.** This repo's tests already pass."

    head = "**Fix verified.**" if r.success else "**No verified fix.**"
    parts = [f"{head} The tests went from `{r.tests_before}` to `{r.tests_after}`."]
    if r.root_cause:
        parts.append(f"**Root cause.** {r.root_cause.strip()}")
    if r.explanation:
        parts.append(f"**What changed.** {r.explanation.strip()}")
    if r.diff and r.diff.strip():
        parts.append("```diff\n" + r.diff.strip("\n") + "\n```")
    if not r.success and r.iterations:
        lines = [f"{it.attempt}. {it.test_summary}" for it in r.iterations]
        parts.append("**Attempts**\n\n" + "\n".join(lines))
    return "\n\n".join(parts)
