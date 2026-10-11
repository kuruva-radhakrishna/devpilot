"""A stand-in for the isolated runner service, for backend tests.

In "execute" mode it does what the real runner does, minus the isolation: unpack the
uploaded .tar.gz and run pytest on it. In scripted mode it just replays canned answers.
It records every request so tests can assert on exactly what the backend sent.
"""
from __future__ import annotations

import io
import json
import subprocess
import sys
import tarfile
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse


class MockRunner:
    def __init__(self, token: str = "t0ken", execute: bool = True):
        self.token = token
        self.execute = execute
        self.ready = True
        self.health_failures = 0        # answer /healthz with 503 this many times first
        self.run_status = 200           # scripted /run answer when execute is False
        self.run_body: dict = {"returncode": 0, "output": "1 passed", "timed_out": False}
        self.requests: list[dict] = []
        self._server: ThreadingHTTPServer | None = None

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

            def do_GET(self):
                mock.requests.append({"method": "GET", "path": self.path})
                if mock.health_failures > 0:
                    mock.health_failures -= 1
                    return self._send(503, {"detail": "waking up"})
                self._send(200, {"ok": True, "ready": mock.ready, "isolation": True, "slots": 2})

            def do_POST(self):
                length = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(length)
                url = urlparse(self.path)
                query = {k: v[0] for k, v in parse_qs(url.query).items()}
                mock.requests.append({"method": "POST", "path": url.path, "query": query,
                                      "auth": self.headers.get("Authorization"), "body": body})
                if self.headers.get("Authorization") != f"Bearer {mock.token}":
                    return self._send(401, {"detail": "Invalid runner token."})
                if not mock.execute:
                    return self._send(mock.run_status, mock.run_body)
                self._send(200, mock._execute(body, query.get("test_path", "")))

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
