"""The golden output corpus (how it works: tests/golden.py). One class per fixed sequence, so the parallel
selftest spreads them over its workers; each compares its transcript with tests/golden/<name>.txt."""

import datetime as dt
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

import golden
from helpers import ROOT

FIXED_DATE = "2020-01-15T10:00:00"  # the commits of the fixture repos (their hashes do not depend on the run)

# What the guard of `faro` denies: the foreman product's own list (ops/foreman.toml [deny]), frozen here with
# tests/golden_guard.tsv, the table run against it.
FARO_DENY = r"""
[deny]
patterns = ["git remote add", "gh repo create", "(^|\\s)git push", "twine upload", "(^|\\s)(uv|poetry) publish",
            "npm publish", "launchctl (load|bootstrap|enable|kickstart|bootout|unload|remove|disable)",
            "panel service (install|uninstall)", "(^|\\s)(cp|mv|ln|rm|tee)\\s.*Library/LaunchAgents",
            "(^|\\s)rm -rf? .*\\.local/state/foreman", "(^|\\s)rm -rf? .*\\.config/foreman"]
message = "Lista de «nunca sin Marta» de faro (normas.md): publicar y lo compartido de la máquina."
"""

FARO_TOML = (
    """
[project]
name = "faro"
owner = "Marta"
language = "es"
rules = "normas.md"

[paths]
worktrees = "{tmp}/wt"

[backlog]
decisions_page = "pages/decisiones.md"

[ids]
criterion_required = ["FA-"]

[agents]
production_slots = 1
ready_target = 2
production_types = ["dev"]

[loop]
prompt = "Bucle del orquestador de faro"

[repos.app]
path = "{tmp}/faro-app"
remote = "none"
local_ci = "true"

[resources.widget]
guard = ["make up", "simctl boot"]
"""
    + FARO_DENY
    + """
[publish]
means = ["crear un remoto o hacer push"]

[check]
guard_table = "guard.tsv"

[[recurring]]
name = "Bucle del orquestador de faro"
every = "every 60 min"
sources = [{ heartbeat = "loop", max_h = 2 }]
"""
)

HARBOR_TOML = """
[project]
name = "harbor"
owner = "Sam"
language = "en"

[paths]
worktrees = "{tmp}/wt"

[backlog]
decisions_page = "pages/wiki.md"
decisions_section = "## Open decisions"

[ids]
issue = "^HB-\\\\d+$"

[agents]
production_slots = 2
ready_target = 1

[loop]
prompt = "harbor orchestrator loop"

[repos.api]
path = "{tmp}/harbor-api"
remote = "none"

[resources.widget]
guard = ["make up"]

[deny]
patterns = ["make deploy", "(^|\\\\s)git push"]
message = "Never without Sam (rules.md)."

[panel]
wiki = "pages"
title = "Harbor"

[[recurring]]
name = "Weekly review"
every = "weekly"
sources = [{ heartbeat = "review", max_h = 168 }]
"""

HARBOR_WIKI = """# Harbor wiki

Hand-written page; foreman rewrites only the section below.

## Open decisions

(generated)

## Notes

Left as it is.
"""

# Each product: its toml, its language's texts for the fixed sequences.
PRODUCTS = {
    "faro": {
        "toml": FARO_TOML,
        "prefix": "FA",
        "area": "Faro",
        "texts": {
            "p1": "Mapa de la costa",
            "p1i": "Dibujar la costa con datos abiertos.",
            "p2": "Avisos",
            "p2i": "Avisar a los barcos.",
            "i1": "Leer las cartas náuticas",
            "i2": "Pintar los faros",
            "i3": "Enviar avisos por radio",
            "i4": "Ordenar las notas",
            "c1": "las cartas se leen en menos de un segundo",
            "c2": "cada faro sale en el mapa",
            "c3": "un aviso llega en un minuto",
            "c4": "notas ordenadas",
            "t1": "Lector de cartas",
            "t2": "Pruebas del lector",
            "block": "espera al lector de cartas",
            "append": "Nota añadida al detalle.",
            "res": "las cartas se leen en 0,4 s",
            "q": "¿Pintamos los faros apagados?",
            "qt": "Algunos faros están apagados; pintarlos confunde, no pintarlos esconde datos.",
            "o1": "a: pintarlos en gris",
            "o2": "b: no pintarlos",
            "rec": "a, porque no se pierde nada",
            "bad": "¿Cerramos FA-2 con el T1 de loop.py?",
            "badt": "Lo pide FA-2 T1.",
            "heading": "Decisiones de Marta",
            "note": "Lectura de cartas",
            "notet": "El lector tarda 0,4 s por carta (medido en 20 cartas).",
            "prog": "Lector de cartas: 0,4 s por carta",
            "done": "Lector entregado, falta la revisión",
            "inbox": "¿Cómo va el mapa?",
            "reply": "Va bien: el lector ya está",
            "plain": "FA-1 T1 listo en loop.py",
        },
    },
    "harbor": {
        "toml": HARBOR_TOML,
        "prefix": "HB",
        "area": "Harbor",
        "texts": {
            "p1": "Berth booking",
            "p1i": "Book a berth from the phone.",
            "p2": "Tide tables",
            "p2i": "Show the tides.",
            "i1": "Booking form",
            "i2": "Payment",
            "i3": "Tide feed",
            "i4": "Tidy the notes",
            "c1": "a booking takes under a minute",
            "c2": "a card payment goes through",
            "c3": "the feed updates hourly",
            "c4": "notes tidy",
            "t1": "Form",
            "t2": "Form tests",
            "block": "waits for the booking form",
            "append": "A note appended to the detail.",
            "res": "a booking takes 40 s",
            "q": "Do we take cash at the berth?",
            "qt": "Some sailors ask to pay cash; taking it means a till and a daily count.",
            "o1": "a: card only",
            "o2": "b: card and cash",
            "rec": "a, no till to count",
            "bad": "Close HB-2 with T1 of loop.py?",
            "badt": "HB-2 T1 asks for it.",
            "heading": "Decisions",
            "note": "Booking form",
            "notet": "The form takes 40 s end to end (measured on 5 bookings).",
            "prog": "Booking form: 40 s end to end",
            "done": "Form delivered, review pending",
            "inbox": "How is the booking going?",
            "reply": "Fine: the form is in",
            "plain": "HB-1 T1 done in loop.py",
        },
    },
}


