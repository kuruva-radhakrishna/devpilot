"""Central configuration, loaded from environment / .env.

Everything that varies between machines or deployments lives here so the rest
of the code never reads os.environ directly.
"""
from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # --- LLM provider selection ---
    # "gemini" (default) | "ollama". The agent, RAG, repair loop, eval, tracing,
    # MCP and sandbox are all provider-independent — only this picks the backend.
    llm_provider: str = "gemini"

    # --- Gemini ---
    gemini_api_key: str = ""
    # flash-lite has a higher free-tier daily limit than flash — better for a
    # public demo. Callers may also supply their own key per request (BYOK),
    # which is used in place of gemini_api_key for that call.
    gemini_model: str = "gemini-2.5-flash-lite"
    gemini_embed_model: str = "gemini-embedding-001"
    # gemini-embedding-001 defaults to 3072 dims but supports output_dimensionality;
    # we pin 768 to keep vectors compact and match the pgvector schema.
    embed_dim: int = 768

    # --- Ollama (local models; no API key, no quota) ---
    ollama_host: str = "http://localhost:11434"
    ollama_model: str = "qwen2.5-coder:7b"
    ollama_embed_model: str = "nomic-embed-text"  # 768-dim, matches embed_dim

    # --- Vector store ---
    vector_backend: str = "memory"  # "memory" | "pgvector"

    # --- Postgres (pgvector backend) ---
    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_db: str = "devpilot"
    postgres_user: str = "devpilot"
    postgres_password: str = ""  # set via POSTGRES_PASSWORD env; no hardcoded default

    # --- Agent ---
    agent_max_steps: int = 8
    prompt_version: str = "v1"

    # --- Retrieval ---
    retrieval_top_k: int = 8
    repo_cache_dir: str = "./.repo_cache"
    # Dependency-aware expansion: after hybrid retrieval, pull in files that the
    # top hits import / that import them, so a bug one hop from the symptom
    # (root-cause-differs cases) is surfaced. Toggle to measure its effect.
    retrieval_dependency_expansion: bool = True

    # --- Sandbox (test execution) ---
    # "docker"   -> isolated container (recommended; real isolation)
    # "local"    -> subprocess on the host (DEV ONLY, NOT isolated — see warning)
    # "disabled" -> run_tests returns a stub (the old behavior)
    sandbox_backend: str = "docker"
    sandbox_image: str = "devpilot-sandbox:latest"
    sandbox_memory: str = "512m"
    sandbox_cpus: str = "1.0"
    sandbox_pids_limit: int = 256
    sandbox_timeout: int = 120  # seconds for the test phase
    workspace_dir: str = "./.workspaces"

    # --- Repair loop ---
    repair_max_iters: int = 3  # patch attempts before giving up

    # --- Observability (tracing) ---
    trace_enabled: bool = True
    traces_dir: str = "./traces"

    # --- CORS ---
    # Comma-separated browser origins allowed to call the API. Add your deployed
    # frontend URL here (e.g. https://devpilot.vercel.app) via ALLOWED_ORIGINS.
    allowed_origins: str = "http://localhost:5173,http://127.0.0.1:5173"

    # --- Auth (email/password) ---
    # A full Postgres connection string for the user store. On a hosted DB
    # (Supabase, Render Postgres) set DATABASE_URL to the provided URL; locally
    # it falls back to the postgres_* fields above. Auth is only offered when a
    # usable DSN is present (auth_available).
    database_url: str = ""
    # Signs the login JWTs. MUST be overridden in production (env JWT_SECRET) —
    # anyone who knows this value can mint valid sessions.
    jwt_secret: str = "dev-insecure-change-me"
    jwt_expire_hours: int = 168  # 7 days

    @property
    def pg_dsn(self) -> str:
        return (
            f"postgresql://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @property
    def auth_dsn(self) -> str:
        """DSN for the auth/user store: explicit DATABASE_URL wins, else pg_dsn."""
        return self.database_url or self.pg_dsn

    @property
    def auth_available(self) -> bool:
        """Auth is offered only when a real DATABASE_URL is configured. Without
        it (plain local dev) the app runs open, with no login gate."""
        return bool(self.database_url)


@lru_cache
def get_settings() -> Settings:
    return Settings()
