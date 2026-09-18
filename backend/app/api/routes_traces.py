"""Observability endpoints — list and fetch lifecycle traces."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.obs import store

router = APIRouter(prefix="/api/traces", tags=["traces"])


@router.get("")
def list_traces(limit: int = 50):
    return {"traces": store.list_traces(limit)}


@router.get("/{trace_id}")
def get_trace(trace_id: str):
    tr = store.get_trace(trace_id)
    if tr is None:
        raise HTTPException(404, f"No trace '{trace_id}'.")
    return tr
