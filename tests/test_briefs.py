"""The briefs carry the owner's rule on silencing checkers (requested by the owner; not in FN-22's criterion)."""

import unittest
from pathlib import Path

BRIEFS = Path(__file__).resolve().parent.parent / "fm" / "briefs"


class Briefs(unittest.TestCase):
    def test_work_brief_forbids_silencing_without_asking(self):
        text = (BRIEFS / "work.md").read_text(encoding="utf-8")
        self.assertIn("A lint or type error is fixed at its root", text)
        self.assertIn("without asking first with `fm ask`", text)

    def test_reviewer_brief_makes_new_suppressions_an_m_finding(self):
        text = (BRIEFS / "reviewer.md").read_text(encoding="utf-8")
        self.assertIn("New suppressions in the diff", text)
        self.assertIn("M finding, unless the owner approved them", text)


if __name__ == "__main__":
    unittest.main()
