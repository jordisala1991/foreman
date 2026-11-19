"""`foreman relaunch FN-n`: the task's idle agent is stopped and a copy of its session (same conversation, new id) starts
with today's flags; the copy becomes the task's session and the loop talks to it. Refused, with nothing stopped, for a
busy agent, no live agent, or when an agent runs it."""

import json
import os
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any
from unittest import mock

import pytest
from fakerepo import FakeRepo
from helpers import Env, fm

from foreman import launcher, util
from foreman.core.oversight import diagnosis
from foreman.core.tasks import backlog, questions
from foreman.loop import tick
from foreman.loop.launch import launch_free
from foreman.services.agents import relaunch, running
from foreman.services.claude import sessions
from foreman.steps import mechanic, talk

CHAIN: backlog.Chain = {"lens": "rules", "max_steps": 5, "max_lines": 150}


def planning(e: Env) -> tuple[str, str]:
    tid = backlog.add(e.p, "x", status="backlog", criteria="y")
    launch_free(e.p)
    return tid, "agent"


def responsible(e: Env) -> tuple[str, str]:
    tid = backlog.add(e.p, "x", status="ready", criteria="y")
    launch_free(e.p)
    return tid, "agent"


def mechanic_(e: Env) -> tuple[str, str]:
    tid = backlog.add(e.p, "t", detail="evidence", status="triage", source="health:k")
    mechanic.launch(e.p, e.task(tid))
    return tid, "mechanic"


def parent(e: Env) -> tuple[str, str]:
    tid = backlog.add(e.p, "parent", status="triage", criteria="c")
    fm(e.home, "add", "s", "--parent", tid, "--triage", "--criteria", "c")
    fm(e.home, "go", tid)
    tick.tick(e.p)
    return tid, "agent"


def chain_parent(e: Env) -> tuple[str, str]:
    tid = backlog.add(e.p, "Lens rules", status="backlog", criteria="c", chain=CHAIN)
    tick.tick(e.p)
    return tid, "agent"


KINDS: dict[str, Callable[[Env], tuple[str, str]]] = {
    "planning": planning,
    "responsible": responsible,
    "mechanic": mechanic_,
    "parent": parent,
    "chain parent": chain_parent,
}


def idle(e: Env, tid: str, role: str) -> dict[str, Any]:
    a = e.task(tid)[role]
    e.set_state(a["name"], "done")
    return a


def rows(e: Env) -> list[dict[str, Any]]:
    return json.loads((e.bin / "agents.json").read_text())


def refused(e: Env, tid: str) -> str:
    before = e.task(tid)
    with pytest.raises(SystemExit) as x:
        fm(e.home, "relaunch", tid)
    msg = str(x.value)
    assert "\n" not in msg
    assert e.stops() == []
    assert not [c for c in e.calls() if "--resume" in c["argv"]]
    assert e.task(tid) == before
    return msg


