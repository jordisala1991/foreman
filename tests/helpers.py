"""A temporary project: a git repo with one commit, a home with fm.toml and an empty backlog, and a fake `claude`
that records each launch and answers `claude agents --json` from a file the test controls."""

import atexit
import contextlib
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

from fm import backlog, util
from fm.config import Project

ROOT = Path(__file__).resolve().parent.parent

# A test run must not write the owner's real ~/.config/fm (registry, panel token, locks): HOME and FM_REGISTRY point
# at a temp dir for the whole run. The real HOME comes from the user database, as $HOME may already be redirected.
REAL_HOME = Path(pwd.getpwuid(os.getuid()).pw_dir)
REAL_REGISTRY = REAL_HOME / ".config/fm/projects.json"
REGISTRY_BEFORE = REAL_REGISTRY.read_bytes() if REAL_REGISTRY.exists() else None
SANDBOX = Path(tempfile.mkdtemp(prefix="fmhome-")).resolve()
os.environ["HOME"] = str(SANDBOX)
os.environ["FM_REGISTRY"] = str(SANDBOX / "projects.json")
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
        sys.exit(0)
    with open(os.path.join(d, "calls.jsonl"), "a") as f:
        f.write(json.dumps({"argv": a, "cwd": os.getcwd(), "token": os.environ.get("CLAUDE_CODE_OAUTH_TOKEN")}) + "\\n")
    name = a[a.index("-n") + 1]
    short = hashlib.sha1(name.encode()).hexdigest()[:8]
    if "--resume" not in a:
        rows = json.load(open(os.path.join(d, "agents.json"))) if os.path.exists(os.path.join(d, "agents.json")) else []
        rows.append({"id": short, "sessionId": short + "-0000-0000", "name": name, "state": "working"})
        json.dump(rows, open(os.path.join(d, "agents.json"), "w"))
    print(f"backgrounded · {short} · {name}")
""")


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=True).stdout


def fm(home: Path, *args: str) -> str:
    """Run an `fm` command in-process with its output captured and returned, so the test run stays quiet.
    If the command fails, its output is printed with the error: a failure is never buried."""
    from fm import cli

    out = io.StringIO()
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            cli.main(["--home", str(home), *args])
    except BaseException:
        print(out.getvalue(), file=sys.stderr)
        raise
    return out.getvalue()


class Env:
    def __init__(self, test: str = "", autonomy: str = "manual", slots: int = 2) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="fmtest-")).resolve()
        self.repo = self.tmp / "repo"
        self.repo.mkdir()
        git(self.repo, "init", "-q", "-b", "main")
        git(self.repo, "config", "user.email", "t@t")
        git(self.repo, "config", "user.name", "t")
        (self.repo / "app.py").write_text("def add(a, b):\n    return a + b\n")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-qm", "init")
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        (self.bin / "claude").write_text(FAKE)
        (self.bin / "claude").chmod(0o755)
        self.home = self.tmp / "home"
        self.home.mkdir()
        (self.home / "fm.toml").write_text(
            textwrap.dedent(f'''\
            [project]
            name = "toy"
            prefix = "TY"
            repo = "{self.repo}"
            [agents]
            slots = {slots}
            claude = "{self.bin / "claude"}"
            [integrate]
            autonomy = "{autonomy}"
            test = "{test}"
            [health]
            loop_seconds = 1
        ''')
        )
        util.write_json(self.home / "backlog.json", backlog.empty())
        self.p = Project.load(self.home)
        os.environ["FM_CLAUDE_PROJECTS"] = str(self.tmp / "transcripts")

    def calls(self) -> list[dict]:
        return util.read_jsonl(self.bin / "calls.jsonl")

    def set_state(self, name: str, state: str) -> None:
        f = self.bin / "agents.json"
        rows = json.loads(f.read_text())
        for r in rows:
            if r["name"] == name:
                r["state"] = state
        f.write_text(json.dumps(rows))

    def review(self, iid: str, high: int = 0, medium: int = 0, low: int = 0) -> None:
        """One tick launches the reviewer; then its verdict, as `fm verdict` gives it."""
        from fm import loop

        loop.tick(self.p)
        os.environ["FM_ISSUE"] = iid
        try:
            fm(self.home, "verdict", "--high", str(high), "--medium", str(medium), "--low", str(low), "--file", str(self.home / f"reviews/{iid}.md"))
        finally:
            os.environ.pop("FM_ISSUE")

    def issue(self, iid: str) -> dict:
        return backlog.get(backlog.load(self.p), iid)
