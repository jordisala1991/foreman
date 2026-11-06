"""Shared fixtures: a fake project in a temporary directory, with its own registry and state root.

Nothing here touches the real ~/.config/foreman, ~/.local/state/foreman or any real repo: every test points
FOREMAN_REGISTRY and FOREMAN_STATE_HOME into its temporary directory.
"""

import atexit
import json
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BIN = ROOT / "bin/foreman"  # the launcher (POSIX sh): what hooks and users run
ENTRY = ROOT / "lib/main.py"  # foreman's Python entry: the tests run it with their own interpreter
sys.path.insert(0, str(ROOT / "lib"))

import forkentry  # noqa: E402
from foreman import commands, config, util  # noqa: E402

# git as the tests' own, not the machine's: no system file, a global file of the tests (an identity for their commits
# on a machine with none, as a CI runner; no signing, no includes, no credential helper of the user's)
_GIT_HOME = Path(tempfile.mkdtemp(prefix="foreman-git-")).resolve()
atexit.register(shutil.rmtree, _GIT_HOME, True)
(_GIT_HOME / "template/hooks").mkdir(parents=True)  # git init copies no sample hooks: fewer files to write and remove
(_GIT_HOME / "gitconfig").write_text(
    "[user]\n\tname = Foreman Tests\n\temail = tests@example.test\n"
    f"[init]\n\tdefaultBranch = main\n\ttemplateDir = {_GIT_HOME / 'template'}\n"
    "[commit]\n\tgpgsign = false\n[tag]\n\tgpgsign = false\n"
)
os.environ["GIT_CONFIG_GLOBAL"] = str(_GIT_HOME / "gitconfig")
os.environ["GIT_CONFIG_NOSYSTEM"] = "1"

BASE_TOML = """
[project]
name = "fake"
owner = "Owner"
language = "en"

[paths]
worktrees = "{tmp}/wt"

[ids]
criterion_required = ["PF-"]

[agents]
production_slots = 1
ready_target = 1

[loop]
prompt = "Fake orchestrator loop"

[resources.widget]
guard = ["make up", "simctl boot"]
"""


