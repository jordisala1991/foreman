"""Hidden acceptance checks (holdout.py) and integrate's gate: a spec in the product's folder of hidden checks, sealed
with its baseline on main, run by `foreman integrate` on a copy of the branch after the local CI. The hidden files
carry marker strings that must never leave the folder: not in `integrate --check` (an agent may run it), the CI log,
the notes, the progress, the state, nor in what the agent is told when a case fails (only the symptom: the owner's
choice)."""

import json
import sys
from pathlib import Path

from helpers import ProjectCase, git, make_repo

from foreman import config, holdout
from foreman.backlog import Backlog

HERE = Path(__file__).resolve().parent

TOML = """
[project]
name = "fake"
owner = "Owner"

[paths]
worktrees = "{tmp}/wt"
holdout = "{tmp}/hidden"

[repos.app]
path = "{tmp}/app"
remote = "none"
local_ci = "true"
post_create = ["echo copy > made-by-post-create.txt"]

[resources.widget]
guard = ["make up"]
"""

SEED = {"README.md": "hi\n", "lib/core.py": "a = 1\n"}

# Every marker lives only in the hidden folder: in a command, a setup step, a file's name or a file's content.
MARKERS = ("QZMARK-CMD", "QZMARK-SETUP", "QZMARKFILE", "QZMARK-CONTENT", "zq-alpha", "zq-beta")
SYMPTOM_1 = "Saving a value keeps the old one"
SYMPTOM_2 = "The welcome page greets nobody"

SPEC = """issue = "AB-1"
repo = "app"
setup = ["echo QZMARK-SETUP > /dev/null"]
{extra}

[[scenario]]
id = "zq-alpha"
command = "grep -q 'a = 2' lib/core.py && test -f made-by-post-create.txt # QZMARK-CMD"
on_main = "fail"
symptom = "{s1}"

[[scenario]]
id = "zq-beta"
command = "sh cases/QZMARKFILE.sh"
on_main = "pass"
symptom = "{s2}"
"""


class HoldoutCase(ProjectCase):
    toml = TOML

    def setUp(self):
        super().setUp()
        make_repo(self.tmp / "app", SEED)
        self.b = Backlog(self.project)
        self.b.init()
        with self.b.mutate() as d:
            self.b.add_project(d, "E1", "One", "intro")
            self.b.add_issue(d, "E1", "AB-1", "feature", status="in progress")
            self.b.set_sub(d, "AB-1", "T1", title="the feature", agent="w1", branch="ab1", status="▶")
        self.hidden = self.tmp / "hidden"
        self.wt = None

    def write_spec(self, extra="", s1=SYMPTOM_1, s2=SYMPTOM_2):
        d = self.hidden / "AB-1"
        (d / "files/cases").mkdir(parents=True, exist_ok=True)
        (d / "holdout.toml").write_text(SPEC.format(extra=extra, s1=s1, s2=s2))
        (d / "files/cases/QZMARKFILE.sh").write_text("# QZMARK-CONTENT\ngrep -q hi README.md\n")
        return d

    def commit(self, files, msg="work"):
        if self.wt is None:
            r = self.cli("worktree", "app", "w1", "ab1")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.wt = self.tmp / "wt/app-w1"
        for f, text in files.items():
            (self.wt / f).parent.mkdir(parents=True, exist_ok=True)
            (self.wt / f).write_text(text)
        git(self.wt, "add", "-A")
        git(self.wt, "commit", "-q", "-m", msg)
        return git(self.wt, "rev-parse", "HEAD")

    def integrate(self, *args):
        return self.cli("integrate", "app", "ab1", "AB-1 T1", *args)

    def seal(self, *args):
        return self.cli("holdout", "seal", "AB-1", *args)

    def readable(self):
        """Everything foreman wrote where an agent reads: the state folder (CI log, progress, holdout.json, agents…)
        and the home (notes, backlog); not foreman.toml, which names the folder by design."""
        out = []
        for root in (self.project.state, self.home):
            out += [p.read_text(errors="replace") for p in root.rglob("*") if p.is_file() and p.name != "foreman.toml"]
        return "\n".join(out)

    def assert_no_markers(self, text, where):
        for m in (*MARKERS, str(self.hidden)):
            self.assertNotIn(m, text, f"{m} leaked into {where}")

    def note(self):
        return "".join(p.read_text() for p in (self.home / "notes").glob("*.md"))

    def main_sha(self):
        return git(self.tmp / "app", "rev-parse", "main")


