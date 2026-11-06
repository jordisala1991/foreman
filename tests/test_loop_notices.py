"""The loop pass as structured notices ({kind, actionable, text, key}), the module's tick that runs it (a pass
submitted to the model, or an idle one left as one line on screen) and the notices between passes.

Every assertion looks at `kind` and `actionable`, never at the wording. Each kind is provoked on a fake project and
checked both ways (actionable and not, or present and absent for the kinds that always ask for action). The real
machine never decides a test: the hung-shells script is replaced (FOREMAN_HUNG_SHELLS_SH) and the disk threshold is 0
except in the disk test.
"""

import contextlib
import json
import os
import re
import signal
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import ClassVar

from helpers import ENTRY, ProjectCase, git, make_repo

from foreman import backlog as backlog_mod
from foreman import config, delivery, heartbeat, hooks_mod, identity, lock, loop, notes, notices, orchestrator, util
from foreman.backlog import Backlog

TOML = """
[project]
name = "fake"
owner = "Owner"

[paths]
worktrees = "{tmp}/wt"

[agents]
production_slots = 1
ready_target = 1

[loop]
prompt = "Fake orchestrator loop"
{loop_extra}

[resources.widget]
guard = ["make up"]

[repos.app]
path = "{tmp}/app"
remote = "none"
{extra}
"""

PASS = {"hook_event_name": "UserPromptSubmit", "session_id": "S1", "prompt": "Fake orchestrator loop\n\nLoop pass"}


class NoticesCase(ProjectCase):
    """A fake project with a backlog, no hung shells and no disk threshold."""

    toml = TOML.replace("{loop_extra}", "disk_low_gb = 0").replace("{extra}", "")

    def setUp(self):
        super().setUp()
        self.saved.setdefault("FOREMAN_HUNG_SHELLS_SH", os.environ.get("FOREMAN_HUNG_SHELLS_SH"))
        self.quiet_sh = self.tmp / "no_hung.sh"
        self.quiet_sh.write_text("exit 0\n")
        os.environ["FOREMAN_HUNG_SHELLS_SH"] = str(self.quiet_sh)
        make_repo(self.tmp / "app")
        self.b = Backlog(self.project)
        self.b.init()
        with self.b.mutate() as d:
            self.b.add_project(d, "E1", "One", "intro")

    # ---------- helpers ----------

    def config(self, loop_extra="", extra=""):
        if "disk_low_gb" not in loop_extra:
            loop_extra += "\ndisk_low_gb = 0"
        self.write_toml(TOML.replace("{loop_extra}", loop_extra).replace("{extra}", extra))

    def notices(self, session=None, mark=True):
        return loop.notices_for_pass(self.project, session, mark)

    def kinds(self, session=None, mark=True):
        """{kind: [actionable, …]} of a pass."""
        out = {}
        for n in self.notices(session, mark):
            self.assertEqual(set(n), {"kind", "actionable", "text", "key"})
            self.assertIsInstance(n["actionable"], bool)
            out.setdefault(n["kind"], []).append(n["actionable"])
        return out

    def issue(self, iid, status, blocked="", **sub):
        with self.b.mutate() as d:
            self.b.add_issue(d, "E1", iid, "an issue", status=status, blocked=blocked)
            if sub:
                self.b.set_sub(d, iid, "T1", status="in progress", **sub)

    def agent(self, aid, name=None, typ="dev", session="S1", minutes_ago=0, text=""):
        t = self.tmp / session / "subagents" / f"agent-{aid}.jsonl"  # where Claude Code keeps it
        t.parent.mkdir(parents=True, exist_ok=True)
        t.write_text(json.dumps({"text": text}) + "\n")
        when = (datetime.now() - timedelta(minutes=minutes_ago)).isoformat(timespec="seconds")
        os.utime(t, (time.time() - 60 * minutes_ago,) * 2)  # its transcript as quiet as its activity
        f = loop.agents_file(self.project)
        d = util.read_json(f, {})
        d[aid] = {
            "start": when,
            "activity": when,
            "type": typ,
            "transcript": str(t),
            **({"name": name} if name else {}),
        }
        f.parent.mkdir(parents=True, exist_ok=True)
        util.write_json(f, d)

    def lock_run(self, who, what="job"):
        """A holder or waiter of `widget` through `run`, alive until the test ends: its own process group, which the
        cleanup stops whole (lock.sh and the command it runs), so no fixed time decides how long it holds."""
        e = self.env
        e["FOREMAN_LOCK_WHO"] = who
        p = subprocess.Popen(
            [sys.executable, str(ENTRY), "lock", "widget", "run", what, "sleep", "600"],
            cwd=self.home,
            env=e,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )

        def stop():
            with contextlib.suppress(ProcessLookupError):
                os.killpg(p.pid, signal.SIGKILL)
            p.wait(30)

        self.addCleanup(stop)
        return p

    def wait_lock(self, cond, timeout=60):
        """The status of `widget` once `cond(status)` holds. The conditions name the state the test needs (who
        holds it, whole; who is queued), never "something is there": lock.sh takes the lock in steps."""
        end = time.monotonic() + timeout
        while True:
            st = lock.status("widget")
            if cond(st):
                return st
            if time.monotonic() > end:
                self.fail(f"lock never reached the state: {st}")
            time.sleep(0.02)

    def holding(self, who):
        """Waits until `who` holds `widget` through a live `run`: its owner line written, with its pid, and its ticket
        gone (lock.status already leaves it out; a test that rewrites the owner line would bring it back)."""
        queue = lock.paths("widget")["queue"]

        def done(st):
            h = [h for h in st["holders"] if h["who"] == who and h["alive"]]
            return h and not any(f.endswith(f"-{int(h[0]['pid']):07d}") for f in os.listdir(queue))

        return self.wait_lock(done)

    def queued(self, who):
        """Waits until `who` is in the queue of `widget` (its ticket written)."""
        return self.wait_lock(lambda st: who in [q["who"] for q in st["queue"]])

    def tick(self, session="S1", **extra):
        """What `foreman hook mod tick` answers the module for `session` (plus `extra` fields of its input), without
        the band (BandTest's business): a marked session's answer must carry it, any other must not."""
        r = self.cli("hook", "mod", "tick", inp=json.dumps({"cwd": str(self.home), "session": session, **extra}))
        self.assertEqual(r.returncode, 0, r.stderr)
        out = json.loads(r.stdout)
        band = out.pop("band", None)
        if out.get("marked"):
            self.assertTrue(isinstance(band, dict) and len(band["rows"]) == 2, band)
        else:
            self.assertIsNone(band)
        return out

    def old_beat(self):
        f = self.project.state / "heartbeats.json"
        f.parent.mkdir(parents=True, exist_ok=True)
        util.write_json(f, {"loop": "2020-01-01T00:00:00"})

    def assert_idle(self):
        """A due pass with nothing to act on: run, but left as one line for the screen (the module's $.ui.log)."""
        self.old_beat()
        out = self.tick()
        self.assertEqual(set(out), {"marked", "due", "grants", "log"}, out)
        self.assertNotEqual(notes.heartbeats(self.project)["loop"], "2020-01-01T00:00:00")
        return out["log"]

    def assert_submitted(self, kind):
        """A due pass with `kind` in it and something to act on: the prompt the module submits ([loop] prompt, a
        blank line, the summary)."""
        self.old_beat()
        out = self.tick()
        self.assertEqual(set(out), {"marked", "due", "grants", "submit"}, out)
        self.assertTrue(out["submit"].startswith("Fake orchestrator loop\n\nLoop pass "), out)
        self.assertNotEqual(notes.heartbeats(self.project)["loop"], "2020-01-01T00:00:00")
        ns = loop.notices_for_pass(self.project, "S1", mark=False)
        self.assertIn(kind, [n["kind"] for n in ns])
        return out["submit"]


