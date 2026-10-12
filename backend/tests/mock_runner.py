"""A stand-in for the isolated runner service, for backend tests.

It speaks the runner's jobs API (POST /jobs -> 202 {job_id}, GET /jobs/{id}). In "execute"
mode it does what the real runner does, minus the isolation: unpack the uploaded .tar.gz
and run pytest on it (Python only; Node/Go need toolchains the test machine may not have).
In scripted mode it replays canned answers. It records every request so tests can assert
on exactly what the backend sent.
"""
from __future__ import annotations

import io
import json
import subprocess
import sys
import tarfile
import tempfile
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse


class MockRunner:
    def __init__(self, token: str = "t0ken", execute: bool = True):
        self.token = token
        self.execute = execute
        self.ready = True
        self.health_failures = 0        # answer /healthz with 503 this many times first
        self.submit_status = 202        # scripted POST /jobs answer when execute is False
        self.submit_body: dict = {}     # body for a non-202 scripted submit
        self.job_result: dict = {"returncode": 0, "output": "1 passed", "timed_out": False}
        self.results: list[dict] = []   # scripted: one result per submitted job (the last repeats)
        self.job_error: str | None = None   # scripted: the job finishes with this error
        self.job_http_status = 200      # scripted: the poll endpoint answers with this status
        self.running_polls = 0          # answer "running" this many times before finishing
        self.install: dict | None = None    # scripted dependency-install report added to results
        self.requests: list[dict] = []
        self._jobs: dict[str, dict] = {}
        self._server: ThreadingHTTPServer | None = None

    # Back-compat names used by older tests.
    @property
    def run_status(self) -> int:
        return self.submit_status

    @run_status.setter
    def run_status(self, value: int) -> None:
        self.submit_status = value

    @property
    def run_body(self) -> dict:
        return self.submit_body

    @run_body.setter
    def run_body(self, value: dict) -> None:
        self.submit_body = value

    @property
    def url(self) -> str:
        host, port = self._server.server_address
        return f"http://{host}:{port}"

    def start(self) -> "MockRunner":
        mock = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, status: int, body: dict):
                data = json.dumps(body).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def _authorized(self) -> bool:
                return self.headers.get("Authorization") == f"Bearer {mock.token}"

            def do_GET(self):
                path = urlparse(self.path).path
                mock.requests.append({"method": "GET", "path": path})
                if path == "/healthz":
                    if mock.health_failures > 0:
                        mock.health_failures -= 1
                        return self._send(503, {"detail": "waking up"})
                    return self._send(200, {"ok": True, "ready": mock.ready, "isolation": True, "slots": 2,
                                            "toolchains": {"python": True, "node": True, "go": True}})
                if path.startswith("/jobs/"):
                    if not self._authorized():
                        return self._send(401, {"detail": "Invalid runner token."})
                    return self._send_job(path.rsplit("/", 1)[1])
                self._send(404, {"detail": "no"})

            def _send_job(self, job_id: str):
                if mock.job_http_status != 200:
                    return self._send(mock.job_http_status, {"detail": "scripted"})
                job = mock._jobs.get(job_id)
                if job is None:
                    return self._send(404, {"detail": "No such job (it may have expired)."})
                if mock.running_polls > 0:
                    mock.running_polls -= 1
                    return self._send(200, {"status": "running", "stage": "testing", "result": None, "error": None})
                if job["error"]:
                    return self._send(200, {"status": "error", "stage": "finished", "result": None, "error": job["error"]})
                self._send(200, {"status": "done", "stage": "finished", "result": job["result"], "error": None})

            def do_POST(self):
                length = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(length)
                url = urlparse(self.path)
                query = {k: v[0] for k, v in parse_qs(url.query).items()}
                mock.requests.append({"method": "POST", "path": url.path, "query": query,
                                      "auth": self.headers.get("Authorization"), "body": body})
                if not self._authorized():
                    return self._send(401, {"detail": "Invalid runner token."})
                if url.path != "/jobs":
                    return self._send(404, {"detail": "no"})
                job_id = uuid.uuid4().hex
                if mock.execute:
                    result = mock._execute(body, query.get("test_path", ""))
                    if mock.install is not None:
                        result["install"] = mock.install
                    mock._jobs[job_id] = {"result": result, "error": None}
                    return self._send(202, {"job_id": job_id})
                if mock.submit_status != 202:
                    return self._send(mock.submit_status, mock.submit_body)
                if mock.results:
                    result = dict(mock.results.pop(0) if len(mock.results) > 1 else mock.results[0])
                else:
                    result = dict(mock.job_result)
                if mock.install is not None:
                    result["install"] = mock.install
                mock._jobs[job_id] = {"result": result, "error": mock.job_error}
                self._send(202, {"job_id": job_id})

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self._server.serve_forever, daemon=True).start()
        return self

    def stop(self) -> None:
        if self._server:
            self._server.shutdown()
            self._server.server_close()

    @staticmethod
    def _execute(archive: bytes, test_path: str) -> dict:
        with tempfile.TemporaryDirectory(prefix="mockrunner-") as ws:
            with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as tf:
                tf.extractall(ws, filter="data")
            cmd = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"]
            if test_path:
                cmd += ["--", test_path]
            r = subprocess.run(cmd, cwd=ws, capture_output=True, text=True, timeout=120)
            return {"returncode": r.returncode, "output": r.stdout + r.stderr, "timed_out": False}

    def uploads(self) -> list[dict]:
        return [r for r in self.requests if r["method"] == "POST"]