class GoldenCase(unittest.TestCase):
    """A world in a temporary directory: the registry, the state root and Claude Code's folders, both products with
    their homes and a git repo each. Nothing outside it is read or written."""

    maxDiff = None

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="foreman-golden-")).resolve()
        self.norm = golden.Normaliser(self.tmp)
        self.t = golden.Transcript(self.norm)
        keep = {
            k: v
            for k, v in os.environ.items()
            if not k.startswith(("FOREMAN_", "CLAUDE_", "GIT_")) and k not in ("AGENT",)
        }
        self.env = dict(
            keep,
            PATH=golden.GOLDEN_PATH,  # fixed: the service files capture the caller's PATH
            FOREMAN_REGISTRY=str(self.tmp / "projects.toml"),
            FOREMAN_STATE_HOME=str(self.tmp / "state"),
            FOREMAN_PYTHON=sys.executable,
            FOREMAN_CLAUDE_PROJECTS=str(self.tmp / "claude-projects"),
            FOREMAN_PANEL_PRS="0",
            FOREMAN_HUNG_SHELLS_SH=str(self.tmp / "no_hung_shells.sh"),  # the machine's shells never decide a golden
            FOREMAN_LOCK_POLL="0.2",
            FOREMAN_GH=str(self.tmp / "no-gh"),
            GIT_AUTHOR_NAME="Golden",
            GIT_AUTHOR_EMAIL="golden@example.org",
            GIT_COMMITTER_NAME="Golden",
            GIT_COMMITTER_EMAIL="golden@example.org",
            GIT_AUTHOR_DATE=FIXED_DATE,
            GIT_COMMITTER_DATE=FIXED_DATE,
            GIT_CONFIG_NOSYSTEM="1",
            GIT_CONFIG_GLOBAL=str(self.tmp / "gitconfig"),
        )
        fake = self.tmp / "fake-claude"
        fake.write_text("#!/bin/sh\necho '2.1.300 (Claude Code)'\n")
        fake.chmod(0o755)
        self.env["CLAUDE_CODE_EXECPATH"] = str(fake)
        (self.tmp / "no_hung_shells.sh").write_text("exit 0\n")
        (self.tmp / "gitconfig").write_text("[init]\n\tdefaultBranch = main\n")
        (self.tmp / "claude-projects").mkdir()
        self.homes = {}
        reg = ['[panel]\nlanguage = "es"\nwho = "Marta"\n']
        for name, spec in PRODUCTS.items():
            home = self.tmp / name
            home.mkdir()
            (home / "foreman.toml").write_text(spec["toml"].replace("{tmp}", str(self.tmp)))
            (home / ("normas.md" if name == "faro" else "rules.md")).write_text(f"# Rules of {name}\n")
            (home / "reviews").mkdir()
            (home / f"reviews/2020-01-10-plan-{spec['prefix']}-1.md").write_text("# Plan\n\nT1, T2.\n")
            (home / f"reviews/2020-01-12-{spec['prefix'].lower()}1-t1.md").write_text("# Report T1\n")
            self.homes[name] = home
            reg.append(f'[[project]]\nname = "{name}"\nhome = "{home}"\nmode = "active"\n')
        shutil.copy(ROOT / "tests/golden_guard.tsv", self.homes["faro"] / "guard.tsv")
        (self.homes["harbor"] / "pages").mkdir()
        (self.homes["harbor"] / "pages/wiki.md").write_text(HARBOR_WIKI)
        (self.tmp / "projects.toml").write_text("\n".join(reg))
        for repo in ("faro-app", "harbor-api"):
            d = self.tmp / repo
            d.mkdir()
            for args in (
                ["init", "-q", "-b", "main"],
                ["add", "-A"],
                ["commit", "-q", "--allow-empty", "-m", "initial"],
            ):
                subprocess.run(["git", "-C", str(d), *args], env=self.env, check=True, capture_output=True)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    # ---------- calls ----------
    def cli(self, *args, project=None, cwd=None, stdin="", env=None, label=None):
        """`foreman [--project P] ARGS…`, recorded in the transcript; returns the CompletedProcess."""
        full = (["--project", project] if project else []) + list(args)
        e = dict(self.env, **(env or {}))
        r = golden.run_entry(full, e, cwd or self.tmp, stdin)
        shown = " ".join(
            a if a and re.fullmatch(r"[\w./:=@+,-]+", a) else json.dumps(a, ensure_ascii=False) for a in full
        )
        prefix = " ".join(f"{k}={v}" for k, v in (env or {}).items())
        self.t.call(label or ((prefix + " " if prefix else "") + "foreman " + shown), r)
        return r

    def hook(self, name, event, project="faro", label=None):
        ev = {"cwd": str(self.homes[project]), **event}
        text = json.dumps(ev, ensure_ascii=False, sort_keys=True)
        r = golden.run_entry(["hook", name], self.env, self.homes[project], text)
        self.t.call(label or f"foreman hook {name}", r, stdin=text)
        return r

    def heartbeat(self, project, sid):
        """The heartbeat foreman's hooks module writes at each tick of a live session of `project` (heartbeat.py),
        the session's folder this test's tmp dir."""
        f = self.tmp / "state" / project / "sessions" / f"{sid}.json"
        f.parent.mkdir(parents=True, exist_ok=True)
        at = dt.datetime.now().isoformat(timespec="seconds")
        version = json.loads((ROOT / ".claude-plugin/plugin.json").read_text())["version"]
        rec = {
            "at": at,
            "started": at,
            "busy": False,
            "version": version,
            "claude_code": "2.1.314",
            "cwd": str(self.tmp),
        }
        f.write_text(json.dumps(rec, indent=1))

    def end(self, project, sid):
        """The session ends: the hooks module's session.end asks `hook mod end`, recorded in the transcript."""
        text = json.dumps({"cwd": str(self.homes[project]), "session": sid}, sort_keys=True)
        r = golden.run_entry(["hook", "mod", "end"], self.env, self.homes[project], text)
        self.t.call("foreman hook mod end", r, stdin=text)

    def state_tree(self):
        self.t.tree("state root", self.tmp / "state")

    def check(self, name, folder=golden.GOLDEN):
        tokens = []
        for f in sorted((self.tmp / "state").rglob("launches.json")):
            tokens += list(json.loads(f.read_text()))
        golden.compare(self, name, self.t.value(tokens), folder)


# ---------- the backlog: a fixed sequence of writes, then every read of every object ----------


