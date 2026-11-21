import itertools
import json
import os
import re
import tempfile
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest import mock

import pytest
from fakeorca import FakeOrca
from helpers import Env, fm, git, install_fm, version_folder

from foreman import launcher, util
from foreman.core.project import registry
from foreman.core.project.config import Project
from foreman.core.tasks import backlog
from foreman.loop import tick
from foreman.providers import registry as provider_registry
from foreman.providers.claude import login
from foreman.providers.claude.rows import ClaudeRows
from foreman.providers.orca import hooks
from foreman.providers.runner import NO_ROWS
from foreman.services.delivery import check
from foreman.steps.health import checks, health, main_tests, notes, orca
from foreman.ui.panel import views


class TestHealth:
    @pytest.fixture(autouse=True)
    def set_up(self, env):
        self.e = env
        self.p = self.e.p

    def triage(self):
        return [i for i in backlog.tasks(provider_registry.backlog(self.p).load()) if i["status"] == "triage"]

    def test_the_python_note_is_shown_by_health(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.dict(os.environ, {"FOREMAN_REGISTRY": f"{d}/projects.json"}):
            assert notes.python_note() == []
            line = "foreman: needs Python 3.12 or newer; the python3 found is 3.9.6 (/usr/bin/python3)"
            registry.python_note_file().write_text(line + "\n")
            assert notes.python_note() == ["! " + line]

    def test_dead_loop_is_seen_from_outside(self):
        assert checks.loop_dead(self.p)[0]["key"] == "loop-dead"
        util.write_json(self.p.state / "loop.json", {"epoch": time.time(), "time": util.now()})
        assert checks.loop_dead(self.p) == []

    def test_orphan_worktree_is_filed_once(self):
        git(self.e.repo, "worktree", "add", "-q", "-b", "x", str(self.e.home / "worktrees/ghost"), "main")
        health.run(self.p, NO_ROWS)
        health.run(self.p, NO_ROWS)
        t = self.triage()
        assert len(t) == 1
        assert "ghost" in t[0]["title"]

    def test_a_finding_that_goes_away_and_returns_is_filed_again_only_once_its_task_closed(self):
        git(self.e.repo, "worktree", "add", "-q", "-b", "x", str(self.e.home / "worktrees/ghost"), "main")
        health.run(self.p, NO_ROWS)
        first = self.triage()[0]["id"]
        provider_registry.backlog(self.p).set_status(first, "done")
        health.run(self.p, NO_ROWS)  # still there, seen continuously: not again
        assert self.triage() == []
        git(self.e.repo, "worktree", "remove", str(self.e.home / "worktrees/ghost"))
        health.run(self.p, NO_ROWS)
        git(self.e.repo, "worktree", "add", "-q", str(self.e.home / "worktrees/ghost"), "x")
        health.run(self.p, NO_ROWS)
        assert len(self.triage()) == 1

    def test_an_answer_not_delivered_after_ten_minutes_is_a_finding(self):
        tid = provider_registry.backlog(self.p).add("x", status="ready", criteria="y")
        qid = provider_registry.backlog(self.p).ask(tid, "a", "A or B?")
        provider_registry.backlog(self.p).answer(qid, "B")
        assert checks.answers_undelivered(self.p) == []
        with provider_registry.backlog(self.p).edit() as d:
            d["decisions_resolved"][0]["answered_at"] = "2020-01-01 00:00:00"
        (f,) = checks.answers_undelivered(self.p)
        assert tid in f["title"]
        assert "«B»" in f["detail"]
        with provider_registry.backlog(self.p).edit() as d:  # an answer made before the stamp existed: its `by` still has the time
            del d["decisions_resolved"][0]["answered_at"]
            d["decisions_resolved"][0]["by"] = "owner (panel), 2020-01-01 00:00:00"
        assert len(checks.answers_undelivered(self.p)) == 1

    def test_live_worktree_is_not_an_orphan(self):
        provider_registry.backlog(self.p).add("x", status="ready", criteria="y")
        tick.tick(self.p)
        assert checks.orphan_worktrees(self.p) == []

    def test_failing_tests_on_main(self):
        e = Env(test="false")
        f = main_tests.main_tests(e.p)
        assert f[0]["title"] == "Tests fail on main"
        assert json.loads((e.home / "state/ci.json").read_text())["result"] == "fail"

    def test_stuck_agent(self):
        tid = provider_registry.backlog(self.p).add("x", status="ready", criteria="y")
        tick.tick(self.p)
        sid = self.e.task(tid)["agent"]["session"]
        t = self.e.tmp / "transcripts/proj"
        t.mkdir(parents=True)
        (t / f"{sid}.jsonl").write_text("{}\n")
        old = time.time() - 3 * 3600
        os.utime(t / f"{sid}.jsonl", (old, old))
        provider_registry.backlog(self.p).update(tid, agent={**self.e.task(tid)["agent"], "started": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(old))})
        rows = ClaudeRows({sid: {"state": "working"}})
        assert checks.agents(self.p, rows)[0]["title"] == f"{tid}'s agent looks stuck"

    def test_loop_step_errors_become_findings(self):
        util.append_jsonl(self.p.state / "errors.jsonl", {"time": util.now(), "step": "queue", "error": "boom"})
        assert "queue" in checks.loop_errors(self.p)[0]["title"]


