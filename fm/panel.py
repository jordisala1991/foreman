"""The panel: a stdlib HTTP server for the owner (127.0.0.1, or the network with `--host 0.0.0.0`). It serves the page built in panel/dist (React, from
panel/src) and a JSON API over every registered project (`p=NAME` picks one; the first by default).

Adapted from foreman's lib/foreman/panel.py: the key (`?key=` or the `fm_key` cookie the page sets), the ETag on every
JSON answer, the gzip of large bodies, the routing by path. What only served the orchestrator is gone. The owner's
actions call what the CLI calls (fm.cli's functions, fm.integrate.accept, fm.backlog.add), so the panel and `fm`
cannot disagree.

GET   /                index.html (sets the cookie)      /app.js, /app.css   the built files
GET   /api/projects    each project: loop heartbeat, paused, what waits, what runs
GET   /api/state       one project: its issues (closed ones of the last week), open questions, latest events
GET   /api/issue       one issue: as stored, its events, its questions, its cost, its agent's session state
GET   /api/system      loop heartbeat, paused, health findings, metrics per issue and their total
POST  /api/add  /api/answer  /api/allow  /api/deny  /api/accept  /api/reject  /api/go  /api/cancel  /api/unblock
      /api/pause       (id=..., the text in the body; /api/add takes JSON {title, detail, claim, criterion})
"""

from __future__ import annotations

import gzip
import hashlib
import json
import os
import secrets
from collections.abc import Callable
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote, urlparse

from . import backlog
from .panel_actions import Refused, act
from .panel_views import Obj, forget, issue, project, projects, state, summary, system

DIST = Path(__file__).resolve().parent.parent / "panel" / "dist"
STATIC = {"/app.js": ("app.js", "text/javascript; charset=utf-8"), "/app.css": ("app.css", "text/css; charset=utf-8")}
COOKIE = "fm_key"

# the read-only JSON views, by path: each takes the query
VIEWS: dict[str, Callable[[dict[str, list[str]]], Obj]] = {
    "/api/projects": lambda q: {"projects": [summary(p) for p in projects()]},
    "/api/state": lambda q: state(project(q)),
    "/api/issue": lambda q: issue(project(q), q.get("id", [""])[0]),
    "/api/system": lambda q: system(project(q)),
}

# ---------- the key ----------


def token_file() -> Path:
    return Path.home() / ".config/fm/panel.token"


def load_token() -> str:
    f = token_file()
    if not f.exists():
        f.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(f, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)  # readable only by its owner from the start
        with os.fdopen(fd, "w") as out:
            out.write(secrets.token_urlsafe(16))
    return f.read_text().strip()


class Handler(BaseHTTPRequestHandler):
    token = ""

    def log_message(self, format: str, *args: Any) -> None:
        pass

    def given_key(self, q: dict[str, list[str]]) -> str:
        if q.get("key"):
            return q["key"][0]
        c = SimpleCookie(self.headers.get("Cookie") or "")
        return c[COOKIE].value if COOKIE in c else ""

    def ok(self, q: dict[str, list[str]]) -> bool:
        return secrets.compare_digest(self.given_key(q), self.token)

    def send(
        self, code: int, body: str | bytes, ctype: str = "application/json; charset=utf-8", etag: bool = False, headers: dict[str, str] | None = None
    ) -> None:
        b = body.encode() if isinstance(body, str) else body
        tag = '"' + hashlib.sha1(b).hexdigest()[:16] + '"' if etag and code == 200 else None
        if tag and self.headers.get("If-None-Match") == tag:
            self.send_response(304)
            self.send_header("ETag", tag)
            self.end_headers()
            return
        gz = len(b) > 1400 and "gzip" in (self.headers.get("Accept-Encoding") or "")
        if gz:
            b = gzip.compress(b, 5)
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Content-Length", str(len(b)))
        self.send_header("X-Content-Type-Options", "nosniff")
        if gz:
            self.send_header("Content-Encoding", "gzip")
        if tag:
            self.send_header("ETag", tag)
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(b)

    def json(self, obj: Any, code: int = 200) -> None:
        self.send(code, json.dumps(obj, ensure_ascii=False), etag=True)

    def index(self) -> None:
        f = DIST / "index.html"
        if not f.exists():
            return self.send(500, "panel/dist is not built: cd panel && npm install && npm run build", "text/plain; charset=utf-8")
        cookie = f"{COOKIE}={self.token}; Path=/; HttpOnly; SameSite=Strict"
        self.send(200, f.read_bytes(), "text/html; charset=utf-8", etag=True, headers={"Set-Cookie": cookie})

    def static(self, path: str) -> None:
        name, ctype = STATIC[path]
        if not (DIST / name).exists():
            return self.json({"error": "not found"}, 404)
        self.send(200, (DIST / name).read_bytes(), ctype, etag=True)

    def api(self, path: str, q: dict[str, list[str]]) -> None:
        view = VIEWS.get(path)
        if view is None:
            return self.json({"error": "not found"}, 404)
        try:
            self.json(view(q))
        except (KeyError, backlog.BacklogError) as e:
            self.json({"error": str(e).strip("'\"")}, 404)

    def do_GET(self) -> None:
        u = urlparse(self.path)
        q = parse_qs(u.query)
        if not self.ok(q):
            return self.send(403, "wrong key: open the URL `fm panel` printed", "text/plain; charset=utf-8")
        if u.path == "/":
            return self.index()
        if u.path in STATIC:
            return self.static(u.path)
        self.api(u.path, q)

    def do_POST(self) -> None:
        u = urlparse(self.path)
        q = parse_qs(u.query)
        if not self.ok(q):
            return self.send(403, "wrong key", "text/plain; charset=utf-8")
        origin = self.headers.get("Origin")
        host = self.headers.get("Host") or ""
        if origin and origin not in (f"http://{host}",):  # another page of this machine may not act for the owner
            return self.json({"error": "cross-origin write refused"}, 403)
        body = self.rfile.read(min(int(self.headers.get("Content-Length") or 0), 64000)).decode(errors="replace")
        try:
            out = act(u.path, q, body)
            forget()
            return self.json(out)
        except LookupError as e:
            if isinstance(e, KeyError):
                return self.json({"error": str(e).strip("'\"")}, 400)
            return self.json({"error": "not found"}, 404)
        except Refused as e:
            return self.json({"error": str(e)}, 409)
        except (backlog.BacklogError, ValueError) as e:
            return self.json({"error": str(e)}, 400)


def make_server(port: int = 7788, token: str | None = None, host: str = "127.0.0.1") -> tuple[ThreadingHTTPServer, str]:
    """The server (not started) and its key; port 0 picks a free one (tests). `host` 0.0.0.0 serves the other
    devices of the network too (the key is still required, as in today's panel)."""
    Handler.token = token or load_token()
    return ThreadingHTTPServer((host, port), Handler), Handler.token


def serve(port: int = 7788, host: str = "127.0.0.1") -> None:
    srv, tok = make_server(port, host=host)
    names = ", ".join(p.name for p in projects()) or "none (fm init registers one)"
    shown = host
    if host in ("0.0.0.0", ""):
        import socket

        shown = socket.gethostname()
    print(f"fm panel on http://{shown}:{srv.server_address[1]}/?key={quote(tok)}\nprojects: {names}", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
