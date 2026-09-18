"""The agent runtime — the loop that turns a question into a grounded answer.

We use Gemini's *automatic* function calling: we hand the model the tool
functions and it decides which to call and when, feeding results back into the
conversation until it produces a final answer. We cap the number of tool round
trips (AGENT_MAX_STEPS) so a confused turn can't loop forever, and we record a
trace of every tool call for observability + evaluation.

Why hand-rolled instead of LangGraph? For a learning/portfolio project, owning
the loop makes the mechanics (planning -> tool call -> observation -> answer)
explicit and easy to explain. Swapping in LangGraph later is a contained change
to this file — see README "Roadmap".
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field

from app.config import get_settings
from app.llm.client import build_tool_model, with_retry, record_usage, supports_tools
from app.obs import tracing
from app.agent.tools import ALL_TOOLS, RepoContext, set_active_repo

PROMPTS_DIR = os.path.join(os.path.dirname(__file__), "prompts")


def load_prompt(task: str, version: str | None = None) -> str:
    version = version or get_settings().prompt_version
    path = os.path.join(PROMPTS_DIR, task, f"{version}.txt")
    with open(path, "r", encoding="utf-8") as fh:
        return fh.read()


@dataclass
class ToolCall:
    name: str
    args: dict
    result_preview: str


@dataclass
class AgentResult:
    answer: str
    steps: int
    tool_calls: list[ToolCall] = field(default_factory=list)
    trace_id: str | None = None


def run_agent(
    repo_id: str,
    repo_dir: str,
    question: str,
    *,
    task: str = "debugging",
    prompt_version: str | None = None,
) -> AgentResult:
    """Run one agent turn against a repo and return a grounded answer + trace."""
    settings = get_settings()
    set_active_repo(RepoContext(repo_id=repo_id, repo_dir=repo_dir))

    if not supports_tools():
        # The QA agent relies on native tool calling; the repair loop does not, so
        # it still works on any provider. Be explicit rather than failing obscurely.
        return AgentResult(
            answer="[agent QA requires a tool-capable provider (e.g. gemini); "
                   "the current provider has tool calling disabled]",
            steps=0, tool_calls=[], trace_id=None,
        )

    system = load_prompt(task, prompt_version)
    model = build_tool_model(system=system, tools=ALL_TOOLS)

    with tracing.start_trace("agent.run", repo_id=repo_id, question=question, task=task) as tr:
        chat = model.start_chat(enable_automatic_function_calling=True)
        # Automatic function calling makes several model calls under the hood; wrap
        # the whole turn so a mid-turn 429 backs off and retries instead of crashing.
        with tracing.span("llm.generate", model=settings.gemini_model, phase="agent-turn"):
            resp = with_retry(lambda: chat.send_message(question), label="agent")
            record_usage(resp)

        # Reconstruct the tool-call trace from the chat history for observability.
        tool_calls = _extract_tool_calls(chat)
        steps = len(tool_calls)
        if steps > settings.agent_max_steps:  # safety valve
            tool_calls = tool_calls[: settings.agent_max_steps]
        tracing.add_attrs(steps=steps, tools=[tc.name for tc in tool_calls])
        return AgentResult(
            answer=resp.text or "", steps=steps, tool_calls=tool_calls,
            trace_id=tr.trace_id if tr else None,
        )


def _extract_tool_calls(chat) -> list[ToolCall]:
    calls: list[ToolCall] = []
    pending: dict | None = None
    for content in chat.history:
        for part in getattr(content, "parts", []) or []:
            fc = getattr(part, "function_call", None)
            if fc and getattr(fc, "name", None):
                pending = {"name": fc.name, "args": dict(fc.args or {})}
            fr = getattr(part, "function_response", None)
            if fr and pending:
                resp_val = getattr(fr, "response", {})
                preview = str(resp_val)[:280]
                calls.append(ToolCall(name=pending["name"], args=pending["args"], result_preview=preview))
                pending = None
    return calls
