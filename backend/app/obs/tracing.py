"""Lifecycle tracing for DevPilot — a small, dependency-free tracer.

Every top-level operation (an agent turn, a repair run) opens a TRACE; nested
work opens SPANS: retrieval, llm.generate, tool.<name>, repair.iteration,
run_tests, apply_patch, ... Each span records timing, token counts, tool
args/status, iteration, and errors. A finished trace is persisted as JSON so it
can be inspected (see app/obs/view.py and /api/traces).

Why hand-rolled instead of OpenTelemetry/LangSmith? It keeps the project
self-contained and makes the model of "trace -> spans with attributes" explicit
and easy to explain. The span/attribute shape mirrors OTel, so exporting to an
OTel/LangSmith backend later is an additive change in _persist().

REDACTION: attributes are sanitized before storage — long strings are truncated
and keys that look like secrets are dropped — so traces never contain API keys or
whole repository files.
"""
from __future__ import annotations

import json
import os
import re
import time
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import asdict, dataclass, field

from app.config import get_settings

_SECRET_RE = re.compile(r"(api[_-]?key|token|secret|password|authorization|bearer)", re.I)
_MAX_STR = 600
_MAX_ITEMS = 40


@dataclass
class Span:
    span_id: str
    parent_id: str | None
    name: str
    start_ms: float
    duration_ms: float = 0.0
    status: str = "ok"          # "ok" | "error"
    error: str | None = None
    attributes: dict = field(default_factory=dict)


@dataclass
class Trace:
    trace_id: str
    name: str
    start_ms: float
    duration_ms: float = 0.0
    status: str = "ok"
    spans: list = field(default_factory=list)  # list[Span]


_current: ContextVar[Trace | None] = ContextVar("dp_trace", default=None)
_stack: ContextVar[tuple] = ContextVar("dp_span_stack", default=())


# --------------------------------------------------------------------------- #
# Redaction
# --------------------------------------------------------------------------- #
def _redact(v):
    if isinstance(v, str):
        return v if len(v) <= _MAX_STR else v[:_MAX_STR] + "…[truncated]"
    if isinstance(v, dict):
        out = {}
        for k, val in list(v.items())[:_MAX_ITEMS]:
            out[k] = "<redacted>" if _SECRET_RE.search(str(k)) else _redact(val)
        return out
    if isinstance(v, (list, tuple)):
        return [_redact(x) for x in list(v)[:_MAX_ITEMS]]
    return v


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #
def current_trace() -> Trace | None:
    return _current.get()


def add_attrs(**kw) -> None:
    """Attach attributes to the innermost active span (no-op if not tracing)."""
    stack = _stack.get()
    if stack:
        stack[-1].attributes.update({k: _redact(v) for k, v in kw.items()})


@contextmanager
def span(name: str, **attrs):
    """Open a child span under the current trace. No-op if tracing is off."""
    tr = _current.get()
    if tr is None:
        yield None
        return
    stack = _stack.get()
    parent = stack[-1].span_id if stack else None
    s = Span(
        span_id=uuid.uuid4().hex[:12], parent_id=parent, name=name,
        start_ms=time.time() * 1000,
        attributes={k: _redact(v) for k, v in attrs.items()},
    )
    tr.spans.append(s)
    token = _stack.set(stack + (s,))
    t0 = time.perf_counter()
    try:
        yield s
    except Exception as exc:  # noqa: BLE001
        s.status = "error"
        s.error = str(exc)[:300]
        tr.status = "error"
        raise
    finally:
        s.duration_ms = round((time.perf_counter() - t0) * 1000, 2)
        _stack.reset(token)


@contextmanager
def start_trace(name: str, **attrs):
    """Open a top-level trace. Yields the Trace (or None if tracing disabled).

    If a trace is already active, this degrades to a span so nested top-level
    operations don't create a second trace file.
    """
    if not get_settings().trace_enabled:
        yield None
        return
    if _current.get() is not None:
        with span(name, **attrs):
            yield _current.get()
        return

    tr = Trace(trace_id=uuid.uuid4().hex[:16], name=name, start_ms=time.time() * 1000)
    ctok = _current.set(tr)
    stok = _stack.set(())
    try:
        with span(name, **attrs):
            yield tr
    finally:
        tr.duration_ms = tr.spans[0].duration_ms if tr.spans else 0.0
        _persist(tr)
        _current.reset(ctok)
        _stack.reset(stok)


# --------------------------------------------------------------------------- #
# Persistence
# --------------------------------------------------------------------------- #
def _persist(tr: Trace) -> str | None:
    try:
        d = get_settings().traces_dir
        os.makedirs(d, exist_ok=True)
        path = os.path.join(d, f"{tr.trace_id}.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(asdict(tr), fh, indent=2)
        return path
    except Exception:
        return None  # tracing must never break the actual request
