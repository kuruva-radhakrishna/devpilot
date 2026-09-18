"""DevPilot FastAPI application.

Run from the backend/ directory:
    uvicorn app.main:app --reload --port 8000

Then open http://localhost:8000/docs for the interactive API.
"""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import routes_agent, routes_repo, routes_review, routes_traces
from app.auth import routes_auth
from app.config import get_settings

app = FastAPI(
    title="DevPilot",
    description="An AI software-engineering agent: RAG + tool-calling over a repo.",
    version="0.1.0",
)

# Allowed browser origins — the Vite dev server locally, plus any deployed
# frontend set via ALLOWED_ORIGINS (comma-separated) in the environment.
_origins = [o.strip() for o in get_settings().allowed_origins.split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def _init_auth() -> None:
    """Create the users table when auth is configured. Best-effort: a DB blip at
    boot shouldn't take the whole API down — the auth routes surface it clearly."""
    s = get_settings()
    if not s.auth_available:
        return
    try:
        from app.auth import db
        db.init_db()
    except Exception as exc:  # noqa: BLE001
        print(f"[auth] init_db failed (auth routes will report errors): {exc}")


app.include_router(routes_auth.router)
app.include_router(routes_repo.router)
app.include_router(routes_agent.router)
app.include_router(routes_review.router)
app.include_router(routes_traces.router)


@app.get("/api/health")
def health():
    s = get_settings()
    from app.llm.client import provider_name
    return {
        "status": "ok",
        "provider": provider_name(),
        "vector_backend": s.vector_backend,
        "model": s.gemini_model if s.llm_provider == "gemini" else s.ollama_model,
        "gemini_key_set": bool(s.gemini_api_key),
        "auth_enabled": s.auth_available,
    }
