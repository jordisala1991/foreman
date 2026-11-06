"""The installed-version trace, the panel that follows it and the panel service (lib/foreman/install.py).

Everything lives under the test's temporary directory: fake plugin roots, the state root (FOREMAN_STATE_HOME), the
service folder (FOREMAN_SERVICE_DIR) and fake launchctl/systemctl that only log their arguments. Nothing here reads
or writes the real ~/.claude, ~/Library/LaunchAgents, ~/.config/systemd or a running panel.
"""

import json
import os
import plistlib
import shlex
import shutil
import socket
import stat
import subprocess
import time
import urllib.request
from pathlib import Path

from helpers import ROOT, ProjectCase

from foreman import install


def fake_root(base, version, script=None, orphaned=False, git=None):
    """A plugin root with a manifest and an executable bin/foreman (a shell script that prints its arguments);
    git="dir" makes it a clone (.git folder), git="file" a worktree (.git file)."""
    base.mkdir(parents=True, exist_ok=True)
    (base / ".claude-plugin").mkdir(exist_ok=True)
    (base / ".claude-plugin/plugin.json").write_text(json.dumps({"name": "foreman", "version": version}))
    (base / "bin").mkdir(exist_ok=True)
    exe = base / "bin/foreman"
    exe.write_text(script or '#!/bin/sh\necho "ran $0 $*"\n')
    exe.chmod(0o755)
    if orphaned:
        (base / ".orphaned_at").write_text("1")
    if git == "dir":
        (base / ".git").mkdir(exist_ok=True)
    elif git == "file":
        (base / ".git").write_text("gitdir: /somewhere/.git/worktrees/x\n")
    return base


def copy_of_foreman(dest, version):
    """A runnable copy of this foreman (bin, lib, panel, manifest) with another version."""
    dest.mkdir(parents=True)
    for d in ("bin", "lib", "panel", ".claude-plugin"):
        shutil.copytree(ROOT / d, dest / d, ignore=shutil.ignore_patterns("__pycache__"))
    m = json.loads((dest / ".claude-plugin/plugin.json").read_text())
    m["version"] = version
    (dest / ".claude-plugin/plugin.json").write_text(json.dumps(m))
    return dest.resolve()


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class TraceTest(ProjectCase):
    def roots(self):
        return install.roots_file().read_text().splitlines()

    def test_nothing_without_plugin_root_or_inline(self):
        a = fake_root(self.tmp / "a", "0.2.0")
        self.assertIsNone(install.record({}))
        self.assertIsNone(install.record(self.env_for(a, "foreman-inline")))
        self.assertFalse(install.install_dir().exists())

    def test_highest_version_wins_and_files_change_only_on_change(self):
        a = fake_root(self.tmp / "cache/0.2.0", "0.2.0")
        b = fake_root(self.tmp / "cache/0.10.0", "0.10.0")
        repo = fake_root(self.tmp / "repo", "0.3.0-rc.1")
        self.assertEqual(install.record(self.env_for(a)), str(a))
        self.assertEqual(install.record(self.env_for(b)), str(b))
        self.assertEqual(install.record(self.env_for(repo)), str(b))  # 0.10.0 > 0.3.0-rc.1 > 0.2.0
        self.assertEqual(self.roots(), [str(b), str(repo), str(a)])
        sig = [(f.stat().st_mtime_ns, f.stat().st_ino) for f in (install.roots_file(), install.installs_file())]
        time.sleep(0.01)
        install.record(self.env_for(a))  # an old session still running: nothing changes, nothing is written
        self.assertEqual(
            sig, [(f.stat().st_mtime_ns, f.stat().st_ino) for f in (install.roots_file(), install.installs_file())]
        )
        # the version of a folder loaded in place changes under the same root
        (repo / ".claude-plugin/plugin.json").write_text(json.dumps({"version": "1.0.0"}))
        self.assertEqual(install.record(self.env_for(repo)), str(repo))
        self.assertEqual(json.loads(install.installs_file().read_text())[str(repo)]["version"], "1.0.0")

    def test_orphaned_gone_and_stale_roots(self):
        new = fake_root(self.tmp / "cache/0.2.0", "0.2.0")
        old = fake_root(self.tmp / "cache/0.3.0", "0.3.0", orphaned=True)  # replaced (e.g. a downgrade)
        install.record(self.env_for(old))
        self.assertEqual(install.record(self.env_for(new)), str(new))
        self.assertEqual(self.roots(), [str(new), str(old)])
        shutil.rmtree(old)  # Claude Code deleted it
        install.record(self.env_for(new))
        self.assertEqual(self.roots(), [str(new)])
        self.assertEqual(list(json.loads(install.installs_file().read_text())), [str(new)])
        # a root nobody reported for 14 days is forgotten
        other = fake_root(self.tmp / "other", "9.0.0")
        install.record(self.env_for(other), now=time.time() - 15 * 86400)
        self.assertEqual(install.record(self.env_for(new)), str(new))
        # equal versions: the one recorded last
        twin = fake_root(self.tmp / "twin", "0.2.0")
        self.assertEqual(install.record(self.env_for(twin), now=time.time() + 5), str(twin))

    def test_working_copies_rank_behind_installed_copies(self):
        cache = fake_root(self.tmp / "cache/0.3.0", "0.3.0")
        repo = fake_root(self.tmp / "repo", "0.3.0", git="dir")
        now = time.time()
        install.record(self.env_for(cache), now=now)
        # same version, the working copy seen after: the installed copy still wins
        self.assertEqual(install.record(self.env_for(repo), now=now + 60), str(cache))
        self.assertEqual(self.roots(), [str(cache), str(repo)])
        # the working copy moves to the next version before the cache has it: still the installed copy
        (repo / ".claude-plugin/plugin.json").write_text(json.dumps({"version": "0.3.1"}))
        self.assertEqual(install.record(self.env_for(repo), now=now + 120), str(cache))
        self.assertEqual(self.roots(), [str(cache), str(repo)])
        # a worktree (.git is a file) is a working copy too
        wt = fake_root(self.tmp / "wt", "9.0.0", git="file")
        self.assertEqual(install.record(self.env_for(wt), now=now + 180), str(cache))
        self.assertEqual(self.roots(), [str(cache), str(wt), str(repo)])
        # an orphaned installed copy still goes before a working copy
        orphan = fake_root(self.tmp / "cache/0.2.0", "0.2.0", orphaned=True)
        install.record(self.env_for(orphan), now=now + 240)
        self.assertEqual(self.roots(), [str(cache), str(orphan), str(wt), str(repo)])
        # with no installed copy left, the working copy is chosen
        shutil.rmtree(cache)
        shutil.rmtree(orphan)
        self.assertEqual(install.record(self.env_for(repo), now=now + 300), str(wt))
        shutil.rmtree(wt)
        self.assertEqual(install.record(self.env_for(repo), now=now + 360), str(repo))
        self.assertEqual(self.roots(), [str(repo)])

    def test_only_a_working_copy(self):
        repo = fake_root(self.tmp / "repo", "0.3.0", git="dir")
        self.assertEqual(install.record(self.env_for(repo)), str(repo))
        self.assertEqual(install.installed_roots(), [str(repo)])

    def test_installed_roots_put_working_copies_last(self):
        # a trace written by an older foreman lists the working copy first
        repo = fake_root(self.tmp / "repo", "0.3.1", git="dir")
        a = fake_root(self.tmp / "cache/0.3.0", "0.3.0")
        b = fake_root(self.tmp / "cache/0.2.0", "0.2.0")
        install.install_dir().mkdir(parents=True)
        install.roots_file().write_text(f"{repo}\n{a}\n{b}\n")
        self.assertEqual(install.installed_roots(), [str(a), str(b), str(repo)])

    def test_never_raises(self):
        install.install_dir().mkdir(parents=True)
        install.installs_file().write_text("{not json")
        a = fake_root(self.tmp / "a", "0.2.0")
        self.assertEqual(install.record(self.env_for(a)), str(a))
        install.installs_file().write_text('{"x": 3, "/nope": {"version": "9.9.9"}}')
        self.assertEqual(install.record(self.env_for(a)), str(a))
        (a / ".claude-plugin/plugin.json").write_text("[]")
        self.assertIsNone(install.record(self.env_for(a)))
        self.assertIsNone(install.record({"CLAUDE_PLUGIN_ROOT": "\0bad"}))

    def test_hooks_record_silently(self):
        a = fake_root(self.tmp / "cache/0.2.0", "0.2.0")
        env = self.env_for(a)
        guard = self.cli(
            "hook",
            "guard",
            inp=json.dumps({"cwd": str(self.home), "tool_name": "Bash", "tool_input": {"command": "ls"}}),
            env=env,
        )
        self.assertEqual((guard.returncode, guard.stdout, guard.stderr), (0, "", ""))
        self.assertFalse(install.roots_file().exists())  # the guard does not record: it runs on every command
        outside = self.cli(
            "hook",
            "who",
            inp=json.dumps({"cwd": str(self.tmp), "hook_event_name": "SessionStart", "source": "startup"}),
            cwd=self.tmp,
            env=env,
        )
        self.assertEqual((outside.returncode, outside.stdout), (0, ""))
        self.assertFalse(install.roots_file().exists())  # outside an active project: nothing
        who = self.cli(
            "hook",
            "who",
            inp=json.dumps(
                {"cwd": str(self.home), "hook_event_name": "SessionStart", "session_id": "S2", "source": "startup"}
            ),
            env=env,
        )
        self.assertEqual(who.returncode, 0)
        self.assertEqual(self.roots(), [str(a)])
        self.assertEqual(who.stderr, "")
        # the same notice with or without the trace: the trace adds no output
        bare = self.hook("who", {"hook_event_name": "SessionStart", "session_id": "S2", "source": "startup"})
        self.assertEqual(who.stdout, bare.stdout)
        b = fake_root(self.tmp / "cache/0.3.0", "0.3.0")
        r = self.cli(
            "hook",
            "loop",
            inp=json.dumps(
                {"cwd": str(self.home), "hook_event_name": "UserPromptSubmit", "session_id": "S2", "prompt": "hi"}
            ),
            env=self.env_for(b),
        )
        self.assertEqual((r.returncode, r.stdout, r.stderr), (0, "", ""))
        self.assertEqual(self.roots()[0], str(b))


