"""Repo ingestion + listing endpoints."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app import registry
from app.auth.deps import BoundKey, CurrentUser
from app.rag.ingest import ingest_repo

# BoundKey binds the request's BYOK Gemini key; CurrentUser requires a logged-in
# caller (a no-op that passes through when auth isn't configured).
router = APIRouter(prefix="/api/repos", tags=["repos"], dependencies=[BoundKey, CurrentUser])


class IngestRequest(BaseModel):
    source: str  # GitHub URL or local filesystem path


@router.post("/ingest")
def ingest(req: IngestRequest):
    try:
        summary = ingest_repo(req.source)
    except Exception as exc:  # noqa: BLE001 — surface a clean error to the client
        raise HTTPException(status_code=400, detail=str(exc))
    if summary.get("repo_dir"):
        registry.register(summary["repo_id"], summary)
    return summary


@router.get("")
def list_repos():
    return {"repos": registry.all_repos()}
