"""Repo ingestion + listing endpoints."""
from __future__ import annotations

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel

from app import registry
from app.auth.deps import CurrentUser
from app.llm.keyctx import reset_request_key, set_request_key
from app.rag.ingest import ingest_repo

# CurrentUser requires a logged-in caller (a no-op that passes through when
# auth isn't configured). BYOK's X-Gemini-Key is handled per-route below, not
# as a router-level dependency — see app/auth/deps.py for why.
router = APIRouter(prefix="/api/repos", tags=["repos"], dependencies=[CurrentUser])


class IngestRequest(BaseModel):
    source: str  # GitHub URL or local filesystem path


@router.post("/ingest")
def ingest(req: IngestRequest, x_gemini_key: str | None = Header(default=None)):
    token = set_request_key((x_gemini_key or "").strip() or None)
    try:
        try:
            summary = ingest_repo(req.source)
        except Exception as exc:  # noqa: BLE001 — surface a clean error to the client
            raise HTTPException(status_code=400, detail=str(exc))
        if summary.get("repo_dir"):
            registry.register(summary["repo_id"], summary)
        return summary
    finally:
        reset_request_key(token)


@router.get("")
def list_repos():
    return {"repos": registry.all_repos()}
