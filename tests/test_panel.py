"""The panel: it starts and serves the page and JSON of two fake products in the temporary directory: an active one
and an inactive one whose foreman.toml lives outside its home (the registry's `config`), in Spanish, with a wiki and
its own paths. No test reads another product's files on this machine.
"""

import contextlib
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import unittest
import urllib.error
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

from helpers import BASE_TOML, ROOT, ProjectCase

from foreman import (
    config,
    heartbeat,
    lock,
    notes,
    orchestrator,
    panel,
    panel_cache,
    panel_cards,
    panel_locks,
    panel_registry,
    util,
    versions,
)
from foreman.backlog import Backlog


def memo_wait(timeout):
    """Wait for the panel's background computations in flight; True when none is left."""
    end = time.time() + timeout
    while time.time() < end:
        with panel_cache._memo_lock:
            if not panel_cache._refreshing:
                return True
        time.sleep(0.02)
    return False


OTHER_TOML = r"""
[project]
name = "other"
owner = "Boss"
language = "es"

[paths]
notes = "raw/notas"
reviews = "raw/reviews"
state = "{state}"
worktrees = "{tmp}/other-wt"

[backlog]
file = "wiki/backlog.json"
page = "wiki/backlog.md"
archive_page = "wiki/backlog-archivo.md"
decisions_page = "wiki/pendientes.md"

[ids]
project = "^E\\d+$"
decision = "^[A-Z][A-Za-z]* J\\d+$"
report_prefixes = ["raw/"]

[agents]
production_types = ["lab"]

[loop]
prompt = "Bucle del orquestador"

[repos.engine]
path = "{tmp}/no-engine"

[resources.gpu]
dir = "{gpu}/lock"
queue = "{gpu}/queue"
guard = ["swift build"]

[panel]
title = "Other"
wiki = "wiki"

[[recurring]]
name = "Bucle del orquestador"
every = "cada 30 min"
sources = [{ heartbeat = "loop", max_h = 1.5 }]

[[recurring]]
name = "Cierre de tanda"
every = "como mucho cada 5 días"
sources = [{ label = "consolidación", log = "consolida", file = "wiki/log.md", max_h = 120 }]

[[recurring]]
name = "Lint de la wiki"
every = "semanal"
sources = [{ log = "lint", file = "wiki/log.md", max_h = 168 }]
"""


def transcript(path, first, last="done for now"):
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        {"message": {"role": "user", "content": first}},
        {"message": {"role": "assistant", "content": [{"type": "text", "text": last}]}},
    ]
    path.write_text("\n".join(json.dumps(x) for x in lines) + "\n")
    return str(path)


def snapshot(*roots):
    """{path: sha1} of every file under the roots (and the roots' own mtimes): any write shows up."""
    out = {}
    for r in roots:
        for f in sorted(Path(r).rglob("*")):
            if f.is_file():
                out[str(f)] = (hashlib.sha1(f.read_bytes()).hexdigest(), f.stat().st_mtime_ns)
        out[str(r)] = Path(r).stat().st_mtime_ns
    return out


def later(seconds):
    """Every wall clock of this process `seconds` ahead (time.time and datetime.now in foreman's modules), without
    waiting: the panel's server runs in a thread of this process, so it sees the shifted clocks too."""
    shifted = type(
        "Later", (datetime,), {"now": classmethod(lambda cls, tz=None: datetime.now(tz) + timedelta(seconds=seconds))}
    )
    real = time.time
    patches = [mock.patch("time.time", lambda: real() + seconds)]
    patches += [mock.patch(f"foreman.{m}.datetime", shifted) for m in ("panel", "orchestrator", "loop", "notes")]
    stack = contextlib.ExitStack()
    for x in patches:
        stack.enter_context(x)
    return stack


class PanelCase(ProjectCase):
    def setUp(self):
        super().setUp()
        os.environ["FOREMAN_PANEL_PRS"] = "0"
        self.make_active()
        self.make_other()
        self.write_registry()
        panel_registry.REG.__init__()
        panel_locks.OWNER.__init__()
        self.srv, self.token = panel.make_server("127.0.0.1", 0, token="k3y")
        self.port = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()
        os.environ.pop("FOREMAN_PANEL_PRS", None)
        super().tearDown()

    # ---------- fixtures ----------
    def make_active(self):
        self.write_toml(
            self.toml.replace("[resources.widget]", "[resources.gadget]\n\n[resources.widget]")
            + '\n[[recurring]]\nname = "Fake orchestrator loop"\nevery = "hourly"\n'
            'sources = [{ heartbeat = "loop", max_h = 2 }]\n'
        )
        b = Backlog(self.project)
        b.init()
        with b.mutate() as data:
            b.add_project(data, "A1", "First project", "")
            for n, st in ((1, "ready"), (2, "ready"), (3, "backlog")):
                b.add_issue(data, "A1", f"PF-{n}", f"Issue {n}", status="backlog", criterion="it works")
                if st != "backlog":
                    b.set_status(data, f"PF-{n}", st)
            b.set_sub(data, "PF-1", "T1", status="in progress", agent="ag1", branch="pf-1-t1")
            b.add_decision(data, "Area J1", "Ship it?", "Context.", ["a: yes", "b: no"])
        (self.home / "reviews").mkdir(exist_ok=True)
        (self.home / "reviews/2026-10-30-plan.md").write_text("# Plan\n")
        st = self.project.state
        st.mkdir(parents=True, exist_ok=True)
        tr = transcript(self.tmp / "tasks/ag1.output", "Issue: PF-1 T1, branch `pf-1-t1`.")
        now = time.strftime("%Y-%m-%dT%H:%M:%S")
        a1 = {"start": now, "type": "dev", "transcript": tr, "tool": "Bash", "activity": now, "name": "ag1"}
        (st / "agents.json").write_text(json.dumps({"a1": a1}))  # named by hand (no launch)
        notes.inbox_add(self.project, "orchestrator says hi", sender="orchestrator")
        notes.progress(self.project, "PF-1 T1 halfway", who="ag1")
        notes.beat(self.project, "loop")
        # ag1 holds widget (a foreman lock)
        p = lock.paths("widget")
        p["dir"].mkdir(parents=True)
        (p["dir"] / "owner").write_text("ag1: widget test (2026-10-30 10:00:00, pid 1)\n")

    def make_other(self):
        """The inactive product: a backlog in wiki/, its state folder and its foreman.toml outside the home
        (the registry's `config`)."""
        self.ohome = self.tmp / "other-home"
        self.ostate = self.tmp / "other-state"
        self.ogpu = self.tmp / "other-gpu"
        (self.ohome / "wiki").mkdir(parents=True, exist_ok=True)
        (self.ohome / "raw/notas").mkdir(parents=True, exist_ok=True)
        self.ostate.mkdir(exist_ok=True)
        self.ogpu.mkdir(exist_ok=True)
        self.oconfig = self.tmp / "other.toml"
        self.oconfig.write_text(
            OTHER_TOML.replace("{state}", str(self.ostate))
            .replace("{gpu}", str(self.ogpu))
            .replace("{tmp}", str(self.tmp))
        )
        self.other = config.load_project(self.ohome, "inactive", self.oconfig)
        self.assertEqual(self.other.warnings, [])
        (self.ohome / "wiki/backlog.json").write_text(
            json.dumps(
                {
                    "schema_version": 5,
                    "updated": "2026-10-30",
                    "pages": {},
                    "issues": [],
                    "projects": [
                        {
                            "id": "E1",
                            "title": "Other project",
                            "intro": "",
                            "issues": [
                                {
                                    "id": "XX-1",
                                    "title": "Other issue",
                                    "status": "in progress",
                                    "blocked": "",
                                    "plan": [],
                                    "subs": [{"id": "T1", "status": "in progress", "agent": "leg1"}],
                                    "detail": "",
                                    "acceptance": {"criterion": "faster"},
                                }
                            ],
                        }
                    ],
                    "archive": [],
                    "decisions": [
                        {
                            "id": "Rend J1",
                            "title": "¿Sí o no?",
                            "date": "2026-10-30",
                            "text": "",
                            "options": ["a: sí", "b: no"],
                        }
                    ],
                    "decisions_resolved": [],
                },
                ensure_ascii=False,
            )
        )
        tr = transcript(self.tmp / "tasks/leg1.output", "Issue: XX-1 T1. Sigue las normas.", "medido")
        now = time.strftime("%Y-%m-%dT%H:%M:%S")
        b1 = {"start": now, "type": "lab", "transcript": tr, "tool": "Bash", "activity": now, "name": "leg1"}
        (self.ostate / "agents.json").write_text(json.dumps({"b1": b1}))
        notes.inbox_add(self.other, "hola", sender="owner")
        notes.inbox_add(self.other, "visto", sender="orchestrator")
        notes.inbox_add(self.other, "a: sí", sender="owner", decision="Rend J1, ¿Sí o no?")
        notes.progress(self.other, "medido", who="leg1")
        notes.beat(self.other, "loop")
        (self.ostate / "report.md").write_text("# Informe\n")

    def write_registry(self, language="es"):
        (self.tmp / "projects.toml").write_text(
            f'[panel]\nlanguage = "{language}"\nwho = "boss"\n\n'
            f'[[project]]\nname = "fake"\nhome = "{self.home}"\nmode = "active"\n\n'
            f'[[project]]\nname = "other"\nhome = "{self.ohome}"\nmode = "inactive"\n'
            f'config = "{self.oconfig}"\n'
        )

    # ---------- http ----------
    def backlog(self, name):
        """The backlog of `name` from /api/backlog (no longer in the state)."""
        code, d = self.req(f"/api/backlog?p={name}")
        self.assertEqual(code, 200, d)
        return d["project"]["backlog"]

    def cards(self, name):
        """{id: card} of the open issues of `name`, with the state's issue_live laid over them as the page does."""
        live = self.req(f"/api/state?p={name}")[1]["project"]["issue_live"]
        bl = self.backlog(name)
        out = {}
        for c in [c for p in bl["projects"] for c in p["issues"]] + bl["issues"]:
            x = dict(live.get(c["id"], {}))
            st = x.pop("status", None)
            out[c["id"]] = {**c, **x, **({"status": st} if st and c["status"] in ("backlog", "ready") else {})}
        return out

    def req(self, path, method="GET", body=b"", token=True):
        url = f"http://127.0.0.1:{self.port}{path}"
        if token:
            url += ("&" if "?" in path else "?") + "t=" + self.token
        r = urllib.request.Request(url, data=body if method == "POST" else None, method=method)
        try:
            with urllib.request.urlopen(r, timeout=20) as resp:
                data = resp.read()
                return resp.status, (
                    json.loads(data) if resp.headers.get_content_type() == "application/json" else data.decode()
                )
        except urllib.error.HTTPError as e:
            data = e.read()
            try:
                return e.code, json.loads(data)
            except ValueError:
                return e.code, data.decode()