class Spec(HoldoutCase):
    def project_(self):
        return config.load_project(self.home)

    def test_reads_and_counts(self):
        self.write_spec('threshold = 0.5\ntimeout = 20\nsubs = ["T1"]')
        s = holdout.spec(self.project_(), "AB-1")
        self.assertEqual([c.id for c in s.scenarios], ["zq-alpha", "zq-beta"])
        self.assertEqual((s.needed, s.timeout, s.subs), (1, 20.0, ["T1"]))
        self.assertTrue(s.applies("app", "T1") and s.applies("app", None) and s.applies("app", "T1,T2"))
        self.assertFalse(s.applies("app", "T2") or s.applies("web", "T1"))
        self.assertIsNone(holdout.spec(self.project_(), "AB-9"))

    def test_bad_specs(self):
        d = self.write_spec()
        good = (d / "holdout.toml").read_text()
        cases = {
            "unknown keys bogus": good.replace('repo = "app"', 'repo = "app"\nbogus = 1'),
            "is not a repo": good.replace('repo = "app"', 'repo = "web"'),
            "but the folder is AB-1": good.replace('issue = "AB-1"', 'issue = "AB-2"'),
            "on_main must be": good.replace('on_main = "pass"', 'on_main = "maybe"'),
            "no symptom": good.replace(f'symptom = "{SYMPTOM_2}"', 'symptom = " "'),
            "id zq-alpha twice": good.replace('id = "zq-beta"', 'id = "zq-alpha"'),
            "must be short": good.replace('id = "zq-beta"', 'id = "Zq Beta"'),
            "threshold = 1.5 out of range": good.replace('repo = "app"', 'repo = "app"\nthreshold = 1.5'),
            "is not a resource": good.replace('repo = "app"', 'repo = "app"\nlock = "gpu"'),
            "no [[scenario]]": good.split("[[scenario]]")[0],
            "unknown keys extra": good.replace('on_main = "fail"', 'on_main = "fail"\nextra = 1'),
            "Invalid": good + "\n[[[",
        }
        for why, text in cases.items():
            with self.subTest(why=why):
                (d / "holdout.toml").write_text(text)
                with self.assertRaises(holdout.HoldoutError) as e:
                    holdout.spec(self.project_(), "AB-1")
                self.assertIn(why, str(e.exception))

    def test_a_symptom_may_not_carry_the_check(self):
        """The symptom is the only hidden text the agent is told: it may not name a case's command, a hidden file
        (its path or its name) or the folder."""
        for s1, word in (
            ("It fails: sh cases/QZMARKFILE.sh", "sh cases/QZMARKFILE.sh"),
            ("QZMARKFILE.sh says no", "QZMARKFILE.sh"),
            (f"see {self.tmp}/hidden", str(self.tmp / "hidden")),
            ("the case in files/ fails", "files/"),
        ):
            with self.subTest(word=word):
                self.write_spec(s1=s1)
                with self.assertRaises(holdout.HoldoutError) as e:
                    holdout.spec(self.project_(), "AB-1")
                self.assertIn(f"the symptom of zq-alpha names {word!r}", str(e.exception))

    def test_the_agent_message_has_none_of_the_hidden_text(self):
        """The owner's choice: the agent is told only the symptom of each failing case, as a user would tell it; never a
        case's id, command, data, setup, file or place."""
        self.write_spec()
        s = holdout.spec(self.project_(), "AB-1")
        msg = holdout.agent_message(s, ["zq-alpha", "zq-beta"])
        self.assertIn(f"- {SYMPTOM_1}", msg)
        self.assertIn(f"- {SYMPTOM_2}", msg)
        hidden = [c.command for c in s.scenarios] + s.setup + [c.id for c in s.scenarios]
        for f in holdout.files(s.folder):
            hidden += [f.name, str(f.relative_to(s.folder))] + f.read_text().split()
        hidden += [w for c in s.scenarios for w in c.command.split() if len(w) > 3]
        for text in hidden:
            if text in (SYMPTOM_1, SYMPTOM_2) or text in SYMPTOM_1.split() + SYMPTOM_2.split():
                continue
            self.assertNotIn(text, msg)
        self.assert_no_markers(msg, "the agent's message")
        self.assertNotIn(SYMPTOM_2, holdout.agent_message(s, ["zq-alpha"]))


