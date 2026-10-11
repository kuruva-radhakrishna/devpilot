"""A tiny stand-in for Google's Gemini REST API, for tests that must not spend quota.

It records every request (path, headers, JSON body) so tests can assert on exactly
what the SDK sent — notably the `x-goog-api-key` header — and plays back scripted
generateContent responses (text or function calls) and optional errors.
"""
from __future__ import annotations

import json
import threading
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def text_response(text: str, *, prompt=10, out=5) -> dict:
    return {
        "candidates": [{"content": {"role": "model", "parts": [{"text": text}]},
                        "finishReason": "STOP"}],
        "usageMetadata": {"promptTokenCount": prompt, "candidatesTokenCount": out,
                          "totalTokenCount": prompt + out},
    }


def call_response(name: str, args: dict) -> dict:
    return {
        "candidates": [{"content": {"role": "model",
                                    "parts": [{"functionCall": {"name": name, "args": args}}]},
                        "finishReason": "STOP"}],
        "usageMetadata": {"promptTokenCount": 10, "candidatesTokenCount": 3,
                          "totalTokenCount": 13},
    }


def error_body(code: int, status: str, message: str, details=None) -> dict:
    err = {"code": code, "message": message, "status": status}
    if details:
        err["details"] = details
    return {"error": err}


class MockGemini:
    def __init__(self, valid_key: str = "AQ.mock-key", embed_dim: int = 768):
        self.valid_key = valid_key
        self.embed_dim = embed_dim
        self.requests: list[dict] = []
        self.generate_script: deque = deque()   # dicts -> 200, or (status, body) tuples
        self._server: ThreadingHTTPServer | None = None

    @property
    def url(self) -> str:
        host, port = self._server.server_address
        return f"http://{host}:{port}/"

    def start(self) -> "MockGemini":
        mock = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):  # keep test output quiet
                pass

            def _send(self, status: int, body: dict):
                data = json.dumps(body).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def _handle(self, method: str):
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length) if length else b""
                body = json.loads(raw) if raw else None
                key = self.headers.get("x-goog-api-key")
                mock.requests.append({"method": method, "path": self.path,
                                      "key": key, "body": body})
                if key != mock.valid_key:
                    return self._send(400, error_body(
                        400, "INVALID_ARGUMENT", "API key not valid. Please pass a valid API key."))
                path = self.path.split("?")[0]
                if method == "GET" and "/models/" in path:
                    return self._send(200, {"name": path.split("/v1beta/")[-1],
                                            "supportedGenerationMethods": ["embedContent"]})
                if path.endswith(":generateContent"):
                    if not mock.generate_script:
                        return self._send(200, text_response("(mock default)"))
                    item = mock.generate_script.popleft()
                    if isinstance(item, tuple):
                        return self._send(*item)
                    return self._send(200, item)
                if path.endswith(":batchEmbedContents"):
                    n = len(body.get("requests", []))
                    return self._send(200, {"embeddings": [
                        {"values": [0.1] * mock.embed_dim} for _ in range(n)]})
                if path.endswith(":embedContent"):
                    return self._send(200, {"embedding": {"values": [0.1] * mock.embed_dim}})
                return self._send(404, error_body(404, "NOT_FOUND", f"mock has no route: {method} {path}"))

            def do_GET(self):
                self._handle("GET")

            def do_POST(self):
                self._handle("POST")

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self._server.serve_forever, daemon=True).start()
        return self

    def stop(self) -> None:
        if self._server:
            self._server.shutdown()
            self._server.server_close()
