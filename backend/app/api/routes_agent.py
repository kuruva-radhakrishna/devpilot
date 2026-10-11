"""Agent (ask / debug) endpoint."""
from __future__ import annotations

import os

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel

from app import registry
from app.agent import jobs
from app.agent.report import result_to_dict
from app.agent.repair import run_repair
from app.agent.runtime import run_agent
from app.auth import ratelimit
from app.auth.deps import get_current_user
from app.config import get_settings
from app.db import store as pstore
from app.llm.client import describe_error
from app.llm.keyctx import reset_request_key, set_request_key
from app.obs.store import tag_owner as tag_trace_owner
from app.rag.ingest import prepare_repo_dir
from app.sandbox.runner import repair_status

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
            _save("error", describe_error(exc))
            raise HTTPException(status_code=400, detail=describe_error(exc))
    finally:
        reset_request_key(token)
    if _persistent(user):
        tag_trace_owner(result.trace_id, user["id"])
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


def _owner(user: dict) -> str:
    return f"user:{user['id']}" if user.get("id") is not None else "local"


@router.post("/debug", status_code=202)
def debug(req: DebugRequest, x_gemini_key: str | None = Header(default=None), user: dict = Depends(get_current_user)):
    """Start the autonomous repair loop (search -> patch -> run tests in the sandbox ->
    retry) as a background job. Returns {job_id} at once; poll GET /debug/{job_id}."""
    available, _runner, reason = repair_status()
    if not available:
        raise HTTPException(403, reason)
    meta = _resolve_repo(user, req.repo_id)
    if not meta:
        raise HTTPException(404, f"Unknown repo_id '{req.repo_id}'. Ingest it first.")
    bug = req.bug_report.strip()
    if len(bug) < 8:
        raise HTTPException(400, "Describe the bug in a sentence or two.")
    if len(bug) > 4000 or len(req.test_path) > 300:
        raise HTTPException(400, "That description is too long.")

    owner = _owner(user)
    if user.get("id") is not None:
        retry = ratelimit.repair_user.retry_after(owner)
        if retry:
            raise ratelimit.too_many(retry, "repair runs")

    key = (x_gemini_key or "").strip() or None
    repo_id, repo_dir = req.repo_id, meta["repo_dir"]

    def _save(role: str, content: str, tools: list[str] | None = None) -> None:
        if _persistent(user):
            pstore.save_message(user["id"], repo_id, role, content, tools)

    def target(job: jobs.Job) -> dict:
        # Runs on its own thread, so BYOK key scoping is set up here, not in the route.
        token = set_request_key(key)
        try:
            _save("user", f"[repair] {bug}")  # first, so history order is always question -> answer
            try:
                result = run_repair(
                    repo_id=repo_id, repo_dir=repo_dir, bug_report=bug,
                    test_path=req.test_path.strip(), prompt_version=req.prompt_version,
                    on_progress=job.add,
                )
            except Exception as exc:  # noqa: BLE001
                _save("error", describe_error(exc))
                raise
            if _persistent(user):
                tag_trace_owner(result.trace_id, user["id"])
            payload = result_to_dict(result)
            _save("assistant", payload["markdown"], ["repair"])
            return payload
        finally:
            reset_request_key(token)

    try:
        job = jobs.store.start(owner, repo_id, target)
    except jobs.JobRefused as exc:
        raise HTTPException(429, str(exc))
    if user.get("id") is not None:
        ratelimit.repair_user.hit(owner)
    return {"job_id": job.id, "status": "running"}


@router.get("/debug/{job_id}")
def debug_status(job_id: str, user: dict = Depends(get_current_user)):
    """Progress and, when finished, the result of a repair job. Owner-only."""
    job = jobs.store.get(job_id, _owner(user))
    if job is None:
        raise HTTPException(404, "No such repair job (it may have expired).")
    return job.snapshot()