class Seal(HoldoutCase):
    def test_seal_runs_the_baseline_and_keeps_no_content(self):
        self.write_spec()
        r = self.seal()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("baseline on main", r.stdout)
        self.assertIn("as expected (1 fail, 1 pass)", r.stdout)
        self.assertIn("hidden check AB-1 sealed: 2 cases, 2 files", r.stdout)
        st = json.loads((self.project.state / "holdout.json").read_text())
        self.assertEqual(st["issues"]["AB-1"]["seal"]["baseline"]["fail"], ["zq-alpha"])
        self.assert_no_markers(json.dumps(st["issues"]["AB-1"]["seal"]).replace("zq-alpha", ""), "holdout.json")
        self.assert_no_markers(self.readable().replace("zq-alpha", "").replace("zq-beta", ""), "the state")
        # the copy is gone, from the disk and from git
        self.assertEqual([p.name for p in (self.hidden / ".runs").iterdir()], ["AB-1"])
        self.assertNotIn(".runs", git(self.tmp / "app", "worktree", "list"))
        log = next((self.hidden / ".runs/AB-1").glob("baseline-main-*.log")).read_text()
        self.assertIn("QZMARK-CMD", log)  # the full output stays in the folder

    def test_a_case_that_does_not_do_on_main_what_it_says_is_not_sealed(self):
        d = self.write_spec()
        (d / "holdout.toml").write_text(
            (d / "holdout.toml").read_text().replace('on_main = "fail"', 'on_main = "pass"')
        )
        r = self.seal()
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("zq-alpha fails on main (on_main = pass)", r.stdout)
        self.assertIn("not sealed: 1 case do not do on main what on_main says", r.stderr)
        self.assertIsNone(holdout.sealed(config.load_project(self.home), "AB-1"))
        r = self.seal("--no-baseline")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("(no baseline: --no-baseline)", r.stdout)

    def test_errors(self):
        r = self.seal()
        self.assertEqual(r.returncode, 2)
        self.assertIn("no holdout.toml in", r.stderr)
        r = self.cli("holdout", "status")
        self.assertIn("no hidden checks in", r.stdout)
        r = self.cli("holdout", "run", "app", "main", "AB-1")
        self.assertEqual(r.returncode, 2)
        self.write_spec()
        self.seal("--no-baseline")
        r = self.cli("holdout", "run", "nope", "main", "AB-1")
        self.assertEqual(r.returncode, 2)
        self.assertIn("no repo 'nope'", r.stderr)
        r = self.cli("holdout", "run", "app", "no-such-branch", "AB-1")
        self.assertEqual(r.returncode, 2)
        self.assertIn("no commit no-such-branch", r.stderr)