class ProjectCase(unittest.TestCase):
    """A fresh fake project per test: self.tmp, self.home, self.project, self.env (for subprocesses)."""

    toml = BASE_TOML
    register = True
    mode = "active"

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="foreman-test-")).resolve()
        self.home = self.tmp / "home"
        self.home.mkdir()
        self.saved = {
            k: os.environ.get(k)
            for k in (
                "FOREMAN_REGISTRY",
                "FOREMAN_STATE_HOME",
                "FOREMAN_HOME",
                "FOREMAN_PROJECT",
                "FOREMAN_GH",
                "FOREMAN_LOCK_WHO",
                "FOREMAN_LOCK_POLL",
                "AGENT",
                "FOREMAN_WHO",
                "FOREMAN_LOCK_WHO_DEFAULT",
                "CLAUDE_CODE_SESSION_ID",
                "FOREMAN_CLAUDE_PROJECTS",
                "CLAUDE_PLUGIN_ROOT",
                "CLAUDE_PLUGIN_DATA",
                "FOREMAN_PANEL_TOKEN",
                "FOREMAN_SERVICE_DIR",
                "FOREMAN_FOLLOW_EVERY",
                "FOREMAN_PYTHON",
                "FOREMAN_HUNG_SHELLS_SH",
                "CLAUDE_CODE_EXECPATH",
                "CLAUDE_CODE_ENTRYPOINT",  # the host running the selftest is not the one a test plays
            )
        }
        # a selftest run as a product command (`foreman integrate` runs local_ci) inherits that point's variables
        # and FOREMAN_BIN/FOREMAN_STATE of the running foreman: the tests see none of them
        for k in (*commands.POINT_VARIABLES, "FOREMAN_BIN", "FOREMAN_STATE", "FOREMAN_LANGUAGE"):
            self.saved.setdefault(k, os.environ.get(k))
        for k in self.saved:
            os.environ.pop(k, None)
        os.environ["FOREMAN_REGISTRY"] = str(self.tmp / "projects.toml")
        os.environ["FOREMAN_STATE_HOME"] = str(self.tmp / "state")
        os.environ["FOREMAN_LOCK_POLL"] = "0.2"
        os.environ["FOREMAN_PYTHON"] = sys.executable  # bin/foreman, when a test runs it, uses the tests' Python
        # the machine's real shells never decide a test (the hung_shells notice): a quiet script, unless a test sets
        # its own
        quiet = self.tmp / "no_hung_shells.sh"
        quiet.write_text("exit 0\n")
        os.environ["FOREMAN_HUNG_SHELLS_SH"] = str(quiet)
        os.environ["FOREMAN_CLAUDE_PROJECTS"] = str(self.tmp / "claude-projects")  # transcripts: also fake
        self.write_toml(self.toml)
        os.environ["CLAUDE_CODE_EXECPATH"] = self.fake_claude(
            "2.1.300 (Claude Code)", "claude-default"
        )  # never the real binary
        if self.register:
            (self.tmp / "projects.toml").write_text(
                f'[[project]]\nname = "fake"\nhome = "{self.home}"\nmode = "{self.mode}"\n'
            )
        self.project = config.load_project(self.home, self.mode)

    def fake_claude(self, output, name="claude-x"):
        """A script that prints `output` for `--version`; its path (for CLAUDE_CODE_EXECPATH)."""
        f = self.tmp / name
        f.write_text(f"#!/bin/sh\necho '{output}'\n")
        f.chmod(0o755)
        return str(f)

    def tearDown(self):
        for k, v in self.saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(self.tmp, ignore_errors=True)

    def write_toml(self, text):
        (self.home / "foreman.toml").write_text(textwrap.dedent(text).replace("{tmp}", str(self.tmp)))
        self.project = config.load_project(self.home, self.mode)

    @property
    def env(self):
        return dict(os.environ)

    def launches(self):
        return util.read_json(self.project.state / "launches.json", {})

    def launch(self, *args, rc=0):
        r = self.cli("launch", *args)
        self.assertEqual(r.returncode, rc, r.stdout + r.stderr)
        return r

    def notes_text(self):
        return "".join(f.read_text() for f in sorted(self.project.notes_dir.glob("*.md")))

    def env_for(self, root, data="foreman-m"):
        """The environment of a session of the plugin installed at `root`."""
        return {"CLAUDE_PLUGIN_ROOT": str(root), "CLAUDE_PLUGIN_DATA": str(self.tmp / "data" / data)}

    def cli(self, *args, cwd=None, inp=None, env=None, timeout=120):
        e = self.env
        e.update(env or {})
        return forkentry.run(args, cwd or self.home, e, inp, timeout)

    def hook(self, name, event, cwd=None):
        event = {"cwd": str(cwd or self.home), **event}
        return self.cli("hook", name, inp=json.dumps(event), cwd=cwd or self.home)


def config_problems(raw):
    """Every problem of a foreman.toml, read strictly (errors and warnings; empty = fine)."""
    err, warn = config.problems(raw)
    return err + warn


GIT = shutil.which("git") or "git"


def git(cwd, *args):
    # the full path and close_fds=False let subprocess use posix_spawn: about half the CPU of a fork of this process
    # (the tests' fds are not inheritable anyway, PEP 446)
    r = subprocess.run([GIT, "-C", str(cwd), *args], capture_output=True, text=True, check=False, close_fds=False)
    if r.returncode:
        raise AssertionError(f"git {' '.join(args)}: {r.stderr}")
    return r.stdout.strip()


def make_repo(path, files=None):
    """A git repo on main with one commit. Into a new or empty folder it is a copy of one built once per process for
    the same files and git settings (snapshot): three git processes fewer per repo."""
    files = files or {"README.md": "hello\n"}
    path = Path(path)
    if path.exists() and any(path.iterdir()):  # what is there goes into the commit too
        return _make_repo(path, files)
    return snapshot(("repo", tuple(sorted(files.items()))), path, lambda p: _make_repo(p, files))


def _make_repo(path, files):
    path.mkdir(parents=True, exist_ok=True)
    git(path, "init", "-q", "-b", "main")
    for name, text in files.items():
        f = path / name
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(text)
    git(path, "add", "-A")
    git(path, "commit", "-q", "-m", "initial")
    return path


_SNAPSHOTS = {}


