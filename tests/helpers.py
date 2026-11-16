"""A temporary project: a git repo with one commit, a home with foreman.toml and an empty backlog, and a fake `claude`
that records each launch and answers `claude agents --json` from a file the test controls."""

import atexit
import contextlib
import functools
import io
import json
import os
import pwd
import shutil
import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path

import pytest
from fakerepo import FakeRepo

from foreman import util
from foreman.core.project.config import Project
from foreman.core.tasks import backlog
from foreman.providers.repo import FileDiff
from foreman.services.claude import sessions

sessions.STOP_SETTLE_S = 0.0  # the fake stops at once
ROOT = Path(__file__).resolve().parent.parent
PLUGIN = ROOT / "plugins/foreman"  # the folder the marketplace installs

# A test run must not write the owner's real ~/.config/foreman (registry, panel token, locks): HOME and FOREMAN_REGISTRY point
# at a temp dir for the whole run. The real HOME comes from the user database, as $HOME may already be redirected.
# And none of an agent's session variables (FOREMAN_TASK…) may change what a test does.
REAL_HOME = Path(pwd.getpwuid(os.getuid()).pw_dir)
REAL_REGISTRY = REAL_HOME / ".config/foreman/projects.json"
SANDBOX = Path(tempfile.mkdtemp(prefix="fmhome-")).resolve()
(SANDBOX / "tmp").mkdir()
tempfile.tempdir = str(SANDBOX / "tmp")  # every temp dir of this run lives in the sandbox: a leak into the real registry is recognisable
os.environ["HOME"] = str(SANDBOX)
os.environ["FOREMAN_REGISTRY"] = str(SANDBOX / "projects.json")
for _name in ("FOREMAN_TASK", "FOREMAN_AGENT", "FOREMAN_HOME", "FOREMAN_PROJECT", "FOREMAN_PINNED"):  # no agent session leaks in: a run is the owner's shell
    os.environ.pop(_name, None)
atexit.register(shutil.rmtree, SANDBOX, ignore_errors=True)

FAKE = textwrap.dedent("""\
    #!/usr/bin/env python3
    import json, sys, os, hashlib
    d = os.path.dirname(os.path.abspath(__file__))
    a = sys.argv[1:]
    if a[:1] == ["agents"]:
        print(open(os.path.join(d, "agents.json")).read() if os.path.exists(os.path.join(d, "agents.json")) else "[]")
        sys.exit(0)
    if a[:2] == ["auth", "status"]:
        print(open(os.path.join(d, "login.json")).read() if os.path.exists(os.path.join(d, "login.json")) else '{"loggedIn": true}')
        sys.exit(0)
    if a[:1] == ["stop"]:
        open(os.path.join(d, "stops.txt"), "a").write(a[1] + "\\n")
        if os.path.exists(os.path.join(d, "stop_noop")):
            sys.exit(0)
        if os.path.exists(os.path.join(d, "stop_breaks_listing")):
            os.remove(os.path.join(d, "agents.json"))
            sys.exit(0)
        path = os.path.join(d, "agents.json")
        rows = json.load(open(path)) if os.path.exists(path) else []
        for r in rows:
            if r["id"] == a[1] or r["sessionId"].startswith(a[1]):
                r.pop("pid", None)  # the live process (pid) is gone
                r["state"] = "stopped"
        json.dump(rows, open(path, "w"))
        sys.exit(0)
    with open(os.path.join(d, "calls.jsonl"), "a") as f:
        f.write(json.dumps({"argv": a, "cwd": os.getcwd(), "token": os.environ.get("CLAUDE_CODE_OAUTH_TOKEN")}) + "\\n")
    def rows_of(fn):
        path = os.path.join(d, fn)
        return json.load(open(path)) if os.path.exists(path) else []
    def save(fn, rows):
        json.dump(rows, open(os.path.join(d, fn), "w"))
    if "--resume" in a:  # alive (with a pid) rows are copied, as claude does
        if os.path.exists(os.path.join(d, "fail_resume")):
            print("boom", file=sys.stderr)
            sys.exit(1)
        sid = a[a.index("--resume") + 1]
        rows = rows_of("agents.json")
        row = next(r for r in rows if r["sessionId"] == sid)
        if row.get("pid") or len(a) != 4 or os.path.exists(os.path.join(d, "copy_next")):  # alive, or with flags: a copy, as claude does
            new = hashlib.sha1((sid + "copy" + str(len(rows))).encode()).hexdigest()[:8]
            save("agents.json", rows + [{"id": new, "sessionId": new + "-0000-0000", "name": row["name"], "state": "working", "pid": 1}])
            print(f"note: started a copy as {new}")
            print(f"backgrounded · {new} · {row['name']}")
        else:
            row["state"] = "working"
            row["pid"] = 1
            save("agents.json", rows)
            print(f"backgrounded · {sid[:8]} · {row['name']}")
        sys.exit(0)
    title = a[a.index("-n") + 1]
    name = json.loads(a[a.index("--settings") + 1])["env"]["FOREMAN_AGENT"]
    short = hashlib.sha1(name.encode()).hexdigest()[:8]
    rows = rows_of("agents.json")
    rows.append({"id": short, "sessionId": short + "-0000-0000", "name": name, "title": title, "state": "working", "pid": 1})
    save("agents.json", rows)
    print(f"backgrounded · {short} · {name}")
""")


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=True).stdout


def fm(home: Path, *args: str) -> str:
    """Run an `fm` command in-process with its output captured and returned, so the test run stays quiet.
    If the command fails, its output is printed with the error: a failure is never buried."""
    from foreman.ui.cli import main

    out = io.StringIO()
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            main.main(["--home", str(home), *args])
    except BaseException:
        print(out.getvalue(), file=sys.stderr)
        raise
    return out.getvalue()