class LoopNoticesTest(NoticesCase):
    # ---------- each kind, both ways ----------

    def test_empty_product_is_idle(self):
        k = self.kinds("S1")
        self.assertEqual(k, {"wip_production": [False], "wip_ready": [False]})
        self.assertTrue(notices.idle(self.notices("S1")))

    def test_paused(self):
        self.assertNotIn("paused", self.kinds())
        self.project.state.mkdir(parents=True, exist_ok=True)
        (self.project.state / "pause").write_text("paused at 21:07\n")
        k = self.kinds()
        self.assertEqual(k["paused"], [False])
        self.assertNotIn("wip_production", k)

    def test_config_warnings_once_per_change(self):
        self.config(extra="futuro = 1")
        self.assertEqual(self.kinds()["config_warnings"], [True])
        self.assertEqual(self.kinds(mark=False)["config_warnings"], [False])
        self.assertEqual(self.kinds()["config_warnings"], [False])
        self.config(extra="otro = 2")
        self.assertEqual(self.kinds()["config_warnings"], [True])
        self.config()
        self.assertNotIn("config_warnings", self.kinds())
        self.config(extra="otro = 2")  # gone and back: announced again
        self.assertEqual(self.kinds()["config_warnings"], [True])

    def test_wip_production(self):
        self.issue("AB-1", "ready", blocked="waits for AB-9")
        self.assertEqual(self.kinds()["wip_production"], [False])  # blocked: nothing to launch
        self.issue("AB-2", "ready")
        self.assertEqual(self.kinds()["wip_production"], [True])
        self.agent("a1", "zed7", typ="dev")  # production 1/1
        self.assertNotIn("wip_production", self.kinds())

    def test_wip_counts_relation_blocks(self):
        self.issue("AB-1", "in progress")
        self.issue("AB-2", "ready")
        self.issue("AB-3", "backlog")
        with self.b.mutate() as d:
            self.b.relate(d, "AB-1", "blocks", "AB-2")
            self.b.relate(d, "AB-1", "blocks", "AB-3")
        k = self.kinds()
        self.assertEqual((k["wip_production"], k["wip_ready"]), ([False], [False]))  # nothing free to launch or plan
        with self.b.mutate() as d:
            self.b.set_status(d, "AB-1", "done", result="r")
        k = self.kinds()
        self.assertEqual(k["wip_production"], [True])  # released by the close: AB-2 can be launched
        self.assertNotIn("wip_ready", k)  # and counts as unblocked ready (1/1)

    # ---------- what pending decisions hold (`blocks` of a decision) ----------

    def decide(self, iid, blocks, title=None):
        title = title or f"Which way for {iid}?"
        with self.b.mutate() as d:
            return self.b.add_decision(d, None, title, "why", ["a", "b"], "a", issue=iid, as_is=True, blocks=blocks)

    def wip(self, kind="wip_production", **kw):
        (n,) = [n for n in loop.pipeline(self.project, **kw) if n["kind"] == kind]
        return n

    def test_wip_production_skips_an_issue_whose_subs_wait_for_a_decision(self):
        self.issue("AB-1", "ready")
        with self.b.mutate() as d:
            self.b.set_sub(d, "AB-1", "T3", title="the part that waits")
        did = self.decide("AB-1", ["T3"])
        n = self.wip()
        self.assertFalse(n["actionable"])  # base: ready 1, actionable, «launch the next `ready` issue.»
        self.assertEqual(
            n["text"],
            "WIP (pipeline not full): production 0/1: no unblocked `ready` issue to launch "
            "(AB-1 T3 waits for decision AB-1 J1).",
        )
        self.assertEqual(n["key"], "wip_production:0/1:ready0:held1")
        self.assertIn("unblocked ready 0/1", self.wip("wip_ready")["text"])
        # a free sub-issue to do: it counts again, and the text still says what waits
        with self.b.mutate() as d:
            self.b.set_sub(d, "AB-1", "T4", title="a free part")
        n = self.wip()
        self.assertTrue(n["actionable"])
        self.assertEqual(n["text"], "WIP (pipeline not full): production 0/1: launch the next `ready` issue.")
        self.assertEqual(n["key"], "wip_production:0/1:ready1")
        # held whole: nothing of it is launchable, whatever its sub-issues
        with self.b.mutate() as d:
            self.b.set_decision_blocks(d, did, ["AB-1"])
        n = self.wip()
        self.assertFalse(n["actionable"])
        self.assertTrue(n["text"].endswith("to launch (AB-1 waits for decision AB-1 J1)."), n["text"])
        # resolved: freed
        with self.b.mutate() as d:
            self.b.resolve_decision(d, did, "a")
        self.assertTrue(self.wip()["actionable"])

    def test_wip_production_names_held_work_next_to_a_launchable_issue(self):
        self.issue("AB-1", "ready")
        self.issue("AB-2", "ready")
        with self.b.mutate() as d:
            for t in ("T1", "T2"):
                self.b.set_sub(d, "AB-2", t, title="part")
        self.decide("AB-2", ["T1"])
        self.decide("AB-2", ["T2", "T1"])
        n = self.wip()
        self.assertTrue(n["actionable"])
        self.assertEqual(
            n["text"],
            "WIP (pipeline not full): production 0/1: launch the next `ready` issue "
            "(AB-2 T1 waits for decision AB-2 J1, AB-2 J2; AB-2 T2 waits for decision AB-2 J2).",
        )
        self.assertEqual(n["key"], "wip_production:0/1:ready1:held2")
        self.assertEqual(self.wip(rotating="context_rotate")["text"].count("waits"), 0)  # nothing to launch anyway

    def test_wip_production_names_at_most_three_waits(self):
        for k in range(1, 5):
            self.issue(f"AB-{k}", "ready")
            self.decide(f"AB-{k}", ["issue"])
        n = self.wip()
        self.assertTrue(n["text"].endswith("AB-3 waits for decision AB-3 J1; …)."), n["text"])
        self.assertNotIn("AB-4 waits", n["text"])
        self.assertIn(":ready0:held4", n["key"])

    def test_held_and_blocked_is_said_as_blocked(self):
        self.issue("AB-1", "ready", blocked="waits for the vendor")
        self.decide("AB-1", ["issue"])
        n = self.wip()
        self.assertEqual(
            n["text"], "WIP (pipeline not full): production 0/1: no unblocked `ready` issue to launch."
        )  # its blocked text says why; the hold is not the only reason
        self.assertEqual(n["key"], "wip_production:0/1:ready0")

    def test_a_decision_answered_in_the_panel_holds_nothing(self):
        self.issue("AB-1", "ready")
        did = self.decide("AB-1", ["issue"], title="Which way?")
        self.assertFalse(self.wip()["actionable"])
        notes.inbox_add(self.project, "a", sender="owner", decision="Which way?")
        self.assertFalse(self.wip()["actionable"])  # an unread message alone resolves nothing
        with self.b.mutate() as d:
            self.b.answer_decision(d, did, "a", "Owner, 2026-11-02 10:00 (panel)")
        n = self.wip()
        self.assertTrue(n["actionable"])  # answered in the panel: resolved
        self.assertNotIn("waits", n["text"])
        notes.inbox_read(self.project)
        self.assertTrue(self.wip()["actionable"])  # and it stays so once the orchestrator reads it

    def test_wip_ready(self):
        self.issue("AB-1", "triage")
        self.assertEqual(self.kinds()["wip_ready"], [False])  # only triage, on purpose
        self.issue("AB-2", "backlog", blocked="waits for AB-1")
        self.assertEqual(self.kinds()["wip_ready"], [False])
        self.issue("AB-3", "refining")
        self.assertEqual(self.kinds()["wip_ready"], [True])
        self.agent("p1", "plan1", typ="foreman:planner")
        self.assertEqual(self.kinds()["wip_ready"], [False])  # a planner is already on it
        self.issue("AB-4", "ready")
        self.assertNotIn("wip_ready", self.kinds())

    def test_reviewer_is_not_a_planner(self):
        """A live reviewer agent is role «other»: the pass keeps asking for plans while it runs."""
        self.assertEqual(identity.role(self.project, "foreman:reviewer"), "other")
        self.assertEqual(identity.role(self.project, "reviewer"), "other")
        self.issue("AB-3", "refining")
        self.agent("r1", "rev1", typ="foreman:reviewer")
        self.assertEqual(self.kinds()["wip_ready"], [True])

    def test_wip_once_between_passes_while_counts_change(self):
        """The WIP notice is said once until the next pass, however its counts change; the pass gives it."""

        def wip():
            return [n["kind"] for n in loop.notices(self.project, "S1") if n["kind"].startswith("wip_")]

        self.issue("AB-1", "backlog")
        self.assertEqual(wip(), ["wip_ready"])  # 0/1 ready, 1 to plan
        self.issue("AB-2", "backlog")  # the count in the old key changed
        self.assertEqual(wip(), [])
        self.issue("AB-3", "refining")
        self.assertEqual(wip(), [])
        pass_kinds = [n["kind"] for n in self.notices()]  # the pass gives the detail
        self.assertIn("wip_ready", pass_kinds)
        loop.remember(self.project, loop.notices_for_pass(self.project, "S1"))
        self.issue("AB-4", "backlog")
        self.assertEqual(wip(), [])  # what the pass said is not repeated either
        self.issue("AB-5", "ready")  # the case goes away: ready 1/1
        self.assertEqual(wip(), ["wip_production"])  # another class, said once

    def test_two_wip_notices_are_one_line(self):
        a = notices.notice(
            "wip_production", True, "WIP (pipeline not full): production 0/1: launch the next `ready` issue."
        )
        b = notices.notice("wip_ready", True, "WIP (pipeline not full): unblocked ready 1/2 (refining 0): plans for X.")
        c = notices.notice("inbox", True, "Inbox: 1")
        out = loop.merge_wip([a, c, b])
        self.assertEqual([n["kind"] for n in out], ["wip_production", "inbox"])
        self.assertEqual(out[0]["text"].count("WIP (pipeline not full)"), 1)
        self.assertIn("unblocked ready 1/2", out[0]["text"])
        self.assertEqual(loop.merge_wip([a, c]), [a, c])

    def test_agents_compact_and_json(self):
        self.agent("a1", "zed7", typ="dev")
        r = self.cli("agents")
        self.assertEqual(len(r.stdout.strip().splitlines()), 1)
        self.assertIn("zed7", r.stdout)
        self.assertIn("working", r.stdout)
        self.assertFalse(r.stdout.lstrip().startswith("{"))
        for flag in ("--json", "--full"):
            d = json.loads(self.cli("agents", flag).stdout)
            self.assertEqual(d["a1"]["state"], "working")
            self.assertEqual(d["a1"]["identity"]["name"], "zed7")

    def test_dead_and_stalled_agents(self):
        self.agent("a1", "zed7", session="S0", minutes_ago=1)
        self.assertNotIn("dead_agents", self.kinds("S0"))
        self.assertEqual(self.kinds("S1")["dead_agents"], [True])  # S0 sends no heartbeat: its agents died with it
        self.assertNotIn("stalled_agents", self.kinds("S0"))
        self.agent("a1", "zed7", session="S0", minutes_ago=60)
        self.assertEqual(self.kinds("S0")["stalled_agents"], [True])
        self.assertNotIn("stalled_agents", self.kinds("S1"))  # dead, not stalled

    def heartbeat_of(self, sid, ago=0, **rec):
        at = (datetime.now() - timedelta(seconds=ago)).isoformat(timespec="seconds")
        f = heartbeat.beat_file(self.project, sid)
        f.parent.mkdir(parents=True, exist_ok=True)
        util.write_json(f, {"at": at, "started": at, "busy": False, **rec})

    def test_dead_agents_by_heartbeat_and_status(self):
        """An agent of another session is dead when that session's heartbeat is gone, not while it ticks; one of
        the marked session is dead when its `$.agent.list()` status says failed or killed."""
        self.agent("a1", "zed7", session="S0", minutes_ago=1)
        self.agent("a2", "ann3", session="S1", minutes_ago=60)
        self.heartbeat_of("S0")
        self.assertEqual(loop.dead_agents(self.project, "S1"), {})  # S0 still ticks: its agent lives
        self.heartbeat_of("S0", ago=3 * 60)
        self.assertEqual(loop.dead_agents(self.project, "S1"), {"a1": "session gone"})
        self.heartbeat_of("S0", ended="2020-01-01T00:00:00")
        self.assertEqual(loop.dead_agents(self.project, "S1"), {"a1": "session gone"})
        self.heartbeat_of("S0")
        self.heartbeat_of("S1", agents={"a2": "idle"})
        self.assertEqual(loop.dead_agents(self.project, "S1"), {})
        self.assertEqual(loop.stalled(self.project, "S1"), ["ann3 (60 min, idle)"])  # its status with it
        self.heartbeat_of("S1", agents={"a2": "killed"})
        self.assertEqual(loop.dead_agents(self.project, "S1"), {"a2": "killed"})
        self.assertEqual(loop.stalled(self.project, "S1"), [])  # dead, not stalled
        n = next(n for n in self.notices("S1") if n["kind"] == "dead_agents")
        self.assertIn("ann3 (killed)", n["text"])
        self.assertTrue(n["text"].startswith("Agents still marked live that are dead"))
        self.heartbeat_of("S1", agents={"a2": "completed"})  # its loop ended: foreman waits for its stop
        self.assertEqual(loop.dead_agents(self.project, "S1"), {})

    def test_lock_foreign_and_queue_with_a_live_run(self):
        self.lock_run("stranger")
        h = self.holding("stranger")["holders"][0]
        self.assertEqual((h["who"], h["alive"]), ("stranger", True))
        self.assertTrue(h["pid"].isdigit())
        self.assertEqual(self.kinds()["lock_foreign"], [False])  # another session using it through run
        self.lock_run("orchestrator", "mine")
        self.queued("orchestrator")
        k = self.kinds()
        self.assertEqual(k["lock_queue"], [False])  # it moves by itself
        self.assertNotIn("lock_foreign", k)  # announced once
        self.assertTrue(notices.idle(self.notices("S1")))

    def test_lock_foreign_once_while_its_meta_is_written(self):
        """lock.sh writes `owner` first and `meta` (where `since` comes from) a moment later: a pass between the two
        and one after them are the same case, announced once (the cause of a rare failure of the test above when the
        suite runs in parallel)."""
        r = self.cli("lock", "widget", "acquire", "by hand", "10", env={"FOREMAN_LOCK_WHO": "stranger"})
        self.assertIn("LOCKED", r.stdout)
        meta = lock.paths("widget")["dir"] / "meta"
        text = meta.read_text()
        meta.unlink()
        self.assertEqual(lock.status("widget")["holders"][0]["since"], "")
        self.assertEqual(self.kinds()["lock_foreign"], [True])
        meta.write_text(text)
        self.assertNotIn("lock_foreign", self.kinds())

    def ticket(self, who, pid=None, text=None):
        """A ticket of `who` in the queue of `widget`, as lock.sh writes it (pid: this test's process, alive)."""
        pid = pid or os.getpid()
        q = lock.paths("widget")["queue"]
        q.mkdir(parents=True, exist_ok=True)
        f = q / f"{int(time.time())}-{pid:07d}"
        f.write_text(f"{who}|{datetime.now():%Y-%m-%dT%H:%M:%S}|{pid}|a job|\n" if text is None else text)
        return f

    def test_lock_being_taken_is_no_notice(self):
        """lock.sh takes the lock with mkdir and writes `owner` right after (an older lock.sh wrote it empty first);
        its ticket goes after that. A pass in between sees a lock taken by nobody yet, with the taker still queued:
        not a foreign holder, not a free lock with a queue, but a queue that moves."""
        d = lock.paths("widget")["dir"]
        d.mkdir(parents=True)
        self.ticket("orchestrator")
        st = lock.status("widget")
        self.assertEqual([(h["who"], h["taking"], h["alive"]) for h in st["holders"]], [("?", "writing", None)])
        k = self.kinds()
        self.assertNotIn("lock_foreign", k)
        self.assertEqual(k["lock_queue"], [False])  # it moves
        (d / "owner").write_text("")  # an older lock.sh, between creating the file and writing it
        self.assertEqual(lock.status("widget")["holders"][0]["taking"], "writing")
        self.assertEqual(self.kinds()["lock_queue"], [False])

    def test_lock_never_written_is_stuck(self):
        """A lock directory left without owner line (the taker died between mkdir and writing it) stays taken by
        nobody known: past WRITING_S, a queue behind it is a case to look at."""
        d = lock.paths("widget")["dir"]
        d.mkdir(parents=True)
        old = time.time() - lock.WRITING_S - 5
        os.utime(d, (old, old))
        self.ticket("zed7")
        self.assertEqual(lock.status("widget")["holders"][0]["taking"], "stuck")
        self.assertEqual(self.kinds()["lock_queue"], [True])
        r = self.cli("lock", "widget", "check")
        self.assertEqual(r.returncode, 0)
        self.assertIn("being taken", r.stdout)

    def test_lock_holder_ticket_is_not_queued(self):
        """The taker writes `owner` and then removes its ticket: in between it is the holder, not one more waiter
        (a foreign `acquire` would be announced twice, queued and holding, and a lone holder gave a queue notice)."""
        d = lock.paths("widget")["dir"]
        d.mkdir(parents=True)
        (d / "owner").write_text(f"stranger: by hand (2026-10-31 10:00:00, pid {os.getpid()})\n")
        self.ticket("stranger")
        self.ticket("zed7", pid=os.getppid(), text="")  # a ticket still being written by an older lock.sh
        (lock.paths("widget")["queue"] / f".{int(time.time())}-{os.getpid():07d}.42").write_text("x|y|1|z|\n")
        self.assertEqual(lock.status("widget")["queue"], [])
        k = self.kinds()
        self.assertEqual(k["lock_foreign"], [False])  # held through a live pid, once
        self.assertNotIn("lock_queue", k)

    def test_lock_foreign_long_and_queue_without_run(self):
        r = self.cli("lock", "widget", "acquire", "by hand", "10", env={"FOREMAN_LOCK_WHO": "stranger"})
        self.assertIn("LOCKED", r.stdout)
        h = lock.status("widget")["holders"][0]
        self.assertEqual(h["alive"], False)  # acquire: its shell ended with the call
        self.assertEqual(self.kinds()["lock_foreign"], [True])
        self.assertNotIn("lock_foreign", self.kinds())  # once per case
        self.assertNotIn("lock_long", self.kinds())
        meta = lock.paths("widget")["dir"] / "meta"
        old = (datetime.now() - timedelta(hours=2)).isoformat(timespec="seconds")
        meta.write_text(f"{old}|{old}|0|by hand\n")
        self.assertEqual(self.kinds()["lock_long"], [True])
        self.assertNotIn("lock_long", self.kinds())
        self.assertNotIn("lock_queue", self.kinds())
        # [loop] lock_long_min: held 2 h is not long for a product that says 3 h; 4 h is
        self.write_toml(self.toml.replace("disk_low_gb = 0", "disk_low_gb = 0\nlock_long_min = 180"))
        (self.project.state / "lock-notified").unlink()
        k = self.kinds()
        self.assertNotIn("lock_long", k)
        old = (datetime.now() - timedelta(hours=4)).isoformat(timespec="seconds")
        meta.write_text(f"{old}|{old}|0|by hand\n")
        self.assertEqual(self.kinds()["lock_long"], [True])
        self.lock_run("orchestrator", "mine")
        self.queued("orchestrator")
        self.assertEqual(self.kinds()["lock_queue"], [True])  # waiting behind someone who is not running

    def test_lock_queue_behind_a_dead_run(self):
        self.lock_run("orchestrator", "first")
        self.holding("orchestrator")
        owner = lock.paths("widget")["dir"] / "owner"
        owner.write_text("orchestrator: first (2026-10-30 10:00:00, pid 999999)\n")  # its process is gone
        self.lock_run("zed7", "second")
        self.queued("zed7")
        self.assertEqual(self.kinds()["lock_queue"], [True])
        owner.write_text("orchestrator: first (delegated, no pid)\n")
        self.assertIsNone(lock.status("widget")["holders"][0]["alive"])
        self.assertEqual(self.kinds()["lock_queue"], [True])  # no pid: in doubt, actionable

    def worktree(self, branch):
        wt = self.tmp / "wt" / branch.replace("/", "-")
        git(self.tmp / "app", "worktree", "add", "-q", "-b", branch, str(wt))
        return wt

    def test_ready_to_integrate_loose_and_orphan(self):
        self.issue("AB-1", "in progress", agent="zed7", branch="ab1/x")
        self.issue("AB-2", "in progress")
        k = self.kinds()
        self.assertNotIn("ready_to_integrate", k)
        self.assertNotIn("loose_worktrees", k)
        self.assertEqual(k["orphan_in_progress"], [True])  # AB-1 and AB-2: no agent, no branch
        self.worktree("ab1/x")
        self.worktree("tmp/loose")
        self.agent("a2", "other", text="Issue: AB-2 T1")
        k = self.kinds()
        self.assertEqual(k["ready_to_integrate"], [True])
        self.assertEqual(k["loose_worktrees"], [True])
        self.assertNotIn("orphan_in_progress", k)
        self.agent("a1", "zed7")  # its agent is alive: not ready yet
        self.assertNotIn("ready_to_integrate", self.kinds())

    def test_ready_to_integrate_paused_informs(self):
        self.issue("AB-1", "in progress", agent="zed7", branch="ab1/x")
        self.worktree("ab1/x")
        self.project.state.mkdir(parents=True, exist_ok=True)
        (self.project.state / "pause").write_text("paused at 21:58\n")
        k = self.kinds()
        self.assertEqual(k["ready_to_integrate"], [False])  # integrate is blocked: nothing to act on
        self.assertTrue(notices.idle(self.notices()))

    def test_backlog_problems_and_unreadable(self):
        self.assertNotIn("backlog_problems", self.kinds())
        data = self.b.load()
        data["decisions"].append(
            {"id": "AB-1 J1", "title": "t", "date": "someday", "text": "", "options": ["a"], "recommendation": ""}
        )
        self.b.data_path.write_text(self.b.dumps(data))
        self.assertEqual(self.kinds()["backlog_problems"], [True])
        self.b.data_path.write_text("{not json")
        k = self.kinds()
        self.assertEqual(k["backlog_unreadable"], [True])
        self.assertNotIn("wip_production", k)

    def test_hung_shells(self):
        self.assertNotIn("hung_shells", self.kinds())
        f = self.tmp / "hung.sh"
        f.write_text("echo 'pid 42, 300 min: older than 3 h without a lock or CI'\nexit 1\n")
        os.environ["FOREMAN_HUNG_SHELLS_SH"] = str(f)
        self.assertEqual(self.kinds()["hung_shells"], [True])

    def test_decision_due(self):
        from datetime import date, timedelta

        today = date.today()
        with self.b.mutate() as d:
            for n, by in (("J1", today), ("J2", today + timedelta(days=3))):
                d["decisions"].append(
                    {
                        "id": f"E1 {n}",
                        "title": "t",
                        "date": today.isoformat(),
                        "text": "",
                        "options": ["a", "b"],
                        "recommendation": "a",
                        "unanswered": {"waits": "the release", "by": by.isoformat()},
                    }
                )
        due = [n for n in self.notices(mark=False) if n["kind"] == "decision_due"]
        self.assertEqual(len(due), 1)  # only the one whose day came; looking marks nothing
        self.assertIn("E1 J1", due[0]["text"])
        self.assertIn("what waits: the release", due[0]["text"])
        self.assertTrue(due[0]["actionable"])
        self.assertEqual(self.kinds()["decision_due"], [True])  # marks it
        self.assertNotIn("decision_due", self.kinds())  # once
        self.assertEqual(backlog_mod.due_decisions(self.b.load(), today + timedelta(days=3))[1]["id"], "E1 J2")
        self.assertEqual(backlog_mod.due_decisions(self.b.load(), today - timedelta(days=1)), [])

    def test_recurring_late(self):
        self.assertNotIn("recurring_late", self.kinds())
        self.config(
            extra='\n[[recurring]]\nname = "health"\nsources = [{ heartbeat = "health", max_h = 1 }]\n'
            '\n[[recurring]]\nname = "Fake orchestrator loop"\nsources = [{ heartbeat = "loop", max_h = 1 }]'
        )
        self.assertEqual(self.kinds()["recurring_late"], [True])
        notes.beat(self.project, "health")
        self.assertNotIn("recurring_late", self.kinds())  # the loop's own task never counts

    def test_recurring_at_is_actionable_in_the_pass(self):
        """A daily task at a fixed time, with a fixed clock (notes.now)."""
        self.config(extra='\n[[recurring]]\nname = "watch"\nsources = [{ heartbeat = "watch", at = "09:30" }]')
        util.write_json(self.project.state / "heartbeats.json", {"watch": "2026-10-30T09:40:00"})
        saved = notes.now
        try:
            notes.now = lambda: datetime(2026, 10, 31, 9, 29)
            self.assertNotIn("recurring_late", self.kinds())
            notes.now = lambda: datetime(2026, 10, 31, 9, 31)
            self.assertEqual(self.kinds()["recurring_late"], [True])
            util.write_json(self.project.state / "heartbeats.json", {"watch": "2026-10-31T09:35:00"})
            self.assertNotIn("recurring_late", self.kinds())
        finally:
            notes.now = saved

    def test_inbox(self):
        self.assertNotIn("inbox", self.kinds())
        notes.inbox_add(self.project, "look at AB-1")
        self.assertEqual(self.kinds()["inbox"], [True])
        self.assertEqual(self.kinds()["inbox"], [True])  # unread: in every pass
        notes.inbox_read(self.project)
        self.assertNotIn("inbox", self.kinds())

    def test_inbox_counts_the_owners_marks(self):
        """The panel's «I want it done» on a triage issue: the pass's inbox notice says so (and the moves)."""
        notes.inbox_add(self.project, "AB-1: the owner wants it (triage)", move="AB-1", want="on")
        notes.inbox_add(self.project, "AB-2: the owner no longer wants it", move="AB-2", want="off")
        notes.inbox_add(self.project, "AB-3: 2 -> 1 in A1", move="AB-3")
        n = next(n for n in self.notices() if n["kind"] == "inbox")
        self.assertTrue(n["actionable"])
        self.assertIn("unread from Owner (1 wanted from triage, 1 no longer wanted, 1 backlog moves) (ids", n["text"])
        # answers from the panel that resolved their decision: said apart, to apply before launching what they freed
        for k in (1, 2):
            notes.inbox_add(
                self.project, "a", decision=f"AB-{k} J1, t", did=f"AB-{k} J1", resolved=True, freed=[f"freed: AB-{k}"]
            )
        n = next(n for n in self.notices() if n["kind"] == "inbox")
        self.assertIn(
            "unread from Owner (2 decisions answered (already resolved): apply them before launching what they "
            "freed; 1 wanted from triage, 1 no longer wanted, 1 backlog moves) (ids",
            n["text"],
        )

    def test_disk_low_once_until_it_recovers(self):
        self.config(loop_extra="disk_low_gb = 1000000")
        self.assertEqual(self.kinds(mark=False)["disk_low"], [True])
        self.assertEqual(self.kinds()["disk_low"], [True])
        self.assertEqual(self.kinds()["disk_low"], [False])
        self.config()
        self.assertNotIn("disk_low", self.kinds())
        self.config(loop_extra="disk_low_gb = 1000000")
        self.assertEqual(self.kinds()["disk_low"], [True])

    def test_checks(self):
        self.config(loop_extra='checks = ["true", "echo \'2 simulators booted\'; echo other", "exit 3"]')
        k = self.kinds()
        self.assertEqual(k["check"], [True, True])  # plain lines ask for action
        self.assertEqual(k["check_failed"], [True])
        self.config(loop_extra='checks = ["true", "exit 1"]')
        k = self.kinds()
        self.assertNotIn("check", k)
        self.assertNotIn("check_failed", k)

    def test_check_json_lines(self):
        lines = [
            '{"kind": "sim", "actionable": false, "text": "2 simulators booted"}',
            '{"text": "a JSON line without kind nor actionable"}',
            '{"kind": "sim", "actionable": "no", "text": "not a boolean: in doubt, actionable"}',
            '{"actionable": false}',  # no text: a plain line
            "{not json",
            "plain text",
        ]
        script = self.tmp / "check.sh"
        script.write_text("".join(f"echo {json.dumps(ln)}\n" for ln in lines))
        self.config(loop_extra=f'checks = ["bash {script}"]')
        ns = [n for n in self.notices() if n["kind"] not in ("wip_production", "wip_ready")]
        self.assertEqual(
            [(n["kind"], n["actionable"]) for n in ns],
            [("sim", False), ("check", True), ("sim", True), ("check", True), ("check", True), ("check", True)],
        )
        # the same case has the same key from one pass to the next
        self.assertEqual([n["key"] for n in ns], [n["key"] for n in self.notices() if n["kind"] in ("sim", "check")])
        self.assertEqual(len({n["key"] for n in ns}), len(ns))

    def test_check_failed_and_timeout(self):
        self.config(loop_extra='checks = ["echo \'{\\"actionable\\": false, \\"text\\": \\"x\\"}\'; exit 2"]')
        k = self.kinds()
        self.assertEqual((k["check"], k["check_failed"]), ([False], [True]))
        self.assertFalse(notices.idle(self.notices()))

    def test_summary_failed(self):
        def boom(*a, **k):
            raise RuntimeError("broken")

        saved = loop.notices_for_pass
        loop.notices_for_pass = boom
        try:
            _text, ns = loop.loop_pass(self.project, "S1")
        finally:
            loop.notices_for_pass = saved
        self.assertEqual([(n["kind"], n["actionable"]) for n in ns], [("summary_failed", True)])
        self.assertFalse(notices.idle(ns))
        self.assertIn("loop", notes.heartbeats(self.project))

    def test_render(self):
        ns = [notices.notice("paused", False, "P"), notices.notice("inbox", True, "I")]
        lines = notices.render(ns, "H").split("\n")
        self.assertEqual(lines[1:], ["- " + notices.INFO + "P", "- I"])
        self.assertTrue(notices.render([], "H").startswith("H"))
        self.assertTrue(notices.render(ns[:1], "H").endswith(notices.INFO + "P"))

    # ---------- the hook ----------

    def test_an_idle_pass_is_one_line_on_screen(self):
        self.cli("session", "S1")
        reason = self.assert_idle()  # nothing at all
        self.assertTrue(reason.startswith("Loop pass "), reason)
        line = json.loads(loop.loop_log_file(self.project).read_text().splitlines()[-1])
        self.assertEqual((line["session"], line["skipped"]), ("S1", True))  # recorded in loop.jsonl
        self.issue("AB-1", "triage")
        self.assert_idle()  # only triage
        self.lock_run("stranger")
        self.holding("stranger")
        self.assert_idle()  # another session's live run
        # the reason goes in the product's language
        self.write_toml(self.toml.replace('owner = "Owner"', 'owner = "Owner"\nlanguage = "es"'))
        self.assertTrue(self.assert_idle().startswith("Pasada del bucle "))
        # no prompt is ever blocked: the submitted pass gets nothing more from the loop hook, nor do other prompts
        for ev in (PASS, {**PASS, "session_id": "S9", "prompt": "hello"}, {**PASS, "prompt": "hello"}):
            self.assertNotIn("decision", self.hook("loop", ev).stdout)
        self.assertEqual(self.hook("loop", PASS).stdout, "")

    def test_a_pass_that_asks_for_action_is_submitted(self):
        self.cli("session", "S1")
        self.issue("AB-1", "ready")
        self.assert_submitted("wip_production")
        self.agent("a1", "zed7", session="S1")  # production full
        self.assert_idle()
        notes.inbox_add(self.project, "hello")
        self.assert_submitted("inbox")
        notes.inbox_read(self.project)
        self.config(loop_extra='checks = ["echo needs a look"]')
        self.assert_submitted("check")
        self.config()
        with self.b.mutate() as d:
            self.b.set_sub(d, "AB-1", "T1", status="in progress", agent="old1", branch="ab1/x")
        self.worktree("ab1/x")
        ctx = self.assert_submitted("ready_to_integrate")
        self.assertIn(notices.INFO, ctx)  # the information-only notices are marked, not dropped

    def test_a_pass_whose_ready_work_all_waits_for_a_decision_is_skipped(self):
        self.cli("session", "S1")
        self.issue("AB-1", "ready")
        with self.b.mutate() as d:
            self.b.set_sub(d, "AB-1", "T1", status="done", result="r")
            self.b.set_sub(d, "AB-1", "T2", title="needs the owner's yes")
            self.b.add_decision(d, None, "Go?", "why", ["a", "b"], "a", issue="AB-1", as_is=True, blocks=["T2"])
        self.issue("AB-2", "ready")
        with self.b.mutate() as d:
            self.b.add_decision(d, None, "Which?", "why", ["a", "b"], "a", issue="AB-2", as_is=True, blocks=["issue"])
        log = self.assert_idle()  # nothing an agent can be launched on: an idle pass
        self.assertIn("to launch (AB-1 T2 waits for decision AB-1 J1; AB-2 waits", log)  # says why
        line = json.loads(loop.loop_log_file(self.project).read_text().splitlines()[-1])
        self.assertEqual((line["skipped"], line["actionable"]), (True, []))
        # one ready issue that can be launched now, next to the held ones: the pass asks for it
        self.issue("AB-3", "ready")
        ctx = self.assert_submitted("wip_production")
        self.assertIn("launch the next `ready` issue (AB-1 T2 waits for decision AB-1 J1;", ctx)
        self.assertNotIn(notices.INFO + "WIP (pipeline not full): production", ctx)

    def test_a_check_json_not_actionable_is_idle(self):
        self.cli("session", "S1")
        self.config(
            loop_extra='checks = ["echo \'{\\"kind\\": \\"sim\\", \\"actionable\\": false, '
            '\\"text\\": \\"2 simulators booted\\"}\'"]'
        )
        self.assertEqual(self.kinds(mark=False)["sim"], [False])
        self.assert_idle()
        self.config(loop_extra='checks = ["echo 2 simulators booted"]')  # plain text: actionable
        self.assert_submitted("check")

    # ---------- the CLI ----------

    def test_cli_json_does_not_mark(self):
        self.config(loop_extra="disk_low_gb = 1000000")
        r = self.cli("loop", "--json")
        self.assertEqual(r.returncode, 0, r.stderr)
        ns = json.loads(r.stdout)
        self.assertEqual(
            {n["kind"]: n["actionable"] for n in ns}, {"wip_production": False, "wip_ready": False, "disk_low": True}
        )
        self.assertFalse((self.project.state / "disk-low-notified").exists())
        r = self.cli("loop")
        self.assertIn("- " + notices.INFO, r.stdout)


