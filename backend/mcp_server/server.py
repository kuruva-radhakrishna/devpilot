"""DevPilot MCP server.

Exposes DevPilot's repository tools over the Model Context Protocol so that ANY
MCP-compatible client (Claude Desktop, other agents, IDEs) can use them — not
just DevPilot's own agent loop. This is the "standardized tool interface"
talking point: the same internals, now discoverable and callable by any host.

Tools: search_repository, read_file, find_references, list_files, run_tests,
git_diff, create_patch — the last three operate on an isolated working copy of
the active repo, so create_patch + run_tests + git_diff give an external client
the full inspect → patch → validate loop without ever touching the source.

Run it (stdio transport):
    python -m mcp_server.server

Register it with an MCP host, e.g. Claude Desktop config:
    {
      "mcpServers": {
        "devpilot": {
          "command": "python",
          "args": ["-m", "mcp_server.server"],
          "cwd": "<path>/devpilot/backend"
        }
      }
    }

The tools operate on a repo selected by DEVPILOT_ACTIVE_REPO (a repo_id already
ingested via the API/CLI). This keeps the MCP surface simple while reusing the
exact same tool implementations as the agent.
"""
from __future__ import annotations

import json
import os

from mcp.server.fastmcp import FastMCP

from app import registry
from app.agent import tools as agent_tools
from app.agent.tools import RepoContext, set_active_repo

mcp = FastMCP("devpilot")

# One reusable workspace per repo for the mutating tools (run_tests/git_diff/
# create_patch), so patches accumulate and tests run against them — never against
# the indexed source.
_workspaces: dict = {}


def _activate_repo() -> tuple[str, str]:
    repo_id = os.environ.get("DEVPILOT_ACTIVE_REPO", "")
    meta = registry.get(repo_id) if repo_id else None
    if not meta:
        raise RuntimeError(
            "Set DEVPILOT_ACTIVE_REPO to an ingested repo_id "
            "(see /api/repos) before calling DevPilot MCP tools."
        )
    set_active_repo(RepoContext(repo_id=repo_id, repo_dir=meta["repo_dir"]))
    return repo_id, meta["repo_dir"]


def _workspace(repo_id: str, repo_dir: str):
    from app.patch.patcher import create_workspace
    if repo_id not in _workspaces:
        _workspaces[repo_id] = create_workspace(repo_dir, repo_id)
    return _workspaces[repo_id]


@mcp.tool()
def search_repository(query: str) -> str:
    """Hybrid semantic + keyword search over the active repository."""
    _activate_repo()
    return agent_tools.search_code(query)


@mcp.tool()
def read_file(path: str, start_line: int = 1, end_line: int = 400) -> str:
    """Read a slice of a file from the active repository by repo-relative path."""
    _activate_repo()
    return agent_tools.read_file(path, start_line, end_line)


@mcp.tool()
def find_references(symbol: str) -> str:
    """Find all references to a symbol across the active repository."""
    _activate_repo()
    return agent_tools.find_references(symbol)


@mcp.tool()
def list_files(subdir: str = "") -> str:
    """List files under a repo-relative subdirectory of the active repository."""
    _activate_repo()
    return agent_tools.list_files(subdir)


@mcp.tool()
def run_tests(test_path: str = "") -> str:
    """Run the active repository's tests in an isolated sandbox against a working
    copy. Returns pass/fail counts and failure output.

    Args:
        test_path: Optional path to a specific test file or directory.
    """
    from app.sandbox.tests import run_tests_in_workspace
    repo_id, repo_dir = _activate_repo()
    ws = _workspace(repo_id, repo_dir)
    report = run_tests_in_workspace(ws.path, test_path)
    return f"{report.summary()}\n\n{report.raw_output[-3000:]}"


@mcp.tool()
def git_diff() -> str:
    """Return the unified diff of all changes made to the active repository's
    working copy since it was set up (e.g. patches applied via create_patch)."""
    from app.patch.patcher import git_diff as _git_diff
    repo_id, repo_dir = _activate_repo()
    ws = _workspace(repo_id, repo_dir)
    return _git_diff(ws) or "(no changes)"


@mcp.tool()
def create_patch(patch_json: str) -> str:
    """Apply a STRUCTURED patch to the active repository's working copy. The
    backend validates line ranges and file paths before applying — the caller
    never edits files directly.

    Args:
        patch_json: A JSON object like
            {"patches": [{"file": "app.py", "changes": [
                {"start_line": 10, "end_line": 11, "replacement": "..."}]}]}
    """
    from app.patch.patcher import apply_patch, parse_patch, git_diff as _git_diff
    repo_id, repo_dir = _activate_repo()
    ws = _workspace(repo_id, repo_dir)
    try:
        obj = json.loads(patch_json)
    except json.JSONDecodeError as exc:
        return f"Invalid patch JSON: {exc}"
    patches = parse_patch(obj)
    if not patches:
        return "No valid patch found in the provided JSON."
    result = apply_patch(ws, patches)
    if not result.applied:
        return f"Patch not applied: {result.error}"
    return f"Applied to {', '.join(result.files_changed)}.\n\n{_git_diff(ws)}"


if __name__ == "__main__":
    mcp.run()  # stdio transport by default
