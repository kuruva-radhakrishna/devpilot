"""DevPilot test runner: an isolated, secret-free service that runs a repo's tests.

POST /jobs        (Authorization: Bearer <RUNNER_TOKEN>)  body = a .tar.gz of the workspace
                  query: lang (python|node|go), test_path, timeout (seconds), install (0|1)
                  -> 202 {"job_id"}   (503 when every slot is busy)
GET  /jobs/{id}   -> {"status": running|done|error, "stage", "result"|"error"}
GET  /healthz     liveness, whether isolation is actually available, which toolchains exist

Why jobs and not one long request: installing dependencies plus a test run can take minutes,
and the host's edge gives up on a request that sends nothing for ~100 s. A submit returns at
once and the caller polls.

The service fails CLOSED: with no RUNNER_TOKEN, or when it cannot run children under a
different uid, /jobs refuses rather than run untrusted code unprotected. Set
RUNNER_ALLOW_NO_ISOLATION=1 only for local development and tests.
"""
from __future__ import annotations

import hmac
import os
import sys
import threading
import time
import uuid

from fastapi import FastAPI, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse

from app import sandbox

app = FastAPI(title="DevPilot runner", docs_url=None, redoc_url=None, openapi_url=None)

ISOLATED = sandbox.isolation_available()
ALLOW_NO_ISOLATION = os.environ.get("RUNNER_ALLOW_NO_ISOLATION") == "1"
JOB_TTL_SECONDS = 600


class Job:
    def __init__(self) -> None:
        self.id = uuid.uuid4().hex
        self.status = "running"
        self.stage = "queued"
        self.result: dict | None = None
        self.error: str | None = None
        self.created = time.time()


_jobs: dict[str, Job] = {}
_jobs_lock = threading.Lock()


def _prune() -> None:
    cutoff = time.time() - JOB_TTL_SECONDS
    for job_id in [j.id for j in _jobs.values() if j.status != "running" and j.created < cutoff]:
        _jobs.pop(job_id, None)


def _token() -> str:
    return os.environ.get("RUNNER_TOKEN", "")


@app.get("/healthz")
def healthz():
    return {
        "ok": True,
        "isolation": ISOLATED,
        "ready": bool(_token()) and (ISOLATED or ALLOW_NO_ISOLATION),
        "slots": sandbox.SLOTS,
        "python": sys.version.split()[0],
        "toolchains": sandbox.toolchains(),
        "network_isolated": False,
    }


def _authorize(request: Request) -> None:
    expected = _token()
    if not expected:
        raise HTTPException(503, "RUNNER_TOKEN is not configured on this runner.")
    header = request.headers.get("authorization", "")
    supplied = header[7:] if header.lower().startswith("bearer ") else ""
    if not hmac.compare_digest(supplied.encode(), expected.encode()):
        raise HTTPException(401, "Invalid runner token.")


def _work(job: Job, slot: int, archive: bytes, lang: str, test_path: str, timeout: float, install: bool) -> None:
    try:
        job.result = sandbox.run_job(
            slot=slot, archive=archive, lang=lang, test_path=test_path, timeout=timeout,
            install=install, isolated=ISOLATED, set_stage=lambda s: setattr(job, "stage", s))
        job.status = "done"
    except sandbox.RunnerError as exc:
        job.error, job.status = str(exc), "error"
    except Exception as exc:  # noqa: BLE001 — never leave a job hanging
        job.error, job.status = f"The runner failed unexpectedly ({type(exc).__name__}).", "error"
    job.stage = "finished"


@app.post("/jobs", status_code=202)
async def submit(request: Request, lang: str = "python", test_path: str = "", timeout: float = 60,
                 install: bool = False):
    _authorize(request)
    if not (ISOLATED or ALLOW_NO_ISOLATION):
        raise HTTPException(
            503,
            "This runner cannot isolate untrusted code here (it needs to run as root so it "
            "can drop to an unprivileged user), so it refuses to run anything.",
        )

    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > sandbox.MAX_UPLOAD_BYTES:
        raise HTTPException(413, "Workspace archive is too large.")
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > sandbox.MAX_UPLOAD_BYTES:
            raise HTTPException(413, "Workspace archive is too large.")

    try:
        lang = sandbox.validate_lang(lang)
        test_path = sandbox.validate_test_path(lang, test_path)
        if not sandbox.toolchains().get(lang):
            raise sandbox.RunnerError(f"This runner has no {lang} toolchain installed.")
        slot = await run_in_threadpool(sandbox.acquire_slot, 10.0)
    except sandbox.RunnerError as exc:
        return JSONResponse({"detail": str(exc)}, status_code=exc.status,
                            headers={"Retry-After": "10"} if exc.status == 503 else None)

    job = Job()
    with _jobs_lock:
        _prune()
        _jobs[job.id] = job
    threading.Thread(target=_work, name=f"job-{job.id[:8]}", daemon=True,
                     args=(job, slot, bytes(body), lang, test_path, timeout, install)).start()
    return {"job_id": job.id}


@app.get("/jobs/{job_id}")
def status(job_id: str, request: Request):
    _authorize(request)
    job = _jobs.get(job_id)
    if job is None:
        raise HTTPException(404, "No such job (it may have expired).")
    return {"status": job.status, "stage": job.stage, "result": job.result, "error": job.error}
