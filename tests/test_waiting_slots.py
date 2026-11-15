"""A task waiting for the owner (open question or permission, no answer on its way) frees its slot (FN-173)."""

import unittest

from helpers import Env

from fm import backlog, loop


def three_ready(e: Env) -> tuple[str, str, str]:
    a, b, c = (backlog.add(e.p, "x", status="ready", criteria="y") for _ in range(3))
    loop.tick(e.p)
    return a, b, c


class WaitingSlots(unittest.TestCase):
    def test_a_question_or_permission_frees_the_slot(self):
        for f in ({"kind": "question"}, {"kind": "permission", "action": "git push"}):
            with self.subTest(**f):
                e = Env(slots=2)
                a, b, c = three_ready(e)
                self.assertEqual((e.task(c)["status"], len(e.calls())), ("ready", 2))
                backlog.ask(e.p, a, a.lower(), "A or B?", **f)
                loop.tick(e.p)
                self.assertEqual((e.task(c)["status"], len(e.calls())), ("in progress", 3))
                self.assertTrue(all(e.task(t).get("agent") for t in (a, b, c)))

    def test_the_answer_reaches_the_agent_over_the_slots(self):
        e = Env(slots=2)
        a, _, c = three_ready(e)
        qid = backlog.ask(e.p, a, a.lower(), "A or B?")
        loop.tick(e.p)
        self.assertEqual(e.task(c)["status"], "in progress")
        d = backlog.add(e.p, "x", status="ready", criteria="y")
        backlog.answer(e.p, qid, "B")
        e.set_state(a.lower(), "done")
        loop.tick(e.p)
        resume = e.calls()[-1]["argv"]
        self.assertEqual(resume[resume.index("--resume") + 1], e.task(a)["agent"]["session"])
        self.assertIn("«B»", resume[-1])
        self.assertEqual(backlog.undelivered(backlog.load(e.p)), [])
        self.assertEqual(e.task(d)["status"], "ready")

    def test_an_answer_not_delivered_yet_holds_the_slot(self):
        e = Env(slots=2)
        a, _, c = three_ready(e)
        backlog.answer(e.p, backlog.ask(e.p, a, a.lower(), "A or B?"), "B")
        loop.tick(e.p)  # a's session is still working: the answer waits, and a keeps its slot
        self.assertEqual((e.task(c)["status"], len(e.calls())), ("ready", 2))

    def test_running_agents_fill_the_slots(self):
        e = Env(slots=2)
        _, _, c = three_ready(e)
        loop.tick(e.p)
        self.assertEqual((e.task(c)["status"], len(e.calls())), ("ready", 2))


if __name__ == "__main__":
    unittest.main()
