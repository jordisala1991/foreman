import functools
import itertools
import os
import signal
import subprocess
import sys
from collections.abc import Callable
from unittest import mock

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
        """a and b are in the queue when c comes; c runs after both, each given the slot only after the one before released it."""
        events: list[str] = []
        ticks = itertools.count(1)
        turns: list[Callable[[], None]] = []

        def spawn(cmd, env, start_new_session):
            events.append("c runs")
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
        """The first wait for the command raises TimeoutExpired at once (no real time passes); the real process then gets the real SIGTERM."""
        real: list[subprocess.Popen[bytes]] = []

        class Waiter:
            def __init__(self, p: subprocess.Popen[bytes]) -> None:
                self.p, self.pid, self.timed_out = p, p.pid, False

            def wait(self, timeout: float | None = None) -> int:
                if not self.timed_out:
                    self.timed_out = True
                    raise subprocess.TimeoutExpired("sleep", timeout or 0)
                return self.p.wait(timeout)

        def spawn(cmd, env, start_new_session):
            real.append(subprocess.Popen(cmd, env=env, start_new_session=start_new_session))
            return Waiter(real[0])

        said: list[str] = []
        assert lock.Resource("gpu", max_minutes=0.05, hands=lock.Hands(spawn=spawn)).run("a", "job a", ["sleep", "30"], say=said.append) == 124
        assert any("split long work" in line for line in said)
        assert real[0].returncode == -signal.SIGTERM
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
