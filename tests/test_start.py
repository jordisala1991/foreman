"""`foreman start`, the short summary a new orchestrator session starts from (lib/foreman/start.py)."""

import json
import os
import time
import unittest
from datetime import date, datetime, timedelta

from helpers import BASE_TOML, ProjectCase

from foreman import backlog, heartbeat, notes, orchestrator, start, util

# a handover note as foreman wrote them before 0.44: still in old notes, `start` no longer shows it
HANDOVER_BODY = (
    "Outgoing session: orq-old (session sess-old, since 2020-01-01T08:00:00); "
    """context 300000 tokens (300K).

### Uncommitted changes
- none

### Live agents
- ag1 (dev), since -, last tool - at -; transcript -

### Locks
- none

### Board
OLD BOARD LINE

### For the incoming session
1. OLD STEP LINE
"""
)


class StartCase(ProjectCase):
    toml = BASE_TOML.replace('criterion_required = ["PF-"]', "criterion_required = []")

    def setUp(self):
        super().setUp()
        self.projects = self.tmp / "claude-projects"
        self.projects.mkdir()
        self.today = date.today()

    def heartbeat(self, sid, ago=0, cwd=None):
        """The heartbeat foreman's hooks module writes for session `sid`, its last tick `ago` seconds ago."""
        at = (datetime.now() - timedelta(seconds=ago)).isoformat(timespec="seconds")
        f = heartbeat.beat_file(self.project, sid)
        f.parent.mkdir(parents=True, exist_ok=True)
        util.write_json(f, {"at": at, "started": at, "busy": False, "cwd": str(cwd or self.home)})

    def write_notes(self, day, text):
        self.project.notes_dir.mkdir(parents=True, exist_ok=True)
        (self.project.notes_dir / f"{day:%Y-%m-%d}.md").write_text(text)

    def world(self):
        """An old handover note in today's notes with earlier notes before it (and an older one the day before), the
        marked session dead, the product paused, two live agents (one of the dead session, one of a live one), a board,
        an active backlog with closed and triage issues, a decision and an owner's message."""
        yesterday = self.today - timedelta(days=1)
        self.write_notes(
            yesterday,
            "# Notes\n\n## 09:00 Handover (session older)\nOutgoing session: older (session "
            "sess-older, since x)\n\n### Board\nVERY OLD\n",
        )
        self.write_notes(
            self.today,
            "# Notes\n\n## 08:00 Earlier note\nEARLIER BODY\n\n## 09:30 Integrated x\n"
            "ANOTHER EARLIER BODY\n\n## 10:00 Handover (session orq-old)\n"
            + HANDOVER_BODY
            + "\n## 10:05 Relief: orq-new\nAFTER BODY\n\n## 10:20 Integrated y\nmore\n",
        )
        st = self.project.state
        st.mkdir(parents=True, exist_ok=True)
        orchestrator.mark(self.project, "sess-dead", "orq-new")
        # marked after the last note of the day (10:20), whatever the hour the test runs at
        rec = util.read_json(st / "orchestrator.json", {})
        util.write_json(st / "orchestrator.json", dict(rec, since=f"{self.today:%Y-%m-%d}T11:00:00"))
        self.heartbeat("sess-dead", ago=600)  # its ticks stopped ten minutes ago
        self.heartbeat("sess-live")
        (st / "pause").write_text("paused at 21:07\n")
        (st / "board.md").write_text("# Board\n- BOARD LINE ONE\n- BOARD LINE TWO\n")
        sub = lambda sid, aid: str(self.projects / "-home" / sid / "subagents" / f"agent-{aid}.jsonl")  # noqa: E731
        now = datetime.now().isoformat(timespec="seconds")
        (st / "agents.json").write_text(
            json.dumps(
                {
                    "a1": {
                        "name": "ag1",
                        "type": "dev",
                        "issue": "PF-1",
                        "sub": "T2",
                        "branch": "pf1/t2",
                        "start": now,
                        "transcript": sub("sess-dead", "a1"),
                    },
                    "a2": {"name": "ag2", "type": "planner", "start": now, "transcript": sub("sess-live", "a2")},
                    "a3": {
                        "name": "ag3",
                        "type": "dev",
                        "start": now,
                        "delivered": True,
                        "transcript": sub("sess-live", "a3"),
                    },
                }
            )
        )
        b = backlog.Backlog(self.project)
        b.init()
        with b.mutate() as data:
            b.add_project(data, "A1", "First", "intro")
            b.add_issue(data, "A1", "PF-1", "Started work", status="in progress", plan=["reviews/plan-PF-1.md"])
            b.set_sub(data, "PF-1", "T1", title="done part", status="done")
            b.set_sub(data, "PF-1", "T2", title="the part now", status="in progress", agent="ag1", branch="pf1/t2")
            b.set_sub(data, "PF-1", "T3", title="next part")
            b.add_issue(data, "A1", "PF-2", "Closed long ago")
            b.set_status(data, "PF-2", "done", result="shipped")
            b.add_issue(data, "A1", "PF-3", "Waiting work", status="backlog", blocked="the owner decides")
            b.add_issue(data, "A1", "PF-4", "An idea", status="triage")
            b.add_issue(data, "A1", "PF-5", "Next up", status="ready")
            b.add_decision(
                data,
                None,
                "Which color for the button?",
                "Blue or green.",
                ["blue", "green"],
                "blue",
                issue="PF-3",
                as_is=True,
            )
        from foreman import notes

        notes.inbox_add(self.project, "please look at the panel", sender="owner")