class FollowTest(ProjectCase):
    def setUp(self):
        super().setUp()
        self.me = fake_root(self.tmp / "cache/0.2.0", "0.2.0").resolve()
        self.logs = []
        self.smoked = []

    def follower(self, smoke_result=None, **kw):
        def smoke(root):
            self.smoked.append(root)
            return smoke_result

        return install.Follower(
            ["--port", "1", "--follow-installed"],
            root=self.me,
            version="0.2.0",
            smoke=smoke,
            log=self.logs.append,
            **kw,
        )

    def trace(self, *roots):
        install.install_dir().mkdir(parents=True, exist_ok=True)
        install.roots_file().write_text("".join(f"{r}\n" for r in roots))

    def test_moves_to_a_new_root_with_the_same_arguments(self):
        new = fake_root(self.tmp / "cache/0.3.0", "0.3.0").resolve()
        self.trace(new, self.me)
        f = self.follower()
        self.assertEqual(f.check(), str(new))
        self.assertIsNone(f.warning)
        calls = []
        f.reexec(str(new), execv=lambda exe, argv: calls.append((exe, argv)))
        exe = str(new / "bin/foreman")
        self.assertEqual(calls, [(exe, [exe, "panel", "--port", "1", "--follow-installed"])])
        self.assertIn("moving from foreman 0.2.0", self.logs[-1])

    def test_same_version_in_place_changed_and_no_trace(self):
        f = self.follower()
        self.assertIsNone(f.check())  # no trace yet
        self.trace(self.me)
        self.assertIsNone(f.check())  # the trace names what runs
        self.assertEqual(self.smoked, [])
        (self.me / ".claude-plugin/plugin.json").write_text(json.dumps({"version": "0.2.1"}))  # loaded in place
        self.assertEqual(f.check(), str(self.me))

    def test_failed_smoke_test_stays_and_warns(self):
        new = fake_root(self.tmp / "cache/0.3.0", "0.3.0").resolve()
        self.trace(new)
        clock = [1000.0]
        f = self.follower(smoke_result="import foreman.panel: SyntaxError", clock=lambda: clock[0])
        self.assertIsNone(f.check())
        self.assertIn("0.3.0", f.warning)
        self.assertIn("SyntaxError", f.warning)
        self.assertEqual(install.info(f)["foreman_warning"], f.warning)
        self.assertIsNone(f.check())  # not retried at once, logged once
        self.assertEqual(len(self.smoked), 1)
        self.assertEqual(len(self.logs), 1)
        newer = fake_root(self.tmp / "cache/0.3.1", "0.3.1").resolve()
        self.trace(newer, new)
        f.smoke = lambda root: None
        self.assertEqual(f.check(), str(newer))  # a new version is tried at once
        self.trace(new)
        f.smoke = lambda root: "still broken"
        self.assertIsNone(f.check())
        clock[0] += install.RETRY_SECONDS + 1
        f.smoke = lambda root: None
        self.assertEqual(f.check(), str(new))  # retried after a while

    def test_leaves_a_working_copy_listed_first_for_the_installed_copy(self):
        repo = fake_root(self.tmp / "repo", "0.3.1", git="dir").resolve()
        new = fake_root(self.tmp / "cache/0.3.0", "0.3.0").resolve()
        self.trace(repo, new)  # written by an older foreman
        self.assertEqual(self.follower().check(), str(new))
        self.assertEqual(self.smoked, [str(new)])

    def test_missing_root_is_skipped_and_gone_self_warns(self):
        self.trace(self.tmp / "nope", self.me)  # the first line is gone: the next usable root is chosen
        f = self.follower()
        self.assertIsNone(f.check())
        self.assertEqual(self.smoked, [])
        shutil.rmtree(self.me)  # the folder it runs from was deleted and nothing else is recorded
        self.assertIsNone(f.check())
        self.assertIn("is gone", f.warning)

    def test_real_smoke_test(self):
        good = copy_of_foreman(self.tmp / "good", "0.3.0")
        self.assertIsNone(install.smoke_test(good))
        bad = copy_of_foreman(self.tmp / "bad", "0.3.1")
        (bad / "lib/foreman/panel.py").write_text("def broken(:\n")
        self.assertIn("import foreman.panel", install.smoke_test(bad))
        self.assertIn("missing", install.smoke_test(self.tmp / "nothing"))

    def test_panel_reexecs_into_the_new_version(self):
        """End to end: a panel started from one copy moves to the version the trace names, keeping its pid and
        port; a copy that fails the smoke test is not adopted and the panel says so."""
        old = copy_of_foreman(self.tmp / "v1", "0.1.0")
        new = copy_of_foreman(self.tmp / "v2", "0.2.0")
        broken = copy_of_foreman(self.tmp / "v3", "0.3.0")
        (broken / "lib/foreman/panel.py").write_text("raise SystemExit(3)\n")
        self.trace(old)
        port, token = free_port(), "k"
        log = open(self.tmp / "panel.log", "w")
        env = {**self.env, "FOREMAN_FOLLOW_EVERY": "0.3"}
        p = subprocess.Popen(
            [str(old / "bin/foreman"), "panel", "--port", str(port), "--token", token, "--follow-installed"],
            stdout=log,
            stderr=subprocess.STDOUT,
            env=env,
        )
        try:

            def api():
                for _ in range(100):
                    try:
                        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/projects?t={token}", timeout=2) as r:
                            return json.loads(r.read())
                    except OSError:
                        time.sleep(0.1)
                self.fail("the panel does not answer: " + (self.tmp / "panel.log").read_text())

            def until(cond):
                for _ in range(100):
                    d = api()
                    if cond(d):
                        return d
                    time.sleep(0.1)
                self.fail(f"never true: {d}")

            d = api()
            self.assertEqual((d["foreman_version"], d["foreman_root"], d["foreman_follows"]), ("0.1.0", str(old), True))
            self.trace(broken, old)
            d = until(lambda d: d["foreman_warning"])
            self.assertEqual(d["foreman_version"], "0.1.0")
            self.assertIn("0.3.0", d["foreman_warning"])
            self.trace(new, old)
            d = until(lambda d: d["foreman_version"] == "0.2.0")
            self.assertEqual((d["foreman_root"], d["foreman_warning"]), (str(new), None))
            self.assertIsNone(p.poll())  # same process (execv keeps the pid)
            self.assertEqual(d["projects"][0]["name"], "fake")
        finally:
            p.terminate()
            try:
                p.wait(30)
            except subprocess.TimeoutExpired:
                p.kill()
                p.wait()
            log.close()
        text = (self.tmp / "panel.log").read_text()
        self.assertIn("did not pass the smoke test", text)
        self.assertIn("moving from foreman 0.1.0", text)
        self.assertNotIn(f"t={token}", text)  # following: the key is not written to the log


