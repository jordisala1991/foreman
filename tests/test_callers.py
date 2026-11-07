"""Who runs `foreman progress` / `foreman lock` without FOREMAN_WHO. The guard hook (PreToolUse on Bash)
writes the call down in <state root>/callers.json with its agent_id; the CLI matches its session id and its own
arguments; the PostToolUse of that Bash drops the entry. Fake events by stdin, fake agents.json, all in $TMPDIR."""

import json

from helpers import ProjectCase

from foreman import identity, lock, util

SESSION = "sess-0001"


class CallersTest(ProjectCase):
    def setUp(self):
        super().setUp()
        util.write_json(
            self.project.state / "agents.json",
            {
                "ag1": {"start": "2026-10-31T10:00:00", "type": "foreman:dev", "name": "zed7", "named_by": "launch"},
                "ag2": {"start": "2026-10-31T10:00:00", "type": "foreman:dev", "name": "kip3", "named_by": "launch"},
                "ag3": {"start": "2026-10-31T10:00:00", "type": "foreman:dev"},
            },
        )
        self.n = 0

    def pre(self, cmd, agent=None, session=SESSION, background=False):
        """The guard's PreToolUse of a Bash; returns its tool_use_id."""
        self.n += 1
        tid = f"toolu_{self.n}"
        ev = {
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_use_id": tid,
            "session_id": session,
            "tool_input": {"command": cmd, **({"run_in_background": True} if background else {})},
        }
        if agent:
            ev.update(agent_id=agent, agent_type="foreman:dev")
        r = self.hook("guard", ev)
        self.assertEqual(r.returncode, 0, r.stderr)
        return tid

    def post(self, tid, cmd, agent=None):
        ev = {
            "hook_event_name": "PostToolUse",
            "tool_name": "Bash",
            "tool_use_id": tid,
            "session_id": SESSION,
            "tool_input": {"command": cmd},
            "tool_response": {"stdout": ""},
        }
        if agent:
            ev.update(agent_id=agent, agent_type="foreman:dev")
        self.assertEqual(self.hook("agents", ev).returncode, 0)

    def callers(self):
        return util.read_json(self.tmp / "state" / "callers.json", {})

    def progress(self, *words, env=None):
        r = self.cli("progress", *words, env={"CLAUDE_CODE_SESSION_ID": SESSION, **(env or {})})
        self.assertEqual(r.returncode, 0, r.stderr)
        line = json.loads((self.project.state / "progress.jsonl").read_text().splitlines()[-1])
        return line, r.stderr

    def test_one_agent(self):
        cmd = 'foreman --project fake progress "x: 12 ms"'
        tid = self.pre(cmd, agent="ag1")
        e = self.callers()[tid]
        self.assertEqual((e["session"], e["agent"], e["calls"]), (SESSION, "ag1", [["fake", "progress", "x: 12 ms"]]))
        r = self.cli("--project", "fake", "progress", "x: 12 ms", env={"CLAUDE_CODE_SESSION_ID": SESSION})
        self.assertEqual(r.returncode, 0, r.stderr)
        line = json.loads((self.project.state / "progress.jsonl").read_text().splitlines()[-1])
        self.assertEqual((line["agent"], line["agent_id"]), ("zed7", "ag1"))
        self.assertEqual(r.stderr, "")
        self.post(tid, cmd, agent="ag1")
        self.assertEqual(self.callers(), {})

    def test_no_entry_for_other_commands(self):
        self.pre("ls -la && git status", agent="ag1")
        self.pre('foreman note "T" "body"', agent="ag1")
        self.assertFalse((self.tmp / "state" / "callers.json").exists())

    def test_two_agents_distinct_commands(self):
        self.pre('foreman progress "first"', agent="ag1")
        self.pre('foreman progress "second"', agent="ag2")
        line, err = self.progress("second")
        self.assertEqual((line["agent"], line["agent_id"], err), ("kip3", "ag2", ""))
        line, err = self.progress("first")
        self.assertEqual((line["agent"], line["agent_id"], err), ("zed7", "ag1", ""))

    def caller_of(self, *call, home="/home/me"):
        """identity.recorded_caller as the CLI of this session would ask it, with the given home."""
        import os
        from unittest import mock

        with mock.patch.dict(os.environ, {"CLAUDE_CODE_SESSION_ID": SESSION, "HOME": home}):
            return identity.recorded_caller("progress", [None, "progress", *call])

    def test_two_agents_deliver_with_shell_expanded_words(self):
        """Two agents of one session deliver at once; the shell expands `~` and `$VAR` before the CLI runs, so the
        words the guard wrote down are not the CLI's own: each must still be told apart."""
        self.pre("foreman progress --done --report ~/reviews/a.md A", agent="ag1")
        self.pre("foreman progress --done --report ~/reviews/b.md B", agent="ag2")
        self.assertEqual(self.caller_of("--done", "--report", "/home/me/reviews/b.md", "B"), ("ag2", "kip3"))
        self.assertEqual(self.caller_of("--done", "--report", "/home/me/reviews/a.md", "A"), ("ag1", "zed7"))
        # a variable the guard cannot resolve stands for any word, when the other call cannot be it
        self.pre('R=/x/r1.md; foreman progress --report "$R" A2', agent="ag1")
        self.pre("foreman progress --report /x/r2.md B2", agent="ag2")
        self.assertEqual(self.caller_of("--report", "/x/r1.md", "A2"), ("ag1", "zed7"))
        self.assertEqual(self.caller_of("--report", "/x/r2.md", "B2"), ("ag2", "kip3"))
        # two calls it cannot tell apart stay ambiguous
        self.pre('Q=/x/r4.md; foreman progress --report "$Q" A2', agent="ag2")
        self.assertEqual(self.caller_of("--report", "/x/r3.md", "A2")[0], "ambiguous")

    def test_ambiguous(self):
        self.pre('foreman progress "same"', agent="ag1")
        self.pre('foreman progress "same"', agent="ag2")
        line, err = self.progress("same")
        self.assertEqual(line["agent"], "orchestrator")
        self.assertNotIn("agent_id", line)
        self.assertIn("cannot tell which agent runs this (2 calls open in this session): set FOREMAN_WHO", err)
        # arguments that match no open call (a $(…) the guard could not read): still two candidates, no guess
        line, err = self.progress("other")
        self.assertEqual(line["agent"], "orchestrator")
        self.assertIn("cannot tell", err)

    def test_other_session_is_not_seen(self):
        self.pre('foreman progress "x"', agent="ag1", session="another")
        line, err = self.progress("x")
        self.assertEqual((line["agent"], err), ("orchestrator", ""))

    def test_explicit_wins_with_warning(self):
        self.pre('FOREMAN_WHO=other foreman progress "x"', agent="ag1")
        line, err = self.progress("x", env={"FOREMAN_WHO": "other"})
        self.assertEqual((line["agent"], line["agent_id"]), ("other", "ag1"))
        self.assertIn("FOREMAN_WHO=other is used, but foreman knows this caller as zed7", err)
        # the same name: no warning
        self.pre('FOREMAN_WHO=zed7 foreman progress "y"', agent="ag1")
        line, err = self.progress("y", env={"FOREMAN_WHO": "zed7"})
        self.assertEqual((line["agent"], err), ("zed7", ""))

    def test_orchestrator_itself(self):
        cmd = 'foreman progress "pass 12:20"'
        tid = self.pre(cmd)
        self.assertIsNone(self.callers()[tid]["agent"])
        line, err = self.progress("pass 12:20")
        self.assertEqual((line["agent"], err), ("orchestrator", ""))
        self.assertNotIn("agent_id", line)
        self.post(tid, cmd)
        self.assertEqual(self.callers(), {})

    def test_orchestrator_and_agent_at_once(self):
        self.pre('foreman progress "pass"')
        self.pre('foreman progress "result"', agent="ag1")
        self.assertEqual(self.progress("result")[0]["agent"], "zed7")
        line, err = self.progress("pass")
        self.assertEqual((line["agent"], err), ("orchestrator", ""))

    def test_nameless_agent(self):
        self.pre('foreman progress "x"', agent="ag3")
        self.assertEqual(self.progress("x")[0]["agent"], "agent-ag3")

    def test_background_stays_until_pruned(self):
        cmd = 'foreman progress "bg"'
        tid = self.pre(cmd, agent="ag1", background=True)
        self.post(tid, cmd, agent="ag1")
        self.assertIn(tid, self.callers())
        d = self.callers()
        d[tid]["at"] -= identity.CALLERS_S + 1
        util.write_json(self.tmp / "state" / "callers.json", d)
        self.pre('foreman progress "later"', agent="ag2")
        self.assertNotIn(tid, self.callers())

    def test_calls_of(self):
        self.assertEqual(
            identity.calls_of('cd /x && FOREMAN_WHO=a foreman lock gpu run "w x" foreman progress "y"'),
            [[None, "progress", "y"], [None, "lock", "gpu", "run", "w x", "foreman", "progress", "y"]],
        )
        self.assertEqual(identity.calls_of('bash -c "bin/foreman --project p progress hi"'), [["p", "progress", "hi"]])
        self.assertEqual(
            identity.calls_of('foreman time-limit 30 foreman lock gpu acquire "w" 5'),
            [[None, "lock", "gpu", "acquire", "w", "5"]],
        )
        self.assertEqual(identity.calls_of("foreman backlog list"), [])

    def test_lock_acquire_and_run(self):
        cmd = 'foreman lock widget acquire "hold" 5'
        self.pre(cmd, agent="ag1")
        r = self.cli("lock", "widget", "acquire", "hold", "5", env={"CLAUDE_CODE_SESSION_ID": SESSION})
        self.assertIn("LOCKED", r.stdout, r.stderr)
        self.assertEqual([h["who"] for h in lock.status("widget")["holders"]], ["zed7"])
        self.pre("foreman lock widget release", agent="ag1")
        r = self.cli("lock", "widget", "release", env={"CLAUDE_CODE_SESSION_ID": SESSION})
        self.assertIn("RELEASED", r.stdout, r.stdout + r.stderr)

    def test_lock_run_passes_name_to_children(self):
        """Inside `lock … run` the children get the holder's name (FOREMAN_LOCK_WHO): their progress is the
        agent's, even in another session (a script, a long run)."""
        (self.tmp / "bin").mkdir()
        shim = self.tmp / "bin" / "foreman"
        import sys

        from helpers import ENTRY

        shim.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{ENTRY}" "$@"\n')
        shim.chmod(0o755)
        self.pre(
            'foreman lock widget run "t" bash -c "CLAUDE_CODE_SESSION_ID=elsewhere foreman progress inside"',
            agent="ag2",
        )
        path = f"{self.tmp / 'bin'}:{self.env['PATH']}"
        r = self.cli(
            "lock",
            "widget",
            "run",
            "t",
            "bash",
            "-c",
            "CLAUDE_CODE_SESSION_ID=elsewhere foreman progress inside",
            env={"CLAUDE_CODE_SESSION_ID": SESSION, "PATH": path},
        )
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        line = json.loads((self.project.state / "progress.jsonl").read_text().splitlines()[-1])
        self.assertEqual(line["agent"], "kip3")
        log = [json.loads(x) for x in lock.paths("widget")["log"].read_text().splitlines()]
        self.assertEqual(log[-1]["who"], "kip3")
        # a lock whose own name is only the default does not hand «agent» to its children as a name
        r = self.cli("lock", "widget", "run", "t", "foreman", "progress", "plain", env={"PATH": path})
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        line = json.loads((self.project.state / "progress.jsonl").read_text().splitlines()[-1])
        self.assertEqual(line["agent"], "orchestrator")

    def test_without_session_nothing_is_looked_up(self):
        self.pre('foreman progress "x"', agent="ag1")
        r = self.cli("progress", "x")
        line = json.loads((self.project.state / "progress.jsonl").read_text().splitlines()[-1])
        self.assertEqual((line["agent"], r.stderr), ("orchestrator", ""))
