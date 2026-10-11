"""Background repair jobs.

A repair (baseline tests, retrieval, up to a few model calls, test re-runs) can take
longer than a free host allows one HTTP request to live, and the runner may need a
minute to wake first. So `POST /api/agent/debug` starts a job and returns at once, and
the browser polls `GET /api/agent/debug/{job_id}` for live progress and the result.

State is in memory, on purpose: jobs are short-lived, a restart simply ends them, and
the finished outcome is also saved to the chat history (the durable record). That makes
this correct for a single instance (Render's free tier); scaling out would need a
shared store.
"""
from __future__ import annotations

import threading
import time
import uuid
from typing import Any, Callable

MAX_RUNNING_TOTAL = 2          # matches the runner's slots
MAX_PER_OWNER = 1
JOB_TTL_SECONDS = 3600
MAX_EVENTS = 200


class JobRefused(Exception):
    """Raised when a job can't be started right now; `message` is user-presentable."""


class Job:
    def __init__(self, owner: str, repo_id: str):
        self.id = uuid.uuid4().hex[:16]
        self.owner = owner
        self.repo_id = repo_id
        self.status = "running"          # running | done | error
        self.events: list[dict] = []
        self.result: dict | None = None
        self.error: str | None = None
        self.created = time.time()
        self.finished: float | None = None
        self._lock = threading.Lock()

    def add(self, event: dict) -> None:
        with self._lock:
            if len(self.events) < MAX_EVENTS:
                self.events.append({"t": round(time.time() - self.created, 1), **event})

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "job_id": self.id, "status": self.status, "events": list(self.events),
                "result": self.result, "error": self.error,
                "elapsed": round((self.finished or time.time()) - self.created, 1),
            }


class JobStore:
    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()

    def _prune(self) -> None:
        cutoff = time.time() - JOB_TTL_SECONDS
        for job_id in [j.id for j in self._jobs.values() if j.finished and j.finished < cutoff]:
            self._jobs.pop(job_id, None)

    def running(self, owner: str | None = None) -> int:
        with self._lock:
            return sum(1 for j in self._jobs.values()
                       if j.status == "running" and (owner is None or j.owner == owner))

    def start(self, owner: str, repo_id: str, target: Callable[[Job], Any]) -> Job:
        """Run `target(job)` on a background thread. `target` returns the result dict."""
        with self._lock:
            self._prune()
            running = [j for j in self._jobs.values() if j.status == "running"]
            if sum(1 for j in running if j.owner == owner) >= MAX_PER_OWNER:
                raise JobRefused("A repair is already running for you. Wait for it to finish.")
            if len(running) >= MAX_RUNNING_TOTAL:
                raise JobRefused("The repair runner is busy with other people's jobs. Try again in a minute.")
            job = Job(owner, repo_id)
            self._jobs[job.id] = job

        def worker() -> None:
            try:
                job.result = target(job)
                job.status = "done"
            except Exception as exc:  # noqa: BLE001 — becomes the job's error
                from app.llm.client import describe_error
                job.error = describe_error(exc)
                job.status = "error"
            finally:
                job.finished = time.time()

        threading.Thread(target=worker, name=f"repair-{job.id}", daemon=True).start()
        return job

    def get(self, job_id: str, owner: str) -> Job | None:
        """The job, only if it belongs to `owner` (so ids can't be used to peek)."""
        with self._lock:
            job = self._jobs.get(job_id)
        return job if job and job.owner == owner else None

    def clear(self) -> None:
        with self._lock:
            self._jobs.clear()


store = JobStore()
