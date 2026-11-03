"""`foreman catchup` (lib/foreman/catchup.py): the four parts on a fake product whose state files are written by hand,
times relative to now: what goes in each part, what is filtered out (a planner stopped without delivering, a CI that
failed on a branch integrated afterwards), the text output, the window and its mark, and that nothing else in the
state folder is written."""

import json
from datetime import datetime, timedelta

from helpers import BASE_TOML, ProjectCase

from foreman import backlog, catchup, util

TOML = BASE_TOML.replace('criterion_required = ["PF-"]', "criterion_required = []") + (
    '\n[deny.grants]\ndeploy = ["make deploy"]\n'
)
S = "11111111-aaaa-4aaa-8aaa-000000000001"


def hm(t):
    return t.strftime("%H:%M")


def iso(t):
    return t.isoformat(timespec="seconds")


class CatchupCase(ProjectCase):
    toml = TOML

    def setUp(self):
        super().setUp()
        # minute-aligned (notes keep HH:MM) and a little in the past, so «--since 2h» taken a moment later holds it all
        self.now = datetime.now().replace(second=0, microsecond=0)
        self.st = self.project.state
        self.st.mkdir(parents=True, exist_ok=True)

    def ago(self, minutes):
        return self.now - timedelta(minutes=minutes)

    def note(self, minutes, title):
        t = self.ago(minutes)
        nd = self.project.notes_dir
        nd.mkdir(parents=True, exist_ok=True)
        f = nd / f"{t:%Y-%m-%d}.md"
        f.write_text((f.read_text() if f.exists() else "# Notes\n") + f"\n## {hm(t)} {title}\nbody\n")

    def catchup(self, *args, rc=0):
        r = self.cli("catchup", *args)
        self.assertEqual(r.returncode, rc, r.stderr)
        return r.stdout

    def js(self, *args):
        return json.loads(self.catchup("--json", *args))

    def snapshot(self):
        return {str(p.relative_to(self.st)): p.stat().st_mtime_ns for p in sorted(self.st.rglob("*")) if p.is_file()}

    def world(self):
        """The world of the plan's criterion (§2): two integrations of an issue closed with its result, a measurement,
        a live production agent with a progress line, a live planner, a pending decision holding a sub-issue, a
        pending permission request, a partial delivery, a failed CI on main, a failed CI on a worktree whose branch
        is integrated afterwards, a planner stopped without `progress --done` and a 3 h gap between loop passes."""
        b = backlog.Backlog(self.project)
        b.init()
        with b.mutate() as data:
            b.add_project(data, "A1", "First", "intro")
            b.add_issue(data, "A1", "PF-1", "The first thing: done in two parts", status="in progress")
            b.set_sub(data, "PF-1", "T0", title="part zero", status="done")
            b.set_sub(data, "PF-1", "T1", title="part one", status="done")
            b.set_status(data, "PF-1", "done", result="The first thing works, 3 of 3 cases")
            b.add_issue(data, "A1", "PF-2", "Second thing", status="in progress")
            b.set_sub(data, "PF-2", "T0", title="build it", status="in progress", agent="dev2", branch="pf2/t0")
            b.add_issue(data, "A1", "PF-3", "Third thing", status="in progress")
            b.set_sub(data, "PF-3", "T2", title="measure the speed", status="done")
            b.add_issue(data, "A1", "PF-4", "Fourth thing", status="ready")
            b.set_sub(data, "PF-4", "T1", title="held part")
            b.add_issue(data, "A1", "PF-5", "Fifth thing", status="ready")
            b.add_issue(data, "A1", "PF-6", "Sixth thing", status="refining")
            b.add_issue(data, "A1", "PF-7", "Seventh thing", status="in progress")
            b.add_decision(
                data, None, "Which color?", "Blue or green.", ["blue", "green"], "blue", issue="PF-4", blocks=["T1"]
            )
        self.note(100, "Integrated fake:pf1/t0 (PF-1 T0)")
        self.note(80, "Integrated fake:pf1/t1 (PF-1 T1)")
        self.note(70, "Measured fake:pf3/t2 (PF-3 T2)")
        self.note(30, "Integrated fake:pf5/t0 (PF-5 T0)")
        (self.st / "session").write_text(S + "\n")
        mark = {"session": S, "name": "orch", "since": iso(self.ago(600))}
        (self.st / "orchestrator.json").write_text(json.dumps(mark))
        agents = {
            "a-dev2": {
                "name": "dev2",
                "type": "foreman:dev",
                "production": True,
                "issue": "PF-2",
                "sub": "T0",
                "branch": "pf2/t0",
                "start": iso(self.ago(50)),
            },
            "a-plan": {
                "name": "plan6",
                "type": "foreman:planner",
                "production": False,
                "issue": "PF-6",
                "start": iso(self.ago(20)),
            },
            "a-plan-old": {
                "name": "plan0",
                "type": "foreman:planner",
                "production": False,
                "start": iso(self.ago(230)),
                "end": iso(self.ago(210)),
            },
            "a-part": {
                "name": "dev7",
                "type": "foreman:dev",
                "production": True,
                "issue": "PF-7",
                "sub": "T0",
                "branch": "pf7/t0",
                "start": iso(self.ago(90)),
                "end": iso(self.ago(40)),
                "delivered": iso(self.ago(40)),
                "outcome": "partial",
            },
        }
        (self.st / "agents.json").write_text(json.dumps(agents))
        progress = [
            {"time": iso(self.ago(10)), "agent": "dev2", "agent_id": "a-dev2", "text": "halfway there"},
            {
                "time": iso(self.ago(41)),
                "agent": "dev7",
                "agent_id": "a-part",
                "text": "only half of it",
                "done": True,
                "outcome": "partial",
            },
        ]
        (self.st / "progress.jsonl").write_text("".join(json.dumps(x) + "\n" for x in progress))
        (self.st / "grants.json").write_text(
            json.dumps({"pending": {"deploy": {"at": iso(self.ago(5)), "session": S, "command": "make deploy"}}})
        )
        ci = self.st / "integrate"
        ci.mkdir()
        (ci / "pf1-t1-ci.log").write_text(
            f"== {self.ago(60):%Y-%m-%d %H:%M:%S} in {self.home}: make check\nFAIL\n== rc 1 in 5 s\n"
        )
        (ci / "pf5-t0-ci.log").write_text(
            f"== {self.ago(45):%Y-%m-%d %H:%M:%S} in {self.tmp}/wt/pf5-t0: make check\nFAIL\n== rc 1 in 4 s\n"
            f"== {self.ago(35):%Y-%m-%d %H:%M:%S} in {self.tmp}/wt/pf5-t0: make check\nok\n== rc 0 in 4 s\n"
        )
        passes = [240, 60, 30, 5]  # a 3 h gap from 240 to 60 minutes ago
        (self.st / "loop.jsonl").write_text(
            "".join(
                json.dumps({"time": iso(self.ago(m)), "session": S, "skipped": True, "notices": [], "actionable": []})
                + "\n"
                for m in passes
            )
        )