class TestMetrics:
    def test_cost_from_transcripts_counts_each_response_once(self):
        from foreman.core.oversight import costs
        from foreman.steps import metrics

        e = Env()
        tid = provider_registry.backlog(e.p).add("x", status="ready", criteria="y")
        provider_registry.backlog(e.p).update(tid, sessions=["s1"])
        d = e.tmp / "transcripts/proj"
        d.mkdir(parents=True)
        rec = {
            "type": "assistant",
            "timestamp": "2026-11-07T10:00:00Z",
            "message": {"id": "m1", "model": "claude-sonnet-5-5", "usage": {"input_tokens": 1_000_000, "output_tokens": 0}},
        }
        (d / "s1.jsonl").write_text(json.dumps(rec) + "\n" + json.dumps(rec) + "\n")
        assert costs.of_sessions(["s1"])["usd"] == 2.0
        assert metrics.per_task(e.p)[0]["usd"] == 2.0

    def test_usage_skips_lines_that_are_not_objects(self):
        from foreman.core.oversight import costs

        good = {"type": "assistant", "message": {"id": "m1", "model": "claude-sonnet-5-5", "usage": {"input_tokens": 1000, "output_tokens": 0}}}
        f = Path(tempfile.mkdtemp()) / "s.jsonl"
        f.write_text("\n".join(["[1, 2]", '"x"', "null", "not json", json.dumps(good)]) + "\n")
        u = costs.usage([f])
        assert u["tokens"] == 1000


