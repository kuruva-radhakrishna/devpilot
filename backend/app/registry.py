"""Tiny persistent registry mapping repo_id -> ingest metadata.

The agent needs the on-disk directory for a repo_id to run its file tools; the
vector store only knows about chunks. This keeps that mapping in a small JSON
file so it survives restarts. (In production this would be a table.)
"""
from __future__ import annotations

import json
import os
import threading

_PATH = "./data/repos.json"
_lock = threading.Lock()


def _read() -> dict:
    if not os.path.exists(_PATH):
        return {}
    with open(_PATH, "r", encoding="utf-8") as fh:
        return json.load(fh)


def register(repo_id: str, meta: dict) -> None:
    with _lock:
        data = _read()
        data[repo_id] = meta
        os.makedirs(os.path.dirname(_PATH) or ".", exist_ok=True)
        with open(_PATH, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2)


def get(repo_id: str) -> dict | None:
    return _read().get(repo_id)


def all_repos() -> dict:
    return _read()
