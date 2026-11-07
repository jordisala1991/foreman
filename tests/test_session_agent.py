"""A session agent: an agent foreman launched as a Claude Code session of its own (`claude --bg`), whose hook events
carry its session_id and no agent_id. foreman knows it by its launch (kind "session") tied to that session_id, by
its agents.json entry (kind "session"), or by the launch token its session carries in FOREMAN_LAUNCH. The guard
treats its main thread as an agent (the agent-only rules, the git class of the reserved files, the hidden checks,
why=agent), guard.jsonl and approved.jsonl name it as an agent, `foreman progress` finds its name, and the `who`
notices leave it alone. A session foreman did not register stays the owner's session."""

import json
import os
from unittest import mock

from test_guard_main_checkout import MainCheckoutCase

from foreman import guard, hooks, identity, permissions, util

SA = "8e28877c-fe43-4d24-8cc9-849cbae066ce"  # the session agent's session id
OWNER = "0f0f0f0f-owner-session"  # a session of the owner foreman never registered
INTEGRATE = 'foreman integrate fake ab1/x "AB-1 T1"'


class SessionAgentCase(MainCheckoutCase):
    """MainCheckoutCase (a main checkout `top`, a worktree `wt`) with fake's hidden checks at their default place, the
    orchestrator marked as S1, and a session agent zed7 (foreman:dev) launched for AB-1 T1 and tied to SA."""

    def setUp(self):
        super().setUp()
        self.hold = self.user / ".local/share/foreman/fake/holdout"
        (self.hold / "AB-1").mkdir(parents=True)
        (self.hold / "AB-1/holdout.toml").write_text('issue = "AB-1"\n')
        self.mark("S1")
        self.token, _ = identity.new_launch(
            self.project, "zed7", "AB-1", "T1", agent_type="foreman:dev", kind=identity.SESSION_KIND
        )
        agents = {}
        self.assertTrue(identity.link(self.project, agents, SA, self.token))
        util.write_json(self.project.state / "agents.json", agents)

    def ctx(self, session=SA, **kw):
        return {"session": session, "agent": None, "cwd": str(self.wt), **kw}

    def why(self, cmd, **kw):
        """(kind, why) of a Bash command for a main thread of `session`; None when it passes."""
        v = guard.check(self.project, cmd, self.ctx(**kw))
        return None if v is None else (v[0], getattr(v[1], "why", None))


