"""Capri's local web server: serves the life dashboard with a live Capri sidebar.

  http://127.0.0.1:8765/            -> the dashboard (life/dashboard/dashboard.html)
  GET  /api/state                   -> {"state": "listening"|"thinking"|..., "log": [...]}
  POST /api/say    {"text": "..."}  -> a typed turn, handled exactly like a spoken one
  POST /api/stop                    -> same as pressing Esc
  POST /api/rebuild                 -> rebuild the dashboard from the life folder
  GET  /life/<path>                 -> files from the life folder (docs links, PDFs)

Only reachable from this PC (bound to 127.0.0.1). Because a typed turn can control the
computer, every request must come from Capri's own page: the Host header must be
127.0.0.1/localhost on our port (stops DNS rebinding), POSTs must be JSON, and a present
Origin header must be our own origin (stops other websites from posting to Capri).
"""
from __future__ import annotations

import json
import mimetypes
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Protocol
from urllib.parse import unquote, urlsplit

MAX_TEXT = 2000


class App(Protocol):
    def web_state(self) -> dict: ...
    def web_say(self, text: str) -> None: ...
    def web_stop(self) -> None: ...
    def web_rebuild(self) -> str: ...


def make_handler(app: App, life_root: Path | None, port: int):
    allowed_hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
    allowed_origins = {f"http://{h}" for h in allowed_hosts}

    class Handler(BaseHTTPRequestHandler):
        server_version = "Capri"

        def log_message(self, *args) -> None:  # keep the console clean
            pass

        # ---------- helpers ----------
        def _send(self, code: int, body: bytes, ctype: str = "application/json") -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, code: int, obj) -> None:
            self._send(code, json.dumps(obj).encode())

        def _host_ok(self) -> bool:
            return (self.headers.get("Host") or "").lower() in allowed_hosts

        def _origin_ok(self) -> bool:
            origin = self.headers.get("Origin")
            return origin is None or origin.lower() in allowed_origins

        def _file(self, p: Path) -> None:
            if not p.is_file():
                return self._json(404, {"error": "not found"})
            ctype = mimetypes.guess_type(p.name)[0] or "application/octet-stream"
            if p.suffix.lower() == ".md":
                ctype = "text/plain; charset=utf-8"
            self._send(200, p.read_bytes(), ctype)

        def _life_path(self, rel: str) -> Path | None:
            if life_root is None:
                return None
            root = life_root.resolve()
            p = (root / unquote(rel)).resolve()
            if root != p and root not in p.parents:
                return None                      # ../ escape attempt
            if any(part.startswith(".") for part in p.relative_to(root).parts):
                return None                      # .claude, .obsidian, dotfiles
            if p.name in {"canvas_feed.txt", "canvas_cache.ics"}:
                return None                      # private Canvas link
            return p

        # ---------- routes ----------
        def do_GET(self) -> None:
            if not self._host_ok():
                return self._json(403, {"error": "bad host"})
            path = urlsplit(self.path).path
            if path in ("/", "/index.html"):
                self.send_response(302)
                self.send_header("Location", "/life/dashboard/dashboard.html")
                self.end_headers()
                return
            if path == "/api/state":
                return self._json(200, app.web_state())
            if path.startswith("/life/"):
                p = self._life_path(path[len("/life/"):])
                if p is None:
                    return self._json(404, {"error": "not found"})
                return self._file(p)
            self._json(404, {"error": "not found"})

        def do_POST(self) -> None:
            if not self._host_ok() or not self._origin_ok():
                return self._json(403, {"error": "forbidden"})
            if not (self.headers.get("Content-Type") or "").startswith("application/json"):
                return self._json(415, {"error": "json only"})
            try:
                n = min(int(self.headers.get("Content-Length") or 0), 16_384)
                data = json.loads(self.rfile.read(n) or b"{}")
            except (ValueError, json.JSONDecodeError):
                return self._json(400, {"error": "bad json"})
            path = urlsplit(self.path).path
            if path == "/api/say":
                text = " ".join(str(data.get("text", "")).split())[:MAX_TEXT]
                if not text:
                    return self._json(400, {"error": "empty"})
                app.web_say(text)
                return self._json(200, {"ok": True})
            if path == "/api/stop":
                app.web_stop()
                return self._json(200, {"ok": True})
            if path == "/api/rebuild":
                return self._json(200, {"result": app.web_rebuild()})
            self._json(404, {"error": "not found"})

    return Handler


class WebServer:
    def __init__(self, app: App, life_root: Path | None, port: int = 8765) -> None:
        self.port = port
        self.httpd = ThreadingHTTPServer(("127.0.0.1", port), make_handler(app, life_root, port))
        self.httpd.daemon_threads = True

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/"

    def start(self) -> "WebServer":
        threading.Thread(target=self.httpd.serve_forever, name="web", daemon=True).start()
        return self

    def stop(self) -> None:
        self.httpd.shutdown()


class TurnLog:
    """The last N exchanges, for the sidebar."""

    def __init__(self, n: int = 40) -> None:
        self.n = n
        self.items: list[dict] = []
        self.lock = threading.Lock()

    def add(self, who: str, text: str, via: str = "") -> None:
        text = (text or "").strip()
        if not text:
            return
        with self.lock:
            self.items.append({"who": who, "text": text[:1500], "via": via, "ts": time.time()})
            del self.items[:-self.n]

    def snapshot(self) -> list[dict]:
        with self.lock:
            return list(self.items)
