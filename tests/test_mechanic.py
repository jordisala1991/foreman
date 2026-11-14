import os
import unittest
from unittest import mock

from helpers import Env, fm

from fm import backlog, diagnosis, loop

FULL = "\n".join(f"## {s}\nx" for s in diagnosis.SECTIONS)


class Sections(unittest.TestCase):
    def test_missing_sections(self):
        names = list(diagnosis.SECTIONS)
        self.assertEqual(diagnosis.missing_sections(FULL), [])
        self.assertEqual(diagnosis.missing_sections(FULL + "\n## Unblock (not the fix)\ny"), [])
        self.assertEqual(diagnosis.missing_sections(FULL.replace("## Experiment", "## Other")), ["Experiment"])
        self.assertEqual(diagnosis.missing_sections("## Seen\na\n## Symptom gone\nb"), [])
        self.assertEqual(diagnosis.missing_sections("## Symptom gone\nb"), ["Seen"])
        self.assertEqual(diagnosis.missing_sections(""), names)


class Mechanic(unittest.TestCase):
    def setUp(self):
        self.e = Env()
        self.iid = backlog.add(self.e.p, "t", detail="evidence", status="triage", source="health:k")
        self.env = mock.patch.dict(os.environ, {"FM_ISSUE": self.iid})
        self.env.start()
        self.addCleanup(self.env.stop)

    def tick(self):
        loop.tick(self.e.p)

    def diagnose(self, text=FULL, iid=None):
        f = self.e.tmp / "d.md"
        f.write_text(text)
        os.environ["FM_ISSUE"] = iid or self.iid
        return fm(self.e.home, "diagnose", "--file", str(f))

    def test_launched_once_and_stays_in_triage(self):
        self.tick()
        self.tick()
        self.e.set_state("ty-1-mechanic", "done")
        self.tick()
        self.assertEqual(len(self.e.calls()), 1)
        self.assertEqual(self.e.calls()[0]["cwd"], str(self.e.repo))
        self.assertEqual(self.e.issue(self.iid)["status"], "triage")

    def test_non_health_triage_gets_none(self):
        e = Env()
        backlog.add(e.p, "t", status="triage")
        loop.tick(e.p)
        self.assertEqual(e.calls(), [])

    def test_within_slots(self):
        e = Env(slots=1)
        a = backlog.add(e.p, "a", status="triage", source="health:a")
        backlog.add(e.p, "b", status="triage", source="health:b")
        loop.tick(e.p)
        self.assertEqual(len(e.calls()), 1)
        os.environ["FM_ISSUE"] = a
        f = e.tmp / "d.md"
        f.write_text(FULL)
        fm(e.home, "diagnose", "--file", str(f))
        loop.tick(e.p)
        self.assertEqual(len(e.calls()), 2)
        self.assertIn("ty-2-mechanic", str(e.calls()[1]))

    def test_diagnosis_shows_and_mechanic_stops(self):
        self.tick()
        self.diagnose(FULL + "\nThe root cause sentence.")
        out = fm(self.e.home, "show", self.iid)
        self.assertIn("The root cause sentence.", out)
        self.assertIn("evidence", out)
        self.assertEqual(self.e.issue(self.iid)["status"], "triage")
        self.tick()
        self.assertIn(self.e.issue(self.iid)["mechanic"]["short"], self.e.stops())

    def test_refuses_incomplete_and_second(self):
        self.tick()
        before = self.e.issue(self.iid)["detail"]
        with self.assertRaises(SystemExit):
            self.diagnose(FULL.replace("## Experiment", "## Other"))
        self.assertNotIn("diagnosis", self.e.issue(self.iid))
        self.assertEqual(self.e.issue(self.iid)["detail"], before)
        self.diagnose()
        after = self.e.issue(self.iid)["detail"]
        with self.assertRaises(SystemExit):
            self.diagnose()
        self.assertEqual(self.e.issue(self.iid)["detail"], after)

    def test_symptom_gone_accepted(self):
        self.tick()
        self.diagnose("## Seen\na\n## Symptom gone\nb")
        self.assertIn("diagnosis", self.e.issue(self.iid))

    def old(self):
        m = self.e.issue(self.iid)["mechanic"]
        backlog.update(self.e.p, self.iid, mechanic={**m, "started": "2000-01-01 00:00:00"})

    def test_failures_leave_a_note(self):
        self.tick()
        self.e.set_state("ty-1-mechanic", "done")
        self.old()
        self.tick()
        i = self.e.issue(self.iid)
        self.assertTrue(i["diagnosis"]["missing"])
        self.assertIn("missing", i["detail"].lower())
        self.assertEqual((i["status"], i["blocked"]), ("triage", ""))
        self.tick()
        self.assertEqual(len(self.e.calls()), 1)

    def test_timeout_while_working_stops_it(self):
        self.tick()
        self.old()
        self.tick()
        self.assertTrue(self.e.issue(self.iid)["diagnosis"]["missing"])
        self.assertIn(self.e.issue(self.iid)["mechanic"]["short"], self.e.stops())

    def test_working_and_recent_is_left_alone(self):
        self.tick()
        self.tick()
        self.assertNotIn("diagnosis", self.e.issue(self.iid))

    def test_unlaunchable(self):
        self.e.p.cfg["agents"]["claude"] = "/nonexistent"
        self.tick()
        i = self.e.issue(self.iid)
        self.assertTrue(i["diagnosis"]["missing"].startswith("could not launch"))
        self.assertEqual(i["status"], "triage")

    def test_cancel_stops_it(self):
        self.tick()
        fm(self.e.home, "cancel", self.iid)
        self.assertIn(self.e.issue(self.iid)["mechanic"]["short"], self.e.stops())