class PanelTest(PanelCase):
    def get(self, path, etag=None):
        """(status, ETag, Date) of a GET, conditional with `etag`."""
        url = f"http://127.0.0.1:{self.port}{path}{'&' if '?' in path else '?'}t={self.token}"
        r = urllib.request.Request(url, headers={"If-None-Match": etag} if etag else {})
        try:
            with urllib.request.urlopen(r, timeout=20) as resp:
                resp.read()
                return resp.status, resp.headers.get("ETag"), resp.headers.get("Date")
        except urllib.error.HTTPError as e:
            return e.code, e.headers.get("ETag"), e.headers.get("Date")

    def test_serve_port_in_use_is_one_line_and_exit_1(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
            rc = panel.serve("127.0.0.1", self.port, token="k3y")
        self.assertEqual(rc, 1)
        lines = err.getvalue().splitlines()
        self.assertEqual(len(lines), 1)
        self.assertIn(f"127.0.0.1:{self.port} is in use", lines[0])
        self.assertIn("foreman panel service status", lines[0])
        self.assertNotIn("Traceback", err.getvalue())

    def test_serve_other_oserror_still_raises(self):
        with mock.patch.object(panel, "make_server", side_effect=PermissionError(13, "denied")):
            with self.assertRaises(PermissionError):
                panel.serve("127.0.0.1", 80, token="k3y")

    def test_notes_of_the_last_hour_across_midnight(self):
        """Just after midnight the last hour began yesterday: today's notes all count."""
        self.project.notes_dir.mkdir(parents=True, exist_ok=True)
        (self.project.notes_dir / "2020-03-04.md").write_text("# Notes\n\n## 00:01 First\nx\n\n- 00:20 Second\n")
        with mock.patch.dict(os.environ, {"FOREMAN_NOW": "2020-03-04T00:30:00"}):
            self.assertEqual([n["time"] for n in panel.notes_last_hour(self.project)], ["00:01", "00:20"])
        with mock.patch.dict(os.environ, {"FOREMAN_NOW": "2020-03-04T01:10:00"}):  # a plain hour: from 00:10
            self.assertEqual([n["time"] for n in panel.notes_last_hour(self.project)], ["00:20"])

    def test_state_without_clocks(self):
        """The polled state carries times, not ages, and no machine stats: two builds 2 s apart with nothing
        changed (the clocks shifted, not waited) give the same ETag, and the conditional GET is a 304 (with a Date
        header, the page's clock). Touching agents.json or progress.jsonl changes it."""
        # a branch off main and a lock in the queue, so every kind of time is in the state
        repo = self.tmp / "repo"
        subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
        g = ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t"]
        subprocess.run(g + ["commit", "-q", "--allow-empty", "-m", "first"], check=True)
        subprocess.run(g + ["checkout", "-q", "-b", "pf-1-t1"], check=True)
        subprocess.run(g + ["commit", "-q", "--allow-empty", "-m", "work"], check=True)
        self.write_toml(
            self.toml.replace("[resources.widget]", "[resources.gadget]\n\n[resources.widget]")
            + f'\n[repos.app]\npath = "{repo}"\n'
            + '\n[[recurring]]\nname = "Fake orchestrator loop"\nevery = "hourly"\n'
            'sources = [{ heartbeat = "loop", max_h = 2 }]\n'
        )
        q = lock.paths("widget")["queue"]
        q.mkdir(parents=True, exist_ok=True)
        (q / f"1-{os.getpid()}").write_text(f"ag2|{time.strftime('%Y-%m-%dT%H:%M:%S')}|1|next job|\n")
        code, d = self.req("/api/state?p=fake")
        p = d["project"]
        a = p["agents"][0]
        self.assertIsInstance(a["activity_ts"], int)
        self.assertIsInstance(a["start_ts"], int)
        self.assertFalse({"activity_s", "running_s"} & set(a))
        self.assertIsInstance(p["repos"][0]["branches"][0]["committed_ts"], int)
        w = {x["name"]: x for x in d["locks"]}["widget"]
        self.assertIsInstance(w["holders"][0]["since_ts"], int)
        self.assertIsInstance(w["queue"][0]["since_ts"], int)
        part = p["recurring"][0]["parts"][0]
        self.assertIsInstance(part["last_ts"], int)
        self.assertNotIn("hours", part)
        self.assertNotIn("mac", d)
        rep = self.req("/api/state?p=other")[1]["project"]["report"]
        self.assertEqual(set(rep), {"text", "ts"})
        for path in ("/api/state?p=fake", "/api/state?p=*"):
            code, etag, _ = self.get(path)
            self.assertEqual(code, 200)
            with later(2.1):  # 2 s later on every clock, with nothing changed: the same ETag
                self.assertEqual(self.get(path)[1], etag, path)
                code, again, date = self.get(path, etag)
            self.assertEqual((code, again), (304, etag), path)
            self.assertIsNotNone(date)
        _, etag, _ = self.get("/api/state?p=fake")
        st = self.project.state
        os.utime(st / "agents.json", (time.time() + 5, time.time() + 5))
        (st / "agents.json").write_text((st / "agents.json").read_text().replace('"tool": "Bash"', '"tool": "Read"'))
        _, etag2, _ = self.get("/api/state?p=fake")
        self.assertNotEqual(etag2, etag)
        notes.progress(self.project, "PF-1 T1 done", who="ag1")
        code, etag3, _ = self.get("/api/state?p=fake", etag2)
        self.assertEqual(code, 200)
        self.assertNotEqual(etag3, etag2)

    def test_backlog_apart(self):
        """The backlog is its own request. /api/backlog (one product and p=*) keeps its ETag while neither
        the backlog file nor a reports folder changes, and its body is not serialized again (the cached bytes come
        back); /api/state carries no cards, only issue_live. A move changes the ETag, and so does a new report."""
        code, d = self.req("/api/state?p=fake")
        self.assertNotIn("backlog", d["project"])
        self.assertIn("PF-1", d["project"]["issue_live"])
        self.assertFalse(any("backlog" in p for p in self.req("/api/state?p=*")[1]["projects"]))
        self.assertEqual(self.req("/api/backlog?p=nope")[0], 404)
        joint = self.req("/api/backlog?p=*")[1]
        self.assertEqual({p["name"] for p in joint["projects"]}, {"fake", "other"})
        self.assertIn("version", joint)
        # the cached cards carry nothing live: no agent, and PF-1 still says what the file says
        pf1 = next(c for c in self.backlog("fake")["projects"][0]["issues"] if c["id"] == "PF-1")
        self.assertEqual((pf1["status"], pf1.get("agent")), ("ready", None))
        for path in ("/api/backlog?p=fake", "/api/backlog?p=*"):
            code, etag, _ = self.get(path)
            self.assertEqual(code, 200)
            # a working agent changes the state, not the backlog
            notes.progress(self.project, "PF-1 T1 still going", who="ag1")
            self.assertEqual(self.get(path)[1], etag, path)
            self.assertEqual(self.get(path, etag)[0], 304, path)
        ps = [panel_registry.REG.get("fake")]
        self.assertIs(panel_cards.backlog_body(ps, False), panel_cards.backlog_body(ps, False))
        _, etag, _ = self.get("/api/backlog?p=fake")
        _, jetag, _ = self.get("/api/backlog?p=*")
        self.assertEqual(self.req("/api/move?p=fake&id=PF-2&a=up", "POST")[0], 200)
        _, etag2, _ = self.get("/api/backlog?p=fake")
        self.assertNotEqual(etag2, etag)
        self.assertNotEqual(self.get("/api/backlog?p=*")[1], jetag)
        # a report written after the issue cites it: its card links it once the reviews folder changes
        b = Backlog(self.project)
        with b.mutate() as data:
            b.set_sub(data, "PF-3", "T1", status="done", report="reviews/2026-10-31-pf3.md")
        _, etag3, _ = self.get("/api/backlog?p=fake")
        old = os.stat(self.home / "reviews").st_mtime
        (self.home / "reviews/2026-10-31-pf3.md").write_text("# PF-3\n")
        os.utime(self.home / "reviews", (old + 5, old + 5))
        _, etag4, _ = self.get("/api/backlog?p=fake")
        self.assertNotEqual(etag4, etag3)
        pf3 = next(c for c in self.backlog("fake")["projects"][0]["issues"] if c["id"] == "PF-3")
        self.assertEqual(pf3["reports"], [{"sub": "T1", "doc": "reviews/2026-10-31-pf3.md"}])

    def test_closed_cards_short_and_whole_on_demand(self):
        """In /api/backlog (one product and p=*) a closed card (done, canceled, archived) is short: what its
        row shows (id, title, status, priority, plan, its sub-issues' id, title, status and
        reference) and `short`, without detail, result, outcome, reports, relations or the sub-issues' text; an open
        card stays whole. /api/issue gives the whole card, the same as the cached one; 404 for an unknown id or
        product."""
        b = Backlog(self.project)
        with b.mutate() as data:
            b.edit(data, "PF-3", detail="Long detail, see `reviews/2026-10-30-plan.md`.", claim="it is quick")
            b.set_sub(
                data,
                "PF-3",
                "T1",
                status="done",
                title="first part",
                agent="ag9",
                text="(a long note about the sub-issue)",
                report="reviews/2026-10-30-plan.md",
            )
            b.edit(data, "PF-3", priority="high", assignee="ana")
            b.relate(data, "PF-3", "related", "PF-2")
            b.set_status(data, "PF-3", "done", result="It works", verdict="met")
            b.add_issue(data, "A1", "PF-4", "Issue 4", status="backlog", criterion="it works")
            b.set_status(data, "PF-4", "canceled", result="Not needed", verdict="not met")
            b.archive(data, "PF-4")
        closed = ("detail", "result", "outcome", "reports", "relations", "assignee", "closed")
        for name, bl in (
            ("fake", self.backlog("fake")),
            ("*", next(x["backlog"] for x in self.req("/api/backlog?p=*")[1]["projects"] if x["name"] == "fake")),
        ):
            cards = {c["id"]: c for c in bl["projects"][0]["issues"] + bl["archive"]}
            pf3, pf4 = cards["PF-3"], cards["PF-4"]
            self.assertTrue(pf3["short"] and pf4["short"] and pf4["archived"], name)
            self.assertFalse(set(closed) & (set(pf3) | set(pf4)), name)
            self.assertEqual((pf3["title"], pf3["status"], pf3["priority"]), ("Issue 3", "done", "high"))
            self.assertEqual(pf3["subs"], [{"id": "T1", "title": "first part", "status": "done", "ref": "ag9"}])
            self.assertEqual(pf4["status"], "canceled")
            for c in (cards["PF-1"], cards["PF-2"]):  # open: whole
                self.assertNotIn("short", c)
                self.assertIn("detail", c)
                self.assertIn("outcome", c)
        code, d = self.req("/api/issue?p=fake&id=PF-3")
        self.assertEqual(code, 200)
        whole = d["issue"]
        self.assertEqual(
            whole, panel_cards.find_card(panel_cards.backlog_cards(panel_registry.REG.get("fake")), "PF-3")
        )
        self.assertNotIn("short", whole)
        self.assertEqual((whole["result"], whole["outcome"]["verdict"] is not None), ("It works", True))
        self.assertIn("Long detail", whole["detail"])
        self.assertEqual(whole["subs"][0]["note"], "(a long note about the sub-issue)")
        self.assertEqual(whole["relations"], {"related": ["PF-2"]})
        self.assertEqual(whole["reports"], [{"sub": "T1", "doc": "reviews/2026-10-30-plan.md"}])
        self.assertTrue(self.req("/api/issue?p=fake&id=PF-4")[1]["issue"]["archived"])
        self.assertEqual(self.req("/api/issue?p=other&id=XX-1")[1]["issue"]["subs"][0]["agent"], "leg1")
        self.assertEqual(self.req("/api/issue?p=fake&id=PF-99")[0], 404)
        self.assertEqual(self.req("/api/issue?p=nope&id=PF-3")[0], 404)
        self.assertEqual(self.req("/api/issue?p=fake&id=PF-3", token=False)[0], 403)

    def test_trim_closed_keeps_the_recent_closed_and_stubs_the_rest(self):
        """trim_closed: open cards whole (the same objects); of the closed ones not archived, the k most recent short
        as today (newest `closed` first, then the later in the file, undated after dated); every other closed card and
        the whole archive a stub in its place with only the stub's keys; the same ids in the same places and the same
        counts."""

        def card(i, status, closed=None, archived=False, **kw):
            c = {
                "id": f"Z-{i}",
                "project": "E1",
                "title": f"Issue {i}",
                "status": status,
                "blocked": "",
                "detail": "long text " * 20,
                "subs": [{"id": "T1", "title": "part", "status": "done", "note": "a note"}],
                "plan": [],
                "reports": [],
                **kw,
            }
            if closed:
                c["closed"] = closed
            if archived:
                c["archived"] = True
            return c

        issues = [
            card(1, "done", "2026-10-01"),
            card(2, "ready"),
            card(3, "canceled", "2026-10-20", outcome={"claim": "x", "aliases": ["OLD-3"]}),
            card(4, "done"),  # undated, earlier in the file
            card(5, "duplicate", "2026-10-10"),
            card(6, "done"),  # undated, later in the file
            card(7, "in progress"),
        ]
        loose = [{**card(8, "done", "2026-10-15"), "project": ""}]
        archive = [card(9, "done", "2026-10-30", archived=True), card(10, "canceled", archived=True, project="E0")]
        bl = {
            "projects": [{"id": "E1", "title": "P", "issues": issues}],
            "issues": loose,
            "archive": archive,
            "counts": {"done": 6},
            "decisions": [],
        }
        stub_keys = {"id", "status", "project", "archived", "outcome", "stub"}
        for k, recent in (
            (3, {"Z-3", "Z-8", "Z-5"}),
            (5, {"Z-3", "Z-8", "Z-5", "Z-1", "Z-6"}),
            (0, set()),
            (99, {"Z-1", "Z-3", "Z-4", "Z-5", "Z-6", "Z-8"}),
        ):
            out = panel_cards.trim_closed(bl, k)
            short = panel_cards.short_backlog(bl)
            self.assertEqual(out["counts"], bl["counts"])
            self.assertEqual(set(out), set(short))
            got = out["projects"][0]["issues"] + out["issues"] + out["archive"]
            want = short["projects"][0]["issues"] + short["issues"] + short["archive"]
            self.assertEqual([c["id"] for c in got], [c["id"] for c in want], k)
            for c, s, src in zip(got, want, issues + loose + archive):
                if src["status"] not in ("done", "canceled", "duplicate"):
                    self.assertIs(c, src)
                elif c["id"] in recent:
                    self.assertEqual(c, s, (k, c["id"]))
                else:
                    self.assertTrue(c["stub"], (k, c["id"]))
                    self.assertLessEqual(set(c), stub_keys, c)
                    self.assertNotIn("short", c)
            self.assertEqual({c["id"] for c in got if c.get("short")}, recent, k)
        stubs = {c["id"]: c for c in panel_cards.trim_closed(bl, 0)["projects"][0]["issues"]}
        self.assertEqual(
            stubs["Z-3"],
            {"id": "Z-3", "status": "canceled", "outcome": {"aliases": ["OLD-3"]}, "stub": True},
        )
        self.assertEqual(stubs["Z-1"], {"id": "Z-1", "status": "done", "stub": True})
        arch = panel_cards.trim_closed(bl, 99)["archive"]
        self.assertEqual(
            arch,
            [
                {"id": "Z-9", "status": "done", "project": "E1", "archived": True, "stub": True},
                {
                    "id": "Z-10",
                    "status": "canceled",
                    "project": "E0",
                    "archived": True,
                    "stub": True,
                },
            ],
        )
        self.assertEqual(panel_cards.RECENT_CLOSED, 15)

    def test_backlog_closed_recent(self):
        """/api/backlog?closed=recent (one product and p=*): its own ETag and a 304 while nothing changes; the same
        counts and ids as without it; the fake products have fewer than RECENT_CLOSED closed issues, so only the
        archive becomes stubs. Without the parameter the body is short_backlog's, as before."""
        b = Backlog(self.project)
        with b.mutate() as data:
            b.set_status(data, "PF-3", "done", result="It works", verdict="met")
            b.add_issue(data, "A1", "PF-4", "Issue 4", status="backlog", criterion="it works")
            b.set_status(data, "PF-4", "canceled", result="Not needed", verdict="not met")
            b.archive(data, "PF-4")
        p = panel_registry.REG.get("fake")
        cards = panel_cards.backlog_cards(p)
        self.assertEqual(self.backlog("fake"), json.loads(json.dumps(panel_cards.short_backlog(cards))))
        for path in ("/api/backlog?p=fake", "/api/backlog?p=*"):
            code, etag, _ = self.get(path)
            code2, etag2, _ = self.get(path + "&closed=recent")
            self.assertEqual((code, code2), (200, 200))
            self.assertNotEqual(etag, etag2, path)
            self.assertEqual(self.get(path + "&closed=recent", etag2)[0], 304, path)
            self.assertEqual(self.get(path + "&closed=other")[1], etag, path)  # an unknown mode: the whole body
        whole = self.backlog("fake")
        code, d = self.req("/api/backlog?p=fake&closed=recent")
        self.assertEqual(code, 200)
        trim = d["project"]["backlog"]
        self.assertEqual(trim["counts"], whole["counts"])
        self.assertEqual(trim["projects"], whole["projects"])  # PF-3: one of the recent closed ones, short
        self.assertEqual(
            trim["archive"], [{"id": "PF-4", "status": "canceled", "project": "A1", "archived": True, "stub": True}]
        )
        joint = {x["name"]: x["backlog"] for x in self.req("/api/backlog?p=*&closed=recent")[1]["projects"]}
        self.assertEqual(joint["fake"], trim)
        self.assertIs(panel_cards.backlog_body([p], False, True), panel_cards.backlog_body([p], False, True))
        self.assertIsNot(panel_cards.backlog_body([p], False, True), panel_cards.backlog_body([p], False))

    def test_closed_recent_grows_little_per_old_closed_issue(self):
        """The size watch: in a product with 300 closed issues with sub-issues, each closed one beyond the
        RECENT_CLOSED recent ones adds at most 120 bytes to /api/backlog?p=…&closed=recent (a stub: id and
        status), whatever its title, text and sub-issues weigh in the whole body."""
        b = Backlog(self.project)

        def close(lo, hi):
            with b.mutate() as data:
                for n in range(lo, hi):
                    iid = f"PF-{100 + n}"
                    b.add_issue(
                        data,
                        "A1",
                        iid,
                        f"Closed issue number {n} with a long title",
                        status="backlog",
                        criterion="it works",
                    )
                    for t in ("T0", "T1", "T2"):
                        b.set_sub(data, iid, t, status="done", title=f"part {t} of the work", text="(a note)")
                    b.set_status(data, iid, "done", result="Done and measured", verdict="met")

        def size():
            code, d = self.req("/api/backlog?p=fake&closed=recent")
            self.assertEqual(code, 200)
            return len(json.dumps(d, ensure_ascii=False).encode()), d["project"]["backlog"]

        close(0, 100)
        s100, _ = size()
        close(100, 300)
        s300, bl = size()
        per = (s300 - s100) / 200
        self.assertLessEqual(per, 120, per)
        cs = [c for c in bl["projects"][0]["issues"] if c["status"] == "done"]
        self.assertEqual(len(cs), 300)
        self.assertEqual(sum(1 for c in cs if c.get("short")), panel_cards.RECENT_CLOSED)
        # all closed the same day: the later in the file are the recent ones
        self.assertEqual([c["id"] for c in cs if c.get("short")], [c["id"] for c in cs][-panel_cards.RECENT_CLOSED :])
        for c in cs:
            if not c.get("short"):
                self.assertEqual(set(c), {"id", "status", "stub"}, c)
        whole = len(json.dumps(self.req("/api/backlog?p=fake")[1], ensure_ascii=False).encode())
        self.assertGreater(whole, 2 * s300)

    def test_page_loads_the_whole_card_of_a_closed_issue(self):
        """The page: the detail of a short card loads the whole one from /api/issue, shows «loading» and
        then the card, or an error; the loaded cards are forgotten when the backlog changes, a failed one when the
        issue is opened again."""
        js = (ROOT / "panel/app.js").read_text()
        h = js[js.index("  h(key){") : js.index("const branchLink")]
        self.assertIn("if(c.short){", h)
        self.assertIn("fullCard(c)", h)
        self.assertIn("t('loading_lc')", h)
        self.assertIn("x.err", h)
        self.assertIn("c = {...c, ...x.issue, short: false}", h)
        full = js[js.index("function fullCard") : js.index("function pushView")]
        self.assertIn("/api/issue?p=${encodeURIComponent(p)}&id=${encodeURIComponent(id)}", full)
        self.assertIn("t('issue_failed'", full)
        self.assertIn("renderModal(true)", full)
        self.assertIn("gen !== FULL_GEN", full)
        self.assertIn("FULL[x].err) delete FULL[x]", js[js.index("function openView") : js.index("const viewP")])
        ld = js[js.index("async function load()") : js.index("async function decide")]
        self.assertIn("clearFull()", ld)
        for lang in ("es", "en"):
            self.assertIn("{id}", self.req(f"/api/strings?lang={lang}")[1]["issue_failed"])

    def test_page_asks_for_the_recent_closed_and_shows_the_older_on_demand(self):
        """The page asks for the light load (closed=recent) until «show the N older» is pressed in this page (a
        reload or another product goes back to it); a stub becomes a short card with an empty title, plan and
        sub-issues, so its detail loads the whole card through /api/issue; a status group counts every card in its
        header but lists only the ones that are not stubs, then the button. With node: normalize over the light body
        gives the same cards, lanes and project counts as over the whole one."""
        js = (ROOT / "panel/app.js").read_text()
        ld = js[js.index("async function load()") : js.index("async function decide")]
        self.assertIn("/api/backlog?p=${q}${ALL_CLOSED ? '' : '&closed=recent'}", ld)
        older = js[js.index("function showOlder") : js.index("function render(")]
        self.assertIn("ALL_CLOSED = true; lastBlEtag = null;", older)
        self.assertIn("load()", older)
        self.assertIn("let ALL_CLOSED = false;", js)
        self.assertIn("ALL_CLOSED = false;", js[js.index("function pick(") : js.index("// ---- Polling")])
        norm = js[js.index("function normalize") : js.index("function attention")]
        self.assertIn("c.stub ? {title: '', plan: [], subs: [], short: true} : {}", norm)
        lanes = js[js.index("function laneGroups") : js.index("function renderBacklog")]
        self.assertIn("cs.filter(c => !c.stub).map(c => storyRow(", lanes)
        self.assertIn("laneCount(l, cs, !page)", lanes)
        self.assertIn("tn('show_older', older)", lanes)
        self.assertIn('onclick="showOlder(this)"', lanes)
        h = js[js.index("  h(key){") : js.index("const branchLink")]
        self.assertIn("title: c.title ? `${c.id} · ${c.title}` : c.id", h)
        for lang in ("es", "en"):
            st = self.req(f"/api/strings?lang={lang}")[1]
            self.assertIn("{n}", st["show_older_n"])
            self.assertNotIn("{n}", st["show_older_1"])
        node = shutil.which("node")
        if not node:
            return
        b = Backlog(self.project)
        with b.mutate() as data:
            for n in range(panel_cards.RECENT_CLOSED + 5):
                iid = f"PF-{100 + n}"
                b.add_issue(data, "A1", iid, f"Closed issue {n}", status="backlog", criterion="it works")
                b.set_sub(data, iid, "T0", status="done", title="the work")
                b.set_status(data, iid, "canceled" if n % 4 else "done", result="Done", verdict="met")
        light = self.req("/api/backlog?p=fake&closed=recent")[1]
        whole = self.req("/api/backlog?p=fake")[1]
        self.assertEqual(sum(1 for c in light["project"]["backlog"]["projects"][0]["issues"] if c.get("stub")), 5)
        fns = "\n".join(
            js[js.index(a) : js.index(z)]
            for a, z in (
                ("function withLive", "// ---- Ahora"),
                ("const isActive", "// WIP targets"),
                ("const isBlocked", "const blocks ="),
                ("function epicStats", "const statsText"),
            )
        )
        script = (
            "let P = 'fake', OVER = null, BL;\n"
            + fns
            + f"""
const d = {{project: {{name: 'fake', issue_live: {{}}}}, locks: []}};
const out = [{json.dumps(light)}, {json.dumps(whole)}].map(b => {{
  BL = normalize(d, b).backlog;
  return {{cards: BL.map(c => [c.id, c.eid, c.epic, c.lane, !!c.stub]),
          stubs: BL.filter(c => c.stub).map(c => [c.title, c.plan, c.subs, c.short]),
          stats: [epicStats('A1')]}};
}});
console.log(JSON.stringify(out));
"""
        )
        r = subprocess.run([node, "-e", script], capture_output=True, text=True, timeout=30, check=False)
        self.assertEqual(r.returncode, 0, r.stderr)
        lt, wh = json.loads(r.stdout)
        self.assertEqual([c[:4] for c in lt["cards"]], [c[:4] for c in wh["cards"]])
        self.assertEqual(sum(1 for c in lt["cards"] if c[4]), 5)
        self.assertEqual(lt["stubs"], [["", [], [], True]] * 5)
        self.assertEqual(lt["stats"], wh["stats"])
        self.assertEqual(lt["stats"][0]["done"], 5)

    def test_issue_live_marks_no_agent_and_cited_branch(self):
        """issue_live has only what applies: an issue in progress with nobody on it is `no_agent`, a card
        that names a live branch gets it, and an issue with nothing live is absent."""
        b = Backlog(self.project)
        with b.mutate() as data:
            b.set_status(data, "PF-3", "in progress")
        live = self.req("/api/state?p=fake")[1]["project"]["issue_live"]
        self.assertEqual(live["PF-3"], {"no_agent": True})
        self.assertNotIn("PF-2", live)

    def test_page_reads_the_backlog_apart_and_turns_marks_on_a_304(self):
        """The page: it asks /api/backlog with the state, parses it only on a new ETag and lays issue_live
        over the cards; the red of a lock held over 3 h and an agent's «over 2 h» are marks with the time they turn
        (data-turn), which paintAges turns on a 304 as it moves the ages."""
        js = (ROOT / "panel/app.js").read_text()
        ld = js[js.index("async function load()") : js.index("async function decide")]
        self.assertIn("/api/backlog?p=", ld)
        self.assertIn("blEtag !== lastBlEtag", ld)
        self.assertIn("normalize(raw, BRAW)", ld)
        self.assertIn("p.issue_live", js[js.index("function normalize") : js.index("function attention")])
        paint = js[js.index("function paintAges") : js.index("function setSkew")]
        self.assertIn("[data-turn]", paint)
        self.assertIn("el.dataset.bg", paint)
        self.assertIn('data-turn="${+ts + LOCK_LONG_S}" data-bg="var(--bad)"', js)
        self.assertIn('data-turn="${+a.start_ts + 7200}" hidden', js)
        self.assertNotIn("dot(lockColor(l))", js)

    def test_page_and_key(self):
        self.assertEqual(self.req("/", token=False)[0], 403)
        code, html = self.req("/")
        self.assertEqual(code, 200)
        self.assertIn("app.js?v=", html)
        self.assertIn('lang="es"', html)
        for asset in ("/app.js", "/app.css"):
            self.assertEqual(self.req(asset)[0], 200)

    def test_page_knows_its_version_from_load(self):
        """The page carries the server's version from the moment it loads (<html data-v>, and the same ?v= on
        app.js and app.css, so a cached older file is never reused), /api/version answers it alone, and it moves when
        app.js changes on disk."""
        static = self.tmp / "static"
        shutil.copytree(ROOT / "panel", static)
        with mock.patch.object(panel, "STATIC", static), mock.patch.object(panel_cache, "STATIC", static):
            v = panel_cache.page_version()
            code, html = self.req("/")
            self.assertEqual(code, 200)
            self.assertIn(f'data-v="{v}"', html)
            self.assertIn(f"app.js?v={v}&", html)
            self.assertIn(f"app.css?v={v}&", html)
            self.assertNotIn("{{V}}", html)
            self.assertEqual(self.req("/api/version"), (200, {"version": v}))
            self.assertEqual(self.req("/api/state?p=fake")[1]["version"], v)
            os.utime(static / "app.js", (v + 60, v + 60))
            self.assertEqual(panel_cache.page_version(), v + 60)
            self.assertIn(f'data-v="{v + 60}"', self.req("/")[1])
            self.assertEqual(self.req("/api/version")[1]["version"], v + 60)
            self.assertEqual(self.req("/api/projects")[1]["version"], v + 60)
        self.assertEqual(self.req("/api/version", token=False)[0], 403)

    def test_page_reloads_on_another_version(self):
        """The page: its version comes from the document, not from the first answer; it is compared with the
        registry's answer, the state's and, when a pass fails, /api/version; the reload happens once per server
        version (sessionStorage) and keeps the URL (with its key). With node, the decision is run."""
        js = (ROOT / "panel/app.js").read_text()
        self.assertIn("pageVersion(document.documentElement.dataset.v", js)
        self.assertNotIn("PAGE_VERSION = 0", js)
        ld = js[js.index("async function load()") : js.index("async function decide")]
        self.assertIn("versionCheck(OVER.version)", ld)
        self.assertIn("versionCheck(raw.version)", ld)
        err = ld[ld.index("}catch(e){") :]
        self.assertIn("/api/version", err)
        self.assertIn("versionCheck(", err)
        self.assertNotIn("location.reload", ld)
        chk = js[js.index("function versionCheck") : js.index("function versionCheck") + 600]
        self.assertIn("sessionStorage", chk)
        self.assertIn("location.reload()", chk)
        node = shutil.which("node")
        if not node:
            return
        fns = js[js.index("function pageVersion") : js.index("function versionCheck")]
        script = (
            fns
            + """
const out = [
  pageVersion('1700000000', ''), pageVersion('', 'http://h/app.js?v=1700000005&t=k'), pageVersion('{{V}}', 'x'),
  reloadDecision(100, 100, null), reloadDecision(100, 200, null), reloadDecision(100, 200, '200'),
  reloadDecision(100, 300, '200'), reloadDecision(0, 200, null), reloadDecision(100, undefined, null)];
console.log(JSON.stringify(out));
"""
        )
        r = subprocess.run([node, "-e", script], capture_output=True, text=True, timeout=30, check=False)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(json.loads(r.stdout), [1700000000, 1700000005, 0, False, True, False, True, False, False])

    def test_strings(self):
        en, es = self.req("/api/strings?lang=en")[1], self.req("/api/strings?lang=es")[1]
        self.assertEqual(set(en), set(es))
        self.assertEqual(es["tab_now"], "Ahora")
        self.assertEqual(en["tab_now"], "Now")

    def test_an_old_strings_panel_is_ignored(self):
        """[strings.panel] is gone: a product's old table changes none of the panel's texts (the product still loads,
        with a warning), and the page asks for its texts by language only."""
        f = self.home / "foreman.toml"
        base = self.req("/api/strings?lang=es")[1]
        f.write_text(f.read_text() + '\n[strings.panel]\ntab_now = "Today"\n[strings.panel.lanes]\ndone = "Shipped"\n')
        panel_registry.REG.__init__()
        for q in ("", "&p=fake", "&p=*"):
            with self.subTest(q=q):
                self.assertEqual(self.req(f"/api/strings?lang=es{q}")[1], base)
        self.assertEqual(base["tab_now"], "Ahora")
        by = {p["name"]: p for p in self.req("/api/projects")[1]["projects"]}
        self.assertFalse(by["fake"].get("broken"))
        js = (ROOT / "panel/app.js").read_text()
        self.assertNotIn("&p=", js[js.index("async function loadStrings") :][:400])
        self.assertIn("await loadStrings((cur && cur.language) || OVER.language || 'en');", js)

    def test_projects_and_locks(self):
        code, d = self.req("/api/projects")
        self.assertEqual(code, 200)
        self.assertEqual(d["language"], "es")
        by = {p["name"]: p for p in d["projects"]}
        self.assertEqual(set(by), {"fake", "other"})
        self.assertTrue(by["fake"]["writable"])
        self.assertTrue(by["other"]["writable"])
        self.assertEqual((by["fake"]["mode"], by["other"]["mode"]), ("active", "inactive"))
        self.assertNotIn("adapter", by["other"])
        locks = {x["name"]: x for x in d["locks"]}
        self.assertEqual({"gadget", "widget", "gpu"}, set(locks))
        self.assertEqual(locks["widget"]["holders"][0]["who"], "ag1")
        self.assertTrue(locks["gadget"]["takeable"])
        self.assertFalse(locks["gpu"]["takeable"])  # only an inactive product uses it
        self.assertFalse(any("delegated" in x or "via" in x for x in d["locks"]))

    def test_active_project_state(self):
        code, d = self.req("/api/state?p=fake")
        self.assertEqual(code, 200)
        p = d["project"]
        self.assertTrue(p["writable"])
        ag = p["agents"][0]
        self.assertEqual(
            (ag["name"], ag["issue"], ag["branch"], ag["status"]), ("ag1", "PF-1", "pf-1-t1", "holds a lock")
        )
        self.assertEqual(ag["last"], "done for now")
        self.assertNotIn("backlog", p)
        self.assertEqual(
            p["issue_live"]["PF-1"],
            {
                "agent": "ag1",
                "agent_status": "holds a lock",
                "branch": "pf-1-t1",
                "production": True,
                "status": "in progress",
            },
        )
        issues = self.cards("fake")
        self.assertEqual(issues["PF-1"]["agent"], "ag1")
        self.assertEqual(issues["PF-1"]["status"], "in progress")
        self.assertEqual(issues["PF-1"]["outcome"]["criterion"], "it works")
        self.assertEqual(p["decisions"][0]["options"], ["a: yes", "b: no"])
        self.assertEqual(p["messages"][-1]["text"], "orchestrator says hi")
        self.assertEqual(p["progress"][0]["agent"], "ag1")
        self.assertFalse(p["recurring"][0]["late"])
        self.assertEqual(self.req("/api/state?p=nope")[0], 404)

    def test_agents_read_the_recorded_identity(self):
        """Name, issue and branch from the identity recorded at launch (a Spanish brief with no «Issue:» and no
        branch); the launch line is not part of the short task. An agent without a launch keeps the old inference of
        its issue and branch (a1, named by hand: «Issue: PF-1 T1, branch `pf-1-t1`»)."""
        tr = transcript(
            self.tmp / "tasks/ag2.output",
            "[foreman launch abcdefghij] zed7 · PF-2 T1 · branch pf-2-t1\nSigue las normas y haz T1.",
        )
        f = self.project.state / "agents.json"
        d = json.loads(f.read_text())
        now = time.strftime("%Y-%m-%dT%H:%M:%S")
        d["a2"] = {
            "start": now,
            "type": "general-purpose",
            "transcript": tr,
            "tool": "Bash",
            "activity": now,
            "name": "zed7",
            "issue": "PF-2",
            "sub": "T1",
            "branch": "pf-2-t1",
            "production": True,
            "launch": "abcdefghij",
            "named_by": "launch",
        }
        f.write_text(json.dumps(d))
        code, st = self.req("/api/state?p=fake")
        self.assertEqual(code, 200)
        ags = {a["id"]: a for a in st["project"]["agents"]}
        a2 = ags["a2"]
        self.assertEqual((a2["name"], a2["issue"], a2["branch"]), ("zed7", "PF-2", "pf-2-t1"))
        self.assertEqual(a2["task"], "Sigue las normas y haz T1.")
        self.assertEqual((ags["a1"]["name"], ags["a1"]["issue"], ags["a1"]["branch"]), ("ag1", "PF-1", "pf-1-t1"))
        issues = self.cards("fake")
        self.assertEqual((issues["PF-2"]["agent"], issues["PF-2"]["status"]), ("zed7", "in progress"))

    def test_who_orchestrates(self):
        by = {p["name"]: p for p in self.req("/api/projects")[1]["projects"]}
        self.assertIsNone(by["fake"]["orchestrator"])
        self.assertIsNone(self.req("/api/state?p=fake")[1]["project"]["orchestrator"])
        orchestrator.mark(self.project, "S1", "fake-2a")
        (self.ostate / "session").write_text("L1\n")  # the bare id, written by hand: no orchestrator.json
        o = self.req("/api/state?p=fake")[1]["project"]["orchestrator"]
        self.assertEqual((o["session"], o["name"]), ("S1", "fake-2a"))
        self.assertRegex(o["since"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d")
        by = {p["name"]: p for p in self.req("/api/projects")[1]["projects"]}
        self.assertEqual(by["other"]["orchestrator"]["session"], "L1")
        self.assertEqual(by["other"]["orchestrator"]["name"], "")  # no name given
        self.assertEqual(by["fake"]["orchestrator"]["name"], "fake-2a")

    def test_no_loop_expiry(self):
        """The loop pass comes from foreman's hooks module, which never expires: no product summary carries a loop
        task, and the page draws no expiry tag."""
        orchestrator.mark(self.project, "S1", "fake-2a")
        for p in self.req("/api/projects")[1]["projects"]:
            self.assertNotIn("loop_task", p)
        self.assertNotIn("loop_task", self.req("/api/state?p=fake")[1]["project"])
        self.assertNotIn("loopTag", (ROOT / "panel/app.js").read_text())

    def test_orchestrator_health(self):
        """Each product's summary carries its marked orchestrator's health (orchestrator.health) as {verdict,
        last_pass}: None without a mark or for an inactive product, ok while the marked session's heartbeat is fresh
        (or none within the limit), gone once it stopped ticking or ended, no_loop when it ticks without passes.
        Memoised, so a poll does not compute it every time."""

        def beat(sid, ago=0, started_ago=None, ended=False):
            now = time.time()
            at = datetime.fromtimestamp(now - ago).isoformat(timespec="seconds")
            st = datetime.fromtimestamp(now - (ago if started_ago is None else started_ago)).isoformat(
                timespec="seconds"
            )
            f = heartbeat.beat_file(self.project, sid)
            f.parent.mkdir(parents=True, exist_ok=True)
            util.write_json(f, {"at": at, "started": st, "busy": False, **({"ended": at} if ended else {})})

        def health(name="fake"):
            panel_cache._memo.clear()
            return {p["name"]: p for p in self.req("/api/projects")[1]["projects"]}[name]["orchestrator_health"]

        self.assertIsNone(health())  # nobody marked
        orchestrator.mark(self.project, "S1", "fake-2a")
        last = notes.heartbeats(self.project)["loop"]
        self.assertEqual(health(), {"verdict": "ok", "last_pass": last})  # no heartbeat: unknown, in time
        beat("S1", ago=300)
        self.assertEqual(health(), {"verdict": "gone", "last_pass": last})  # its ticks stopped
        self.assertEqual(self.req("/api/state?p=fake")[1]["project"]["orchestrator_health"]["verdict"], "gone")
        beat("S1")  # it ticks again
        self.assertEqual(health(), {"verdict": "ok", "last_pass": last})
        beat("S1", ended=True)
        self.assertEqual(health()["verdict"], "gone")
        # alive, marked 3 h ago, last pass 3 h ago and its module running for 3 h: its loop is not running
        past = datetime.fromtimestamp(time.time() - 3 * 3600).isoformat(timespec="seconds")
        util.write_json(self.project.state / "orchestrator.json", {"session": "S1", "name": "fake-2a", "since": past})
        util.write_json(self.project.state / "heartbeats.json", {"loop": past})
        beat("S1", started_ago=3 * 3600)
        self.assertEqual(health(), {"verdict": "no_loop", "last_pass": past})
        beat("S1")
        # the memo: a second poll does not call orchestrator.health again, a change of the mark does
        calls = []
        real = orchestrator.health
        with mock.patch.object(orchestrator, "health", lambda p, *a, **k: calls.append(p.name) or real(p, *a, **k)):
            panel_cache._memo.clear()
            for _ in range(3):
                self.req("/api/projects")
            self.assertEqual(calls.count("fake"), 1)
            orchestrator.mark(self.project, "S2", "fake-2b")
            by = {p["name"]: p for p in self.req("/api/projects")[1]["projects"]}
            self.assertEqual(calls.count("fake"), 2)
            self.assertEqual(by["fake"]["orchestrator_health"]["verdict"], "ok")  # S2 has no heartbeat yet
        reg = self.tmp / "projects.toml"
        reg.write_text(reg.read_text().replace('mode = "active"', 'mode = "inactive"'))
        panel_registry.REG.__init__()
        self.assertIsNone(health())  # an inactive product
        js = (ROOT / "panel/app.js").read_text()
        self.assertIn("HEALTH_CLS = {gone: 'bad', no_loop: 'warn', silent: 'warn'}", js)
        self.assertIn("meta: `${healthTag(o.orchestrator_health)}${behindTag(o.name)}${langTag(o)}", js)
        self.assertIn("healthBad(p.orchestrator_health)", js)  # the Now tab's orchestrator line
        self.assertIn("healthBad(o.orchestrator_health) ? `var(--${HEALTH_CLS[", js)  # the System row's dot
        for lang in ("en", "es"):
            st = json.loads((ROOT / f"panel/strings.{lang}.json").read_text())
            for k in (
                "orch_gone",
                "orch_no_pass",
                "orch_no_pass_seen",
                "orch_gone_tip",
                "orch_no_loop_tip",
                "orch_silent_tip",
            ):
                self.assertIn(k, st, (lang, k))

    @mock.patch.object(versions, "GRACE_S", 0)  # past the grace after the install
    def test_foreman_sessions(self):
        """/api/projects carries foreman_installed and the live sessions of the registered products with the
        foreman each runs (versions.live_sessions): recorded or estimated («≈», a session with no record), behind or
        not, and whether it is the marked orchestrator; without pids, start times or status (the ETag). Nobody behind
        when every session runs the installed version. Memoised: a poll does not read the heartbeats every time."""
        from test_install import fake_root

        started = time.time() - 60
        cache = self.tmp / "cache/m/foreman"
        old, new = fake_root(cache / "0.1.0", "0.1.0"), fake_root(cache / "0.2.0", "0.2.0")
        iso = datetime.fromtimestamp(started).isoformat(timespec="seconds")
        for sid in ("S1", "S2"):  # their heartbeats, without the foreman version: S2's is estimated
            f = heartbeat.beat_file(self.project, sid)
            f.parent.mkdir(parents=True, exist_ok=True)
            util.write_json(f, {"at": util.now().isoformat(timespec="seconds"), "started": iso, "cwd": str(self.home)})
        gone = datetime.fromtimestamp(time.time() - 300).isoformat(timespec="seconds")
        f = heartbeat.beat_file(self.project, "S9")  # no tick for five minutes: left out
        util.write_json(f, {"at": gone, "started": gone, "cwd": str(self.home)})
        orchestrator.mark(self.project, "S1", "fake-2a")

        def stamp(t):
            return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(t))

        def installs(new_seen):
            install_dir = versions.sessions_file().parent
            install_dir.mkdir(parents=True, exist_ok=True)
            (install_dir / "installs.json").write_text(
                json.dumps(
                    {
                        str(old): {"version": "0.1.0", "seen": stamp(started - 7200), "last": stamp(started)},
                        str(new): {"version": "0.2.0", "seen": stamp(new_seen), "last": stamp(new_seen)},
                    }
                )
            )

        def api():
            panel_cache._memo.clear()
            d = self.req("/api/projects")[1]
            return d["foreman_installed"], {e["session"]: e for e in d["sessions"]}

        # everyone current: S1 recorded on 0.2.0, S2 estimated ≈0.2.0 (installed before it started)
        installs(started - 3600)
        versions.record_session("S1", versions.own({"CLAUDE_PLUGIN_ROOT": str(new)}), "fake", str(self.home))
        inst, got = api()
        self.assertEqual(inst, {"version": "0.2.0", "root": str(new)})
        self.assertEqual(sorted(got), ["S1", "S2"])
        self.assertEqual(set(got["S1"]), set(panel.SESSION_FIELDS))
        self.assertFalse(any(e["behind"] for e in got.values()))
        self.assertEqual((got["S2"]["version"], got["S2"]["estimated"]), ("0.2.0", True))
        # 0.2.0 installed after both started; S1 (the orchestrator) records 0.1.0: behind; S2 estimated ≈0.1.0, behind
        installs(started + 60)
        versions.record_session("S1", versions.own({"CLAUDE_PLUGIN_ROOT": str(old)}), "fake", str(self.home))
        inst, got = api()
        self.assertEqual(
            got["S1"],
            {
                "session": "S1",
                "name": "fake-2a",
                "project": "fake",
                "version": "0.1.0",
                "estimated": False,
                "behind": True,
                "orchestrator": True,
            },
        )
        self.assertEqual(
            (got["S2"]["version"], got["S2"]["estimated"], got["S2"]["behind"], got["S2"]["orchestrator"]),
            ("0.1.0", True, True, False),
        )
        self.assertEqual(versions.label(got["S2"]), "≈0.1.0")
        # the memo: three polls scan the sessions once; a new record in sessions.json is seen at once
        calls = []
        real = versions.live_sessions
        with mock.patch.object(versions, "live_sessions", lambda *a, **k: calls.append(1) or real(*a, **k)):
            panel_cache._memo.clear()
            for _ in range(3):
                self.req("/api/projects")
            self.assertEqual(len(calls), 1)
            time.sleep(0.01)
            versions.record_session("S1", versions.own({"CLAUDE_PLUGIN_ROOT": str(new)}), "fake", str(self.home))
            got = {e["session"]: e for e in self.req("/api/projects")[1]["sessions"]}
            self.assertEqual(len(calls), 2)
            self.assertFalse(got["S1"]["behind"])
        js = (ROOT / "panel/app.js").read_text()
        self.assertIn("${orchLine(p.orchestrator)}${orchVersionTag(p.name)}", js)  # the Now tab's orchestrator line
        self.assertIn("${healthTag(o.orchestrator_health)}${behindTag(o.name)}", js)  # the System product row
        self.assertIn("$('fsess').innerHTML", js)
        self.assertNotIn("bad", js[js.index("const vLabel") : js.index("const ktok")])  # amber, never red (O4)
        self.assertIn('<div id="fsess"></div>', (ROOT / "panel/index.html").read_text())
        css = (ROOT / "panel/app.css").read_text()
        self.assertIn(".lrow.fsess .meta{grid-area:2/2/3/4", css)
        for lang in ("en", "es"):
            st = json.loads((ROOT / f"panel/strings.{lang}.json").read_text())
            for k in (
                "fsess_title",
                "fsess_reload",
                "fsess_tip",
                "fsess_estimated_tip",
                "fsess_current_1",
                "fsess_current_n",
                "fsess_behind_1",
                "fsess_behind_n",
                "fsess_behind_tip",
                "fsess_orch",
                "fsess_orch_tip",
            ):
                self.assertIn(k, st, (lang, k))

    def test_inactive_project_state(self):
        p = self.req("/api/state?p=other")[1]["project"]
        self.assertTrue(p["writable"])
        self.assertEqual(p["language"], "es")
        ag = p["agents"][0]
        self.assertEqual((ag["name"], ag["issue"], ag["type"]), ("leg1", "XX-1", "lab"))
        self.assertTrue(ag["task"].startswith("Issue: XX-1"), ag["task"])
        self.assertEqual(
            [(m["from"], m["text"]) for m in p["messages"]], [("owner", "hola"), ("orchestrator", "visto")]
        )
        # an unread answer the backlog did not take (the 202 fallback): still pending, so not answered
        self.assertEqual((p["decisions"][0]["id"], p["decisions"][0]["answer"]), ("Rend J1", None))
        self.assertEqual((p["progress"][0]["agent"], p["progress"][0]["text"]), ("leg1", "medido"))
        loop_task = next(t for t in p["recurring"] if t["name"] == "Bucle del orquestador")
        self.assertFalse(loop_task["late"])
        self.assertEqual(p["report"]["text"], "# Informe\n")
        self.assertEqual(self.cards("other")["XX-1"]["agent"], "leg1")
        self.assertTrue(p["repos"][0]["missing"])

    def test_active_writes(self):
        self.assertEqual(self.req("/api/message?p=fake", "POST", b"a word")[0], 200)
        self.assertEqual(self.req("/api/decide?p=fake&d=Area%20J1%2C%20Ship%20it%3F&op=a%3A%20yes", "POST")[0], 200)
        self.assertEqual(self.req("/api/approve?p=fake&repo=nope&branch=b", "POST")[0], 400)
        code, d = self.req("/api/move?p=fake&id=PF-2&a=up", "POST")
        self.assertEqual(code, 200, d)
        self.assertIn("PF-2", d["sentence"])
        order = [i["id"] for i in Backlog(self.project).load()["projects"][0]["issues"]]
        self.assertEqual(order[:2], ["PF-2", "PF-1"])
        inbox = notes.inbox(self.project)
        self.assertEqual(
            [m.get("decision") or m.get("move") or m["text"] for m in inbox if m["from"] == "owner"],
            ["a word", "Area J1, Ship it?", "PF-2"],
        )
        self.req("/api/pause?p=fake&on=1", "POST")
        p = self.req("/api/state?p=fake")[1]["project"]
        self.assertTrue(p["paused"])
        # the answer resolved it: shown as answered until the orchestrator reads it (tests/test_panel_decide.py)
        self.assertEqual([(x["id"], x["answer"]) for x in p["decisions"]], [("Area J1", "a: yes")])
        self.assertEqual(Backlog(self.project).load()["decisions_resolved"][0]["answer"], "a: yes")
        self.req("/api/pause?p=fake&on=0", "POST")
        self.assertIsNone(self.req("/api/state?p=fake")[1]["project"]["paused"])

    def test_want_marks_a_triage_issue(self):
        """«I want it done» on a triage issue: the field, not the status, and an inbox message the notice counts;
        «no longer» clears it; anything else is a 400."""
        b = Backlog(self.project)
        with b.mutate() as data:
            b.add_issue(data, "A1", "PF-9", "Issue 9", status="triage")
        code, d = self.req("/api/want?p=fake&id=PF-9&on=1", "POST")
        self.assertEqual(code, 200, d)
        self.assertTrue(d["wanted"])
        i = b.issue(b.load(), "PF-9")
        self.assertEqual(i["status"], "triage")
        self.assertRegex(i["wanted"], r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}$")
        self.assertEqual(self.cards("fake")["PF-9"]["wanted"], i["wanted"])
        m = notes.inbox(self.project)[-1]
        self.assertEqual((m["from"], m["move"], m["want"], m["read"]), ("owner", "PF-9", "on", False))
        self.assertIn("owner wants it: PF-9", notes.inbox_line(m))
        # twice, a closed or an active issue, an unknown id: 400 and nothing more in the inbox
        with b.mutate() as data:
            b.add_issue(data, "A1", "PF-10", "Issue 10", status="triage")
            b.set_status(data, "PF-10", "canceled", result="not needed")
        before = len(notes.inbox(self.project))
        for q in ("id=PF-9&on=1", "id=PF-10&on=1", "id=PF-2&on=1", "id=PF-99&on=1", "id=PF-2&on=0"):
            self.assertEqual(self.req(f"/api/want?p=fake&{q}", "POST")[0], 400, q)
        self.assertEqual(len(notes.inbox(self.project)), before)
        # another write keeps it; «no longer» clears it, whatever the status (an agent may be on it: not stopped)
        with b.mutate() as data:
            b.edit(data, "PF-9", detail="More detail.", criterion="it works")
            b.set_status(data, "PF-9", "ready")
        self.assertEqual(b.issue(b.load(), "PF-9")["wanted"], i["wanted"])
        code, d = self.req("/api/want?p=fake&id=PF-9&on=0", "POST")
        self.assertEqual((code, d["wanted"]), (200, False), d)
        i = b.issue(b.load(), "PF-9")
        self.assertEqual((i["status"], i.get("wanted")), ("ready", None))
        m = notes.inbox(self.project)[-1]
        self.assertEqual((m["move"], m["want"]), ("PF-9", "off"))
        self.assertIn("owner no longer wants it: PF-9", notes.inbox_line(m))
        self.assertEqual(self.req("/api/want?p=fake&id=PF-9&on=0", "POST")[0], 400)

    def test_reject_cancels_a_triage_issue(self):
        """«I don't want it» on a triage issue: canceled (not deleted) with the panel's reason in the panel's
        language as its result, verdict discarded when it has a criterion, its mark cleared, and an inbox message the
        notice counts; not triage or unknown: 400 and nothing more in the inbox; `backlog status` brings it back."""
        b = Backlog(self.project)
        with b.mutate() as data:
            b.add_issue(data, "A1", "PF-9", "Issue 9", status="triage", criterion="it works")
            b.add_issue(data, "A1", "PF-10", "Issue 10", status="triage")
            b.want(data, "PF-9", True, "2026-11-03 00:00")
        reason = json.loads((ROOT / "panel/strings.es.json").read_text())["reject_reason"]
        code, d = self.req("/api/reject?p=fake&id=PF-9", "POST")
        self.assertEqual((code, d["canceled"]), (200, True), d)
        i = b.issue(b.load(), "PF-9")
        self.assertEqual((i["status"], i["result"], i["acceptance"]["verdict"]), ("canceled", reason, "discarded"))
        self.assertNotIn("wanted", i)
        self.assertEqual(self.cards("fake")["PF-9"]["status"], "canceled")  # the page's Canceled group
        m = notes.inbox(self.project)[-1]
        self.assertEqual((m["from"], m["move"], m["want"], m["read"]), ("owner", "PF-9", "reject", False))
        self.assertIn("owner discarded it: PF-9", notes.inbox_line(m))
        self.assertIn("1 discarded from triage", notes.inbox_counts([m]))
        self.assertEqual(self.req("/api/reject?p=fake&id=PF-10", "POST")[0], 200)  # no criterion: no verdict
        self.assertNotIn("acceptance", b.issue(b.load(), "PF-10"))
        before = len(notes.inbox(self.project))
        for q in ("id=PF-9", "id=PF-2", "id=PF-99", ""):
            self.assertEqual(self.req(f"/api/reject?p=fake&{q}", "POST")[0], 400, q)
        self.assertEqual(len(notes.inbox(self.project)), before)
        with b.mutate() as data:
            b.set_status(data, "PF-9", "triage")
        self.assertEqual(b.issue(b.load(), "PF-9")["status"], "triage")

    def test_detail_priority_of_a_closed_or_triage_issue(self):
        """The «Priority» row of an issue that is not active says done for done, archived only for the archive, and
        the lane's own name for triage, canceled and duplicate: a triage issue is not archived."""
        js = (ROOT / "panel/app.js").read_text()
        row = js[js.index("[t('priority'), isActive(c)") : js.index("[t('agent'), c.agent")]
        self.assertIn("c.lane==='done' ? t('done_lc')", row)
        self.assertIn("c.lane==='archive' ? t('archived_lc')", row)
        self.assertIn("esc(laneName(c.lane).toLowerCase())", row)
        self.assertNotIn(": t('archived_lc')", row)

    def test_want_button_in_the_detail(self):
        """The issue's detail carries «I want to do it» on an unmarked triage issue and «I no longer want it» on a
        marked open one, only where the panel may write and the product is active; the mark shows as a row of the
        detail and a tag in the triage group, and the activity says it in the owner's words, not the
        orchestrator's sentence."""
        js = (ROOT / "panel/app.js").read_text()
        btn = js[js.index("function wantButton") : js.index("const WANT_TAG")]
        self.assertIn("if(!canWrite(c.p) || proj(c.p).mode!=='active') return '';", btn)
        self.assertIn("on = !c.wanted && c.lane==='triage', off = c.wanted && isOpen(c)", btn)
        self.assertIn('data-on="${on ? 1 : 0}" onclick="want(this)"', btn)
        self.assertIn(
            "await post(`/api/want?id=${encodeURIComponent(b.dataset.id)}&on=${b.dataset.on}`, b.dataset.p);", btn
        )
        self.assertIn("renderModal(true)", btn)  # the issue's detail is not live: it repaints after the write
        self.assertIn("t(on ? 'want_btn' : 'unwant_btn')", btn)
        self.assertIn("WANT_TAG(c)", js[js.index("const storyRow") : js.index("function laneGroups")])
        detail = js[js.index("  h(key){") : js.index("  ag(key){")]
        self.assertIn("c.wanted && [t('wanted_lbl'), t('wanted_since', {when: esc(whenShort(c.wanted))})]", detail)
        self.assertIn("]) + wantButton(c)", detail)
        self.assertIn("m.want ? ", js[js.index("const moveText") : js.index("const activityHtml")])
        self.assertEqual(js.count("moveText(m"), 2)  # the detail's activity and «Your last moves»
        es = json.loads((ROOT / "panel/strings.es.json").read_text())
        self.assertEqual((es["want_btn"], es["unwant_btn"]), ("La quiero hacer", "Ya no la quiero hacer"))
        # «I don't want it», next to it on a triage issue, asks before it cancels; its reason names no one
        self.assertIn("const rej = c.lane==='triage' ?", btn)
        self.assertIn("onclick=\"reject(this)\">${t('reject_btn')}</button>", btn)
        rej = js[js.index("async function reject") : js.index("const WANT_TAG")]
        self.assertLess(rej.index("if(!confirm(t('confirm_reject', {id: b.dataset.id}))) return;"), rej.index("post("))
        self.assertIn("await post(`/api/reject?id=${encodeURIComponent(b.dataset.id)}`, b.dataset.p);", rej)
        self.assertIn("reject: 'reject_act'", js[js.index("const moveText") : js.index("const activityHtml")])
        en = json.loads((ROOT / "panel/strings.en.json").read_text())
        self.assertEqual((es["reject_btn"], en["reject_btn"]), ("No la quiero", "I don't want it"))
        self.assertEqual(
            (es["reject_reason"], en["reject_reason"]),
            ("el dueño la descartó desde el panel", "the owner discarded it from the panel"),
        )

    def test_joint_state_and_titles(self):
        code, d = self.req("/api/state?p=*")
        self.assertEqual(code, 200)
        self.assertEqual({p["name"] for p in d["projects"]}, {"fake", "other"})
        by = {p["name"]: p for p in self.req("/api/projects")[1]["projects"]}
        # the panel shows a product by its [panel] title, else by its registry name (still the id), and its language
        self.assertEqual((by["other"]["title"], by["fake"]["title"]), ("Other", "fake"))
        self.assertEqual((by["other"]["language"], by["fake"]["language"]), ("es", "es"))
        self.assertTrue(by["other"]["wiki"])
        self.assertFalse(by["fake"]["wiki"])
        self.assertIn("activity", d["projects"][0])
        # the machine's stats change every few seconds: they come with /api/projects, not the polled state
        self.assertNotIn("mac", d)
        self.assertIn("top", self.req("/api/projects")[1]["mac"])

    def test_wiki(self):
        (self.ohome / "wiki/models").mkdir()
        (self.ohome / "raw/reviews").mkdir(parents=True)
        (self.ohome / "raw/reviews/2026-10-30-x.md").write_text("# X\n")
        (self.ohome / "wiki/models/m-one.md").write_text(
            "---\ntype: model\nstatus: activo\ntags: [moe, fast]\nsources: [raw/reviews/2026-10-30-x.md]\n---\n"
            "# Model One\n\nUses [[other]] and `2026-10-30-x.md`; búsqueda rápida.\n"
        )
        pages = self.req("/api/wiki?p=other")[1]["pages"]
        self.assertEqual(
            [(x["n"], x["type"], x["status"], x["tags"]) for x in pages],
            [("m-one", "model", "activo", ["moe", "fast"])],
        )
        page = self.req("/api/wiki/page?p=other&n=m-one")[1]
        self.assertEqual(page["title"], "Model One")
        self.assertEqual(
            page["docs"],
            {
                "raw/reviews/2026-10-30-x.md": "raw/reviews/2026-10-30-x.md",
                "2026-10-30-x.md": "raw/reviews/2026-10-30-x.md",
            },
        )
        self.assertEqual(self.req("/api/wiki/search?p=other&q=busqueda")[1]["results"][0]["n"], "m-one")
        self.assertEqual(self.req("/api/wiki/page?p=other&n=../backlog")[0], 404)
        self.assertEqual(self.req("/api/wiki?p=fake")[0], 404)

    def test_move_a_project_and_log_recurring(self):
        b = Backlog(self.project)
        with b.mutate() as data:
            b.add_project(data, "A2", "Second project", "")
        code, d = self.req("/api/move?p=fake&kind=project&id=A2&a=0", "POST")
        self.assertEqual(code, 200, d)
        self.assertEqual([x["id"] for x in b.load()["projects"]], ["A2", "A1"])
        (self.ohome / "wiki/log.md").write_text(f"## [{time.strftime('%Y-%m-%d')}] lint | weekly\n")
        rec = {t["name"]: t for t in self.req("/api/state?p=other")[1]["project"]["recurring"]}
        self.assertTrue(rec["Lint de la wiki"]["parts"][0]["last"])  # read from the log
        self.assertFalse(rec["Cierre de tanda"]["parts"][0]["last"])  # never: late, but not shown so (inactive)
        self.assertEqual({t["late"] for t in rec.values()}, {False})

    def test_owner_takes_a_lock(self):
        code, _d = self.req("/api/lock/take?r=gadget&min=1", "POST")
        self.assertEqual(code, 200)
        panel_locks.OWNER.tick()
        st = lock.status("gadget")
        self.assertEqual(st["holders"][0]["who"], "boss")
        self.assertIsNone(st["next"])
        self.req("/api/lock/release?r=gadget", "POST")
        self.assertEqual(lock.status("gadget")["holders"], [])
        self.assertEqual(self.req("/api/lock/take?r=nope", "POST")[0], 400)

    def test_owner_extends_queues_and_recovers(self):
        # free: «Usar» takes it at once (no wait for the watcher) with an expiry written in the owner line
        code, d = self.req("/api/lock/take?r=gadget&min=60", "POST")
        g = {x["name"]: x for x in d["locks"]}["gadget"]
        self.assertEqual((g["owner_state"], g["me"]), ("holds", "boss"))
        exp = panel_locks.OWNER.state("gadget")["expires"]
        # +30 min moves the expiry and the owner line
        code, d = self.req("/api/lock/extend?r=gadget&min=30", "POST")
        self.assertEqual(code, 200)
        new = panel_locks.OWNER.state("gadget")["expires"]
        self.assertEqual(new - exp, panel.timedelta(minutes=30))
        owner = (lock.paths("gadget")["dir"] / "owner").read_text()
        self.assertIn(f"expires {new:%Y-%m-%d %H:%M:%S})", owner)
        # a restarted panel recovers it with that expiry
        panel_locks.OWNER.__init__()
        self.assertEqual(panel_locks.OWNER.state("gadget")["state"], "no")
        panel_locks.OWNER.recover()
        self.assertEqual(panel_locks.OWNER.state("gadget")["state"], "holds")
        self.assertEqual(panel_locks.OWNER.state("gadget")["expires"], new)
        self.req("/api/lock/release?r=gadget", "POST")
        self.assertEqual(lock.status("gadget")["holders"], [])
        # nothing to extend when it is not his, nor without an expiry
        self.assertEqual(self.req("/api/lock/extend?r=gadget", "POST")[0], 400)
        self.req("/api/lock/take?r=gadget&min=0", "POST")
        self.assertEqual(self.req("/api/lock/extend?r=gadget", "POST")[0], 400)
        self.req("/api/lock/release?r=gadget", "POST")
        # held by someone else: «Ponerme en cola» leaves him waiting with the turn; «Cancelar» gives the turn back
        p = lock.paths("widget")
        p["next"].write_text("ag2: next\n")
        code, d = self.req("/api/lock/take?r=widget&min=60", "POST")
        w = {x["name"]: x for x in d["locks"]}["widget"]
        self.assertEqual((w["owner_state"], w["holders"][0]["who"]), ("waiting", "ag1"))
        self.assertTrue(lock.status("widget")["next"].startswith("boss:"))
        self.assertEqual(self.req("/api/lock/extend?r=widget&min=30", "POST")[0], 200)  # asks for 90 min now
        self.assertEqual(panel_locks.OWNER.state("widget")["minutes"], 90)
        self.req("/api/lock/release?r=widget", "POST")
        self.assertEqual(lock.status("widget")["next"], "ag2: next")
        self.assertEqual(lock.status("widget")["holders"][0]["who"], "ag1")
        # the GPU is only the inactive product's: not from here
        locks = {x["name"]: x for x in self.req("/api/projects")[1]["locks"]}
        self.assertEqual((locks["gpu"]["takeable"], locks["gpu"]["takers"]), (False, []))
        self.assertEqual(self.req("/api/lock/extend?r=gpu", "POST")[0], 403)

    def test_busy_output_in_the_resource_card(self):
        """A resource with `busy` carries what that command prints (run in the background, cached for the
        probe's ttl, cut to its limit), a slow command shows as a timeout and not as a failed request, and a resource
        without `busy` has no such field."""
        self.write_toml(
            self.toml.replace(
                "[resources.widget]",
                "[resources.gadget]\nbusy = \"echo one; echo 'two <b>'; "
                'echo $FOREMAN_LOCK_WHO; echo run >> runs.log"\n\n[resources.widget]',
            )
        )
        panel_locks.BUSY = panel_locks.BusyProbe(ttl=60, timeout=5, limit=4000)
        locks = {x["name"]: x for x in self.req("/api/projects")[1]["locks"]}
        self.assertIn(locks["gadget"]["busy"]["state"], ("pending", "ok"))
        self.assertNotIn("busy", locks["widget"])
        panel_locks.BUSY.wait(10)
        locks = {x["name"]: x for x in self.req("/api/projects")[1]["locks"]}
        b = locks["gadget"]["busy"]
        self.assertEqual(b["state"], "ok")
        self.assertEqual(b["output"], "one\ntwo <b>\nboss")  # as printed: the page escapes it
        self.assertFalse(b["truncated"])
        self.assertRegex(b["since"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d$")
        self.assertNotIn("checked", b)
        self.assertNotIn("busy", locks["widget"])
        # within the ttl, more requests reuse it: the command ran once (in the declaring project's home)
        for _ in range(3):
            self.req("/api/state?p=fake")
        panel_locks.BUSY.wait(10)
        self.assertEqual(panel_locks.BUSY.runs, 1)
        self.assertEqual((self.home / "runs.log").read_text(), "run\n")
        # a hanging command: the request answers at once, the card says timeout once the limit passes
        self.write_toml(
            self.toml.replace(
                "[resources.widget]", '[resources.gadget]\nbusy = "echo started; sleep 30"\n\n[resources.widget]'
            )
        )
        panel_locks.BUSY = panel_locks.BusyProbe(ttl=60, timeout=0.5, limit=4000)
        t0 = time.time()
        self.assertEqual(self.req("/api/projects")[0], 200)
        self.assertLess(time.time() - t0, 10)  # the command hangs 30 s: this did not wait for it
        panel_locks.BUSY.wait(10)
        b = {x["name"]: x for x in self.req("/api/projects")[1]["locks"]}["gadget"]["busy"]
        self.assertEqual((b["state"], b["output"]), ("timeout", "started"))
        # a failing command and a chatty one
        self.write_toml(
            self.toml.replace(
                "[resources.widget]",
                '[resources.gadget]\nbusy = "echo oops >&2; exit 3"\n\n'
                '[resources.widget]\nbusy = "yes x | head -c 100000"\n',
            )
        )
        panel_locks.BUSY = panel_locks.BusyProbe(ttl=60, timeout=5, limit=1000)
        self.req("/api/projects")
        panel_locks.BUSY.wait(10)
        locks = {x["name"]: x for x in self.req("/api/projects")[1]["locks"]}
        self.assertEqual((locks["gadget"]["busy"]["state"], locks["gadget"]["busy"]["output"]), ("error", "oops"))
        self.assertTrue(locks["widget"]["busy"]["truncated"])
        self.assertLessEqual(len(locks["widget"]["busy"]["output"]), 1000)
        panel_locks.BUSY = panel_locks.BusyProbe()
        # the page shows it as text, never as HTML
        js = (ROOT / "panel/app.js").read_text()
        self.assertIn('<pre class="busyout">${esc(', js)
        for lang in ("en", "es"):
            self.assertIn("busy_timeout", self.req(f"/api/strings?lang={lang}")[1])

    def test_docs_stay_inside(self):
        code, d = self.req("/api/doc?p=fake&path=reviews/2026-10-30-plan.md")
        self.assertEqual((code, d["md"]), (200, "# Plan\n"))
        for bad in ("../projects.toml", "/etc/passwd", "foreman.toml", "reviews/../foreman.toml"):
            self.assertEqual(self.req(f"/api/doc?p=fake&path={bad}")[0], 404, bad)

    def test_docs_of_another_product_by_absolute_path(self):
        # AB-10: a backlog links another product's reports with absolute paths; only another registered product's
        # reviews, notes or report prefixes are served, never any other file
        rep = self.ohome / "raw/reviews/2026-10-30-fo1.md"
        rep.parent.mkdir(parents=True, exist_ok=True)
        rep.write_text("# AB-1\n")
        code, d = self.req(f"/api/doc?p=fake&path={rep}")
        self.assertEqual((code, d.get("md")), (200, "# AB-1\n"))
        for bad in (self.ohome / "wiki/backlog.json", self.oconfig, self.ohome / "wiki/x.md", "/etc/passwd"):
            self.assertEqual(self.req(f"/api/doc?p=fake&path={bad}")[0], 404, bad)
        # with that product's reviews as an absolute report prefix, the backlog still loads and links the report
        self.write_toml(self.toml.replace("[ids]", f'[ids]\nreport_prefixes = ["reviews/", "{rep.parent}/"]'))
        b = Backlog(self.project)
        with b.mutate() as data:
            b.set_sub(data, "PF-3", "T1", status="done", report=str(rep))
        code, d = self.req("/api/state?p=fake")
        self.assertEqual((code, d["project"]["backlog_error"]), (200, None))
        cards = self.cards("fake")
        self.assertEqual(cards["PF-3"]["reports"], [{"sub": "T1", "doc": str(rep)}])

    def test_cli_starts_and_answers(self):
        """The CLI on a port the OS gave as free (self.port + 1 could be another worker's server); if another process
        takes it in between, one more try on a new one. A failure shows what the CLI printed."""
        import socket

        def start():
            with socket.socket() as so:
                so.bind(("127.0.0.1", 0))
                port = so.getsockname()[1]
            proc = subprocess.Popen(
                [sys.executable, str(ROOT / "lib/main.py"), "panel", "--port", str(port), "--token", "k3y"],
                env=self.env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
            )
            line = proc.stdout.readline()
            if f"127.0.0.1:{port}" in line:
                return proc, port, line
            try:  # a CLI that failed has exited; one that printed something else first is stopped
                rest = proc.communicate(timeout=10)[0]
            except subprocess.TimeoutExpired:
                proc.kill()
                rest = proc.communicate()[0]
            return None, port, line + rest
            return None, port, out

        proc, port, out = start()
        if proc is None and "Address already in use" in out:
            proc, port, out = start()
        self.assertIsNotNone(proc, f"the panel CLI did not start on port {port}:\n{out}")
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/projects?t=k3y", timeout=20) as r:
                self.assertEqual(len(json.loads(r.read())["projects"]), 2)
        finally:
            proc.terminate()
            proc.wait(10)
            proc.stdout.close()

    def test_decision_card_lead_and_recommended_option(self):
        """Each decision carries the first sentence of its text (`lead`, the card shows it under the title)
        and the option its recommendation names (`recommended`, its button says «(recommended)»); the detail keeps the
        labelled «Recommendation» row and marks the option too."""
        b = Backlog(self.project)
        with b.mutate() as data:
            b.add_decision(
                data,
                "Area J2",
                "Does the warning also go to the other products?",
                "Today only this product's orchestrator sees it. With «yes» everyone gets it; with «no» "
                "nothing changes.",
                ["yes", "no"],
                "yes: one more step, and today they miss it",
            )
        code, d = self.req("/api/state?p=fake")
        self.assertEqual(code, 200)
        dec = {x["id"]: x for x in d["project"]["decisions"]}
        j2 = dec["Area J2"]
        self.assertEqual(j2["lead"], "Today only this product's orchestrator sees it.")
        self.assertEqual(j2["recommended"], "yes")
        self.assertEqual(j2["recommendation"], "yes: one more step, and today they miss it")
        self.assertEqual((dec["Area J1"]["lead"], dec["Area J1"]["recommended"]), ("Context.", ""))
        # the first sentence: up to «.?!…» before a capital or an opening mark, or the first line break
        self.assertEqual(panel_cards.lead("**What**: the copy. More text"), "What: the copy.")
        self.assertEqual(panel_cards.lead("Use e.g. the GPU. Then"), "Use e.g. the GPU.")
        self.assertEqual(panel_cards.lead("¿Ahora? ¿O luego?"), "¿Ahora?")
        self.assertEqual(panel_cards.lead("one line\nanother"), "one line")
        self.assertEqual(panel_cards.lead(""), "")
        self.assertEqual(len(panel_cards.lead("word " * 100)), 240)
        # the option the recommendation names: at its start, quoted, by its letter or its label; none when unclear
        rec = panel_cards.recommended_option
        self.assertEqual(rec(["sí", "no"], "sí: cuesta un paso más"), "sí")
        self.assertEqual(rec(["a: yes", "b: no"], "b: safer"), "b: no")
        self.assertEqual(rec(["a: yes", "b: no"], "no, because it is safer"), "b: no")
        self.assertEqual(rec(["yes", "no"], "I recommend no, since it is cheaper"), "no")
        self.assertEqual(
            rec(["un título llano", "título técnico + línea llana"], "Recomiendo «un título llano»: es más simple"),
            "un título llano",
        )
        self.assertEqual(rec(["sí", "sí, con aviso", "no"], "sí, con aviso: mejor"), "sí, con aviso")
        self.assertEqual(rec(["sí", "no"], "depende del coste"), "")
        self.assertEqual(rec(["yes", "no"], ""), "")
        self.assertEqual(rec(["no", "nota"], "nota: x"), "nota")
        # the page: the lead under the title, the mark on the button (and on the chip of a read-only product) and in
        # the detail's options, the labelled recommendation row
        js = (ROOT / "panel/app.js").read_text()
        self.assertIn("(x.lead ? `<div class=\"clead\">${esc(x.lead)}</div>` : '')", js)
        self.assertIn("o===x.recommended ? ` <span class=\"recm\">${t('recommended_mark')}</span>` : ''", js)
        self.assertEqual(js.count("${optLabel(x, o)}"), 2)
        self.assertIn("[t('options'), x.options.map(o => optLabel(x, o)).join(' · ')]", js)
        self.assertIn("x.recommendation && [t('recommendation'), esc(x.recommendation)]", js)
        self.assertIn(".clead{", (ROOT / "panel/app.css").read_text())

    def test_decision_recommend_option(self):
        """`decision add --recommend-option` stores the exact option as `recommended` (exit 2 if it is none of the
        options) and the panel marks it even when the recommendation does not name it."""
        args = [
            "backlog",
            "decision",
            "add",
            "--id",
            "Area J3",
            "--title",
            "Which copy do we keep on the laptop?",
            "--text",
            "Both work today. The first is smaller.",
            "--option",
            "the small copy",
            "--option",
            "the full copy",
            "--recommend",
            "the second one: it has everything",
        ]
        r = self.cli(*args, "--recommend-option", "the big copy")
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("--recommend-option 'the big copy' is not one of the --option texts", r.stderr)
        self.assertNotIn("Area J3", [d["id"] for d in Backlog(self.project).load()["decisions"]])
        r = self.cli(*args, "--recommend-option", "the full copy")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        data = Backlog(self.project).load()
        dec = {d["id"]: d for d in data["decisions"]}
        self.assertEqual(dec["Area J3"]["recommended"], "the full copy")
        self.assertNotIn("recommended", dec["Area J1"])  # optional key: absent when not given
        self.assertEqual(
            panel_cards.recommended_option(dec["Area J3"]["options"], dec["Area J3"]["recommendation"]), ""
        )
        code, d = self.req("/api/state?p=fake")
        self.assertEqual(code, 200)
        cards = {x["id"]: x for x in d["project"]["decisions"]}
        self.assertEqual(cards["Area J3"]["recommended"], "the full copy")
        self.assertEqual(cards["Area J1"]["recommended"], "")  # old data: inferred from the text, as before
        self.assertEqual(self.cli("check").returncode, 0)
        for lang, label, mark in (("en", "Recommendation", "(recommended)"), ("es", "Recomendación", "(recomendada)")):
            st = self.req(f"/api/strings?lang={lang}")[1]
            self.assertEqual((st["recommendation"], st["recommended_mark"]), (label, mark))

    def test_owner_strings_without_jargon(self):
        """The panel's texts for the owner say what happens in plain words (docs/owner.md): no command or
        tool names in the resource and orchestrator notices, the Spanish board in Spanish. Same keys in both
        languages, the placeholders each text had, and `foreman plain` finds nothing to explain in any of them."""
        from foreman import plain

        en = json.loads((ROOT / "panel/strings.en.json").read_text())
        es = json.loads((ROOT / "panel/strings.es.json").read_text())

        def flat(d, pre=""):
            for k, v in d.items():
                yield from flat(v, f"{pre}{k}.") if isinstance(v, dict) else [(pre + k, v)]

        fen, fes = dict(flat(en)), dict(flat(es))
        self.assertEqual(set(fen), set(fes))
        for k in fen:
            self.assertEqual(set(re.findall(r"\{\w+\}", fen[k])), set(re.findall(r"\{\w+\}", fes[k])), k)
        for k in ("busy_title", "busy_timeout", "busy_error"):
            for st in (en, es):
                self.assertNotIn("busy", st[k])
                self.assertNotIn("lock", st[k])
        self.assertEqual(es["busy_error"], "no se pudo saber quién lo usa: la comprobación falló")
        self.assertEqual(en["busy_timeout"], "could not tell who uses it: the check took too long")
        for k in (
            "orch_gone_tip",
            "orch_no_loop_tip",
            "orch_silent_tip",
        ):
            for st in (en, es):
                for word in ("Cron", "foreman session", "marked", "marcado", "relieves"):
                    self.assertNotIn(word, st[k], k)
        self.assertEqual(es["n_ready"], "{n} por empezar")
        for k, v in es["lanes"].items():
            self.assertNotEqual(v, en["lanes"][k], k)
        for k in ("subs", "nothing_in_lane", "project_status.backlog"):
            self.assertFalse(re.search(r"(?i)updates|sub-issues|status|backlog", fes[k]), k)
        # the rules of `foreman plain` find nothing in any text (each key on its own, as the owner reads it)
        for lang, fl in (("en", fen), ("es", fes)):
            for k, v in fl.items():
                self.assertEqual(plain.findings(self.project, v), [], (lang, k, v))


class MachineResourcePanelTest(PanelCase):
    """A machine resource ([resources.NAME] in the registry) shows with the machine's table whatever the
    registry order (lock paths, busy, take and release), lists the products that use it, runs its `busy` in the
    registry's folder, and is listed but not takeable when no active product uses it. Product resources are as
    before."""

    def setUp(self):
        super().setUp()
        self.mlock = self.tmp / "machine-locks"
        busy = self.tmp / "machine-busy.sh"
        busy.write_text('#!/bin/bash\necho "machine busy in $(pwd -P)"\n')
        busy.chmod(0o755)
        # `first` comes before `fake` in the registry and still copies its own keys for gadget
        self.first = self.tmp / "first"
        self.first.mkdir()
        (self.first / "foreman.toml").write_text(
            f'[project]\nname = "first"\nowner = "Owner"\n\n[resources.gadget]\nbusy = "echo first; pwd -P"\n'
            f'dir = "{self.tmp / "first-gadget-lock"}"\nguard = ["make gadget"]\n'
        )
        self.machine = (
            f'\n[resources.gadget]\ndir = "{self.mlock / "gadget"}"\nqueue = "{self.mlock / "gadget.queue"}"\n'
            f'next = "{self.mlock / "gadget.next"}"\nbusy = "{busy}"\n\n'
            f'[resources.sim]\ndir = "{self.mlock / "sim"}"\nbusy = "{busy}"\n'
        )
        self.register(first_first=True)
        panel_locks.BUSY = panel_locks.BusyProbe(ttl=60, timeout=5, limit=4000)

    def register(self, first_first):
        entry = lambda n, h, m: f'[[project]]\nname = "{n}"\nhome = "{h}"\nmode = "{m}"\n'  # noqa: E731
        first, fake = entry("first", self.first, "active"), entry("fake", self.home, "active")
        (self.tmp / "projects.toml").write_text(
            '[panel]\nlanguage = "es"\nwho = "boss"\n\n'
            + "\n".join([first, fake] if first_first else [fake, first])
            + f'\n{entry("other", self.ohome, "inactive")}config = "{self.oconfig}"\n'
            + self.machine
        )

    def tearDown(self):
        panel_locks.BUSY = panel_locks.BusyProbe()
        super().tearDown()

    def locks(self):
        self.req("/api/projects")
        panel_locks.BUSY.wait(10)
        return {x["name"]: x for x in self.req("/api/projects")[1]["locks"]}

    def test_machine_table_wins_over_the_first_product(self):
        res = panel_registry.REG.resources()
        cfg, users, machine = res["gadget"]
        self.assertEqual((users, machine), (["first", "fake"], True))
        self.assertEqual(cfg["dir"], str(self.mlock / "gadget"))
        self.assertNotIn("guard", [k for k, v in cfg.items() if v])  # the machine's table, not a product's
        g = self.locks()["gadget"]
        self.assertEqual(
            (g["machine"], g["projects"], g["takeable"], g["takers"]),
            (True, ["first", "fake"], True, ["first", "fake"]),
        )
        # busy: the machine's command, run in the registry's folder (not in `first`'s home)
        self.assertEqual(g["busy"]["state"], "ok")
        self.assertEqual(g["busy"]["output"], f"machine busy in {self.tmp}")
        # the same whatever the registry order
        self.register(first_first=False)
        cfg2, users2, _ = panel_registry.REG.resources()["gadget"]
        self.assertEqual(users2, ["fake", "first"])
        self.assertEqual(cfg2, cfg)

    def test_owner_takes_and_releases_a_machine_resource(self):
        code, d = self.req("/api/lock/take?r=gadget&min=5&p=fake", "POST")
        self.assertEqual(code, 200, d)
        g = {x["name"]: x for x in d["locks"]}["gadget"]
        self.assertEqual((g["owner_state"], g["holders"][0]["who"]), ("holds", "boss"))
        self.assertTrue((self.mlock / "gadget/owner").read_text().startswith("boss:"))
        self.assertFalse((self.tmp / "first-gadget-lock").exists())
        self.assertEqual(self.req("/api/lock/extend?r=gadget&min=30", "POST")[0], 200)
        self.req("/api/lock/release?r=gadget", "POST")
        self.assertFalse((self.mlock / "gadget").exists())
        self.assertEqual(lock.status("gadget", panel_registry.REG.resources()["gadget"][0])["holders"], [])

    def test_machine_resource_nobody_uses(self):
        s = self.locks()["sim"]
        self.assertEqual((s["machine"], s["projects"], s["takeable"], s["takers"]), (True, [], False, []))
        self.assertEqual(s["busy"]["output"], f"machine busy in {self.tmp}")
        self.assertEqual(self.req("/api/lock/take?r=sim&min=5", "POST")[0], 403)
        self.assertFalse((self.mlock / "sim").exists())

    def test_product_resources_unchanged(self):
        locks = self.locks()
        w = locks["widget"]
        self.assertEqual((w["machine"], w["projects"], w["takeable"]), (False, ["fake"], True))
        self.assertEqual(w["holders"][0]["who"], "ag1")
        self.assertNotIn("busy", w)
        gpu = locks["gpu"]
        self.assertEqual((gpu["machine"], gpu["takeable"], gpu["projects"]), (False, False, ["other"]))
        cfg, users, machine = panel_registry.REG.resources()["widget"]
        self.assertEqual((users, machine, cfg["dir"]), (["fake"], False, ""))


class SlowGithubPanelTest(PanelCase):
    """The panel does not wait for GitHub: a fake `gh` that blocks until the test opens its gate stands in
    for the network, so "answered without waiting" and "both ran at once" are shown by the gate, not by a stopwatch
    (the time bounds left are far from GH_MAX, the longest a closed gate can hold a gh)."""

    toml = (
        BASE_TOML + '\n[repos.app]\npath = "{tmp}/app"\ngithub = "acme/app"\n\n'
        '[repos.web]\npath = "{tmp}/web"\ngithub = "acme/web"\n'
    )
    GH_MAX = 15  # s: a gh whose gate is never opened gives up after this (the panel's own timeout is 20)
    FAST = 5  # s: what "at once" means under a loaded machine; well below GH_MAX

    def setUp(self):
        super().setUp()
        os.environ.pop("FOREMAN_PANEL_PRS", None)
        gh = self.tmp / "bin/gh"
        gh.parent.mkdir()
        pr = [
            {
                "number": 7,
                "title": "A change",
                "headRefName": "feat",
                "isDraft": False,
                "url": "https://x/7",
                "statusCheckRollup": [{"conclusion": "SUCCESS"}],
                "reviewDecision": "",
            }
        ]
        self.gate = self.tmp / "gh-gate"
        gh.write_text(
            f"#!/bin/sh\necho \"$@\" >> '{self.tmp}/gh.log'\nn=0\n"
            f"while [ ! -e '{self.gate}' ] && [ $n -lt {self.GH_MAX * 20} ]; do\n"
            f"  sleep 0.05; n=$((n+1))\ndone\necho '{json.dumps(pr)}'\n"
        )
        gh.chmod(0o755)
        os.environ["FOREMAN_GH"] = str(gh)
        for repo in ("app", "web"):
            d = self.tmp / repo
            d.mkdir()
            subprocess.run(["git", "init", "-q", "-b", "main", str(d)], check=True)
            subprocess.run(
                [
                    "git",
                    "-C",
                    str(d),
                    "-c",
                    "user.name=t",
                    "-c",
                    "user.email=t@t",
                    "commit",
                    "-q",
                    "--allow-empty",
                    "-m",
                    "first",
                ],
                check=True,
            )
            for args in (
                ["checkout", "-q", "-b", "feat"],
                ["commit", "-q", "--allow-empty", "-m", "feat"],
                ["checkout", "-q", "main"],
            ):
                subprocess.run(["git", "-C", str(d), "-c", "user.name=t", "-c", "user.email=t@t", *args], check=True)
        panel_cache._memo.clear()

    def tearDown(self):
        self.open_gate()  # no gh is left blocked, and none outlives the test's folder
        memo_wait(20)
        super().tearDown()

    def joint_prs(self):
        code, d = self.req("/api/state?p=*")
        self.assertEqual(code, 200)
        fake = next(p for p in d["projects"] if p["name"] == "fake")
        return {r["key"]: r["prs"] for r in fake["repos"]}, fake

    def open_gate(self):
        self.gate.touch()

    def wait_gh_calls(self, n):
        """True once n gh have started (each logs its call before it blocks on the gate)."""
        end = time.monotonic() + self.FAST
        while self.gh_calls() < n and time.monotonic() < end:
            time.sleep(0.02)
        return self.gh_calls() >= n

    def gh_calls(self):
        f = self.tmp / "gh.log"
        return len(f.read_text().splitlines()) if f.exists() else 0

    def test_first_state_does_not_wait_for_github(self):
        t = time.time()
        prs, _ = self.joint_prs()  # the gate is closed: an answer now did not wait for gh
        self.assertLess(time.time() - t, self.FAST)
        self.assertEqual(prs, {"app": None, "web": None})  # "loading": the page already shows it
        # both gh run at once, not one after the other: the two have started while the gate holds the first
        self.assertTrue(self.wait_gh_calls(2), self.gh_calls())
        # a second request while gh is still running neither waits nor starts another gh
        t = time.time()
        self.assertEqual(self.joint_prs()[0], {"app": None, "web": None})
        self.assertLess(time.time() - t, self.FAST)
        self.open_gate()
        self.assertTrue(memo_wait(20))
        self.assertEqual(self.gh_calls(), 2)
        prs, fake = self.joint_prs()
        self.assertEqual([p["number"] for p in prs["app"]], [7])
        self.assertEqual(prs["app"][0]["checks"], {"ok": 1, "fail": 0, "pending": 0})
        app = next(r for r in fake["repos"] if r["key"] == "app")
        self.assertEqual({b["branch"]: (b["col"], b["pr"]) for b in app["branches"]}, {"feat": ("pr", 7)})

    def test_warm_up_at_start(self):
        t = time.time()
        panel.warm_up()  # with the gate closed: it starts gh and does not wait for it
        self.assertLess(time.time() - t, self.FAST)
        self.assertTrue(self.wait_gh_calls(2), self.gh_calls())
        self.open_gate()
        self.assertTrue(memo_wait(20))
        self.assertEqual(self.gh_calls(), 2)
        t = time.time()
        prs, _ = self.joint_prs()
        self.assertLess(time.time() - t, self.FAST)
        self.assertEqual({k: [p["number"] for p in v] for k, v in prs.items()}, {"app": [7], "web": [7]})
        self.assertEqual(self.gh_calls(), 2)

    def test_no_contention_between_keys(self):
        """A slow computation holds only its own key: another key is served at once, the same key waits for it and
        is computed once."""
        started, release, calls = threading.Event(), threading.Event(), []

        def slow():
            calls.append(1)
            started.set()
            release.wait(10)
            return "slow"

        out = {}
        a = threading.Thread(target=lambda: out.setdefault("a", panel_cache.memo(("t", "slow"), slow)))
        a.start()
        self.assertTrue(started.wait(5))
        t = time.time()
        self.assertEqual(panel_cache.memo(("t", "fast"), lambda: "fast"), "fast")
        self.assertEqual(panel.git(self.tmp / "app", "rev-parse", "--abbrev-ref", "HEAD").strip(), "main")
        self.assertLess(time.time() - t, self.FAST)  # the slow key would hold it 10 s
        b = threading.Thread(target=lambda: out.setdefault("b", panel_cache.memo(("t", "slow"), slow)))
        b.start()
        b.join(0.3)
        self.assertTrue(b.is_alive())  # the same key waits for the computation in flight
        release.set()
        a.join(5)
        b.join(5)
        self.assertEqual((out, len(calls)), ({"a": "slow", "b": "slow"}, 1))
        # with an initial value nothing waits, not even the first call
        hold = threading.Event()
        t = time.time()
        self.assertIsNone(panel_cache.memo(("t", "bg"), lambda: hold.wait(10) and "late", ttl=60, initial=None))
        self.assertLess(time.time() - t, self.FAST)  # waiting would take 10 s
        hold.set()
        self.assertTrue(memo_wait(5))
        self.assertEqual(panel_cache.memo(("t", "bg"), lambda: "again", ttl=60, initial=None), "late")


class LinearPanelTest(PanelCase):
    """The projects and issues in /api/state (project fields, issue fields, issues without project, relations),
    projects without a status shown with one taken from their issues, and an inactive product without late
    recurring tasks."""

    def test_projects_and_issues_in_state(self):
        b = Backlog(self.project)
        with b.mutate() as d:
            b.edit_project(d, "A1", status="in progress", priority="high")
            b.edit(d, "PF-2", priority="high", assignee="ana")
            b.relate(d, "PF-3", "blocked-by", "PF-2")
            b.add_issue(d, "-", "PF-9", "loose", status="triage", criterion="x")
        bl = self.backlog("fake")
        self.assertNotIn("initiatives", bl)
        a1 = bl["projects"][0]
        self.assertEqual((a1["status"], a1["priority"]), ("in progress", "high"))
        self.assertNotIn("status_derived", a1)
        cards = {c["id"]: c for c in a1["issues"]}
        self.assertEqual((cards["PF-2"]["priority"], cards["PF-2"]["assignee"]), ("high", "ana"))
        self.assertNotIn("assignee", cards["PF-3"])
        self.assertEqual(cards["PF-3"]["relations"], {"blocked_by": ["PF-2"]})
        self.assertEqual((cards["PF-3"].get("blocked_rel"), cards["PF-2"].get("blocked_rel")), (["PF-2"], None))
        self.assertEqual(cards["PF-2"]["relations"], {"blocks": ["PF-3"]})
        self.assertEqual([(c["id"], c["status"], c["project"]) for c in bl["issues"]], [("PF-9", "triage", "")])

    def test_list_rows_show_priority(self):
        """An issue row of the list (global and project page) shows its urgency at the left of the id,
        as in Linear, in a fixed 14 px slot with a muted «—» for 'none' or a missing field (so the ids stay aligned),
        reusing the detail's strings."""
        js = (ROOT / "panel/app.js").read_text()
        row = js[js.index("const storyRow") : js.index("function laneGroups")]
        self.assertIn("lead: (drag ? GRIP() : '') + PRIO_ICON(c.priority), icon:", row)
        self.assertIn("meta: subToggle(c)", row)
        self.assertEqual(row.count("PRIO_ICON"), 1)
        prio = js[js.index("const PRIO_ICON") : js.index("const ICONS")]
        self.assertIn("['urgent', 'high', 'medium', 'low'].includes(p)) p = 'none'", prio)
        self.assertNotIn("return ''", prio)  # every row gets the slot
        self.assertIn("p==='none' ? '<path d=\"M3 7h8\"", prio)
        self.assertIn("t('priorities.' + p)", prio)
        self.assertIn('aria-label="${lbl}"><title>${lbl}</title>', prio)
        css = (ROOT / "panel/app.css").read_text()
        self.assertIn(".prio{flex:none;width:14px;", css)
        self.assertIn("padding:0 var(--sp3) 0 calc(var(--sp3) + 44px)", css)  # sub-issues: still under the id
        for lang in ("es", "en"):
            st = self.req(f"/api/strings?lang={lang}")[1]
            self.assertEqual(set(st["priorities"]), {"urgent", "high", "medium", "low", "none"})
            self.assertTrue(st["priority_lbl"])

    def test_projects_without_status_get_a_derived_status(self):
        bl = self.backlog("other")
        e1 = bl["projects"][0]
        self.assertEqual((e1["status"], e1["status_derived"]), ("in progress", True))
        self.assertEqual(bl["issues"], [])

    def test_inactive_product_has_nothing_late(self):
        (self.tmp / "projects.toml").write_text(
            (self.tmp / "projects.toml").read_text().replace('mode = "active"', 'mode = "inactive"')
        )
        (self.project.state / "heartbeats.json").unlink()
        p = self.req("/api/state?p=fake")[1]["project"]
        self.assertEqual([(t["late"], t.get("inactive")) for t in p["recurring"]], [(False, True)])
        over = {x["name"]: x for x in self.req("/api/projects")[1]["projects"]}
        self.assertEqual(over["fake"]["late"], [])

    def test_inactive_product_takes_nothing(self):
        """An inactive product neither takes its resources nor pauses its loop from the panel; a resource it
        shares with an active product is taken through that one."""
        reg = self.tmp / "projects.toml"
        reg.write_text(reg.read_text().replace('mode = "active"', 'mode = "inactive"'))
        panel_registry.REG.__init__()
        over = self.req("/api/projects")[1]
        self.assertEqual({p["name"]: p["active"] for p in over["projects"]}, {"fake": False, "other": False})
        locks = {x["name"]: x for x in over["locks"]}
        self.assertEqual((locks["gadget"]["takeable"], locks["gadget"]["takers"]), (False, []))
        self.assertEqual(self.req("/api/lock/take?r=gadget&min=5&p=fake", "POST")[0], 403)
        self.assertEqual(self.req("/api/pause?on=1&p=fake", "POST")[0], 403)
        self.assertFalse((self.project.state / "pause").exists())
        self.assertEqual(self.req("/api/want?on=1&p=fake&id=PF-3", "POST")[0], 403)  # no orchestrator to take it in
        self.assertEqual(self.req("/api/reject?p=fake&id=PF-3", "POST")[0], 403)
        # a twin, active, declares gadget too: the lock is takeable, with twin as the product that shows the action
        twin = self.tmp / "twin"
        twin.mkdir()
        (twin / "foreman.toml").write_text('[project]\nname = "twin"\nowner = "Owner"\n\n[resources.gadget]\n')
        reg.write_text(reg.read_text() + f'\n[[project]]\nname = "twin"\nhome = "{twin}"\nmode = "active"\n')
        panel_registry.REG.__init__()
        locks = {x["name"]: x for x in self.req("/api/projects")[1]["locks"]}
        self.assertEqual((locks["gadget"]["takeable"], locks["gadget"]["takers"]), (True, ["twin"]))
        self.assertEqual(sorted(locks["gadget"]["projects"]), ["fake", "twin"])
        code, d = self.req("/api/lock/take?r=gadget&min=5&p=twin", "POST")
        self.assertEqual(code, 200, d)
        self.req("/api/lock/release?r=gadget&p=twin", "POST")
        js = (ROOT / "panel/app.js").read_text()
        self.assertIn("p.mode==='inactive'", js)  # the report foot says «Inactivo», no «Pausar»
        self.assertIn("lastData.ps.some(p => (l.takers", js)  # actions only where an active product is shown
        es = self.req("/api/strings?lang=es")[1]
        self.assertEqual(es["inactive"], "Inactivo")

    def test_strings_cover_the_new_views(self):
        es = self.req("/api/strings?lang=es")[1]
        self.assertNotIn("view_initiatives", es)
        self.assertIn("triage", es["lanes"])
        self.assertNotIn("idea", es["lanes"])
        js = (ROOT / "panel/app.js").read_text()
        for old in ("'ahora'", "'trabajo'", "'sistema'", "'rama'", "historias", "epicas", "$('tablero')"):
            self.assertNotIn(
                old,
                js.replace("ahora: 'now'", "")
                .replace("trabajo: 'work'", "")
                .replace("sistema: 'system'", "")
                .replace("historias: 'issues'", "")
                .replace("epicas: 'projects'", ""),
            )

    def test_products_with_warnings_and_broken_stay_listed(self):
        """A product with unknown keys and a newer config schema is listed with its warnings (and has its
        state); one whose config has a bad value, and one with a mode this foreman does not know, stay in the list as
        broken with their error, without taking the others down."""
        for name, toml in (
            ("warned", '[project]\nname = "warned"\nconfig_schema = 99\n[repos.app]\npath = "{d}"\nfuturo = 1\n'),
            ("bad", '[project]\nname = "bad"\n[repos.app]\npath = "{d}"\nmerge = "octopus"\n'),
            ("newer", '[project]\nname = "newer"\n'),
        ):
            d = self.tmp / name
            d.mkdir()
            (d / "foreman.toml").write_text(toml.format(d=d))
        f = self.tmp / "projects.toml"
        f.write_text(
            f.read_text()
            + "".join(
                f'\n[[project]]\nname = "{n}"\nhome = "{self.tmp / n}"\nmode = "{m}"\n'
                for n, m in (("warned", "active"), ("bad", "active"), ("newer", "paused"))
            )
        )
        code, d = self.req("/api/projects")
        self.assertEqual(code, 200)
        by = {p["name"]: p for p in d["projects"]}
        self.assertEqual(set(by), {"fake", "other", "warned", "bad", "newer"})
        self.assertEqual(by["fake"]["warnings"], [])
        self.assertEqual(len(by["warned"]["warnings"]), 2)
        self.assertIn("newer foreman (config schema 99", by["warned"]["warnings"][0])
        self.assertFalse(by["warned"].get("broken"))
        self.assertTrue(by["bad"]["broken"])
        self.assertIn("merge must be one of", by["bad"]["error"])
        self.assertTrue(by["newer"]["broken"])
        self.assertIn("unknown mode 'paused'", by["newer"]["error"])
        self.assertEqual(len(d["errors"]), 2)
        code, st = self.req("/api/state?p=warned")
        self.assertEqual(code, 200)
        self.assertEqual(len(st["project"]["warnings"]), 2)
        self.assertEqual(self.req("/api/state?p=bad")[0], 404)
        # the page shows them: the picker marks both kinds, a broken product shows its error instead of the tabs
        js = (ROOT / "panel/app.js").read_text()
        for part in ("config_broken_tag", "config_warnings_tag", "config_warning", "classList.toggle('broken'"):
            self.assertIn(part, js)
        self.assertIn('id="broken"', (ROOT / "panel/index.html").read_text())


if __name__ == "__main__":
    unittest.main()