class Gate(HoldoutCase):
    def test_no_hidden_check_integrates_as_today(self):
        self.commit({"lib/core.py": "a = 2\n"})
        r = self.integrate("--result", "ok")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertNotIn("hidden", r.stdout + r.stderr + self.note())
        self.assertFalse((self.project.state / "holdout.json").exists())

    def test_unsealed_or_changed_stops_with_nothing_done(self):
        self.write_spec()
        self.commit({"lib/core.py": "a = 2\n"})
        main = self.main_sha()
        for args in (("--check",), ("--result", "ok")):
            r = self.integrate(*args)
            self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
            self.assertIn("== hidden check (AB-1): 2 cases, not sealed: nothing done", r.stdout)
        self.assertEqual(self.seal().returncode, 0)
        (self.hidden / "AB-1/files/cases/QZMARKFILE.sh").write_text("true\n")
        r = self.integrate("--result", "ok")
        self.assertEqual(r.returncode, 2)
        self.assertIn("changed since it was sealed: nothing done", r.stdout)
        (self.hidden / "AB-1/holdout.toml").write_text("[[[")
        r = self.integrate("--check")
        self.assertEqual(r.returncode, 2)
        self.assertIn("does not read: nothing done (`foreman holdout status AB-1`", r.stdout)
        self.assert_no_markers(r.stdout + r.stderr, "--check")
        self.assertEqual(self.main_sha(), main)

    def test_check_says_it_exists_and_never_its_content(self):
        self.write_spec()
        self.seal()
        self.commit({"lib/core.py": "a = 2\n"})
        r = self.integrate("--check")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("== hidden check (AB-1): sealed, 2 cases (needs 2), runs after the local CI", r.stdout)
        self.assert_no_markers(r.stdout + r.stderr, "--check")
        self.assertNotIn(SYMPTOM_1, r.stdout + r.stderr)

    def test_fail_tells_only_the_symptom_then_a_fix_goes_in(self):
        self.write_spec()
        self.assertEqual(self.seal().returncode, 0)
        main = self.main_sha()
        self.commit({"lib/core.py": "a = 3\n"})
        r = self.integrate("--result", "ok")
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("== hidden check AB-1: 1 of 2 pass (needs 2)", r.stdout)
        self.assertIn("round 1 of 2 (failed: zq-alpha;", r.stderr)
        self.assertIn("nothing merged", r.stderr)
        message = r.stderr.split("as it is (never a case's id, command, data or place):\n", 1)[1]
        self.assertIn(f"- {SYMPTOM_1}", message)
        self.assertNotIn(SYMPTOM_2, message)
        self.assert_no_markers(message, "the message for the agent")
        self.assert_no_markers(r.stdout, "integrate's output")
        self.assertEqual(self.main_sha(), main)
        self.assert_no_markers(self.readable().replace("zq-alpha", "").replace("zq-beta", ""), "state and home")
        self.assertNotIn(SYMPTOM_1, self.readable())
        # the agent fixes it: round 2 passes and goes in
        new = self.commit({"lib/core.py": "a = 2\n"}, "fix")
        r = self.integrate("--result", "ok")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("== hidden check AB-1: 2 of 2 pass (needs 2)", r.stdout)
        self.assertIn("hidden check AB-1: 2/2 (round 2)", self.note())
        self.assertEqual(self.main_sha(), new)
        self.assert_no_markers(self.note(), "the note")
        self.assertFalse(any(p.name.startswith("AB-1-app") for p in (self.hidden / ".runs").iterdir()))

    def test_a_pass_is_reused_on_the_same_tip(self):
        self.write_spec()
        self.seal()
        self.commit({"lib/core.py": "a = 2\n"})
        r = self.cli("holdout", "run", "app", "ab1", "AB-1", "--full")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("2 of 2 pass (needs 2)", r.stdout)
        self.assertIn("QZMARK-CMD", r.stdout)  # --full: the orchestrator's
        r = self.integrate("--result", "ok")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("2/2 already passed on", r.stdout)
        self.assertIn("not run again", r.stdout)

    def test_no_third_round_only_the_owner_lets_it_in(self):
        self.write_spec()
        self.seal()
        self.commit({"lib/core.py": "a = 3\n"})
        self.assertEqual(self.integrate("--result", "ok").returncode, 2)
        self.assertEqual(self.integrate("--result", "ok").returncode, 2)  # the same tip: still round 1
        self.commit({"lib/core.py": "a = 4\n"}, "fix")
        r = self.integrate("--result", "ok")
        self.assertEqual(r.returncode, 2)
        self.assertIn("round 2 of 2", r.stderr)
        self.assertIn("no third round: ask the owner", r.stderr)
        self.assertIn(f"- {SYMPTOM_1}", r.stderr)
        r = self.cli("holdout", "status", "AB-1")
        self.assertIn("1/2 round 2, failed zq-alpha", r.stdout)
        self.assertIn("1/2 round 1, failed zq-alpha", r.stdout)
        r = self.integrate("--result", "ok", "--approved", "Owner, chat 10:00")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("the owner's yes (Owner, chat 10:00) lets it in with 1 of 2 pass", r.stdout)
        self.assertIn("hidden check AB-1: 1/2, the owner lets it in (round 2)", self.note())

    def test_sealing_again_starts_the_rounds_again(self):
        self.write_spec()
        self.seal()
        self.commit({"lib/core.py": "a = 3\n"})
        self.integrate("--result", "ok")
        self.commit({"lib/core.py": "a = 4\n"}, "fix")
        self.assertIn("round 2 of 2", self.integrate("--result", "ok").stderr)
        d = self.hidden / "AB-1/holdout.toml"
        d.write_text(d.read_text().replace("grep -q 'a = 2'", "grep -q 'a = 4'"))
        self.assertEqual(self.seal("--no-baseline").returncode, 0)
        r = self.integrate("--result", "ok")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("hidden check AB-1: 2/2 (round 1)", self.note())

    def test_threshold_subs_and_lock(self):
        self.write_spec('threshold = 0.5\nlock = "widget"\nsubs = ["T1"]')
        self.seal()
        self.commit({"lib/core.py": "a = 3\n"})
        with self.b.mutate() as d:
            self.b.set_sub(d, "AB-1", "T2", title="more", agent="w1", branch="ab1", status="▶")
        r = self.cli("integrate", "app", "ab1", "AB-1 T2", "--check")
        self.assertIn("== hidden check (AB-1): for T1, not T2; not run here", r.stdout)
        r = self.integrate("--result", "ok")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("1 of 2 pass (needs 1)", r.stdout)
        self.assertIn("waiting for widget", r.stdout)
        uses = (self.project.state.parent / "locks/widget/uses.jsonl").read_text()
        self.assertIn("hidden check AB-1", uses)

    def test_a_copy_that_cannot_be_prepared_stops(self):
        self.write_spec()
        self.seal()
        self.commit({"lib/core.py": "a = 2\n"})
        self.write_toml(
            TOML.replace('post_create = ["echo copy > made-by-post-create.txt"]', 'post_create = ["false"]')
        )
        r = self.integrate("--result", "ok")
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("hidden check AB-1 could not run (the repo's post_create failed in the copy", r.stderr)
        self.assertNotIn(".runs", git(self.tmp / "app", "worktree", "list"))


