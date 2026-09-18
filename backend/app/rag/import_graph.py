"""Lightweight per-repo import graph (Python).

Used by dependency-aware retrieval expansion: given the files retrieval already
surfaced, we can pull in the files they import (and files that import them) so a
root cause one hop from the symptom gets into the candidate set.

Parses with `ast`; only edges between LOCAL modules of the repo are recorded.
Cached per repo directory.
"""
from __future__ import annotations

import ast
import os
from functools import lru_cache

from app.rag.chunker import iter_source_files


def _local_modules(repo_dir: str) -> dict[str, str]:
    """Map an importable module name -> its repo-relative path (.py files)."""
    mods: dict[str, str] = {}
    for _abs, rel, _lang in iter_source_files(repo_dir):
        if not rel.endswith(".py"):
            continue
        name = os.path.splitext(os.path.basename(rel))[0]
        mods.setdefault(name, rel)
    return mods


def _imports_of(abs_path: str, local: dict[str, str]) -> set[str]:
    out: set[str] = set()
    try:
        with open(abs_path, "r", encoding="utf-8", errors="ignore") as fh:
            tree = ast.parse(fh.read())
    except (OSError, SyntaxError):
        return out
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                base = alias.name.split(".")[0]
                if base in local:
                    out.add(local[base])
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                base = node.module.split(".")[0]
                if base in local:
                    out.add(local[base])
    return out


@lru_cache(maxsize=64)
def build_graph(repo_dir: str) -> tuple[dict[str, set[str]], dict[str, set[str]]]:
    """Return (imports, imported_by) as {repo-relative path -> set of paths}."""
    local = _local_modules(repo_dir)
    imports: dict[str, set[str]] = {}
    imported_by: dict[str, set[str]] = {}
    for abs_p, rel, _lang in iter_source_files(repo_dir):
        if not rel.endswith(".py"):
            continue
        imps = _imports_of(abs_p, local)
        imps.discard(rel)
        imports[rel] = imps
        for target in imps:
            imported_by.setdefault(target, set()).add(rel)
    return imports, imported_by


def related_files(repo_dir: str, path: str) -> set[str]:
    """Files that `path` imports plus files that import `path`."""
    imports, imported_by = build_graph(repo_dir)
    return set(imports.get(path, set())) | set(imported_by.get(path, set()))
