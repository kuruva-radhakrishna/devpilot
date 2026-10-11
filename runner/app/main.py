"""DevPilot test runner: an isolated, secret-free service that runs a repo's pytest suite.

POST /run   (Authorization: Bearer <RUNNER_TOKEN>)  body = a .tar.gz of the workspace
            query: test_path (optional), timeout (seconds)
GET  /healthz  liveness + whether isolation is actually available

The service fails CLOSED: with no RUNNER_TOKEN, or when it cannot run children under a
different uid, /run refuses rather than run untrusted code unprotected. Set
RUNNER_ALLOW_NO_ISOLATION=1 only for local development and tests.
"""
from __future__ import annotations

import hmac
import os
import sys

from fastapi import FastAPI, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse

from app import sandbox

app = FastAPI(title="DevPilot runner", docs_url=None, redoc_url=None, openapi_url=None)

ISOLATED = sandbox.isolation_available()
ALLOW_NO_ISOLATION = os.environ.get("RUNNER_ALLOW_NO_ISOLATION") == "1"


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


@app.post("/run")
async def run(request: Request, test_path: str = "", timeout: float = 60):
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
        result = await run_in_threadpool(
            sandbox.run_pytest, bytes(body), test_path, timeout, isolated=ISOLATED)
    except sandbox.RunnerError as exc:
        return JSONResponse({"detail": str(exc)}, status_code=exc.status,
                            headers={"Retry-After": "10"} if exc.status == 503 else None)
    return result.as_dict()