class TestEvidence:
    def commit(self, e, name, text="x"):
        (e.repo / name).write_text(text)
        git(e.repo, "add", "-A")
        git(e.repo, "commit", "-qm", name)

    def triage(self, e):
        return [i for i in backlog.tasks(provider_registry.backlog(e.p).load()) if i["status"] == "triage"]

    def test_failing_main_is_one_task_until_it_passes(self):
        e = Env(test="test ! -e broken")
        stamps = (f"2026-01-01 00:00:{n:02d}" for n in itertools.count(1))
        with mock.patch.object(util, "now", lambda: next(stamps)):
            self.commit(e, "broken")
            health.run(e.p, NO_ROWS)
            assert len(self.triage(e)) == 1
            self.commit(e, "other")
            health.run(e.p, NO_ROWS)
            assert len(self.triage(e)) == 1
            git(e.repo, "rm", "-q", "broken")
            git(e.repo, "commit", "-qm", "fix")
            health.run(e.p, NO_ROWS)
            assert len(self.triage(e)) == 1
            self.commit(e, "broken")
            health.run(e.p, NO_ROWS)
        assert len(self.triage(e)) == 2

    def test_failing_tests_task_names_commit_step_and_tail(self):
        e = Env(test="echo lint ok; echo boom 1>&2; exit 1")
        health.run(e.p, NO_ROWS)
        (i,) = self.triage(e)
        head = git(e.repo, "rev-parse", "main").strip()[:12]
        for text in (head, "echo lint ok", "boom"):
            assert text in i["detail"]

    def test_run_tests_keeps_the_last_twenty_lines(self):
        e = Env(test="seq 1 30; exit 1")
        assert check.run_tests(e.p, e.repo) == ("fail", "\n".join(map(str, range(11, 31))))

    def test_failing_step_is_the_make_target(self):
        out = "lint ok\nFAIL line\nmake[1]: *** [test] Error 1\nmake: *** [check] Error 2\n"
        assert main_tests.failing_step(out, "make check") == "test"
        assert main_tests.failing_step("make[1]: *** [Makefile:25: lint] Error 1", "make check") == "lint"
        assert main_tests.failing_step("boom", "make check") == "make check"

    def test_every_finding_has_meaning_evidence_and_a_pending_hypothesis(self):
        e = Env()
        git(e.repo, "worktree", "add", "-q", "-b", "x", str(e.home / "worktrees/ghost"), "main")
        (e.p.state).mkdir(parents=True, exist_ok=True)
        (e.p.state / "errors.jsonl").write_text(json.dumps({"step": "s", "error": "bad", "time": util.now()}) + "\n")
        tid = provider_registry.backlog(e.p).add("x", status="ready", criteria="y")
        provider_registry.backlog(e.p).update(tid, blocked="its agent stopped: x")
        found = [
            *checks.loop_dead(Env().p),
            *checks.orphan_worktrees(e.p),
            *checks.loop_errors(e.p),
            *checks.agents(e.p, NO_ROWS),
            *main_tests.main_tests(Env(test="false").p),
            *checks.login_check(e.p, check=lambda c: login.Login(False)),
            *checks.login_expiring(login.Login(True, datetime.now(UTC) + timedelta(hours=1))),
        ]
        assert len(found) >= 7
        for f in found:
            assert f["meaning"] and f["evidence"], f["key"]
            assert f["hypothesis"] == "pending"
            for part in (f["meaning"], f["evidence"], "pending"):
                assert part in f["detail"]

    def test_a_symptom_with_an_open_task_is_not_filed_twice(self):
        e = Env()
        wt = e.home / "worktrees/ghost"
        errors = e.p.state / "errors.jsonl"
        errors.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps({"step": "s", "error": "bad", "time": util.now()}) + "\n"
        git(e.repo, "worktree", "add", "-q", "-b", "x", str(wt), "main")
        errors.write_text(line)
        health.run(e.p, NO_ROWS)
        git(e.repo, "worktree", "remove", str(wt))
        errors.write_text("")
        health.run(e.p, NO_ROWS)
        git(e.repo, "worktree", "add", "-q", str(wt), "x")
        errors.write_text(line)
        health.run(e.p, NO_ROWS)
        assert len(self.triage(e)) == 2  # one orphan, one error: each filed once
        assert sorted(i["source"] for i in self.triage(e)) == ["health:error:s:bad", "health:orphan:ghost"]


