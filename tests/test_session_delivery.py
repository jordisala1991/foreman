"""The lifecycle of a session agent (a Claude Code session of its own, `foreman launch --session`): its main-thread
events (session_id, no agent_id) feed its agents.json entry like a subagent's: activity, delivery, the `Stop` hook with
the delivery check, cost from its transcript, the state `claude agents --json` gives, `foreman agents --stop`, not
being counted as dying with the orchestrator's session, and an early pass when it delivers. A session foreman did not
register sees no change. `claude` is a fake in PATH."""

import json
import os
import time
from datetime import datetime

from helpers import git
from test_launch_session import SID, SessionLaunchCase
from test_transcripts import rec

from foreman import agents as agents_mod
from foreman import costs, hooks_mod, identity, loop, util

REPORT = "# Report\n\nMisled: none\n"
OWNER = "0f0f0f0f-owner-session"


class SessionLifecycle(SessionLaunchCase):
    def setUp(self):
        super().setUp()
        self.on()
        self.fake()
        self.run_launch(*self.prod("--session"))
        ((self.token, _),) = self.launches().items()
        self.wt = self.tmp / "wt" / "app-s1"
        self.transcript = self.tmp / "claude" / f"{SID}.jsonl"
        self.transcript.parent.mkdir()
        self.transcript.write_text(rec("m1", model="claude-sonnet-5-5", input_tokens=7) + "\n")

    def tok(self, token=True):
        return {"FOREMAN_LAUNCH": self.token} if token else {"FOREMAN_LAUNCH": ""}

    def send(self, name, session=SID, token=True, **kw):
        ev = {"hook_event_name": name, "session_id": session, "transcript_path": str(self.transcript), **kw}
        r = self.cli("hook", "agents", inp=json.dumps({"cwd": str(self.home), **ev}), env=self.tok(token))
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout) if r.stdout.strip() else None

    def bash(self, cmd, **kw):
        return self.send(
            "PostToolUse",
            tool_name="Bash",
            tool_input={"command": cmd},
            tool_response={"stdout": ""},
            cwd=str(self.wt),
            **kw,
        )

    def entry(self, sid=SID):
        return agents_mod.agents(self.project).get(sid)

    def state(self):
        return agents_mod.agent_state(self.entry())

    def deliver_work(self):
        (self.wt / "work.txt").write_text("work\n")
        git(self.wt, "add", "-A")
        git(self.wt, "commit", "-q", "-m", "work")
        d = self.home / "reviews"
        d.mkdir(exist_ok=True)
        (d / "2026-11-07-s1.md").write_text(REPORT)


