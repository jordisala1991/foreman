"""A lens entry files a chain parent: in backlog, never planned, with a parent agent before any step, told where its lens
report is; its `foreman done` closes the chain with no step, and is refused while a step is open."""

import json
from collections.abc import Callable
from datetime import datetime

import pytest
from helpers import Env, fm
from test_misled import run_as
from test_parents import resumes

from foreman import util
from foreman.core.project.config import Project
from foreman.core.tasks import backlog, chains, questions
from foreman.loop import tick
from foreman.providers import registry
from foreman.services.delivery import merge
from foreman.steps import parents, schedule, watch
from foreman.ui.panel import views

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
        tid = registry.backlog(self.p).add("one", criteria="c")
        registry.backlog(self.p).set_status(tid, "done")

    def filed(self) -> str:
        (P,) = schedule.run(self.p, datetime.now())
        return P

    def launched(self) -> str:
        P = self.filed()
        tick.tick(self.p)
        return P

    def test_list_show_health_and_panel_carry_the_step(self):
        P = self.launched()
        merge.close(self.p, self.step(P), "done", "x")
        S2 = self.step(P)
        (line,) = [ln for ln in fm(self.e.home, "list").splitlines() if ln.startswith(P)]
        assert "[chain: rules, step 2 of 5]" in line
        assert json.loads(fm(self.e.home, "show", P))["chain"] == {**CHAIN, "step": 2}
        health = fm(self.e.home, "health")
        assert f"(chain step 2 of 5, {S2} open)" in health
        assert f"waits for {P} to close" in health
        assert views.system(self.p)["recurring"][0]["chain"] == {"lens": "rules", "step": 2, "cap": 5, "open": S2}

    def test_before_the_first_step_it_is_step_0(self):
        P = self.launched()
        (line,) = [ln for ln in fm(self.e.home, "list").splitlines() if ln.startswith(P)]
        assert "step 0 of 5" in line
        assert "(chain step 0 of 5)" in fm(self.e.home, "health")
        assert schedule.rows(self.p)[0]["chain"]["open"] == ""

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
        registry.backlog(self.p).add("step", criteria="c", parent=P)
        with pytest.raises(SystemExit):
            run_as(self.e, P, "ty-2", "done", "--summary", "s")
        assert self.e.task(P)["status"] == "backlog"

    def step(self, P: str) -> str:
        """Files a step from the chain's agent and returns its id."""
        before = {i["id"] for i in backlog.tasks(registry.backlog(self.p).load())}
        run_as(self.e, P, "ty-2", "add", "t", "--parent", P, "--detail", "d")
        (sid,) = {i["id"] for i in backlog.tasks(registry.backlog(self.p).load())} - before
        return sid

    def refused(self, P: str) -> str:
        with pytest.raises(SystemExit) as x:
            self.step(P)
        return str(x.value)

    def steps(self, P: str) -> list[backlog.Task]:
        return [i for i in backlog.tasks(registry.backlog(self.p).load()) if i.get("parent") == P]

    @pytest.mark.parametrize("flags", [(), ("--goal", "x", "--criteria", "y", "--model", "opus", "--effort", "high", "--type", "change", "--triage")])
    def test_a_step_is_filed_in_backlog_with_the_parents_fields(self, flags: tuple[str, ...]) -> None:
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

    def fill(self, P: str, n: int) -> None:
        for _ in range(n):
            merge.close(self.p, self.step(P), "done", "x")

    def edit_refused(self, *args: str) -> str:
        with pytest.raises(SystemExit) as x:
            fm(self.e.home, "edit", *args)
        return str(x.value)

    def test_edit_max_steps_renews_the_cap_while_the_agent_runs(self):
        P = self.launched()
        self.fill(P, 5)
        assert "5" in self.refused(P)
        fm(self.e.home, "edit", P, "--max-steps", "6")
        t = self.e.task(P)
        assert (t["chain"], t["status"], t["agent"]["kind"]) == ({**CHAIN, "max_steps": 6}, "backlog", "parent")
        self.step(P)
        assert len(self.steps(P)) == 6

    def test_a_chain_parent_whose_session_ended_is_launched_again_once_the_cap_is_renewed(self):
        P = self.launched()
        self.fill(P, 5)
        old = self.e.task(P)["agent"]
        fm(self.e.home, "edit", P, "--max-steps", "6")
        self.e.set_state(old["name"], "stopped")
        before = len(self.e.calls())
        tick.tick(self.p)
        assert len(self.e.calls()) == before + 1
        new = self.e.task(P)["agent"]
        assert new["session"] != old["session"]
        assert new["session"] in self.e.task(P)["sessions"]
        text = self.e.calls()[-1]["argv"][-1]
        for needle in ("Chain step 5 of 6", "at most 6 steps", *(s["id"] for s in self.steps(P))):
            assert needle in text

    def test_an_idle_chain_parent_is_told_what_the_owner_edited(self):
        P = self.launched()
        self.fill(P, 5)
        parents.notify(self.p)
        for q in questions.undelivered(registry.backlog(self.p).load()):
            registry.backlog(self.p).mark_delivered(q["id"])
        old = self.e.task(P)["agent"]
        self.e.set_state(old["name"], "done")
        registry.backlog(self.p).update(P, agent={**old, "started": "2000-01-01 00:00:00"})
        assert f"parent:{P}" in watch.needs(self.p, registry.rows(self.p))
        fm(self.e.home, "edit", P, "--max-steps", "6")
        tick.tick(self.p)
        (call,) = resumes(self.e, old["session"])
        for needle in ("max steps", "«5»", "«6»", f"foreman show {P}"):
            assert needle in call["argv"][-1]
        self.e.set_state(old["name"], "working")
        assert [n for n in json.loads(fm(self.e.home, "needs", "--json")) if n["kind"] == "parent" and n["task"] == P] == []

    def test_edit_max_steps_is_refused_from_an_agent(self):
        P = self.launched()
        with pytest.raises(SystemExit):
            run_as(self.e, P, "ty-2", "edit", P, "--max-steps", "9")
        assert self.e.task(P)["chain"]["max_steps"] == 5

    def test_edit_max_steps_is_refused_on_a_task_that_is_not_a_chain_parent(self):
        t = registry.backlog(self.p).add("plain", criteria="c")
        assert "not a chain parent" in self.edit_refused(t, "--max-steps", "3")
        assert "chain" not in self.e.task(t)

    def test_edit_max_steps_below_the_steps_filed_is_refused(self):
        P = self.launched()
        self.fill(P, 3)
        assert "3 steps filed" in self.edit_refused(P, "--max-steps", "2")
        assert self.e.task(P)["chain"]["max_steps"] == 5
        fm(self.e.home, "edit", P, "--max-steps", "3")
        assert self.e.task(P)["chain"]["max_steps"] == 3

    def test_edit_max_steps_must_be_a_whole_number_of_1_or_more(self):
        P = self.launched()
        for bad in ("0", "-1", "x"):
            with pytest.raises(SystemExit):
                fm(self.e.home, "edit", P, "--max-steps", bad)
        assert self.e.task(P)["chain"]["max_steps"] == 5

    def test_edit_max_steps_alone_changes_nothing_else(self):
        P = self.launched()
        before = self.e.task(P)
        fm(self.e.home, "edit", P, "--max-steps", "8")
        assert self.e.task(P) == {**before, "chain": {**CHAIN, "max_steps": 8}}

    def told(self, P: str, close: Callable[[str], object]) -> tuple[str, str]:
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
        (["duplicate"], 5, False),
    ],
)
def test_refusal_table(statuses: list[str], max_steps: int, refused: bool) -> None:
    parent = {"id": "P", "status": "backlog", "chain": {**CHAIN, "max_steps": max_steps}}
    steps = [{"id": f"S{n}", "status": st, "parent": "P"} for n, st in enumerate(statuses)]
    assert bool(chains.refusal([parent, *steps], "P")) == refused


