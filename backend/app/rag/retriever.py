"""Hybrid retrieval: semantic (vector) + keyword (lexical), then merge.

Pure vector search misses exact-symbol lookups ("where is validateToken");
pure keyword search misses paraphrases ("why does login fail"). Combining both
with a simple reciprocal-rank fusion gives noticeably better recall, and it's a
great thing to be able to explain in an interview.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from app import registry
from app.config import get_settings
from app.llm.client import embed_query
from app.obs import tracing
from app.rag.import_graph import build_graph
from app.rag.vector_store import SearchHit, get_store

# Dependency-added files are inserted immediately AFTER the hit that pulled them
# in (guaranteed adjacency), so the symptom file stays on top while the connected
# root-cause file is placed right below it — RRF scores are too bunched for a
# score bonus to reliably lift it into top_k.
_DEP_MAX_ADDS = 3


@dataclass
class RetrievedContext:
    hits: list[SearchHit]

    def as_prompt_block(self, max_chars: int = 12000) -> str:
        """Format hits into a context block for the LLM, budgeted to max_chars."""
        parts, used = [], 0
        for h in self.hits:
            header = f"### {h.path}  (lines {h.start_line}-{h.end_line})"
            if h.symbol:
                header += f"  — {h.symbol}"
            block = f"{header}\n```{h.language}\n{h.content}\n```\n"
            if used + len(block) > max_chars:
                break
            parts.append(block)
            used += len(block)
        return "\n".join(parts)


def _keyword_score(query: str, hit: SearchHit) -> float:
    terms = {t.lower() for t in re.findall(r"[A-Za-z_][A-Za-z0-9_]{2,}", query)}
    if not terms:
        return 0.0
    haystack = (hit.content + " " + hit.path + " " + (hit.symbol or "")).lower()
    matched = sum(1 for t in terms if t in haystack)
    return matched / len(terms)


def retrieve(repo_id: str, query: str, top_k: int | None = None) -> RetrievedContext:
    settings = get_settings()
    top_k = top_k or settings.retrieval_top_k
    store = get_store()

    with tracing.span("retrieval", repo_id=repo_id, query=query, top_k=top_k):
        # Semantic candidates (pull extra so fusion has room to re-rank).
        query_vec = embed_query(query)
        semantic = store.search(repo_id, query_vec, top_k=top_k * 3)

        # Reciprocal-rank fusion of semantic rank + keyword rank.
        keyword_sorted = sorted(semantic, key=lambda h: _keyword_score(query, h), reverse=True)
        K = 60  # standard RRF constant
        fused: dict[str, tuple[float, SearchHit]] = {}
        for rank, h in enumerate(semantic):
            fused[h.chunk_id] = (1.0 / (K + rank), h)
        for rank, h in enumerate(keyword_sorted):
            prev = fused.get(h.chunk_id, (0.0, h))
            fused[h.chunk_id] = (prev[0] + 1.0 / (K + rank), h)

        ranked = sorted(fused.values(), key=lambda x: x[0], reverse=True)

        if settings.retrieval_dependency_expansion:
            hits = _expand_by_dependencies(repo_id, ranked, top_k)
        else:
            hits = [h for _, h in ranked[:top_k]]

        tracing.add_attrs(candidates=len(semantic), returned=len(hits),
                          top_paths=[h.path for h in hits[:5]])
        return RetrievedContext(hits=hits)


def _expand_by_dependencies(repo_id: str, ranked: list[tuple[float, SearchHit]],
                            top_k: int) -> list[SearchHit]:
    """For each file in the base top-k, insert (right after it) a file it IMPORTS
    that isn't already in the top-k — the root cause is imported by the symptom.
    Then trim back to top_k. Only imports (downstream deps) are followed and only
    the base top-k is touched, so naturally-ranked hits aren't reordered."""
    file_best: dict[str, tuple[float, SearchHit]] = {}
    order: list[str] = []
    for score, hit in ranked:
        if hit.path not in file_best:
            file_best[hit.path] = (score, hit)
            order.append(hit.path)
    base = order[:top_k]

    meta = registry.get(repo_id)
    repo_dir = meta.get("repo_dir") if meta else None
    if not repo_dir:
        return [file_best[p][1] for p in base]
    try:
        imports, _imported_by = build_graph(repo_dir)
    except Exception:
        return [file_best[p][1] for p in base]

    store = get_store()
    base_set = set(base)
    result: list[SearchHit] = []
    seen: set[str] = set()
    added: list[str] = []

    for path in base:
        result.append(file_best[path][1])
        seen.add(path)
        if len(added) >= _DEP_MAX_ADDS:
            continue
        for dep in sorted(imports.get(path, set())):
            if dep in base_set or dep in seen:
                continue
            hit = (file_best[dep][1] if dep in file_best
                   else (chunks[0] if (chunks := store.chunks_for_file(repo_id, dep)) else None))
            if hit is None:
                continue
            result.append(hit)
            seen.add(dep)
            added.append(dep)
            if len(added) >= _DEP_MAX_ADDS:
                break

    if added:
        tracing.add_attrs(dependency_added=added)
    return result[:top_k]
