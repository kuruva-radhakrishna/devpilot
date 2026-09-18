"""Tolerant JSON extraction from LLM output.

Real models don't always return clean JSON: they wrap it in ```json fences, add
a sentence before/after, or emit a trailing comma. This module pulls the JSON
out robustly so the repair loop and the reviewer don't fail on cosmetic issues.
It tries progressively looser candidates and light repairs, but never "guesses"
structure — if nothing parses, it returns an empty object/array and the caller
handles the miss (e.g. the repair loop retries with a stricter nudge).
"""
from __future__ import annotations

import json
import re


def _strip_trailing_commas(s: str) -> str:
    # {"a": 1,}  ->  {"a": 1}   and   [1, 2,]  ->  [1, 2]
    return re.sub(r",(\s*[}\]])", r"\1", s)


def extract_json(raw: str, kind: str = "object"):
    """Return the first parseable JSON value of the requested kind.

    kind="object" -> dict (default), kind="array" -> list.
    Falls back to {} or [] respectively if nothing parses.
    """
    empty = {} if kind == "object" else []
    if not raw:
        return empty
    open_c, close_c = ("{", "}") if kind == "object" else ("[", "]")

    candidates: list[str] = []
    # 1. Anything inside a fenced code block.
    for m in re.finditer(r"```(?:json)?\s*(.*?)```", raw, re.DOTALL):
        candidates.append(m.group(1))
    # 2. From the first opening bracket to the last matching close (greedy).
    i, j = raw.find(open_c), raw.rfind(close_c)
    if i != -1 and j != -1 and j > i:
        candidates.append(raw[i : j + 1])
    # 3. The raw string itself.
    candidates.append(raw)

    for c in candidates:
        for attempt in (c, _strip_trailing_commas(c)):
            try:
                val = json.loads(attempt)
            except (json.JSONDecodeError, TypeError):
                continue
            if kind == "object" and isinstance(val, dict):
                return val
            if kind == "array" and isinstance(val, list):
                return val
    return empty
