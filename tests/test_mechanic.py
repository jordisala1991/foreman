import os
from unittest import mock

import pytest
from helpers import Env, fm

from foreman.core.oversight import diagnosis
from foreman.core.tasks import backlog
from foreman.loop import tick

FULL = "\n".join(f"## {s}\nx" for s in diagnosis.SECTIONS)


class TestSections:
    def test_missing_sections(self):
        names = list(diagnosis.SECTIONS)
        assert diagnosis.missing_sections(FULL) == []
        assert diagnosis.missing_sections(FULL + "\n## Unblock (not the fix)\ny") == []
        assert diagnosis.missing_sections(FULL.replace("## Experiment", "## Other")) == ["Experiment"]
        assert diagnosis.missing_sections("## Seen\na\n## Symptom gone\nb") == []
        assert diagnosis.missing_sections("## Symptom gone\nb") == ["Seen"]
        assert diagnosis.missing_sections("") == names


class TestMechanic:
    @pytest.fixture(autouse=True)
    def set_up(self, request, env):
        self.e = env
        self.tid = backlog.add(self.e.p, "t", detail="evidence", status="triage", source="health:k")
        self.env = mock.patch.dict(os.environ, {"FM_TASK": self.tid})
        self.env.start()
        request.addfinalizer(self.env.stop)

    def tick(self):
        tick.tick(self.e.p)

    def diagnose(self, text=FULL, tid=None):
        f = self.e.tmp / "d.md"
        f.write_text(text)
        os.environ["FM_TASK"] = tid or self.tid
        return fm(self.e.home, "diagnose", "--file", str(f))

    def test_launched_once_and_stays_in_triage(self):
        self.tick()
        self.tick()
        self.e.set_state("ty-1-mechanic", "done")
        self.tick()
        assert len(self.e.calls()) == 1
        assert self.e.calls()[0]["cwd"] == str(self.e.repo)
        assert self.e.task(self.tid)["status"] == "triage"

    def test_session_shows_id_role_and_title(self):
        self.tick()
        argv = self.e.calls()[0]["argv"]
        assert argv[argv.index("-n") + 1] == "TY-1 mechanic · t"

    def test_non_health_triage_gets_none(self):
        e = Env()
        backlog.add(e.p, "t", status="triage")
        tick.tick(e.p)
        assert e.calls() == []

    def test_within_slots(self):
        e = Env(slots=1)
        a = backlog.add(e.p, "a", status="triage", source="health:a")
        backlog.add(e.p, "b", status="triage", source="health:b")
        tick.tick(e.p)
        assert len(e.calls()) == 1
        os.environ["FM_TASK"] = a
        f = e.tmp / "d.md"
        f.write_text(FULL)
        fm(e.home, "diagnose", "--file", str(f))
        tick.tick(e.p)
        assert len(e.calls()) == 2
        assert "ty-2-mechanic" in str(e.calls()[1])

    def test_diagnosis_shows_and_mechanic_stops(self):
        self.tick()
        self.diagnose(FULL + "\nThe root cause sentence.")
        out = fm(self.e.home, "show", self.tid)
        assert "The root cause sentence." in out
        assert "evidence" in out
        assert self.e.task(self.tid)["status"] == "triage"
        self.tick()
        assert self.e.task(self.tid)["mechanic"]["short"] in self.e.stops()

    def test_refuses_incomplete_and_second(self):
        self.tick()
        before = self.e.task(self.tid)["detail"]
        with pytest.raises(SystemExit):
            self.diagnose(FULL.replace("## Experiment", "## Other"))
        assert "diagnosis" not in self.e.task(self.tid)
        assert self.e.task(self.tid)["detail"] == before
        self.diagnose()
        after = self.e.task(self.tid)["detail"]
        with pytest.raises(SystemExit):
            self.diagnose()
        assert self.e.task(self.tid)["detail"] == after

    def test_symptom_gone_accepted(self):
        self.tick()
        self.diagnose("## Seen\na\n## Symptom gone\nb")
        assert "diagnosis" in self.e.task(self.tid)

    def old(self):
        m = self.e.task(self.tid)["mechanic"]
        backlog.update(self.e.p, self.tid, mechanic={**m, "started": "2000-01-01 00:00:00"})

    def test_failures_leave_a_note(self):
        self.tick()
        self.e.set_state("ty-1-mechanic", "done")
        self.old()
        self.tick()
        i = self.e.task(self.tid)
        assert i["diagnosis"]["missing"]
        assert "missing" in i["detail"].lower()
        assert (i["status"], i["blocked"]) == ("triage", "")
        self.tick()
        assert len(self.e.calls()) == 1

    def test_timeout_while_working_stops_it(self):
        self.tick()
        self.old()
        self.tick()
        assert self.e.task(self.tid)["diagnosis"]["missing"]
        assert self.e.task(self.tid)["mechanic"]["short"] in self.e.stops()

    def test_working_and_recent_is_left_alone(self):
        self.tick()
        self.tick()
        assert "diagnosis" not in self.e.task(self.tid)

    def test_unlaunchable(self):
        self.e.p.cfg["agents"]["claude"] = "/nonexistent"
        self.tick()
        i = self.e.task(self.tid)
        assert i["diagnosis"]["missing"].startswith("could not launch")
        assert i["status"] == "triage"

    def test_cancel_stops_it(self):
        self.tick()
        fm(self.e.home, "cancel", self.tid)
        assert self.e.task(self.tid)["mechanic"]["short"] in self.e.stops()

    def test_unlaunchable_is_not_retried(self):
        fake = self.e.p.cfg["agents"]["claude"]
        self.e.p.cfg["agents"]["claude"] = "/nonexistent"
        self.tick()
        self.e.p.cfg["agents"]["claude"] = fake  # claude recovers: the task still gets no mechanic
        self.tick()
        assert self.e.calls() == []
        assert "mechanic" not in self.e.task(self.tid)

    def test_gone_session_is_not_stopped_again(self):
        self.tick()
        self.diagnose()
        (self.e.bin / "agents.json").write_text("[]")
        self.tick()
        self.tick()
        assert self.e.stops() == []
