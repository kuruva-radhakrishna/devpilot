"""Tools the agent can call.

These are plain Python functions with type hints + docstrings. The Gemini SDK
reads the signature and docstring to build the schema the model sees, so the
docstrings below are effectively prompts — keep them clear and specific.

The agent operates on ONE repo at a time. Rather than thread repo context
through every call, we stash it in a ContextVar that the runtime sets before
starting a turn. Every tool reads from it.

SECURITY NOTE: read_file / search_code are sandboxed to the repo directory via
_safe_path(). run_tests is intentionally a stub — executing untrusted repo code
must happen inside an isolated Docker sandbox. See review/ and README "Roadmap".
"""
from __future__ import annotations

import os
import re
import subprocess
from contextvars import ContextVar
from dataclasses import dataclass

from app.obs import tracing
from app.rag.retriever import retrieve

# --------------------------------------------------------------------------- #
# Per-turn repo context
# --------------------------------------------------------------------------- #
@dataclass
class RepoContext:
    repo_id: str
    repo_dir: str
    # A lazily-created workspace copy used by run_tests (so the agent never runs
    # tests against, or mutates, the indexed source). Typed loosely to avoid an
    # import cycle with app.patch.patcher.
    workspace: object | None = None


_active: ContextVar[RepoContext | None] = ContextVar("active_repo", default=None)


def set_active_repo(ctx: RepoContext) -> None:
    _active.set(ctx)


def _ctx() -> RepoContext:
    ctx = _active.get()
    if ctx is None:
        raise RuntimeError("No active repo context set for this agent turn.")
    return ctx


def _safe_path(rel_path: str) -> str:
    """Resolve a repo-relative path and refuse anything escaping the repo dir."""
    root = os.path.abspath(_ctx().repo_dir)
    target = os.path.abspath(os.path.join(root, rel_path))
    if os.path.commonpath([root, target]) != root:
        raise ValueError(f"Path escapes repository: {rel_path}")
    return target


# --------------------------------------------------------------------------- #
# Tools (exposed to the model)
# --------------------------------------------------------------------------- #
def search_code(query: str) -> str:
    """Search the repository for code relevant to a natural-language query using
    hybrid semantic + keyword retrieval. Use this first to locate where
    something lives before reading files. Returns ranked snippets with file
    paths and line numbers.

    Args:
        query: What you're looking for, e.g. "where JWT tokens are validated".
    """
    ctx = _ctx()
    with tracing.span("tool.search_code", query=query):
        result = retrieve(ctx.repo_id, query, top_k=6)
        tracing.add_attrs(hits=len(result.hits))
        if not result.hits:
            return "No matching code found."
        lines = []
        for h in result.hits:
            loc = f"{h.path}:{h.start_line}-{h.end_line}"
            sym = f" [{h.symbol}]" if h.symbol else ""
            lines.append(f"{loc}{sym} (score {h.score:.2f})\n{h.content}\n")
        return "\n---\n".join(lines)


def read_file(path: str, start_line: int = 1, end_line: int = 400) -> str:
    """Read a slice of a file from the repository by its repo-relative path.
    Use after search_code to inspect full context around a snippet.

    Args:
        path: Repo-relative path, e.g. "src/services/orderService.js".
        start_line: 1-indexed first line to return.
        end_line: 1-indexed last line to return (inclusive).
    """
    with tracing.span("tool.read_file", path=path, start_line=start_line, end_line=end_line):
        abs_path = _safe_path(path)
        if not os.path.isfile(abs_path):
            return f"File not found: {path}"
        with open(abs_path, "r", encoding="utf-8", errors="ignore") as fh:
            lines = fh.readlines()
        start = max(1, start_line)
        end = min(len(lines), end_line)
        numbered = [f"{i}\t{lines[i-1].rstrip()}" for i in range(start, end + 1)]
        return "\n".join(numbered) if numbered else "(empty range)"


def find_references(symbol: str) -> str:
    """Find every place a symbol (function, class, variable) is referenced across
    the repository using a fast text scan. Use to trace call sites and impact.

    Args:
        symbol: Identifier to search for, e.g. "validateToken".
    """
    with tracing.span("tool.find_references", symbol=symbol):
        root = os.path.abspath(_ctx().repo_dir)
        pattern = re.compile(rf"\b{re.escape(symbol)}\b")
        results: list[str] = []
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in {".git", "node_modules", ".venv", "__pycache__", "dist", "build"}]
            for name in filenames:
                fp = os.path.join(dirpath, name)
                try:
                    with open(fp, "r", encoding="utf-8", errors="ignore") as fh:
                        for i, line in enumerate(fh, 1):
                            if pattern.search(line):
                                rel = os.path.relpath(fp, root).replace(os.sep, "/")
                                results.append(f"{rel}:{i}: {line.strip()}")
                except OSError:
                    continue
                if len(results) >= 50:
                    results.append("... (truncated at 50 matches)")
                    return "\n".join(results)
        return "\n".join(results) if results else f"No references to '{symbol}' found."


def list_files(subdir: str = "") -> str:
    """List files and directories under a repo-relative subdirectory to
    understand project structure. Pass "" for the repository root.

    Args:
        subdir: Repo-relative directory, e.g. "src/controllers".
    """
    with tracing.span("tool.list_files", subdir=subdir):
        abs_path = _safe_path(subdir) if subdir else os.path.abspath(_ctx().repo_dir)
        if not os.path.isdir(abs_path):
            return f"Not a directory: {subdir}"
        entries = []
        for name in sorted(os.listdir(abs_path)):
            if name in {".git", "node_modules", ".venv", "__pycache__"}:
                continue
            full = os.path.join(abs_path, name)
            entries.append(f"{name}/" if os.path.isdir(full) else name)
        return "\n".join(entries) or "(empty)"


def run_tests(test_path: str = "") -> str:
    """Run the repository's test suite (or a subset) inside an isolated sandbox
    and return the results (pass/fail counts and failure output).

    Tests run in a throwaway workspace copy of the repo, executed via the
    configured sandbox (Docker by default — no network, memory/CPU/PID limits,
    non-root). The indexed source is never mutated or executed directly.

    Args:
        test_path: Optional path to a specific test file or directory.
    """
    from app.patch.patcher import create_workspace
    from app.sandbox.tests import run_tests_in_workspace

    ctx = _ctx()
    with tracing.span("tool.run_tests", test_path=test_path):
        if ctx.workspace is None:
            ctx.workspace = create_workspace(ctx.repo_dir, ctx.repo_id)
        report = run_tests_in_workspace(ctx.workspace.path, test_path)  # type: ignore[attr-defined]
        tracing.add_attrs(summary=report.summary(), passed=report.ok)
        return f"{report.summary()}\n\n{report.raw_output[-3000:]}"


# The set the agent runtime hands to Gemini. Add new tools here.
ALL_TOOLS = [search_code, read_file, find_references, list_files, run_tests]