class BetweenPassesTest(NoticesCase):
    """Between passes only the actionable notices whose case was not said yet (<state>/notices-last)."""

    def between(self):
        """[(kind, actionable)] of loop.notices()."""
        out = []
        for n in loop.notices(self.project):
            self.assertEqual(set(n), {"kind", "actionable", "text", "key"})
            out.append((n["kind"], n["actionable"]))
        return out

    def test_same_state_twice_says_nothing_the_second_time(self):
        self.issue("AB-1", "ready")
        self.assertEqual(self.between(), [("wip_production", True)])
        self.assertEqual(self.between(), [])
        self.assertEqual(self.between(), [])  # no time limit any more, and still quiet

    def test_a_case_that_goes_away_and_comes_back_is_said_again(self):
        self.issue("AB-1", "ready")
        self.assertEqual(self.between(), [("wip_production", True)])
        self.agent("a1", "zed7")  # production full: the case goes away
        self.assertEqual(self.between(), [])
        f = loop.agents_file(self.project)
        d = util.read_json(f, {})
        d["a1"]["stopped"] = True
        util.write_json(f, d)
        self.assertEqual(self.between(), [("wip_production", True)])  # back
        self.assertEqual(self.between(), [])

    def test_a_changed_case_is_said_again(self):
        notes.inbox_add(self.project, "one")
        self.assertEqual(self.between(), [("inbox", True)])
        notes.inbox_add(self.project, "two")  # another unread message: another case
        self.assertEqual(self.between(), [("inbox", True)])

    def test_wip_counts_changing_is_not_another_case(self):
        self.issue("AB-1", "ready")
        self.assertEqual(self.between(), [("wip_production", True)])
        self.issue("AB-2", "ready")
        self.assertEqual(self.between(), [])

    def test_only_actionable(self):
        self.issue("AB-1", "triage")  # wip_production and wip_ready, neither actionable
        self.assertEqual(self.between(), [])
        self.project.state.mkdir(parents=True, exist_ok=True)
        (self.project.state / "pause").write_text("paused at 21:07\n")
        self.issue("AB-2", "ready")
        self.assertEqual(self.between(), [])  # paused: nothing to launch

    def test_inbox_once(self):
        notes.inbox_add(self.project, "hello")
        self.assertEqual(self.between(), [("inbox", True)])
        self.assertEqual(self.between(), [])
        notes.inbox_add(self.project, "again")
        self.assertEqual(self.between(), [("inbox", True)])  # a new message
        self.assertEqual(self.kinds()["inbox"], [True])  # the pass still lists the unread ones…
        self.assertEqual(self.between(), [])  # …without making them new again

    def test_inbox_counts_the_owners_marks(self):
        notes.inbox_add(self.project, "AB-1: the owner wants it (triage)", move="AB-1", want="on")
        n = next(n for n in loop.notices(self.project) if n["kind"] == "inbox")
        self.assertTrue(n["actionable"])
        self.assertIn("unread from Owner (1 wanted from triage): `foreman inbox new`", n["text"])
        notes.inbox_add(self.project, "a", decision="AB-1 J1, t", did="AB-1 J1", resolved=True, freed=[])
        n = next(n for n in loop.notices(self.project) if n["kind"] == "inbox")
        self.assertIn(
            "1 unread from Owner (1 decision answered (already resolved): apply it before launching what it "
            "freed): `foreman inbox new`",  # only the new one, between passes
            n["text"],
        )

    def test_locks_once_and_shared_with_the_pass(self):
        r = self.cli("lock", "widget", "acquire", "by hand", "10", env={"FOREMAN_LOCK_WHO": "stranger"})
        self.assertIn("LOCKED", r.stdout)
        self.assertEqual(self.between(), [("lock_foreign", True)])
        self.assertEqual(self.between(), [])
        self.assertNotIn("lock_foreign", self.kinds())  # said between passes: the pass does not repeat it

    def test_lock_not_actionable_is_left_for_the_pass(self):
        self.lock_run("stranger")
        self.holding("stranger")
        self.assertEqual(self.between(), [])
        self.assertEqual(self.kinds()["lock_foreign"], [False])  # the pass still shows it, as information

    def test_stalled_once(self):
        self.agent("a1", "zed7", minutes_ago=60)
        self.issue("AB-1", "ready")
        self.assertEqual(self.between(), [("stalled_agents", True)])  # production is full (the agent counts)
        self.assertEqual(self.between(), [])

    def test_tick_between_passes_and_after_a_pass(self):
        """The module's tick in the marked session, with no pass due, answers the notices not said yet (`notices`,
        once per case); a tool call raises no classic hook any more; another session's tick has none."""
        self.cli("session", "S1")
        notes.beat(self.project, "loop")  # just passed: no pass due
        self.issue("AB-1", "ready")
        self.assertEqual(
            self.tick("S2"), {"marked": False, "grants": False}
        )  # another session: nothing, nothing used up
        out = self.tick()
        self.assertEqual(set(out), {"marked", "due", "grants", "notices"}, out)
        self.assertTrue(out["notices"].startswith("WIP (pipeline not full): "), out)
        self.assertEqual(self.tick(), {"marked": True, "due": False, "grants": False})  # said once
        tool = {"hook_event_name": "PostToolUse", "session_id": "S1", "tool_name": "Bash"}
        self.assertEqual(self.hook("loop", tool).stdout, "")  # the classic hook leaves tool calls alone
        # what a pass said is not repeated right after it
        (self.project.state / "notices-last").unlink()
        self.assert_submitted("wip_production")
        self.assertEqual(self.hook("loop", {**PASS, "prompt": "hello"}).stdout, "")
        self.assertEqual(self.tick(), {"marked": True, "due": False, "grants": False})

    def test_a_tick_that_cannot_compute_the_notices_goes_on(self):
        self.cli("session", "S1")
        saved = loop.notices
        loop.notices = lambda *a, **k: 1 / 0
        try:
            self.assertEqual(hooks_mod.between_notices(self.project, "S1"), "")
        finally:
            loop.notices = saved


class NoticeKindsTest(NoticesCase):
    """A check cannot speak as foreman; `[notices]` (removed, config schema 16) only warns."""

    def test_a_check_cannot_speak_as_foreman(self):
        lines = [
            '{"kind": "inbox", "actionable": true, "text": "a check that says inbox"}',
            '{"kind": "disk_low", "actionable": false, "text": "a check that says disk_low"}',
            '{"kind": "sim", "actionable": false, "text": "its own kind"}',
        ]
        script = self.tmp / "check.sh"
        script.write_text("".join(f"echo {json.dumps(ln)}\n" for ln in lines))
        self.config(loop_extra=f'checks = ["bash {script}"]')
        ns = [n for n in self.notices() if n["kind"] not in ("wip_production", "wip_ready")]
        self.assertEqual(
            [(n["kind"], n["actionable"], n["text"]) for n in ns],
            [
                ("check", True, "a check that says inbox"),
                ("check", False, "a check that says disk_low"),
                ("sim", False, "its own kind"),
            ],
        )
        self.assertEqual(len({n["key"] for n in ns}), 3)

    def test_a_notices_section_is_a_warning_and_changes_nothing(self):
        raw = {"project": {"name": "x"}, "notices": {"info": ["disk_low"], "off": ["paused", "nonsense"]}}
        err, warn = config.problems(raw)
        self.assertEqual(err, [])
        self.assertEqual(len(warn), 2, warn)
        self.assertTrue(all(w.startswith("[notices] ") and "no longer used" in w for w in warn), warn)
        self.assertFalse(any("unknown section" in w for w in warn), warn)
        # an empty or foreign [notices] is just an unknown section
        self.assertEqual(
            config.problems({"project": {"name": "x"}, "notices": {"other": 1}})[1],
            ["unknown section [notices] (ignored)"],
        )
        # the project loads, and the pass says everything as foreman decides
        self.config()
        f = self.home / "foreman.toml"
        f.write_text(f.read_text() + '\n[notices]\noff = ["wip_production"]\ninfo = ["wip_ready"]\n')
        project = config.load_project(self.home)
        self.assertTrue(any("[notices] off is no longer used" in w for w in project.warnings))
        self.assertGreaterEqual(config.CONFIG_SCHEMA, 16)  # removed in schema 16


