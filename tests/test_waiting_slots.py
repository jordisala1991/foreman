"""A task waiting for the owner (open question or permission, no answer on its way) frees its slot (FN-173)."""

import pytest
from helpers import Env

from foreman.core.tasks import questions
from foreman.loop import tick
from foreman.providers import registry


def three_ready(e: Env) -> tuple[str, str, str]:
    a, b, c = (registry.backlog(e.p).add("x", status="ready", criteria="y") for _ in range(3))
    tick.tick(e.p)
    return a, b, c


class TestWaitingSlots:
    @pytest.mark.parametrize("f", ({"kind": "question"}, {"kind": "permission", "action": "git push"}))
    def test_a_question_or_permission_frees_the_slot(self, f: dict[str, str]):
        e = Env(slots=2)
        a, b, c = three_ready(e)
        assert (e.task(c)["status"], len(e.calls())) == ("ready", 2)
        registry.backlog(e.p).ask(a, a.lower(), "A or B?", **f)
        tick.tick(e.p)
        assert (e.task(c)["status"], len(e.calls())) == ("in progress", 3)
        assert all(e.task(t).get("agent") for t in (a, b, c))

    def test_the_answer_reaches_the_agent_over_the_slots(self):
        e = Env(slots=2)
        a, _, c = three_ready(e)
        qid = registry.backlog(e.p).ask(a, a.lower(), "A or B?")
        tick.tick(e.p)
        assert e.task(c)["status"] == "in progress"
        d = registry.backlog(e.p).add("x", status="ready", criteria="y")
        registry.backlog(e.p).answer(qid, "B")
        e.set_state(a.lower(), "done")
        tick.tick(e.p)
        resume = e.calls()[-1]["argv"]
        assert resume[resume.index("--resume") + 1] == e.task(a)["agent"]["session"]
        assert "«B»" in resume[-1]
        assert questions.undelivered(registry.backlog(e.p).load()) == []
        assert e.task(d)["status"] == "ready"

    def test_an_answer_not_delivered_yet_holds_the_slot(self):
        e = Env(slots=2)
        a, _, c = three_ready(e)
        registry.backlog(e.p).answer(registry.backlog(e.p).ask(a, a.lower(), "A or B?"), "B")
        tick.tick(e.p)  # a's session is still working: the answer waits, and a keeps its slot
        assert (e.task(c)["status"], len(e.calls())) == ("ready", 2)

    def test_running_agents_fill_the_slots(self):
        e = Env(slots=2)
        _, _, c = three_ready(e)
        tick.tick(e.p)
        assert (e.task(c)["status"], len(e.calls())) == ("ready", 2)