class FourPartsTest(CatchupCase):
    def setUp(self):
        super().setUp()
        self.world()

    def test_the_criterion_world(self):
        d = self.js("--since", "2h")
        self.assertEqual(
            {"since", "until", "since_from", "done", "running", "waiting", "wrong"} - set(d), set(), list(d)
        )
        self.assertEqual(d["since_from"], "arg")
        for key in ("done", "running", "waiting", "wrong"):
            for x in d[key]:
                self.assertEqual({"time", "issue", "sub", "title", "text", "ref"} - set(x), set(), x)
        # done: one entry for PF-1 with both sub-issues and its result; the measurement
        pf1 = [x for x in d["done"] if x["issue"] == "PF-1"]
        self.assertEqual(len(pf1), 1)
        self.assertEqual((pf1[0]["subs"], pf1[0]["closed"]), (["T0", "T1"], True))
        self.assertEqual(pf1[0]["text"], "The first thing works, 3 of 3 cases")
        self.assertEqual(pf1[0]["title"], "The first thing")
        m = [x for x in d["done"] if x["kind"] == "measure"]
        self.assertEqual([(x["issue"], x["sub"]) for x in m], [("PF-3", "T2")])
        self.assertIn("measure the speed", m[0]["text"])
        # running: the production agent with its last progress line first, the planner apart
        roles = [(x["role"], x["agent"], x["issue"]) for x in d["running"]]
        self.assertEqual(roles, [("production", "dev2", "PF-2"), ("planner", "plan6", "PF-6")])
        self.assertEqual(d["running"][0]["text"], "halfway there")
        self.assertEqual(d["running"][0]["sub"], "T0")
        self.assertIn("PF-7", d["in_progress_idle"])
        self.assertNotIn("PF-2", d["in_progress_idle"])
        # waiting: the decision with what it holds, the permission request
        dec = [x for x in d["waiting"] if x["kind"] == "decision"]
        self.assertEqual([(x["id"], x["holds"], x["issue"]) for x in dec], [("PF-4 J1", ["PF-4 T1"], "PF-4")])
        perm = [x for x in d["waiting"] if x["kind"] == "permission"]
        self.assertEqual([(x["group"], x["title"]) for x in perm], [("deploy", "deploy")])
        self.assertIn("make deploy", perm[0]["text"])
        # wrong: the partial delivery, the CI on main, the loop gap; not the stopped planner, not the worktree CI
        kinds = sorted(x["kind"] for x in d["wrong"] if x["kind"] in ("delivery", "ci", "loop_gap", "stopped"))
        self.assertEqual(kinds, ["ci", "delivery", "loop_gap"], d["wrong"])
        ci = next(x for x in d["wrong"] if x["kind"] == "ci")
        self.assertEqual(ci["place"], "main")
        self.assertTrue(catchup.same_branch(ci["branch"], "pf1/t1"), ci)  # no agent keeps it: the log's name
        gap = next(x for x in d["wrong"] if x["kind"] == "loop_gap")
        self.assertEqual(gap["minutes"], 180)
        part = next(x for x in d["wrong"] if x["kind"] == "delivery")
        self.assertEqual((part["issue"], part["outcome"]), ("PF-7", "partial"))
        self.assertNotIn("plan0", json.dumps(d))

    def test_a_branch_awaiting_approval_until_it_is_integrated(self):
        with open(self.st / "progress.jsonl", "a") as f:
            for b in ("pf8/t0", "pf5/t0"):
                f.write(
                    json.dumps(
                        {
                            "time": iso(self.ago(50)),
                            "agent": "orchestrator",
                            "text": f"fake:{b} green, awaiting approval to merge: touches the CI",
                        }
                    )
                    + "\n"
                )
        d = self.js("--since", "2h")
        ap = [x for x in d["waiting"] if x["kind"] == "approval"]
        self.assertEqual([x["branch"] for x in ap], ["pf8/t0"])  # pf5/t0 was integrated 30 minutes ago
        self.assertIn("touches the CI", ap[0]["text"])

    def test_a_production_agent_stopped_without_delivering(self):
        agents = json.loads((self.st / "agents.json").read_text())
        agents["a-gone"] = {
            "name": "dev9",
            "type": "foreman:dev",
            "production": True,
            "issue": "PF-9",
            "sub": "T0",
            "start": iso(self.ago(300)),
            "end": iso(self.ago(200)),  # past the 3 h it counts as waiting: now it is stopped
        }
        agents["a-inc"] = {
            "name": "dev10",
            "type": "foreman:dev",
            "production": True,
            "issue": "PF-10",
            "start": iso(self.ago(60)),
            "end": iso(self.ago(20)),
            "delivery_incomplete": {"at": iso(self.ago(20)), "missing": ["report"]},
        }
        (self.st / "agents.json").write_text(json.dumps(agents))
        d = self.js("--since", "2h")
        got = {x["agent"]: x["kind"] for x in d["wrong"] if x["kind"] in ("stopped", "incomplete")}
        self.assertEqual(got, {"dev9": "stopped", "dev10": "incomplete"})
        self.assertIn("report", next(x["text"] for x in d["wrong"] if x["kind"] == "incomplete"))

    def test_text_in_the_project_language(self):
        out = self.catchup("--since", "2h")
        lines = out.splitlines()
        self.assertLessEqual(len(lines), 40)
        for h in ("## Done", "## Running", "## Waiting for you", "## Went wrong"):
            self.assertIn(h, lines)
        self.assertIn("The first thing (PF-1 T0, T1) · The first thing works", out)
        self.assertIn("being planned", out)
        self.write_toml(TOML.replace('language = "en"', 'language = "es"'))
        out = self.catchup("--since", "2h")
        for h in ("## Terminado", "## En marcha", "## Te espera", "## Fue mal"):
            self.assertIn(h, out.splitlines())
        self.assertIn("se está planificando", out)

    def test_reads_only_but_its_mark(self):
        before = self.snapshot()
        self.catchup("--since", "2h")
        self.catchup("--since", "2h", "--json")
        self.assertEqual(self.snapshot(), before)  # --since: not even the mark
        self.catchup()
        after = self.snapshot()
        self.assertEqual(set(after) - set(before), {"catchup.json"})
        self.assertEqual({k: v for k, v in after.items() if k != "catchup.json"}, before)


