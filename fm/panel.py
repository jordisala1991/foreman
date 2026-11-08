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

import argparse
import contextlib
import gzip
import hashlib
import io
import json
import os
import secrets
import threading
import time
from collections.abc import Callable
from datetime import date, timedelta
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote, urlparse

from . import backlog, cli, costs, health, integrate, lock, metrics, sessions, util
from .config import Project, registered

DIST = Path(__file__).resolve().parent.parent / "panel" / "dist"
STATIC = {"/app.js": ("app.js", "text/javascript; charset=utf-8"), "/app.css": ("app.css", "text/css; charset=utf-8")}
COOKIE = "fm_key"
RECENT_DAYS = 7  # closed issues the tasks screen still shows
Obj = dict[str, Any]


class Refused(Exception):
    pass


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


# ---------- what is slow, kept a few seconds ----------

_memo: dict[Any, tuple[float, Any]] = {}
_memo_lock = threading.Lock()


def memo(key: Any, ttl: float, fn: Callable[[], Any]) -> Any:
    with _memo_lock:
        hit = _memo.get(key)
        if hit and time.time() - hit[0] < ttl:
            return hit[1]
    value = fn()
    with _memo_lock:
        _memo[key] = (time.time(), value)
    return value


def forget() -> None:
    with _memo_lock:
        _memo.clear()


def session_rows(p: Project) -> dict[str, Any]:
    """`claude agents` (a subprocess): every 10 s at most."""
    return memo(("sessions", p.get("agents", "claude")), 10, lambda: sessions.listing(p))


# ---------- the projects ----------

def projects() -> list[Project]:
    out = []
    for home in registered():
        try:
            out.append(Project.load(home))
        except Exception:  # noqa: BLE001 - a broken fm.toml leaves the others served
            continue
    return out


def project(q: dict[str, list[str]]) -> Project:
    ps = projects()
    name = q.get("p", [""])[0]
    p = next((x for x in ps if x.name == name), None) if name else (ps[0] if ps else None)
    if p is None:
        raise KeyError(f"no project {name!r}" if name else "no project registered (fm init)")
    return p


def loop_beat(p: Project) -> Obj:
    hb = util.read_json(p.state / "loop.json", {})
    age = time.time() - float(hb.get("epoch") or 0)
    return {"time": hb.get("time"), "alive": age < 3 * int(p.get("health", "loop_seconds"))}


def paused(p: Project) -> str | None:
    f = p.state / "paused"
    return f.read_text().strip() or "yes" if f.exists() else None


def needs(d: Obj) -> int:
    """What waits for the owner: open questions, the queue, triage, blocked issues."""
    waiting = [i for i in backlog.issues(d) if i["status"] in ("in review", "triage")
               or (i.get("blocked") and i["status"] not in backlog.CLOSED)]
    return len(d["decisions"]) + len(waiting)


def summary(p: Project) -> Obj:
    try:
        d = backlog.load(p)
        return {"name": p.name, "paused": paused(p), "loop": loop_beat(p), "needs": needs(d),
                "running": sum(1 for i in backlog.issues(d) if i["status"] in backlog.RUNNING)}
    except Exception as e:  # noqa: BLE001
        return {"name": p.name, "broken": str(e)}


def with_session(p: Project, i: Obj, rows: dict[str, Any]) -> Obj:
    a = i.get("agent") or {}
    return {**i, "agent_state": sessions.state(rows, a.get("session")) if a else None}


def state(p: Project) -> Obj:
    d = backlog.load(p)
    since = (date.today() - timedelta(days=RECENT_DAYS)).isoformat()
    rows = session_rows(p)
    shown = [with_session(p, i, rows) for i in backlog.issues(d)
             if i["status"] not in backlog.CLOSED or (i.get("closed") or "") >= since]
    events = util.read_jsonl(p.state / "events.jsonl")[-20:][::-1]
    return {"name": p.name, "paused": paused(p), "loop": loop_beat(p), "issues": shown, "questions": d["decisions"],
            "events": events}


def issue(p: Project, iid: str) -> Obj:
    d = backlog.load(p)
    i = backlog.get(d, iid)
    sids = [s for s in i.get("sessions", []) if s]
    c = memo(("cost", tuple(sids)), 30, lambda: costs.of_sessions(sids))
    return {
        "issue": with_session(p, i, session_rows(p)),
        "events": [e for e in util.read_jsonl(p.state / "events.jsonl") if e.get("issue") == iid][::-1],
        "questions": [q for q in d["decisions"] + d["decisions_resolved"] if q.get("issue") == iid],
        "cost": {"usd": c["usd"], "tokens": c["tokens"], "minutes": c["minutes"]},
    }


def system(p: Project) -> Obj:
    found = memo(("health", str(p.home)), 60, lambda: health.findings(p, session_rows(p), outside=True))
    rows = memo(("metrics", str(p.home)), 30, lambda: metrics.per_issue(p))
    locks = [r.status() for r in lock.declared(p.cfg).values()]
    return {"name": p.name, "paused": paused(p), "loop": loop_beat(p), "findings": found, "metrics": rows,
            "total": round(sum(r["usd"] for r in rows), 2), "locks": locks}


# ---------- the owner's actions ----------