def build_backlog(case, name, reads=True):
    """The backlog of `name` written from nothing with the CLI (the write sequence of the backlog's golden)."""
    spec = PRODUCTS[name]
    x, P = spec["texts"], spec["prefix"]
    i = lambda n: f"{P}-{n}"  # noqa: E731
    b = lambda *a, **k: case.cli("backlog", *a, project=name, **k)  # noqa: E731
    case.t.section(f"{name}: writes")
    case.cli("init", str(case.homes[name]), "--name", name)  # a home with its foreman.toml and no backlog
    b("project", "add", "A1", "--title", x["p1"], "--intro", x["p1i"], "--status", "in progress", "--priority", "high")
    b("project", "add", "A2", "--title", x["p2"], "--intro", x["p2i"])
    b("add", "A1", "--title", x["i1"], "--criterion", x["c1"], "--plan", f"reviews/2020-01-10-plan-{P}-1.md")
    b(
        "add",
        "A1",
        "--id",
        i(1),
        "--title",
        x["i1"],
        "--criterion",
        x["c1"],
        "--plan",
        f"reviews/2020-01-10-plan-{P}-1.md",
    )
    b("add", "A1", "--title", x["i2"], "--criterion", x["c2"], "--status", "ready")
    b("add", "A2", "--title", x["i3"], "--criterion", x["c3"])
    b("add", "-", "--title", x["i4"], "--criterion", x["c4"], "--status", "triage")
    b("add", "A1", "--title", "missing criterion")  # refused where a criterion is required
    b("relate", i(2), "blocked-by", i(1))
    b("relate", i(3), "related", i(2))
    b("edit", i(1), "--priority", "high", "--assignee", "ana")
    b("sub", i(1), "T1", "--title", x["t1"], "--status", "in progress", "--agent", "ag1", "--branch", "a1/t1")
    b("sub", i(1), "T2", "--title", x["t2"])
    b("status", i(1), "in progress")
    b("edit", i(1), "--append", x["append"])
    b("block", i(3), x["block"])
    b(
        "integrated",
        i(1),
        "T1",
        "--dry-run",
        "--commit",
        "abc1234",
        "--branch",
        "a1/t1",
        "--report",
        f"reviews/2020-01-12-{P.lower()}1-t1.md",
        "--result",
        x["res"],
    )
    b(
        "integrated",
        i(1),
        "T1",
        "--commit",
        "abc1234",
        "--branch",
        "a1/t1",
        "--report",
        f"reviews/2020-01-12-{P.lower()}1-t1.md",
        "--result",
        x["res"],
    )
    b("integrated", i(1), "T2", "--commit", "def5678", "--result", x["res"], "--verdict", "met")
    b("show", i(1))
    b("show", "A1")
    b("archive", i(1))
    b(
        "decision",
        "add",
        "--issue",
        i(2),
        "--title",
        x["q"],
        "--text",
        x["qt"],
        "--option",
        x["o1"],
        "--option",
        x["o2"],
        "--recommend",
        x["rec"],
        "--recommend-option",
        x["o1"],
        "--date",
        "2020-01-17",
    )
    b(
        "decision",
        "add",
        "--title",
        x["bad"],
        "--text",
        x["badt"],
        "--option",
        "a",
        "--option",
        "b",
        "--date",
        "2020-01-17",
    )
    b(
        "decision",
        "add",
        "--title",
        x["bad"],
        "--text",
        x["badt"],
        "--option",
        "a",
        "--option",
        "b",
        "--date",
        "2020-01-18",
        "--as-is",
    )
    b("decision", "resolve", f"{i(2)} J1", "--answer", x["o1"])
    b("page", "decisions", "--heading", x["heading"])
    b("page", "backlog", "--heading", x["heading"])
    b("move", i(4), "--project", "A2")
    b("move", i(3), "--before", i(2))
    b("unblock", i(3))
    b("status", f"{P}-99", "done")
    b("status", i(4), "nonsense")
    b("render", "--check")
    if not reads:
        return
    case.t.section(f"{name}: reads")
    b("show")
    b("show", "--json")
    b("render")
    data = json.loads((case.homes[name] / "backlog.json").read_text())
    ids = [p["id"] for p in data["projects"]] + [
        d["id"] for d in data["decisions"] + data["decisions_resolved"] if d.get("id")
    ]
    issues = (
        [it["id"] for p in data["projects"] for it in p["issues"]]
        + [it["id"] for it in data["issues"]]
        + [it["id"] for g in data["archive"] for it in g["issues"]]
    )
    for ref in ids:
        b("show", ref)
        b("show", ref, "--json")
    for ref in issues:
        b("show", ref)
        b("show", ref, "--json")
        for field in ("subs", "plan", "status", "blocked", "detail", "result"):
            b("get", ref, field)
    b("show", "NOPE-1")
    b("get", i(2), "nofield")


class GoldenBacklogFaro(GoldenCase):
    def test_backlog(self):
        build_backlog(self, "faro")
        self.t.tree("faro home", self.homes["faro"], skip=("reviews", "guard.tsv", "foreman.toml", "normas.md"))
        self.check("backlog-faro.txt")


class GoldenBacklogHarbor(GoldenCase):
    def test_backlog(self):
        build_backlog(self, "harbor")
        self.t.tree("harbor home", self.homes["harbor"], skip=("reviews", "foreman.toml", "rules.md"))
        self.check("backlog-harbor.txt")


# ---------- everyday commands: notes, progress, inbox, launches, agents, the orchestrator's session, the loop ---


def operations(case, name):
    spec = PRODUCTS[name]
    x, P = spec["texts"], spec["prefix"]
    c = lambda *a, **k: case.cli(*a, project=name, **k)  # noqa: E731
    build_backlog(case, name, reads=False)
    case.t = golden.Transcript(case.norm)  # the backlog's own sequence is in backlog-NAME.txt
    case.t.section(f"{name}: configuration")
    c("check")
    case.cli("config", "--projects")
    case.cli("check", cwd=case.homes[name])  # the project from the working directory
    case.cli("--version")
    c("docs")
    c("docs", "nope")
    case.cli("--help")
    case.cli("backlog", "--help")
    case.cli("integrate", "--help")
    case.cli("launch", "--help")
    case.cli("nope")
    c("guard", "git push origin main")
    c("guard", "make up")
    c("guard", "ls -la")
    c("guard")
    c("plain", x["plain"])
    c("plain", "--json", x["plain"])
    case.t.section(f"{name}: notes, progress, inbox, heartbeats")
    c("note", x["note"], x["notet"])
    c("note", "-", x["notet"])
    c("note", x["note"])
    c("progress", x["prog"], env={"FOREMAN_WHO": "ag1"})
    c("progress", "--done", x["done"], env={"FOREMAN_WHO": "ag1"})
    c("progress", "--outcome", "failed", x["done"], env={"FOREMAN_WHO": "ag1"})
    c("progress", x["plain"])
    c("inbox", "add", x["inbox"])
    c("inbox", "new")
    c("inbox", "reply", x["reply"])
    c("inbox", "read")
    c("inbox", "new")
    c("recurring")
    c("recurring", "done", "loop")
    c("recurring", "done", "review")
    c("recurring")
    case.t.section(f"{name}: launches and agents")
    c("launch", "ag2", "--issue", f"{P}-2", "--sub", "T1", "--type", "dev")
    c("launch", "ag2", "--issue", f"{P}-2")
    c("launch", "ag3", "--issue", f"{P}-3", "--sub", "T1", "--no-backlog", "--json")
    c("launch", "ag3", "--cancel")
    c(
        "launch",
        "ag4",
        "--issue",
        f"{P}-4",
        "--repo",
        next(iter(["app"] if name == "faro" else ["api"])),
        "--branch",
        f"{P.lower()}4/t1",
    )
    c("launch", "bad name")
    case.t.section(f"{name}: a branch integrated (no remote: fast-forward)")
    repo = "app" if name == "faro" else "api"
    branch = f"{P.lower()}2/t1"
    c("backlog", "sub", f"{P}-2", "T1", "--title", x["t1"])
    c("launch", "ag6", "--issue", f"{P}-2", "--sub", "T1", "--repo", repo, "--branch", branch)
    wt = case.tmp / "wt" / f"{repo}-ag6"
    (wt / "feature.txt").write_text("the work\n")
    for args in (["add", "-A"], ["commit", "-q", "-m", "The work of T1"]):
        subprocess.run(["git", "-C", str(wt), *args], env=case.env, check=True, capture_output=True)
    (case.homes[name] / f"reviews/2020-01-20-{P.lower()}2-t1.md").write_text("# Report\n")
    c("progress", "--done", x["done"], "--outcome", "partial", env={"FOREMAN_WHO": "ag6"})
    c("integrate", repo, branch, f"{P}-2 T1", "--check")
    c(
        "integrate",
        repo,
        branch,
        f"{P}-2 T1",
        "--report",
        f"reviews/2020-01-20-{P.lower()}2-t1.md",
        "--result",
        x["res"],
    )
    c("integrate", repo, "no/such", f"{P}-2 T1")
    c("backlog", "show", f"{P}-2")
    case.t.section(f"{name}: locks, waits and time limits")
    c("lock", "widget", "acquire", "a test", env={"FOREMAN_WHO": "ag1"})
    c("lock", "widget", "check")
    c("lock", "widget", "release", env={"FOREMAN_WHO": "ag1"})
    c("lock", "widget", "run", "a short job", "echo", "inside the lock", env={"FOREMAN_WHO": "ag1"})
    c("lock", "widget", "check")
    c("lock", "widget")
    c("wait", "file", str(case.homes[name] / "foreman.toml"))
    c("wait", "-t", "0.1", "-i", "0.05", "file", str(case.tmp / "never"))
    c("wait")
    c("time-limit", "5", "echo", "in time")
    c("panel", "service", "print", "--platform", "linux", "--port", "8800")
    c("panel", "service", "print", "--platform", "macos", "--host", "0.0.0.0")
    c("worktree", "--list")
    c("agents")
    c("agents", "--json")
    c("agents", "--name", "a1b2c3", "ag5")
    c("agents", "--stop", "nobody")
    case.t.section(f"{name}: the orchestrator's session and the loop")
    c("session")
    c("loop")
    case.heartbeat(name, "orq-session-1")
    c("session", "orq-session-1", "--name", "orq")
    c("session")
    c("loop")
    c("loop", "--json")
    c("start")  # this session found by its heartbeat in the working folder: already marked
    c("handover")  # gone: an unknown command
    c("sessions")  # replaced (cli.REPLACED): says by what, exit 2
    c("start", env={"CLAUDE_CODE_SESSION_ID": "orq-session-2"})  # an incoming session
    c("start", "--json", env={"CLAUDE_CODE_SESSION_ID": "orq-session-2"})
    c("session", "--all")
    c("session", "--all", "--json")
    c("lock", "widget", "check")
    c("lock", "nope", "check")
    case.state_tree()
    case.t.tree(f"{name} notes", case.homes[name] / "notes")


