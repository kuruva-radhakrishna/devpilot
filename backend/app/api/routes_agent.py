"""Agent (ask / debug) endpoint."""
from __future__ import annotations

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel

from app import registry
from app.agent.repair import run_repair
from app.agent.runtime import run_agent
from app.auth.deps import CurrentUser
from app.llm.keyctx import reset_request_key, set_request_key

# CurrentUser requires a logged-in caller (a no-op that passes through when
# auth isn't configured). BYOK's X-Gemini-Key is handled per-route below, not
# as a router-level dependency — see app/auth/deps.py for why.
router = APIRouter(prefix="/api/agent", tags=["agent"], dependencies=[CurrentUser])


class AskRequest(BaseModel):
    repo_id: str
    question: str
    task: str = "debugging"          # which prompt family to use
    prompt_version: str | None = None  # override PROMPT_VERSION for A/B tests


class DebugRequest(BaseModel):
    repo_id: str
    bug_report: str
    test_path: str = ""              # optional: narrow to one test file/dir
    prompt_version: str = "v4"       # whole-function format (the validated default)


@router.post("/ask")
def ask(req: AskRequest, x_gemini_key: str | None = Header(default=None)):
    meta = registry.get(req.repo_id)
    if not meta:
        raise HTTPException(404, f"Unknown repo_id '{req.repo_id}'. Ingest it first.")

    token = set_request_key((x_gemini_key or "").strip() or None)
    try:
        try:
            result = run_agent(
                repo_id=req.repo_id,
                repo_dir=meta["repo_dir"],
                question=req.question,
                task=req.task,
                prompt_version=req.prompt_version,
            )
        except Exception as exc:  # noqa: BLE001 — surface a clean error to the client
            raise HTTPException(status_code=400, detail=str(exc))
    finally:
        reset_request_key(token)
    return {
        "answer": result.answer,
        "steps": result.steps,
        "trace_id": result.trace_id,
        "tool_calls": [
            {"name": tc.name, "args": tc.args, "result_preview": tc.result_preview}
            for tc in result.tool_calls
        ],
    }


@router.post("/debug")
def debug(req: DebugRequest, x_gemini_key: str | None = Header(default=None)):
    """Autonomous repair loop: search -> patch -> run tests in sandbox -> retry."""
    meta = registry.get(req.repo_id)
    if not meta:
        raise HTTPException(404, f"Unknown repo_id '{req.repo_id}'. Ingest it first.")

    token = set_request_key((x_gemini_key or "").strip() or None)
    try:
        try:
            result = run_repair(
                repo_id=req.repo_id,
                repo_dir=meta["repo_dir"],
                bug_report=req.bug_report,
                test_path=req.test_path,
                prompt_version=req.prompt_version,
            )
        except Exception as exc:  # noqa: BLE001 — surface a clean error to the client
            raise HTTPException(status_code=400, detail=str(exc))
    finally:
        reset_request_key(token)
    return {
        "success": result.success,
        "root_cause": result.root_cause,
        "explanation": result.explanation,
        "files_changed": result.files_changed,
        "diff": result.diff,
        "test_output": result.test_output,
        "note": result.note,
        "trace_id": result.trace_id,
        "iterations": [
            {
                "attempt": it.attempt,
                "root_cause": it.root_cause,
                "files_changed": it.files_changed,
                "test_summary": it.test_summary,
                "passed": it.passed,
            }
            for it in result.iterations
        ],
    }
