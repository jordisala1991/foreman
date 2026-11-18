"""A lens entry files a chain parent: in backlog, never planned, with a parent agent before any step, told where its lens
report is; its `foreman done` closes the chain with no step, and is refused while a step is open."""

from datetime import datetime

import pytest
from helpers import Env, fm
from test_misled import run_as
from test_parents import resumes

from foreman import util
from foreman.core.project.config import Project
from foreman.core.tasks import backlog, chains
from foreman.loop import tick
from foreman.services.delivery import merge
from foreman.steps import schedule

T0 = datetime(2026, 11, 15, 0, 0, 0)
SEEN = "2026-11-15 00:00:00"
CHAIN = {"lens": "rules", "max_steps": 5, "max_lines": 150}
RULES = (
    '[[recurring]]\nname = "rules"\ntitle = "Lens rules"\nlens = "rules"\nmax_steps = 5\nmax_lines = 150\ncriteria = "c"\n'
    'goal = "g"\nmodel = "sonnet"\neffort = "low"\ntype = "bug"\nevery_merges = 1\n'
)


@pytest.mark.usefixtures("owner")
class TestChainParent:
    @pytest.fixture(autouse=True)
    def set_up(self, env: Env) -> None:
        self.e = env
        f = env.home / "foreman.toml"
        f.write_text(f.read_text().split("[[recurring]]")[0] + RULES)
        self.p = Project.load(env.home)
        util.write_json(self.p.state / "recurring.json", {"rules": SEEN})
        tid = backlog.add(self.p, "one", criteria="c")
        backlog.set_status(self.p, tid, "done")

    def filed(self) -> str:
        (P,) = schedule.run(self.p, datetime.now())
        return P

    def launched(self) -> str:
        P = self.filed()
        tick.tick(self.p)
        return P

    def test_a_lens_entry_files_a_chain_parent_in_backlog(self):
        t = self.e.task(self.filed())
        assert (t["status"], t["chain"], t["source"]) == ("backlog", CHAIN, "recurring:rules")

    def test_the_chain_parent_is_never_planned_and_gets_its_agent(self):
        P = self.launched()
        (call,) = self.e.calls()
        argv = call["argv"]
        assert argv[argv.index("-n") + 1] == f"{P} parent · Lens rules"
        t = self.e.task(P)
        assert (t["agent"]["kind"], t["status"]) == ("parent", "backlog")
        assert "worktree" not in t
        tick.tick(self.p)
        assert len(self.e.calls()) == 1

    def test_the_agent_is_told_where_the_lens_report_is(self):
        self.launched()
        text = self.e.calls()[0]["argv"][-1]
        assert str(self.e.home / "reports" / "lens-rules.md") in text
        for needle in ("at most 150 changed lines", "at most 5 steps", "## Direction", "## Frontier", "## History"):
            assert needle in text
        for left in ("$id", "$lens", "$max_steps", "$max_lines", "$repo", "$main"):
            assert left not in text
        assert "--triage" not in text
        assert "wait for his answer" not in text

    def test_done_closes_a_chain_with_no_step(self):
        P = self.launched()
        run_as(self.e, P, "ty-2", "done", "--summary", "enough for now")
        assert self.e.task(P)["status"] == "done"
        tick.tick(self.p)
        assert self.e.task(P)["agent"]["short"] in self.e.stops()

    def test_done_is_refused_while_a_step_is_open(self):
        P = self.launched()
        backlog.add(self.p, "step", criteria="c", parent=P)
        with pytest.raises(SystemExit):
            run_as(self.e, P, "ty-2", "done", "--summary", "s")
        assert self.e.task(P)["status"] == "backlog"

    def step(self, P: str) -> str:
        """Files a step from the chain's agent and returns its id."""
        before = {i["id"] for i in backlog.tasks(backlog.load(self.p))}
        run_as(self.e, P, "ty-2", "add", "t", "--parent", P, "--detail", "d")
        (sid,) = {i["id"] for i in backlog.tasks(backlog.load(self.p))} - before
        return sid

    def refused(self, P: str) -> str:
        with pytest.raises(SystemExit) as x:
            self.step(P)
        return str(x.value)

    def steps(self, P: str) -> list[backlog.Task]:
        return [i for i in backlog.tasks(backlog.load(self.p)) if i.get("parent") == P]

    @pytest.mark.parametrize("flags", [(), ("--goal", "x", "--criteria", "y", "--model", "opus", "--effort", "high", "--type", "change", "--triage")])
    def test_a_step_is_filed_in_backlog_with_the_parents_fields(self, flags):
        P = self.launched()
        run_as(self.e, P, "ty-2", "add", "t", "--parent", P, "--detail", "d", *flags)
        (s,) = self.steps(P)
        got = (s["status"], s["acceptance"], s["model"], s["effort"], s["type"])
        assert got == ("backlog", {"goal": "g", "criteria": "c"}, "sonnet", "low", "bug")

    def test_a_second_step_is_refused_while_one_is_open(self):
        P = self.launched()
        S = self.step(P)
        assert S in self.refused(P)
        assert len(self.steps(P)) == 1

    def test_a_step_after_a_canceled_one_is_refused(self):
        P = self.launched()
        S = self.step(P)
        fm(self.e.home, "cancel", S)
        assert S in self.refused(P)
        assert len(self.steps(P)) == 1

    def test_a_step_after_a_merged_one_is_filed(self):
        P = self.launched()
        merge.close(self.p, self.step(P), "done", "x")
        self.step(P)
        assert len(self.steps(P)) == 2

    def test_the_sixth_step_is_refused(self):
        P = self.launched()
        for _ in range(5):
            merge.close(self.p, self.step(P), "done", "x")
        assert "5" in self.refused(P)
        assert len(self.steps(P)) == 5

    def told(self, P: str, close) -> tuple[str, str]:
        """Files a step, closes it with `close`, idles the loop and returns the step id and the one message its agent got."""
        sid = self.e.task(P)["agent"]["session"]
        S = self.step(P)
        close(S)
        for _ in range(2):
            self.e.set_state("ty-2", "done")
            tick.tick(self.p)
        (msg,) = resumes(self.e, sid)
        return S, msg["argv"][3]

    def test_the_agent_is_told_to_file_the_next_step_or_close(self):
        P = self.launched()
        S, text = self.told(P, lambda S: merge.close(self.p, S, "done", "x"))
        assert S in text
        assert "foreman add" in text
        assert "foreman done" in text

    def test_the_agent_is_told_to_close_after_a_canceled_step(self):
        P = self.launched()
        S, text = self.told(P, lambda S: fm(self.e.home, "cancel", S))
        assert S in text
        assert "foreman done" in text
        assert "foreman add" not in text


