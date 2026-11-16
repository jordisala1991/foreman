import functools
import os
import subprocess
import sys
import time

import pytest
from helpers import PLUGIN

from foreman import util
from foreman.services import lock
from foreman.services.agents import brief

FM = str(PLUGIN / "bin/foreman")


class TestLock:
    @pytest.fixture(autouse=True)
    def set_up(self, request, env):
        self.request = request  # for the cleanups of helpers
        self.e = env
        os.environ["FOREMAN_LOCKS"] = str(self.e.tmp / "locks")
        with open(self.e.home / "foreman.toml", "a") as f:
            f.write('\n[resources.gpu]\nmax_minutes = 0.05\n[resources.sim]\nslots = ["A", "B"]\n')
        self.log = self.e.tmp / "order.txt"
        yield
        os.environ.pop("FOREMAN_LOCKS")

    def fm_lock(self, name: str, who: str, *cmd: str) -> subprocess.Popen:
        env = {**os.environ, "FOREMAN_AGENT": who, "FOREMAN_TASK": who.upper()}
        proc = subprocess.Popen(
            [sys.executable, FM, "--home", str(self.e.home), "lock", name, "run", f"job {who}", *cmd], env=env, stderr=subprocess.PIPE, text=True
        )
        self.request.addfinalizer(functools.partial(proc.__exit__, None, None, None))  # waits for it and closes its pipes, whatever the test does
        return proc

    def test_one_at_a_time_in_arrival_order(self):
        body = f"echo start $FOREMAN_AGENT >> {self.log}; sleep 0.4; echo end $FOREMAN_AGENT >> {self.log}"
        procs = []
        for who in ("a", "b", "c"):
            procs.append(self.fm_lock("gpu", who, "sh", "-c", body.replace("$FOREMAN_AGENT", who)))
            time.sleep(0.15)
        for pr in procs:
            assert pr.wait(timeout=20) == 0
        assert self.log.read_text().split("\n")[:-1] == ["start a", "end a", "start b", "end b", "start c", "end c"]
        uses = lock.uses(["gpu"])
        assert [u["who"] for u in uses] == ["a", "b", "c"]
        assert uses[2]["waited_s"] > 0.3

    def test_slots_run_side_by_side_with_their_own_slot(self):
        body = f"echo start $FOREMAN_SLOT >> {self.log}; sleep 1.5; echo end $FOREMAN_SLOT >> {self.log}"
        procs = [self.fm_lock("sim", w, "sh", "-c", body) for w in ("a", "b")]
        for pr in procs:
            pr.wait(timeout=20)
        lines = self.log.read_text().split("\n")[:-1]
        assert [ln.split()[0] for ln in lines] == ["start", "start", "end", "end"]  # they overlapped
        assert sorted(ln.split()[1] for ln in lines[:2]) == ["A", "B"]

    def test_the_command_fails_and_the_resource_is_released_with_its_code(self):
        assert self.fm_lock("gpu", "a", "sh", "-c", "exit 3").wait(timeout=20) == 3
        from foreman.core.project.config import Project

        assert lock.declared(Project.load(self.e.home).cfg)["gpu"].status()["held"] == {}

    def test_the_time_limit_stops_the_command(self):
        pr = self.fm_lock("gpu", "a", "sleep", "30")
        assert pr.wait(timeout=60) == 124
        assert "split long work" in pr.stderr.read()
        assert lock.uses(["gpu"])[-1]["event"] == "timeout"

    def test_a_holder_that_died_is_cleaned_up(self):
        from foreman.core.project.config import Project

        dead = subprocess.Popen(["true"])
        dead.wait()
        r = lock.declared(Project.load(self.e.home).cfg)["gpu"]
        util.write_json(r.dir / "state.json", {"held": {"1": {"who": "ghost", "what": "x", "pid": dead.pid, "since": "", "t": 0}}, "queue": []})
        assert self.fm_lock("gpu", "a", "true").wait(timeout=20) == 0

    def test_unknown_resource_and_status(self):
        r = subprocess.run([sys.executable, FM, "--home", str(self.e.home), "lock", "nope", "status"], capture_output=True, text=True)
        assert "unknown resource" in r.stderr
        r = subprocess.run([sys.executable, FM, "--home", str(self.e.home), "lock", "gpu", "status"], capture_output=True, text=True)
        assert r.stdout.strip() == "free"

    def test_the_brief_names_the_resources(self):
        from foreman.core.project.config import Project
        from foreman.core.tasks import backlog

        p = Project.load(self.e.home)
        tid = backlog.add(p, "x", status="ready", criteria="y")
        text = brief.brief(p, backlog.get(backlog.load(p), tid), "responsible")
        assert "`gpu`, `sim` (2 slots)" in text
        assert "foreman lock NAME run" in text