class TestVersions:
    @pytest.fixture(autouse=True)
    def set_up(self, env):
        self.e = env
        self.p = self.e.p

    def test_health_shows_the_versions(self, request):
        util.write_json(self.p.state / "loop.json", {"version": "aaa"})
        install_fm(request, version_folder(self.e.tmp / "cache", "ccc"))
        assert f"versions: this command {launcher.ROOT.name} · the loop aaa · installed ccc\n" in fm(self.p.home, "health")

    def test_a_loop_json_that_is_not_an_object_gives_no_beat(self):
        for hb in (None, [], 3):
            (self.p.state / "loop.json").unlink(missing_ok=True)
            if hb is not None:
                util.write_json(self.p.state / "loop.json", hb)
            assert views.loop_beat(self.p) == {"time": None, "alive": False}
            assert views.versions(self.p)["loop"] is None
            assert checks.loop_dead(self.p)[0]["key"] == "loop-dead"
            assert "the loop unknown" in notes.versions(self.p)[0]

    def test_without_an_install_health_says_the_jobs_cannot_start(self, request):
        """Whether or not a loop ever ran from the plugin: a job that exits at once writes no heartbeat."""
        for hb in (None, [], {"version": "aaa"}):
            (self.p.state / "loop.json").unlink(missing_ok=True)
            if hb is not None:
                util.write_json(self.p.state / "loop.json", hb)
            lines = notes.versions(self.p)
            assert lines[0].endswith("installed none")
            assert lines[1].startswith("! foreman@foreman cannot start the loop and the panel: foreman@foreman is not installed"), lines
        install_fm(request, version_folder(self.e.tmp / "cache", "ccc"))
        assert len(notes.versions(self.p)) == 1


class TestOrcaHealth:
    """`foreman health` on a runner = "orca" project (DESIGN D8): the binary, Orca answering, Orca's hooks."""

    @pytest.fixture(autouse=True)
    def set_up(self, env, monkeypatch):
        self.e = env
        self.runner("orca")
        self.fake = FakeOrca(env.tmp)
        monkeypatch.setenv("ORCA_BIN", str(self.fake.bin))
        self.settings = env.tmp / "settings.json"

    def runner(self, name):
        toml = self.e.home / "foreman.toml"
        text = re.sub(r"(?m)^runner = .*\n", "", toml.read_text())
        toml.write_text(text.replace("[agents]\n", f'[agents]\nrunner = "{name}"\n', 1))
        self.p = Project.load(self.e.home)

    def hooked(self, events=hooks.STATE_EVENTS):
        cmd = {"hooks": [{"type": "command", "command": "/x/Orca/hooks/claude-hook.sh"}]}
        self.settings.write_text(json.dumps({"hooks": {ev: [cmd] for ev in events}}))

    def keys(self):
        return [f["key"] for f in orca.findings(self.p, self.settings)]

    def test_a_missing_binary_is_a_finding(self, monkeypatch):
        monkeypatch.setenv("ORCA_BIN", str(self.e.tmp / "nope"))
        self.hooked()
        assert self.keys() == ["orca-missing"]

    def test_missing_hooks_are_a_finding(self):
        assert self.keys() == ["orca-hooks"]  # no file
        self.settings.write_text("{}")
        assert self.keys() == ["orca-hooks"]
        self.hooked(hooks.STATE_EVENTS[:-1])
        f = orca.findings(self.p, self.settings)[0]
        assert f["key"] == "orca-hooks" and hooks.STATE_EVENTS[-1] in f["evidence"] and hooks.STATE_EVENTS[0] not in f["evidence"]
        self.hooked()
        assert self.keys() == []

    def test_a_closed_orca_is_a_warning_not_a_finding(self):
        self.hooked()
        assert orca.warnings(self.p) == []
        self.fake.down()
        assert [w[:21] for w in orca.warnings(self.p)] == ["! Orca is not running"]
        assert self.keys() == []

    def test_health_prints_the_warning(self):
        self.fake.down()
        assert "! Orca is not running" in fm(self.p.home, "health")

    def test_a_claude_project_has_no_orca_line(self, monkeypatch):
        self.runner("claude")
        monkeypatch.setenv("ORCA_BIN", str(self.e.tmp / "nope"))
        assert orca.findings(self.p, self.settings) == [] and orca.warnings(self.p) == []
        assert "Orca" not in fm(self.p.home, "health")
