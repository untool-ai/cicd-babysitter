"""Single-worker bounded webhook receiver; acknowledge only durable ingestion."""
from __future__ import annotations

import json
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any


def make_server(service: Any, secret: str | bytes, host: str = "127.0.0.1", port: int = 8788,
                *, max_body: int = 1_000_000) -> HTTPServer:
    """Build an HTTP receiver. TLS belongs at a trusted reverse proxy."""
    if not secret or not 1 <= max_body <= 1_000_000:
        raise ValueError("Secret and bounded body size required")

    secret_bytes = secret.encode("utf-8") if isinstance(secret, str) else secret
    if not isinstance(secret_bytes, bytes):
        raise ValueError("Webhook secret must be text or bytes")

    class Handler(BaseHTTPRequestHandler):
        def setup(self):
            super().setup()
            self.connection.settimeout(10)

        def log_message(self, format, *args):
            pass  # Never log URLs, signatures, headers, or request bodies.

        def reply(self, code: int):
            body = json.dumps({"accepted": code == 202}).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            if self.path != "/webhook":
                self.reply(404)
                return
            lengths = self.headers.get_all("Content-Length", [])
            if self.headers.get("Transfer-Encoding") or len(lengths) != 1:
                self.reply(411)
                return
            try:
                if not lengths[0].isascii() or not lengths[0].isdigit():
                    raise ValueError()
                length = int(lengths[0])
            except ValueError:
                self.reply(400)
                return
            if not 0 < length <= max_body:
                self.reply(413)
                return
            try:
                deadline = time.monotonic() + 10
                chunks = []
                remaining = length
                while remaining:
                    budget = deadline - time.monotonic()
                    if budget <= 0:
                        raise TimeoutError("Body deadline exceeded")
                    self.connection.settimeout(budget)
                    chunk = self.rfile.read1(min(65536, remaining))
                    if not chunk:
                        break
                    chunks.append(chunk)
                    remaining -= len(chunk)
                raw = b"".join(chunks)
                if len(raw) != length:
                    raise ValueError("Incomplete body")
                fields = ["X-Hub-Signature-256", "X-GitHub-Delivery", "X-GitHub-Event"]
                if any(len(self.headers.get_all(field, [])) != 1 for field in fields):
                    raise ValueError("Missing or duplicate identity header")
                service.ingest(raw, *(self.headers[field] for field in fields), secret_bytes)
            except PermissionError:
                self.reply(401)
            except ValueError:
                self.reply(400)
            except Exception:
                self.reply(503)
            else:
                self.reply(202)

    return HTTPServer((host, port), Handler)
