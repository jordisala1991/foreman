"""A lens entry files a chain parent: in backlog, never planned, with a parent agent before any step, told where its lens
report is; its `foreman done` closes the chain with no step, and is refused while a step is open."""

from datetime import datetime

import pytest
from helpers import Env
from test_misled import run_as

from foreman import util
from foreman.core.project.config import Project
from foreman.core.tasks import backlog, chains
from foreman.loop import tick
from foreman.steps import schedule

T0 = datetime(2026, 11, 15, 0, 0, 0)
SEEN = "2026-11-15 00:00:00"
CHAIN = {"lens": "rules", "max_steps": 5, "max_lines": 150}
RULES = '[[recurring]]\nname = "rules"\ntitle = "Lens rules"\nlens = "rules"\nmax_steps = 5\nmax_lines = 150\ncriteria = "c"\nevery_merges = 1\n'


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
