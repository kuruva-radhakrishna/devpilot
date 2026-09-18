"""Code review agent.

Takes a unified diff (e.g. from a PR or `git diff`) and returns structured
findings across security / correctness / performance / maintainability.

This uses a single structured-output call rather than the full tool loop, which
is the right tradeoff for review: the diff is the primary context, and we want
deterministic, parseable output. For deep reviews that need surrounding context,
the model is told it may reference the repo — extend this to pass retrieved
context (see TODO) for a stronger review.
"""
from __future__ import annotations

from app.agent.runtime import load_prompt
from app.llm.client import generate
from app.llm.json_utils import extract_json


def review_diff(diff: str, prompt_version: str | None = None) -> list[dict]:
    """Review a unified diff. Returns a list of finding dicts (see prompt schema)."""
    system = load_prompt("code_review", prompt_version)

    # TODO(context): for each changed file, retrieve() surrounding code and append
    # it here so the reviewer sees callers/definitions the diff doesn't include.
    user = f"Review the following change and return findings as a JSON array.\n\n```diff\n{diff}\n```"

    raw = generate(user, system=system, temperature=0.1)
    return _parse_findings(raw)


def _parse_findings(raw: str) -> list[dict]:
    """Robustly pull a JSON array of findings out of the model response."""
    data = extract_json(raw, kind="array")
    if isinstance(data, list) and data:
        return [d for d in data if isinstance(d, dict)]
    if isinstance(data, list):
        return []  # model legitimately returned [] (no issues)
    return [{
        "severity": "LOW", "category": "maintainability", "file": None,
        "line": None, "title": "Could not parse model output",
        "detail": raw[:500], "suggestion": "Retry the review.",
    }]