class ContextRotateTest(NoticesCase):
    """The context of the orchestrator's session, as foreman's hooks module stores Claude Code's measure
    (`hook mod measure`, <state>/measure/<session>.json): `context_rotate` actionable when it crosses [loop]
    context_rotate and again when the session is left without live agents; information otherwise. No measure is
    «unknown»: no notice, and no transcript is read instead. test_renew has the renewal itself."""

    def setUp(self):
        super().setUp()
        self.config(loop_extra="context_rotate = 1000")

    def measure(self, tokens, session="S1", window=200000, cost=0.5, changed=("context",)):
        """What the module sends `hook mod measure` after a turn (the engine's session.measure fields)."""
        ctx = {"window": window}
        if tokens is not None:
            ctx.update(tokens=tokens, percent=round(100 * tokens / window))
        return hooks_mod.measure(
            {
                "cwd": str(self.home),
                "session": session,
                "context": ctx,
                "rate_limits": [{"kind": "five_hour", "percentUsed": 23.5, "resetsAt": "2020-01-15T15:00:00Z"}],
                "cost": {"usd": cost} if cost is not None else None,
            }
        )

    def test_the_stored_measure(self):
        self.assertEqual(self.measure(1500), {"stored": True})
        f = self.project.state / "measure" / "S1.json"
        d = json.loads(f.read_text())
        self.assertEqual(sorted(d), sorted(hooks_mod.MEASURE_KEYS))
        self.assertEqual((d["tokens"], d["window"], d["percent"], d["cost_usd"]), (1500, 200000, 1, 0.5))
        self.assertEqual(
            d["rate_limits"], [{"kind": "five_hour", "percent_used": 23.5, "resets_at": "2020-01-15T15:00:00Z"}]
        )
        self.assertEqual(hooks_mod.read_measure(self.project, "S1"), d)
        self.measure(None, cost=None)  # a fresh or just compacted window: no tokens yet, no cost ledger
        self.assertEqual(
            {
                k: v
                for k, v in hooks_mod.read_measure(self.project, "S1").items()
                if k in ("tokens", "percent", "cost_usd")
            },
            {"tokens": None, "percent": None, "cost_usd": None},
        )

    def test_the_stored_tokens_are_the_transcript_definition(self):
        """The objective check: for one turn's usage, the figure Claude Code measures (input + cache written + cache
        read of the last response, SessionContextUsage.tokens) and stored here equals transcripts.context."""
        from foreman import transcripts

        usage = {
            "input_tokens": 3,
            "cache_creation_input_tokens": 2417,
            "cache_read_input_tokens": 118034,
            "output_tokens": 912,
        }
        line = json.dumps({"type": "assistant", "message": {"id": "m1", "model": "claude-test", "usage": usage}})
        call = transcripts.parse_call(line.encode())
        engine = usage["input_tokens"] + usage["cache_creation_input_tokens"] + usage["cache_read_input_tokens"]
        self.measure(engine)
        self.assertEqual(hooks_mod.read_measure(self.project, "S1")["tokens"], transcripts.context(call))
        self.assertEqual(transcripts.context(call), 120454)

    def test_outside_a_project_and_bad_input(self):
        import tempfile

        with tempfile.TemporaryDirectory() as elsewhere:
            self.assertEqual(
                hooks_mod.measure({"cwd": elsewhere, "session": "S1", "context": {"tokens": 5}}), {"stored": False}
            )
        self.assertIsNone(hooks_mod.read_measure(self.project, "S1"))
        for bad in ("../S1", "", None, "a/b"):
            with self.assertRaises(ValueError, msg=bad):
                hooks_mod.measure({"cwd": str(self.home), "session": bad, "context": {"tokens": 5}})
            self.assertIsNone(hooks_mod.read_measure(self.project, bad))
        f = self.project.state / "measure" / "S1.json"
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("not json")
        self.assertIsNone(hooks_mod.read_measure(self.project, "S1"))

    def test_old_measures_go(self):
        self.measure(10, session="S0")
        old = self.project.state / "measure" / "S0.json"
        past = time.time() - (hooks_mod.MEASURE_DAYS + 1) * 86400
        os.utime(old, (past, past))
        self.measure(10, session="S1")
        self.assertFalse(old.exists())
        self.assertTrue((self.project.state / "measure" / "S1.json").exists())

    def test_the_cli_op(self):
        stdin = json.dumps(
            {"cwd": str(self.home), "session": "S1", "context": {"tokens": 7, "window": 1000, "percent": 1}}
        )
        r = self.cli("hook", "mod", "measure", inp=stdin)
        self.assertEqual((r.returncode, r.stdout), (0, '{"stored": true}\n'), r.stderr)
        r = self.cli("hook", "mod", "measure", inp=json.dumps({"cwd": str(self.home), "session": "../x"}))
        self.assertEqual(r.returncode, 1)
        self.assertIn("not a session id", r.stderr)

    def test_no_measure_no_notice_and_no_transcript_read(self):
        f = self.tmp / "claude-projects" / "-fake-home" / "S1.jsonl"  # a transcript over the threshold: not read
        f.parent.mkdir(parents=True)
        f.write_text(
            json.dumps({"type": "assistant", "message": {"model": "m", "usage": {"input_tokens": 10**6}}}) + "\n"
        )
        self.assertNotIn("context_rotate", self.kinds("S1"))
        self.cli("session", "S1")
        self.assert_idle()
        self.assertEqual(loop.notices(self.project, "S1"), [])
        self.measure(None)  # measured, but no response in the window yet
        self.assertNotIn("context_rotate", self.kinds("S1"))
        r = self.cli("session")
        self.assertIn("context unknown (no response since the session started or compacted", r.stdout)

    def test_crossing_then_free_of_agents(self):
        self.measure(900)
        self.assertNotIn("context_rotate", self.kinds("S1"))  # below the threshold
        self.agent("a1", "zed7", session="S1")  # a live agent of this session
        self.measure(1200)  # below the safety trigger (1.5 × 1000)
        self.assertEqual(self.kinds("S1", mark=False)["context_rotate"], [True])  # looking does not use it up
        self.assertEqual(self.kinds("S1")["context_rotate"], [True])  # crosses
        self.assertEqual(self.kinds("S1")["context_rotate"], [False])  # then information
        f = loop.agents_file(self.project)
        d = util.read_json(f, {})
        d["a1"]["stopped"] = True
        util.write_json(f, d)
        self.assertEqual(self.kinds("S1")["context_rotate"], [True])  # free of agents: time to renew
        self.assertEqual(self.kinds("S1")["context_rotate"], [False])
        self.measure(400)  # after a /compact
        self.assertNotIn("context_rotate", self.kinds("S1"))
        self.measure(1100)  # crosses again
        self.assertEqual(self.kinds("S1")["context_rotate"], [True])
        self.assertNotIn("context_rotate", self.kinds("S2"))  # another session: no measure

    def test_the_text_says_the_window(self):
        self.measure(1200, window=2000)
        text = next(n["text"] for n in self.notices("S1", False) if n["kind"] == "context_rotate")
        self.assertTrue(
            text.startswith(
                "Context 1K (60% of 2K) >= 1K ([loop] context_rotate): renew this session's memory in place "
                "(docs/orchestrator.md, «Renewal»)"
            ),
            text,
        )
        self.assertNotIn("handover", text)

    def test_wip_production_informs_while_rotating(self):
        self.issue("AB-1", "ready")
        self.measure(1500)
        k = self.kinds("S1")
        self.assertEqual((k["context_rotate"], k["wip_production"]), ([True], [False]))
        self.measure(500)
        self.assertEqual(self.kinds("S1")["wip_production"], [True])

    def test_off_and_bad_values(self):
        from foreman import config

        self.measure(10**6, window=2 * 10**6)
        self.assertEqual(self.kinds("S1")["context_rotate"], [True])
        self.config(loop_extra="context_rotate = 0")
        self.assertNotIn("context_rotate", self.kinds("S1"))
        self.config()  # the default, 200000
        self.assertEqual(self.kinds("S1")["context_rotate"], [True])
        self.measure(150000)
        self.assertNotIn("context_rotate", self.kinds("S1"))
        for bad in ('"300K"', "-1", "true", "1.5"):
            with self.assertRaises(config.ConfigError, msg=bad):
                self.config(loop_extra=f"context_rotate = {bad}")
        for old in ('"handover"', '"compact"', '"compactt"', "true"):  # a removed key: a warning, whatever it says
            self.config(loop_extra=f"renew = {old}")
            self.assertEqual(
                [w for w in self.project.warnings if "renew" in w],
                [
                    "foreman.toml: [loop] renew is no longer used (the orchestrator's memory always renews in place "
                    "(docs/orchestrator.md, «Renewal»)): delete it from foreman.toml (ignored)"
                ],
            )

    def test_between_passes_once_and_shared_with_the_pass(self):
        self.measure(1500)
        out = [(n["kind"], n["actionable"]) for n in loop.notices(self.project, "S1")]
        self.assertEqual(out, [("context_rotate", True)])
        self.assertEqual(loop.notices(self.project, "S1"), [])
        self.assertEqual(self.kinds("S1")["context_rotate"], [False])  # said between passes: the pass informs

    def test_a_pass_submitted_then_idle(self):
        self.cli("session", "S1")
        self.measure(1200)  # below the safety trigger: the tick renews nothing by itself
        for want in ("submit", "log"):
            self.old_beat()
            self.assertIn(want, self.tick())
        self.assertEqual(self.kinds("S1", mark=False)["context_rotate"], [False])
        # between passes through the tick: a new case (free -> busy) is said once
        self.agent("a1", "zed7", session="S1")
        self.assertIn("notices", self.tick())
        self.assertNotIn("notices", self.tick())

    def test_cli_loop_uses_the_marked_session(self):
        self.measure(1500)
        self.agent("a0", "old1", session="S0")  # an agent of an earlier session
        r = self.cli("loop", "--json")
        self.assertNotIn("context_rotate", [n["kind"] for n in json.loads(r.stdout)])  # no session marked
        self.cli("session", "S1")
        r = self.cli("loop", "--json")
        self.assertEqual(r.returncode, 0, r.stderr)
        k = {n["kind"]: n["actionable"] for n in json.loads(r.stdout)}
        self.assertEqual((k["context_rotate"], k["dead_agents"]), (True, True))
        self.assertFalse((self.project.state / "context-rotate-notified").exists())  # looking does not mark

    def test_session_shows_the_measure(self):
        self.cli("session", "S1")
        self.assertIn("context unknown (no measure from foreman's hooks module", self.cli("session").stdout)
        self.measure(1500, window=2000)
        r = self.cli("session")
        self.assertRegex(r.stdout, r"\n  context 1500 tokens \(1K, 75% of 2K, at \d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\)\n")


class IdentityPassTest(NoticesCase):
    """The pass reads the identity recorded at launch (or by hand) first and the old
    inference from the brief only for agents without one."""

    def entry(self, aid, brief, session="S1", **fields):
        """A live agent in agents.json with a transcript whose first message is `brief`, plus `fields`."""
        t = self.tmp / session / f"{aid}.jsonl"
        t.parent.mkdir(exist_ok=True)
        t.write_text(json.dumps({"message": {"role": "user", "content": brief}}, ensure_ascii=False) + "\n")
        now = datetime.now().isoformat(timespec="seconds")
        f = loop.agents_file(self.project)
        d = util.read_json(f, {})
        d[aid] = {"start": now, "activity": now, "type": "general-purpose", "transcript": str(t), **fields}
        f.parent.mkdir(parents=True, exist_ok=True)
        util.write_json(f, d)

    def worktree(self, branch):
        wt = self.tmp / "wt" / branch.replace("/", "-")
        git(self.tmp / "app", "worktree", "add", "-q", "-b", branch, str(wt))
        return wt

    def notice_of(self, kind, mark=True):
        return next((n for n in self.notices("S1", mark) if n["kind"] == kind), None)

    def test_the_recorded_identity_wins(self):
        """Criterion 4: zed7 launched with `foreman launch`, a Spanish brief with neither a name nor «Issue:»,
        and a sub-issue with the branch but no agent."""
        self.issue("AB-1", "in progress", branch="ab1/x")
        wt = self.worktree("ab1/x")
        self.entry(
            "ag1",
            "[foreman launch abcdefghij] zed7 · AB-1 T1 · branch ab1/x\nSigue las normas y haz T1.",
            name="zed7",
            issue="AB-1",
            sub="T1",
            repo="app",
            branch="ab1/x",
            worktree=str(wt),
            production=True,
            launch="abcdefghij",
            named_by="launch",
        )
        k = self.kinds("S1")
        self.assertNotIn("ready_to_integrate", k)
        self.assertNotIn("orphan_in_progress", k)
        self.assertNotIn("nameless_agents", k)
        self.assertNotIn("wip_production", k)  # zed7 fills the production slot (general-purpose, but recorded so)
        ident = json.loads(loop.agents_json(self.project))["ag1"]["identity"]
        self.assertEqual(
            (ident["name"], ident["issue"], ident["branch"], ident["production"], ident["registered"], ident["source"]),
            ("zed7", "AB-1", "ab1/x", True, True, "launch"),
        )
        self.cli("agents", "--stop", "ag1")
        n = self.notice_of("ready_to_integrate")
        self.assertIn("app:ab1/x", n["text"])  # once it is no longer live, the branch is ready

    def test_a_live_agent_with_no_name(self):
        """A production agent launched without the launch line; its sub-issue names it (c6) with the branch. Its
        branch is not ready (the brief names its worktree) and the pass says it has no name, once per set."""
        self.issue("AB-1", "in progress", agent="c6", branch="feat/x")
        wt = self.worktree("feat/x")
        self.worktree("feat/x2")  # a folder whose name starts like the agent's: not cited by it
        self.issue("AB-2", "in progress", branch="feat/x2")
        self.entry("a14a8c99", f"Sigue las normas. Trabaja en {wt}. Issue: AB-1 T1.", type="foreman:dev")
        ns = self.notices("S1")
        ready = next(n for n in ns if n["kind"] == "ready_to_integrate")
        self.assertNotIn("app:feat/x ", ready["text"] + " ")
        self.assertNotIn("app:feat/x;", ready["text"])
        self.assertIn("app:feat/x2", ready["text"])
        nameless = next(n for n in ns if n["kind"] == "nameless_agents")
        self.assertTrue(nameless["actionable"])
        self.assertIn("foreman:dev a14a8c", nameless["text"])
        self.assertIn("without `foreman launch` (no launch line", nameless["text"])
        self.assertIn("foreman agents --name ID NAME", nameless["text"])
        self.assertNotIn("wip_production", [n["kind"] for n in ns])  # foreman:dev counts for production_types dev
        self.assertEqual(self.kinds("S1")["nameless_agents"], [False])  # said once for this set
        self.entry("b22b0000", "Otro agente sin nombre.", type="dev")
        self.assertEqual(self.kinds("S1")["nameless_agents"], [True])  # a new set
        self.cli("agents", "--name", "a14a8c", "c6", "--issue", "AB-1", "--branch", "feat/x")
        n = self.notice_of("nameless_agents")
        self.assertNotIn("a14a8c", n["text"])
        self.assertTrue(n["actionable"])
        self.cli("agents", "--stop", "b22b0000")
        k = self.kinds("S1")
        self.assertNotIn("nameless_agents", k)
        self.assertNotIn("app:feat/x;", self.notice_of("ready_to_integrate")["text"] + ";")

    def test_a_name_in_the_brief_names_nobody(self):
        """The name phrase is gone: «your name is c6» in a brief without the launch line leaves the agent
        nameless (the launch line, or `foreman agents --name`, is the way an agent gets its name)."""
        self.issue("AB-1", "in progress", agent="c6", branch="feat/x")
        self.worktree("feat/x")
        self.entry("a1", "Read the rules; your name is c6. Issue: AB-1 T1.", type="foreman:dev")
        ident = json.loads(loop.agents_json(self.project))["a1"]["identity"]
        self.assertEqual((ident["name"], ident["issue"], ident["source"]), (None, "AB-1", None))
        self.assertIn("nameless_agents", self.kinds("S1"))

    def test_a_cited_worktree_only_counts_for_agents_without_an_identity(self):
        """O6: a planner launched with `foreman launch` that reads a dev's worktree does not keep its branch from
        being ready; the same brief without an identity does."""
        self.issue("AB-1", "in progress", branch="ab1/x")
        wt = self.worktree("ab1/x")
        brief = f"Read the branch in {wt} and plan AB-9."
        self.entry(
            "p1",
            "[foreman launch bcdefghijk] pl1 · AB-9\n" + brief,
            type="foreman:planner",
            name="pl1",
            issue="AB-9",
            production=False,
            launch="bcdefghijk",
            named_by="launch",
        )
        self.assertIn("app:ab1/x", self.notice_of("ready_to_integrate")["text"])
        self.entry("p2", brief, type="foreman:planner")
        self.assertNotIn("ready_to_integrate", self.kinds("S1"))

    def test_is_production(self):
        self.write_toml(
            TOML.replace("ready_target = 1\n", 'ready_target = 1\nproduction_types = ["builder"]\n')
            .replace("{loop_extra}", "disk_low_gb = 0")
            .replace("{extra}", "")
        )
        p = self.project
        self.assertTrue(identity.is_production(p, {"type": "foreman:builder"}, ""))  # a plugin-qualified type
        self.assertTrue(identity.is_production(p, {"type": "builder"}, ""))
        self.assertTrue(identity.is_production(p, {"type": "foreman:dev"}, ""))
        self.assertFalse(identity.is_production(p, {"type": "foreman:planner"}, ""))
        self.assertFalse(identity.is_production(p, {"type": "foreman:dev", "production": False}, ""))
        self.assertTrue(identity.is_production(p, {"type": "Explore", "production": True}, ""))
        self.assertTrue(identity.is_production(p, {"type": "general-purpose"}, f"work in {self.tmp}/wt/app-x"))

    def test_cites_path(self):
        from foreman import identity

        self.assertTrue(identity.cites_path("work in /a/wt/app-x.", "/a/wt/app-x"))
        self.assertTrue(identity.cites_path("work in /a/wt/app-x/lib", "/a/wt/app-x/"))
        self.assertFalse(identity.cites_path("work in /a/wt/app-x2", "/a/wt/app-x"))
        self.assertFalse(identity.cites_path("work in /a/wt/app-x.old", "/a/wt/app-x"))
        home = str(Path.home())
        self.assertTrue(identity.cites_path("cd ~/wt/app-x", f"{home}/wt/app-x"))
        self.assertFalse(identity.cites_path("anything", ""))