def snapshot(key, dest, build):
    """Copies into `dest` (what it holds stays) what `build(folder)` makes, built once per process for each `key`
    and git's settings from the environment; dest's path replaces the built one in the copied .git/config files (a
    clone's remote). Fixtures that only build files and repos use it to skip their git processes on every test."""
    git_env = tuple(sorted((k, v) for k, v in os.environ.items() if k.startswith("GIT_") or k == "HOME"))
    gc = os.environ.get("GIT_CONFIG_GLOBAL")
    git_env += (Path(gc).read_text() if gc and Path(gc).exists() else None,)
    full = (key, git_env)
    src = _SNAPSHOTS.get(full)
    if src is None:
        src = Path(tempfile.mkdtemp(dir=_snapshot_root())) / "x"  # a build may take snapshots of its own
        src.mkdir()
        # no background maintenance of git (detached after a commit) writing into it while a copy is taken
        saved = os.environ.get("GIT_CONFIG_PARAMETERS")
        os.environ["GIT_CONFIG_PARAMETERS"] = " ".join(
            x for x in (saved, "'maintenance.auto'='false'", "'gc.auto'='0'") if x
        )
        try:
            build(src)
        finally:
            if saved is None:
                os.environ.pop("GIT_CONFIG_PARAMETERS")
            else:
                os.environ["GIT_CONFIG_PARAMETERS"] = saved
        _SNAPSHOTS[full] = src
    dest = Path(dest)
    shutil.copytree(src, dest, symlinks=True, dirs_exist_ok=True, ignore=shutil.ignore_patterns("*.lock"))
    old, new = str(src), str(dest)
    for cfg in [*dest.glob(".git/config"), *dest.glob("*/.git/config"), *dest.glob("config"), *dest.glob("*/config")]:
        text = cfg.read_text()
        if old in text:
            cfg.write_text(text.replace(old, new))
    return dest


_ROOT = []


def _snapshot_root():
    if not _ROOT:
        _ROOT.append(Path(tempfile.mkdtemp(prefix="foreman-snapshots-")).resolve())
        atexit.register(shutil.rmtree, _ROOT[0], True)
    return _ROOT[0]


# Simulated pids above any real one (macOS stops at 99998, Linux at 4194304): a test process whose own pid were one
# of them would see its simulated tree tangle (its shell or its claude replaced by itself) and fail by chance.
CLAUDE_PID = 9_004_242
SHELL_PID = 9_000_001  # the shell between the simulated claude and this test process
PS_START = "Sun Nov 1 10:00:00 2026"


class FakePs:
    """A simulated `ps` for procs.py (a mixin for a ProjectCase): start_ps() replaces procs.run_ps until the test
    ends. self.ps_rows holds (pid, ppid, lstart, comm) rows; None makes `ps` fail. By default this test process
    runs in a shell under a `claude` process (CLAUDE_PID) that started at PS_START."""

    def start_ps(self):
        from unittest import mock

        from foreman import procs

        me = os.getpid()
        self.ps_rows = [
            (1, 0, PS_START, "/sbin/launchd"),
            (CLAUDE_PID, 1, PS_START, "/Applications/Some App/claude"),
            (SHELL_PID, CLAUDE_PID, PS_START, "/bin/zsh"),
            (me, SHELL_PID, PS_START, sys.executable),
        ]

        def run_ps():
            if self.ps_rows is None:
                raise procs.Unavailable("simulated: no ps")
            return "\n".join(f"{p:>6} {pp:>6} {start} {comm}" for p, pp, start, comm in self.ps_rows) + "\n"

        patcher = mock.patch.object(procs, "run_ps", run_ps)
        patcher.start()
        self.addCleanup(patcher.stop)

    def reparent(self, claude_pid, start=PS_START, comm="claude"):
        """This process's shell now hangs from another `claude` (a copy of the session), which hangs from the first."""
        self.ps_rows = [r for r in self.ps_rows if r[0] not in (SHELL_PID, claude_pid)]
        self.ps_rows.append((claude_pid, CLAUDE_PID, start, comm))
        self.ps_rows.append((SHELL_PID, claude_pid, PS_START, "/bin/zsh"))

    def hook_here(self, event):
        """`foreman hook guard` in this process (the simulated `ps` reaches it): (exit code, stderr)."""
        import contextlib
        import io

        from foreman import hooks

        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            rc = hooks.hook_guard(self.project, {"cwd": str(self.home), **event})
        return rc, err.getvalue()
