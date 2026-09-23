"""Tiny persistent registry mapping repo_id -> ingest metadata.

The agent needs the on-disk directory for a repo_id to run its file tools; the
vector store only knows about chunks. This keeps that mapping in a small JSON
file so it survives restarts. (In production this would be a table.)
"""
from __future__ import annotations

import json
import os
import tempfile
import threading

_PATH = "./data/repos.json"
_lock = threading.Lock()


def _read() -> dict:
    """Read the registry file. Defensive: a corrupted or partially-written
    file (e.g. from a killed process mid-write) degrades to "no repos" rather
    than 500ing every endpoint that touches the registry."""
    if not os.path.exists(_PATH):
        return {}
    try:
        with open(_PATH, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (json.JSONDecodeError, OSError) as exc:
        print(f"[registry] {_PATH} unreadable ({exc}); treating as empty")
        return {}


def register(repo_id: str, meta: dict) -> None:
    with _lock:
        data = _read()
        data[repo_id] = meta
        dir_ = os.path.dirname(_PATH) or "."
        os.makedirs(dir_, exist_ok=True)
        # Write to a temp file and atomically rename over the target so a
        # concurrent reader (or a crash mid-write) never sees a truncated /
        # partial JSON file — os.replace is atomic on both POSIX and Windows.
        fd, tmp_path = tempfile.mkstemp(dir=dir_, prefix=".repos_", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(data, fh, indent=2)
            os.replace(tmp_path, _PATH)
        except BaseException:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
            raise


def get(repo_id: str) -> dict | None:
    with _lock:
        return _read().get(repo_id)


def all_repos() -> dict:
    with _lock:
        return _read()