class MainThreadTest(SessionLifecycle):
    def test_a_tool_call_is_activity_with_the_sessions_transcript(self):
        self.bash("git status")
        e = self.entry()
        self.assertEqual((e["tool"], self.state()), ("Bash", "working"))
        self.assertEqual(e["transcript"], str(self.transcript))
        self.assertTrue(e["activity"])
        self.assertIsNotNone(agents_mod.last_sign(e))

    def test_the_entry_is_made_from_the_token_when_launch_could_not_tie_the_id(self):
        util.write_json(self.project.state / "agents.json", {})
        rec_ = self.launches()[self.token]
        rec_.pop("agent", None)
        rec_.pop("session_id", None)
        util.write_json(self.project.state / "launches.json", {self.token: rec_})
        self.bash("git status")
        e = self.entry()
        self.assertEqual((e["name"], e["launch"], e[identity.SESSION_MARK]), ("s1", self.token, True))
        self.assertEqual(self.launches()[self.token]["session_id"], SID)

    def test_a_token_foreman_does_not_know_makes_no_entry(self):
        util.write_json(self.project.state / "agents.json", {})
        util.write_json(self.project.state / "launches.json", {})
        self.bash("git status")
        self.assertEqual(agents_mod.agents(self.project), {})

    def test_delivery_is_recorded_by_the_progress_done_command(self):
        self.bash('foreman --project fake progress --done "ok" --outcome partial --report reviews/2026-11-07-s1.md')
        e = self.entry()
        self.assertEqual(
            (self.state(), e["outcome"], e["report"]), ("delivered", "partial", "reviews/2026-11-07-s1.md")
        )

    def test_resumed_work_after_a_delivery_is_working_again(self):
        self.bash('foreman --project fake progress --done "no" --outcome failed')
        self.send("Stop", last_assistant_message="done", stop_hook_active=False)
        self.assertEqual(self.state(), "delivered")
        agents = agents_mod.agents(self.project)
        agents[SID]["stopped"] = True
        util.write_json(self.project.state / "agents.json", agents)
        self.bash("git log")
        e = self.entry()
        self.assertEqual(self.state(), "working")
        self.assertNotIn("stopped", e)

    def test_review_f1_a_delivery_survives_a_later_tool_and_stop(self):
        """deliver, stop, a tool call (the owner asked «show me the diff»), stop: still delivered, its branch ready."""
        self.deliver_work()
        self.bash('foreman --project fake progress --done "all good" --outcome ok --report reviews/2026-11-07-s1.md')
        self.cli(
            "--project", "fake", "progress", "--done", "all good", "--outcome", "ok",
            "--report", "reviews/2026-11-07-s1.md", env={"FOREMAN_WHO": "s1"},
        )  # fmt: skip
        self.assertIsNone(self.send("Stop", last_assistant_message="Delivered."))
        self.assertEqual(self.state(), "delivered")
        self.bash("git diff")
        self.assertEqual(self.state(), "working")
        self.assertIsNone(self.send("Stop", last_assistant_message="Here is the diff."))
        e = self.entry()
        self.assertEqual((self.state(), e["outcome"]), ("delivered", "ok"))
        self.assertEqual(e["report"], "reviews/2026-11-07-s1.md")
        self.assertNotIn(SID, [h["id"] for h in loop.live_agents(self.project)])

    def test_review_f4_a_new_message_gives_the_delivery_check_its_blocks_again(self):
        for _ in range(2):
            self.assertEqual(self.send("Stop", last_assistant_message="x")["decision"], "block")
        self.assertIsNone(self.send("Stop", last_assistant_message="x"))  # let go, incomplete
        self.bash("git status")  # woken by a message
        self.assertNotIn("delivery_blocks", self.entry())
        self.assertEqual(self.send("Stop", last_assistant_message="x")["decision"], "block")

    def test_review_f3_waking_clears_claude_state(self):
        agents = agents_mod.agents(self.project)
        agents[SID]["claude_state"] = "stopped"
        util.write_json(self.project.state / "agents.json", agents)
        self.assertEqual(self.state(), "stopped")
        self.bash("git status")
        self.assertEqual(self.state(), "working")

    def test_subagent_events_of_a_session_agent_are_not_agents(self):
        """Claude Code's compaction runs internal subagents with the session's agent type: no entry, no check."""
        for name in ("SubagentStart", "SubagentStop", "PostToolUse"):
            out = self.send(name, agent_id="a9", agent_type="foreman:dev", last_assistant_message="x")
            self.assertIsNone(out)
        self.assertEqual(set(agents_mod.agents(self.project)), {SID})

    def test_the_token_alone_is_enough_for_the_subagent_events_too(self):
        util.write_json(self.project.state / "agents.json", {})
        util.write_json(self.project.state / "launches.json", {})
        self.send("SubagentStop", agent_id="a9", agent_type="foreman:dev", last_assistant_message="x")
        self.assertEqual(agents_mod.agents(self.project), {})


class StopTest(SessionLifecycle):
    def test_stop_without_delivery_is_blocked_with_what_is_missing(self):
        out = self.send("Stop", last_assistant_message="finished", stop_hook_active=False)
        self.assertEqual(out["decision"], "block")
        self.assertIn("try 1 of 2", out["reason"])
        self.assertIn("no commits on b/s1 beyond main", out["reason"])
        self.assertIn("no delivery recorded", out["reason"])
        e = self.entry()
        self.assertEqual((e["delivery_blocks"], "end" in e, self.state()), (1, False, "working"))

    def test_after_two_blocks_it_stops_incomplete(self):
        for _ in range(2):
            self.assertEqual(self.send("Stop", last_assistant_message="x")["decision"], "block")
        self.assertIsNone(self.send("Stop", last_assistant_message="x", stop_hook_active=True))
        e = self.entry()
        self.assertTrue(e["delivery_incomplete"]["missing"])
        self.assertTrue(e["end"])

    def test_stop_with_a_complete_delivery_goes_through(self):
        self.deliver_work()
        self.bash('foreman --project fake progress --done "all good" --outcome ok --report reviews/2026-11-07-s1.md')
        self.assertIsNone(self.send("Stop", last_assistant_message="All good, delivered."))
        e = self.entry()
        self.assertEqual(
            (self.state(), e["final"], e["model"]), ("delivered", "All good, delivered.", "claude-sonnet-5-5")
        )
        self.assertNotIn("delivery_blocks", e)

    def test_stop_of_a_failed_delivery_needs_no_commits(self):
        self.bash('foreman --project fake progress --done "could not" --outcome failed')
        self.assertIsNone(self.send("Stop", last_assistant_message="failed"))

    def test_stop_for_background_tasks_is_not_checked(self):
        self.assertIsNone(self.send("Stop", last_assistant_message="waiting", background_tasks=[{"id": "b1"}]))
        e = self.entry()
        self.assertEqual((self.state(), "delivery_blocks" in e), ("waiting", False))