# The fake launchctl and systemctl keep each label's state next to $FAKE_CTL_LOG. launchctl: loaded while
# $FAKE_CTL_LOG.loaded.<label> exists (the label is the last part of gui/<uid>/<label>, or the plist's file name for
# `bootstrap`); like launchd, `bootout` unloads asynchronously: with FAKE_UNLOAD_CALLS=N the label stays loaded for the
# next N launchctl calls (`never`: forever), and meanwhile `bootstrap` fails with launchd's error 5. systemctl: active
# while $FAKE_CTL_LOG.active.<unit> exists (`restart` or `enable --now` start it, `disable --now` and `stop` stop it).
# `launchctl print` and `systemctl show -p MainPID` give $FAKE_PID_<label, with . and - as _> or 4242.
FAKE_CTL = """#!/bin/sh
me=$(basename "$0")
echo "$me $*" >> "$FAKE_CTL_LOG"
st="$FAKE_CTL_LOG"
pid_of() {
  v=$(printf %s "$1" | tr .- __)
  eval "echo \\${FAKE_PID_$v:-4242}"
}
if [ "$me" = launchctl ]; then
  for left in "$st".unloading.*; do
    [ -f "$left" ] || continue
    l=${left#"$st".unloading.}
    n=$(cat "$left")
    if [ "$n" = never ]; then :
    elif [ "$n" -le 0 ]; then rm -f "$left" "$st.loaded.$l"
    else echo $((n - 1)) > "$left"; fi
  done
  case "$1" in
    bootstrap) l=$(basename "$3" .plist);;
    *) l=${2##*/};;
  esac
  loaded="$st.loaded.$l"
  case "$1" in
    bootout)
      [ -f "$loaded" ] || { echo "Boot-out failed: 3: No such process" >&2; exit 3; }
      case "${FAKE_UNLOAD_CALLS:-0}" in 0) rm -f "$loaded";; *) echo "$FAKE_UNLOAD_CALLS" > "$st.unloading.$l";; esac;;
    bootstrap)
      [ -f "$loaded" ] && { echo "Bootstrap failed: 5: Input/output error" >&2; exit 5; }
      touch "$loaded";;
    print)
      [ -f "$loaded" ] || { echo "Could not find service in domain" >&2; exit 113; }
      echo "  state = running"; echo "  pid = $(pid_of "$l")";;
  esac
  exit 0
fi
for u; do :; done
active="$st.active.$u"
case "$*" in
  "--user restart "*|"--user start "*|"--user enable --now "*) touch "$active";;
  "--user disable --now "*|"--user stop "*) rm -f "$active";;
  "--user is-active "*) [ -f "$active" ] || { echo inactive; exit 3; }; echo active;;
  "--user show "*) if [ -f "$active" ]; then pid_of "${u%.service}"; else echo 0; fi;;
esac
exit 0
"""

