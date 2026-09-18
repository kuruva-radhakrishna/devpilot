"""Agent (ask / debug) endpoint."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app import registry
from app.agent.repair import run_repair
from app.agent.runtime import run_agent

router = APIRouter(prefix="/api/agent", tags=["agent"])


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
def ask(req: AskRequest):
    meta = registry.get(req.repo_id)
    if not meta:
        raise HTTPException(404, f"Unknown repo_id '{req.repo_id}'. Ingest it first.")

    result = run_agent(
        repo_id=req.repo_id,
        repo_dir=meta["repo_dir"],
        question=req.question,
        task=req.task,
        prompt_version=req.prompt_version,
    )
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
def debug(req: DebugRequest):
    """Autonomous repair loop: search -> patch -> run tests in sandbox -> retry."""
    meta = registry.get(req.repo_id)
    if not meta:
        raise HTTPException(404, f"Unknown repo_id '{req.repo_id}'. Ingest it first.")

    result = run_repair(
        repo_id=req.repo_id,
        repo_dir=meta["repo_dir"],
        bug_report=req.bug_report,
        test_path=req.test_path,
        prompt_version=req.prompt_version,
    )
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