class UnregisteredTest(SessionLifecycle):
    """A session foreman did not register (no entry, no launch, no token) sees no change."""

    def test_its_events_write_nothing_and_its_stop_is_never_blocked(self):
        before = (self.project.state / "agents.json").read_bytes()
        for name, kw in (
            ("Stop", {"last_assistant_message": "x"}),
            ("PostToolUse", {"tool_name": "Bash", "tool_input": {"command": 'foreman progress --done "x"'}}),
        ):
            self.assertIsNone(self.send(name, session=OWNER, token=False, **kw))
        self.assertEqual((self.project.state / "agents.json").read_bytes(), before)

    def test_a_subagent_of_such_a_session_is_recorded_as_before(self):
        self.send("SubagentStart", session=OWNER, token=False, agent_id="a9", agent_type="foreman:dev")
        self.assertIn("a9", agents_mod.agents(self.project))

    def test_no_claude_runs_without_a_session_agent(self):
        util.write_json(self.project.state / "agents.json", {"a9": {"start": "2026-11-07T10:00:00", "type": "dev"}})
        self.log.unlink(missing_ok=True)
        self.assertFalse(agents_mod.refresh_sessions(self.project))
        self.assertFalse(self.log.exists())


class CrossCheckTest(SessionLifecycle):
    def rows(self, state):
        f = self.bin / "claude"
        f.write_text(
            "#!/bin/sh\n" + f'echo \'[{{"id":"8e28877c","sessionId":"{SID}","name":"s1","state":"{state}"}}]\'\n'
        )
        f.chmod(0o755)

    def path(self):
        return {"PATH": f"{self.bin}{os.pathsep}{os.environ['PATH']}"}

    def refresh(self, state):
        self.rows(state)
        old = os.environ.get("PATH")
        os.environ["PATH"] = self.path()["PATH"]
        try:
            return agents_mod.refresh_sessions(self.project)
        finally:
            os.environ["PATH"] = old

    def test_failed_and_stopped_sessions_are_stopped_and_working_again_clears_it(self):
        self.assertTrue(self.refresh("failed"))
        self.assertEqual((self.entry()["claude_state"], self.state()), ("failed", "stopped"))
        self.assertFalse(self.refresh("failed"))
        self.assertTrue(self.refresh("working"))
        self.assertEqual(self.state(), "working")
        self.assertTrue(self.refresh("stopped"))
        self.assertEqual(self.state(), "stopped")

    def test_review_f2_a_session_missing_from_the_list_is_gone_after_the_grace(self):
        """Live (Claude Code 2.1.317): `claude stop` and `claude rm` take a session off the default list; `--all`
        keeps a stopped one as `stopped` and drops a removed one; a killed process is respawned by the supervisor."""
        f = self.bin / "claude"
        f.write_text('#!/bin/sh\necho \'[{"sessionId":"other","state":"working"}]\'\n')
        f.chmod(0o755)
        old = os.environ["PATH"]
        os.environ["PATH"] = self.path()["PATH"]
        try:
            self.assertFalse(agents_mod.refresh_sessions(self.project))  # just started: it may not be listed yet
            self.assertEqual(self.state(), "working")
            agents = agents_mod.agents(self.project)
            agents[SID]["start"] = "2026-11-07T00:00:00"
            util.write_json(self.project.state / "agents.json", agents)
            self.assertTrue(agents_mod.refresh_sessions(self.project))
        finally:
            os.environ["PATH"] = old
        self.assertEqual((self.entry()["claude_state"], self.state()), ("gone", "stopped"))

    def test_the_rows_asked_for_are_the_whole_list(self):
        self.rows("working")
        f = self.bin / "claude"
        f.write_text(f.read_text().replace("echo '", f'echo "$@" > "{self.log}.args"; echo \'', 1))
        self.cli("agents", env=self.path())
        self.assertEqual((self.log.with_name("claude.args.args")).read_text().split(), ["agents", "--json", "--all"])

    def test_a_delivered_agent_stays_delivered(self):
        self.bash('foreman --project fake progress --done "x"')
        self.refresh("stopped")
        self.assertEqual(self.state(), "delivered")

    def test_foreman_agents_shows_it(self):
        self.rows("failed")
        r = self.cli("agents", env=self.path())
        self.assertIn("stopped", r.stdout)
        self.assertEqual(self.state(), "stopped")

    def test_unreadable_rows_change_nothing(self):
        f = self.bin / "claude"
        f.write_text("#!/bin/sh\necho not json\n")
        f.chmod(0o755)
        old = os.environ["PATH"]
        os.environ["PATH"] = self.path()["PATH"]
        try:
            self.assertFalse(agents_mod.refresh_sessions(self.project))
        finally:
            os.environ["PATH"] = old
        self.assertNotIn("claude_state", self.entry())


