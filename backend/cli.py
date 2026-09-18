"""DevPilot command-line interface — use the agent without the web UI.

    python cli.py ingest https://github.com/some/repo
    python cli.py ingest ./path/to/local/repo
    python cli.py repos
    python cli.py ask <repo_id> "Why does login return 401?"
    python cli.py review path/to/change.diff
    python cli.py demo          # one-command live run on the bundled sample_app
    python cli.py traces        # list recent traces  (add an id to view one)

Handy for quick testing and for the eval loop.
"""
from __future__ import annotations

import argparse
import os
import sys

# Windows consoles default to a legacy code page (cp1252) that can't encode many
# characters Gemini may emit (smart quotes, arrows, etc.) or our own markers,
# which otherwise crashes output mid-run. Force UTF-8 where the runtime allows.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    except Exception:
        pass

from rich.console import Console

from app import registry
from app.agent.repair import run_repair
from app.agent.runtime import run_agent
from app.rag.ingest import ingest_repo, signature_matches
from app.review.reviewer import review_diff

# Plain ASCII markers only, so output is safe on any console.
console = Console()


def _resolve_repo(repo_id):
    """Return a repo's meta, re-ingesting first if the embedding config changed
    since it was indexed (e.g. after switching provider), so we never query a
    stale vector space. Exits if the repo is unknown."""
    meta = registry.get(repo_id)
    if not meta:
        console.print(f"[red]Unknown repo_id '{repo_id}'. Run `repos` to list.[/red]")
        sys.exit(1)
    if not signature_matches(meta):
        console.print("[yellow]Embedding config changed since this repo was indexed; "
                      "re-ingesting so retrieval uses the current provider...[/yellow]")
        summary = ingest_repo(meta.get("source") or meta["repo_dir"])
        registry.register(summary["repo_id"], summary)
        meta = registry.get(summary["repo_id"])
    return meta


def cmd_ingest(args):
    summary = ingest_repo(args.source)
    if summary.get("repo_dir"):
        registry.register(summary["repo_id"], summary)
    console.print(summary)
    console.print(f"\n[green]Ingested.[/green] Ask with:  python cli.py ask {summary['repo_id']} \"...\"")


def cmd_repos(_args):
    repos = registry.all_repos()
    if not repos:
        console.print("No repos ingested yet.")
        return
    for rid, meta in repos.items():
        console.print(f"[cyan]{rid}[/cyan]  files={meta.get('files')} chunks={meta.get('chunks')}  ({meta.get('source')})")


def cmd_ask(args):
    meta = _resolve_repo(args.repo_id)
    result = run_agent(args.repo_id, meta["repo_dir"], args.question, task=args.task)
    console.rule("Tool calls")
    for tc in result.tool_calls:
        console.print(f"[dim]{tc.name}({tc.args})[/dim]")
    console.rule("Answer")
    console.print(result.answer)


def cmd_demo(args):
    """Ingest the bundled sample_app and run the repair loop — the live smoke test."""
    sample = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "examples", "sample_app"))
    if not os.path.isdir(sample):
        console.print(f"[red]sample_app not found at {sample}[/red]")
        sys.exit(1)
    console.print(f"[bold]==>[/bold] Ingesting sample_app  ({sample})")
    summary = ingest_repo(sample)
    registry.register(summary["repo_id"], summary)
    console.print(f"    repo_id = [cyan]{summary['repo_id']}[/cyan]  "
                  f"({summary['files']} files, {summary['chunks']} chunks)\n")
    bug = ("Orders without an address crash with a KeyError; they should ship to "
           "'unknown' instead.")
    cmd_debug(argparse.Namespace(repo_id=summary["repo_id"], bug_report=bug, test_path=""))


def cmd_debug(args):
    meta = _resolve_repo(args.repo_id)
    console.print(f"[cyan]Investigating:[/cyan] {args.bug_report}\n")
    result = run_repair(args.repo_id, meta["repo_dir"], args.bug_report,
                        test_path=args.test_path)

    for it in result.iterations:
        tag = "[green][PASS][/green]" if it.passed else "[yellow][retry][/yellow]"
        console.print(f"{tag} Attempt {it.attempt}: {it.test_summary}")
        if it.files_changed:
            console.print(f"        changed: {', '.join(it.files_changed)}")
    console.rule("Result")
    verdict = "[green]FIX VALIDATED[/green]" if result.success else "[red]NOT FIXED[/red]"
    console.print(f"[bold]{verdict}[/bold]")
    console.print(f"[bold]Root cause:[/bold] {result.root_cause}")
    console.print(f"[bold]Explanation:[/bold] {result.explanation}")
    if result.files_changed:
        console.print(f"[bold]Files changed:[/bold] {', '.join(result.files_changed)}")
    if result.diff:
        console.rule("Patch")
        console.print(result.diff)
    if result.note:
        console.print(f"[dim]note: {result.note}[/dim]")


def cmd_traces(args):
    from app.obs import store, view
    if args.trace_id:
        tr = store.get_trace(args.trace_id)
        if not tr:
            console.print(f"[red]No trace '{args.trace_id}'[/red]")
            sys.exit(1)
        view.render(tr)
        return
    rows = store.list_traces()
    if not rows:
        console.print("No traces yet. Run `ask`, `debug`, or `demo` first.")
        return
    for r in rows:
        console.print(f"[cyan]{r['trace_id']}[/cyan]  {r['name']:<12}  "
                      f"{r['duration_ms']:.0f}ms  spans={r['spans']}  status={r['status']}")


def cmd_review(args):
    with open(args.diff_file, "r", encoding="utf-8") as fh:
        diff = fh.read()
    findings = review_diff(diff)
    for f in findings:
        console.print(f"[bold]{f.get('severity')}[/bold] {f.get('category')} "
                      f"{f.get('file')}:{f.get('line')} — {f.get('title')}")
        console.print(f"  {f.get('detail')}")
        console.print(f"  [green]fix:[/green] {f.get('suggestion')}\n")


def main():
    p = argparse.ArgumentParser(prog="devpilot")
    p.add_argument("--provider", default=None,
                   help="LLM provider: gemini (default) or ollama")
    sub = p.add_subparsers(required=True)

    pi = sub.add_parser("ingest"); pi.add_argument("source"); pi.set_defaults(fn=cmd_ingest)
    pr = sub.add_parser("repos"); pr.set_defaults(fn=cmd_repos)
    pa = sub.add_parser("ask")
    pa.add_argument("repo_id"); pa.add_argument("question")
    pa.add_argument("--task", default="debugging"); pa.set_defaults(fn=cmd_ask)
    pd = sub.add_parser("debug")
    pd.add_argument("repo_id"); pd.add_argument("bug_report")
    pd.add_argument("--test-path", default="", dest="test_path")
    pd.set_defaults(fn=cmd_debug)

    pdemo = sub.add_parser("demo"); pdemo.set_defaults(fn=cmd_demo)

    pt = sub.add_parser("traces")
    pt.add_argument("trace_id", nargs="?", default=None)
    pt.set_defaults(fn=cmd_traces)

    pv = sub.add_parser("review"); pv.add_argument("diff_file"); pv.set_defaults(fn=cmd_review)

    args = p.parse_args()
    if args.provider:
        from app.llm.client import set_provider
        set_provider(args.provider)
    args.fn(args)


if __name__ == "__main__":
    main()