class TextTest(CatchupCase):
    def test_an_empty_product_says_nothing_in_each_part(self):
        self.write_toml(TOML.replace('language = "en"', 'language = "es"'))
        lines = self.catchup("--since", "2h").splitlines()
        self.assertEqual(
            lines[1:], ["## Terminado", "nada", "## En marcha", "nada", "## Te espera", "nada", "## Fue mal", "nada"]
        )
        self.assertIn("(pedido)", lines[0])

    def test_a_part_is_cut_at_eight_lines(self):
        b = backlog.Backlog(self.project)
        b.init()
        with b.mutate() as data:
            b.add_project(data, "A1", "First", "intro")
            b.add_issue(data, "A1", "PF-1", "One", status="ready")
            for k in range(12):
                b.add_decision(data, None, f"Question {k}?", "Text.", ["a", "b"], "a", issue="PF-1")
        self.write_toml(TOML.replace('language = "en"', 'language = "es"'))
        lines = self.catchup("--since", "2h").splitlines()
        self.assertLessEqual(len(lines), 40)
        body = lines[lines.index("## Te espera") + 1 : lines.index("## Fue mal")]
        self.assertEqual(len(body), 8)
        self.assertEqual(body[-1], "y 5 más (--json: todos)")
        self.assertEqual(len(self.js("--since", "2h")["waiting"]), 12)  # --json: all

    def test_a_bad_since(self):
        r = self.cli("catchup", "--since", "yesterday-ish")
        self.assertEqual(r.returncode, 2)
        self.assertIn("--since", r.stderr)

    def test_no_project(self):
        r = self.cli("catchup", cwd=self.tmp)
        self.assertEqual(r.returncode, 2)