class StopCommandTest(SessionLifecycle):
    def test_agents_stop_runs_claude_stop_on_the_session(self):
        r = self.cli("agents", "--stop", "s1", env={"PATH": f"{self.bin}{os.pathsep}{os.environ['PATH']}"})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.argv(), ["stop", SID[:8]])
        self.assertEqual(self.state(), "stopped")

    def test_stop_gives_claude_the_short_id_and_a_full_id_is_rejected(self):
        f = self.bin / "claude"
        f.write_text(
            '#!/bin/sh\nif [ "$1" = stop ] && [ "${#2}" -ne 8 ]; then\n'
            "  echo \"No job matching '$2'. Run 'claude agents' to list running sessions.\" >&2\n  exit 1\nfi\nexit 0\n"
        )
        r = self.cli("agents", "--stop", "s1", env={"PATH": f"{self.bin}{os.pathsep}{os.environ['PATH']}"})
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_a_claude_that_fails_is_reported_and_the_mark_stays(self):
        self.fake(tail="exit 0")
        f = self.bin / "claude"
        f.write_text('#!/bin/sh\necho "no such session" >&2\nexit 1\n')
        r = self.cli("agents", "--stop", "s1", env={"PATH": f"{self.bin}{os.pathsep}{os.environ['PATH']}"})
        self.assertEqual(r.returncode, 1)
        self.assertIn("claude stop failed", r.stderr)
        self.assertEqual(self.state(), "stopped")

    def test_review_f5_a_prefix_of_several_sessions_stops_none(self):
        agents = agents_mod.agents(self.project)
        agents["8e28aaaa-other"] = {**agents[SID], "name": "s2"}
        util.write_json(self.project.state / "agents.json", agents)
        r = self.cli("agents", "--stop", "8e28", env={"PATH": f"{self.bin}{os.pathsep}{os.environ['PATH']}"})
        self.assertEqual(r.returncode, 1)
        self.assertIn("matches 2 agents", r.stderr)
        self.assertNotIn("stopped", self.entry())
        self.assertFalse(self.log.with_name("claude.args").read_text().startswith("stop"))

    def test_a_subagent_is_only_marked(self):
        util.write_json(self.project.state / "agents.json", {"a9": {"start": "2026-11-07T10:00:00", "name": "x"}})
        self.log.unlink(missing_ok=True)
        r = self.cli("agents", "--stop", "x", env={"PATH": f"{self.bin}{os.pathsep}{os.environ['PATH']}"})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse(self.log.exists())


class RenewalAndPassTest(SessionLifecycle):
    def test_a_session_agent_does_not_die_with_the_orchestrator_session(self):
        """Its transcript path has no `/S1/` and, before its first event, none at all: it is not one of S1's."""
        self.assertEqual(self.state(), "working")
        self.assertEqual(loop.session_agents(self.project, "S1"), [])
        self.bash("git status")
        self.assertEqual(loop.session_agents(self.project, "S1"), [])
        agents = agents_mod.agents(self.project)
        agents["a9"] = {"start": "2026-11-07T10:00:00", "type": "dev"}  # a subagent with no transcript still counts
        util.write_json(self.project.state / "agents.json", agents)
        self.assertEqual([h["id"] for h in loop.session_agents(self.project, "S1")], ["a9"])

    def pass_at(self, minutes_ago):
        beat = datetime.fromtimestamp(time.time() - minutes_ago * 60).strftime("%Y-%m-%dT%H:%M:%S")
        util.write_json(self.project.state / "heartbeats.json", {"loop": beat})
        return {"session": "S1", "since": datetime.fromtimestamp(time.time() - 7200).strftime("%Y-%m-%dT%H:%M:%S")}

    def test_a_delivery_after_the_last_pass_makes_the_pass_due(self):
        mark = self.pass_at(5)
        self.assertFalse(hooks_mod.pass_due(self.project, mark))
        self.bash('foreman --project fake progress --done "x"')
        self.assertTrue(hooks_mod.pass_due(self.project, mark))
        mark = self.pass_at(0)  # the pass that saw it
        self.assertFalse(hooks_mod.pass_due(self.project, mark))

    def test_a_subagents_delivery_does_not(self):
        mark = self.pass_at(5)
        util.write_json(
            self.project.state / "agents.json",
            {"a9": {"start": "2026-11-07T10:00:00", "delivered": time.strftime("%Y-%m-%dT%H:%M:%S")}},
        )
        self.assertFalse(hooks_mod.pass_due(self.project, mark))


class CostTest(SessionLifecycle):
    def test_the_delivery_costs_the_sessions_transcript(self):
        self.bash("git status")
        costs.record(self.project, SID, "s1", "ok")
        (run,) = costs.runs(self.project)
        self.assertEqual((run["agent"], run["issue"], run["sub"]), (SID, "AB-1", "T1"))
        self.assertGreater(run["tokens"], 0)
        self.assertIn("claude-sonnet-5-5", run["models"])
