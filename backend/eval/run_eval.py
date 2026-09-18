"""Run the DevPilot benchmark and print the evaluation report.

    # run repair cases against one prompt version (+ all QA cases):
    python -m eval.run_eval

    # compare prompt versions objectively (the prompt-engineering story):
    python -m eval.run_eval --prompt-versions v1,v2,v3

    # a single case or one category (fast iteration):
    python -m eval.run_eval --only repair_overdraft
    python -m eval.run_eval --only repository-qa

    # re-print a saved run without re-running:
    python -m eval.run_eval --report eval/results/<run_id>.json

Uses the configured Gemini model + sandbox. With no Docker, run with
SANDBOX_BACKEND=local (fine for the trusted bundled fixtures).
"""
from __future__ import annotations

import argparse
import json
import sys

# Windows consoles: force UTF-8 so rich output never crashes on encoding.
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    except Exception:
        pass

from app.llm.client import set_provider
from eval.harness import run_benchmark, run_retrieval_eval
from eval.report import print_report


def main() -> None:
    p = argparse.ArgumentParser(prog="devpilot-eval")
    p.add_argument("--provider", default=None,
                   help="LLM provider for this run: gemini (default) or ollama")
    p.add_argument("--prompt-versions", default="v1",
                   help="comma-separated patch prompt versions, e.g. v1,v2,v3")
    p.add_argument("--only", default=None, help="run a single case id or category")
    p.add_argument("--no-baseline", action="store_true",
                   help="skip the no-RAG baseline mode")
    p.add_argument("--retrieval-only", action="store_true",
                   help="score retrieval (Recall@K/MRR/file-hit) with NO generation")
    p.add_argument("--report", default=None, help="re-print a saved run JSON and exit")
    args = p.parse_args()

    if args.report:
        with open(args.report, "r", encoding="utf-8") as fh:
            print_report(json.load(fh))
        return

    if args.provider:
        set_provider(args.provider)

    if args.retrieval_only:
        run = run_retrieval_eval(only=args.only)
        print()
        print_report(run)
        return

    versions = [v.strip() for v in args.prompt_versions.split(",") if v.strip()]
    run = run_benchmark(versions, only=args.only, include_baseline=not args.no_baseline)
    print()
    print_report(run)


if __name__ == "__main__":
    main()
