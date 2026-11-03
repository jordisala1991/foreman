import json
import subprocess
import sys
import time
import unittest
from pathlib import Path

from helpers import ENTRY, ProjectCase

from foreman import lock

TOML = """
[project]
name = "fake"

[resources.widget]
guard = ["make up"]

[resources.sim]
slots = ["s1", "s2"]

[resources.busyres]
busy = "test -e {tmp}/busy && echo 'something outside the lock'"
busy_wait = 5
"""


class LockTest(ProjectCase):
    toml = TOML

    def start(self, who, *args, env=None):
        e = self.env
        e.update({"FOREMAN_LOCK_WHO": who, **(env or {})})
        return subprocess.Popen(
            [sys.executable, str(ENTRY), "lock", *args],
            cwd=self.home,
            env=e,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

    def lock_cli(self, who, *args):
        return self.cli("lock", *args, env={"FOREMAN_LOCK_WHO": who})

    # The queue tests wait for conditions, not for fixed times: each job is in the queue before the next one starts
    # (arrival order does not depend on how fast the machine starts a process), and the first holder keeps the lock
    # until the test opens its gate (not for a fixed time that a loaded machine could outlast).
    def until(self, cond, what, timeout=20):
        end = time.monotonic() + timeout
        while not cond():
            if time.monotonic() > end:
                self.fail(f"timed out waiting for {what}")
            time.sleep(0.02)

    def held_by(self, who, name="widget", res=None):
        self.until(lambda: who in [h["who"] for h in lock.status(name, res)["holders"]], f"{who} to hold {name}")

    def queued(self, n, name="widget"):
        self.until(lambda: len(lock.status(name)["queue"]) >= n, f"{n} in the queue of {name}")

    def gated(self, then):
        """A shell command that runs `then` and waits for the gate file (open it with self.gate.touch()); 30 s at
        most, so a failed test leaves no process behind."""
        self.gate = self.tmp / "gate"
        return f"{then}; n=0; while [ ! -e {self.gate} ] && [ $n -lt 600 ]; do sleep 0.05; n=$((n+1)); done"

    def test_acquire_release_check(self):
        self.assertEqual(self.lock_cli("a", "widget", "check").returncode, 1)
        r = self.lock_cli("a", "widget", "acquire", "testing", "10")
        self.assertIn("LOCKED", r.stdout)
        r = self.lock_cli("b", "widget", "check")
        self.assertEqual(r.returncode, 0)
        self.assertIn("a: testing", r.stdout)
        r = self.lock_cli("b", "widget", "release")
        self.assertEqual(r.returncode, 1)
        self.assertIn("NOT yours", r.stdout)
        r = self.lock_cli("b", "widget", "acquire", "too", "1")
        self.assertEqual(r.returncode, 1)
        self.assertIn("TIMEOUT", r.stdout)
        self.assertIn("RELEASED", self.lock_cli("a", "widget", "release").stdout)
        st = lock.status("widget")
        self.assertEqual(st["holders"], [])
        log = [json.loads(x) for x in lock.paths("widget")["log"].read_text().splitlines()]
        self.assertEqual([x["event"] for x in log], ["timeout", "use"])
        self.assertEqual(log[1]["who"], "a")

    def test_run_releases_and_returns_rc(self):
        r = self.lock_cli("a", "widget", "run", "fails", "bash", "-c", "exit 7")
        self.assertEqual(r.returncode, 7)
        self.assertEqual(lock.status("widget")["holders"], [])
        log = [json.loads(x) for x in lock.paths("widget")["log"].read_text().splitlines()]
        self.assertEqual(log[-1]["rc"], 7)

    def test_queue_in_arrival_order(self):
        order = self.tmp / "order"
        first = self.start("a", "widget", "run", "first", "bash", "-c", self.gated(f"echo a >> {order}"))
        self.held_by("a")
        procs = []
        for n, who in enumerate(("b", "c", "d"), 1):
            procs.append(self.start(who, "widget", "run", who, "bash", "-c", f"echo {who} >> {order}; sleep 0.1"))
            self.queued(n)
        st = lock.status("widget")
        self.assertEqual([q["who"] for q in st["queue"]], ["b", "c", "d"])
        self.assertEqual(st["holders"][0]["who"], "a")
        self.gate.touch()
        for p in [first] + procs:
            p.wait(30)
        self.assertEqual(order.read_text().split(), ["a", "b", "c", "d"])

    def test_short_job_jumps_once(self):
        order = self.tmp / "order"
        first = self.start("a", "widget", "run", "long", "bash", "-c", self.gated(f"echo a >> {order}"))
        self.held_by("a")
        procs = [self.start("b", "widget", "run", "normal", "bash", "-c", f"echo b >> {order}")]
        self.queued(1)
        procs.append(
            self.start(
                "s1", "widget", "run", "short", "bash", "-c", f"echo s1 >> {order}", env={"FOREMAN_LOCK_SHORT": "1"}
            )
        )
        self.queued(2)
        procs.append(
            self.start(
                "s2", "widget", "run", "short", "bash", "-c", f"echo s2 >> {order}", env={"FOREMAN_LOCK_SHORT": "1"}
            )
        )
        self.queued(3)
        self.gate.touch()
        for p in [first] + procs:
            p.wait(30)
        # s1 jumps ahead of b; s2 then waits for a normal job (b) before it
        self.assertEqual(order.read_text().split(), ["a", "s1", "b", "s2"])

    def test_explicit_turn(self):
        order = self.tmp / "order"
        first = self.start("a", "widget", "run", "long", "bash", "-c", self.gated(f"echo a >> {order}"))
        self.held_by("a")
        b = self.start("b", "widget", "run", "b", "bash", "-c", f"echo b >> {order}")
        self.queued(1)
        c = self.start("c", "widget", "run", "c", "bash", "-c", f"echo c >> {order}")
        self.queued(2)
        lock.paths("widget")["next"].write_text("c\n")
        self.gate.touch()
        for p in (first, b, c):
            p.wait(30)
        self.assertEqual(order.read_text().split(), ["a", "c", "b"])
        self.assertFalse(lock.paths("widget")["next"].exists())

    def test_nested_run_is_reentrant(self):
        inner = f"{sys.executable} {ENTRY} lock widget run inner echo nested-ok"
        r = self.lock_cli("a", "widget", "run", "outer", "bash", "-c", inner)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("nested-ok", r.stdout)

    def test_slots_give_each_holder_its_own(self):
        out1, out2 = self.tmp / "1", self.tmp / "2"
        sim = self.project.resources["sim"]
        p1 = self.start("a", "sim", "run", "one", "bash", "-c", self.gated(f"echo $FOREMAN_SLOT > {out1}"))
        self.held_by("a", "sim", sim)
        p2 = self.start("b", "sim", "run", "two", "bash", "-c", f"echo $FOREMAN_SLOT > {out2}")
        # the second slot does not wait for the first: b ends while a still holds its slot behind the closed gate
        self.assertEqual(p2.wait(20), 0, "the second slot should not wait for the first")
        self.assertIsNone(p1.poll())
        self.gate.touch()
        p1.wait(30)
        self.assertEqual({out1.read_text().strip(), out2.read_text().strip()}, {"s1", "s2"})
        self.assertEqual(lock.status("sim", self.project.resources["sim"])["holders"], [])

    def test_busy_detector_waits(self):
        flag = self.tmp / "busy"
        flag.write_text("x")
        out = self.tmp / "ran"
        p = self.start("a", "busyres", "run", "after busy", "bash", "-c", f"date +%s.%N > {out}")
        time.sleep(1.0)
        self.assertFalse(out.exists(), "ran while the resource was busy")
        flag.unlink()
        p.wait(30)
        self.assertTrue(out.exists())

    def test_lock_files_are_written_whole(self):
        """Every file a reader looks at (ticket, slot, owner, meta) arrives by rename from a dot-file in the same
        folder, so lock.status never reads one empty or half-written; no temporary is left behind. The ticket's name
        sorts by arrival."""
        bin_dir = self.tmp / "bin"
        bin_dir.mkdir()
        log = self.tmp / "mv.log"
        (bin_dir / "mv").write_text(f'#!/bin/sh\nprintf "%s\\n" "$*" >> {log}\nexec /bin/mv "$@"\n')
        (bin_dir / "mv").chmod(0o755)
        path = f"{bin_dir}:{self.env['PATH']}"
        sim = self.project.resources["sim"]
        r = self.cli("lock", "sim", "acquire", "testing", "10", env={"FOREMAN_LOCK_WHO": "a", "PATH": path})
        self.assertIn("LOCKED", r.stdout)
        held = lock.paths("sim", sim)["dir"].parent / "held-s1"
        queue = lock.paths("sim", sim)["queue"]
        renamed = [line.split()[-1] for line in log.read_text().splitlines() if line.startswith("-f ")]
        self.assertEqual([Path(f).name for f in renamed if Path(f).parent == held], ["slot", "owner", "meta"])
        tickets = [Path(f) for f in renamed if Path(f).parent != held]
        self.assertEqual([f.parent for f in tickets], [queue])
        # named by arrival to the microsecond: two waiters of the same second are not ordered by pid (which macOS
        # does not hand out in order)
        self.assertRegex(tickets[0].name, r"^\d{10}\.\d{6}-\d{7}$")
        self.assertEqual(sorted(x.name for x in held.iterdir()), ["meta", "owner", "slot"])
        self.assertEqual(list(queue.iterdir()), [])
        self.assertEqual(lock.status("sim", sim)["holders"][0]["who"], "a")
        self.assertIn("RELEASED", self.cli("lock", "sim", "release", env={"FOREMAN_LOCK_WHO": "a"}).stdout)

    def test_unknown_resource(self):
        r = self.lock_cli("a", "nope", "check")
        self.assertEqual(r.returncode, 2)
        self.assertIn("unknown resource", r.stderr)


if __name__ == "__main__":
    unittest.main()
