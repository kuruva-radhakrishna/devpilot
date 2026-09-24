"""Repository ingestion pipeline.

    GitHub URL / local path
            |
       clone or copy  -> REPO_CACHE_DIR/<repo_id>
            |
        chunk_repo()   -> list[Chunk]   (AST-aware or windowed)
            |
       embed_texts()   -> vectors       (Gemini text-embedding-004)
            |
       store.upsert()  -> vector store  (memory or pgvector)

repo_id is a stable slug derived from the source so re-ingesting updates in place.
"""
from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess

from app.config import get_settings
from app.llm.client import embed_signature, embed_texts
from app.rag.chunker import chunk_repo
from app.rag.vector_store import get_store


def signature_matches(meta: dict) -> bool:
    """True if a repo's stored embedding config matches the current provider/model.

    Guards against querying a repo indexed with one embedder (e.g. Gemini) using
    another's query vectors (e.g. Ollama) — different vector spaces, meaningless
    similarity. A mismatch means the repo must be re-ingested.
    """
    sig = embed_signature()
    return all(meta.get(k) == v for k, v in sig.items())


def make_repo_id(source: str) -> str:
    """Human-ish, filesystem-safe id for a repo source."""
    base = source.rstrip("/").split("/")[-1].replace(".git", "")
    base = re.sub(r"[^A-Za-z0-9_.-]", "-", base) or "repo"
    digest = hashlib.sha1(source.encode()).hexdigest()[:8]
    return f"{base}-{digest}"


def prepare_repo_dir(source: str, repo_id: str) -> str:
    """Materialize a repo's working directory on disk (clone or resolve a
    local path). Public because callers may need to re-run just this step —
    e.g. recovering a git clone that a container restart wiped, without
    re-chunking/re-embedding anything."""
    cache_root = get_settings().repo_cache_dir
    os.makedirs(cache_root, exist_ok=True)
    dest = os.path.join(cache_root, repo_id)

    if source.startswith(("http://", "https://", "git@")):
        if os.path.isdir(dest):
            shutil.rmtree(dest, ignore_errors=True)
        # --depth 1: we only need the current tree, not history.
        subprocess.run(
            ["git", "clone", "--depth", "1", source, dest],
            check=True, capture_output=True, text=True,
        )
        return dest

    # Local path: index it in place (no copy) to save disk/time.
    if not os.path.isdir(source):
        raise FileNotFoundError(f"Local path does not exist: {source}")
    return os.path.abspath(source)


def ingest_repo(source: str) -> dict:
    """Ingest a repo from a GitHub URL or a local path. Returns a summary dict."""
    repo_id = make_repo_id(source)
    repo_dir = prepare_repo_dir(source, repo_id)

    store = get_store()
    store.delete_repo(repo_id)  # idempotent: clear old chunks first

    chunks = chunk_repo(repo_dir, repo_id)
    if not chunks:
        return {"repo_id": repo_id, "source": source, "repo_dir": repo_dir,
                "files": 0, "chunks": 0, "note": "No indexable source files found.",
                **embed_signature()}

    vectors = embed_texts([c.content for c in chunks], task_type="retrieval_document")
    store.upsert(chunks, vectors)

    files = len({c.path for c in chunks})
    return {
        "repo_id": repo_id,
        "source": source,
        "repo_dir": repo_dir,
        "files": files,
        "chunks": len(chunks),
        # Embedding identity — used by signature_matches() to detect a provider
        # change and re-ingest instead of mixing vector spaces.
        **embed_signature(),
    }