_cli_lock = threading.Lock()  # one at a time: what a command prints for the terminal is dropped


def run_cli(fn: Callable[[argparse.Namespace], Any], p: Project, **kw: Any) -> None:
    """A command of fm.cli as `fm --home HOME ...` would run it; its sys.exit(message) becomes a refusal."""
    try:
        with _cli_lock, contextlib.redirect_stdout(io.StringIO()):
            fn(argparse.Namespace(home=str(p.home), **kw))
    except SystemExit as e:
        raise Refused(str(e.code)) from None


def open_permission(p: Project, qid: str) -> None:
    q = next((q for q in backlog.load(p)["decisions"] if q["id"] == qid), None)
    if not q or q.get("kind") != "permission":
        raise Refused(f"{qid} is not an open permission request")


def act(path: str, q: dict[str, list[str]], body: str) -> Obj:
    p = project(q)
    iid = q.get("id", [""])[0]
    text = body.strip()
    if path == "/api/add":
        f = json.loads(body or "{}")
        if not str(f.get("title", "")).strip():
            raise Refused("a task needs a title")
        crit = str(f.get("criterion", "")).strip()
        new = backlog.add(p, f["title"].strip(), detail=str(f.get("detail", "")).strip(),
                          status="ready" if crit else "backlog", criterion=crit,
                          claim=str(f.get("claim", "")).strip())
        return {"id": new}
    if not iid and path != "/api/pause":
        raise KeyError("id is missing")
    if path == "/api/answer":
        if not text:
            raise Refused("the answer is empty")
        q_ = next((x for x in backlog.load(p)["decisions"] if x["id"] == iid), None)
        if q_ and q_.get("kind") == "permission":
            raise Refused(f"{iid} is a permission request: allow or deny it")
        run_cli(cli.cmd_answer, p, qid=iid, text=text)
    elif path in ("/api/allow", "/api/deny"):
        open_permission(p, iid)
        run_cli(lambda a: cli.cmd_allow(a, path == "/api/allow"), p, qid=iid, text=text or None)
    elif path == "/api/accept":
        try:
            return {"commit": integrate.accept(p, iid, by="owner (panel)")}
        except integrate.IntegrateError as e:
            raise Refused(str(e)) from None
    elif path == "/api/reject":
        if not text:
            raise Refused("say what to change: the note goes back to its agent")
        run_cli(cli.cmd_reject, p, id=iid, note=text)
    elif path == "/api/go":
        if backlog.get(backlog.load(p), iid)["status"] != "triage":
            raise Refused(f"{iid} is not in triage")
        run_cli(cli.cmd_go, p, id=iid)
    elif path == "/api/cancel":
        run_cli(cli.cmd_cancel, p, id=iid, why=text or None)
    elif path == "/api/unblock":
        run_cli(cli.cmd_unblock, p, id=iid, relaunch=q.get("relaunch", [""])[0] == "1")
    elif path == "/api/pause":
        run_cli(cli.cmd_pause, p, off=q.get("on", ["1"])[0] == "0")
    else:
        raise LookupError(path)
    return {"ok": True}


# ---------- HTTP ----------

class Handler(BaseHTTPRequestHandler):
    token = ""

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        pass

    def given_key(self, q: dict[str, list[str]]) -> str:
        if q.get("key"):
            return q["key"][0]
        c = SimpleCookie(self.headers.get("Cookie") or "")
        return c[COOKIE].value if COOKIE in c else ""

    def ok(self, q: dict[str, list[str]]) -> bool:
        return secrets.compare_digest(self.given_key(q), self.token)

    def send(self, code: int, body: str | bytes, ctype: str = "application/json; charset=utf-8",
             etag: bool = False, headers: dict[str, str] | None = None) -> None:
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

    def do_GET(self) -> None:  # noqa: N802
        u = urlparse(self.path)
        q = parse_qs(u.query)
        if not self.ok(q):
            return self.send(403, "wrong key: open the URL `fm panel` printed", "text/plain; charset=utf-8")
        if u.path == "/":
            f = DIST / "index.html"
            if not f.exists():
                return self.send(500, "panel/dist is not built: cd panel && npm install && npm run build",
                                 "text/plain; charset=utf-8")
            cookie = f"{COOKIE}={self.token}; Path=/; HttpOnly; SameSite=Strict"
            return self.send(200, f.read_bytes(), "text/html; charset=utf-8", etag=True,
                             headers={"Set-Cookie": cookie})
        if u.path in STATIC:
            name, ctype = STATIC[u.path]
            if not (DIST / name).exists():
                return self.json({"error": "not found"}, 404)
            return self.send(200, (DIST / name).read_bytes(), ctype, etag=True)
        try:
            if u.path == "/api/projects":
                return self.json({"projects": [summary(p) for p in projects()]})
            if u.path == "/api/state":
                return self.json(state(project(q)))
            if u.path == "/api/issue":
                return self.json(issue(project(q), q.get("id", [""])[0]))
            if u.path == "/api/system":
                return self.json(system(project(q)))
        except (KeyError, backlog.BacklogError) as e:
            return self.json({"error": str(e).strip("'\"")}, 404)
        return self.json({"error": "not found"}, 404)

    def do_POST(self) -> None:  # noqa: N802
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