# The fake lsof (FOREMAN_LSOF) logs its arguments to $FAKE_CTL_LOG.lsof and answers $FAKE_LSOF_PID, or nothing.
FAKE_LSOF = """#!/bin/sh
echo "lsof $*" >> "$FAKE_CTL_LOG.lsof"
[ -n "$FAKE_LSOF_PID" ] || exit 1
echo "$FAKE_LSOF_PID"
"""


class ServiceTest(ProjectCase):
    def setUp(self):
        super().setUp()
        self.units = self.tmp / "units"
        os.environ["FOREMAN_SERVICE_DIR"] = str(self.units)
        bindir = self.tmp / "fakebin"
        bindir.mkdir()
        for n in ("launchctl", "systemctl"):
            (bindir / n).write_text(FAKE_CTL)
            (bindir / n).chmod(0o755)
        (bindir / "lsof").write_text(FAKE_LSOF)
        (bindir / "lsof").chmod(0o755)
        os.environ["FOREMAN_LSOF"] = str(bindir / "lsof")
        os.environ["FOREMAN_LAUNCHCTL"] = str(bindir / "launchctl")
        os.environ["FOREMAN_SYSTEMCTL"] = str(bindir / "systemctl")
        os.environ["FAKE_CTL_LOG"] = str(self.tmp / "ctl.log")
        self.assertTrue(install.service_dir("macos").is_relative_to(self.tmp))  # never the real units
        self.assertTrue(install.service_dir("linux").is_relative_to(self.tmp))

    def tearDown(self):
        for k in (
            *(k for k in os.environ if k.startswith("FAKE_PID_")),
            "FOREMAN_SERVICE_DIR",
            "FOREMAN_LAUNCHCTL",
            "FOREMAN_SYSTEMCTL",
            "FOREMAN_LSOF",
            "FAKE_LSOF_PID",
            "FAKE_CTL_LOG",
            "FAKE_UNLOAD_CALLS",
            "FOREMAN_LAUNCHD_WAIT",
            "FOREMAN_LAUNCHD_PAUSE",
        ):
            os.environ.pop(k, None)
        super().tearDown()

    def calls(self):
        f = self.tmp / "ctl.log"
        return f.read_text().splitlines() if f.exists() else []

    def assert_generic(self, text):
        self.assertNotIn(str(ROOT), text)  # never the plugin folder (versioned, deleted after updates)
        self.assertNotIn("/plugins/cache/", text)
        self.assertNotIn("--token", text)
        self.assertNotIn("TOKEN", text)
        self.assertIn(str(install.launcher_file()), text)

    def test_print_macos_and_linux(self):
        path = {"PATH": f"{ROOT / 'bin'}{os.pathsep}{os.environ['PATH']}"}  # the plugin's bin/ is dropped
        r = self.cli(
            "panel",
            "service",
            "print",
            "--platform",
            "macos",
            "--host",
            "0.0.0.0",
            "--port",
            "9999",
            "--label",
            "x.panel",
            env=path,
        )
        self.assertEqual(r.returncode, 0, r.stderr)
        d = plistlib.loads(r.stdout.encode())
        self.assertEqual(d["Label"], "x.panel")
        self.assertEqual(
            d["ProgramArguments"], ["/bin/sh", str(install.launcher_file()), "--host", "0.0.0.0", "--port", "9999"]
        )
        self.assertTrue(d["KeepAlive"])
        self.assertEqual(d["EnvironmentVariables"]["FOREMAN_STATE_HOME"], os.environ["FOREMAN_STATE_HOME"])
        self.assert_generic(r.stdout)
        self.assertIn('exec "$r/bin/foreman" panel --follow-installed', r.stderr)  # the launcher, for review
        r = self.cli("panel", "service", "print", "--platform", "linux")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn(f'ExecStart="/bin/sh" "{install.launcher_file()}" "--host" "127.0.0.1" "--port" "8766"', r.stdout)
        self.assertIn("Restart=always", r.stdout)
        self.assertIn("WantedBy=default.target", r.stdout)
        self.assert_generic(r.stdout)
        self.assertFalse(self.units.exists())  # print writes nothing
        self.assertFalse(install.install_dir().exists())
        self.assertEqual(self.calls(), [])
        self.assertEqual(self.cli("panel", "service", "print", "--label", "a b").returncode, 2)

    def test_install_status_uninstall_macos(self):
        os.environ["FOREMAN_PANEL_TOKEN"] = "from-env"  # the service does not see it: a key file is made anyway
        try:
            r = self.cli("panel", "service", "install", "--platform", "macos", "--host", "0.0.0.0")
        finally:
            os.environ.pop("FOREMAN_PANEL_TOKEN")
        self.assertEqual(r.returncode, 0, r.stderr)
        uid = os.getuid()
        plist = self.units / "foreman.panel.plist"
        self.assertEqual(
            self.calls(),
            [
                f"launchctl bootout gui/{uid}/foreman.panel",
                f"launchctl print gui/{uid}/foreman.panel",
                f"launchctl bootstrap gui/{uid} {plist}",
            ],
        )
        self.assert_generic(plist.read_text())
        token = Path(os.environ["FOREMAN_STATE_HOME"]) / "panel/token"
        self.assertEqual(stat.S_IMODE(token.stat().st_mode), 0o600)
        self.assertNotIn(token.read_text().strip(), plist.read_text())
        self.assertNotEqual(token.read_text().strip(), "from-env")
        self.assertEqual(install.roots_file().read_text(), f"{ROOT}\n")  # first start: the installing root
        self.assertTrue(os.access(install.launcher_file(), os.X_OK))
        r = self.cli("panel", "service", "status", "--platform", "macos")
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn("running, pid 4242", r.stdout)
        self.assertIn(str(ROOT), r.stdout)
        r = self.cli("panel", "service", "uninstall", "--platform", "macos")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.calls()[-1], f"launchctl bootout gui/{uid}/foreman.panel")
        self.assertFalse(plist.exists())
        self.assertFalse(install.launcher_file().exists())
        self.assertTrue(token.exists())  # the key stays (links keep working after a reinstall)
        self.assertEqual(self.cli("panel", "service", "status", "--platform", "macos").returncode, 1)
        # nothing outside the temporary directory
        self.assertEqual(sorted(p.name for p in self.units.iterdir()), [])

    def reinstall(self, unload_calls, wait):
        """Install over an already loaded label that launchd takes `unload_calls` launchctl calls to unload."""
        (self.tmp / "ctl.log.loaded.foreman.panel").touch()
        os.environ.update(
            FAKE_UNLOAD_CALLS=str(unload_calls), FOREMAN_LAUNCHD_WAIT=str(wait), FOREMAN_LAUNCHD_PAUSE="0.05"
        )
        r = self.cli("panel", "service", "install", "--platform", "macos")
        return r, [c.split()[1] for c in self.calls()]

    def test_install_waits_for_the_asynchronous_unload(self):
        r, verbs = self.reinstall(3, wait=30)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(verbs, ["bootout", "print", "print", "print", "print", "bootstrap"])

    def test_install_retries_bootstrap_after_error_5(self):
        r, verbs = self.reinstall(2, wait=0)  # the wait gives up at once: the first load hits error 5
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(verbs, ["bootout", "print", "bootstrap", "bootstrap"])
        self.assertTrue((self.tmp / "ctl.log.loaded.foreman.panel").exists())
        self.assertIn("running", self.cli("panel", "service", "status", "--platform", "macos").stdout)

    def test_install_never_unloaded_says_how_to_load_by_hand(self):
        start = time.monotonic()
        r, verbs = self.reinstall("never", wait=0.3)
        self.assertLess(time.monotonic() - start, 10)
        self.assertEqual(r.returncode, 1)
        self.assertEqual(verbs[0], "bootout")
        self.assertEqual(verbs.count("bootstrap"), install.BOOTSTRAP_TRIES)
        self.assertGreater(verbs.count("print"), 1)
        uid = os.getuid()
        plist = self.units / "foreman.panel.plist"
        self.assertIn("Bootstrap failed: 5: Input/output error", r.stderr)
        self.assertIn(
            f"Load it by hand: launchctl bootout gui/{uid}/foreman.panel; sleep 5; "
            f"launchctl bootstrap gui/{uid} {shlex.quote(str(plist))}",
            r.stderr,
        )
        self.assertTrue(plist.exists())  # the unit stays, ready for the command above

    def svc(self, platform, action, label=None, *args):
        return self.cli(
            "panel", "service", action, "--platform", platform, *(("--label", label) if label else ()), *args
        )

    def test_panel_units_reads_the_service_folder(self):
        self.assertEqual(install.panel_units("macos"), [])  # no folder yet
        self.units.mkdir()
        (self.units / "com.other.app.plist").write_bytes(
            plistlib.dumps({"Label": "com.other.app", "ProgramArguments": ["/usr/bin/true"]})
        )
        (self.units / "broken.plist").write_text("<plist> panel-launcher, not a plist")
        (self.units / "list.plist").write_bytes(plistlib.dumps(["panel-launcher"]))
        (self.units / "notes.txt").write_text(install.plist_text("txt.panel", "127.0.0.1", 1))
        (self.units / "dev.foreman.panel.plist").write_text(install.plist_text("dev.foreman.panel", "0.0.0.0", 8766))
        d = plistlib.loads(install.plist_text("bin.panel", "127.0.0.1", 9000).encode())
        d["ProgramArguments"] = d["ProgramArguments"][:2]  # an old unit without --host/--port: the defaults
        (self.units / "named-otherwise.plist").write_bytes(plistlib.dumps(d, fmt=plistlib.FMT_BINARY))
        elsewhere = plistlib.loads(install.plist_text("far.panel", "127.0.0.1", 8767).encode())
        elsewhere["ProgramArguments"][1] = str(self.tmp / "other-state/install/panel-launcher")
        (self.units / "far.panel.plist").write_bytes(plistlib.dumps(elsewhere))
        units = install.panel_units("macos")
        self.assertEqual(
            [(u.label, u.path.name, u.host, u.port, u.shares_launcher) for u in units],
            [
                ("bin.panel", "named-otherwise.plist", "127.0.0.1", 8766, True),
                ("dev.foreman.panel", "dev.foreman.panel.plist", "0.0.0.0", 8766, True),
                ("far.panel", "far.panel.plist", "127.0.0.1", 8767, False),
            ],
        )
        self.assertEqual(install.panel_units("linux"), [])  # .plist files are not systemd units
        # Linux: the label is the file name; systemd_quote is undone (a state root with %, $, quotes and spaces)
        os.environ["FOREMAN_STATE_HOME"] = str(self.tmp / 'st %h $HOME "q" \\b')
        (self.units / "foreman-panel.service").write_text(install.unit_text("ignored", "0.0.0.0", 8800))
        (self.units / "other.service").write_text("[Service]\nExecStart=/usr/bin/true panel-launcher-not\n")
        (self.units / "bad.service").write_text('[Service]\nExecStart="/bin/sh" "panel-launcher\n')
        (u,) = install.panel_units("linux")
        self.assertEqual(
            (u.label, u.launcher, u.host, u.port), ("foreman-panel", str(install.launcher_file()), "0.0.0.0", 8800)
        )
        self.assertTrue(u.shares_launcher)

    def test_unit_clashes(self):
        u = install.Unit("a", self.tmp / "a.plist", "/x/panel-launcher", "127.0.0.1", 8766)
        self.assertTrue(u.clashes("127.0.0.1", 8766))
        self.assertTrue(u.clashes("0.0.0.0", 8766))
        self.assertFalse(u.clashes("127.0.0.1", 8767))
        self.assertFalse(u.clashes("192.168.1.2", 8766))
        wide = install.Unit("b", self.tmp / "b.plist", "/x/panel-launcher", "0.0.0.0", 8766)
        self.assertTrue(wide.clashes("192.168.1.2", 8766))
        self.assertTrue(wide.clashes("::", 8766))
        self.assertFalse(wide.clashes("0.0.0.0", 1))

    def check_uninstall_keeps_a_shared_launcher(self, platform):
        os.environ.update(FAKE_PID_dev_foreman_panel="5151", FAKE_PID_foreman_panel="6161")
        for label, port in (("dev.foreman.panel", "8766"), ("foreman.panel", "8767")):
            r = self.svc(platform, "install", label, "--port", port)
            self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual([u.label for u in install.panel_units(platform)], ["dev.foreman.panel", "foreman.panel"])
        # the fake keeps each label apart (the Linux status shows no pid yet)
        pids = {"dev.foreman.panel": "5151", "foreman.panel": "6161"} if platform == "macos" else {}
        for label in ("dev.foreman.panel", "foreman.panel"):
            r = self.svc(platform, "status", label)
            self.assertEqual(r.returncode, 0, r.stdout)
            self.assertIn(pids.get(label, "active"), r.stdout)
        # G: uninstalling one keeps the launcher the other runs
        r = self.svc(platform, "uninstall", "dev.foreman.panel")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("launcher kept: used by foreman.panel", r.stdout)
        self.assertNotIn(str(install.launcher_file()), r.stdout.splitlines()[0])
        self.assertTrue(os.access(install.launcher_file(), os.X_OK))
        self.assertEqual([u.label for u in install.panel_units(platform)], ["foreman.panel"])
        r = self.svc(platform, "status", "foreman.panel")
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn(pids.get("foreman.panel", "active"), r.stdout)
        self.assertNotEqual(self.svc(platform, "status", "dev.foreman.panel").returncode, 0)
        # a unit that runs another state root's launcher does not keep this one
        far = install.service_text(platform, "far.panel", "127.0.0.1", 9000).replace(
            str(install.launcher_file()), str(self.tmp / "other-state/install/panel-launcher")
        )
        far_file = install.unit_file(platform, "far.panel")
        far_file.write_text(far)
        # H: uninstalling the last one removes the launcher too
        r = self.svc(platform, "uninstall", "foreman.panel")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn(str(install.launcher_file()), r.stdout)
        self.assertNotIn("launcher kept", r.stdout)
        self.assertFalse(install.launcher_file().exists())
        self.assertEqual(sorted(p.name for p in self.units.iterdir()), [far_file.name])

    def test_uninstall_keeps_a_shared_launcher_macos(self):
        self.check_uninstall_keeps_a_shared_launcher("macos")

    def test_uninstall_keeps_a_shared_launcher_linux(self):
        self.check_uninstall_keeps_a_shared_launcher("linux")

    def put_unit(self, platform, label, host, port, running=True, launcher=None):
        """A panel unit in the service folder, loaded (running) or not in the fake service manager, without install
        (which T2 makes refuse a clash of ports)."""
        text = install.service_text(platform, label, host, port)
        if launcher:
            text = text.replace(str(install.launcher_file()), launcher)
        f = install.unit_file(platform, label)
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(text)
        state = self.tmp / (f"ctl.log.loaded.{label}" if platform == "macos" else f"ctl.log.active.{f.name}")
        if running:
            state.touch()
        else:
            state.unlink(missing_ok=True)
        return f

    def status_lines(self, platform, label=None):
        r = self.svc(platform, "status", label)
        return r.returncode, r.stdout.splitlines()

    def check_status_lists_the_other_units(self, platform):
        os.environ.update(FAKE_PID_dev_foreman_panel="5151", FAKE_PID_foreman_panel="6161")
        running = "running" if platform == "macos" else "active"
        self.put_unit(platform, "dev.foreman.panel", "0.0.0.0", 8766)
        # without --label and one other unit: status describes that one, and says so (J1)
        rc, lines = self.status_lines(platform)
        self.assertEqual(rc, 0, lines)
        only = "the only panel service here is dev.foreman.panel (--label to choose another)"
        self.assertEqual(lines[0], f"label     {only}")
        self.assertIn(str(install.unit_file(platform, "dev.foreman.panel")) + ": present", lines[1])
        self.assertIn(f"{running}, pid 5151", "\n".join(lines))
        self.assertIn("port      0.0.0.0:8766", lines)  # running: the address only, nobody is asked
        self.assertFalse([ln for ln in lines if ln.startswith("other")])
        # E: two units; the requested one lists the other with its port and state
        self.put_unit(platform, "foreman.panel", "127.0.0.1", 8767)
        far_launcher = self.tmp / "st2/install/panel-launcher"
        far = self.put_unit(platform, "far.panel", "127.0.0.1", 9000, running=False, launcher=str(far_launcher))
        rc, lines = self.status_lines(platform)  # foreman.panel exists: no guessing
        self.assertEqual(rc, 0, lines)
        self.assertFalse([ln for ln in lines if ln.startswith("label")])
        self.assertIn(f"{running}, pid 6161", "\n".join(lines))
        stopped = "not loaded" if platform == "macos" else "inactive"
        self.assertEqual(
            [ln for ln in lines if ln.startswith("other")],
            [
                f"other     dev.foreman.panel: port 8766, {running}, pid 5151",
                f"other     far.panel: port 9000, {stopped}, another state root ({far_launcher})",
            ],
        )
        rc, lines = self.status_lines(platform, "dev.foreman.panel")
        self.assertEqual(rc, 0, lines)  # the others do not change the exit code (O1)
        self.assertIn(f"other     foreman.panel: port 8767, {running}, pid 6161", lines)
        # several of our units and none named foreman.panel: like install and uninstall, status does not guess (exit
        # 2); it says to choose and lists every unit, describing none
        install.unit_file(platform, "foreman.panel").unlink()
        self.put_unit(platform, "b.panel", "127.0.0.1", 8768)
        rc, lines = self.status_lines(platform)
        self.assertEqual(rc, 2)
        several = "several panel services here (b.panel, dev.foreman.panel): choose one with --label"
        self.assertEqual(lines[0], f"label     {several}")
        self.assertEqual(
            lines[1:],
            [
                f"other     b.panel: port 8768, {running}, pid 4242",
                f"other     dev.foreman.panel: port 8766, {running}, pid 5151",
                f"other     far.panel: port 9000, {stopped}, another state root ({far_launcher})",
            ],
        )
        for action in ("install", "uninstall"):
            r = self.svc(platform, action)
            self.assertEqual(r.returncode, 2, r.stdout)
            self.assertIn(several, r.stderr)
        self.assertEqual(
            sorted(p.name for p in self.units.iterdir()),
            sorted(install.unit_file(platform, n).name for n in ("b.panel", "dev.foreman.panel", "far.panel")),
        )
        far.unlink()

    def test_status_lists_the_other_units_macos(self):
        self.check_status_lists_the_other_units("macos")

    def test_status_lists_the_other_units_linux(self):
        self.check_status_lists_the_other_units("linux")

    def check_status_says_who_holds_the_port(self, platform):
        os.environ["FAKE_PID_dev_foreman_panel"] = "5151"
        port = free_port()
        self.put_unit(platform, "foreman.panel", "0.0.0.0", port, running=False)
        self.put_unit(platform, "dev.foreman.panel", "0.0.0.0", port)
        rc, lines = self.status_lines(platform, "foreman.panel")
        self.assertEqual(rc, 1)
        self.assertIn(f"port      0.0.0.0:{port}: free", lines)  # nothing listens: lsof is not asked
        self.assertFalse((self.tmp / "ctl.log.lsof").exists())
        with socket.socket() as s:
            s.bind(("127.0.0.1", port))
            s.listen()
            # F: the port answers and lsof gives the pid of the other unit
            os.environ["FAKE_LSOF_PID"] = "5151"
            rc, lines = self.status_lines(platform, "foreman.panel")
            self.assertEqual(rc, 1)
            self.assertIn(f"port      0.0.0.0:{port}: in use by pid 5151 (dev.foreman.panel)", lines)
            self.assertIn(f"other     dev.foreman.panel: port {port}, ", "\n".join(lines))
            self.assertEqual(
                (self.tmp / "ctl.log.lsof").read_text().splitlines(), [f"lsof -nP -iTCP:{port} -sTCP:LISTEN -t"]
            )
            os.environ["FAKE_LSOF_PID"] = "999"  # a panel started by hand, or another program
            rc, lines = self.status_lines(platform, "foreman.panel")
            self.assertIn(f"port      0.0.0.0:{port}: in use by pid 999 (not a foreman service)", lines)
            del os.environ["FAKE_LSOF_PID"]  # lsof says nothing (missing, too slow, another user's process)
            rc, lines = self.status_lines(platform, "foreman.panel")
            self.assertIn(f"port      0.0.0.0:{port}: in use", lines)
            os.environ["FOREMAN_LSOF"] = str(self.tmp / "no-such-lsof")
            rc, lines = self.status_lines(platform, "foreman.panel")
            self.assertIn(f"port      0.0.0.0:{port}: in use", lines)

    def test_status_says_who_holds_the_port_macos(self):
        self.check_status_says_who_holds_the_port("macos")

    def test_status_says_who_holds_the_port_linux(self):
        self.check_status_says_who_holds_the_port("linux")

    def load_calls(self, platform, label):
        """The service manager calls of loading `label` (service_install)."""
        if platform == "macos":
            uid = os.getuid()
            unit = install.unit_file(platform, label)
            return [f"launchctl bootout gui/{uid}/{label}", f"launchctl print gui/{uid}/{label}",
                    f"launchctl bootstrap gui/{uid} {unit}"]  # fmt: skip
        name = install.unit_file(platform, label).name
        return ["systemctl --user daemon-reload", f"systemctl --user enable {name}", f"systemctl --user restart {name}"]

    def labels(self, platform):
        return [u.label for u in install.panel_units(platform)]

    def check_install_reinstalls_the_only_unit(self, platform):
        port = free_port()
        dev = self.put_unit(platform, "dev.foreman.panel", "0.0.0.0", port)
        # A (J1): no --label and one panel unit of another name: install reinstalls that one, by its name, and says so
        r = self.svc(platform, "install", None, "--port", str(port))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(
            r.stdout.splitlines()[0],
            "label     the only panel service here is dev.foreman.panel (--label to choose another): reinstalling it",
        )
        self.assertEqual(self.labels(platform), ["dev.foreman.panel"])  # no second unit
        self.assertEqual(self.calls(), self.load_calls(platform, "dev.foreman.panel"))
        self.assertTrue(os.access(install.launcher_file(), os.X_OK))
        # without --host and --port the unit keeps its own address (it was 0.0.0.0, the default is 127.0.0.1)
        r = self.svc(platform, "install")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn(f"on 0.0.0.0:{port}", r.stdout)
        (unit,) = install.panel_units(platform)
        self.assertEqual((unit.path, unit.host, unit.port), (dev, "0.0.0.0", port))
        self.assertEqual(self.svc(platform, "status").returncode, 0)  # status resolves the same label
        # uninstall without --label resolves it the same way
        r = self.svc(platform, "uninstall")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("dev.foreman.panel (--label to choose another): uninstalling it", r.stdout)
        self.assertEqual(self.labels(platform), [])
        self.assertFalse(install.launcher_file().exists())
        # nothing installed: the default label, on the default address
        r = self.svc(platform, "install", None, "--port", str(port))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("label", r.stdout)
        self.assertEqual(self.labels(platform), ["foreman.panel"])
        self.assertEqual(install.panel_units(platform)[0].host, "127.0.0.1")

    def test_install_reinstalls_the_only_unit_macos(self):
        self.check_install_reinstalls_the_only_unit("macos")

    def test_install_reinstalls_the_only_unit_linux(self):
        self.check_install_reinstalls_the_only_unit("linux")

    def check_install_refuses_a_port_clash(self, platform):
        port = free_port()
        dev = self.put_unit(platform, "dev.foreman.panel", "0.0.0.0", port)
        # B: another name on the same port: refused, nothing written or loaded, both ways out named
        r = self.svc(platform, "install", "foreman.panel", "--port", str(port))
        self.assertEqual(r.returncode, 2, r.stdout)
        self.assertIn(f"port {port} is taken by the panel service dev.foreman.panel (0.0.0.0:{port})", r.stderr)
        self.assertIn("foreman panel service install --label dev.foreman.panel", r.stderr)
        self.assertIn("to replace it with foreman.panel: add --replace", r.stderr)
        self.assertEqual(self.labels(platform), ["dev.foreman.panel"])
        self.assertFalse(install.launcher_file().exists())
        self.assertEqual(self.calls(), [])
        # C: --replace unloads and removes the other one, then installs: one unit, the launcher there
        r = self.svc(platform, "install", "foreman.panel", "--port", str(port), "--replace")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn(f"replaced  dev.foreman.panel (0.0.0.0:{port}): removed {dev}", r.stdout)
        self.assertEqual(self.labels(platform), ["foreman.panel"])
        self.assertTrue(os.access(install.launcher_file(), os.X_OK))
        uid = os.getuid()
        unload = (
            [f"launchctl bootout gui/{uid}/dev.foreman.panel", f"launchctl print gui/{uid}/dev.foreman.panel"]
            if platform == "macos"
            else ["systemctl --user disable --now dev.foreman.panel.service"]
        )
        self.assertEqual(self.calls(), unload + self.load_calls(platform, "foreman.panel"))
        loaded = "loaded.dev.foreman.panel" if platform == "macos" else "active.dev.foreman.panel.service"
        self.assertFalse((self.tmp / f"ctl.log.{loaded}").exists())
        self.assertEqual(self.svc(platform, "status").returncode, 0)
        # --replace never removes another state root's unit: still refused
        far_launcher = str(self.tmp / "st2/install/panel-launcher")
        far = self.put_unit(platform, "far.panel", "127.0.0.1", port + 1 if port < 65535 else port - 1,
                            launcher=far_launcher)  # fmt: skip
        far_port = install.read_unit(platform, far).port
        r = self.svc(platform, "install", "foreman.panel", "--port", str(far_port), "--replace")
        self.assertEqual(r.returncode, 2, r.stdout)
        self.assertIn(f"far.panel (127.0.0.1:{far_port}) of another state root (it runs {far_launcher})", r.stderr)
        self.assertTrue(far.exists())
        self.assertEqual(self.labels(platform), ["far.panel", "foreman.panel"])
        # --replace only goes with install
        self.assertEqual(self.svc(platform, "status", None, "--replace").returncode, 2)

    def test_install_refuses_a_port_clash_macos(self):
        self.check_install_refuses_a_port_clash("macos")

    def test_install_refuses_a_port_clash_linux(self):
        self.check_install_refuses_a_port_clash("linux")

    def check_install_names_the_other_units(self, platform):
        port = free_port()
        self.put_unit(platform, "dev.foreman.panel", "0.0.0.0", port)
        other = next(p for p in iter(free_port, None) if p != port)
        # D: another port: installed next to it, with one line naming the other
        r = self.svc(platform, "install", "other.panel", "--port", str(other))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn(f"note      another panel service: dev.foreman.panel on 0.0.0.0:{port}", r.stdout.splitlines())
        self.assertEqual(self.labels(platform), ["dev.foreman.panel", "other.panel"])
        self.assertEqual(self.calls(), self.load_calls(platform, "other.panel"))
        # a port held by something that is no panel unit (a panel started by hand): installed, with a warning
        third = next(p for p in iter(free_port, None) if p not in (port, other))
        with socket.socket() as s:
            s.bind(("127.0.0.1", third))
            s.listen()
            os.environ["FAKE_LSOF_PID"] = "999"
            r = self.svc(platform, "install", "third.panel", "--port", str(third))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn(
            f"note      port {third} is in use by pid 999, not by a panel service: the service starts once it is free",
            r.stdout.splitlines(),
        )
        self.assertEqual(self.labels(platform), ["dev.foreman.panel", "other.panel", "third.panel"])

    def test_install_names_the_other_units_macos(self):
        self.check_install_names_the_other_units("macos")

    def test_install_names_the_other_units_linux(self):
        self.check_install_names_the_other_units("linux")

    def test_service_path(self):
        scratch = self.tmp / "tools"  # exists, but in the temporary folder: gone by the next start
        scratch.mkdir()
        given = os.pathsep.join(
            [
                "/sbin",
                "",
                "bin",
                ".",
                "/no/such/dir",
                str(scratch),
                str(ROOT / "bin"),
                "/x/plugins/cache/y/bin",
                "/usr/bin",
                "/sbin",
            ]
        )
        self.assertEqual(install.service_path(given), "/sbin:/usr/bin:/bin:/usr/sbin")
        self.assertEqual(install.service_path(""), "/usr/bin:/bin:/usr/sbin:/sbin")

    def test_install_uninstall_linux(self):
        trace = fake_root(self.tmp / "cache/0.2.0", "0.2.0").resolve()
        install.install_dir().mkdir(parents=True)
        install.roots_file().write_text(f"{trace}\n")
        r = self.cli("panel", "service", "install", "--platform", "linux", "--label", "foreman-panel")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(install.roots_file().read_text(), f"{trace}\n")  # an existing trace is kept
        self.assertEqual(
            self.calls(),
            [
                "systemctl --user daemon-reload",
                "systemctl --user enable foreman-panel.service",
                "systemctl --user restart foreman-panel.service",
            ],
        )
        self.assertTrue((self.units / "foreman-panel.service").exists())
        r = self.cli("panel", "service", "status", "--platform", "linux", "--label", "foreman-panel")
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn("0.2.0", r.stdout)
        r = self.cli("panel", "service", "uninstall", "--platform", "linux", "--label", "foreman-panel")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(
            self.calls()[-2:],
            ["systemctl --user disable --now foreman-panel.service", "systemctl --user daemon-reload"],
        )
        self.assertFalse((self.units / "foreman-panel.service").exists())

    def test_launcher_runs_the_first_usable_root(self):
        install.install_dir().mkdir(parents=True)
        launcher = install.launcher_file()
        launcher.write_text(install.launcher_text())
        spaced = fake_root(self.tmp / "with space/0.2.0", "0.2.0")
        install.roots_file().write_text(f"{self.tmp / 'gone'}\n{spaced}")  # no final newline either
        r = subprocess.run(
            ["/bin/sh", str(launcher), "--host", "0.0.0.0", "--port", "1"],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.strip(), f"ran {spaced}/bin/foreman panel --follow-installed --host 0.0.0.0 --port 1")
        # a working copy listed first (a trace from an older foreman) runs only if nothing else can
        repo = fake_root(self.tmp / "repo", "0.3.1", git="file")
        install.roots_file().write_text(f"{repo}\n{self.tmp / 'gone'}\n{spaced}\n")
        r = subprocess.run(["/bin/sh", str(launcher)], capture_output=True, text=True, timeout=30, check=False)
        self.assertEqual(r.stdout.strip(), f"ran {spaced}/bin/foreman panel --follow-installed")
        install.roots_file().write_text(f"{self.tmp / 'gone'}\n{repo}\n")
        r = subprocess.run(["/bin/sh", str(launcher)], capture_output=True, text=True, timeout=30, check=False)
        self.assertEqual(r.stdout.strip(), f"ran {repo}/bin/foreman panel --follow-installed")
        install.roots_file().write_text(f"{self.tmp / 'gone'}\n")
        r = subprocess.run(["/bin/sh", str(launcher)], capture_output=True, text=True, timeout=30, check=False)
        self.assertEqual(r.returncode, 1)
        self.assertIn("no installed foreman", r.stderr)
        install.roots_file().unlink()
        self.assertEqual(
            subprocess.run(["/bin/sh", str(launcher)], capture_output=True, timeout=30, check=False).returncode, 1
        )