class GuardTest(SessionAgentCase):
    def test_row2_integrate_refused_why_agent_help_passes(self):
        self.assertEqual(self.why(INTEGRATE), ("orchestrator", "agent"))
        self.assertEqual(guard.check(self.project, INTEGRATE, self.ctx())[1], guard.INTEGRATE)
        self.assertIsNone(self.why("foreman integrate --help"))
        self.assertIsNone(self.why(f"{INTEGRATE} --check"))

    def test_row5_owner_session_is_unchanged(self):
        self.assertIsNone(self.why(INTEGRATE, session=OWNER))
        self.assertEqual(guard._who(self.project, self.ctx(session=OWNER), process=False), "session")
        self.assertEqual(guard._who(self.project, self.ctx(), process=False), "agent")
        self.assertTrue(guard.holdout_reader(self.project, self.ctx(session=OWNER)))

    def test_row3_main_checkout_holdout_and_git_files(self):
        reset = f"git -C {self.top} reset --hard"
        self.assertEqual(guard.check(self.project, reset, self.ctx())[1], guard.MAIN_CHECKOUT)
        self.assertIsNone(guard.check(self.project, reset, self.ctx(session=OWNER)))
        cat = f"cat {self.hold}/AB-1/holdout.toml"
        self.assertEqual(guard.check(self.project, cat, self.ctx())[1], guard.HOLDOUT)
        self.assertIsNone(guard.check(self.project, cat, self.ctx(session=OWNER)))
        hook = str(self.top / ".git/hooks/pre-commit")
        self.assertEqual(guard.check_file(self.project, hook, self.ctx())[1].kind, "git")
        self.assertIsNone(guard.check_file(self.project, hook, self.ctx(session=OWNER)))

    def test_row4_push_refused_why_agent(self):
        self.write_toml(
            self.toml + '\n[check]\nguard_table = "tests/guard.tsv"\n\n[deny.grants]\npush = ["git push"]\n'
        )
        for session, why in ((SA, "agent"), (OWNER, "session")):
            v = guard.check(self.project, "git push origin x", {"session": session, "agent": None})
            self.assertEqual((v[0], v[1].why), ("orchestrator", why))

    def test_a_planner_session_reaches_the_hidden_checks(self):
        token, _ = identity.new_launch(self.project, "pl1", agent_type="foreman:planner", kind=identity.SESSION_KIND)
        agents = util.read_json(self.project.state / "agents.json", {})
        identity.link(self.project, agents, "PLANNER-S", token)
        util.write_json(self.project.state / "agents.json", agents)
        self.assertTrue(guard.holdout_reader(self.project, self.ctx(session="PLANNER-S")))
        self.assertFalse(guard.holdout_reader(self.project, self.ctx()))

    def test_marking_itself_does_not_make_it_the_orchestrator(self):
        self.mark(SA)
        self.assertEqual(guard._who(self.project, self.ctx(), process=False), "agent")
        self.assertEqual(self.why(INTEGRATE), ("orchestrator", "agent"))

    def test_known_by_its_launch_alone_or_its_entry_alone(self):
        util.write_json(self.project.state / "agents.json", {})  # the launch with its session_id is enough
        self.assertEqual(self.why(INTEGRATE), ("orchestrator", "agent"))
        util.write_json(self.project.state / "launches.json", {})
        self.assertIsNone(self.why(INTEGRATE))  # nothing registers it any more: the owner's session
        util.write_json(
            self.project.state / "agents.json", {SA: {"session_agent": True, "name": "zed7", "type": "foreman:dev"}}
        )
        self.assertEqual(self.why(INTEGRATE), ("orchestrator", "agent"))
        # an entry or a launch of another kind with that key is not a session agent
        util.write_json(self.project.state / "agents.json", {SA: {"name": "zed7", "type": "foreman:dev"}})
        util.write_json(self.project.state / "launches.json", {"abcdefghij": {"name": "x", "session_id": SA}})
        self.assertIsNone(self.why(INTEGRATE))

    def test_the_launch_token_only_restricts(self):
        util.write_json(self.project.state / "launches.json", {})
        util.write_json(self.project.state / "agents.json", {})
        self.assertEqual(self.why(INTEGRATE, launch=self.token), ("orchestrator", "agent"))
        # a token foreman does not know is still an agent, of unknown type (no hidden checks)
        self.assertEqual(self.why(INTEGRATE, launch="zzzzzzzzzz"), ("orchestrator", "agent"))
        self.assertFalse(guard.holdout_reader(self.project, self.ctx(launch="zzzzzzzzzz")))
        # even in the marked orchestrator's session (a session it launched inherits it)
        self.assertEqual(self.why(INTEGRATE, session="S1", launch="zzzzzzzzzz"), ("orchestrator", "agent"))
        self.assertIsNone(self.why(INTEGRATE, session="S1"))

    def test_a_subagent_of_the_session_agent_stays_an_agent(self):
        self.assertEqual(self.why(INTEGRATE, agent="a1"), ("orchestrator", "agent"))

    def test_broken_state_files_never_raise(self):
        # on purpose: unreadable files read as "no session agent" (the owner's session), not as a refusal of every
        # main thread, the orchestrator's included; foreman writes both atomically (util.write_json), so only a hand
        # edit breaks them, and FOREMAN_LAUNCH, the lasting mark, does not depend on them
        (self.project.state / "launches.json").write_text("{not json")
        (self.project.state / "agents.json").write_text("[1, 2]")
        self.assertEqual(guard._who(self.project, self.ctx(), process=False), "session")

    def test_its_entry_stays_a_session_agent_after_a_delivery(self):
        """Review F1: the entry's mark is not the delivery's `kind`, which a delivery without --kind drops."""
        d = util.read_json(self.project.state / "agents.json", {})
        ev = {
            "hook_event_name": "PostToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": 'foreman progress --done "x"'},
            "tool_response": {"stdout": ""},
            "session_id": SA,
        }
        hooks.agent_event(self.project, d, SA, "PostToolUse", ev, "2026-11-07T13:00:00")
        util.write_json(self.project.state / "agents.json", d)
        util.write_json(self.project.state / "launches.json", {})
        self.assertIsNotNone(identity.session_agent(self.project.state, SA))
        self.assertEqual(self.why(INTEGRATE), ("orchestrator", "agent"))

    def test_the_session_id_expires_with_the_pruning_the_launch_token_does_not(self):
        """Review F2: an entry gone 24 h after its end takes its launch with it, so the session id alone stops
        marking the session; FOREMAN_LAUNCH, kept in the session's saved options, still does."""
        d = util.read_json(self.project.state / "agents.json", {})
        d[SA]["end"] = "2000-01-01T00:00:00"
        util.write_json(self.project.state / "agents.json", d)
        start = {"hook_event_name": "SubagentStart", "agent_id": "a9", "agent_type": "foreman:dev", "session_id": "S1"}
        self.assertEqual(self.hook("agents", start).returncode, 0)
        self.assertNotIn(SA, util.read_json(self.project.state / "agents.json", {}))
        identity.new_launch(self.project, "other")  # any new launch prunes launches.json
        self.assertNotIn(self.token, util.read_json(self.project.state / "launches.json", {}))
        self.assertEqual(guard._who(self.project, self.ctx(), process=False), "session")
        self.assertEqual(guard._who(self.project, self.ctx(launch=self.token), process=False), "agent")