class WindowTest(CatchupCase):
    def mark(self, t):
        util.write_json(catchup.mark_file(self.project), {"last": iso(t), "session": None})

    def since(self, d):
        return datetime.fromisoformat(d["since"])

    def test_no_mark_counts_24_hours_then_marks(self):
        d = self.js()
        self.assertEqual(d["since_from"], "default")
        self.assertAlmostEqual((datetime.fromisoformat(d["until"]) - self.since(d)).total_seconds(), 86400, delta=1)
        rec = json.loads(catchup.mark_file(self.project).read_text())
        self.assertEqual(rec["last"], d["until"])
        self.assertEqual(self.js()["since_from"], "mark")

    def test_from_the_mark(self):
        self.mark(self.ago(180))
        d = self.js()
        self.assertEqual((d["since_from"], d["since"]), ("mark", iso(self.ago(180))))

    def test_an_old_mark_is_capped_at_7_days(self):
        self.mark(self.ago(10 * 24 * 60))
        d = self.js()
        self.assertEqual(d["since_from"], "capped")
        self.assertAlmostEqual((datetime.fromisoformat(d["until"]) - self.since(d)).days, 7, delta=0)

    def test_since_neither_reads_nor_moves_the_mark(self):
        self.mark(self.ago(180))
        d = self.js("--since", "30m")
        self.assertEqual(d["since_from"], "arg")
        rec = json.loads(catchup.mark_file(self.project).read_text())
        self.assertEqual(rec["last"], iso(self.ago(180)))

    def test_a_mark_in_the_future_or_broken_counts_24_hours(self):
        self.mark(self.now + timedelta(days=1))
        self.assertEqual(self.js()["since_from"], "default")
        catchup.mark_file(self.project).write_text("{not json")
        self.assertEqual(self.js()["since_from"], "default")
