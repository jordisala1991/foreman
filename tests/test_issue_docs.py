"""The documents of an issue: one list for its card in the panel and for `foreman backlog show`."""

import unittest

from helpers import ProjectCase

from foreman import panel_cards
from foreman.backlog import Backlog


def review(branch, rnd, opened):
    return f"# Review\n\nBranch: foreman:{branch}\nTip: abcdef123456\nRound: {rnd}\nOpen: H {opened}, M 0\n\ntext\n"


class IssueDocsTest(ProjectCase):
    def setUp(self):
        super().setUp()
        b = self.b = Backlog(self.project)
        b.init()
        rv = self.home / "reviews"
        rv.mkdir(exist_ok=True)
        (self.home / "reviews/2026-11-01-plan-AB-1.md").write_text("plan\n")
        (self.home / "reviews/2026-11-02-plan-AB-1b.md").write_text("plan 2\n")
        (self.home / "reviews/2026-11-03-ab-1-t1.md").write_text("report\n")
        # an old name (no id in it): found by the branch its header names
        (rv / "2026-11-04-review-old.md").write_text(review("ab1/one", 1, 2))
        (rv / "2026-11-05-review-other.md").write_text(review("ab1/one", 2, 0))
        (rv / "2026-11-05-review-zz.md").write_text(review("zz/else", 1, 9))
        # a new name, no branch recorded in the sub-issue: found by its name
        (rv / "2026-11-07-review-AB-1-T2-r2.md").write_text(review("ab1/two", 2, 1))
        (rv / "2026-11-07-q").mkdir()
        (rv / "2026-11-07-q/page.md").write_text("page\n")
        (rv / "cited.md").write_text("cited\n")
        with b.mutate() as d:
            b.add_project(d, "E1", "First", "What E1 is for.")
            b.add_issue(d, "E1", "AB-1", "one", "ready", "See `reviews/cited.md` and `reviews/gone.md`.", criterion="c")
            b.add_issue(d, "E1", "AB-10", "ten", "ready", "x", criterion="c")
            b.edit(d, "AB-1", plan=["reviews/2026-11-01-plan-AB-1.md", "reviews/2026-11-02-plan-AB-1b.md"])
            b.set_sub(d, "AB-1", "T1", status="done", branch="ab1/one", report="reviews/2026-11-03-ab-1-t1.md")
            b.set_sub(d, "AB-1", "T2", status="done", report="reviews/gone-report.md")
            b.add_decision(
                d,
                "AB-1 J1",
                "Which",
                "t",
                ["a", "b"],
                details=[{"effect": "x"}, {"effect": "y"}],
                page="reviews/2026-11-07-q/page.md",
                blocks=["AB-1 T2"],
            )
            b.add_decision(
                d, "AB-10 J1", "Other", "t", ["a", "b"], details=[{"effect": "x"}, {"effect": "y"}], blocks=["AB-10"]
            )

    def card(self):
        cards = panel_cards.backlog_cards(self.project)
        return next(i for i in cards["projects"][0]["issues"] if i["id"] == "AB-1")

    def test_card_lists_every_document(self):
        docs = self.card()["docs"]
        got = [(d["kind"], d.get("sub"), d["doc"], d.get("round"), d.get("open")) for d in docs]
        self.assertEqual(
            [d["doc"] for d in docs if d["kind"] == "plan"],
            ["reviews/2026-11-01-plan-AB-1.md", "reviews/2026-11-02-plan-AB-1b.md"],
        )
        self.assertIn(("decision", None, "reviews/2026-11-07-q/page.md", None, None), got)
        self.assertIn(("report", "T1", "reviews/2026-11-03-ab-1-t1.md", None, None), got)
        self.assertIn(("review", "T1", "reviews/2026-11-04-review-old.md", 1, 2), got)
        self.assertIn(("review", "T1", "reviews/2026-11-05-review-other.md", 2, 0), got)
        self.assertIn(("review", "T2", "reviews/2026-11-07-review-AB-1-T2-r2.md", 2, 1), got)
        self.assertIn(("cited", None, "reviews/cited.md", None, None), got)
        self.assertNotIn("reviews/2026-11-05-review-zz.md", [g[2] for g in got])
        # what does not exist is left out
        self.assertFalse([g for g in got if "gone" in g[2]])

    def test_show_prints_the_same_list(self):
        out = self.cli("backlog", "show", "AB-1").stdout
        self.assertIn("documents:", out)
        self.assertIn("review T1 r1, 2 open: reviews/2026-11-04-review-old.md", out)
        self.assertIn("review T2 r2, 1 open: reviews/2026-11-07-review-AB-1-T2-r2.md", out)
        self.assertIn("decision AB-1 J1: reviews/2026-11-07-q/page.md", out)
        self.assertIn("plan: reviews/2026-11-01-plan-AB-1.md", out)
        self.assertIn("report T1: reviews/2026-11-03-ab-1-t1.md", out)
        self.assertNotIn("gone", out.split("documents:")[1].split("detail:")[0])
        self.assertNotIn("documents:", self.cli("backlog", "show", "AB-10").stdout)


if __name__ == "__main__":
    unittest.main()


class CardNewFieldsTest(IssueDocsTest):
    """What the new panel's backlog reads from a card: its short name, the short reason of a block, what is
    integrated and the title of each document."""

    def test_short_reason_integrated_and_titles(self):
        (self.home / "reviews/cited.md").write_text("# The cited page\n\ntext\n")
        with self.b.mutate() as d:
            self.b.edit(d, "AB-1", short="One short")
            lst, k, _ = self.b.find(d, "AB-10")
            lst[k]["blocked"] = "espera a tidepool y quayside: que confirmen"
            self.b.set_sub(d, "AB-1", "T1", commit="abcdef1")
            self.b.set_sub(d, "AB-1", "T3", status="in progress")
        c = self.card()
        self.assertEqual(c["short_name"], "One short")
        self.assertEqual(c["integrated"], {"n": 1, "of": 3, "commits": ["abcdef1"]})
        titles = {d["doc"]: d.get("title") for d in c["docs"]}
        self.assertEqual(titles["reviews/cited.md"], "The cited page")
        self.assertIsNone(titles["reviews/2026-11-03-ab-1-t1.md"])  # no heading: no title
        ten = next(i for i in panel_cards.backlog_cards(self.project)["projects"][0]["issues"] if i["id"] == "AB-10")
        self.assertEqual(ten["why"], "espera a tidepool y quayside")
        self.assertNotIn("integrated", ten)
        self.assertNotIn("short_name", ten)

    def test_why_short(self):
        why = panel_cards.why_short
        self.assertEqual(why(""), "")
        self.assertEqual(why("es operar tidepool en su propia sesión: cuándo y cómo lo decide Ana"), "lo decide Ana")
        self.assertEqual(
            why("Solo queda T4, medida tras desplegar en los cuatro productos (los otros están parados)"),
            "Solo queda T4…",
        )
        self.assertLessEqual(len(why("x " * 80)), panel_cards.WHY_MAX)
