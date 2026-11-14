"""The verdict as a review body: pure."""

import unittest
from dataclasses import replace

from fm import verdict
from fm.verdict import FIX, LOOK, READY, Case, Finding, findings, key, render


def case(risk="low", tests="pass", ci="pass", counts=(0, 0, 0), reasons=None) -> Case:
    return Case(risk, reasons or ["r"], tests, ci, "abcdef1234567", "does x", 3, 120, counts)


def f(sev: str, what: str, place: str = "a.py:1", why: str = "") -> Finding:
    return Finding("F1", sev, place, what, why)


class Verdict(unittest.TestCase):
    def test_the_headline_follows_the_findings_checks_and_risk(self):
        rows = [
            (case(), [], READY),
            (case(counts=(0, 0, 2)), [f("L", "a"), f("L", "b")], READY),
            (case(counts=(0, 1, 0)), [f("M", "a")], LOOK.format(n=1)),
            (case("medium"), [], LOOK.format(n=1)),
            (case("medium", counts=(0, 2, 0)), [f("M", "a"), f("M", "b")], LOOK.format(n=3)),
            (case("high", counts=(1, 0, 0)), [f("H", "a")], FIX.format(n=1)),
            (case(tests="fail"), [], FIX.format(n=1)),
            (case("high", "fail", "fail", (1, 0, 0)), [f("H", "a")], FIX.format(n=3)),
            (case(counts=(0, 1, 0)), [], LOOK.format(n=1)),
        ]
        for c, found, want in rows:
            self.assertEqual(verdict.headline(c, found), want)

    def test_the_body_has_the_facts_in_order(self):
        c = case("medium", ci="", reasons=["r one", "r two"])
        body = render(c, [], set())
        order = [body.index(x) for x in ("Look", "does x", "r one", "abcdef123", "120")]
        self.assertEqual(order, sorted(order))
        self.assertIn("r two", body)
        placed = f("M", "w", why="secret why")
        self.assertNotIn("secret why", render(case(counts=(0, 1, 0)), [placed], set()))

    def test_only_blocking_findings_sit_outside_the_fold(self):
        found = [f("H", "hhh"), f("M", "mmm"), f("L", "placed", "a.py:2"), f("L", "unplaced", "a.py:3")]
        placed = {found[2].place}
        body = render(case(counts=(1, 1, 2)), found, placed)
        fold = body.index("<details")
        self.assertLess(body.index("hhh"), fold)
        self.assertNotIn("mmm", body[:fold])
        self.assertGreater(body.index("mmm"), fold)
        self.assertGreater(body.index("unplaced"), fold)
        self.assertNotIn("placed", body.replace("unplaced", ""))

    def test_a_repeated_nit_is_still_reachable(self):
        a, b = f("L", "same", "a.py:999"), f("L", "same", "a.py:3")
        body = render(case(counts=(0, 0, 2)), [a, b], {"a.py:3"})
        self.assertIn("a.py:999", body)
        self.assertNotIn("a.py:3", body)

    def test_counts_use_the_singular_for_one(self):
        self.assertIn("1 nit<", render(case(counts=(0, 0, 1)), [f("L", "x", "-")], set()))
        self.assertNotIn("1 nits", render(case(counts=(0, 1, 1)), [f("L", "x", "-")], set()))
        self.assertIn("2 nits", render(case(counts=(0, 0, 2)), [f("L", "x", "-")], set()))

    def test_reviewer_text_cannot_notify_or_break_the_fold(self):
        body = render(case(counts=(1, 0, 0)), [f("H", "ping @someone </details>")], set())
        self.assertNotIn("@someone", body)
        self.assertNotIn("</details>", body)

    def test_findings_parse_only_the_fixed_line(self):
        text = "Open: H 1\nFindings:\nF1 | H | a.py:1 | bad | it breaks\n**F1 · H** prose\n- F2 | M | - | other\nF3 | X | a | b"
        self.assertEqual(findings(text), [Finding("F1", "H", "a.py:1", "bad", "it breaks"), Finding("F2", "M", "-", "other", "")])
        self.assertEqual(findings(""), [])

    def test_a_finding_keeps_its_key_when_its_id_or_line_moves(self):
        self.assertEqual(key(findings("F1|M|a.py:3|x")[0]), key(findings("F7|M|a.py:9|x")[0]))
        self.assertNotEqual(key(findings("F1|M|a.py:3|x")[0]), key(findings("F1|M|a.py:3|y")[0]))


if __name__ == "__main__":
    unittest.main()


class Since(unittest.TestCase):
    def test_since_last_push_shows_only_with_an_earlier_review(self):
        for since in ((2, 7), (0, 0)):
            out = render(replace(case(), since=since), [], set())
            self.assertIn(verdict.SINCE.format(fixed=since[0], new=since[1]), out)
        self.assertNotIn("since last push", render(case(), [], set()))

    def test_complete_means_every_counted_finding_is_listed(self):
        m = [f("M", "x")]
        for counts, listed, want in (
            (None, [], False),
            ((0, 0, 0), [], True),
            ((0, 1, 0), [], False),
            ((0, 1, 0), m, True),
            ((1, 1, 0), m, False),
            ((0, 1, 0), m + [f("M", "y")], True),
        ):
            self.assertEqual(verdict.complete(case(counts=counts), listed), want, (counts, listed))
