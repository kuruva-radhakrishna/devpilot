"""View DevPilot traces from the terminal.

    python -m app.obs.view                 # list recent traces
    python -m app.obs.view <trace_id>      # render one as a span tree

Each span shows its duration and key attributes (tokens, tool status, outcome),
so you can see exactly where time and tokens went across the agent lifecycle.
"""
from __future__ import annotations

import sys

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    except Exception:
        pass

from rich.console import Console
from rich.tree import Tree

from app.obs import store

console = Console()

# Attributes worth surfacing inline per span (kept short).
_SHOW = ("total_tokens", "passed", "outcome", "returned", "hits", "summary",
         "steps", "error", "n", "attempt")


def _label(span: dict) -> str:
    dur = span.get("duration_ms", 0)
    status = span.get("status", "ok")
    dot = "[red]●[/red]" if status == "error" else "[green]●[/green]"
    attrs = span.get("attributes", {})
    shown = " ".join(
        f"[dim]{k}={attrs[k]}[/dim]" for k in _SHOW if k in attrs and attrs[k] != ""
    )
    return f"{dot} [bold]{span['name']}[/bold] [cyan]{dur:.0f}ms[/cyan]  {shown}".rstrip()


def render(trace: dict) -> None:
    console.print(
        f"trace [bold]{trace['trace_id']}[/bold]  "
        f"{trace['name']}  {trace['duration_ms']:.0f}ms  "
        f"status={trace['status']}  spans={len(trace['spans'])}\n"
    )
    spans = trace["spans"]
    by_parent: dict = {}
    for s in spans:
        by_parent.setdefault(s.get("parent_id"), []).append(s)

    # The root span has parent_id == None.
    roots = by_parent.get(None, [])
    nodes: dict = {}

    def add(parent_tree, span):
        node = parent_tree.add(_label(span))
        nodes[span["span_id"]] = node
        for child in by_parent.get(span["span_id"], []):
            add(node, child)

    for root in roots:
        tree = Tree(_label(root))
        for child in by_parent.get(root["span_id"], []):
            add(tree, child)
        console.print(tree)


def main() -> None:
    if len(sys.argv) > 1:
        tr = store.get_trace(sys.argv[1])
        if not tr:
            console.print(f"[red]No trace '{sys.argv[1]}'[/red]")
            return
        render(tr)
        return
    rows = store.list_traces()
    if not rows:
        console.print("No traces yet. Run an agent or repair task first.")
        return
    for r in rows:
        console.print(
            f"[cyan]{r['trace_id']}[/cyan]  {r['name']:<12}  "
            f"{r['duration_ms']:.0f}ms  spans={r['spans']}  status={r['status']}"
        )


if __name__ == "__main__":
    main()
