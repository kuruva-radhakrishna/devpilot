"""Pluggable vector store.

Two backends behind one interface (VectorStore):
  * MemoryStore  -> numpy cosine similarity, persisted to a .pkl on disk.
                    Zero infrastructure; perfect for a first run or a demo.
  * PgVectorStore -> PostgreSQL + pgvector; production-style, supports many
                    repos and concurrent access.

Both implement upsert(), search() and delete_repo(). The retriever and ingest
code never care which one is active — set VECTOR_BACKEND in .env.
"""
from __future__ import annotations

import os
import pickle
from dataclasses import dataclass

import numpy as np

from app.config import get_settings
from app.rag.chunker import Chunk


@dataclass
class SearchHit:
    chunk_id: str
    repo_id: str
    path: str
    language: str
    start_line: int
    end_line: int
    content: str
    symbol: str | None
    score: float


class VectorStore:
    def upsert(self, chunks: list[Chunk], vectors: list[list[float]]) -> None: ...
    def search(self, repo_id: str, query_vec: list[float], top_k: int) -> list[SearchHit]: ...
    def chunks_for_file(self, repo_id: str, path: str) -> list[SearchHit]: ...
    def delete_repo(self, repo_id: str) -> None: ...
    def list_repos(self) -> list[str]: ...


# --------------------------------------------------------------------------- #
# In-memory / on-disk numpy backend
# --------------------------------------------------------------------------- #
class MemoryStore(VectorStore):
    def __init__(self, path: str = "./data/memory_store.pkl"):
        self.path = path
        # rows: list of (Chunk, np.ndarray)
        self._rows: list[tuple[Chunk, np.ndarray]] = []
        self._load()

    def _load(self) -> None:
        if os.path.exists(self.path):
            with open(self.path, "rb") as fh:
                self._rows = pickle.load(fh)

    def _save(self) -> None:
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        with open(self.path, "wb") as fh:
            pickle.dump(self._rows, fh)

    def upsert(self, chunks: list[Chunk], vectors: list[list[float]]) -> None:
        new_ids = {c.chunk_id() for c in chunks}
        # drop any existing rows with the same id (idempotent re-ingest)
        self._rows = [r for r in self._rows if r[0].chunk_id() not in new_ids]
        for c, v in zip(chunks, vectors):
            vec = np.asarray(v, dtype=np.float32)
            norm = np.linalg.norm(vec)
            if norm > 0:
                vec = vec / norm  # store normalized -> dot product == cosine
            self._rows.append((c, vec))
        self._save()

    def search(self, repo_id: str, query_vec: list[float], top_k: int) -> list[SearchHit]:
        q = np.asarray(query_vec, dtype=np.float32)
        qn = np.linalg.norm(q)
        if qn > 0:
            q = q / qn
        scored: list[tuple[float, Chunk]] = []
        for chunk, vec in self._rows:
            if chunk.repo_id != repo_id:
                continue
            scored.append((float(np.dot(q, vec)), chunk))
        scored.sort(key=lambda x: x[0], reverse=True)
        hits = []
        for score, c in scored[:top_k]:
            hits.append(
                SearchHit(
                    chunk_id=c.chunk_id(), repo_id=c.repo_id, path=c.path,
                    language=c.language, start_line=c.start_line, end_line=c.end_line,
                    content=c.content, symbol=c.symbol, score=score,
                )
            )
        return hits

    def chunks_for_file(self, repo_id: str, path: str) -> list[SearchHit]:
        hits = []
        for chunk, _vec in self._rows:
            if chunk.repo_id == repo_id and chunk.path == path:
                hits.append(SearchHit(
                    chunk_id=chunk.chunk_id(), repo_id=chunk.repo_id, path=chunk.path,
                    language=chunk.language, start_line=chunk.start_line,
                    end_line=chunk.end_line, content=chunk.content,
                    symbol=chunk.symbol, score=0.0,
                ))
        hits.sort(key=lambda h: h.start_line)
        return hits

    def delete_repo(self, repo_id: str) -> None:
        self._rows = [r for r in self._rows if r[0].repo_id != repo_id]
        self._save()

    def list_repos(self) -> list[str]:
        return sorted({r[0].repo_id for r in self._rows})


# --------------------------------------------------------------------------- #
# PostgreSQL + pgvector backend
# --------------------------------------------------------------------------- #
class PgVectorStore(VectorStore):
    def __init__(self):
        import psycopg  # imported lazily so "memory" users don't need it
        from pgvector.psycopg import register_vector

        self._psycopg = psycopg
        self._register_vector = register_vector
        self._dsn = get_settings().pg_dsn

    def _conn(self):
        conn = self._psycopg.connect(self._dsn, autocommit=True)
        self._register_vector(conn)
        return conn

    def upsert(self, chunks: list[Chunk], vectors: list[list[float]]) -> None:
        with self._conn() as conn, conn.cursor() as cur:
            for c, v in zip(chunks, vectors):
                cur.execute(
                    """
                    INSERT INTO code_chunks
                        (chunk_id, repo_id, path, language, start_line, end_line,
                         symbol, content, embedding)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (chunk_id) DO UPDATE SET
                        content = EXCLUDED.content,
                        embedding = EXCLUDED.embedding
                    """,
                    (
                        c.chunk_id(), c.repo_id, c.path, c.language,
                        c.start_line, c.end_line, c.symbol, c.content,
                        np.asarray(v, dtype=np.float32),
                    ),
                )

    def search(self, repo_id: str, query_vec: list[float], top_k: int) -> list[SearchHit]:
        q = np.asarray(query_vec, dtype=np.float32)
        with self._conn() as conn, conn.cursor() as cur:
            # <=> is pgvector's cosine distance operator; 1 - distance == similarity
            cur.execute(
                """
                SELECT chunk_id, repo_id, path, language, start_line, end_line,
                       symbol, content, 1 - (embedding <=> %s) AS score
                FROM code_chunks
                WHERE repo_id = %s
                ORDER BY embedding <=> %s
                LIMIT %s
                """,
                (q, repo_id, q, top_k),
            )
            rows = cur.fetchall()
        return [
            SearchHit(
                chunk_id=r[0], repo_id=r[1], path=r[2], language=r[3],
                start_line=r[4], end_line=r[5], symbol=r[6], content=r[7],
                score=float(r[8]),
            )
            for r in rows
        ]

    def chunks_for_file(self, repo_id: str, path: str) -> list[SearchHit]:
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT chunk_id, repo_id, path, language, start_line, end_line,
                       symbol, content
                FROM code_chunks
                WHERE repo_id = %s AND path = %s
                ORDER BY start_line
                """,
                (repo_id, path),
            )
            rows = cur.fetchall()
        return [
            SearchHit(chunk_id=r[0], repo_id=r[1], path=r[2], language=r[3],
                      start_line=r[4], end_line=r[5], symbol=r[6], content=r[7], score=0.0)
            for r in rows
        ]

    def delete_repo(self, repo_id: str) -> None:
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM code_chunks WHERE repo_id = %s", (repo_id,))

    def list_repos(self) -> list[str]:
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute("SELECT DISTINCT repo_id FROM code_chunks ORDER BY repo_id")
            return [r[0] for r in cur.fetchall()]


_store: VectorStore | None = None


def get_store() -> VectorStore:
    """Singleton selected by config. Import this everywhere else."""
    global _store
    if _store is None:
        backend = get_settings().vector_backend.lower()
        _store = PgVectorStore() if backend == "pgvector" else MemoryStore()
    return _store
