import functools
import itertools
import os
import subprocess
import sys
from collections.abc import Callable
from unittest import mock

import pytest
from helpers import PLUGIN

from foreman import util
from foreman.providers import registry
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
        yield
        os.environ.pop("FOREMAN_LOCKS")

    def fm_lock(self, name: str, who: str, *cmd: str) -> subprocess.Popen[str]:
        env = {**os.environ, "FOREMAN_AGENT": who, "FOREMAN_TASK": who.upper()}
        proc = subprocess.Popen(
            [sys.executable, FM, "--home", str(self.e.home), "lock", name, "run", f"job {who}", *cmd], env=env, stderr=subprocess.PIPE, text=True
        )
        self.request.addfinalizer(functools.partial(proc.__exit__, None, None, None))  # waits for it and closes its pipes, whatever the test does
        return proc

    def test_one_at_a_time_in_arrival_order(self):
        """a and b are in the queue when c comes; c runs after both, each given the slot only after the one before released it."""
        events: list[str] = []
        ticks = itertools.count(1)
        turns: list[Callable[[], None]] = []

        env_of_c: dict[str, str] = {}

        def spawn(cmd, env, start_new_session):
            events.append("c runs")
            env_of_c.update(env)
            return mock.Mock(wait=lambda timeout: 0)

        def pause(_s):
            turns.pop(0)()

        r = lock.Resource("gpu", hands=lock.Hands(clock=lambda: float(next(ticks)), pause=pause, spawn=spawn))
        a, b = r.enqueue("a", "job a"), r.enqueue("b", "job b")
        slots: dict[str, str | None] = {}

        def take(who: str, ticket: dict) -> Callable[[], None]:
            def turn() -> None:
                slots[who] = r.take(ticket)
                events.append(f"{who} takes" if slots[who] else f"{who} waits")

            return turn

        def release(who: str) -> Callable[[], None]:
            def turn() -> None:
                assert (slot := slots[who]) is not None
                r.release(slot)
                events.append(f"{who} releases")

            return turn

        turns += [take("b", b), take("a", a), take("b", b), release("a"), take("b", b), release("b")]
        assert r.run("c", "job c", ["true"]) == 0
        assert events == ["b waits", "a takes", "b waits", "a releases", "b takes", "b releases", "c runs"]
        assert (env_of_c["FOREMAN_SLOT"], env_of_c["FOREMAN_RESOURCE"]) == ("1", "gpu")  # what the command is told
        use = lock.uses(["gpu"])[0]
        assert use["who"] == "c"
        assert use["waited_s"] > 0

    def test_slots_run_side_by_side_with_their_own_slot(self):
        ticks = itertools.count(1)
        r = lock.Resource("sim", ["A", "B"], hands=lock.Hands(clock=lambda: float(next(ticks))))
        a, b, c = (r.enqueue(w, f"job {w}") for w in "abc")
        assert r.take(c) is None  # not its turn
        assert (r.take(a), r.take(b)) == ("A", "B")  # they overlap, each in its own slot
        assert r.take(c) is None  # no slot free
        r.release("A")
        assert r.take(c) == "A"

    def test_the_command_fails_and_the_resource_is_released_with_its_code(self):
        assert self.fm_lock("gpu", "a", "sh", "-c", "exit 3").wait(timeout=20) == 3
        from foreman.core.project.config import Project

        assert lock.declared(Project.load(self.e.home).cfg)["gpu"].status()["held"] == {}

    def test_the_time_limit_stops_the_command(self):
        """A limit of 0 minutes (max_minutes of foreman.toml) is over at once: no real wait, the real sleep gets the real SIGTERM."""
        with open(self.e.home / "foreman.toml", "a") as f:
            f.write("\n[resources.stop]\nmax_minutes = 0\n")
        pr = self.fm_lock("stop", "a", "sleep", "30")
        assert pr.wait(timeout=20) == 124
        assert pr.stderr is not None
        assert "split long work" in pr.stderr.read()
        assert lock.uses(["stop"])[-1]["event"] == "timeout"

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
        tid = registry.backlog(p).add("x", status="ready", criteria="y")
        text = brief.brief(p, backlog.get(registry.backlog(p).load(), tid), "responsible")
        assert "`gpu`, `sim` (2 slots)" in text
        assert "foreman lock NAME run" in text