class OwnSessionTest(StartCase):
    def test_from_the_environment(self):
        me = start.own_session(self.project, {"CLAUDE_CODE_SESSION_ID": "sess-env"}, cwd=self.home)
        self.assertEqual((me["session"], me["how"]), ("sess-env", "how_env"))

    def test_from_the_live_sessions_of_this_folder_or_a_parent(self):
        self.heartbeat("sess-dead-here", ago=600, cwd=self.tmp)  # gone: not taken
        self.heartbeat("sess-old-here", ago=50, cwd=self.tmp)
        self.heartbeat("sess-new-here", ago=5, cwd=self.tmp)
        self.heartbeat("sess-other", cwd=self.tmp / "other")
        orchestrator.mark(self.project, "sess-new-here", "newest")
        me = start.own_session(self.project, {}, cwd=self.home)
        self.assertEqual(
            (me["session"], me["name"], me["how"], me["files"]), ("sess-new-here", "newest", "how_beat", 2)
        )
        d = start.collect(self.project, env={}, cwd=self.home)
        self.assertIn(
            "found as the newest of the 2 live sessions of this folder (heartbeats from foreman's hooks module): "
            "check it is this one",
            start.render(self.project, d),
        )
        self.assertEqual(start.own_session(self.project, {}, cwd=self.tmp / "other")["session"], "sess-other")

    def test_from_the_newest_transcript_of_this_folder(self):
        d = self.projects / start.folder_key(self.home)
        d.mkdir(parents=True)
        (d / "sess-a.jsonl").write_text("{}\n")
        (d / "sess-b.jsonl").write_text("{}\n")
        os.utime(d / "sess-a.jsonl", (time.time() - 100, time.time() - 100))
        me = start.own_session(self.project, {}, cwd=self.home)
        self.assertEqual((me["session"], me["how"]), ("sess-b", "how_transcript"))
        self.assertEqual(me["transcript"], str(d / "sess-b.jsonl"))

    def test_not_found_says_so(self):
        me = start.own_session(self.project, {}, cwd=self.home)
        self.assertEqual((me["session"], me["how"]), (None, "how_none"))
        text = start.render(self.project, start.collect(self.project, env={}, cwd=self.home))
        self.assertIn("This session: not found (no session id in the environment", text)
        self.assertIn("session <session id> --name <your name, as ListAgents shows it>", text)


