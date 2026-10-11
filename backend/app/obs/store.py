"""Read persisted traces from disk (used by the API and the CLI viewer).

Traces can hold users' questions and bug reports, so each one can be stamped with
its owner (see tag_owner). Pass restrict=True to list/get only what that owner
may see; the CLI viewer reads everything.
"""
from __future__ import annotations

import json
import os
import re

from app.config import get_settings

_ID_RE = re.compile(r"[0-9a-f]{16}")  # tracing.start_trace ids; also blocks path tricks


def _dir() -> str:
    return get_settings().traces_dir


def _visible(tr: dict, owner: int | None, restrict: bool) -> bool:
    return (not restrict) or (owner is not None and tr.get("owner") == owner)


def list_traces(limit: int = 50, *, owner: int | None = None, restrict: bool = False) -> list[dict]:
    """Return recent trace summaries, newest first."""
    d = _dir()
    if not os.path.isdir(d):
        return []
    files = [f for f in os.listdir(d) if f.endswith(".json")]
    files.sort(key=lambda f: os.path.getmtime(os.path.join(d, f)), reverse=True)
    out = []
    for f in files:
        if len(out) >= limit:
            break
        try:
            with open(os.path.join(d, f), "r", encoding="utf-8") as fh:
                tr = json.load(fh)
        except (OSError, json.JSONDecodeError):
            continue
        if not _visible(tr, owner, restrict):
            continue
        out.append({
            "trace_id": tr.get("trace_id"),
            "name": tr.get("name"),
            "status": tr.get("status"),
            "duration_ms": tr.get("duration_ms"),
            "spans": len(tr.get("spans", [])),
        })
    return out


def get_trace(trace_id: str, *, owner: int | None = None, restrict: bool = False) -> dict | None:
    if not _ID_RE.fullmatch(trace_id or ""):
        return None
    path = os.path.join(_dir(), f"{trace_id}.json")
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as fh:
            tr = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return None
    return tr if _visible(tr, owner, restrict) else None


def tag_owner(trace_id: str | None, owner: int | None) -> None:
    """Stamp a finished trace with the user who triggered it. Best-effort: tracing
    must never break the request it describes."""
    if not trace_id or owner is None or not _ID_RE.fullmatch(trace_id):
        return
    path = os.path.join(_dir(), f"{trace_id}.json")
    try:
        with open(path, "r", encoding="utf-8") as fh:
            tr = json.load(fh)
        tr["owner"] = owner
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(tr, fh, indent=2)
        os.replace(tmp, path)
    except (OSError, json.JSONDecodeError):
        pass