class DeliveredTest(NoticesCase):
    """The delivery is a datum. `foreman progress --done` (seen by the agents hook in the agent's own Bash
    PostToolUse) makes an agent `delivered` at once; a SubagentStop without it (the agent stops to wait for a
    background task of its own) leaves it `waiting` and live, 3 h at most; a resumed agent is live again. Every event
    goes through `foreman hook agents`, as Claude Code sends it."""

    DONE = 'FOREMAN_WHO=zed7 bin/foreman --project fake progress --done "ab1/x: tests green"'

    def setUp(self):
        super().setUp()
        self.issue("AB-1", "in progress", agent="zed7", branch="ab1/x")
        self.wt = self.worktree("ab1/x")

    def worktree(self, branch):
        wt = self.tmp / "wt" / branch.replace("/", "-")
        git(self.tmp / "app", "worktree", "add", "-q", "-b", branch, str(wt))
        return wt

    def send(self, name, aid="d1", **kw):
        t = self.tmp / "S1" / f"{aid}.jsonl"
        if not t.exists():
            t.parent.mkdir(exist_ok=True)
            line = self.cli("launch", "zed7", "--issue", "AB-1", "--no-backlog").stdout.strip().splitlines()[-1]
            t.write_text(
                json.dumps(
                    {
                        "text": f"{line}\nFollow docs/agent.md. Issue: AB-1 T1. "
                        f"Branch `ab1/x` in the worktree `{self.wt}`."
                    }
                )
                + "\n"
            )
        ev = {"hook_event_name": name, "agent_id": aid, "agent_type": "dev", "agent_transcript_path": str(t), **kw}
        r = self.hook("agents", ev)
        self.assertEqual((r.returncode, r.stdout), (0, ""), r.stderr)

    def bash(self, cmd, aid="d1"):
        self.send("PostToolUse", aid, tool_name="Bash", tool_input={"command": cmd})

    def state(self, aid="d1"):
        return loop.agent_state(loop.agents(self.project)[aid])

    def live(self):
        return [h["id"] for h in loop.live_agents(self.project)]

    def panel_status(self, aid="d1"):
        from foreman import panel

        return {a["id"]: a["status"] for a in panel.agents(self.project, [], {})}.get(aid)

    def age(self, aid="d1", **minutes):
        """Moves the entry's timestamps back (key=minutes ago); `activity` moves its transcript's time too."""
        f = loop.agents_file(self.project)
        d = util.read_json(f, {})
        for k, m in minutes.items():
            d[aid][k] = (datetime.now() - timedelta(minutes=m)).isoformat(timespec="seconds")
        util.write_json(f, d)
        if "activity" in minutes:
            os.utime(d[aid]["transcript"], (time.time() - 60 * minutes["activity"],) * 2)

    def test_done_then_stop_is_delivered_at_once(self):
        self.send("SubagentStart")
        self.bash("ls")
        self.assertEqual((self.state(), self.live()), ("working", ["d1"]))
        k = self.kinds()
        self.assertNotIn("wip_production", k)  # production 1/1
        self.assertNotIn("ready_to_integrate", k)  # its agent is live
        self.bash(self.DONE)
        self.assertEqual(self.state(), "delivered")  # before its SubagentStop already
        self.send("SubagentStop", last_assistant_message="Delivered.")
        self.assertEqual((self.state(), self.live()), ("delivered", []))
        ns = {n["kind"]: n for n in self.notices()}
        self.assertTrue(ns["wip_production"]["key"].startswith("wip_production:0/1"))
        self.assertIn("app:ab1/x", ns["ready_to_integrate"]["text"])
        self.assertNotIn("orphan_in_progress", ns)  # its branch is there
        self.age(activity=60, start=90)
        self.assertEqual(loop.stalled(self.project), [])
        self.assertEqual(self.panel_status(), "finished")
        out = json.loads(self.cli("agents", "--json").stdout)
        self.assertEqual((out["d1"]["state"], out["d1"]["name"]), ("delivered", "zed7"))
        # after 15 min the panel no longer shows it; the loop still does not count it
        self.age(end=20, delivered=21)
        self.assertIsNone(self.panel_status())
        self.assertEqual(self.live(), [])

    def test_stalled_is_one_rule_for_loop_and_panel(self):
        """The loop's stalled_agents and the panel's «maybe stuck» read the same last sign of life, the
        latest of the recorded activity and the transcript's time: a transcript that advances without a new tool call
        (the agent writes, thinks or waits for a reply) is not stalled, in either."""
        self.send("SubagentStart")
        self.bash("ls")
        self.age(activity=60, start=90)
        self.assertEqual((loop.stalled(self.project), self.panel_status()), (["zed7 (60 min)"], "maybe stuck"))
        t = loop.agents(self.project)["d1"]["transcript"]
        with open(t, "a") as f:
            f.write('{"text": "still thinking"}\n')  # the transcript advances, `activity` does not
        self.assertEqual(
            loop.agents(self.project)["d1"]["activity"][:16], (datetime.now() - timedelta(minutes=60)).isoformat()[:16]
        )
        self.assertEqual((loop.stalled(self.project), self.panel_status()), ([], "working"))
        os.utime(t, (time.time() - 25 * 60,) * 2)  # quiet again for 25 min: stalled in both, counted from there
        self.assertEqual((loop.stalled(self.project), self.panel_status()), (["zed7 (25 min)"], "maybe stuck"))
        # waiting for a background task of its own that long: the same rule, in both
        self.send("SubagentStop", last_assistant_message="Waiting for the selftest in the background.")
        os.utime(t, (time.time() - 25 * 60,) * 2)
        self.age(activity=25)
        self.assertEqual(self.state(), "waiting")
        self.assertEqual((loop.stalled(self.project), self.panel_status()), (["zed7 (25 min)"], "maybe stuck"))

    def test_stop_without_done_is_waiting_for_3_hours(self):
        """An agent that stops to wait for a background job of its own (SubagentStop with no --done) stays live."""
        self.send("SubagentStart")
        self.bash("foreman time-limit 600 bin/foreman selftest > /tmp/x.log 2>&1 &")
        self.send("SubagentStop", last_assistant_message="Waiting for the selftest in the background.")
        self.assertEqual((self.state(), self.live()), ("waiting", ["d1"]))
        k = self.kinds()
        self.assertNotIn("ready_to_integrate", k)
        self.assertNotIn("wip_production", k)
        self.assertEqual(self.panel_status(), "waiting for its task")
        self.age(activity=60, start=90)
        self.assertEqual(loop.stalled(self.project), ["zed7 (60 min)"])  # idle without a lock: said, as before
        # its notification resumes it: working again
        self.bash("tail -5 /tmp/x.log")
        self.assertEqual(self.state(), "working")
        # stopped without delivering more than 3 h ago: the last resort
        self.send("SubagentStop")
        self.age(end=181)
        self.assertEqual((self.state(), self.live()), ("stopped", []))
        self.assertIsNone(self.panel_status())
        self.assertIn("ready_to_integrate", self.kinds())

    def test_resumed_after_delivering_is_live_until_it_delivers_again(self):
        self.send("SubagentStart")
        self.bash(self.DONE)
        self.send("SubagentStop")
        self.assertEqual(self.state(), "delivered")
        self.bash("git status")  # resumed (SendMessage, or a notification of its task)
        h = loop.agents(self.project)["d1"]
        self.assertNotIn("end", h)
        self.assertNotIn("delivered", h)
        self.assertEqual((self.state(), self.live()), ("working", ["d1"]))
        self.assertEqual(self.panel_status(), "working")
        self.send("SubagentStop")  # stops again without delivering: waiting
        self.assertEqual(self.state(), "waiting")
        self.bash("ls")  # tools after a resume and before --done keep it working
        self.bash(f"cd {self.wt} && {self.DONE}")
        self.bash("git log -1")  # a tool after --done, before its SubagentStop: still delivered
        self.assertEqual(self.state(), "delivered")
        self.send("SubagentStop")
        self.assertEqual((self.state(), self.live()), ("delivered", []))
        self.assertEqual(list(loop.agents(self.project)), ["d1"])  # two deliveries of one task: one agent
        self.assertIn("ready_to_integrate", self.kinds())

    def test_stop_by_hand_and_what_is_not_a_delivery(self):
        self.send("SubagentStart")
        for cmd in (
            'foreman progress "almost --done"',
            'foreman note "t" "foreman progress --done x"',
            'git commit -m "foreman progress --done"',
            "echo foreman progress --done",
        ):
            self.bash(cmd)
            self.assertEqual(self.state(), "working", cmd)
        self.send("PostToolUse", tool_name="Write", tool_input={"command": self.DONE})
        self.send("PostToolUse", tool_name="Bash", tool_input="not a dict")
        self.assertEqual(self.state(), "working")
        self.assertEqual(loop.stop_agent(self.project, "zed7"), ["d1"])
        self.assertEqual((self.state(), self.live()), ("stopped", []))
        self.assertEqual(json.loads(self.cli("agents", "--json").stdout)["d1"]["state"], "stopped")
        self.assertEqual(self.panel_status(), "finished")
        # the delivery line itself: a progress line for the owner, marked done
        r = self.cli("progress", "--done", "ab1/x: tests green", env={"FOREMAN_WHO": "zed7"})
        self.assertEqual(r.returncode, 0, r.stderr)
        last = json.loads((self.project.state / "progress.jsonl").read_text().splitlines()[-1])
        self.assertEqual((last["agent"], last["text"], last.get("done")), ("zed7", "ab1/x: tests green", True))
        self.cli("progress", "plain")
        self.assertNotIn("done", json.loads((self.project.state / "progress.jsonl").read_text().splitlines()[-1]))

    def test_agent_state(self):
        now = time.time()
        old = datetime.fromtimestamp(now - 4 * 3600).isoformat(timespec="seconds")
        new = datetime.fromtimestamp(now - 60).isoformat(timespec="seconds")
        for h, want in (
            ({}, "working"),
            ({"end": new}, "waiting"),
            ({"end": old}, "stopped"),
            ({"end": new, "delivered": new}, "delivered"),
            ({"delivered": new}, "delivered"),
            ({"end": old, "delivered": old}, "delivered"),
            ({"stopped": True, "delivered": new}, "stopped"),
            ({"tool": "SubagentHandback", "end": new}, "waiting"),
        ):
            self.assertEqual(loop.agent_state(h, now), want, h)

    def test_a_report_set_on_the_sub_issue_is_the_branch_report(self):
        """The acceptance criterion: a delivered branch whose sub-issue names its report (a file that does not cite
        the branch) is ready with that report, not «no report»."""
        self.send("SubagentStart")
        self.bash(self.DONE)
        self.send("SubagentStop", last_assistant_message="Delivered.")
        self.assertIn("(no report: ask for it first", self.notice_of_ready())
        (self.home / "reviews").mkdir()
        (self.home / "reviews/2026-11-01-ab1.md").write_text("# Report\n")
        with self.b.mutate() as d:
            self.b.set_sub(d, "AB-1", "T1", report="reviews/2026-11-01-ab1.md")
        text = self.notice_of_ready()
        self.assertIn("app:ab1/x (reviews/2026-11-01-ab1.md; outcome ok)", text)
        self.assertNotIn("no report", text)

    def test_the_report_named_at_delivery(self):
        """`progress --done --report`: the progress line and the agent's entry keep it (relative to the home), the
        pass shows it with no field on the sub-issue, a resume clears it, and a named report that exists nowhere is
        said as such."""
        report = self.home / "reviews/2026-11-01-result.md"  # names no key and cites no branch: only --report finds it
        report.parent.mkdir()
        report.write_text("# Result\n")
        self.send("SubagentStart")
        r = self.cli("progress", "--done", "ab1/x: tests green", "--report", str(report), env={"FOREMAN_WHO": "zed7"})
        self.assertEqual(r.returncode, 0, r.stderr)
        last = json.loads((self.project.state / "progress.jsonl").read_text().splitlines()[-1])
        self.assertEqual((last["done"], last["report"]), (True, "reviews/2026-11-01-result.md"))
        self.bash(f"{self.DONE} --outcome partial --report {report}")
        self.send("SubagentStop", last_assistant_message="Delivered.")
        h = loop.agents(self.project)["d1"]
        self.assertEqual((h["outcome"], h["report"]), ("partial", "reviews/2026-11-01-result.md"))
        self.assertIsNone(self.b.issue(self.b.load(), "AB-1")["subs"][0].get("report"))
        self.assertIn("app:ab1/x (reviews/2026-11-01-result.md; outcome partial)", self.notice_of_ready())
        self.bash("git status")  # resumed: working again, the delivery and its report go
        self.assertNotIn("report", loop.agents(self.project)["d1"])
        self.bash(self.DONE + " --report=reviews/nope.md")
        self.send("SubagentStop")
        self.assertIn(
            "app:ab1/x (report reviews/nope.md not found: fix the sub-issue's --report", self.notice_of_ready()
        )

    def test_a_named_report_is_checked_before_anything_is_written(self):
        """--report outside the prefixes, missing, outside the home or without --done: exit 2, no line written."""
        (self.home / "notes").mkdir(exist_ok=True)
        (self.home / "notes/x.md").write_text("x\n")
        outside = self.tmp / "elsewhere.md"
        outside.write_text("x\n")
        for args, said in (
            (["--done", "x", "--report", "notes/x.md"], "must be a path under reviews/"),
            (["--done", "x", "--report", "reviews/nope.md"], "reviews/nope.md does not exist"),
            (["--done", "x", "--report", str(outside)], "is not inside the home"),
            (["--done", "x", "--report", "../reviews/x.md"], "is not inside the home"),
            (["x", "--report", "reviews/x.md"], "--report goes with --done"),
        ):
            r = self.cli("progress", *args, env={"FOREMAN_WHO": "zed7"})
            self.assertEqual(r.returncode, 2, args)
            self.assertIn(said, r.stderr, args)
            self.assertFalse((self.project.state / "progress.jsonl").exists(), args)

    def notice_of_ready(self):
        return next(n for n in self.notices() if n["kind"] == "ready_to_integrate")["text"]


class MeasureBranchesTest(NoticesCase):
    """Measurement branches: five worktrees with no live agent. Code is ready to integrate (text as before); a
    measurement, by its sub-issue's kind or by the delivery's `--kind measure`, is `measure_delivered`; a branch whose
    sub-issues are all closed without a commit is in neither; a single open code sub-issue makes the branch code."""

    def setUp(self):
        super().setUp()
        with self.b.mutate() as d:
            for iid in ("AB-1", "AB-2", "AB-3", "AB-4", "AB-5"):
                self.b.add_issue(d, "E1", iid, "an issue", status="in progress")
            self.b.set_sub(d, "AB-1", "T1", status="▶", agent="c1", branch="ab1/code")  # a
            self.b.set_sub(d, "AB-2", "T1", status="▶", agent="m2", branch="ab2/measure", kind="measure")  # b
            self.b.set_sub(d, "AB-3", "T1", status="▶", agent="m3", branch="ab3/said")  # c
            self.b.set_sub(d, "AB-4", "T1", status="done", agent="d4", branch="ab4/closed")  # d
            self.b.set_sub(d, "AB-5", "T1", status="▶", agent="e5", branch="ab5/mixed")  # e
            self.b.set_sub(d, "AB-5", "T2", status="▶", agent="e5", branch="ab5/mixed", kind="measure")
        for br in ("ab1/code", "ab2/measure", "ab3/said", "ab4/closed", "ab5/mixed"):
            git(self.tmp / "app", "worktree", "add", "-q", "-b", br, str(self.tmp / "wt" / br.replace("/", "-")))
        util.write_json(
            loop.agents_file(self.project),
            {"a3": {"name": "m3", "branch": "ab3/said", "delivered": "2026-11-01T10:00:00", "kind": "measure"}},
        )

    def test_the_five_cases(self):
        ns = {n["kind"]: n for n in self.notices(mark=False)}
        ready = ns["ready_to_integrate"]
        self.assertTrue(ready["actionable"])
        self.assertEqual(
            ready["text"],
            "Ready to integrate (no live agent): app:ab1/code (no report: ask for it first); "
            "app:ab5/mixed (no report: ask for it first)",
        )
        self.assertEqual(ready["key"], "ready_to_integrate:app:ab1/code;app:ab5/mixed")
        m = ns["measure_delivered"]
        self.assertTrue(m["actionable"])
        self.assertTrue(
            m["text"].startswith(
                "Measurements delivered (nothing to merge): app:ab2/measure (no report: ask for it first); "
                "app:ab3/said (no report: ask for it first; outcome ok): read the report, then close it with "
                '`foreman integrate REPO BRANCH "ID Tn" --measure --result "…"`'
            ),
            m["text"],
        )
        self.assertIn("`--keep-branch` keeps the branch", m["text"])
        self.assertEqual(m["key"], "measure_delivered:app:ab2/measure;app:ab3/said")
        self.assertNotIn("ab4/closed", ready["text"] + m["text"])
        self.assertNotIn("loose_worktrees", ns)

    def test_a_report_and_outcome_as_in_ready_lines(self):
        (self.home / "reviews").mkdir(exist_ok=True)
        (self.home / "reviews/m2.md").write_text("figures\n")
        with self.b.mutate() as d:
            self.b.set_sub(d, "AB-2", "T1", report="reviews/m2.md")
        util.write_json(
            loop.agents_file(self.project),
            {"a2": {"name": "m2", "branch": "ab2/measure", "delivered": "2026-11-01T10:00:00", "outcome": "partial"}},
        )
        m = next(n for n in self.notices(mark=False) if n["kind"] == "measure_delivered")
        self.assertIn("app:ab2/measure (reviews/m2.md; outcome partial)", m["text"])
        self.assertNotIn("ab3/said", m["text"])  # its delivery no longer says --kind measure: code

    def test_a_closed_branch_with_a_commit_is_code(self):
        with self.b.mutate() as d:
            self.b.set_sub(d, "AB-4", "T1", commit="abc1234")
        ready = next(n for n in self.notices(mark=False) if n["kind"] == "ready_to_integrate")
        self.assertIn("app:ab4/closed", ready["text"])