class GoldenOperationsFaro(GoldenCase):
    def test_operations(self):
        operations(self, "faro")
        self.check("operations-faro.txt")


class GoldenOperationsHarbor(GoldenCase):
    def test_operations(self):
        operations(self, "harbor")
        self.check("operations-harbor.txt")


# ---------- hooks: the guard on its table, then the events of a session's life ----------


def guard_rows(base):
    """(expect, command, folder or None) of each row of tests/golden_guard.tsv, its @DIR joined to base."""
    from foreman import guard

    text = (ROOT / "tests/golden_guard.tsv").read_text()
    return [(expect, cmd, at) for _, expect, cmd, at in guard.parse_table(text, base)]


class GoldenGuardHook(GoldenCase):
    """Every command of the guard table as a PreToolUse of Bash in a session of `faro` (the hook's exit code and
    message), then `foreman guard --table` on it."""

    def test_guard_hook(self):
        self.t.section("faro: the guard hook on each row of tests/golden_guard.tsv")
        subs = {
            "{state}": self.tmp / "state/faro",
            "{state_root}": self.tmp / "state",
            "{registry}": self.tmp / "projects.toml",
        }
        for n, (expect, cmd, at) in enumerate(guard_rows(self.homes["faro"]), 1):
            if cmd.startswith("file:"):  # a reserved file: a Write call (guard.row_path)
                path = cmd[len("file:") :]
                for k, v in subs.items():
                    path = path.replace(k, str(v))
                tool, ti = "Write", {"file_path": path, "content": "x"}
            else:  # a Bash command, with the same placeholders (guard.row_text)
                text = cmd
                for k, v in subs.items():
                    text = text.replace(k, str(v))
                tool, ti = "Bash", {"command": text}
            self.hook(
                "guard",
                {
                    **({"cwd": at} if at else {}),
                    "hook_event_name": "PreToolUse",
                    "tool_name": tool,
                    "session_id": "s-guard",
                    "tool_use_id": f"toolu_{n:03d}",
                    "tool_input": ti,
                },
                label=f"foreman hook guard  # row {n}: {expect}",
            )
        self.hook("guard", {"hook_event_name": "PreToolUse", "tool_name": "Read", "tool_input": {"file_path": "x"}})
        agent = {"hook_event_name": "PreToolUse", "tool_name": "Bash", "session_id": "s-guard", "agent_id": "a-1"}
        self.hook("guard", {**agent, "tool_input": {"command": 'foreman integrate faro ab1/x "AB-1 T0"'}})
        self.hook("guard", {**agent, "tool_input": {"command": 'foreman integrate faro ab1/x "AB-1 T0" --check'}})
        self.hook("guard", {"hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": {"command": "make up"}})
        self.hook(
            "guard",
            {"hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": {"command": "git push"}},
            project="harbor",
        )
        self.cli(
            "guard",
            "--table",
            str(self.homes["faro"] / "guard.tsv"),  # its copy: a row's @DIR is relative to the table's folder
            project="faro",
            label="foreman --project faro guard --table tests/golden_guard.tsv",
        )
        self.state_tree()
        self.check("hooks-guard.txt")


