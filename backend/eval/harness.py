"""DevPilot evaluation harness.

Runs the benchmark and measures the WHOLE pipeline, not just the final answer:

  RETRIEVAL   Recall@K, MRR, relevant-file hit           (both case types)
  TOOL USE    tool-call count, tool-success rate          (QA cases)
  AGENT       task completion, iterations/task            (both)
  REPAIR      patch generated / applied / tests passed    (repair cases)
  LLM         groundedness proxy (expected keywords)      (QA cases)
  PERF        latency, tokens, estimated cost             (all)

Headline metric: Repair Success Rate = validated fixes / total repair bugs.

Repair cases are run once per prompt version so you can compare v1/v2/v3
objectively. QA cases are prompt-independent (they use the debugging prompt) and
run once. Every run is persisted to eval/results/<timestamp>.json.

Numbers come only from real runs — nothing here fabricates results.
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, dataclass, field

from app import registry
from app.agent.repair import run_repair
from app.agent.runtime import run_agent
from app.config import get_settings
from app.llm import usage
from app.llm.client import embed_signature, provider_name
from app.rag.ingest import ingest_repo, make_repo_id, signature_matches
from app.rag.retriever import retrieve

_HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(os.path.dirname(_HERE))  # devpilot/
BENCHMARK = os.path.join(_HERE, "benchmark.json")
RESULTS_DIR = os.path.join(_HERE, "results")
TOP_K = 5


@dataclass
class Row:
    id: str
    type: str
    category: str
    prompt_version: str        # "baseline" | "v1" | "v2" | "v3" | "-" (qa)
    # retrieval
    recall_at_k: float = 0.0
    mrr: float = 0.0
    file_hit: int = 0
    # tool use (qa)
    tool_calls: int = 0
    tool_success: float = 0.0
    # repair
    iterations: int = 0
    patch_generated: int = 0
    patch_applied: int = 0
    tests_before: str = ""
    tests_after: str = ""
    tests_passed: int = 0
    # llm (qa)
    groundedness: float = 0.0
    # outcome
    task_success: int = 0
    failure_reason: str = ""    # success | retrieval_failure | no_patch_generated | ...
    # perf
    latency_ms: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    tokens: int = 0
    cost_usd: float = 0.0
    error: str = ""
    trace_id: str = ""
    note: str = ""


# --------------------------------------------------------------------------- #
# Setup
# --------------------------------------------------------------------------- #
def load_benchmark() -> dict:
    with open(BENCHMARK, "r", encoding="utf-8") as fh:
        return json.load(fh)


def ensure_ingested(rel_path: str) -> tuple[str, str]:
    """Ingest a fixture repo if it isn't indexed OR if the embedding config
    changed (different provider/model → different vector space). Returns
    (repo_id, dir)."""
    abs_path = os.path.abspath(os.path.join(PROJECT_ROOT, rel_path))
    repo_id = make_repo_id(abs_path)
    meta = registry.get(repo_id)
    if meta and os.path.isdir(meta.get("repo_dir", "")):
        if signature_matches(meta):
            return repo_id, meta["repo_dir"]
        sig = embed_signature()
        print(
            "Embedding configuration changed:\n"
            f"  existing: {meta.get('provider')} / {meta.get('embedding_model')}\n"
            f"  current:  {sig['provider']} / {sig['embedding_model']}\n"
            f"Re-ingesting {repo_id} ..."
        )
    summary = ingest_repo(abs_path)
    registry.register(summary["repo_id"], summary)
    return summary["repo_id"], summary["repo_dir"]


# --------------------------------------------------------------------------- #
# Metric helpers
# --------------------------------------------------------------------------- #
def _path_match(expected: str, got: str) -> bool:
    e, g = expected.replace("\\", "/"), got.replace("\\", "/")
    return e == g or g.endswith(e) or e.endswith(g)


def eval_retrieval(repo_id: str, query: str, expected_files: list[str], k: int = TOP_K):
    hits = retrieve(repo_id, query, top_k=k).hits
    paths = [h.path for h in hits]
    hit_count = sum(1 for e in expected_files if any(_path_match(e, p) for p in paths))
    recall = hit_count / len(expected_files) if expected_files else 0.0
    mrr = 0.0
    for rank, p in enumerate(paths, 1):
        if any(_path_match(e, p) for e in expected_files):
            mrr = 1.0 / rank
            break
    file_hit = 1 if hit_count > 0 else 0
    return recall, mrr, file_hit


def _keyword_ratio(text: str, keywords: list[str]) -> float:
    if not keywords:
        return 0.0
    low = (text or "").lower()
    return sum(1 for kw in keywords if kw.lower() in low) / len(keywords)


_ERR_MARKERS = ("not found", "no matching", "error", "escapes repository", "(empty")


def _tool_success_rate(result) -> float:
    if not result.tool_calls:
        return 0.0
    ok = sum(
        1 for tc in result.tool_calls
        if not any(m in tc.result_preview.lower() for m in _ERR_MARKERS)
    )
    return ok / len(result.tool_calls)


# --------------------------------------------------------------------------- #
# Case runners
# --------------------------------------------------------------------------- #
def classify_repair(*, baseline: bool, file_hit: int, patch_generated: int,
                    patch_applied: int, tests_passed: int, correct_file: bool) -> str:
    """Bucket a repair outcome so aggregate failures answer *where* the pipeline
    breaks — retrieval vs reasoning vs patch vs validation."""
    if tests_passed and correct_file:
        return "success"
    if tests_passed and not correct_file:
        return "wrong_file"                 # tests green but changed the wrong file
    if not baseline and not file_hit:
        return "retrieval_failure"          # the relevant file was never retrieved
    if not patch_generated:
        return "no_patch_generated"         # model produced no valid structured patch
    if not patch_applied:
        return "patch_not_applied"          # patch had invalid line ranges / paths
    return "applied_but_tests_fail"         # patch applied, tests still red (reasoning/quality)


def run_repair_case(case: dict, repo_id: str, repo_dir: str, mode: str) -> Row:
    """mode is "baseline" (no RAG) or a prompt version ("v1"/"v2"/"v3")."""
    test_path = case.get("test_path", "")
    baseline = mode == "baseline"
    pv = "v1" if baseline else mode

    # Retrieval quality is a property of RAG, so only measure it for non-baseline.
    if baseline:
        recall = mrr = 0.0
        file_hit = 0
    else:
        recall, mrr, file_hit = eval_retrieval(repo_id, case["bug"], case["expected_files"])

    usage.reset()
    t0 = time.perf_counter()
    result = run_repair(repo_id, repo_dir, case["bug"], test_path=test_path,
                        prompt_version=pv, retrieval=not baseline)
    latency = int((time.perf_counter() - t0) * 1000)
    u = usage.snapshot()

    correct_file = any(
        any(_path_match(e, f) for f in result.files_changed)
        for e in case["expected_files"]
    )
    reason = classify_repair(
        baseline=baseline, file_hit=file_hit,
        patch_generated=int(result.patch_generated),
        patch_applied=int(result.patch_applied),
        tests_passed=int(result.success), correct_file=correct_file,
    )
    return Row(
        id=case["id"], type="repair", category=case["category"], prompt_version=mode,
        recall_at_k=recall, mrr=mrr, file_hit=file_hit,
        iterations=len(result.iterations),
        patch_generated=int(result.patch_generated),
        patch_applied=int(result.patch_applied),
        tests_before=result.tests_before, tests_after=result.tests_after,
        tests_passed=int(result.success),
        task_success=int(result.success and correct_file),
        failure_reason=reason,
        latency_ms=latency,
        input_tokens=u.prompt_tokens, output_tokens=u.output_tokens, tokens=u.total_tokens,
        cost_usd=round(u.cost_usd(), 6),
        trace_id=result.trace_id or "",
        note=result.note or ("wrong file" if result.success and not correct_file else ""),
    )


def run_qa_case(case: dict, repo_id: str, repo_dir: str) -> Row:
    recall, mrr, file_hit = eval_retrieval(repo_id, case["question"], case["expected_files"])
    usage.reset()
    t0 = time.perf_counter()
    result = run_agent(repo_id, repo_dir, case["question"], task="debugging")
    latency = int((time.perf_counter() - t0) * 1000)
    u = usage.snapshot()

    grounded = _keyword_ratio(result.answer, case.get("expected_keywords", []))
    answer_cites = any(
        any(_path_match(e, tok) for tok in result.answer.replace("`", " ").split())
        for e in case["expected_files"]
    )
    task_success = int((file_hit or answer_cites) and grounded >= 0.5)
    return Row(
        id=case["id"], type="qa", category=case["category"], prompt_version="-",
        recall_at_k=recall, mrr=mrr, file_hit=file_hit,
        tool_calls=result.steps, tool_success=_tool_success_rate(result),
        groundedness=grounded, task_success=task_success,
        latency_ms=latency,
        input_tokens=u.prompt_tokens, output_tokens=u.output_tokens, tokens=u.total_tokens,
        cost_usd=round(u.cost_usd(), 6), trace_id=result.trace_id or "",
    )


# --------------------------------------------------------------------------- #
# Run + persist
# --------------------------------------------------------------------------- #
def run_benchmark(prompt_versions: list[str], only: str | None = None,
                  include_baseline: bool = True) -> dict:
    bench = load_benchmark()
    cases = bench["cases"]
    if only:
        cases = [c for c in cases if c["id"] == only or c["category"] == only]

    # Ingest every referenced repo once.
    repo_ctx: dict[str, tuple[str, str]] = {}
    for c in cases:
        key = c["repo"]
        if key not in repo_ctx:
            print(f"[ingest] {key} ...")
            repo_ctx[key] = ensure_ingested(bench["repos"][key])

    rows: list[Row] = []

    def _safe(case, pv, fn):
        """Run one case; on error (e.g. quota exhausted) record it and continue so
        a partial run still yields a report instead of losing everything."""
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001
            short = str(exc).splitlines()[0][:150]
            print(f"[error]  {case['id']} [{pv}]: {short}")
            return Row(id=case["id"], type=case["type"], category=case["category"],
                       prompt_version=pv, error=short, note=f"error: {short}")

    # QA cases: prompt-independent, run once.
    for c in cases:
        if c["type"] != "qa":
            continue
        rid, rdir = repo_ctx[c["repo"]]
        print(f"[qa]     {c['id']}")
        rows.append(_safe(c, "-", lambda c=c, rid=rid, rdir=rdir: run_qa_case(c, rid, rdir)))

    # Repair cases: baseline (no RAG) first, then once per prompt version.
    modes = (["baseline"] if include_baseline else []) + list(prompt_versions)
    for mode in modes:
        for c in cases:
            if c["type"] != "repair":
                continue
            rid, rdir = repo_ctx[c["repo"]]
            print(f"[repair] {c['id']}  ({mode})")
            rows.append(_safe(c, mode, lambda c=c, rid=rid, rdir=rdir, mode=mode:
                              run_repair_case(c, rid, rdir, mode)))

    run = {
        "run_id": time.strftime("%Y%m%d-%H%M%S"),
        "provider": provider_name(),
        "model": _model_label(),
        "embed_model": _embed_label(),
        "modes": modes,
        "prompt_versions": prompt_versions,
        "top_k": TOP_K,
        "rows": [asdict(r) for r in rows],
    }
    run["aggregates"] = aggregate(run)
    _persist(run)
    return run


def aggregate(run: dict) -> dict:
    rows = run["rows"]
    repair = [r for r in rows if r["type"] == "repair"]
    qa = [r for r in rows if r["type"] == "qa"]

    def mean(xs):
        return round(sum(xs) / len(xs), 3) if xs else 0.0

    modes = run.get("modes") or run.get("prompt_versions", [])
    per_mode = {}
    for mode in modes:
        rp = [r for r in repair if r["prompt_version"] == mode]
        per_mode[mode] = {
            "tasks": len(rp),
            "repair_success_rate": mean([r["task_success"] for r in rp]),
            "tests_passed_rate": mean([r["tests_passed"] for r in rp]),
            "patch_generated_rate": mean([r["patch_generated"] for r in rp]),
            "patch_applied_rate": mean([r["patch_applied"] for r in rp]),
            "avg_iterations": mean([r["iterations"] for r in rp]),
            "avg_latency_ms": int(mean([r["latency_ms"] for r in rp])),
            "total_tokens": sum(r["tokens"] for r in rp),
            "total_cost_usd": round(sum(r["cost_usd"] for r in rp), 4),
        }

    # Repair success by category, for the best non-baseline mode (last version).
    versions = [m for m in modes if m != "baseline"]
    best = versions[-1] if versions else (modes[-1] if modes else None)
    per_category = {}
    if best:
        cats = sorted({r["category"] for r in repair})
        for cat in cats:
            rc = [r for r in repair if r["category"] == cat and r["prompt_version"] == best]
            if rc:
                per_category[cat] = mean([r["task_success"] for r in rc])

    # Failure taxonomy per mode: where does the pipeline break?
    failure_breakdown = {}
    for mode in modes:
        rp = [r for r in repair if r["prompt_version"] == mode]
        counts: dict[str, int] = {}
        for r in rp:
            counts[r.get("failure_reason") or "unknown"] = \
                counts.get(r.get("failure_reason") or "unknown", 0) + 1
        failure_breakdown[mode] = dict(sorted(counts.items(), key=lambda kv: -kv[1]))

    # Retrieval metrics only meaningful for RAG rows (exclude baseline).
    rag_rows = [r for r in rows if r["prompt_version"] != "baseline"]
    return {
        "per_mode": per_mode,
        "per_category": per_category,
        "failure_breakdown": failure_breakdown,
        "best_mode": best,
        "retrieval_recall_at_k": mean([r["recall_at_k"] for r in rag_rows]),
        "retrieval_mrr": mean([r["mrr"] for r in rag_rows]),
        "qa_tool_success": mean([r["tool_success"] for r in qa]),
        "qa_groundedness": mean([r["groundedness"] for r in qa]),
        "qa_task_success": mean([r["task_success"] for r in qa]),
    }


def _model_label() -> str:
    s = get_settings()
    return s.ollama_model if provider_name() == "ollama" else s.gemini_model


def _embed_label() -> str:
    s = get_settings()
    return s.ollama_embed_model if provider_name() == "ollama" else s.gemini_embed_model


def run_retrieval_eval(only: str | None = None) -> dict:
    """Retrieval-only evaluation across all cases — NO generation, NO repair.

    Exploits the deterministic fixtures: we know the relevant file for each case,
    so we can score Recall@K / MRR / file-hit from retrieval alone. Only needs
    embeddings (cheap, and free on a local provider), so it runs even when
    generation quota is exhausted.
    """
    bench = load_benchmark()
    cases = bench["cases"]
    if only:
        cases = [c for c in cases if c["id"] == only or c["category"] == only]

    repo_ctx: dict[str, tuple[str, str]] = {}
    for c in cases:
        if c["repo"] not in repo_ctx:
            print(f"[ingest] {c['repo']} ...")
            repo_ctx[c["repo"]] = ensure_ingested(bench["repos"][c["repo"]])

    rows = []
    for c in cases:
        rid, _ = repo_ctx[c["repo"]]
        query = c.get("bug") or c.get("question", "")
        print(f"[retrieval] {c['id']}")
        recall, mrr, file_hit = eval_retrieval(rid, query, c["expected_files"])
        rows.append({"id": c["id"], "type": c["type"], "category": c["category"],
                     "recall_at_k": recall, "mrr": mrr, "file_hit": file_hit})

    def mean(xs):
        return round(sum(xs) / len(xs), 3) if xs else 0.0

    cats = sorted({r["category"] for r in rows})
    per_category = {cat: {
        "recall_at_k": mean([r["recall_at_k"] for r in rows if r["category"] == cat]),
        "mrr": mean([r["mrr"] for r in rows if r["category"] == cat]),
        "file_hit": mean([r["file_hit"] for r in rows if r["category"] == cat]),
    } for cat in cats}

    run = {
        "run_id": time.strftime("%Y%m%d-%H%M%S") + "-retrieval",
        "provider": provider_name(),
        "embed_model": _embed_label(),
        "top_k": TOP_K,
        "mode": "retrieval-only",
        "rows": rows,
        "aggregates": {
            "recall_at_k": mean([r["recall_at_k"] for r in rows]),
            "mrr": mean([r["mrr"] for r in rows]),
            "file_hit": mean([r["file_hit"] for r in rows]),
            "per_category": per_category,
        },
    }
    _persist(run)
    return run


def _persist(run: dict) -> str:
    os.makedirs(RESULTS_DIR, exist_ok=True)
    path = os.path.join(RESULTS_DIR, f"{run['run_id']}.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(run, fh, indent=2)
    return path
