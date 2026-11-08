import os
import subprocess
import sys
import time
import unittest
from pathlib import Path

from helpers import Env

from fm import lock, util

FM = str(Path(__file__).resolve().parent.parent / "bin/fm")


class Lock(unittest.TestCase):
    def setUp(self):
        self.e = Env()
        os.environ["FM_LOCKS"] = str(self.e.tmp / "locks")
        with open(self.e.home / "fm.toml", "a") as f:
            f.write('\n[resources.gpu]\nmax_minutes = 0.05\n[resources.sim]\nslots = ["A", "B"]\n')
        self.log = self.e.tmp / "order.txt"

    def tearDown(self):
        os.environ.pop("FM_LOCKS")

    def fm_lock(self, name: str, who: str, *cmd: str) -> subprocess.Popen:
        env = {**os.environ, "FM_AGENT": who, "FM_ISSUE": who.upper()}
        return subprocess.Popen(
            [sys.executable, FM, "--home", str(self.e.home), "lock", name, "run", f"job {who}", *cmd], env=env, stderr=subprocess.PIPE, text=True
        )

    def test_one_at_a_time_in_arrival_order(self):
        body = f"echo start $FM_AGENT >> {self.log}; sleep 0.4; echo end $FM_AGENT >> {self.log}"
        procs = []
        for who in ("a", "b", "c"):
            procs.append(self.fm_lock("gpu", who, "sh", "-c", body.replace("$FM_AGENT", who)))
            time.sleep(0.15)
        for pr in procs:
            self.assertEqual(pr.wait(timeout=20), 0)
        self.assertEqual(self.log.read_text().split("\n")[:-1], ["start a", "end a", "start b", "end b", "start c", "end c"])
        uses = lock.uses(["gpu"])
        self.assertEqual([u["who"] for u in uses], ["a", "b", "c"])
        self.assertGreater(uses[2]["waited_s"], 0.3)

    def test_slots_run_side_by_side_with_their_own_slot(self):
        body = f"echo start $FM_SLOT >> {self.log}; sleep 1.5; echo end $FM_SLOT >> {self.log}"
        procs = [self.fm_lock("sim", w, "sh", "-c", body) for w in ("a", "b")]
        for pr in procs:
            pr.wait(timeout=20)
        lines = self.log.read_text().split("\n")[:-1]
        self.assertEqual([ln.split()[0] for ln in lines], ["start", "start", "end", "end"])  # they overlapped
        self.assertEqual(sorted(ln.split()[1] for ln in lines[:2]), ["A", "B"])

    def test_the_command_fails_and_the_resource_is_released_with_its_code(self):
        self.assertEqual(self.fm_lock("gpu", "a", "sh", "-c", "exit 3").wait(timeout=20), 3)
        from fm.config import Project

        self.assertEqual(lock.declared(Project.load(self.e.home).cfg)["gpu"].status()["held"], {})

    def test_the_time_limit_stops_the_command(self):
        pr = self.fm_lock("gpu", "a", "sleep", "30")
        self.assertEqual(pr.wait(timeout=60), 124)
        self.assertIn("split long work", pr.stderr.read())
        self.assertEqual(lock.uses(["gpu"])[-1]["event"], "timeout")

    def test_a_holder_that_died_is_cleaned_up(self):
        from fm.config import Project

        dead = subprocess.Popen(["true"])
        dead.wait()
        r = lock.declared(Project.load(self.e.home).cfg)["gpu"]
        util.write_json(r.dir / "state.json", {"held": {"1": {"who": "ghost", "what": "x", "pid": dead.pid, "since": "", "t": 0}}, "queue": []})
        self.assertEqual(self.fm_lock("gpu", "a", "true").wait(timeout=20), 0)

    def test_unknown_resource_and_status(self):
        r = subprocess.run([sys.executable, FM, "--home", str(self.e.home), "lock", "nope", "status"], capture_output=True, text=True)
        self.assertIn("unknown resource", r.stderr)
        r = subprocess.run([sys.executable, FM, "--home", str(self.e.home), "lock", "gpu", "status"], capture_output=True, text=True)
        self.assertEqual(r.stdout.strip(), "free")

    def test_the_brief_names_the_resources(self):
        from fm import agents, backlog
        from fm.config import Project

        p = Project.load(self.e.home)
        iid = backlog.add(p, "x", status="ready", criterion="y")
        text = agents.brief(p, backlog.get(backlog.load(p), iid), "work")
        self.assertIn("`gpu`, `sim` (2 slots)", text)
        self.assertIn("fm lock NAME run", text)


if __name__ == "__main__":
    unittest.main()
