"""Agent (ask / debug) endpoint."""
from __future__ import annotations

import os

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel

from app import registry
from app.agent.repair import run_repair
from app.agent.runtime import run_agent
from app.auth.deps import get_current_user
from app.config import get_settings
from app.db import store as pstore
from app.llm.keyctx import reset_request_key, set_request_key
from app.rag.ingest import prepare_repo_dir

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


def _persistent(user: dict) -> bool:
    return get_settings().auth_available and user.get("id") is not None


def _resolve_repo(user: dict, repo_id: str) -> dict | None:
    """Look up a repo's metadata (Postgres when persistence is on, else the
    legacy shared registry), then make sure its on-disk directory actually
    exists — a container restart can wipe the local clone even though the
    metadata + embeddings persisted in Postgres."""
    meta = pstore.get_user_repo(user["id"], repo_id) if _persistent(user) else registry.get(repo_id)
    if not meta:
        return None
    if not os.path.isdir(meta["repo_dir"]):
        meta = {**meta, "repo_dir": prepare_repo_dir(meta["source"], repo_id)}
    return meta


@router.get("/messages/{repo_id}")
def get_messages(repo_id: str, user: dict = Depends(get_current_user)):
    if not _persistent(user):
        return {"messages": []}
    return {"messages": pstore.list_messages(user["id"], repo_id)}


@router.post("/ask")
def ask(req: AskRequest, x_gemini_key: str | None = Header(default=None), user: dict = Depends(get_current_user)):
    meta = _resolve_repo(user, req.repo_id)
    if not meta:
        raise HTTPException(404, f"Unknown repo_id '{req.repo_id}'. Ingest it first.")

    def _save(role: str, content: str, tools: list[str] | None = None) -> None:
        if _persistent(user):
            pstore.save_message(user["id"], req.repo_id, role, content, tools)

    _save("user", req.question)
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
            _save("error", str(exc))
            raise HTTPException(status_code=400, detail=str(exc))
    finally:
        reset_request_key(token)
    tool_names = [tc.name for tc in result.tool_calls]
    _save("assistant", result.answer, tool_names)
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
def debug(req: DebugRequest, x_gemini_key: str | None = Header(default=None), user: dict = Depends(get_current_user)):
    """Autonomous repair loop: search -> patch -> run tests in sandbox -> retry."""
    meta = _resolve_repo(user, req.repo_id)
    if not meta:
        raise HTTPException(404, f"Unknown repo_id '{req.repo_id}'. Ingest it first.")

    def _save(role: str, content: str, tools: list[str] | None = None) -> None:
        if _persistent(user):
            pstore.save_message(user["id"], req.repo_id, role, content, tools)

    _save("user", f"[repair] {req.bug_report}")
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
            _save("error", str(exc))
            raise HTTPException(status_code=400, detail=str(exc))
    finally:
        reset_request_key(token)
    _save("assistant", result.explanation or result.root_cause or "(no explanation)")
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