def committed_branch(repo, branch, wt, files=None):
    """A local branch with one commit and no worktree left (made in one, then removed, as a lost worktree)."""
    git(repo, "worktree", "add", "-q", "-b", branch, str(wt))
    for name, text in (files or {f"{branch.replace('/', '-')}.txt": f"{branch}\n"}).items():
        (wt / name).write_text(text)
    git(wt, "add", "-A")
    git(wt, "commit", "-q", "-m", f"work on {branch}")
    git(repo, "worktree", "remove", "--force", str(wt))


class BranchNoWorktreeTest(NoticesCase):
    """Branches named by the backlog with no worktree: an open sub-issue with commits not in main and no live agent is
    `branch_no_worktree` (measurement or code), and its issue is not `orphan_in_progress`; every sub-issue closed,
    with or without a commit, is silent; with a worktree, everything as before."""

    def setUp(self):
        super().setUp()
        app = self.tmp / "app"
        with self.b.mutate() as d:
            for iid in ("AB-1", "AB-2", "AB-3", "AB-4", "AB-5", "AB-6", "AB-7"):
                self.b.add_issue(d, "E1", iid, "an issue", status="in progress")
            self.b.set_sub(d, "AB-1", "T1", status="▶", agent="c1", branch="ab1/code")  # a
            self.b.set_sub(d, "AB-2", "T1", status="▶", agent="m2", branch="ab2/measure", kind="measure")  # b
            self.b.set_sub(d, "AB-3", "T1", status="done", agent="k3", branch="ab3/kept")  # c
            self.b.set_sub(d, "AB-4", "T1", status="canceled", agent="s4", branch="ab4/squashed", commit="abc1234")  # d
            self.b.set_sub(d, "AB-5", "T1", status="▶", agent="w5", branch="ab5/worktree")  # j
            self.b.set_sub(d, "AB-6", "T1", status="▶", agent="e6", branch="ab6/empty")  # no work: orphan as today
            self.b.set_sub(d, "AB-7", "T1", status="▶", agent="r7", branch="ab7/merged")  # ancestor of main
        for br in ("ab1/code", "ab2/measure", "ab3/kept", "ab4/squashed", "ab7/merged"):
            committed_branch(app, br, self.tmp / "wt" / br.replace("/", "-"))
        git(app, "merge", "-q", "--ff-only", "ab7/merged")
        git(app, "branch", "ab6/empty")
        git(app, "worktree", "add", "-q", "-b", "ab5/worktree", str(self.tmp / "wt" / "ab5-worktree"))

    def test_the_cases(self):
        ns = {n["kind"]: n for n in self.notices(mark=False)}
        n = ns["branch_no_worktree"]
        self.assertTrue(n["actionable"])
        self.assertTrue(
            n["text"].startswith(
                "Unmerged branches of open sub-issues with no worktree: "
                "app:ab1/code (AB-1 T1; no report: ask for it first; 1 commit not in main); "
                "app:ab2/measure (AB-2 T1, measurement; no report: ask for it first; 1 commit not in main): "
                "integrate needs a worktree: `foreman worktree REPO NAME BRANCH` (it reuses the branch)"
            ),
            n["text"],
        )
        self.assertIn("--measure", n["text"])
        self.assertEqual(n["key"], "branch_no_worktree:app:ab1/code;app:ab2/measure")
        orphans = ns["orphan_in_progress"]["text"]
        self.assertNotIn("AB-1", orphans)
        self.assertNotIn("AB-2", orphans)
        self.assertIn("AB-6", orphans)  # a branch with nothing in it is no work
        self.assertIn("AB-7", orphans)  # nor one already in main
        self.assertEqual(
            ns["ready_to_integrate"]["text"],
            "Ready to integrate (no live agent): app:ab5/worktree (no report: ask for it first)",
        )
        self.assertNotIn("measure_delivered", ns)
        self.assertNotIn("loose_worktrees", ns)
        self.assertNotIn("loose_branches", ns)  # c and d: named by closed sub-issues

    def test_a_live_agent_keeps_it_quiet(self):
        util.write_json(loop.agents_file(self.project), {"a1": {"name": "c1", "branch": "ab1/code"}})
        self.agent("a1", "c1")
        n = next(n for n in self.notices(mark=False) if n["kind"] == "branch_no_worktree")
        self.assertNotIn("ab1/code", n["text"])
        self.assertIn("ab2/measure", n["text"])

    def test_the_report_and_the_commits(self):
        app = self.tmp / "app"
        wt = self.tmp / "wt" / "again"
        git(app, "worktree", "add", "-q", str(wt), "ab1/code")
        (wt / "two.txt").write_text("2\n")
        git(wt, "add", "-A")
        git(wt, "commit", "-q", "-m", "more")
        git(app, "worktree", "remove", "--force", str(wt))
        (self.home / "reviews").mkdir(exist_ok=True)
        (self.home / "reviews/c1.md").write_text("done\n")
        with self.b.mutate() as d:
            self.b.set_sub(d, "AB-1", "T1", report="reviews/c1.md")
        n = next(n for n in self.notices(mark=False) if n["kind"] == "branch_no_worktree")
        self.assertIn("app:ab1/code (AB-1 T1; reviews/c1.md; 2 commits not in main)", n["text"])

    def test_closing_the_sub_issue_silences_it(self):
        with self.b.mutate() as d:
            self.b.set_sub(d, "AB-1", "T1", status="canceled")
            self.b.set_sub(d, "AB-2", "T1", status="done")
        self.assertNotIn("branch_no_worktree", self.kinds(mark=False))


class LooseBranchesTest(NoticesCase):
    """Local branches with no worktree that no sub-issue names: `loose_branches` when their work is only here and not
    in main; silent when the backlog's text mentions them, when main has their content (cherry-picked or squashed),
    when they are merged (an ancestor of main) or when the remote branch of the same name has their tip (only for a
    repo with a remote: `remote = "none"` never looks at remote refs)."""

    def setUp(self):
        super().setUp()
        self.app = self.tmp / "app"
        self.lib = self.tmp / "lib"
        make_repo(self.lib)
        git(self.tmp, "init", "-q", "--bare", "-b", "main", str(self.tmp / "lib.git"))
        git(self.lib, "remote", "add", "origin", str(self.tmp / "lib.git"))
        git(self.lib, "push", "-q", "origin", "main")
        self.config(extra=f'\n[repos.lib]\npath = "{self.lib}"\nremote = "origin"\n')
        self.wt = self.tmp / "wt"

    def branch(self, repo, br, files=None):
        committed_branch(repo, br, self.wt / br.replace("/", "-"), files)

    def loose(self):
        return next((n for n in self.notices(mark=False) if n["kind"] == "loose_branches"), None)

    def test_only_the_work_that_is_only_here(self):
        git(self.app, "branch", "release")  # h: an ancestor of main
        self.branch(self.app, "fix/x")  # e
        self.branch(self.app, "pick/y")  # f: cherry-picked into main
        git(self.app, "cherry-pick", "pick/y")
        self.branch(self.app, "sq/z", {"a.txt": "a\n"})  # f: two commits squashed into one in main
        wt = self.wt / "sq-z-2"
        git(self.app, "worktree", "add", "-q", str(wt), "sq/z")
        (wt / "b.txt").write_text("b\n")
        git(wt, "add", "-A")
        git(wt, "commit", "-q", "-m", "second")
        git(self.app, "worktree", "remove", "--force", str(wt))
        git(self.app, "merge", "-q", "--squash", "sq/z")
        git(self.app, "commit", "-q", "-m", "squashed")
        self.branch(self.app, "kept/v")  # i: the backlog's text keeps it
        with self.b.mutate() as d:
            self.b.add_issue(d, "E1", "AB-1", "the old attempt is kept in kept/v", status="ready")
        self.branch(self.lib, "pushed/w")  # g: on the remote
        git(self.lib, "push", "-q", "origin", "pushed/w")
        self.branch(self.lib, "ahead/u")  # pushed, then more work here
        git(self.lib, "push", "-q", "origin", "ahead/u")
        wt = self.wt / "ahead-u-2"
        git(self.lib, "worktree", "add", "-q", str(wt), "ahead/u")
        (wt / "more.txt").write_text("more\n")
        git(wt, "add", "-A")
        git(wt, "commit", "-q", "-m", "a local fix of the copy")
        git(self.lib, "worktree", "remove", "--force", str(wt))
        n = self.loose()
        self.assertTrue(n["actionable"])
        self.assertTrue(
            n["text"].startswith(
                "Unmerged branches with no worktree that the backlog does not name: "
                'app:fix/x (1 commit not in main, 0 days, "work on fix/x"); '
                'lib:ahead/u (2 commits not in main, 0 days, "a local fix of the copy"): '
                "note them in their sub-issue"
            ),
            n["text"],
        )
        self.assertIn("Ask Owner before deleting one", n["text"])
        self.assertEqual(n["key"], "loose_branches:app:fix/x;lib:ahead/u")
        self.assertNotIn("branch_no_worktree", self.kinds(mark=False))

    def test_a_repo_without_a_remote_never_reads_remote_refs(self):
        self.branch(self.app, "fix/x")
        git(self.app, "update-ref", "refs/remotes/origin/fix/x", "fix/x")  # left by hand: remote = "none"
        self.assertIn("app:fix/x", self.loose()["text"])
        self.branch(self.lib, "fix/l")
        git(self.lib, "update-ref", "refs/remotes/origin/fix/l", "fix/l")  # as a fetch leaves it
        self.assertNotIn("lib:fix/l", self.loose()["text"])

    def test_a_sub_issue_naming_it_takes_it(self):
        self.branch(self.app, "fix/x")
        self.assertIsNotNone(self.loose())
        self.issue("AB-1", "in progress", agent="c1", branch="fix/x")
        ns = {n["kind"]: n for n in self.notices(mark=False)}
        self.assertNotIn("loose_branches", ns)
        self.assertIn("app:fix/x (AB-1 T1;", ns["branch_no_worktree"]["text"])

    def test_with_a_worktree_it_is_loose_worktrees_as_before(self):
        git(self.app, "worktree", "add", "-q", "-b", "tmp/w", str(self.wt / "tmp-w"))
        (self.wt / "tmp-w" / "w.txt").write_text("w\n")
        git(self.wt / "tmp-w", "add", "-A")
        git(self.wt / "tmp-w", "commit", "-q", "-m", "w")
        k = self.kinds(mark=False)
        self.assertEqual(k["loose_worktrees"], [True])
        self.assertNotIn("loose_branches", k)


class BranchReportTest(NoticesCase):
    """The report of a ready branch (delivery.branch_report, the rule the delivery check uses too): the sub-issue's
    `report` first (in the home, or added by the branch), then what the branch adds under `[ids] report_prefixes`,
    then the home's reports under every prefix."""

    def setUp(self):
        super().setUp()
        self.issue("AB-1", "in progress", agent="zed7", branch="ab1/x")
        self.wt = self.tmp / "wt" / "ab1-x"
        git(self.tmp / "app", "worktree", "add", "-q", "-b", "ab1/x", str(self.wt))

    def ready(self):
        return next(n for n in self.notices(mark=False) if n["kind"] == "ready_to_integrate")["text"]

    def write(self, rel, text="# Report\n"):
        f = self.home / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(text)
        return f

    def set_report(self, path):
        with self.b.mutate() as d:
            self.b.set_sub(d, "AB-1", "T1", report=path)

    def test_nothing_asks_for_it(self):
        self.assertIn("app:ab1/x (no report: ask for it first)", self.ready())

    def test_no_report_prefixes_asks_for_none(self):
        self.config(extra="\n[ids]\nreport_prefixes = []")
        self.assertEqual(self.ready(), "Ready to integrate (no live agent): app:ab1/x")

    def test_outside_the_reviews_folder(self):
        self.config(extra='\n[ids]\nreport_prefixes = ["reviews/", "bench/"]')
        self.write("bench/lab/x.md")  # names nothing: only the sub-issue makes it the report
        self.assertIn("(no report: ask for it first)", self.ready())
        self.set_report("bench/lab/x.md")
        self.assertIn("app:ab1/x (bench/lab/x.md)", self.ready())

    def test_outside_the_reviews_folder_without_the_field(self):
        self.config(extra='\n[ids]\nreport_prefixes = ["reviews/", "bench/"]')
        self.write("bench/lab/2026-10-31-ab-1-x.md", "Branch ab1/x: measured.\n")
        self.assertIn("app:ab1/x (bench/lab/2026-10-31-ab-1-x.md)", self.ready())

    def test_a_missing_field_is_said_as_such(self):
        self.set_report("reviews/nope.md")
        text = self.ready()
        self.assertIn("app:ab1/x (report reviews/nope.md not found: fix the sub-issue's --report", text)
        self.assertNotIn("no report", text)
        self.write("reviews/2026-11-01-ab-1.md")  # another report found: shown, the stale field still said
        self.assertIn(
            "app:ab1/x (reviews/2026-11-01-ab-1.md; the sub-issue's report reviews/nope.md not found)", self.ready()
        )

    def test_the_sub_issue_wins_over_a_file_citing_the_branch(self):
        self.write("reviews/2026-11-01-ab1.md")
        self.write("reviews/2026-11-01-health.md", "| ab1/x | green |\n")
        self.assertIn("app:ab1/x (reviews/2026-11-01-health.md)", self.ready())  # the last resort, as before
        self.set_report("reviews/2026-11-01-ab1.md")
        self.assertIn("app:ab1/x (reviews/2026-11-01-ab1.md)", self.ready())

    def test_a_named_report_citing_the_branch_wins_over_other_files(self):
        plan = self.write("reviews/2026-10-30-plan-AB-1.md")  # names the issue, older than the agent
        os.utime(plan, (time.time() - 7200,) * 2)
        self.write("reviews/2026-11-01-health.md", "| ab1/x | green |\n")
        self.write("reviews/2026-11-01-ab-1-x.md", "Branch ab1/x.\n")
        self.write("reviews/2026-11-01-ab-10.md")  # AB-10 is not AB-1: a name match would win over the health file
        os.utime(self.home / "reviews/2026-11-01-ab-1-x.md", (time.time() - 60,) * 2)
        self.assertIn("app:ab1/x (reviews/2026-11-01-ab-1-x.md)", self.ready())
        f = loop.agents_file(self.project)
        start = (datetime.now() - timedelta(minutes=30)).isoformat(timespec="seconds")
        util.write_json(f, {"d1": {"start": start, "end": start, "delivered": start, "branch": "ab1/x"}})
        os.utime(self.home / "reviews/2026-11-01-ab-1-x.md", (time.time() - 3600,) * 2)  # before the agent
        self.assertIn("app:ab1/x (reviews/2026-11-01-health.md", self.ready())

    def test_a_plan_is_no_report(self):
        """A recent plan names the issue and cites the branch, but the home search never takes it as the report."""
        self.write("reviews/2026-11-01-plan-AB-1.md", "T1 on branch ab1/x.\n")
        self.write("reviews/plan.md", "Branch ab1/x.\n")
        self.assertIn("app:ab1/x (no report: ask for it first)", self.ready())
        self.set_report("reviews/2026-11-01-plan-AB-1.md")  # named by the sub-issue: the orchestrator's choice
        self.assertIn("app:ab1/x (reviews/2026-11-01-plan-AB-1.md)", self.ready())
        for name, plan in (
            ("2026-11-01-plan-AB-1.md", True),
            ("plan-AB-1.md", True),
            ("PLAN.md", True),
            ("2026-11-01-planner-notes.md", False),
            ("2026-11-01-ab-1-plan.md", False),
            ("2026-11-01-ab-1.md", False),
        ):
            self.assertEqual(delivery.is_plan(name), plan, name)


class BranchReportInRepoTest(NoticesCase):
    """The home inside the repo: the report travels in the branch and the main checkout lacks it until the merge."""

    toml = TOML.replace("{loop_extra}", "disk_low_gb = 0").replace("{extra}", "").replace("{tmp}/app", "{tmp}/home")

    def setUp(self):
        super().setUp()
        make_repo(self.home)
        self.issue("AB-1", "in progress", agent="zed7", branch="ab1/x")
        self.wt = self.tmp / "wt" / "ab1-x"
        git(self.home, "worktree", "add", "-q", "-b", "ab1/x", str(self.wt))

    def ready(self):
        return next(n for n in self.notices(mark=False) if n["kind"] == "ready_to_integrate")["text"]

    def commit_report(self, rel):
        f = self.wt / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("# Report\n")
        git(self.wt, "add", "-A")
        git(self.wt, "commit", "-q", "-m", "report")

    def test_the_report_in_the_branch(self):
        self.assertIn("app:ab1/x (no report: ask for it first)", self.ready())
        self.commit_report("reviews/2026-11-01-x.md")
        self.assertFalse((self.home / "reviews/2026-11-01-x.md").exists())
        self.assertIn("app:ab1/x (reviews/2026-11-01-x.md)", self.ready())

    def test_the_field_names_a_report_in_the_branch(self):
        self.commit_report("reviews/a.md")
        self.commit_report("reviews/b.md")
        with self.b.mutate() as d:
            self.b.set_sub(d, "AB-1", "T1", report="reviews/b.md")
        self.assertIn("app:ab1/x (reviews/b.md)", self.ready())
        with self.b.mutate() as d:
            self.b.set_sub(d, "AB-1", "T1", report="reviews/c.md")
        self.assertIn("(reviews/a.md; the sub-issue's report reviews/c.md not found)", self.ready())


