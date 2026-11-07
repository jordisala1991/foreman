"""metrics: the series of the new panel's charts. Today only `cost`: list-price dollars per local day and model
family, from costs.jsonl, counting only the lines of issues in the product's own backlog."""

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
            out["cost"]["2026-11-05"], {"opus": 12.5, "sonnet": 1.25, "haiku": 0, "other": 0, "usd": 13.75}
        )
        self.assertEqual(out["cost"]["2026-11-06"], {"opus": 0, "sonnet": 0, "haiku": 0, "other": 0, "usd": 0})
        self.assertEqual(out["cost"]["2026-11-07"], {"opus": 0, "sonnet": 3.0, "haiku": 0.5, "other": 0, "usd": 3.5})
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


if __name__ == "__main__":
    unittest.main()
