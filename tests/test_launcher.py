"""The launcher bin/foreman (POSIX sh that execs lib/main.py with a Python that fits, never silent when none does).

Each case runs the launcher with only PATH=/usr/bin:/bin and HOME (in the test's temporary directory, so no mise
install is found) and FOREMAN_PYTHON either unset or a stub that answers `-V` with 3.98. The cases without a Python
that fits run a copy of the launcher (and of lib/main.py, the package linked) whose min_python is 3.99, which no
Python reaches (and newer than the stub), so they run on every machine, whatever Pythons it has where the launcher
always looks (/usr/bin, Homebrew).
"""

import json
import os
import re
import shutil
import subprocess
import sys

from helpers import BIN, ENTRY, ProjectCase

MIN_LINE = re.compile(r"^min_python=(\d+)\.(\d+)\b", re.M)
MIN = tuple(int(x) for x in MIN_LINE.search(BIN.read_text()).groups())
UNREACHABLE = (3, 99)  # the minimum of the launcher copy that finds no Python
UNREACHABLE_TEXT = "{}.{}".format(*UNREACHABLE)
RUNNING = sys.version_info[:2]
EVENT = {"tool_name": "Bash", "tool_input": {"command": "git push origin main"}}


def version_of(exe):
    try:
        r = subprocess.run([exe, "-V"], capture_output=True, text=True, timeout=10, check=False)
        return tuple(int(x) for x in (r.stdout or r.stderr).split()[1].split(".")[:2]) if r.returncode == 0 else None
    except (OSError, ValueError, IndexError, subprocess.SubprocessError):
        return None


# a Python in /usr/bin older than the one running the tests (the Mac's 3.9 under a newer one), or None
OLD_SYSTEM = next(
    (p for p in ("/usr/bin/python3",) if os.path.exists(p) and (version_of(p) or RUNNING) < RUNNING), None
)


class LauncherCase(ProjectCase):
    register = False  # each case decides whether a registry exists

    def setUp(self):
        super().setUp()
        self.fake_home = self.tmp / "userhome"
        self.fake_home.mkdir()
        self.stub = self.tmp / "bin/py39"
        self.stub.parent.mkdir()
        self.stub.write_text("#!/bin/sh\necho Python 3.98.0\n")
        self.stub.chmod(0o755)

    def launcher_copy(self, minimum):
        """A copy of bin/foreman whose min_python is `minimum` (major, minor), next to a copy of lib/main.py (which
        runs the launcher next to it again) and a link to the package; returns the copy of bin/foreman."""
        root = self.tmp / ("root-{}.{}".format(*minimum))
        (root / "bin").mkdir(parents=True)
        (root / "lib").mkdir()
        launcher = root / "bin/foreman"
        launcher.write_text(MIN_LINE.sub("min_python={}.{}".format(*minimum), BIN.read_text(), count=1))
        launcher.chmod(0o755)
        shutil.copy(ENTRY, root / "lib/main.py")
        (root / "lib/foreman").symlink_to(ENTRY.parent / "foreman")
        return launcher

    def run_bare(self, *args, inp=None, cwd=None, shell=None, launcher=None, **env):
        """bin/foreman, or `launcher` (or either through `shell`), with a near-empty environment plus **env."""
        e = {"PATH": "/usr/bin:/bin", "HOME": str(self.fake_home)}
        e.update({k: str(v) for k, v in env.items() if v is not None})
        cmd = ([shell] if shell else []) + [str(launcher or BIN), *args]
        return subprocess.run(
            cmd, input=inp, text=True, capture_output=True, env=e, cwd=cwd or self.tmp, timeout=60, check=False
        )

    def registry(self):
        reg = self.fake_home / ".config/foreman/projects.toml"
        reg.parent.mkdir(parents=True, exist_ok=True)
        reg.write_text("")
        return reg


