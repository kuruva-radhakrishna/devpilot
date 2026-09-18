-- DevPilot schema for the pgvector backend.
-- Runs automatically via docker-compose on first boot; run manually otherwise:
--   psql "$PG_DSN" -f backend/app/db/schema.sql

CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS code_chunks (
    chunk_id    TEXT PRIMARY KEY,
    repo_id     TEXT NOT NULL,
    path        TEXT NOT NULL,
    language    TEXT NOT NULL,
    start_line  INTEGER NOT NULL,
    end_line    INTEGER NOT NULL,
    symbol      TEXT,
    content     TEXT NOT NULL,
    -- Keep this dimension in sync with EMBED_DIM (.env). text-embedding-004 = 768.
    embedding   vector(768) NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_code_chunks_repo ON code_chunks (repo_id);

-- IVFFlat index for approximate nearest-neighbour cosine search.
-- Build it after data is loaded for best results; safe to create empty too.
CREATE INDEX IF NOT EXISTS idx_code_chunks_embedding
    ON code_chunks USING ivfflat (embedding vector_cosine_ops)
    WITH (lists = 100);
