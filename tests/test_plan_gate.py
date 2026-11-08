import unittest

from helpers import Env, fm

from fm import backlog, loop, panel_actions, panel_views, plan_gate


def plan(risk: str) -> str:
    return f"# Plan\n\n- **Steps:** one\n- **Declared risk:** {risk}\n"


class PlanRisk(unittest.TestCase):
    def test_declared_risk_to_level(self):
        for text, level in [
            ("none", "low"),
            ("new dependency", "medium"),
            ("Data-format change", "medium"),
            ("size", "medium"),
            ("sensitive paths", "high"),
            ("architecture", "high"),
            ("open decision", "high"),
            ("size; open decision", "high"),
            ("new dependency; size", "medium"),
            ("", "high"),
            ("it is fine", "high"),
            ("**none**.", "low"),
            ("Risk of data loss; none of the tests cover it", "high"),
            ("not none", "high"),
        ]:
            self.assertEqual(plan_gate.plan_risk(plan(text))[0], level, text)

    def test_missing_section_or_heading_form(self):
        self.assertEqual(plan_gate.plan_risk("# Plan\n\nSteps only\n")[0], "high")
        self.assertEqual(plan_gate.plan_risk("## Declared risk\nnone\n\n## Other\nsize\n")[0], "low")
        self.assertEqual(plan_gate.plan_risk("- **Declared risk:** none\n- **Risks:** size\n")[0], "low")


class Gate(unittest.TestCase):
    def make(self, autonomy: str, risk: str | None):
        e = Env(autonomy=autonomy)
        i = backlog.add(e.p, "t", status="ready", criterion="c")
        if risk is not None:
            f = e.home / "plan.md"
            f.write_text(plan(risk))
            backlog.update(e.p, i, plan_file=str(f))
        return e, i

    def test_table(self):
        rows = {
            "manual": {"none": False, "size": False, "open decision": False},
            "low": {"none": True, "size": False, "open decision": False},
            "medium": {"none": True, "size": True, "open decision": False},
        }
        for autonomy, cases in rows.items():
            for risk, launched in cases.items():
                e, i = self.make(autonomy, risk)
                loop.launch_free(e.p)
                it = e.issue(i)
                self.assertEqual(it["status"] == "in progress", launched, (autonomy, risk))
                self.assertEqual(plan_gate.pending(e.p, it), not launched)
                if launched:
                    self.assertEqual(it["plan_approved"]["by"], f"autonomy level {autonomy}")
                    ev = [x["text"] for x in backlog.util.read_jsonl(e.p.state / "events.jsonl")]
                    self.assertIn(f"plan approved by autonomy level {autonomy}", ev)

    def test_no_plan_and_backlog_launch_as_before(self):
        for autonomy in ("manual", "low", "medium"):
            e, i = self.make(autonomy, None)
            d = backlog.add(e.p, "vague")
            loop.launch_free(e.p)
            self.assertEqual(e.issue(i)["status"], "in progress")
            self.assertEqual(e.issue(d)["status"], "refining")

    def test_approve_launches_even_at_manual_and_is_visible_before(self):
        e, i = self.make("manual", "open decision")
        loop.launch_free(e.p)
        self.assertEqual(e.calls(), [])
        self.assertTrue(panel_views.issue(e.p, i)["issue"]["plan_pending"])
        self.assertEqual(panel_views.needs(e.p, backlog.load(e.p)), 1)
        fm(e.home, "approve-plan", i)
        self.assertEqual(e.issue(i)["plan_approved"]["by"], "owner")
        loop.launch_free(e.p)
        self.assertEqual(e.issue(i)["status"], "in progress")

    def test_reject_sends_back_to_a_definer(self):
        e, i = self.make("manual", "size")
        fm(e.home, "approve-plan", i)
        backlog.update(e.p, i, plan_approved=None)
        fm(e.home, "reject-plan", i, "--note", "too big")
        it = e.issue(i)
        self.assertEqual(it["status"], "backlog")
        self.assertIn("too big", it["detail"])
        self.assertNotIn("plan_approved", it)
        loop.launch_free(e.p)
        self.assertEqual(e.issue(i)["status"], "refining")

    def test_refused_when_nothing_waits(self):
        e, i = self.make("manual", "size")
        fm(e.home, "approve-plan", i)
        n = backlog.add(e.p, "no plan", status="ready", criterion="c")
        for args in (("approve-plan", i), ("reject-plan", i, "--note", "x"), ("approve-plan", n), ("reject-plan", n, "--note", "x")):
            with self.assertRaises(SystemExit) as r:
                fm(e.home, *args)
            self.assertNotEqual(r.exception.code, 0)

    def test_panel_handlers(self):
        e, i = self.make("manual", "size")
        with self.assertRaises(panel_actions.Refused):
            panel_actions.reject_plan(e.p, i, "", {})
        panel_actions.approve_plan(e.p, i, "", {})
        self.assertTrue(e.issue(i)["plan_approved"])
        with self.assertRaises(panel_actions.Refused):
            panel_actions.approve_plan(e.p, i, "", {})
        j = self.make("manual", "size")
        panel_actions.reject_plan(j[0].p, j[1], "smaller", {})
        self.assertEqual(j[0].issue(j[1])["status"], "backlog")

    def test_ready_clears_the_approval(self):
        e, i = self.make("manual", "size")
        fm(e.home, "approve-plan", i)
        backlog.update(e.p, i, status="refining")
        import os
        from unittest import mock

        with mock.patch.dict(os.environ, {"FM_ISSUE": i}):
            fm(e.home, "ready", "--claim", "g", "--criterion", "c", "--plan", str(e.home / "plan.md"))
        self.assertNotIn("plan_approved", e.issue(i))


if __name__ == "__main__":
    unittest.main()