class LivenessCase(NoticesCase):
    """Heartbeats of foreman's hooks module (heartbeat.py) written here as its ticks write them, `ago` seconds old;
    the mark and the `loop` heartbeat as `foreman session` and the pass write them."""

    HOUR = 3600
    GONE = heartbeat.GONE_TICKS * heartbeat.TICK_S

    def alive(self, sid, ago=0, started_ago=None, busy=False, ended=False, agents=None):
        """The heartbeat of session `sid`: its last tick `ago` s ago, the module started `started_ago` s ago (ago when
        None), `ended` at that tick (session.end), the subagents' statuses ({id: status}) when given."""
        now = time.time()

        def iso(t):
            return datetime.fromtimestamp(t).isoformat(timespec="seconds")

        rec = {
            "at": iso(now - ago),
            "started": iso(now - (ago if started_ago is None else started_ago)),
            "busy": busy,
            "version": "0.1.0",
            "claude_code": "2.1.314",
            "cwd": str(self.home),
        }
        if ended:
            rec["ended"] = iso(now - ago)
        if agents is not None:
            rec["agents"] = agents
        f = heartbeat.beat_file(self.project, sid)
        f.parent.mkdir(parents=True, exist_ok=True)
        util.write_json(f, rec)

    def gone(self, sid, **kw):
        self.alive(sid, ago=self.GONE + 30, **kw)

    def mark(self, sid="S1", since_ago=0):
        """Mark `sid` as the orchestrator `since_ago` seconds ago (what `foreman session` writes)."""
        self.project.state.mkdir(parents=True, exist_ok=True)
        orchestrator.session_file(self.project).write_text(sid + "\n")
        since = datetime.fromtimestamp(time.time() - since_ago).isoformat(timespec="seconds")
        util.write_json(self.project.state / "orchestrator.json", {"session": sid, "name": "orch", "since": since})

    def beat(self, ago):
        f = self.project.state / "heartbeats.json"
        f.parent.mkdir(parents=True, exist_ok=True)
        util.write_json(f, {"loop": datetime.fromtimestamp(time.time() - ago).isoformat(timespec="seconds")})


class LivenessTest(LivenessCase):
    """Orchestrator.liveness, health and the `foreman session` line, from the heartbeat alone."""

    def state(self, sid="S1"):
        return orchestrator.liveness(self.project, sid)["state"]

    def test_a_fresh_heartbeat_is_alive(self):
        self.alive("S1", ago=5, started_ago=600, busy=True)
        live = orchestrator.liveness(self.project, "S1")
        self.assertEqual((live["state"], live["busy"], live["claude_code"]), ("alive", True, "2.1.314"))
        self.assertAlmostEqual(live["started_ts"], time.time() - 600, delta=2)

    def test_gone_after_two_missed_ticks(self):
        self.assertEqual(self.GONE, 120)
        self.alive("S1", ago=self.GONE - 5)
        self.assertEqual(self.state(), "alive")  # one tick missed: still alive
        self.alive("S1", ago=self.GONE + 5)
        self.assertEqual(self.state(), "gone")  # two ticks missed

    def test_an_ended_session_is_gone_at_once(self):
        self.alive("S1", ended=True)
        live = orchestrator.liveness(self.project, "S1")
        self.assertEqual(live["state"], "gone")
        self.assertTrue(live["ended"])
        self.alive("S1")  # the same id ticks again (resumed): alive
        self.assertEqual(self.state(), "alive")

    def test_without_a_heartbeat_it_is_unknown(self):
        self.assertEqual(self.state(), "unknown")
        self.assertEqual(self.state(""), "unknown")
        self.assertEqual(self.state("../x"), "unknown")  # not a session id
        f = heartbeat.beat_file(self.project, "S1")
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("not json")
        self.assertEqual(self.state(), "unknown")

    def test_no_claude_code_file_is_read(self):
        """Nothing in foreman reads Claude Code's own session files (~/.claude/sessions): liveness is the heartbeat."""
        lib = Path(orchestrator.__file__).parent
        gone = re.compile(r"\.claude/sessions|FOREMAN_CLAUDE_SESSIONS|\b(session_files|file_alive|live_name)\b")
        for f in sorted(lib.glob("*.py")):
            self.assertIsNone(gone.search(f.read_text()), f.name)

    def verdict(self, **kw):
        h = orchestrator.health(self.project, **kw)
        return h and h["verdict"]

    def test_a_resumed_session_with_an_old_pass_is_ok(self):
        """A product with an hourly loop: alive, last pass 3 h ago, every_min 60, the module started 10 min ago (a
        resumed or reloaded session): ok; started 3 h ago: no_loop."""
        self.config("every_min = 60")
        self.mark("S1", since_ago=5 * self.HOUR)
        self.beat(3 * self.HOUR)
        self.alive("S1", started_ago=10 * 60)
        h = orchestrator.health(self.project)
        self.assertEqual((h["verdict"], h["alive"], h["limit_min"], h["status"]), ("ok", "alive", 120, "idle"))
        self.alive("S1", started_ago=3 * self.HOUR)
        self.assertEqual(self.verdict(), "no_loop")

    def test_verdicts(self):
        self.config("every_min = 30")  # limit max(60, 90) = 90 min
        self.mark("S1", since_ago=5 * self.HOUR)
        self.beat(100 * 60)
        self.assertEqual(self.verdict(), "silent")  # unknown and past the limit
        self.beat(80 * 60)
        self.assertEqual(self.verdict(), "ok")  # unknown, within the limit: the `loop` heartbeat decides
        self.alive("S1", started_ago=5 * self.HOUR)
        self.beat(100 * 60)
        self.assertEqual(self.verdict(), "no_loop")
        self.mark("S1", since_ago=60)
        self.assertEqual(self.verdict(), "ok")  # just marked: the window starts at the mark
        self.gone("S1")
        self.assertEqual(self.verdict(), "gone")
        self.alive("S1", ended=True)
        self.assertEqual(self.verdict(), "gone")

    def test_without_a_loop_prompt_only_ok_or_gone(self):
        self.write_toml(
            TOML.replace('prompt = "Fake orchestrator loop"', 'prompt = ""\ndisk_low_gb = 0')
            .replace("{loop_extra}", "")
            .replace("{extra}", "")
        )
        self.mark("S1", since_ago=5 * self.HOUR)
        self.beat(5 * self.HOUR)
        self.alive("S1", started_ago=5 * self.HOUR)
        self.assertEqual(self.verdict(), "ok")
        self.gone("S1")
        self.assertEqual(self.verdict(), "gone")

    def test_inactive_or_unmarked_is_none(self):
        self.assertIsNone(orchestrator.health(self.project))  # nobody marked
        self.mark("S1")
        self.project.mode = "inactive"
        self.assertIsNone(orchestrator.health(self.project))

    def test_session_command(self):
        """The four verdicts as `foreman session` prints them (exit code 0 in all of them)."""
        self.mark("S1", since_ago=5 * self.HOUR)
        self.beat(3 * self.HOUR)
        r = self.cli("session")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("may be gone", r.stdout)  # silent: no heartbeat
        self.gone("S1")
        r = self.cli("session")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("NOT ALIVE: no heartbeat from it since", r.stdout)
        self.assertIn("foreman --project fake session <your session id> --name <your name>", r.stdout)
        self.alive("S1", ended=True)
        self.assertIn("NOT ALIVE: the session ended at", self.cli("session").stdout)
        self.alive("S1", started_ago=5 * self.HOUR)  # alive, the `loop` heartbeat 3 h old
        self.assertIn("alive, but no loop pass since", self.cli("session").stdout)
        self.alive("S1", busy=True, started_ago=60)
        out = self.cli("session").stdout
        self.assertIn("alive: last tick ", out)
        self.assertIn(", module running since ", out)
        self.assertIn("(busy); last loop pass", out)

    def test_session_says_gone_two_ticks_after_the_ticks_stop_or_the_end(self):
        """The objective check, on a fixed clock (FOREMAN_NOW): the module ticks at 10:00:00 through `hook mod tick`;
        `foreman session` says alive at 10:01 and 10:02, NOT ALIVE from 10:02:01 on (two ticks missed); after a
        `hook mod end` at 10:00:30 it says NOT ALIVE at once."""
        self.cli("session", "S1", "--name", "orch", env={"FOREMAN_NOW": "2030-01-15T09:59:00"})

        def at(clock, *args, inp=None):
            r = self.cli(*args, inp=inp, env={"FOREMAN_NOW": f"2030-01-15T{clock}"})
            self.assertEqual(r.returncode, 0, r.stderr)
            return r.stdout

        def session_at(clock):
            return at(clock, "session").splitlines()[1]

        tick = json.dumps({"cwd": str(self.home), "session": "S1", "busy": False, "claude_code": "2.1.314"})
        at("10:00:00", "hook", "mod", "tick", inp=tick)
        self.assertIn("alive: last tick 10:00:0", session_at("10:01:00"))
        self.assertIn("alive: last tick 10:00:0", session_at("10:01:59"))
        self.assertIn("NOT ALIVE: no heartbeat from it since 10:00", session_at("10:02:05"))
        at("10:03:00", "hook", "mod", "tick", inp=tick)  # it ticks again: alive
        self.assertIn("alive: last tick 10:03:0", session_at("10:03:10"))
        end = json.dumps({"cwd": str(self.home), "session": "S1"})
        self.assertEqual(json.loads(at("10:03:30", "hook", "mod", "end", inp=end)), {"ended": True})
        self.assertIn("NOT ALIVE: the session ended at 10:03", session_at("10:03:31"))

    def test_marking_over_a_dead_session_says_so(self):
        self.mark("S0")
        self.gone("S0")
        r = self.cli("session", "S1", "--name", "orch2")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("relieves orch (session S0)", r.stdout)
        self.assertIn("(not alive)", r.stdout)
        self.mark("W")
        self.alive("W")
        self.assertNotIn("(not alive)", self.cli("session", "S1").stdout)
        self.assertEqual(orchestrator.current(self.project)["name"], "")  # a new session, no --name: no name
        self.cli("session", "S1", "--name", "orch3")
        self.cli("session", "S1")
        self.assertEqual(orchestrator.current(self.project)["name"], "orch3")  # marked again: kept


class StaleLoopTest(LivenessCase):
    """`loop_stale` between passes in the marked session."""

    def between(self, session="S1"):
        return [(n["kind"], n["actionable"]) for n in loop.notices(self.project, session)]

    def test_alive_without_passes_is_said_once(self):
        self.config("every_min = 30")
        self.mark("S1", since_ago=5 * self.HOUR)
        self.beat(100 * 60)
        self.alive("S1", started_ago=5 * self.HOUR)
        self.assertEqual(self.between(), [("loop_stale", True)])
        self.assertEqual(self.between(), [])  # the same case: once
        self.assertEqual(self.between("S0"), [])  # notices() in another session: never its
        self.assertNotIn("loop_stale", self.kinds("S1"))  # a pass writes the heartbeat: never there
        self.beat(95 * 60)  # another last pass, still late: a new case
        self.assertEqual(self.between(), [("loop_stale", True)])

    def test_within_the_limit_nothing_and_no_heartbeat_is_read(self):
        self.config("every_min = 30")
        self.mark("S1", since_ago=5 * self.HOUR)
        self.beat(80 * 60)
        saved = orchestrator.liveness
        orchestrator.liveness = lambda *a, **k: self.fail("liveness read within the limit")
        try:
            self.assertEqual(self.between(), [])
        finally:
            orchestrator.liveness = saved

    def test_a_just_started_module_is_not_stale(self):
        self.mark("S1", since_ago=5 * self.HOUR)
        self.beat(5 * self.HOUR)
        self.alive("S1", started_ago=10)  # its module started seconds ago (resumed, reloaded): its loop comes back
        self.assertEqual(self.between(), [])

    def test_unknown_and_late_is_said(self):
        self.mark("S1", since_ago=5 * self.HOUR)
        self.beat(5 * self.HOUR)
        self.assertEqual(self.between(), [("loop_stale", True)])  # silent: no heartbeat of its own


class TickTest(LivenessCase):
    """`foreman hook mod tick`: the heartbeat, the mark read at every tick, whether a pass is due, and what the
    module gets."""

    def test_outside_a_project_and_in_another_session(self):
        r = self.cli("hook", "mod", "tick", inp=json.dumps({"cwd": str(self.tmp), "session": "S1"}))
        self.assertEqual((r.returncode, json.loads(r.stdout)), (0, {"marked": False, "grants": False}))
        self.assertEqual(self.tick(), {"marked": False, "grants": False})  # nobody marked
        self.mark("S1", since_ago=self.HOUR)
        self.assertEqual(self.tick("S2"), {"marked": False, "grants": False})
        self.assertEqual(self.tick(""), {"marked": False, "grants": False})
        self.assertFalse(loop.loop_log_file(self.project).exists())  # no pass ran

    def test_every_tick_writes_the_heartbeat(self):
        self.mark("S1", since_ago=self.HOUR)
        self.beat(60)
        agents = [{"id": "a1", "status": "running"}, {"id": "a2", "status": "killed"}, {"id": 3}, "x"]
        ev = {"cwd": str(self.home), "busy": True, "claude_code": "2.1.314", "agents": agents}
        for sid in ("S1", "S2"):
            r = self.cli("hook", "mod", "tick", inp=json.dumps({**ev, "session": sid}))
            self.assertEqual(r.returncode, 0, r.stderr)
        mine, other = heartbeat.read(self.project, "S1"), heartbeat.read(self.project, "S2")
        self.assertEqual(
            {k: mine[k] for k in ("busy", "claude_code", "cwd", "agents")},
            {
                "busy": True,
                "claude_code": "2.1.314",
                "cwd": str(self.home),
                "agents": {"a1": "running", "a2": "killed"},
            },
        )
        self.assertEqual(mine["started"], mine["at"])  # the first tick seen: it starts there
        self.assertNotIn("agents", other)  # only the marked session's subagents are kept
        self.assertEqual(orchestrator.liveness(self.project, "S2")["state"], "alive")
        self.assertEqual(heartbeat.agents_of(self.project, "S1"), {"a1": "running", "a2": "killed"})
        self.cli("hook", "mod", "tick", inp=json.dumps({"cwd": str(self.home), "session": "S1"}))
        self.assertNotIn("agents", heartbeat.read(self.project, "S1"))  # a tick without them: none known

    def test_a_held_tick_writes_the_heartbeat_and_nothing_else(self):
        self.mark("S1", since_ago=self.HOUR)  # a pass is due: no `loop` heartbeat
        ev = {"cwd": str(self.home), "session": "S1", "busy": True, "claude_code": "2.1.314", "hold": True}
        r = self.cli("hook", "mod", "tick", inp=json.dumps(ev))
        self.assertEqual((r.returncode, json.loads(r.stdout)), (0, {"marked": True, "grants": False}), r.stderr)
        self.assertFalse(loop.loop_log_file(self.project).exists())  # no pass ran
        self.assertTrue(heartbeat.read(self.project, "S1")["busy"])
        r = self.cli("hook", "mod", "tick", inp=json.dumps({**ev, "session": "S2"}))
        self.assertEqual(json.loads(r.stdout), {"marked": False, "grants": False})

    def test_a_tick_reads_the_mark_once(self):
        """hooks_mod reads the mark once per tick, held or not (the pass, the band and the version notice read it on
        their own)."""
        from unittest import mock

        real = orchestrator.current
        callers: list[str] = []

        def current(project):
            callers.append(sys._getframe(1).f_globals["__name__"])
            return real(project)

        self.mark("S1", since_ago=self.HOUR)  # a pass is due
        ev = {"cwd": str(self.home), "session": "S1", "busy": False, "claude_code": "2.1.314", "agents": []}
        for extra, marked in (({"hold": True}, True), ({}, True), ({"session": "S2"}, False)):
            callers.clear()
            with mock.patch.object(orchestrator, "current", current):
                out = hooks_mod.tick({**ev, **extra})
            own = [c for c in callers if c in ("foreman.hooks_mod", "foreman.orchestrator")]  # or is_orchestrator
            self.assertEqual((out["marked"], len(own)), (marked, 1), (extra, callers))
            self.assertEqual("submit" in out or "log" in out, extra == {}, out)  # only the full tick ran the pass

    def test_a_tick_without_a_pass_reads_the_state_once(self):
        """The notices between passes and the band of one tick share one loop.Snapshot: agents.json and the backlog
        are read once; a tick with a pass reads them again for the band, after the pass."""
        from unittest import mock

        from foreman.backlog import Backlog

        self.mark("S1", since_ago=self.HOUR)  # a pass is due, but a turn runs (busy): no pass
        ev = {"cwd": str(self.home), "session": "S1", "busy": True, "claude_code": "2.1.314", "agents": []}
        with mock.patch.object(loop, "agents", wraps=loop.agents) as ags:
            with mock.patch.object(Backlog, "load", autospec=True, side_effect=Backlog.load) as load:
                out = hooks_mod.tick(ev)
        self.assertEqual((out["due"], "band" in out, ags.call_count, load.call_count), (False, True, 1, 1), out)

    def test_start_and_end(self):
        self.mark("S1", since_ago=self.HOUR)
        root = str(Path(ENTRY).resolve().parent.parent)
        ev = {"cwd": str(self.home), "session": "S1", "claude_code": "2.1.314", "root": root}
        r = self.cli("hook", "mod", "start", inp=json.dumps(ev))
        self.assertEqual(r.returncode, 0, r.stderr)
        rec = heartbeat.read(self.project, "S1")
        self.assertEqual((rec["started"], rec["busy"], rec["claude_code"]), (rec["at"], False, "2.1.314"))
        end = {"cwd": str(self.home), "session": "S1"}
        self.assertEqual(json.loads(self.cli("hook", "mod", "end", inp=json.dumps(end)).stdout), {"ended": True})
        self.assertEqual(orchestrator.liveness(self.project, "S1")["state"], "gone")
        none = {"cwd": str(self.home), "session": "S9"}
        self.assertEqual(json.loads(self.cli("hook", "mod", "end", inp=json.dumps(none)).stdout), {"ended": False})
        self.assertIsNone(heartbeat.read(self.project, "S9"))  # no heartbeat is made up for it
        elsewhere = {"cwd": "/", "session": "S1"}  # outside any project
        self.assertEqual(json.loads(self.cli("hook", "mod", "end", inp=json.dumps(elsewhere)).stdout), {"ended": False})

    def test_old_heartbeats_go(self):
        self.alive("S-old")
        f = heartbeat.beat_file(self.project, "S-old")
        os.utime(f, (time.time() - 8 * 86400,) * 2)
        heartbeat.beat(self.project, "S1", start=True)
        self.assertFalse(f.exists())
        self.assertTrue(heartbeat.beat_file(self.project, "S1").exists())

    def test_due_by_every_min(self):
        self.config("every_min = 30")
        self.mark("S1", since_ago=self.HOUR)
        self.beat(29 * 60)
        self.assertEqual(self.tick(), {"marked": True, "due": False, "grants": False})
        self.beat(30 * 60 - hooks_mod.DUE_SLACK_S + 5)  # a tick a little early still counts
        self.assertEqual(self.tick()["due"], True)
        self.assertEqual(self.tick(), {"marked": True, "due": False, "grants": False})  # the pass wrote the heartbeat

    def test_the_first_tick_after_the_mark_is_due(self):
        self.mark("S1", since_ago=10)
        self.beat(5 * 60)  # the outgoing session's pass, before this session was marked
        self.assertTrue(self.tick()["due"])
        f = self.project.state / "heartbeats.json"
        f.unlink()  # no pass ever
        self.assertTrue(self.tick()["due"])

    def test_no_pass_while_a_turn_runs(self):
        """A pass due while a main-loop turn runs (`busy`) waits for the first tick after the turn ends: a pass
        queued behind a running turn would be ended by that turn's turn.complete."""
        self.mark("S1", since_ago=self.HOUR)
        self.beat(2 * self.HOUR)  # a pass is due
        for _ in range(2):
            self.assertEqual(self.tick(busy=True), {"marked": True, "due": False, "grants": False})
        self.assertFalse(loop.loop_log_file(self.project).exists())  # no pass ran
        self.assertTrue(self.tick(busy=False)["due"])  # the turn ended: the next tick gives it
        self.assertEqual(
            self.tick(busy=False), {"marked": True, "due": False, "grants": False}
        )  # the pass wrote the heartbeat
        self.beat(2 * self.HOUR)
        self.assertTrue(self.tick()["due"])  # a module that does not say `busy` is not held back

    def test_no_loop_prompt_no_pass(self):
        toml = TOML.replace('prompt = "Fake orchestrator loop"', 'prompt = ""')
        self.write_toml(toml.replace("{loop_extra}", "disk_low_gb = 0").replace("{extra}", ""))
        self.mark("S1", since_ago=self.HOUR)
        self.assertEqual(self.tick(), {"marked": True, "due": False, "grants": False})

    def test_bad_input(self):
        r = self.cli("hook", "mod", "tick", inp="[]")
        self.assertEqual(r.returncode, 2)
        self.assertEqual(hooks_mod.every_min(self.project), 60)