def session_life(case, name):
    """One product's sessions: no orchestrator, then one marked; an agent launched, running, delivering and ending;
    the loop's prompts from the orchestrator and from another session; a pause; a compaction."""
    spec = PRODUCTS[name]
    P = spec["prefix"]
    prompt = re.search(r'prompt = "([^"]+)"', spec["toml"]).group(1)
    build_backlog(case, name, reads=False)
    case.t = golden.Transcript(case.norm)
    h = lambda hook, ev, **k: case.hook(hook, ev, project=name, **k)  # noqa: E731
    c = lambda *a, **k: case.cli(*a, project=name, **k)  # noqa: E731
    orq, other = "orq-session-1", "other-session-2"

    def tick(session):  # the hooks module's timer asks for a pass; what it submits, if anything
        text = json.dumps({"cwd": str(case.homes[name]), "session": session}, sort_keys=True)
        r = golden.run_entry(["hook", "mod", "tick"], case.env, case.homes[name], text)
        case.t.call("foreman hook mod tick", r, stdin=text)
        return json.loads(r.stdout).get("submit") if r.returncode == 0 else None

    case.t.section(f"{name}: no orchestrator yet")
    h("who", {"hook_event_name": "SessionStart", "source": "startup", "session_id": orq})
    h("loop", {"hook_event_name": "UserPromptSubmit", "session_id": orq, "prompt": prompt + ": pass"})
    case.t.section(f"{name}: the orchestrator marks itself")
    case.heartbeat(name, orq)
    case.heartbeat(name, other)
    c("session", orq, "--name", "orq")
    h("who", {"hook_event_name": "SessionStart", "source": "startup", "session_id": orq})
    h("who", {"hook_event_name": "SessionStart", "source": "startup", "session_id": other})
    h("who", {"hook_event_name": "SessionStart", "source": "clear", "session_id": other})
    case.t.section(f"{name}: an agent is launched, works, delivers and stops")
    launch = c("launch", "ag2", "--issue", f"{P}-2", "--type", "dev")
    line = launch.stdout.strip().splitlines()[-1]
    brief = f"{line}\nIssue {P}-2."
    tr = case.tmp / "tasks/agent-aaa111.output"
    tr.parent.mkdir()
    tr.write_text(json.dumps({"message": {"role": "user", "content": brief}}) + "\n")
    h(
        "pause-agents",
        {
            "hook_event_name": "PreToolUse",
            "session_id": orq,
            "tool_name": "Agent",
            "tool_input": {"subagent_type": "dev", "prompt": brief},
        },
    )
    h(
        "agents",
        {
            "hook_event_name": "SubagentStart",
            "session_id": orq,
            "agent_id": "agent-aaa111",
            "agent_type": "dev",
            "agent_transcript_path": str(tr),
        },
    )
    h(
        "agents",
        {
            "hook_event_name": "PostToolUse",
            "session_id": orq,
            "tool_name": "Agent",
            "tool_input": {"subagent_type": "dev", "prompt": brief},
            "tool_response": {"status": "async_launched", "agentId": "agent-aaa111"},
        },
    )
    h(
        "agents",
        {
            "hook_event_name": "PostToolUse",
            "session_id": orq,
            "tool_name": "Agent",
            "tool_input": {"subagent_type": "dev", "prompt": "no launch line"},
            "tool_response": {"status": "async_launched", "agentId": "agent-bbb222"},
        },
    )
    h(
        "guard",
        {
            "hook_event_name": "PreToolUse",
            "session_id": orq,
            "agent_id": "agent-aaa111",
            "tool_name": "Bash",
            "tool_use_id": "toolu_p1",
            "tool_input": {"command": f"foreman --project {name} progress 'halfway'"},
        },
    )
    case.cli("progress", "halfway", project=name)
    h(
        "agents",
        {
            "hook_event_name": "PostToolUse",
            "session_id": orq,
            "agent_id": "agent-aaa111",
            "tool_name": "Bash",
            "tool_use_id": "toolu_p1",
            "tool_input": {"command": f"foreman --project {name} progress 'halfway'"},
        },
    )
    c("agents")
    c("loop")
    h(
        "agents",
        {
            "hook_event_name": "PostToolUse",
            "session_id": orq,
            "agent_id": "agent-aaa111",
            "tool_name": "Bash",
            "tool_use_id": "toolu_p2",
            "tool_input": {"command": f"foreman --project {name} progress --done 'delivered'"},
        },
    )
    h(
        "agents",
        {
            "hook_event_name": "SubagentStop",
            "session_id": orq,
            "agent_id": "agent-aaa111",
            "last_assistant_message": "Delivered: branch ready.",
        },
    )
    c("agents")
    case.t.section(f"{name}: the loop's prompts")
    submitted = tick(orq)  # due (no pass yet): the pass, submitted as a prompt
    tick(orq)  # just passed: not due
    h("loop", {"hook_event_name": "UserPromptSubmit", "session_id": orq, "prompt": submitted or prompt})
    tick(other)  # not the marked session: nothing
    h("loop", {"hook_event_name": "UserPromptSubmit", "session_id": other, "prompt": prompt + ": pass"})
    h("loop", {"hook_event_name": "UserPromptSubmit", "session_id": orq, "prompt": "how is it going?"})
    tick(orq)  # between passes: the notices not said yet, for the module to hand over with the next tool result
    case.t.section(f"{name}: paused, then a compaction")
    (case.tmp / "state" / name / "pause").write_text("paused at 09:30\n")
    h(
        "pause-agents",
        {
            "hook_event_name": "PreToolUse",
            "session_id": orq,
            "tool_name": "Agent",
            "tool_input": {"subagent_type": "dev", "prompt": "x"},
        },
    )
    h(
        "pause-agents",
        {
            "hook_event_name": "PreToolUse",
            "session_id": orq,
            "tool_name": "Agent",
            "tool_input": {"subagent_type": "Explore", "prompt": "x"},
        },
    )
    h(
        "guard",
        {
            "hook_event_name": "PreToolUse",
            "session_id": orq,
            "tool_name": "Bash",
            "tool_input": {"command": f"foreman launch ag9 --issue {P}-3"},
        },
    )
    c("loop")
    (case.tmp / "state" / name / "board.md").write_text("# Board\n\n- one line\n")
    h("resume", {"hook_event_name": "SessionStart", "source": "compact", "session_id": orq})
    h("resume", {"hook_event_name": "SessionStart", "source": "compact", "session_id": other})
    case.t.section(f"{name}: the orchestrator's session is gone")
    case.end(name, orq)
    h("who", {"hook_event_name": "SessionStart", "source": "startup", "session_id": other})
    h("loop", {"hook_event_name": "UserPromptSubmit", "session_id": other, "prompt": prompt + ": pass"})
    tick(other)
    case.t.section(f"{name}: the delivery check stops an incomplete delivery twice, then lets it go")
    repo = "app" if name == "faro" else "api"
    launch = c(
        "launch",
        "ag5",
        "--issue",
        f"{P}-3",
        "--sub",
        "T1",
        "--repo",
        repo,
        "--branch",
        f"{P.lower()}3/t1",
        "--no-backlog",
    )
    line = launch.stdout.strip().splitlines()[-1]
    h(
        "agents",
        {
            "hook_event_name": "PostToolUse",
            "session_id": other,
            "tool_name": "Agent",
            "tool_input": {"subagent_type": "dev", "prompt": line + "\nWork."},
            "tool_response": {"status": "async_launched", "agentId": "agent-ccc333"},
        },
    )
    for _ in range(3):
        h(
            "agents",
            {
                "hook_event_name": "SubagentStop",
                "session_id": other,
                "agent_id": "agent-ccc333",
                "agent_type": "dev",
                "last_assistant_message": "Done.",
            },
        )
    c("agents")
    c("loop")
    case.t.section(f"{name}: outside any product, and unknown hooks")
    case.t.call(
        "foreman hook who (cwd outside the products)",
        golden.run_entry(
            ["hook", "who"], case.env, case.tmp, json.dumps({"cwd": str(case.tmp), "hook_event_name": "SessionStart"})
        ),
    )
    case.t.call("foreman hook nope", golden.run_entry(["hook", "nope"], case.env, case.tmp, "{}"))
    case.state_tree()


class GoldenSessionFaro(GoldenCase):
    def test_session_life(self):
        session_life(self, "faro")
        self.check("hooks-faro.txt")


class GoldenSessionHarbor(GoldenCase):
    def test_session_life(self):
        session_life(self, "harbor")
        self.check("hooks-harbor.txt")


# ---------- the hooks module's Python side, on the cases of tests/mod/contract.ts ----------


def _stamp(minutes=0):
    return (dt.datetime.now() + dt.timedelta(minutes=minutes)).isoformat(timespec="seconds")


def _jsonl(f, *lines):
    f.parent.mkdir(parents=True, exist_ok=True)
    with open(f, "a") as out:
        for line in lines:
            out.write(json.dumps(line) + "\n")


class ModWorld:
    """The world of one case of tests/mod/contract.ts: its own state root (`state`, harbor's under `project`), the
    env its calls run with, the case's placeholders (`places`, for its stdin) and the paths of this world its
    answer names (`shown`: path → what the case says instead)."""

    def __init__(self, case, c, places):
        self.case, self.sid = case, c["stdin"]["session"]
        self.state = case.tmp / "worlds" / re.sub(r"\W+", "-", c["name"]).strip("-")
        self.project = self.state / "harbor"
        self.project.mkdir(parents=True)
        self.env = dict(case.env, FOREMAN_STATE_HOME=str(self.state))
        self.places, self.shown = dict(places), {}

    def write(self, rel, obj):
        f = self.project / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps(obj, indent=1))


def _w_heartbeat(w):
    """The session's heartbeat, as its module's ticks write it."""
    rec = {"at": _stamp(), "started": _stamp(), "busy": False, "claude_code": "2.1.314", "cwd": str(w.case.tmp)}
    w.write(f"sessions/{w.sid}.json", rec)


def _w_interactive(w):
    """The session's heartbeat, its module's start said `interactive` (a person at the prompt: a terminal)."""
    rec = {"at": _stamp(), "started": _stamp(), "busy": False, "claude_code": "2.1.314", "cwd": str(w.case.tmp)}
    w.write(f"sessions/{w.sid}.json", {**rec, "interactive": True})


