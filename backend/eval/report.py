"""Render a benchmark run as a readable report."""
from __future__ import annotations

from rich.console import Console
from rich.table import Table

console = Console()


def print_retrieval_report(run: dict) -> None:
    agg = run["aggregates"]
    console.rule(f"DevPilot Retrieval-only Eval — run {run['run_id']}")
    console.print(f"provider: [cyan]{run.get('provider','?')}[/cyan]   "
                  f"embed: [cyan]{run.get('embed_model','?')}[/cyan]   "
                  f"top_k: {run['top_k']}   cases: {len(run['rows'])}\n")
    t = Table(title="Retrieval quality (no generation)")
    t.add_column("metric"); t.add_column("value")
    t.add_row("Recall@K", f"{agg['recall_at_k']*100:.0f}%")
    t.add_row("MRR", f"{agg['mrr']:.2f}")
    t.add_row("Relevant-file hit", f"{agg['file_hit']*100:.0f}%")
    console.print(t)

    tc = Table(title="By category")
    for col in ("category", "Recall@K", "MRR", "file hit"):
        tc.add_column(col)
    for cat, m in agg["per_category"].items():
        tc.add_row(cat, f"{m['recall_at_k']*100:.0f}%", f"{m['mrr']:.2f}",
                   f"{m['file_hit']*100:.0f}%")
    console.print(tc)


def print_report(run: dict) -> None:
    if run.get("mode") == "retrieval-only":
        print_retrieval_report(run)
        return
    agg = run["aggregates"]
    console.rule(f"DevPilot Evaluation — run {run['run_id']}")
    console.print(f"provider: [cyan]{run.get('provider','?')}[/cyan]   "
                  f"model: [cyan]{run['model']}[/cyan]   "
                  f"embed: [cyan]{run['embed_model']}[/cyan]   "
                  f"top_k: {run['top_k']}\n")

    # Per-mode repair comparison (baseline vs v1/v2/v3)
    per_mode = agg.get("per_mode") or agg.get("per_prompt", {})
    t = Table(title="Repair — baseline (no RAG) vs prompt versions")
    for col in ("mode", "tasks", "repair success", "tests passed",
                "patch gen", "patch applied", "avg iters", "avg ms", "tokens", "cost $"):
        t.add_column(col)
    for mode, m in per_mode.items():
        label = "[yellow]baseline[/yellow]" if mode == "baseline" else mode
        t.add_row(
            label, str(m["tasks"]),
            f"{m['repair_success_rate']*100:.0f}%",
            f"{m['tests_passed_rate']*100:.0f}%",
            f"{m['patch_generated_rate']*100:.0f}%",
            f"{m['patch_applied_rate']*100:.0f}%",
            f"{m['avg_iterations']:.1f}",
            str(m["avg_latency_ms"]),
            str(m["total_tokens"]),
            f"{m['total_cost_usd']:.4f}",
        )
    console.print(t)

    # The headline: lift from baseline to the best RAG mode.
    if "baseline" in per_mode and agg.get("best_mode") in per_mode:
        b = per_mode["baseline"]["repair_success_rate"]
        v = per_mode[agg["best_mode"]]["repair_success_rate"]
        console.print(
            f"[bold]RAG + tools lift:[/bold] repair success "
            f"{b*100:.0f}% (baseline) → {v*100:.0f}% ({agg['best_mode']})\n"
        )

    # Repair success by category (best mode)
    if agg.get("per_category"):
        tc = Table(title=f"Repair success by category — {agg.get('best_mode')}")
        tc.add_column("category"); tc.add_column("success")
        for cat, val in agg["per_category"].items():
            tc.add_row(cat, f"{val*100:.0f}%")
        console.print(tc)

    # Failure analysis — WHERE the pipeline breaks (retrieval vs reasoning vs patch)
    fb = agg.get("failure_breakdown") or {}
    if fb:
        modes = list(fb.keys())
        reasons = sorted({r for m in fb.values() for r in m},
                         key=lambda r: (r == "success", r))  # success last
        tf = Table(title="Failure analysis — outcomes by mode (case counts)")
        tf.add_column("outcome")
        for m in modes:
            tf.add_column("baseline" if m == "baseline" else m)
        for reason in reasons:
            style = "green" if reason == "success" else ("yellow" if reason == "retrieval_failure" else "")
            row = [f"[{style}]{reason}[/{style}]" if style else reason]
            for m in modes:
                row.append(str(fb[m].get(reason, 0)))
            tf.add_row(*row)
        console.print(tf)

    # Pipeline-wide metrics
    t2 = Table(title="Pipeline metrics")
    t2.add_column("metric"); t2.add_column("value")
    t2.add_row("Retrieval Recall@K", f"{agg['retrieval_recall_at_k']*100:.0f}%")
    t2.add_row("Retrieval MRR", f"{agg['retrieval_mrr']:.2f}")
    t2.add_row("QA tool-call success", f"{agg['qa_tool_success']*100:.0f}%")
    t2.add_row("QA groundedness", f"{agg['qa_groundedness']*100:.0f}%")
    t2.add_row("QA task success", f"{agg['qa_task_success']*100:.0f}%")
    console.print(t2)

    # Per-case detail
    t3 = Table(title="Per-case detail")
    for col in ("id", "type", "category", "prompt", "recall", "success", "iters", "ms"):
        t3.add_column(col)
    for r in run["rows"]:
        t3.add_row(
            r["id"], r["type"], r["category"], r["prompt_version"],
            f"{r['recall_at_k']*100:.0f}%",
            "[green]yes[/green]" if r["task_success"] else "[red]no[/red]",
            str(r["iterations"]) if r["type"] == "repair" else "-",
            str(r["latency_ms"]),
        )
    console.print(t3)