class GoneOrchestratorTest(LivenessCase):
    """The hooks of the other sessions when the marked orchestrator is not alive (and sessions already open hear it
    once per case on a prompt); the live orchestrator and the sessions whose marked orchestrator is alive see no
    change."""

    ORPHAN: ClassVar[dict] = {**PASS, "session_id": "S2"}  # the loop prompt typed in another session
    PROMPT: ClassVar[dict] = {"hook_event_name": "UserPromptSubmit", "session_id": "S2", "prompt": "what next?"}

    def run_hook(self, name, event):
        r = self.hook(name, event)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout) if r.stdout.strip() else None

    def who(self, session="S2"):
        out = self.run_hook("who", {"hook_event_name": "SessionStart", "session_id": session, "source": "startup"})
        return out["hookSpecificOutput"]["additionalContext"] if out else ""

    def prompt(self, **ev):
        out = self.run_hook("loop", {**self.PROMPT, **ev})
        return out["hookSpecificOutput"]["additionalContext"] if out else ""

    def set_gone(self, ended=False):
        """S1 marked an hour ago, its last pass an hour ago, its heartbeat gone (ticks stopped, or ended)."""
        self.mark("S1", since_ago=self.HOUR)
        self.beat(self.HOUR)
        if ended:
            self.alive("S1", ago=self.HOUR, ended=True)
        else:
            self.gone("S1")

    def test_who_says_the_marked_session_is_gone(self):
        for ended in (False, True):
            self.set_gone(ended)
            ctx = self.who(f"S2-{ended}")
            self.assertIn("not alive", ctx)
            self.assertIn("orch", ctx)
            self.assertIn(f"`foreman --project fake session S2-{ended} --name <your name>`", ctx)
            self.assertIn("Owner", ctx)
            self.assertNotIn("is orchestrated by", ctx)

    def test_what_session_start_said_a_prompt_does_not_repeat(self):
        self.set_gone()
        self.assertIn("not alive", self.who())
        self.assertEqual(self.prompt(), "")

    def test_who_without_a_heartbeat_within_the_limit_is_the_usual_text(self):
        self.mark("S1", since_ago=self.HOUR)  # no heartbeat: liveness cannot tell (unknown, within the limit)
        self.assertIn("is orchestrated by the session", self.who())

    def test_who_says_a_silent_one_may_be_gone(self):
        self.config("every_min = 30")
        self.mark("S1", since_ago=5 * self.HOUR)
        self.beat(5 * self.HOUR)  # no heartbeat at all: unknown, and past the limit
        ctx = self.who()
        self.assertIn("may be gone", ctx)
        self.assertIn("more than 90 min", ctx)
        self.assertIn("session S2 --name", ctx)

    def test_who_with_the_marked_session_alive_is_unchanged(self):
        self.mark("S1", since_ago=self.HOUR)
        self.beat(self.HOUR)
        self.alive("S1", started_ago=self.HOUR)
        ctx = self.who()
        self.assertIn("is orchestrated by the session orch", ctx)
        self.assertNotIn("not alive", ctx)
        self.assertEqual(self.who("S1"), "")  # the orchestrator itself: nothing, as always
        self.assertEqual(self.prompt(), "")

    def test_who_in_spanish(self):
        self.write_toml(
            TOML.replace('owner = "Owner"', 'owner = "Owner"\nlanguage = "es"')
            .replace("{loop_extra}", "disk_low_gb = 0")
            .replace("{extra}", "")
        )
        self.set_gone()
        self.assertIn("no está viva", self.who())

    def test_another_session_gets_no_pass(self):
        """The tick reads the mark: a session that is not the marked one is told so and no pass runs there, alive
        or gone; the loop prompt typed there is just a prompt."""
        self.set_gone()
        beat = notes.heartbeats(self.project)["loop"]
        self.assertEqual(self.tick("S2"), {"marked": False, "grants": False})
        self.assertEqual(notes.heartbeats(self.project)["loop"], beat)
        for _ in range(2):
            self.assertNotIn("decision", json.dumps(self.run_hook("loop", self.ORPHAN) or {}))

    def test_an_open_session_hears_it_once_on_a_prompt(self):
        self.set_gone()
        self.assertIn("not alive", self.prompt())
        self.assertEqual(self.prompt(), "")  # the same case: never again
        self.assertEqual(self.prompt(agent_id="a1", session_id="S3"), "")  # a subagent: nothing
        self.assertIsNone(self.run_hook("loop", {**self.PROMPT, "hook_event_name": "PostToolUse", "tool_name": "Bash"}))
        self.assertIn("not alive", self.prompt(session_id="S3"))  # another open session: its own once
        self.beat(60)  # it was resumed, passed again and died again: a new case
        self.assertIn("not alive", self.prompt())

    def test_the_live_orchestrator_sees_no_change(self):
        self.mark("S1", since_ago=self.HOUR)
        self.alive("S1")
        self.gone("S0")  # a dead session around changes nothing for it
        self.assertEqual(self.who("S1"), "")
        out = self.run_hook("loop", {**self.PROMPT, "session_id": "S1"})
        self.assertNotIn("not alive", json.dumps(out or {}))
        self.issue("AB-1", "ready")
        self.assertNotIn("not alive", self.assert_submitted("wip_production"))

    def test_a_prompt_says_nothing_while_the_marked_session_ticks(self):
        from foreman import hooks

        self.mark("S1", since_ago=self.HOUR)
        self.beat(10 * 60)
        self.alive("S1", ago=30)
        self.assertEqual(hooks.open_session_notice(self.project, "S2"), "")
        self.gone("S1")  # its ticks stopped
        self.assertIn("not alive", hooks.open_session_notice(self.project, "S2"))


class ChatReviewTest(NoticesCase):
    """The chat reviewer. At the end of each main-loop turn foreman's hooks module hands the turn's answer to `hook
    mod answer`; in the marked session it is checked with plain.findings, one line of counts goes to
    <state>/plain.jsonl for `foreman plain --stats`, and a reminder comes back when something was flagged (the module
    adds it to the owner's next prompt: tests/mod/answer.test.ts). No transcript is read."""

    BAD = "Antes de seguir necesito tus respuestas a J1 y J2."
    CLEAN = "Dos preguntas te esperan en el panel: quién escribe la tabla de la GPU y si la config va en un git local."

    def setUp(self):
        super().setUp()
        self.cli("session", "S1")

    def answer(self, text, session="S1", turn="turn-1", cwd=None):
        ev = {"cwd": str(cwd or self.home), "session": session, "turn": turn, "answer": text}
        r = self.cli("hook", "mod", "answer", inp=json.dumps(ev))
        self.assertEqual((r.returncode, r.stderr), (0, ""))
        return json.loads(r.stdout)

    def log(self):
        f = self.project.state / "plain.jsonl"
        return [json.loads(x) for x in f.read_text().splitlines()] if f.exists() else []

    def test_a_flagged_answer_gets_a_reminder_and_a_log_line(self):
        out = self.answer(self.BAD)
        self.assertEqual(set(out), {"reviewed", "reminder"})
        self.assertTrue(out["reviewed"])
        self.assertIn("J1, J2", out["reminder"])
        self.assertNotIn("\n", out["reminder"])
        (line,) = self.log()
        self.assertEqual(
            (line["session"], line["message"], line["findings"], line["kinds"]), ("S1", "turn-1", 2, {"code": 2})
        )
        self.assertEqual(line["words"], len(self.BAD.split()))
        self.assertNotIn("text", line)
        self.assertNotIn("J1", json.dumps(line))  # counts, never the text
        # each turn ends once: a second answer is another message
        self.assertIn("reminder", self.answer(self.BAD, turn="turn-2"))
        self.assertEqual([x["message"] for x in self.log()], ["turn-1", "turn-2"])

    def test_a_clean_answer_gets_nothing_and_logs_zero(self):
        self.assertEqual(self.answer(self.CLEAN), {"reviewed": True})
        (line,) = self.log()
        self.assertEqual((line["findings"], line["kinds"]), (0, {}))

    def test_the_log_line_is_the_one_the_transcript_reader_wrote(self):
        """The same answers give the same lines in plain.jsonl as the reviewer that read the transcript on an owner's
        prompt (foreman 0.33.0, run on these fixtures; time and message id aside: the message is now the turn's
        id)."""
        for text, words, findings, kinds, what in (
            (self.BAD, 10, 2, {"code": 2}, {"sub": 2}),
            (self.CLEAN, 23, 0, {}, {}),
            (
                "La rama hb7/notices está lista; el informe va en ops/reviews y HB-7 T4 queda cerrada con "
                "`foreman integrate`.",
                18,
                1,
                {"code": 1},
                {"sub": 1},
            ),
            (
                "Todo verde.\n\nEl selftest pasa 640 pruebas y ruff no se queja; mira hooks_mod.tick si quieres el "
                "detalle.",
                18,
                1,
                {"internal": 1},
                {"snake": 1},
            ),
            ("Necesito que decidas J3: ¿el bucle va al temporizador del mod o seguimos con CronCreate?", 15, 0, {}, {}),
            ("He lanzado a hb7t4 (dev) sobre la rama `hb7/notices`; tarda ~2 h.", 12, 0, {}, {}),
        ):
            self.answer(text)
            line = self.log()[-1]
            self.assertEqual(set(line), {"time", "session", "message", "words", "findings", "kinds", "what"})
            want = {"session": "S1", "words": words, "findings": findings, "kinds": kinds, "what": what}
            self.assertEqual({k: line[k] for k in want}, want, text)

    def test_not_for_other_sessions_empty_answers_or_outside_a_project(self):
        self.assertEqual(self.answer(self.BAD, session="S2"), {"reviewed": False})
        self.assertEqual(self.answer("   "), {"reviewed": False})
        self.assertEqual(self.answer(self.BAD, cwd="/"), {"reviewed": False})  # outside any project
        self.assertEqual(self.log(), [])
        # the classic prompt hook no longer reviews anything, the pass's prompt neither
        r = self.hook("loop", {"hook_event_name": "UserPromptSubmit", "session_id": "S1", "prompt": "hola"})
        self.assertNotIn("J1, J2", r.stdout)
        self.assertEqual(self.log(), [])

    def test_bad_input(self):
        for inp in ("not json", "[1]"):
            r = self.cli("hook", "mod", "answer", inp=inp)
            self.assertEqual(r.returncode, 2, inp)
        out = self.answer(None)  # an answer that is not text: nothing to review
        self.assertEqual(out, {"reviewed": False})

    def test_stats(self):
        f = self.project.state / "plain.jsonl"
        f.parent.mkdir(parents=True, exist_ok=True)
        rows = [
            ("2026-10-20 10:00:00", "S0", 3, {"code": 3}),
            ("2026-10-30 10:00:00", "S1", 2, {"code": 1, "name": 1}),
            ("2026-10-30 11:00:00", "S1", 0, {}),
            ("2026-10-31 09:00:00", "S2", 0, {}),
            ("2026-10-31 10:00:00", "S2", 1, {"internal": 1}),
        ]
        f.write_text(
            "".join(
                json.dumps({"time": t, "session": s, "message": "m", "words": 9, "findings": n, "kinds": k, "what": {}})
                + "\n"
                for t, s, n, k in rows
            )
            + "broken\n"
        )

        def stats(*args):
            r = self.cli("plain", "--stats", "--json", *args)
            self.assertEqual(r.returncode, 0, r.stderr)
            return json.loads(r.stdout)

        s = stats()
        self.assertEqual((s["messages"], s["flagged"], s["share"]), (5, 3, 60.0))
        self.assertEqual(s["kinds"], {"code": 2, "name": 1, "internal": 1})
        s = stats("--since", "2026-10-30")
        self.assertEqual((s["messages"], s["flagged"], s["share"]), (4, 2, 50.0))
        self.assertEqual(
            [(d["day"], d["messages"], d["flagged"], d["share"]) for d in s["days"]],
            [("2026-10-30", 2, 1, 50.0), ("2026-10-31", 2, 1, 50.0)],
        )
        self.assertEqual(stats("--session", "S2")["messages"], 2)
        self.assertEqual(stats("--since", "36500d")["messages"], 5)
        self.assertEqual(stats("--since", "1m")["messages"], 0)
        r = self.cli("plain", "--stats", "--since", "2026-10-30")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("2 of 4", r.stdout)
        self.assertIn("50.0 %", r.stdout)
        self.assertEqual(self.cli("plain", "--stats", "--since", "a week").returncode, 2)
        f.unlink()
        r = self.cli("plain", "--stats")
        self.assertEqual(r.returncode, 0)
        self.assertIn("no message", r.stdout)

    def test_parse_since(self):
        from foreman import plain

        now = datetime(2026, 10, 31, 12, 0)
        self.assertEqual(plain.parse_since("7d", now), datetime(2026, 10, 24, 12, 0))
        self.assertEqual(plain.parse_since("12h", now), datetime(2026, 10, 31, 0, 0))
        self.assertEqual(plain.parse_since("30m", now), datetime(2026, 10, 31, 11, 30))
        self.assertEqual(plain.parse_since("2026-10-30 08:00", now), datetime(2026, 10, 30, 8, 0))
        with self.assertRaises(ValueError):
            plain.parse_since("yesterday", now)
