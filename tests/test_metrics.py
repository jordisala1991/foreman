"""metrics: the series of the new panel's charts (cost, deliveries, reviews, guard, today, since), each by local day,
all days present, only the product's own issues in the costs."""

import json
import unittest
from datetime import date

from helpers import ProjectCase

from foreman import metrics
from foreman.backlog import Backlog


def line(issue, end, usd_by_model, agent=None):
    models = {m: {"tokens": int(u * 1000), "usd": u} for m, u in usd_by_model.items()}
    return {
        "agent": agent or f"a-{issue}-{end}-{len(usd_by_model)}",
        "issue": issue,
        "end": end,
        "tokens": sum(m["tokens"] for m in models.values()),
        "usd": round(sum(usd_by_model.values()), 4),
        "models": models,
    }


class CostTest(ProjectCase):
    def setUp(self):
        super().setUp()
        b = Backlog(self.project)
        b.init()
        with b.mutate() as data:
            b.add_project(data, "A1", "First project", "")
            b.add_issue(data, "A1", "PF-1", "One", status="backlog", criterion="it works")
            b.add_issue(data, "A1", "PF-2", "Two", status="backlog", criterion="it works")
        self.costs = b.data_path.parent / "costs.jsonl"

    def write(self, *lines):
        self.costs.write_text("".join(json.dumps(x) + "\n" for x in lines))

    def test_per_day_and_model_with_foreign_lines_out_and_empty_days_zero(self):
        self.write(
            line("PF-1", "2026-11-05 10:00", {"claude-opus-4-7": 10.0, "claude-sonnet-4-6": 1.25}),
            line("PF-2", "2026-11-05 23:59", {"claude-opus-4-7": 2.5}),
            line("PF-1", "2026-11-07 09:00", {"claude-sonnet-4-6": 3.0, "claude-haiku-4-5": 0.5}),
            line("BA-2", "2026-11-07 09:30", {"claude-opus-4-7": 100.0}),  # another product's issue
            line(None, "2026-11-07 09:40", {"claude-opus-4-7": 50.0}),  # no issue: not this product's
            line("PF-2", "2026-10-20 08:00", {"claude-opus-4-7": 7.0}),  # before the period
        )
        out = metrics.cost(self.project, 7, today=date(2026, 11, 7))
        self.assertEqual(out["days"], [f"2026-11-0{d}" for d in range(1, 8)])
        self.assertEqual(
            out["cost"]["2026-11-05"],
            {
                "opus": 12.5,
                "sonnet": 1.25,
                "haiku": 0,
                "other": 0,
                "usd": 13.75,
                "tokens": 13750,
                "cheap_share": 0.0909,
            },
        )
        self.assertEqual(
            out["cost"]["2026-11-06"],
            {"opus": 0, "sonnet": 0, "haiku": 0, "other": 0, "usd": 0, "tokens": 0, "cheap_share": None},
        )
        self.assertEqual(
            out["cost"]["2026-11-07"],
            {"opus": 0, "sonnet": 3.0, "haiku": 0.5, "other": 0, "usd": 3.5, "tokens": 3500, "cheap_share": 1.0},
        )
        self.assertEqual(out["total"], 17.25)
        self.assertEqual(out["since"], "2026-10-20")  # the first day with a line of its own, before the period too

    def test_archived_issues_count_and_unknown_models_go_to_other(self):
        b = Backlog(self.project)
        with b.mutate() as data:
            data["archive"].append(
                {"project": "A1", "issues": [{**data["projects"][0]["issues"][0], "id": "PF-9", "status": "done"}]}
            )
        self.write(line("PF-9", "2026-11-07 09:00", {"some-other-model": 4.0}))
        out = metrics.cost(self.project, 7, today=date(2026, 11, 7))
        self.assertEqual(len(out["days"]), 7)
        self.assertEqual(out["cost"]["2026-11-07"]["other"], 4.0)
        self.assertEqual(out["total"], 4.0)

    def test_no_file_is_all_zero_and_no_since(self):
        out = metrics.cost(self.project, 7, today=date(2026, 11, 7))
        self.assertEqual(out["total"], 0)
        self.assertIsNone(out["since"])
        self.assertEqual(sum(d["usd"] for d in out["cost"].values()), 0)

    def test_days_must_be_7_or_30(self):
        with self.assertRaises(ValueError):
            metrics.cost(self.project, 5)

    def test_a_run_across_midnight_counts_whole_on_its_end_day(self):
        r = line("PF-1", "2026-11-06 00:10", {"claude-opus-4-7": 8.0})
        r["start"] = "2026-11-05 22:00"
        self.write(r)
        out = metrics.cost(self.project, 7, today=date(2026, 11, 7))
        self.assertEqual((out["cost"]["2026-11-05"]["usd"], out["cost"]["2026-11-06"]["usd"]), (0, 8.0))

    def test_top_issues_and_types_and_a_bad_models_value(self):
        a = line("PF-1", "2026-11-07 09:00", {"claude-opus-4-7": 5.0})
        a["type"] = "dev"
        b = line("PF-2", "2026-11-07 10:00", {"claude-opus-4-7": 2.0}, agent="x")
        b["type"] = "reviewer"
        c = line("PF-1", "2026-11-06 10:00", {"claude-sonnet-4-6": 1.0}, agent="y")
        c["models"]["junk"] = "not an object"
        self.write(a, b, c)
        out = metrics.cost(self.project, 7, today=date(2026, 11, 7))
        self.assertEqual(
            out["top_issues"],
            [
                {"issue": "PF-1", "short": "One", "usd": 6.0, "runs": 2},
                {"issue": "PF-2", "short": "Two", "usd": 2.0, "runs": 1},
            ],
        )
        self.assertEqual(
            [(t["type"], t["usd"]) for t in out["by_type"]], [("dev", 5.0), ("reviewer", 2.0), ("other", 1.0)]
        )


