"""Repo ingestion + listing endpoints."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel

from app import registry
from app.auth.deps import get_current_user
from app.config import get_settings
from app.db import store as pstore
from app.llm.client import embed_signature
from app.llm.keyctx import reset_request_key, set_request_key
from app.rag.ingest import ingest_repo, make_repo_id, prepare_repo_dir

router = APIRouter(prefix="/api/repos", tags=["repos"])


class IngestRequest(BaseModel):
    source: str  # GitHub URL or local filesystem path


def _persistent(user: dict) -> bool:
    """True when repo ownership/history should go to Postgres (a database is
    configured AND the caller is a real logged-in user, not the anonymous
    principal used when auth isn't configured at all)."""
    return get_settings().auth_available and user.get("id") is not None


@router.post("/ingest")
def ingest(
    req: IngestRequest,
    x_gemini_key: str | None = Header(default=None),
    user: dict = Depends(get_current_user),
):
    token = set_request_key((x_gemini_key or "").strip() or None)
    try:
        try:
            if _persistent(user):
                repo_key = make_repo_id(req.source)
                sig = embed_signature()
                existing = pstore.find_matching_repo(repo_key, sig)
                if existing:
                    # Someone already indexed this exact source with the same
                    # embedder — reuse those embeddings instead of re-spending
                    # Gemini quota. Still (re-)clone locally so file-reading
                    # tools have something on disk even after a cold restart
                    # wiped the previous clone — cheap, no Gemini calls.
                    repo_dir = prepare_repo_dir(req.source, repo_key)
                    summary = {**existing, "repo_dir": repo_dir, "source": req.source}
                else:
                    summary = ingest_repo(req.source)
                pstore.upsert_user_repo(user["id"], repo_key, summary)
            else:
                summary = ingest_repo(req.source)
                if summary.get("repo_dir"):
                    registry.register(summary["repo_id"], summary)
        except Exception as exc:  # noqa: BLE001 — surface a clean error to the client
            raise HTTPException(status_code=400, detail=str(exc))
        return summary
    finally:
        reset_request_key(token)


@router.get("")
def list_repos(user: dict = Depends(get_current_user)):
    if _persistent(user):
        return {"repos": pstore.list_user_repos(user["id"])}
    return {"repos": registry.all_repos()}