class HookTest(SessionAgentCase):
    def event(self, cmd, session=SA, tid="T1"):
        return {
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": cmd},
            "session_id": session,
            "tool_use_id": tid,
            "cwd": str(self.home),
        }

    def guard_log(self):
        return [json.loads(x) for x in (self.project.state / "guard.jsonl").read_text().splitlines()]

    def test_plan_check_help_passes_integrate_refused_who_agent(self):
        r = self.hook("guard", self.event("foreman integrate --help"))
        self.assertEqual(r.returncode, 0, r.stderr)
        r = self.hook("guard", self.event("foreman integrate x", tid="T2"))
        self.assertEqual(r.returncode, 2)
        self.assertNotIn("foreman session ID", r.stderr)  # an agent is never told to mark itself
        last = self.guard_log()[-1]
        self.assertEqual((last["who"], last["kind"], last["rule"]), ("agent", "builtin", guard.INTEGRATE))
        # the owner's session: unchanged
        r = self.hook("guard", self.event("foreman integrate x", session=OWNER, tid="T3"))
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_refusals_name_the_agent(self):
        self.write_toml(
            self.toml + '\n[check]\nguard_table = "tests/guard.tsv"\n\n[deny.grants]\npush = ["git push"]\n'
        )
        ev = self.event("git push origin x")
        self.assertEqual(
            permissions.refusal(self.project, ev, guard.check(self.project, "git push", self.ctx()))["who"], "agent"
        )
        r = self.hook("guard", ev)
        self.assertEqual(r.returncode, 2)
        self.assertEqual(self.guard_log()[-1]["who"], "agent")
        line = json.loads((self.project.state / "approved.jsonl").read_text().splitlines()[-1])
        self.assertEqual((line["why"], line["agent_id"], line["agent"]), ("agent", SA, "zed7"))
        r = self.hook("guard", self.event("git push origin x", session=OWNER, tid="T4"))
        self.assertEqual(self.guard_log()[-1]["who"], "other")

    def test_the_hook_reads_the_launch_token_from_its_environment(self):
        util.write_json(self.project.state / "launches.json", {})
        util.write_json(self.project.state / "agents.json", {})
        with mock.patch.dict(os.environ, {identity.LAUNCH_ENV: self.token}):
            self.assertEqual(hooks._guard_ctx(self.event("ls"))["launch"], self.token)
            r = self.hook("guard", self.event("foreman integrate x", session=OWNER))
        self.assertEqual(r.returncode, 2)
        self.assertEqual(self.guard_log()[-1]["who"], "agent")
        self.assertIsNone(hooks._guard_ctx(self.event("ls"))["launch"])

    def test_who_leaves_a_session_agent_alone(self):
        start = {"hook_event_name": "SessionStart", "source": "startup", "session_id": SA}
        self.assertEqual(self.hook("who", start).stdout.strip(), "")
        self.assertEqual(hooks.who_text(self.project, SA), "")
        self.assertEqual(hooks.open_session_notice(self.project, SA), "")
        self.assertIn("fake-1", self.hook("who", {**start, "session_id": OWNER}).stdout)


