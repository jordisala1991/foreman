"""The verdict text: pure, so each row is a call."""

import unittest

from fm import verdict
from fm.verdict import Finding

REVIEW = {"round": 1, "high": 1, "medium": 0, "low": 1}


class Verdict(unittest.TestCase):
    def test_open_findings_are_listed_one_per_line(self):
        out = verdict.render("high", [], "t", REVIEW, "F2|L|-|nit\nF1|H|x.py:5|boom")
        lines = out.splitlines()
        h = next(n for n, x in enumerate(lines) if "x.py:5" in x)
        low = next(n for n, x in enumerate(lines) if "nit" in x)
        self.assertIn("H", lines[h])
        self.assertIn("boom", lines[h])
        self.assertIn("L", lines[low])
        self.assertLess(h, low)

    def test_findings_parse_only_the_fixed_line(self):
        text = "Open: H 1\nFindings:\nF1 | H | a.py:1 | bad\n**F1 · H** prose\n- F2 | M | - | other"
        self.assertEqual(verdict.findings(text), [Finding("F1", "H", "a.py:1", "bad"), Finding("F2", "M", "-", "other")])
        self.assertEqual(verdict.findings(""), [])

    def test_nothing_open_says_clean(self):
        out = verdict.render("low", [], "t", {"round": 1, "high": 0, "medium": 0, "low": 0}, "")
        self.assertIn(verdict.CLEAN, out)
        self.assertNotIn("\n- ", out)
        out = verdict.render("low", [], "t", {"round": 1, "high": 0, "medium": 1, "low": 0}, "")
        self.assertNotIn(verdict.CLEAN, out)

    def test_the_risk_and_every_reason_appear(self):
        out = verdict.render("high", ["r one", "r two"], "t", None, "")
        for x in ("high", "r one", "r two"):
            self.assertIn(x, out)

    def test_unlisted_findings_are_never_read_as_clean(self):
        for text, medium in (("", 1), ("F1 | M | a.py:1 | x", 2)):
            out = verdict.render("medium", [], "t", {"round": 1, "high": 0, "medium": medium, "low": 0}, text)
            self.assertNotIn(verdict.CLEAN, out)
            self.assertIn("1 more open", out)

    def test_a_huge_review_is_cut(self):
        out = verdict.render("low", [], "t", REVIEW, "x" * 100000)
        self.assertLess(len(out), 60000)

    def test_no_review_is_said(self):
        self.assertIn(verdict.NO_REVIEW, verdict.render("low", [], "t", None, ""))