def _w_marked(w):
    """The session marked as harbor's orchestrator (`foreman session`)."""
    r = golden.run_entry(["--project", "harbor", "session", w.sid, "--name", "orq"], w.env, w.case.tmp)
    w.case.assertEqual(r.returncode, 0, r.stderr)


def _w_backlog(w):
    """An empty backlog (`foreman init`, once: it lives in the home, which the worlds share)."""
    if not (w.case.homes["harbor"] / "backlog.json").exists():
        r = golden.run_entry(["init", str(w.case.homes["harbor"]), "--name", "harbor"], w.env, w.case.tmp)
        w.case.assertEqual(r.returncode, 0, r.stderr)


def _w_passed(w):
    """A loop pass just ran (its heartbeat) and the weekly review is done: no pass due, nothing overdue."""
    w.write("heartbeats.json", {"loop": _stamp(), "review": _stamp()})


def _w_reviewed(w):
    """The weekly review is done: nothing overdue for a pass to act on."""
    w.write("heartbeats.json", {"review": _stamp()})


def _w_inbox(w):
    """One message from the owner, unread."""
    w.write("inbox.json", [{"id": 1, "time": "2020-01-15 09:50", "from": "owner", "text": "Done?", "read": False}])


def _w_measured(w):
    """The session's last measure: 208K of a 1000K window, above harbor's [loop] context_rotate (200K)."""
    rec = {"tokens": 208000, "window": 1000000, "percent": 21, "cost_usd": None, "rate_limits": [], "at": _stamp()}
    w.write(f"measure/{w.sid}.json", rec)


def _w_renew_asked(w):
    """`foreman renew` from the session: its request waits for the next tick."""
    w.write("renew.json", {"session": w.sid, "at": _stamp(), "now": False})


RENEWAL = "r1700000000"  # the renewal the module's `/compact` call settles (contract.ts)


def _w_renewal_open(w):
    """A renewal (r1700000000) a tick started a moment ago: the module's `/compact` call has not settled."""
    a = {"at": _stamp(), "session": w.sid, "id": RENEWAL, "state": "asked", "why": "request", "tokens_before": 208000}
    _jsonl(w.project / "renewals.jsonl", a)


def _w_renewal_compacted(w):
    """That renewal's compaction ran (its SessionStart settled it); no measure since."""
    _w_renewal_open(w)
    a = {"at": _stamp(), "session": w.sid, "id": RENEWAL, "state": "compacted", "compacted_at": _stamp()}
    _jsonl(w.project / "renewals.jsonl", a)


def _plugin(w, version, orphaned=False):
    """An installed copy of foreman `version` in this world's plugin cache (shown as /cache/foreman/VERSION): its
    plugin.json and an executable bin/foreman; `orphaned`: Claude Code marked it replaced an hour ago (past the
    grace, versions.GRACE_S)."""
    root = w.state / "cache/foreman" / version
    (root / ".claude-plugin").mkdir(parents=True)
    (root / ".claude-plugin/plugin.json").write_text(json.dumps({"name": "foreman", "version": version}))
    (root / "bin").mkdir()
    (root / "bin/foreman").write_text("#!/bin/sh\n")
    (root / "bin/foreman").chmod(0o755)
    if orphaned:
        (root / ".orphaned_at").write_text("1")
        old = time.time() - 3600
        os.utime(root / ".orphaned_at", (old, old))
    w.shown[str(w.state / "cache")] = "/cache"
    return root


def _w_replaced(w):
    """The session runs an installed foreman 0.1.0 (its hooks' CLAUDE_PLUGIN_ROOT), replaced by an install of 0.2.0."""
    w.env["CLAUDE_PLUGIN_ROOT"] = str(_plugin(w, "0.1.0", orphaned=True))
    _plugin(w, "0.2.0")


def _w_behind(w):
    """The session runs an installed foreman 0.35.0 (its hooks' CLAUDE_PLUGIN_ROOT), replaced by an install of 9.9.0."""
    w.env["CLAUDE_PLUGIN_ROOT"] = str(_plugin(w, "0.35.0", orphaned=True))
    _plugin(w, "9.9.0")


# What `hook mod OP` must find for the answer of a case of tests/mod/contract.ts, by the case's name: each one runs
# on a state root of its own, empty but for these steps (in this order), and the transcript lists them and shows
# that state after the call. A case not named here runs on the state the cases before it left. The module's tests
# never see these: they feed the module the case's answer.
MARKED = (_w_heartbeat, _w_marked, _w_backlog)
MOD_WORLDS = {
    "a pass due with something to act on": (*MARKED, _w_reviewed, _w_inbox),
    "an idle pass": (*MARKED, _w_reviewed),
    "no pass due": (*MARKED, _w_passed),
    "a held tick: the heartbeat only, even with a pass due": (*MARKED, _w_reviewed, _w_inbox),
    "in-between notices with no pass due": (*MARKED, _w_passed, _w_inbox),
    "in-between notices and a version notice": (*MARKED, _w_passed, _w_inbox, _w_replaced),
    "a version notice in a session that is not the marked one": (_w_heartbeat, _w_replaced),
    "the session's subagents, as $.agent.list() gives them": (*MARKED, _w_passed),
    "a version notice in the marked session, a terminal one": (
        _w_interactive,
        _w_marked,
        _w_backlog,
        _w_passed,
        _w_behind,
    ),
    "a renewal due": (*MARKED, _w_passed, _w_measured, _w_renew_asked),
    "a flagged answer of the marked session": MARKED,
    "a clean answer of the marked session": MARKED,
    "the session ends in an active project": (_w_heartbeat,),
    "a renewal call that settled": (*MARKED, _w_renewal_open),
    "a renewal call rejected": (*MARKED, _w_renewal_open),
    "measure: the first after a renewal": (*MARKED, _w_renewal_compacted),
}

# Beyond the golden normalisation (times, durations, versions, paths), a renewal's id: `r` and the epoch it started.
RENEWAL_ID = re.compile(r"\br\d{10}\b")


class GoldenModContract(GoldenCase):
    """Every case of tests/mod/contract.ts that has a Python side: its stdin (placeholders replaced) to `foreman hook
    mod OP`, on the world MOD_WORLDS builds for it, must answer the case's `run.stdout`, the answer the module's
    tests feed the module: byte for byte once both are normalised as the golden files are (a case may say «10:00»
    where Python says the time it is), with the world's paths written as the case writes them and a renewal's id
    masked. The transcript and the session records it leaves go to tests/golden/mod/OP.txt. The `grant` cases are
    tests/test_grants.py's (ContractTest): they need a simulated `ps`."""

    def same(self, text, shown):
        for path, written in shown.items():
            text = text.replace(path, written)
        return RENEWAL_ID.sub("r<EPOCH>", self.norm(text))

    def test_contract(self):
        from foreman import selftest

        elsewhere = self.tmp / "elsewhere"
        elsewhere.mkdir()
        places = {"<root>": str(ROOT), "<project>": str(self.homes["harbor"]), "<elsewhere>": str(elsewhere)}
        # the grant cases need a simulated `ps`: tests/test_grants.py ContractTest checks them
        cases = [c for c in selftest.mod_contract() if c["python"] and c["op"] != "grant"]
        self.assertTrue(cases)
        self.assertEqual(sorted(set(MOD_WORLDS) - {c["name"] for c in cases}), [])  # each world has its case
        for op in sorted({c["op"] for c in cases}):
            self.t = golden.Transcript(self.norm)
            for c in [c for c in cases if c["op"] == op]:
                self.t.section(f"{c['name']}")
                w = None
                if c["name"] in MOD_WORLDS:
                    w = ModWorld(self, c, places)
                    for step in MOD_WORLDS[c["name"]]:
                        step(w)
                    self.t.text("world", "\n".join(f"- {step.__doc__}" for step in MOD_WORLDS[c["name"]]))
                where = w.places if w else places
                stdin = json.dumps(
                    {k: where.get(v, v) if isinstance(v, str) else v for k, v in c["stdin"].items()}, sort_keys=True
                )
                r = golden.run_entry(["hook", "mod", op], w.env if w else self.env, self.tmp, stdin)
                for rid in RENEWAL_ID.findall(r.stdout):  # drawn from the clock: masked in the transcript too
                    self.norm.add(rid, "r<EPOCH>")
                self.t.call(f"foreman hook mod {op}", r, stdin=stdin)
                if w:
                    self.t.tree("its state", w.state, skip=("cache",))
                shown = w.shown if w else {}
                got = (r.returncode, self.same(r.stdout, shown), r.stderr)
                self.assertEqual(got, (0, self.same(c["run"]["stdout"], {}), ""), c["name"])
            self.state_tree()
            self.check(f"mod/{op}.txt")