class IdentityTest(SessionAgentCase):
    def test_launch_and_link_record_the_session(self):
        rec = identity.launches_file(self.project)
        launch = util.read_json(rec, {})[self.token]
        self.assertEqual((launch["kind"], launch["session_id"], launch["agent"]), ("session", SA, SA))
        entry = util.read_json(self.project.state / "agents.json", {})[SA]
        self.assertEqual(
            (entry["session_agent"], entry["name"], entry["issue"], entry["launch"]), (True, "zed7", "AB-1", self.token)
        )
        # a launch of a subagent records neither
        t2, rec2 = identity.new_launch(self.project, "kip3", agent_type="foreman:dev")
        self.assertNotIn("kind", rec2)
        self.assertNotIn("session_id", rec2)
        agents = {}
        identity.link(self.project, agents, "ag9", t2)
        self.assertNotIn("session_agent", agents["ag9"])
        self.assertNotIn("session_id", util.read_json(rec, {})[t2])
        # T2 may know the session id at launch
        _, rec3 = identity.new_launch(self.project, "lop1", kind=identity.SESSION_KIND, session_id="S9")
        self.assertEqual(rec3["session_id"], "S9")
        self.assertEqual(identity.session_agent(self.project.state, "S9")["name"], "lop1")

    def test_session_agent(self):
        state = self.project.state
        self.assertEqual(identity.session_agent(state, SA)["name"], "zed7")
        self.assertEqual(identity.session_agent(state, SA)["id"], SA)
        self.assertIsNone(identity.session_agent(state, OWNER))
        self.assertIsNone(identity.session_agent(state, None))
        self.assertEqual(identity.session_agent(state, OWNER, self.token)["name"], "zed7")
        self.assertEqual(identity.session_agent(state, OWNER, "zzzzzzzzzz"), {"id": OWNER})

    def calls(self, session, cmd, agent=None):
        util.write_json(
            identity.callers_file(),
            {
                "toolu_1": {
                    "at": __import__("time").time(),
                    "session": session,
                    "agent": agent,
                    "state": str(self.project.state),
                    "calls": identity.calls_of(cmd),
                }
            },
        )

    def test_recorded_caller_names_the_session_agent(self):
        self.calls(SA, 'foreman progress "x"')
        with mock.patch.dict(os.environ, {"CLAUDE_CODE_SESSION_ID": SA}):
            self.assertEqual(identity.recorded_caller("progress", [None, "progress", "x"]), (SA, "zed7"))
            self.assertEqual(identity.who("orchestrator", "progress", [None, "progress", "x"]), ("zed7", SA, False))
        # the owner's main thread: as before
        self.calls(OWNER, 'foreman progress "x"')
        with mock.patch.dict(os.environ, {"CLAUDE_CODE_SESSION_ID": OWNER}):
            self.assertEqual(identity.recorded_caller("progress", [None, "progress", "x"]), (None, None))
        # a session that carries the launch token: that agent, whatever its session id
        with mock.patch.dict(os.environ, {"CLAUDE_CODE_SESSION_ID": OWNER, identity.LAUNCH_ENV: self.token}):
            self.assertEqual(identity.recorded_caller("progress", [None, "progress", "x"]), (SA, "zed7"))