@functools.cache
def template_repo() -> Path:
    """The repo every Env starts from, built once per process and copied: a git init and commit per test is slow."""
    repo = SANDBOX / "template-repo"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    git(repo, "config", "user.email", "t@t")
    git(repo, "config", "user.name", "t")
    (repo / "app.py").write_text("def add(a, b):\n    return a + b\n")
    git(repo, "add", "-A")
    git(repo, "-c", "maintenance.auto=false", "commit", "-qm", "init")  # no background maintenance racing the copies
    return repo


class Env:
    def __init__(self, test: str = "", autonomy: str = "manual", slots: int = 2, repo: FakeRepo | None = None) -> None:
        """With `repo` (the `fake_git` fixture), the repo folder is empty and the fake answers for git."""
        self.tmp = Path(tempfile.mkdtemp(prefix="fmtest-")).resolve()
        self.repo = self.tmp / "repo"
        self.fake = repo
        if repo:
            self.repo.mkdir()
        else:
            shutil.copytree(template_repo(), self.repo, symlinks=True)
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        (self.bin / "claude").write_text(FAKE)
        (self.bin / "claude").chmod(0o755)
        self.home = self.tmp / "home"
        self.home.mkdir()
        (self.home / "foreman.toml").write_text(
            textwrap.dedent(f'''\
            [project]
            name = "toy"
            prefix = "TY"
            repo = "{self.repo}"
            [agents]
            slots = {slots}
            claude = "{self.bin / "claude"}"
            [merge]
            autonomy = "{autonomy}"
            test = "{test}"
            [health]
            loop_seconds = 1
        ''')
        )
        util.write_json(self.home / "backlog.json", backlog.empty())
        self.p = Project.load(self.home)
        os.environ["FOREMAN_CLAUDE_PROJECTS"] = str(self.tmp / "transcripts")

    def calls(self) -> list[dict]:
        return util.read_jsonl(self.bin / "calls.jsonl")

    def stops(self) -> list[str]:
        f = self.bin / "stops.txt"
        return f.read_text().split() if f.exists() else []

    def state_of(self, name: str) -> str:
        return next(r["state"] for r in json.loads((self.bin / "agents.json").read_text()) if r["name"] == name)

    def set_state(self, name: str, state: str) -> None:
        f = self.bin / "agents.json"
        rows = json.loads(f.read_text())
        for r in rows:
            if r["name"] == name:
                r["state"] = state
        f.write_text(json.dumps(rows))

    def set_status(self, name: str, status: str) -> None:
        f = self.bin / "agents.json"
        rows = json.loads(f.read_text())
        for r in rows:
            if r["name"] == name:
                r["status"] = status
        f.write_text(json.dumps(rows))

    def drop_pid(self, name: str) -> None:
        """Its process went away (the row stays)."""
        f = self.bin / "agents.json"
        f.write_text(json.dumps([{k: v for k, v in r.items() if k != "pid" or r["name"] != name} for r in json.loads(f.read_text())]))

    def hunter_done(self, tid: str) -> None:
        """The hunter wrote its findings file and ended."""
        h = self.task(tid)["hunter"]
        Path(h["file"]).write_text("hunter findings\n")
        self.set_state(h["name"], "done")

    def hunt_then_review(self, tid: str) -> None:
        """A tick launches the hunter; it writes its file and ends; the next tick launches the reviewer."""
        from foreman.loop import tick

        tick.tick(self.p)
        self.hunter_done(tid)
        tick.tick(self.p)

    def review(self, tid: str, open_findings: int = 0) -> None:
        """One tick launches the hunter, which writes its file; the next the reviewer; then its verdict, as `fm verdict` gives it."""
        self.hunt_then_review(tid)
        self.verdict(tid, open_findings)

    def verdict(self, tid: str, open_findings: int = 0) -> None:
        """The reviewer's verdict, as `fm verdict` gives it."""
        os.environ["FOREMAN_TASK"] = tid
        try:
            fm(self.home, "verdict", "--open", str(open_findings), "--file", str(self.home / f"reviews/{tid}.md"))
        finally:
            os.environ.pop("FOREMAN_TASK")

    def change(self, tid: str, lines: int = 1) -> None:
        """The task's agent commits `lines` lines added to app.py on its branch."""
        if self.fake:
            self.fake.commit(self.task(tid)["branch"], FileDiff("app.py", lines, 0))
            return
        with open(Path(self.task(tid)["worktree"]) / "app.py", "a") as f:
            f.write("".join(f"# {n}\n" for n in range(lines)))
        git(Path(self.task(tid)["worktree"]), "commit", "-qam", "change")

    def task(self, tid: str) -> dict:
        return backlog.get(backlog.load(self.p), tid)


def install_fm(request: pytest.FixtureRequest, *entries: dict, version: int = 2) -> Path:
    """Write Claude Code's record of installed plugins in the sandbox HOME with these foreman@foreman entries; removed after the test."""
    from foreman import launcher

    f = launcher.record()
    f.parent.mkdir(parents=True, exist_ok=True)
    util.write_json(f, {"version": version, "plugins": {"other@x": [{"scope": "user", "installPath": "/x", "version": "1"}], "foreman@foreman": list(entries)}})
    request.addfinalizer(functools.partial(f.unlink, missing_ok=True))
    return f


def version_folder(parent: Path, name: str) -> dict:
    """A version folder `parent/name` with a bin/foreman, and its foreman@foreman entry."""
    (parent / name / "bin").mkdir(parents=True)
    (parent / name / "bin/foreman").write_text("#!/usr/bin/env python3\n")
    return {"scope": "user", "installPath": str(parent / name), "version": name, "gitCommitSha": "stale"}