class Remote(HoldoutCase):
    """With a remote: the check runs after the local CI and before anything is pushed."""

    toml = TOML.replace('remote = "none"\n', 'github = "acme/app"\n')

    def setUp(self):
        super().setUp()
        seed = self.tmp / "app"
        git(self.tmp, "clone", "-q", "--bare", str(seed), str(self.tmp / "app.git"))
        git(seed, "remote", "add", "origin", str(self.tmp / "app.git"))
        git(seed, "fetch", "-q", "origin")
        git(seed, "branch", "-q", "-u", "origin/main", "main")
        gh = self.tmp / "gh"
        gh.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{HERE / "fake_gh.py"}" "$@"\n')
        gh.chmod(0o755)
        self.ghenv = {"FOREMAN_GH": str(gh), "FAKE_GH_DIR": str(self.tmp / "ghstate"), "FOREMAN_CHECKS_RETRY_S": "0"}

    def test_nothing_pushed_on_a_failure(self):
        self.write_spec()
        self.seal()
        self.commit({"lib/core.py": "a = 3\n"})
        args = ("--title", "The feature", "--body", "What and why.", "--result", "ok")
        r = self.cli("integrate", "app", "ab1", "AB-1 T1", *args, env=self.ghenv)
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("nothing pushed", r.stderr)
        self.assertNotIn("ab1", git(self.tmp / "app.git", "branch"))
        self.assertFalse((self.tmp / "ghstate/calls.log").exists())
        self.commit({"lib/core.py": "a = 2\n"}, "fix")
        r = self.cli("integrate", "app", "ab1", "AB-1 T1", *args, env=self.ghenv)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        calls = (self.tmp / "ghstate/calls.log").read_text()
        self.assert_no_markers(calls, "the PR")
        self.assertNotIn("hidden", calls)
        self.assertIn("hidden check AB-1: 2/2 (round 2)", self.note())