class GoldenModBand(GoldenCase):
    """The orchestrator's band (band.py) in both products and languages: what the marked session's ticks answer as
    `band` and `hook mod status` prints, with no orchestrator, then marked with a measure, two live agents, one
    delivered and a branch ready to integrate; an unmarked session gets no band."""

    def test_band(self):
        self.t = golden.Transcript(self.norm)
        # the agents start 47 and 90 minutes ago, which after midnight is yesterday: the day is no part of this test
        self.norm.dates[(dt.date.today() - dt.timedelta(days=1)).isoformat()] = "<TODAY>"
        elsewhere = self.tmp / "elsewhere"
        elsewhere.mkdir()
        for name in ("harbor", "faro"):
            band_world(self, name, elsewhere)
        self.state_tree()
        self.check("mod/band.txt")


def band_world(case, name, elsewhere):
    spec = PRODUCTS[name]
    P = spec["prefix"]
    home = case.homes[name]
    orq, other = "orq-band-1", "other-band-2"
    keep, case.t = case.t, golden.Transcript(case.norm)
    build_backlog(case, name, reads=False)
    case.t = keep  # the backlog's writes are GoldenBacklog*'s business

    def mod(op, **ev):
        text = json.dumps({"cwd": str(home), **ev}, sort_keys=True)
        r = golden.run_entry(["hook", "mod", op], case.env, home, text)
        case.t.call(f"foreman hook mod {op}", r, stdin=text)
        return r

    case.t.section(f"{name}: no orchestrator marked yet")
    mod("status", session=orq)
    case.t.section(f"{name}: marked, measured, two agents live, one delivered, a branch ready to integrate")
    case.heartbeat(name, orq)
    case.heartbeat(name, other)
    case.cli("session", orq, "--name", "orq", project=name)
    mod("measure", session=orq, context={"tokens": 84000, "window": 200000, "percent": 42}, rate_limits=[], cost=None)
    repo = "app" if name == "faro" else "api"
    case.cli(
        "launch",
        "ag1",
        "--issue",
        f"{P}-1",
        "--sub",
        "T1",
        "--repo",
        repo,
        "--branch",
        "a1/t1",
        "--no-backlog",
        project=name,
    )
    now = dt.datetime.now()
    at = lambda minutes: (now - dt.timedelta(minutes=minutes)).isoformat(timespec="seconds")  # noqa: E731
    agents = {
        "agent-band01": {"name": "ag2", "issue": f"{P}-2", "sub": "T1", "named_by": "launch", "start": at(47)},
        "agent-band02": {"name": "ag3", "issue": f"{P}-3", "named_by": "launch", "start": at(12)},
        "agent-band03": {
            "name": "ag1",
            "issue": f"{P}-1",
            "sub": "T1",
            "named_by": "launch",
            "start": at(90),
            "end": at(5),
            "delivered": at(5),
        },
    }
    (case.tmp / "state" / name / "agents.json").write_text(json.dumps(agents, indent=1))
    mod("tick", session=orq)  # due: the pass, and the band after it
    mod("tick", session=orq)  # not due: the band alone
    mod("status", session=other)  # the command in another session: the same summary
    mod("tick", session=other)  # not the marked session: no band
    case.t.section(f"{name}: outside any project")
    text = json.dumps({"cwd": str(elsewhere), "session": other}, sort_keys=True)
    case.t.call(
        "foreman hook mod status", golden.run_entry(["hook", "mod", "status"], case.env, elsewhere, text), stdin=text
    )


# ---------- the panel: every API route over both products, before and after the owner's writes ----------


