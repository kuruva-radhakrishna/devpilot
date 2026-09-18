"""Workspace management + structured patch application.

Key safety principle (from the plan): the LLM never touches the filesystem
directly. It emits a *structured* patch; this module validates and applies it
deterministically. A patch is line-range replacements per file:

    {
      "file": "orders.py",
      "changes": [
        {"start_line": 42, "end_line": 44, "replacement": "..."}
      ]
    }

We operate on a throwaway *workspace* — a copy of the ingested repo with a git
baseline — so applying patches and running tests never corrupts the indexed
source, and `git diff` gives us a clean unified diff of what changed.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import uuid
from dataclasses import dataclass, field

from app.config import get_settings
from app.rag.chunker import IGNORE_DIRS


# --------------------------------------------------------------------------- #
# Workspace
# --------------------------------------------------------------------------- #
@dataclass
class Workspace:
    workspace_id: str
    path: str


def create_workspace(repo_dir: str, repo_id: str) -> Workspace:
    """Copy repo_dir into a fresh workspace and set a git baseline commit."""
    s = get_settings()
    os.makedirs(s.workspace_dir, exist_ok=True)
    ws_id = f"{repo_id}-{uuid.uuid4().hex[:8]}"
    dest = os.path.abspath(os.path.join(s.workspace_dir, ws_id))

    def _ignore(_dir, names):
        return [n for n in names if n in IGNORE_DIRS]

    shutil.copytree(repo_dir, dest, ignore=_ignore)
    _git_baseline(dest)
    return Workspace(workspace_id=ws_id, path=dest)


def destroy_workspace(ws: Workspace) -> None:
    shutil.rmtree(ws.path, ignore_errors=True)


def _git(cwd: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True,
        env={**os.environ, "GIT_AUTHOR_NAME": "devpilot",
             "GIT_AUTHOR_EMAIL": "devpilot@local",
             "GIT_COMMITTER_NAME": "devpilot",
             "GIT_COMMITTER_EMAIL": "devpilot@local"},
    )


def _git_baseline(path: str) -> None:
    """Init a git repo (or reuse) and commit the current tree as the baseline."""
    if not os.path.isdir(os.path.join(path, ".git")):
        _git(path, "init", "-q")
    # Keep test-run artifacts out of the diff (pytest writes these into the
    # workspace when it runs, so they'd otherwise show up as "changes").
    exclude = os.path.join(path, ".git", "info", "exclude")
    os.makedirs(os.path.dirname(exclude), exist_ok=True)
    with open(exclude, "w", encoding="utf-8") as fh:
        fh.write("__pycache__/\n*.pyc\n.pytest_cache/\nnode_modules/\n.coverage\n")
    _git(path, "add", "-A")
    _git(path, "commit", "-q", "-m", "devpilot baseline", "--allow-empty")


def git_diff(ws: Workspace) -> str:
    """Unified diff of everything changed since the baseline commit."""
    _git(ws.path, "add", "-A")
    r = _git(ws.path, "diff", "--cached", "--no-color")
    return r.stdout


# --------------------------------------------------------------------------- #
# Structured patch application
# --------------------------------------------------------------------------- #
@dataclass
class PatchChange:
    start_line: int
    end_line: int
    replacement: str


@dataclass
class FilePatch:
    file: str
    changes: list[PatchChange]
    # Whole-function replacement mode (preferred for small models): the LLM
    # returns the full new function source and the backend AST-locates the
    # function by name, so there are no line numbers to get wrong.
    function: str | None = None
    new_source: str | None = None


@dataclass
class PatchResult:
    applied: bool
    files_changed: list[str] = field(default_factory=list)
    error: str | None = None


def parse_patch(obj: dict) -> list[FilePatch]:
    """Turn the LLM's JSON into typed FilePatch objects.

    Deliberately tolerant of real-model output: skips entries missing a file or
    with non-integer line numbers rather than crashing, so one malformed change
    doesn't sink an otherwise valid patch.
    """
    if not isinstance(obj, dict):
        return []
    patches_raw = obj.get("patches")
    if not patches_raw:
        patches_raw = [obj] if obj.get("file") else []
    patches: list[FilePatch] = []
    for p in patches_raw:
        if not isinstance(p, dict) or not p.get("file"):
            continue
        # Whole-function mode: {file, function, new_source}
        if p.get("function") and p.get("new_source"):
            patches.append(FilePatch(
                file=str(p["file"]), changes=[],
                function=str(p["function"]), new_source=str(p["new_source"])))
            continue
        changes: list[PatchChange] = []
        for c in p.get("changes", []) or []:
            if not isinstance(c, dict):
                continue
            try:
                start = int(c["start_line"])
                end = int(c["end_line"])
            except (KeyError, TypeError, ValueError):
                continue
            changes.append(PatchChange(start, end, c.get("replacement", "") or ""))
        if changes:
            patches.append(FilePatch(file=str(p["file"]), changes=changes))
    return patches


def _resolve_file(root: str, rel: str) -> str | None:
    """Map a model-supplied path to a real workspace file.

    Handles backslashes, leading "./", and a leading repo-name segment by falling
    back to a unique basename match. Returns the repo-relative path, or None.
    """
    rel = rel.replace("\\", "/").lstrip("/")
    while rel.startswith("./"):
        rel = rel[2:]
    root_abs = os.path.abspath(root)
    if os.path.isfile(os.path.join(root_abs, rel)):
        return rel
    base = os.path.basename(rel)
    matches: list[str] = []
    for dirpath, dirnames, filenames in os.walk(root_abs):
        dirnames[:] = [d for d in dirnames if d not in IGNORE_DIRS and d != ".git"]
        if base in filenames:
            matches.append(
                os.path.relpath(os.path.join(dirpath, base), root_abs).replace(os.sep, "/")
            )
    return matches[0] if len(matches) == 1 else None


def _safe_join(root: str, rel: str) -> str:
    target = os.path.abspath(os.path.join(root, rel))
    if os.path.commonpath([os.path.abspath(root), target]) != os.path.abspath(root):
        raise ValueError(f"Patch path escapes workspace: {rel}")
    return target


def _replace_function(lines: list[str], func_name: str, new_source: str):
    """Replace a function's whole source span (located by name via AST) with
    new_source. Returns (new_lines, None) or (None, error)."""
    import ast
    try:
        tree = ast.parse("".join(lines))
    except SyntaxError as exc:
        return None, f"target file does not parse: {exc}"

    target = None
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == func_name:
            target = node
            break
    if target is None:
        return None, f"function '{func_name}' not found in file"

    start = target.lineno            # 1-based `def` line (decorators, if any, stay above)
    end = target.end_lineno or start  # inclusive
    ns = new_source if new_source.endswith("\n") else new_source + "\n"
    ns_lines = ns.splitlines(keepends=True)

    # Preserve the original indentation (handles methods inside a class).
    orig = lines[start - 1]
    indent = orig[: len(orig) - len(orig.lstrip())]
    if indent and ns_lines and not ns_lines[0].startswith((" ", "\t")):
        ns_lines = [(indent + ln if ln.strip() else ln) for ln in ns_lines]

    result = lines[: start - 1] + ns_lines + lines[end:]
    try:
        ast.parse("".join(result))       # never write invalid Python
    except SyntaxError as exc:
        return None, f"replacement would produce invalid Python: {exc}"
    return result, None


def apply_patch(ws: Workspace, patches: list[FilePatch]) -> PatchResult:
    """Apply a patch — either whole-function replacement (preferred) or line-range
    edits (highest line first so earlier edits don't shift later line numbers)."""
    changed: list[str] = []
    try:
        for fp in patches:
            resolved = _resolve_file(ws.path, fp.file)
            if resolved is None:
                return PatchResult(
                    False,
                    error=f"File not found in workspace (or ambiguous): {fp.file}",
                )
            abs_path = _safe_join(ws.path, resolved)
            with open(abs_path, "r", encoding="utf-8", errors="ignore") as fh:
                lines = fh.readlines()

            # Whole-function mode
            if fp.function and fp.new_source is not None:
                new_lines, err = _replace_function(lines, fp.function, fp.new_source)
                if err:
                    return PatchResult(False, files_changed=changed,
                                       error=f"{fp.file}: {err}")
                with open(abs_path, "w", encoding="utf-8") as fh:
                    fh.writelines(new_lines)
                changed.append(resolved)
                continue

            for ch in sorted(fp.changes, key=lambda c: c.start_line, reverse=True):
                if ch.start_line < 1 or ch.end_line > len(lines) + 1 or ch.start_line > ch.end_line:
                    return PatchResult(
                        False,
                        error=f"Invalid range {ch.start_line}-{ch.end_line} in {fp.file} "
                              f"(file has {len(lines)} lines).",
                    )
                repl = ch.replacement
                if repl and not repl.endswith("\n"):
                    repl += "\n"
                repl_lines = repl.splitlines(keepends=True) if repl else []
                # 1-indexed, inclusive -> slice
                lines[ch.start_line - 1 : ch.end_line] = repl_lines

            with open(abs_path, "w", encoding="utf-8") as fh:
                fh.writelines(lines)
            changed.append(resolved)
        return PatchResult(True, files_changed=changed)
    except Exception as exc:  # noqa: BLE001
        return PatchResult(False, files_changed=changed, error=str(exc))