@pytest.mark.usefixtures("owner")
class TestRelaunch:
    @pytest.fixture(autouse=True)
    def set_up(self, fake_git: FakeRepo) -> None:
        self.e = Env(repo=fake_git)

    def test_the_copy_runs_todays_settings(self) -> None:
        e = self.e
        tid, role = responsible(e)
        old = idle(e, tid, role)
        assert fm(e.home, "relaunch", tid).strip().startswith(f"{tid}: relaunched, session {old['short']} → ")
        argv = e.calls()[-1]["argv"]
        assert argv[:3] == ["--bg", "--resume", old["session"]]
        s = json.loads(argv[argv.index("--settings") + 1])
        assert s["env"]["PATH"].startswith(str(launcher.ROOT / "bin"))
        assert s["env"]["FOREMAN_TASK"] == tid
        assert "guard" in s["hooks"]["PreToolUse"][0]["hooks"][0]["command"]
        for flag, value in (("--model", old["model"]), ("--effort", old["effort"]), ("--permission-mode", "bypassPermissions")):
            assert argv[argv.index(flag) + 1] == value
        assert argv[argv.index("-n") + 1] == util.session_title(tid, old["kind"], "x")
        assert e.stops() == [old["short"]]

    @pytest.mark.parametrize("kind", list(KINDS))
    def test_every_kind_gets_the_copy_and_the_loop_talks_to_it(self, kind: str) -> None:
        e = self.e
        tid, role = KINDS[kind](e)
        old = idle(e, tid, role)
        assert old["kind"] == {"chain parent": "parent"}.get(kind, kind)
        before = e.task(tid)
        fm(e.home, "relaunch", tid)
        new = rows(e)[-1]
        after = e.task(tid)
        a = after[role]
        assert (a["short"], a["session"]) == (new["id"], new["sessionId"]) != (old["short"], old["session"])
        assert after["session_kinds"][a["session"]] == old["kind"]
        assert a["session"] in after["sessions"]
        keep = ("agent", "mechanic", "sessions", "session_kinds")
        assert {k: v for k, v in after.items() if k not in keep} == {k: v for k, v in before.items() if k not in keep}
        assert json.loads(fm(e.home, "show", tid))[role]["short"] == new["id"]
        events = [ev["text"] for ev in util.read_jsonl(e.p.state / "events.jsonl") if ev.get("task") == tid]
        assert f"session {old['short']} → {new['id']}" in events[-1]
        e.set_state(old["name"], "done")
        if role == "mechanic":
            diagnosis.record(e.p, tid, "Diagnosis", "\n".join(f"## {s}\nx" for s in diagnosis.SECTIONS))
            tick.tick(e.p)
            assert e.stops()[-1] == new["id"]
        else:
            questions.answer(e.p, questions.ask(e.p, tid, old["name"], "A or B?"), "B")
            talk.deliver_answers(e.p, sessions.listing(e.p))
            assert e.calls()[-1]["argv"][:3] == ["--bg", "--resume", new["sessionId"]]
            assert len(e.calls()[-1]["argv"]) == 4

    def test_a_working_agent_is_refused(self) -> None:
        tid, _ = responsible(self.e)
        assert "is working: relaunch it when it is idle" in refused(self.e, tid)

    def test_a_task_with_no_agent_is_refused(self) -> None:
        tid = backlog.add(self.e.p, "x", status="ready", criteria="y")
        assert refused(self.e, tid) == f"foreman: {tid} has no live agent"

    def test_a_stopped_agent_is_refused(self) -> None:
        tid, role = responsible(self.e)
        self.e.set_state(self.e.task(tid)[role]["name"], "stopped")
        assert refused(self.e, tid) == f"foreman: {tid} has no live agent"

    def test_an_unreadable_listing_is_refused(self) -> None:
        tid, role = responsible(self.e)
        idle(self.e, tid, role)
        self.e.p.cfg["agents"]["claude"] = str(Path(self.e.tmp) / "missing")
        with pytest.raises(sessions.SessionError, match="claude agents is unreadable"):
            relaunch.relaunch(self.e.p, tid)
        assert self.e.stops() == []

    def test_an_agent_cannot_relaunch(self) -> None:
        tid, role = responsible(self.e)
        idle(self.e, tid, role)
        with mock.patch.dict(os.environ, {"FOREMAN_TASK": tid}):
            assert refused(self.e, tid) == "foreman: relaunch is the owner's, not an agent's"

    def test_no_copy_keeps_the_old_session(self) -> None:
        e = self.e
        tid, role = responsible(e)
        old = idle(e, tid, role)
        (e.bin / "resume_same_id").write_text("")
        with pytest.raises(SystemExit, match="kept session"):
            fm(e.home, "relaunch", tid)
        assert e.task(tid)[role]["session"] == old["session"]
        events = [ev["text"] for ev in util.read_jsonl(e.p.state / "events.jsonl") if ev.get("task") == tid]
        assert events[-1].startswith(f"relaunch of {old['name']} failed: claude kept session")

    def test_a_task_closed_meanwhile_gets_no_copy(self) -> None:
        e = self.e
        tid, role = responsible(e)
        old = idle(e, tid, role)
        real = sessions.relaunch

        def cancel_meanwhile(*args: Any) -> tuple[str, str | None]:
            out = real(*args)
            backlog.set_status(e.p, tid, "canceled", "by the owner")
            return out

        with mock.patch.object(sessions, "relaunch", cancel_meanwhile), pytest.raises(SystemExit, match="changed while its agent restarted"):
            fm(e.home, "relaunch", tid)
        new = rows(e)[-1]["id"]
        assert e.stops() == [old["short"], new]
        assert e.task(tid)[role]["session"] == old["session"]

    def test_a_send_waits_for_the_relaunch_and_goes_to_the_copy(self) -> None:
        e = self.e
        tid, role = responsible(e)
        old = idle(e, tid, role)
        stale = e.task(tid)  # the loop read the task before the relaunch
        real = sessions.relaunch
        sender = threading.Thread(target=running.send, args=(e.p, stale, "hi"))

        def send_meanwhile(*args: Any) -> tuple[str, str | None]:
            sender.start()
            sender.join(0.5)
            assert sender.is_alive()  # the send waits for the relaunch
            return real(*args)

        with mock.patch.object(sessions, "relaunch", send_meanwhile):
            fm(e.home, "relaunch", tid)
        sender.join(10)
        new = e.task(tid)[role]
        assert not [c for c in e.calls() if c["argv"][:3] == ["--bg", "--resume", old["session"]] and len(c["argv"]) == 4]
        events = [ev["text"] for ev in util.read_jsonl(e.p.state / "events.jsonl") if ev.get("task") == tid]
        assert f"session {new['session'][:8]} is working" in events[-1]  # the send went to the copy (busy: retried next tick)
