"""The orchestrator's memory renewed in place (renew.py): the `context_rotate` notice asks for `foreman renew`, the
module's tick starts the attempt (written first) and runs `/compact`, the classic SessionStart `compact` hook gives
the pack back and settles it, the first measure closes it with its figures; waits for live agents, the safety
trigger, the cooldown, failures and what follows them (always another renewal, never a handover)."""

import json
import time
from datetime import datetime

from test_loop_notices import NoticesCase

from foreman import band, hooks, hooks_mod, i18n, orchestrator, renew, start, util


def line(kind, text, **extra):
    """One transcript record of the main chain."""
    if kind == "user":
        rec = {"type": "user", "message": {"role": "user", "content": text}}
    else:
        rec = {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": text}]}}
    rec.update({"timestamp": "2026-11-01T10:00:00Z", **extra})
    return json.dumps(rec)


class RenewalCase(NoticesCase):
    def setUp(self):
        super().setUp()
        self.config(loop_extra="context_rotate = 200000")
        orchestrator.mark(self.project, "S1", "orch-1")
        self.transcript = self.tmp / "claude-projects" / "-home" / "S1.jsonl"
        self.transcript.parent.mkdir(parents=True)
        self.transcript.write_text("")

    def measure(self, tokens, session="S1", window=1000000):
        ctx = {"window": window}
        if tokens is not None:
            ctx.update(tokens=tokens, percent=round(100 * tokens / window))
        return hooks_mod.measure({"cwd": str(self.home), "session": session, "context": ctx, "cost": None})

    def resume(self, source="compact", session="S1"):
        r = self.cli(
            "hook",
            "resume",
            inp=json.dumps(
                {
                    "hook_event_name": "SessionStart",
                    "cwd": str(self.home),
                    "source": source,
                    "session_id": session,
                    "transcript_path": str(self.transcript),
                }
            ),
        )
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout

    def records(self):
        f = renew.renewals_file(self.project)
        return [json.loads(x) for x in f.read_text().splitlines()] if f.exists() else []

    def notice(self, session="S1"):
        return next((n for n in self.notices(session) if n["kind"] == "context_rotate"), None)


class RenewalTest(RenewalCase):
    def test_a_whole_renewal(self):
        self.measure(150000)
        self.assertIsNone(self.notice())
        self.measure(208000)
        n = self.notice()
        self.assertTrue(n["actionable"])
        self.assertIn("then run `foreman renew`", n["text"])
        self.assertIn("«Renewal»", n["text"])
        self.assertNotIn("renew", self.hook_tick())  # no request, below the safety trigger
        # the model writes its board, then asks
        r = self.cli("renew", "--note", "the owner asked for X", env={"CLAUDE_CODE_SESSION_ID": "S1"})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("renewal asked for session S1", r.stdout)
        n = self.notice()
        self.assertFalse(n["actionable"])
        self.assertIn("the renewal of this session's memory is asked", n["text"])
        # not while a turn runs, nor while a pass is under way
        self.assertNotIn("renew", self.hook_tick(busy=True))
        self.assertNotIn("renew", self.hook_tick(hold=True))
        got = self.hook_tick()
        self.assertEqual(set(got), {"marked", "due", "grants", "renew", "instructions", "log"})
        self.assertIn("foreman renews the memory of fake's orchestrator session in place", got["instructions"])
        self.assertEqual(
            got["log"],
            "foreman: renewing this session's memory in place (208K of 1000K used); the work goes on in a minute",
        )
        self.assertFalse(renew.renew_file(self.project).exists())
        [a] = self.records()
        self.assertEqual(
            (a["state"], a["why"], a["tokens_before"], a["note"]), ("asked", "request", 208000, "the owner asked for X")
        )
        # while it waits for the compaction: no pass, no second attempt
        self.old_beat()
        self.assertEqual(self.hook_tick(), {"marked": True, "due": False, "grants": False})
        # the call settled (resolved): noted; the compaction's SessionStart settles it
        out = hooks_mod.renewed({"cwd": str(self.home), "session": "S1", "id": a["id"], "text": ""})
        self.assertEqual(out, {"recorded": True})
        self.transcript.write_text(
            "\n".join(
                [
                    line("user", "please do X"),
                    line("assistant", "Doing X."),
                    line("user", "Fake orchestrator loop\n\nLoop pass"),
                ]
            )
            + "\n"
        )
        recent = datetime.fromtimestamp(time.time() - 60).strftime("%Y-%m-%dT%H:%M:%S")
        util.write_json(self.project.state / "heartbeats.json", {"loop": recent})  # a pass a minute ago
        old = datetime.fromtimestamp(time.time() - 120).strftime("%Y-%m-%dT%H:%M:%S")
        util.write_json(self.project.state / "orchestrator.json", {"session": "S1", "name": "orch-1", "since": old})
        self.assertFalse(hooks_mod.pass_due(self.project, orchestrator.current(self.project)))
        pack = self.resume()
        self.assertIn("foreman renewed this session's memory in place", pack)
        self.assertIn("You are still orch-1", pack)
        self.assertIn(str(self.transcript), pack)
        self.assertIn("the owner asked for X", pack)
        self.assertIn("please do X", pack)
        self.assertIn("Doing X.", pack)
        self.assertNotIn("Loop pass", pack)
        self.assertIn("Board", pack)
        self.assertEqual(renew.last(self.project, "S1")["state"], "compacted")
        self.assertTrue(renew.pack_file(self.project).exists())
        # a pass right after, though the last one is recent
        self.assertTrue(hooks_mod.pass_due(self.project, orchestrator.current(self.project)))
        # the first measure with tokens closes it
        self.assertEqual(self.measure(None), {"stored": True})
        out = self.measure(14000)
        self.assertEqual(
            out["log"],
            "foreman: memory renewed in this session, 208K → 14K; the work goes on, nothing to do on your side",
        )
        self.assertEqual(renew.last(self.project, "S1")["state"], "ok")
        today = (self.project.notes_dir / f"{datetime.now():%Y-%m-%d}.md").read_text()
        self.assertIn("Renewal (session orch-1): 208K → 14K", today)
        self.assertNotIn("log", self.measure(15000))  # once
        self.assertIsNone(self.notice())
        self.assertIsNone(renew.trouble(self.project, "S1"))
        self.assertRegex(renew.status_line(self.project, "S1"), r"^last renewal \d\d:\d\d ok \(208K → 14K\), 1 today$")
        self.assertTrue(any(p["text"].startswith("renewed ") for p in band.lines(self.project)["rows"][0]))
        r = self.cli("session")
        self.assertIn("last renewal", r.stdout)

    def compact_with_no_turn(self, tokens, ready=True):
        """The 2026-11-02 sequence up to the compaction: the context at `tokens`, the notice said once, a
        ready issue (or none), `foreman renew`, the tick's attempt and the compaction, with no turn after it (no new
        measure: the module's last one predates the compaction) and a pass shortly before it."""
        if ready:
            self.issue("AB-1", "ready")
        self.measure(tokens)
        self.assertTrue(self.notice()["actionable"])
        r = self.cli("renew", env={"CLAUDE_CODE_SESSION_ID": "S1"})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("renew", self.hook_tick())
        f = hooks_mod.measure_file(self.project, "S1")
        before = datetime.fromtimestamp(time.time() - 60).strftime("%Y-%m-%dT%H:%M:%S")
        util.write_json(f, {**util.read_json(f, {}), "at": before})  # taken at the last turn, before /compact
        util.write_json(self.project.state / "heartbeats.json", {"loop": before})
        self.resume()
        self.assertEqual(renew.last(self.project, "S1")["state"], "compacted")

    def last_pass(self):
        return json.loads((self.project.state / "loop.jsonl").read_text().splitlines()[-1])

    def test_the_pass_after_a_compaction_with_no_turn_is_submitted(self):
        # 2026-11-02: 213K asked at 01:56:43, compacted at 01:57:04; every pass after it was skipped (context_rotate and
        # wip_production information only) until the owner wrote at 07:01
        self.compact_with_no_turn(213756)
        self.assertEqual(hooks_mod.read_measure(self.project, "S1")["tokens"], 213756)
        self.assertIsNone(renew.measure(self.project, "S1"))  # from before the compaction: not the context now
        self.assertIsNone(self.notice())
        out = self.hook_tick()
        self.assertEqual(set(out), {"marked", "due", "grants", "submit"}, out)
        self.assertTrue(out["submit"].startswith("Fake orchestrator loop\n\nLoop pass "))
        self.assertNotIn("context_rotate", out["submit"])
        rec = self.last_pass()
        self.assertFalse(rec["skipped"])
        self.assertNotIn("context_rotate", rec["notices"])
        self.assertIn("wip_production", rec["actionable"])  # the ready issue can be launched again
        # the turn that pass brings measures the new context and closes the renewal
        self.measure(83197)
        self.assertEqual(renew.last(self.project, "S1")["state"], "ok")
        self.assertEqual(renew.measure(self.project, "S1")["tokens"], 83197)

    def test_the_first_pass_after_a_compaction_goes_even_with_nothing_to_do(self):
        self.compact_with_no_turn(213756, ready=False)
        out = self.hook_tick()
        self.assertIn("submit", out)  # the session's only way to a turn: its pack says a pass comes
        rec = self.last_pass()
        self.assertEqual((rec["skipped"], rec["actionable"]), (False, []))  # sent, with nothing actionable
        self.assertFalse(hooks_mod.renewed_since_pass(self.project, orchestrator.current(self.project)))
        # an hour on, still no turn: the next pass, with nothing to do, is idle again
        hour = datetime.fromtimestamp(time.time() - 3700).strftime("%Y-%m-%dT%H:%M:%S")
        f = renew.renewals_file(self.project)
        recs = [{**r, "compacted_at": hour} if "compacted_at" in r else r for r in self.records()]
        f.write_text("".join(json.dumps(r) + "\n" for r in recs))
        util.write_json(self.project.state / "heartbeats.json", {"loop": hour})
        m = hooks_mod.measure_file(self.project, "S1")
        before = datetime.fromtimestamp(time.time() - 3760).strftime("%Y-%m-%dT%H:%M:%S")
        util.write_json(m, {**util.read_json(m, {}), "at": before})
        self.assertFalse(hooks_mod.renewed_since_pass(self.project, orchestrator.current(self.project)))
        out = self.hook_tick()
        self.assertEqual(set(out), {"marked", "due", "grants", "log"}, out)
        self.assertTrue(self.last_pass()["skipped"])

    def test_a_stale_measure_does_not_force_another_renewal(self):
        self.compact_with_no_turn(320000, ready=False)  # past FORCE_FACTOR × 200K before the compaction
        self.assertIsNone(renew._tokens(self.project, "S1"))
        later = time.time() + renew.COOLDOWN_S + 60
        self.assertIsNone(renew.due(self.project, "S1", {"busy": False, "agents": []}, now=later))
        self.measure(320000)  # a new measure after the compaction counts again (and says it freed too little)
        self.assertEqual(renew._tokens(self.project, "S1"), 320000)
        self.assertIn("freed too little", renew.trouble(self.project, "S1"))
        # past FORCE_FACTOR × context_rotate, the safety trigger renews it again once the cooldown is over
        self.assertEqual(renew.due(self.project, "S1", {"busy": False, "agents": []}, now=later)["why"], "safety")

    def test_waits_for_live_agents_until_forced(self):
        self.measure(250000)
        self.agent("a1", "zed7", session="S1")
        n = self.notice()
        self.assertIn("once the 1 live agents deliver", n["text"])
        self.assertIn("At 300K foreman renews it anyway", n["text"])
        renew.request(self.project, "S1")
        self.assertNotIn("renew", self.hook_tick())
        self.assertNotIn("renew", self.hook_tick(agents=[{"id": "x", "status": "running"}]))
        self.measure(310000)
        self.assertEqual(renew.due(self.project, "S1", {"busy": False, "agents": []})["why"], "request")

    def test_a_subagent_of_the_module_also_waits(self):
        self.measure(250000)
        renew.request(self.project, "S1")
        self.assertNotIn("renew", self.hook_tick(agents=[{"id": "x", "status": "running"}]))
        self.assertIn("renew", self.hook_tick(agents=[{"id": "x", "status": "completed"}]))

    def tick_without_agents(self):  # `$.agent.list()` rejected, or the first ticks after a new mark
        out = hooks_mod.tick({"cwd": str(self.home), "session": "S1", "busy": False})
        out.pop("band", None)
        return out

    def test_a_tick_without_agents_cannot_tell(self):
        self.measure(250000)
        renew.request(self.project, "S1")
        self.assertNotIn("renew", self.tick_without_agents())
        self.assertIsNone(renew.due(self.project, "S1", {"busy": False, "agents": None}))
        self.assertEqual(self.records(), [])
        self.assertIsNotNone(renew.requested(self.project, "S1"))  # the request waits for a tick that can tell
        self.assertIn("renew", self.hook_tick(agents=[]))

    def test_a_tick_without_agents_still_renews_when_forced(self):
        self.measure(310000)
        got = self.tick_without_agents()
        self.assertIn("renew", got)
        self.assertEqual(self.records()[0]["why"], "safety")

    def test_the_safety_trigger_needs_no_request(self):
        self.measure(250000)
        self.assertNotIn("renew", self.hook_tick())
        self.measure(300000)
        self.agent("a1", "zed7", session="S1")
        n = self.notice()
        self.assertIn(
            "foreman renews this session's memory in place when this turn ends, live agents or not", n["text"]
        )
        self.assertTrue(n["actionable"])
        got = self.hook_tick()
        self.assertIn("renew", got)
        self.assertEqual(self.records()[0]["why"], "safety")

    def test_now_below_the_threshold_and_with_agents(self):
        self.measure(50000)
        with self.assertRaises(renew.RenewError) as e:
            renew.request(self.project, "S1")
        self.assertIn("below [loop] context_rotate", str(e.exception))
        self.agent("a1", "zed7", session="S1")
        renew.request(self.project, "S1", now=True)
        self.assertEqual(renew.due(self.project, "S1", {"busy": False})["why"], "now")  # no `agents`: --now skips it

    def test_refused_requests(self):
        self.measure(250000)
        for sid in (None, "S2"):
            with self.assertRaises(renew.RenewError):
                renew.request(self.project, sid)
        r = self.cli("renew", env={"CLAUDE_CODE_SESSION_ID": "S2"})
        self.assertEqual(r.returncode, 1)
        self.assertIn("not fake's marked orchestrator", r.stderr)
        # an agent of the orchestrator's session shares its id: the guard wrote its call down
        from foreman import identity

        util.write_json(
            identity.callers_file(),
            {
                "t1": {
                    "at": time.time(),
                    "session": "S1",
                    "agent": "a1",
                    "state": str(self.project.state),
                    "calls": [[None, "renew"]],
                    "background": False,
                }
            },
        )
        r = self.cli("renew", "--now", env={"CLAUDE_CODE_SESSION_ID": "S1"})
        self.assertEqual(r.returncode, 1)
        self.assertIn("run by an agent of the orchestrator's session", r.stderr)
        self.assertFalse(renew.renew_file(self.project).exists())
        identity.callers_file().unlink()
        # an old `[loop] renew = "handover"` is a warning and changes nothing: the session renews in place
        self.config(loop_extra='context_rotate = 200000\nrenew = "handover"')
        self.assertTrue(any("[loop] renew is no longer used" in w for w in self.project.warnings))
        renew.request(self.project, "S1", now=True)
        self.assertEqual(renew.due(self.project, "S1", {"busy": False, "agents": []})["why"], "now")

    def test_failures_cooldown_and_trying_again(self):
        self.measure(250000)
        renew.request(self.project, "S1")
        a = renew.due(self.project, "S1", {"busy": False, "agents": []})
        out = hooks_mod.renewed(
            {"cwd": str(self.home), "session": "S1", "id": a["id"], "text": "", "error": "HooksError: refused"}
        )
        self.assertEqual(out["recorded"], True)
        self.assertIn("foreman tries again in 30 min", out["log"])
        self.assertEqual(
            hooks_mod.renewed({"cwd": str(self.home), "session": "S1", "id": a["id"]}), {"recorded": False}
        )
        self.assertIsNone(renew.trouble(self.project, "S1"))
        renew.request(self.project, "S1")
        self.assertIsNone(renew.due(self.project, "S1", {"busy": False, "agents": []}))  # cooling down
        later = time.time() + renew.COOLDOWN_S + 5
        b = renew.due(self.project, "S1", {"busy": False, "agents": []}, now=later)
        self.assertIsNotNone(b)
        # no compaction came: failed WAIT_S on, said once by the tick
        self.assertEqual(renew.state_of(b, later + renew.WAIT_S + 5), "failed")
        gone = renew.expire(self.project, "S1", later + renew.WAIT_S + 5)
        self.assertEqual(gone["state"], "failed")
        self.assertEqual(renew.trouble(self.project, "S1"), "the last two renewals failed")
        line = hooks_mod.failed_line(self.project, "S1", gone)
        self.assertIn("did not work again", line)
        self.assertIn("foreman tries again in 30 min", line)
        self.assertIn("type /compact in this session", line)
        self.assertNotIn("new session", line)
        n = self.notice()  # it still asks for the renewal, and says why it may take a while
        self.assertIn("then run `foreman renew`", n["text"])
        self.assertIn("the last two renewals failed: foreman tries again, at most every 30 min", n["text"])
        self.assertNotIn("Handover", n["text"])
        self.assertIn("renewal trouble: the last two renewals failed", renew.status_line(self.project, "S1"))
        # by request, once the cooldown is over
        renew.request(self.project, "S1")
        self.assertIsNone(renew.due(self.project, "S1", {"busy": False, "agents": []}, now=later + 60))
        c = renew.due(self.project, "S1", {"busy": False, "agents": []}, now=later + 2 * renew.COOLDOWN_S)
        self.assertEqual(c["why"], "request")
        # and the safety trigger with no request, though the last ones failed
        renew.expire(self.project, "S1", later + 3 * renew.COOLDOWN_S)
        self.measure(310000)
        d = renew.due(self.project, "S1", {"busy": False, "agents": []}, now=later + 4 * renew.COOLDOWN_S)
        self.assertEqual(d["why"], "safety")

    def test_the_tick_says_a_failure_once(self):
        self.measure(250000)
        renew.request(self.project, "S1")
        self.assertIn("renew", self.hook_tick())
        f = renew.renewals_file(self.project)
        recs = self.records()
        recs[0]["at"] = "2020-01-01T00:00:00"
        f.write_text("".join(json.dumps(x) + "\n" for x in recs))
        got = self.hook_tick()
        self.assertIn("did not work (no compaction came)", got["log"])
        self.assertNotIn("log", self.hook_tick())

    def test_a_call_that_settled_with_no_compaction(self):
        self.measure(250000)
        renew.request(self.project, "S1")
        a = renew.due(self.project, "S1", {"busy": False, "agents": []})
        renew.settled(self.project, "S1", a["id"], "Not enough messages to compact.", None)
        b = renew.attempts(self.project, "S1")[-1]
        self.assertEqual(renew.state_of(b), "asked")
        self.assertEqual(renew.state_of(b, time.time() + renew.RAN_S + 5), "failed")

    def test_a_renewal_that_freed_too_little(self):
        self.measure(250000)
        renew.request(self.project, "S1")
        renew.due(self.project, "S1", {"busy": False, "agents": []})
        self.resume()
        out = self.measure(170000)
        self.assertIn("freed too little (250K → 170K)", out["log"])
        self.assertIn("foreman renews it again by itself when it reaches 300K", out["log"])
        self.assertEqual(renew.trouble(self.project, "S1"), "the last renewal freed too little (250K → 170K)")
        self.measure(210000)
        n = self.notice()  # information: the next one waits for the safety trigger
        self.assertFalse(n["actionable"])
        self.assertTrue(n["key"].endswith(":low"))
        self.assertIn("again only at 300K", n["text"])
        self.assertIn("the long outputs you keep", n["text"])
        self.assertNotIn("foreman stats", n["text"])
        self.assertNotIn("Handover", n["text"])
        today = (self.project.notes_dir / f"{datetime.now():%Y-%m-%d}.md").read_text()
        self.assertIn("Freed too little: the next automatic renewal waits for 300K.", today)
        # a request below the safety trigger is refused; `--now` and the safety trigger are not
        later = time.time() + renew.COOLDOWN_S + 60
        with self.assertRaises(renew.RenewError) as e:
            renew.request(self.project, "S1")
        self.assertIn("foreman renews again by itself at 300K", str(e.exception))
        self.assertIsNone(renew.due(self.project, "S1", {"busy": False, "agents": []}, now=later))
        self.measure(305000)
        renew.request(self.project, "S1")
        self.assertEqual(renew.due(self.project, "S1", {"busy": False, "agents": []}, now=later)["why"], "request")

    def test_only_the_marked_session(self):
        self.measure(310000, session="S2")
        self.assertEqual(self.hook_tick(session="S2"), {"marked": False, "grants": False})
        self.assertIsNone(renew.due(self.project, None, {"busy": False}))


class PackTest(RenewalCase):
    def test_owner_messages(self):
        rows = [
            line("user", "first"),
            line("user", "second"),
            line("assistant", "answer two"),
            line("user", "Fake orchestrator loop\n\nLoop pass 10:00"),
            line("assistant", "pass done"),
            line("user", "<task-notification>agent done</task-notification>"),
            line("user", "meta", isMeta=True),
            line("user", "This session is being continued", isCompactSummary=True),
            json.dumps(
                {"type": "user", "message": {"role": "user", "content": [{"type": "tool_result", "content": "x"}]}}
            ),
            line("user", "side", isSidechain=True),
            json.dumps(
                {
                    "type": "user",
                    "timestamp": "2026-11-01T10:05:00Z",
                    "message": {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": "<system-reminder>x</system-reminder>"},
                            {"type": "text", "text": "third"},
                        ],
                    },
                }
            ),
            line("assistant", "answer three"),
            line("user", "fourth"),
            line("user", "fifth"),
            line("user", "sixth"),
            line("assistant", "the last answer"),
            line("user", "Fake orchestrator loop\n\nLoop pass 11:00"),
            line("assistant", "a pass reply"),
        ]
        self.transcript.write_text("\n".join(rows) + "\n")
        msgs, answer = renew.owner_messages(str(self.transcript), "Fake orchestrator loop")
        self.assertEqual([m["text"] for m in msgs], ["second", "third", "fourth", "fifth", "sixth"])
        self.assertEqual(answer, "the last answer")
        self.assertRegex(msgs[0]["time"], r"^\d\d-\d\d \d\d:\d\d$")
        self.assertEqual(renew.owner_messages(None, ""), ([], ""))
        self.assertEqual(renew.owner_messages(str(self.tmp / "none.jsonl"), ""), ([], ""))

    def test_cut_to_fit_and_whole_in_the_file(self):
        self.project.board.parent.mkdir(parents=True, exist_ok=True)
        self.project.board.write_text("# Board\n\n" + "\n".join(f"- line {i} " + "x" * 80 for i in range(300)))
        self.transcript.write_text(line("user", "keep me " + "y" * 3000) + "\n")
        a = {"tokens_before": 210000, "note": "my note"}
        text = renew.pack(self.project, "S1", str(self.transcript), a)
        self.assertLessEqual(len(text), renew.PACK_MAX_CHARS)
        self.assertIn("my note", text)
        self.assertIn("keep me", text)
        self.assertIn("read it before you act", text)
        whole = renew.pack_file(self.project).read_text()
        self.assertIn("line 299", whole)
        self.assertNotIn("line 299", text)

    def test_any_compaction_brings_the_pack_and_settles_nothing_else(self):
        out = self.resume()
        self.assertIn("was compacted at", out)
        self.assertEqual(self.records(), [])
        self.assertEqual(self.resume(session="S2"), "")  # not the marked session
        self.assertIn("Pick up without asking", self.resume(source="resume"))  # a resumed session: the board
        self.config(loop_extra='renew = "handover"')  # an old key changes nothing: the pack still comes back
        self.assertIn("was compacted at", self.resume())

    def test_the_renewal_mode_of_start(self):
        d = start.collect(self.project, env={"CLAUDE_CODE_SESSION_ID": "S1"}, cwd=str(self.home))
        s = i18n.for_project(self.project)["start"]
        full, short = start.render(self.project, d), start.render(self.project, d, renewal=True)
        self.assertIn(f"## {s['steps']}", full)
        self.assertNotIn(f"## {s['steps']}", short)
        self.assertIn(f"## {s['agents']}", short)
        owner = f"## {s['owner'].format(owner=self.project.owner)}"
        board = f"## {s['board'].format(path=d['board']['path'])}"
        self.assertLess(short.index(owner), short.index(f"## {s['agents']}"))
        self.assertLess(short.index(f"## {s['agents']}"), short.index(board))
        self.assertLess(full.index(board), full.index(owner))

    def test_a_pack_that_fails_leaves_the_board(self):
        from unittest import mock

        with mock.patch.object(renew, "resume_text", side_effect=RuntimeError("boom")):
            ev = {"hook_event_name": "SessionStart", "source": "compact", "session_id": "S1"}
            import contextlib
            import io

            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                hooks.hook_resume(self.project, ev)
        self.assertIn("renewal pack failed (RuntimeError: boom)", buf.getvalue())
        self.assertIn("Pick up without asking", buf.getvalue())