class BuildTest(CostTest):
    TODAY = date(2026, 11, 7)

    def note(self, day, *headings):
        d = self.project.notes_dir
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{day}.md").write_text("".join(f"## {h}\n\ntext\n\n" for h in headings))

    def progress(self, *lines):
        f = self.project.state / "progress.jsonl"
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("".join(json.dumps(x) + "\n" for x in lines))

    def guard_log(self, *lines):
        f = self.project.state / "guard.jsonl"
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("".join(json.dumps(x) + "\n" for x in lines))

    def review(self, name, rnd, open_):
        f = self.project.reviews_dir / name
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(f"# Review\n\nBranch: foreman:x/y\nTip: abcdef1\nRound: {rnd}\nOpen: {open_}\n")

    def test_deliveries_from_notes_and_progress(self):
        self.note(
            "2026-11-07",
            "10:00 Integrated repo:a/b (PF-1 T1)",
            "11:00 Integrated repo:a/c (PF-1 T2)",
            "12:00 Integrated repo:a/d (PF-2)",
            "13:00 Measured repo:a/e (PF-2 T0)",
            "14:00 Some other note",
        )
        self.note("2026-11-01", "10:00 Integrated repo:a/f (PF-1 T3)")  # before the 7-day period
        self.progress(
            {"time": "2026-11-07 10:00", "agent": "a", "text": "x", "done": True, "outcome": "ok"},
            {"time": "2026-11-07 11:00", "agent": "a", "text": "x", "done": True, "outcome": "ok"},
            {"time": "2026-11-07 12:00", "agent": "b", "text": "x", "done": True, "outcome": "partial"},
            {"time": "2026-11-07 12:30", "agent": "b", "text": "not a delivery"},
            {"time": "2026-11-06 12:00", "agent": "b", "text": "x", "done": True, "outcome": "failed"},
            {"time": "2026-10-20 12:00", "agent": "b", "text": "x", "done": True, "outcome": "ok"},
        )
        out = metrics.build(self.project, 7, self.TODAY)
        self.assertEqual(
            out["deliveries"]["2026-11-07"], {"integrated": 3, "measured": 1, "ok": 2, "partial": 1, "failed": 0}
        )
        self.assertEqual(
            out["deliveries"]["2026-11-06"], {"integrated": 0, "measured": 0, "ok": 0, "partial": 0, "failed": 1}
        )
        self.assertEqual(
            out["deliveries"]["2026-11-05"], dict.fromkeys(("integrated", "measured", "ok", "partial", "failed"), 0)
        )
        self.assertEqual(out["since"]["deliveries"], "2026-10-20")

    def test_reviews_by_day_and_round(self):
        self.review("2026-11-06-review-A-T1.md", 1, "H 0, M 2")
        self.review("2026-11-06-review-A-T1-r2.md", 2, "H 0, M 0")
        self.review("2026-11-07-review-B.md", 1, "H 1, M 0")
        self.review("2026-10-01-review-old.md", 1, "H 0, M 0")
        (self.project.reviews_dir / "2026-11-07-review-bad.md").write_text("no header at all\n")
        out = metrics.build(self.project, 7, self.TODAY)
        self.assertEqual(out["reviews"]["2026-11-06"], {"clean": 1, "back": 1})
        self.assertEqual(out["reviews"]["2026-11-07"], {"clean": 0, "back": 1})
        self.assertEqual(out["rounds"], {"1": 2, "2": 1})
        self.assertEqual(out["since"]["reviews"], "2026-10-01")
        self.assertEqual(out["today"]["reviews"], {"back": 1, "total": 1, "yesterday_back": 1, "yesterday_total": 2})

    def test_guard_by_who_and_top_rules(self):
        def g(at, who, rule, kind="builtin"):
            return {"at": at, "kind": kind, "rule": rule, "who": who}

        self.guard_log(
            g("2026-11-07T10:00:00", "agent", "r1"),
            g("2026-11-07T10:01:00", "agent", "r1"),
            g("2026-11-07T10:02:00", "orchestrator", "r2"),
            g("2026-11-07T10:03:00", "someone", "r3"),
            g("2026-11-06T10:03:00", "agent", "r4"),
            g("2026-10-01T10:00:00", "agent", "r1"),
        )
        out = metrics.build(self.project, 7, self.TODAY)
        self.assertEqual(out["guard"]["2026-11-07"], {"agent": 2, "orchestrator": 1, "other": 1})
        self.assertEqual(out["guard"]["2026-11-06"], {"agent": 1, "orchestrator": 0, "other": 0})
        self.assertEqual(out["guard"]["2026-11-05"], {"agent": 0, "orchestrator": 0, "other": 0})
        self.assertEqual(out["guard_rules"][0], {"rule": "r1", "band": "builtin", "n": 2})  # the Oct line is outside
        self.assertEqual(len(out["guard_rules"]), 3)
        self.assertEqual(out["since"]["guard"], "2026-10-01")

    def test_today_figures_and_empty_product(self):
        out = metrics.build(self.project, 30, self.TODAY)
        self.assertEqual(len(out["days"]), 30)
        self.assertEqual(out["since"], {"cost": None, "deliveries": None, "reviews": None, "guard": None})
        self.assertEqual(out["today"]["cost"]["usd"], 0)
        self.assertEqual(out["today"]["cost"]["spark"], [0.0] * 7)
        self.write(
            line("PF-1", "2026-11-01 10:00", {"claude-opus-4-7": 60.0}),
            line("PF-1", "2026-11-06 10:00", {"claude-opus-4-7": 12.0}),
            line("PF-1", "2026-11-07 10:00", {"claude-opus-4-7": 6.0, "claude-sonnet-4-6": 3.0}),
        )
        t = metrics.build(self.project, 7, self.TODAY)["today"]
        self.assertEqual(t["cost"]["usd"], 9.0)
        self.assertEqual(t["cost"]["avg_before"], 12.0)  # (60 + 0 + 0 + 0 + 0 + 12) / 6, empty days included
        self.assertEqual(t["cost"]["spark"], [60.0, 0, 0, 0, 0, 12.0, 9.0])
        self.assertEqual((t["cost"]["opus"], t["cost"]["sonnet"]), (6.0, 3.0))

    def test_a_cost_of_30_days_matches_a_hand_count(self):
        self.write(*[line("PF-1", f"2026-10-{d:02d} 10:00", {"claude-opus-4-7": float(d)}) for d in range(9, 32)])
        out = metrics.build(self.project, 30, self.TODAY)
        self.assertEqual(out["total"], float(sum(range(9, 32))))

    def test_performance_with_five_times_the_real_data(self):
        """Real foreman data (2026-11-07): 387 cost lines, 482 progress lines, 49 refusals, 8 note files, 40 reviews;
        the plan's limit is 200 ms cold; here 5 times the data and 500 ms (a slow CI), and a warm read of the panel's
        cache (memo) must be much faster."""
        import time

        from foreman.panel_cache import memo

        self.write(
            *[
                line("PF-1", f"2026-10-{1 + n % 31:02d} 10:00", {"claude-opus-4-7": 1.5}, agent=f"a{n}")
                for n in range(2000)
            ]
        )
        self.progress(
            *[
                {"time": f"2026-11-0{1 + n % 7} 10:00", "agent": "a", "text": "x", "done": n % 3 == 0, "outcome": "ok"}
                for n in range(2400)
            ]
        )
        self.guard_log(
            *[
                {"at": f"2026-11-0{1 + n % 7}T10:00:00", "kind": "builtin", "rule": f"r{n % 9}", "who": "agent"}
                for n in range(250)
            ]
        )
        for d in range(1, 8):
            self.note(f"2026-11-0{d}", *[f"{h:02d}:00 Integrated repo:a/b{h} (PF-1)" for h in range(10, 20)] * 5)
        for n in range(200):
            self.review(f"2026-11-0{1 + n % 7}-review-X{n}.md", 1 + n % 2, "H 0, M 1")
        t = time.perf_counter()
        out = memo(
            ("perf", 30),
            lambda: metrics.build(self.project, 30, self.TODAY),
            deps=metrics.deps(self.project, 30, self.TODAY),
        )
        cold = time.perf_counter() - t
        t = time.perf_counter()
        again = memo(
            ("perf", 30),
            lambda: metrics.build(self.project, 30, self.TODAY),
            deps=metrics.deps(self.project, 30, self.TODAY),
        )
        warm = time.perf_counter() - t
        self.assertLess(cold, 0.5)
        self.assertLess(warm, cold / 2)
        self.assertIs(out, again)

    def test_deps_name_every_file_read(self):
        names = {p.name for p in metrics.deps(self.project, 7, self.TODAY)}
        self.assertTrue({"costs.jsonl", "progress.jsonl", "guard.jsonl", "2026-11-07.md", "2026-11-01.md"} <= names)


if __name__ == "__main__":
    unittest.main()