class NoPythonTest(LauncherCase):
    """No Python fits: a launcher copy that asks for a Python no machine has."""

    def setUp(self):
        super().setUp()
        self.launcher = self.launcher_copy(UNREACHABLE)

    def run_bare(self, *args, **kw):
        return super().run_bare(*args, launcher=self.launcher, **kw)

    def each_setup(self):
        """No FOREMAN_PYTHON, and a FOREMAN_PYTHON that says 3.98."""
        return ({}, {"FOREMAN_PYTHON": self.stub})

    def test_guard_denies_with_a_registry(self):
        self.registry()
        for env in self.each_setup():
            r = self.run_bare("hook", "guard", inp=json.dumps(EVENT), **env)
            self.assertEqual(r.returncode, 2, (env, r.stderr))
            self.assertEqual(r.stdout, "")
            self.assertIn("foreman blocked this call", r.stderr)
            self.assertIn(f"Python {UNREACHABLE_TEXT} or newer", r.stderr)
            self.assertIn("brew install python@", r.stderr)
            self.assertIn("FOREMAN_PYTHON", r.stderr)
        r = self.run_bare("hook", "guard", inp=json.dumps(EVENT), FOREMAN_PYTHON=self.stub)
        self.assertIn("the newest found is 3.98", r.stderr)

    def test_guard_passes_without_a_registry(self):
        for env in self.each_setup():
            r = self.run_bare("hook", "guard", inp=json.dumps(EVENT), **env)
            self.assertEqual((r.returncode, r.stdout), (0, ""), (env, r.stderr))
            self.assertIn("no foreman registry", r.stderr)

    def test_guard_follows_foreman_registry(self):
        """The same registry config.py reads: $FOREMAN_REGISTRY (also with ~/) wins over ~/.config/foreman."""
        other = self.tmp / "elsewhere/projects.toml"
        other.parent.mkdir()
        other.write_text("")
        r = self.run_bare("hook", "guard", inp=json.dumps(EVENT), FOREMAN_REGISTRY=other)
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn(str(other), r.stderr)
        (self.fake_home / "mine.toml").write_text("")
        self.assertEqual(self.run_bare("hook", "guard", FOREMAN_REGISTRY="~/mine.toml").returncode, 2)
        self.registry()
        r = self.run_bare("hook", "guard", inp=json.dumps(EVENT), FOREMAN_REGISTRY=self.tmp / "none.toml")
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_who_tells_the_owner_and_the_model(self):
        for registered in (False, True):
            if registered:
                self.registry()
            for env in self.each_setup():
                for source in ("startup", "resume"):
                    ev = {"hook_event_name": "SessionStart", "session_id": "s1", "source": source}
                    r = self.run_bare("hook", "who", inp=json.dumps(ev), **env)
                    self.assertEqual(r.returncode, 0, r.stderr)
                    out = json.loads(r.stdout)
                    self.assertIn(f"foreman needs Python {UNREACHABLE_TEXT} or newer", out["systemMessage"])
                    self.assertIn("brew install python@", out["systemMessage"])
                    ctx = out["hookSpecificOutput"]
                    self.assertEqual(ctx["hookEventName"], "SessionStart")
                    self.assertIn("blocks every tool call", ctx["additionalContext"])
        r = self.run_bare("hook", "who", inp='{"session_id": "s1", "source": "compact"}')
        self.assertEqual((r.returncode, r.stdout), (0, ""))
        r = self.run_bare("hook", "who")  # no event on stdin at all
        self.assertEqual(r.returncode, 0)
        self.assertIn("systemMessage", json.loads(r.stdout))

    def test_other_hooks_quiet_on_stdout(self):
        self.registry()
        for name in ("loop", "agents", "resume", "pause-agents"):
            r = self.run_bare("hook", name, inp="{}")
            self.assertEqual((r.returncode, r.stdout), (0, ""), name)
            self.assertIn(f"hook {name} skipped", r.stderr)

    def test_cli_says_why_and_fails(self):
        for args in (["--version"], ["status"], []):
            for env in self.each_setup():
                r = self.run_bare(*args, **env)
                self.assertEqual(r.returncode, 1, args)
                self.assertEqual(r.stdout, "")
                self.assertIn(f"foreman needs Python {UNREACHABLE_TEXT} or newer", r.stderr)
                self.assertIn("FOREMAN_PYTHON", r.stderr)

    def test_portable_shells(self):
        """The same answers from every POSIX shell at hand (dash has no bash extensions)."""
        self.registry()
        for shell in [s for s in ("/bin/sh", "/bin/dash", "/bin/bash") if os.path.exists(s)]:
            r = self.run_bare("hook", "guard", inp=json.dumps(EVENT), shell=shell, FOREMAN_PYTHON=self.stub)
            self.assertEqual(r.returncode, 2, (shell, r.stderr))
            self.assertIn("the newest found is 3.98", r.stderr)
            r = self.run_bare("hook", "who", inp="{}", shell=shell)
            self.assertIn("systemMessage", json.loads(r.stdout), shell)

    def test_old_python3_first_on_the_path(self):
        """The fast path runs the PATH's python3 unasked; when it is too old, the entry hands back to the launcher,
        which looks further and, finding nothing, says so."""
        first = self.tmp / "oldbin"
        first.mkdir()
        (first / "python3").symlink_to(sys.executable)
        self.registry()
        r = self.run_bare("hook", "guard", inp=json.dumps(EVENT), PATH=f"{first}:/usr/bin:/bin")
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("foreman blocked this call", r.stderr)
        self.assertRegex(r.stderr, r"the newest found is \d+\.\d+")

    def test_lying_interpreter_does_not_loop(self):
        """An interpreter that claims to fit but is older stops with a message, it never re-executes forever."""
        liar = self.tmp / "liar"
        liar.write_text(
            f'#!/bin/sh\nif [ "$1" = -V ]; then echo Python {UNREACHABLE_TEXT}.0; exit 0; fi\n'
            f'exec {sys.executable} "$@"\n'
        )
        liar.chmod(0o755)
        r = self.run_bare("--version", FOREMAN_PYTHON=liar)
        self.assertEqual(r.returncode, 1)
        self.assertIn("answered as Python", r.stderr)
        r = self.run_bare("hook", "loop", inp="{}", FOREMAN_PYTHON=liar)
        self.assertEqual((r.returncode, r.stdout), (0, ""))
        self.assertIn("answered as Python", r.stderr)


