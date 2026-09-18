"""Code review endpoint."""
from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from app.review.reviewer import review_diff

router = APIRouter(prefix="/api/review", tags=["review"])


class ReviewRequest(BaseModel):
    diff: str
    prompt_version: str | None = None


@router.post("/diff")
def review(req: ReviewRequest):
    findings = review_diff(req.diff, req.prompt_version)
    return {"findings": findings, "count": len(findings)}