class GoldenPanel(GoldenCase):
    """`foreman panel` on a free port, as its own process; each answer's status and JSON (keys sorted)."""

    def setUp(self):
        super().setUp()
        self.proc = None
        # the panel and the notes it reads run at noon of today whatever the real hour: a lock's expiry (now + 30 min)
        # must not fall on the next day, nor a note on another day's file (FOREMAN_NOW, util.now)
        self.env["FOREMAN_NOW"] = f"{dt.date.today():%Y-%m-%d}T12:00:00"

    def tearDown(self):
        if self.proc:
            self.proc.terminate()
            try:
                self.proc.wait(30)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait()
        super().tearDown()

    def start_panel(self):
        import socket
        import time

        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            self.port = s.getsockname()[1]
        self.norm.add(f":{self.port}", ":<PORT>")
        self.proc = subprocess.Popen(
            [sys.executable, str(golden.ENTRY), "panel", "--port", str(self.port), "--token", "k3y"],
            env=self.env,
            cwd=str(self.tmp),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        deadline = time.monotonic() + 60
        while True:  # until it answers (a condition, not a fixed wait)
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/api/version?t=k3y", timeout=5):
                    return
            except (urllib.error.URLError, ConnectionError, OSError):
                if self.proc.poll() is not None or time.monotonic() > deadline:
                    raise AssertionError("the panel did not start") from None
                time.sleep(0.05)

    def req(self, path, method="GET", body=b"", token="k3y"):
        url = f"http://127.0.0.1:{self.port}{path}"
        if token:
            url += ("&" if "?" in path else "?") + "t=" + token
        r = urllib.request.Request(url, data=body if method == "POST" else None, method=method)
        try:
            with urllib.request.urlopen(r, timeout=30) as resp:
                code, ctype, data = resp.status, resp.headers.get_content_type(), resp.read()
        except urllib.error.HTTPError as e:
            code, ctype, data = e.code, e.headers.get_content_type(), e.read()
        obj = json.loads(data) if ctype == "application/json" else data.decode()
        self.t.json(f"{method} {path}" + (f" < {body.decode()}" if body else ""), code, obj)
        return code, obj

    def test_panel(self):
        for name in PRODUCTS:
            build_backlog(self, name, reads=False)
            self.cli("note", "-", PRODUCTS[name]["texts"]["notet"], project=name)
            self.cli("progress", PRODUCTS[name]["texts"]["prog"], project=name, env={"FOREMAN_WHO": "ag1"})
            self.cli("inbox", "add", PRODUCTS[name]["texts"]["inbox"], project=name)
            self.cli("recurring", "done", "loop", project=name)
        self.t = golden.Transcript(self.norm)
        self.start_panel()
        self.t.section("reads")
        g = self.req
        g("/api/version")
        g("/api/projects")
        g("/api/strings?lang=es")
        g("/api/strings?lang=en")
        for name in PRODUCTS:
            g(f"/api/state?p={name}")
            g(f"/api/backlog?p={name}")
        g("/api/state?p=*")
        g("/api/backlog?p=*")
        g("/api/state?p=nope")
        g("/api/backlog?p=nope")
        g("/api/issue?p=faro&id=FA-2")
        g("/api/issue?p=faro&id=FA-1")
        g("/api/issue?p=harbor&id=HB-404")
        g("/api/wiki?p=harbor")
        g("/api/wiki/page?p=harbor&n=wiki")
        g("/api/wiki/page?p=harbor&n=nope.md")
        g("/api/wiki/search?p=harbor&q=decisions")
        g("/api/wiki?p=faro")
        g("/api/doc?p=faro&path=reviews/2020-01-10-plan-FA-1.md")
        g("/api/doc?p=faro&path=../projects.toml")
        g("/api/nope")
        g("/api/projects", token="wrong")
        self.t.section("the owner's writes")
        g("/api/message?p=faro", "POST", "¿Seguimos mañana?".encode())
        g("/api/message?p=faro", "POST", b"  ")
        g("/api/decide?p=harbor&d=" + urllib.request.quote("DEC J1, Close it?") + "&op=a", "POST")
        g("/api/decide?p=harbor", "POST")
        g("/api/approve?p=faro&repo=app&branch=fa2/t1", "POST")
        g("/api/approve?p=faro&repo=nope&branch=x", "POST")
        g("/api/move?p=faro&id=FA-2&a=up", "POST")
        g("/api/move?p=faro&id=FA-2&a=first", "POST")
        g("/api/move?p=faro&id=FA-404&a=up", "POST")
        g("/api/move?p=harbor&kind=project&id=A2&a=0", "POST")
        g("/api/pause?p=faro&on=1", "POST")
        g("/api/state?p=faro")
        g("/api/pause?p=faro&on=0", "POST")
        g("/api/lock/take?r=widget&min=30", "POST")
        g("/api/lock/extend?r=widget&min=15", "POST")
        g("/api/lock/release?r=widget", "POST")
        g("/api/lock/take?r=nope", "POST")
        g("/api/message?p=nope", "POST", b"x")
        g("/api/nope?p=faro", "POST")
        self.t.section("after the writes")
        for name in PRODUCTS:
            g(f"/api/state?p={name}")
        g("/api/backlog?p=*")
        self.state_tree()
        self.check("panel.txt")


# ---------- integration through pull requests (a GitHub remote; gh is tests/fake_gh.py) ----------

GITHUB_REPO = """
[repos.web]
path = "{tmp}/web"
github = "acme/web"
merge = "squash"
local_ci = "test -f feature.txt && echo local-ci-ok"
risky_paths = ["migrations/"]
"""


class GoldenIntegrateGithub(GoldenCase):
    """harbor with a repo on a (bare, local) remote: a branch integrated through a pull request, one whose checks
    fail, and one that touches a risky path. The calls foreman makes to gh are part of the transcript."""

    def test_integrate_github(self):
        home = self.homes["harbor"]
        with open(home / "foreman.toml", "a") as f:
            f.write(GITHUB_REPO.replace("{tmp}", str(self.tmp)))
        seed = self.tmp / "seed-web"
        seed.mkdir()
        (seed / "README.md").write_text("web\n")

        def git(cwd, *a):
            return subprocess.run(["git", "-C", str(cwd), *a], env=self.env, check=True, capture_output=True)

        git(seed, "init", "-q", "-b", "main")
        git(seed, "add", "-A")
        git(seed, "commit", "-q", "-m", "initial")
        git(self.tmp, "clone", "-q", "--bare", str(seed), str(self.tmp / "web.git"))
        git(self.tmp, "clone", "-q", str(self.tmp / "web.git"), str(self.tmp / "web"))
        gh = self.tmp / "gh"
        gh.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{ROOT / "tests/fake_gh.py"}" "$@"\n')
        gh.chmod(0o755)
        self.env.update(FOREMAN_GH=str(gh), FAKE_GH_DIR=str(self.tmp / "ghstate"), FOREMAN_CHECKS_RETRY_S="0")
        build_backlog(self, "harbor", reads=False)
        self.t = golden.Transcript(self.norm)
        c = lambda *a, **k: self.cli(*a, project="harbor", **k)  # noqa: E731
        for n, (issue, files, extra) in enumerate(
            (
                ("HB-2", {"feature.txt": "new\n"}, {}),
                ("HB-3", {"feature.txt": "new\n", "other.txt": "x\n"}, {"FAKE_GH_CHECKS": "fail"}),
                ("HB-4", {"feature.txt": "new\n", "migrations/001.sql": "create table t;\n"}, {}),
            ),
            1,
        ):
            self.t.section(f"{issue}: through a pull request" + (" (its checks fail)" if extra else ""))
            branch = f"hb{n}/t1"
            c("backlog", "sub", issue, "T1", "--title", f"part {n}")
            c("launch", f"w{n}", "--issue", issue, "--sub", "T1", "--repo", "web", "--branch", branch)
            wt = self.tmp / "wt" / f"web-w{n}"
            for name, text in files.items():
                (wt / name).parent.mkdir(parents=True, exist_ok=True)
                (wt / name).write_text(text)
            git(wt, "add", "-A")
            git(wt, "commit", "-q", "-m", f"work of {issue}")
            c("integrate", "web", branch, f"{issue} T1", "--check")
            c(
                "integrate",
                "web",
                branch,
                f"{issue} T1",
                "--title",
                f"{issue}: part {n}",
                "--body",
                "What and why.",
                "--result",
                f"part {n} works",
                env=extra,
            )
            c("backlog", "show", issue)
        log = self.tmp / "ghstate/calls.log"
        self.t.text("gh calls (fake gh's log)", log.read_text() if log.exists() else "(none)")
        self.t.text("remote main", git(self.tmp / "web.git", "log", "--format=%h %s", "main").stdout.decode())
        self.state_tree()
        self.t.tree("harbor notes", home / "notes")
        self.check("integrate-github.txt")


class NormaliserTest(unittest.TestCase):
    """What the corpus masks, and that it masks nothing else."""

    def test_masks_only_what_varies(self):
        import time

        epoch = int(time.time()) - 100
        tmp = Path(tempfile.gettempdir()) / "golden-x"
        n = golden.Normaliser(tmp)
        n.token("abcdefgh23")
        today = dt.date.today()
        text = (
            f"{tmp}/a {today}T09:30:01 {today - dt.timedelta(days=1)} 2020-01-15 12:05 "
            f'pid 4242 "pid": 77 {epoch}.5 1500000000 (heartbeat written, 0.3 s) passed on abc in 2 s. '
            f"[foreman launch abcdefgh23] 0,4 s 3 s ago FA-1 T1\noptional arguments:\n"
        )
        self.assertEqual(
            n(text),
            '<TMP>/a <TODAY>T<TIME> <TODAY-1> 2020-01-15 <TIME> pid <PID> "pid": <PID> '
            "<EPOCH> 1500000000 "
            "(heartbeat written, <SECONDS> s) passed on abc in <SECONDS> s. "
            "[foreman launch <TOKEN1>] 0,4 s 3 s ago FA-1 T1\noptions:\n",
        )