class SamePythonTest(LauncherCase):
    """With a Python that fits, the sh launcher gives the same answers as running the Python entry directly."""

    register = True

    def runs(self, *args, inp=None):
        """(the entry in this Python, the launcher with FOREMAN_PYTHON, the launcher's fast path with python3 first
        on the PATH, and, with an older /usr/bin/python3 first on the PATH, the fast path of a launcher copy that asks
        for this Python's version handing back to that launcher, which finds this Python in a fake mise folder): each
        (rc, stdout, stderr)."""
        fast = self.tmp / "fastbin"
        fast.mkdir(exist_ok=True)
        if not (fast / "python3").exists():
            (fast / "python3").symlink_to(sys.executable)
        mise = self.fake_home / ".local/share/mise/installs/python/9.99/bin"
        mise.mkdir(parents=True, exist_ok=True)
        if not (mise / "python3").exists():
            (mise / "python3").symlink_to(sys.executable)
        base = {k: os.environ[k] for k in ("FOREMAN_REGISTRY", "FOREMAN_STATE_HOME", "FOREMAN_CLAUDE_PROJECTS")}
        base.update(PATH="/usr/bin:/bin", HOME=str(self.fake_home))
        cmds = [
            ([sys.executable, str(ENTRY), *args], {}),
            ([str(BIN), *args], {"FOREMAN_PYTHON": sys.executable}),
            ([str(BIN), *args], {"PATH": f"{fast}:/usr/bin:/bin"}),
        ]
        if OLD_SYSTEM:
            old = self.tmp / "oldbin"
            old.mkdir(exist_ok=True)
            if not (old / "python3").exists():
                (old / "python3").symlink_to(OLD_SYSTEM)
            if not hasattr(self, "launcher"):
                self.launcher = self.launcher_copy(RUNNING)
            cmds.append(([str(self.launcher), *args], {"PATH": f"{old}:/usr/bin:/bin"}))
        out = []
        for cmd, extra in cmds:
            r = subprocess.run(
                cmd,
                input=inp,
                text=True,
                capture_output=True,
                env={**base, **extra},
                cwd=self.home,
                timeout=60,
                check=False,
            )
            out.append((r.returncode, r.stdout, r.stderr))
        return out

    def assertSame(self, *args, inp=None):
        first, *rest = self.runs(*args, inp=inp)
        for other in rest:
            self.assertEqual(other, first, args)
        return first

    def test_cli_identical(self):
        rc, out, _ = self.assertSame("--version")
        self.assertEqual(rc, 0)
        self.assertTrue(out.startswith("foreman "))
        self.assertSame("check")
        self.assertSame("nonsense-command")

    def test_hooks_identical(self):
        rc, _, err = self.assertSame(
            "hook", "guard", inp=json.dumps({**EVENT, "cwd": str(self.home), "tool_input": {"command": "make up"}})
        )
        self.assertEqual(rc, 2, err)  # the fake project's resource guard, not the launcher
        self.assertNotIn("Python", err)
        rc, out, _ = self.assertSame(
            "hook", "guard", inp=json.dumps({**EVENT, "cwd": str(self.home), "tool_input": {"command": "ls"}})
        )
        self.assertEqual((rc, out), (0, ""))
        ev = {"hook_event_name": "SessionStart", "session_id": "s1", "source": "startup", "cwd": str(self.home)}
        self.assertSame("hook", "who", inp=json.dumps(ev))

    def test_reexec_marker_not_inherited(self):
        """What the launcher tells the entry is gone in foreman's own environment (children choose again)."""
        rc, out, err = self.assertSame(
            "time-limit", "30", "sh", "-c", "echo ${FOREMAN_PYTHON_MIN:-none} ${FOREMAN_PYTHON_PROBE:-none}"
        )
        self.assertEqual((rc, out), (0, "none none\n"), err)


class StaticTest(LauncherCase):
    def test_plain_posix_sh(self):
        for shell in [s for s in ("/bin/sh", "/bin/dash", "/bin/bash") if os.path.exists(s)]:
            r = subprocess.run([shell, "-n", str(BIN)], capture_output=True, text=True, check=False)
            self.assertEqual(r.returncode, 0, (shell, r.stderr))
        sc = shutil.which("shellcheck")
        if sc and subprocess.run([sc, "--version"], capture_output=True, check=False).returncode == 0:
            r = subprocess.run([sc, "-s", "sh", str(BIN)], capture_output=True, text=True, check=False)
            self.assertEqual(r.returncode, 0, r.stdout)
        self.assertEqual(
            subprocess.run(
                [
                    "/usr/bin/python3" if OLD_SYSTEM else sys.executable,
                    "-c",
                    "compile(open(__import__('sys').argv[1]).read(), 'm', 'exec')",
                    str(ENTRY),
                ],
                capture_output=True,
                check=False,
            ).returncode,
            0,
            "lib/main.py parses on old Pythons",
        )
