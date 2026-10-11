"""Observability endpoints — list and fetch lifecycle traces.

Login-gated, and (when auth is on) scoped to the caller's own traces: they
contain users' questions and bug reports.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from app.auth.deps import get_current_user
from app.obs import store

router = APIRouter(prefix="/api/traces", tags=["traces"])


def _scope(user: dict) -> dict:
    # Anonymous only happens when auth is off (local dev): nothing to restrict.
    return {"owner": user["id"], "restrict": not user["anonymous"]}


@router.get("")
def list_traces(limit: int = 50, user: dict = Depends(get_current_user)):
    limit = max(1, min(limit, 200))
    return {"traces": store.list_traces(limit, **_scope(user))}


@router.get("/{trace_id}")
def get_trace(trace_id: str, user: dict = Depends(get_current_user)):
    tr = store.get_trace(trace_id, **_scope(user))
    if tr is None:
        raise HTTPException(404, f"No trace '{trace_id}'.")
    return tr