@pytest.mark.parametrize(
    ("statuses", "max_steps", "refused"),
    [
        ([], 5, False),
        (["done"], 5, False),
        (["done"] * 4, 5, False),
        (["backlog"], 5, True),
        (["canceled"], 5, True),
        (["done", "canceled"], 5, True),
        (["done"] * 5, 5, True),
    ],
)
def test_refusal_table(statuses, max_steps, refused):
    parent = {"id": "P", "status": "backlog", "chain": {**CHAIN, "max_steps": max_steps}}
    steps = [{"id": f"S{n}", "status": st, "parent": "P"} for n, st in enumerate(statuses)]
    assert bool(chains.refusal([parent, *steps], "P")) == refused


def test_no_refusal_under_a_parent_that_is_not_a_chain():
    assert chains.refusal([{"id": "P", "status": "backlog"}, {"id": "S", "status": "backlog", "parent": "P"}], "P") == ""


@pytest.mark.parametrize(
    ("task", "subs", "status"),
    [
        ({"chain": CHAIN}, [], "done"),
        ({}, [], ""),
        ({"chain": CHAIN}, [{"id": "S", "status": "backlog"}], ""),
        ({"chain": CHAIN}, [{"id": "S", "status": "done"}], "done"),
    ],
)
def test_outcome_table(task, subs, status):
    assert chains.outcome(task, subs) == status
