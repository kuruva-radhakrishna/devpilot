"""DevPilot FastAPI application.

Run from the backend/ directory:
    uvicorn app.main:app --reload --port 8000

Then open http://localhost:8000/docs for the interactive API.
"""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import routes_agent, routes_repo, routes_review, routes_traces
from app.config import get_settings

app = FastAPI(
    title="DevPilot",
    description="An AI software-engineering agent: RAG + tool-calling over a repo.",
    version="0.1.0",
)

# Allow the Vite dev server (and any local frontend) to call the API.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)

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
    }
