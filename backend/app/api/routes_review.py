"""Code review endpoint (login-gated; spends the caller's own Gemini key if sent)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field

from app.auth.deps import get_current_user
from app.llm.keyctx import reset_request_key, set_request_key
from app.review.reviewer import review_diff

router = APIRouter(prefix="/api/review", tags=["review"])


class ReviewRequest(BaseModel):
    diff: str = Field(max_length=100_000)
    prompt_version: str | None = None


@router.post("/diff")
def review(
    req: ReviewRequest,
    x_gemini_key: str | None = Header(default=None),
    user: dict = Depends(get_current_user),
):
    token = set_request_key((x_gemini_key or "").strip() or None)
    try:
        findings = review_diff(req.diff, req.prompt_version)
    except Exception as exc:  # noqa: BLE001 — surface a clean error to the client
        raise HTTPException(status_code=400, detail=str(exc))
    finally:
        reset_request_key(token)
    return {"findings": findings, "count": len(findings)}