class SummaryTest(StartCase):
    def summary(self, env=None):
        d = start.collect(self.project, env=env or {"CLAUDE_CODE_SESSION_ID": "sess-me"}, cwd=self.home)
        return d, start.render(self.project, d)

    def test_the_whole_summary_of_a_relief(self):
        self.world()
        d, text = self.summary()
        # who: this session, the marked one (dead), the steps with the exact lines
        self.assertIn("This session: sess-me (name ?; found in $CLAUDE_CODE_SESSION_ID)", text)
        self.assertIn("Marked now: orq-new (session sess-dead, since ", text)
        self.assertIn("): NOT ALIVE", text)
        self.assertIn(
            "1. Mark this session: `foreman --project fake session sess-me --name <your name, as ListAgents "
            "shows it>` (it will say it relieves orq-new).",
            text,
        )
        self.assertIn(
            "2. The loop needs nothing from you: once this session is marked, foreman's hooks module sends it the "
            "pass, a prompt starting with «Fake orchestrator loop», every 60 min (the first within a minute).",
            text,
        )
        self.assertEqual(d["loop"], {"prompt": "Fake orchestrator loop", "every_min": 60})
        self.assertIn("3. orq-new is not alive: nobody to tell.", text)
        self.assertIn("4. Tell Owner in plain words (read `foreman docs owner` first)", text)
        self.assertIn("PAUSED since 21:07", text)
        # no handover section: the old handover notes are not read
        self.assertNotIn("handover", text.lower())
        for gone in ("Outgoing session", "EARLIER BODY", "VERY OLD", "OLD BOARD LINE", "OLD STEP LINE", "AFTER BODY"):
            self.assertNotIn(gone, text)
        # the board, the active backlog, what waits for the owner
        self.assertIn("- BOARD LINE ONE\n- BOARD LINE TWO", text)
        self.assertIn("## Active backlog (3 issues;", text)
        self.assertIn(
            "- PF-1 in progress 1/3: Started work\n    T2 in progress (ag1, pf1/t2): the part now\n"
            "    T3 todo: next part\n    plan: reviews/plan-PF-1.md",
            text,
        )
        self.assertIn("- PF-3 backlog: Waiting work [blocked: the owner decides]", text)
        self.assertLess(text.index("PF-1 in progress"), text.index("PF-5 ready"))
        self.assertLess(text.index("PF-5 ready"), text.index("PF-3 backlog"))
        self.assertNotIn("Closed long ago", text)
        self.assertIn("triage: 1 (PF-4)", text)
        self.assertIn("- decision PF-3 J1 (", text)
        self.assertIn("Which color for the button?", text)
        self.assertIn("please look at the panel", text)
        # resources, agents: live, dead, delivered left out
        self.assertIn("- widget: free", text)
        self.assertIn("- ag1 (dev, PF-1 T2, pf1/t2): DEAD (its session is gone)", text)
        self.assertIn("- ag2 (planner): live", text)
        self.assertNotIn("ag3", text)
        # rules and what to read next
        self.assertIn(f"Rules: {self.home / 'rules.md'} is missing", text)
        self.assertIn("- `foreman docs owner`: before your first message to Owner.", text)
        self.assertIn("- the plan of PF-1: reviews/plan-PF-1.md", text)
        self.assertIn("lines): look things up with grep, never whole.", text)
        # the JSON has the same fields
        self.assertEqual(d["marked"]["verdict"], "gone")
        self.assertNotIn("handover", d)
        self.assertEqual([a["session_state"] for a in d["agents"]], ["gone", "alive"])
        self.assertEqual(d["paused"], "21:07")
        r = self.cli("start", "--json", env={"CLAUDE_CODE_SESSION_ID": "sess-me"})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(set(json.loads(r.stdout)), set(d))
        r = self.cli("start", env={"CLAUDE_CODE_SESSION_ID": "sess-me"})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("1. Mark this session: `foreman --project fake session sess-me", r.stdout)

    def test_what_waits_for_a_decision(self):
        """The backlog says what of an issue waits for which pending decision, and the decision what it
        holds; an answer in the panel resolves it, which frees it."""
        self.world()
        b = backlog.Backlog(self.project)
        with b.mutate() as data:
            b.set_decision_blocks(data, "PF-3 J1", ["PF-1 T3", "PF-5"])
        d, text = self.summary()
        self.assertIn("- PF-5 ready: Next up [blocked: waits for decision PF-3 J1]", text)
        self.assertIn("- PF-1 in progress 1/3: Started work [blocked: T3 waits for decision PF-3 J1]", text)
        self.assertIn("Which color for the button? [holds: PF-1 T3, PF-5]", text)
        self.assertEqual(d["backlog"]["decisions"][0]["blocks"], ["PF-1 T3", "PF-5"])
        notes.inbox_add(self.project, "blue", sender="owner", decision="Which color for the button?")
        _, text = self.summary()
        self.assertIn("- PF-5 ready: Next up [blocked: waits for decision PF-3 J1]", text)  # a message frees nothing
        opt = b.load()["decisions"][0]["options"][0]
        with b.mutate() as data:
            b.answer_decision(data, "PF-3 J1", opt, "Owner, 2026-11-02 10:00 (panel)")
        d, text = self.summary()
        self.assertIn("- PF-5 ready: Next up\n", text)
        self.assertNotIn("[holds: PF-1 T3, PF-5]", text)  # resolved: no longer waits for the owner
        self.assertEqual(d["backlog"]["decisions"], [])

    def test_a_live_marked_session_is_told(self):
        self.world()
        orchestrator.mark(self.project, "sess-live", "orq-me")
        _, text = self.summary({"CLAUDE_CODE_SESSION_ID": "sess-new"})
        self.assertIn("Marked now: orq-me (session sess-live, since ", text)
        self.assertIn("): alive", text)
        self.assertIn("3. Tell orq-me (SendMessage) that you are marked: its passes stop and it closes.", text)
        self.assertIn("- ag2 (planner): live", text)

    def test_already_marked(self):
        self.world()
        orchestrator.mark(self.project, "sess-me", "orq-me")
        _, text = self.summary()
        self.assertIn("Marked now: this session, since ", text)
        self.assertIn("1. Already marked as the orchestrator.\n2. The loop needs nothing from you", text)
        self.assertNotIn("Tell Owner", text)
        self.assertNotIn("older than the current mark", text)

    def test_a_fresh_project(self):
        _, text = self.summary()
        self.assertIn("Marked now: nobody", text)
        self.assertIn("(no board yet", text)
        self.assertIn("nothing active", text)
        self.assertIn("none live", text)
        self.assertNotIn("PAUSED", text)
        self.assertNotIn("today's notes", text)

    def test_rules_sections_and_spanish(self):
        (self.home / "rules.md").write_text("# Rules\n\n## 1. Where\nx\n\n## 2. Never\ny\n")
        self.write_toml(open(self.home / "foreman.toml").read().replace('language = "en"', 'language = "es"'))
        _, text = self.summary()
        self.assertIn("Arranque de una sesión de orquestador: fake", text)
        self.assertIn("Secciones: 1. Where · 2. Never", text)
        self.assertIn("## Pasos", text)

    def test_strings_are_in_both_languages(self):
        from foreman import i18n

        self.assertEqual(set(i18n.strings("en")["start"]), set(i18n.strings("es")["start"]))


if __name__ == "__main__":
    unittest.main()