@pytest.mark.parametrize(
    ("status", "how", "next_step"),
    [("done", "merged", True), ("canceled", "canceled", False), ("duplicate", "closed as duplicate", True)],
)
def test_notice_table(status: str, how: str, next_step: bool) -> None:
    parent = {"id": "P", "status": "backlog", "chain": CHAIN}
    step = {"id": "S", "title": "t", "status": status, "parent": "P"}
    text = chains.notice(step, [step], parent)
    assert f"was {how}." in text
    assert ("foreman add" in text) == next_step
    assert "foreman done" in text


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
def test_outcome_table(task: backlog.Task, subs: list[backlog.Task], status: str) -> None:
    assert chains.outcome(task, subs) == status


@pytest.mark.parametrize(
    ("statuses", "want"),
    [([], (0, "")), (["done"], (1, "")), (["done", "backlog"], (2, "S2"))],
)
def test_progress_table(statuses: list[str], want: tuple[int, str]) -> None:
    tasks: list[backlog.Task] = [{"id": "P", "status": "backlog", "chain": CHAIN}]
    tasks += [{"id": f"S{n}", "parent": "P", "status": s} for n, s in enumerate(statuses, 1)]
    got = chains.progress(tasks, tasks[0])
    assert got == {"lens": "rules", "step": want[0], "cap": 5, "open": want[1]}
    assert chains.progress(tasks, {"id": "Q", "status": "backlog"}) is None
