"""Read persisted traces from disk (used by the API and the CLI viewer)."""
from __future__ import annotations

import json
import os

from app.config import get_settings


def _dir() -> str:
    return get_settings().traces_dir


def list_traces(limit: int = 50) -> list[dict]:
    """Return recent trace summaries, newest first."""
    d = _dir()
    if not os.path.isdir(d):
        return []
    files = [f for f in os.listdir(d) if f.endswith(".json")]
    files.sort(key=lambda f: os.path.getmtime(os.path.join(d, f)), reverse=True)
    out = []
    for f in files[:limit]:
        try:
            with open(os.path.join(d, f), "r", encoding="utf-8") as fh:
                tr = json.load(fh)
        except (OSError, json.JSONDecodeError):
            continue
        out.append({
            "trace_id": tr.get("trace_id"),
            "name": tr.get("name"),
            "status": tr.get("status"),
            "duration_ms": tr.get("duration_ms"),
            "spans": len(tr.get("spans", [])),
        })
    return out


def get_trace(trace_id: str) -> dict | None:
    path = os.path.join(_dir(), f"{trace_id}.json")
    if not os.path.isfile(path):
        return None
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)
