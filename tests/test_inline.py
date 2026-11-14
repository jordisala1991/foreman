"""The diff's visible lines and findings as line comments: pure."""

import unittest

from fm import inline
from fm.verdict import Finding, link_target


class Inline(unittest.TestCase):
    def test_visible_lines_are_the_added_and_context_lines_of_the_new_side(self):
        diff = (
            "--- a/a.py\n+++ b/a.py\n@@ -1,3 +1,4 @@\n x\n-old\n+new\n+++ y\n z\n\\ No newline at end of file\n"
            "--- a/b.py\n+++ /dev/null\n@@ -1 +0,0 @@\n-gone\n"
        )
        self.assertEqual(inline.visible_lines(diff), {"a.py": {1, 2, 3, 4}})

    def test_only_findings_on_a_visible_line_are_comments(self):
        visible = {"a.py": {1, 2, 3, 4}}
        found = [Finding("F1", "M", p, "w", "") for p in ("a.py:3", "a.py:99", "-", "b.py:3", "a.py")]
        (c,) = inline.line_comments(found, visible)
        self.assertEqual((c.path, c.line), ("a.py", 3))

    def test_the_why_is_folded_in_the_comment(self):
        (c,) = inline.line_comments([Finding("F1", "H", "a.py:1", "bad", "because")], {"a.py": {1}})
        self.assertIn("bad", c.body)
        self.assertLess(c.body.index("<details"), c.body.index("because"))

    def test_a_finding_link_works_for_any_place_the_reviewer_writes(self):
        def link(place: str) -> str:
            return inline.resolve_links(f"[`{place}`](fm-link:{link_target(place)}) x", "https://github.com/o/r", "SHA", {})

        base = "https://github.com/o/r/blob/SHA"
        self.assertEqual(link("a.py:3-5"), f"[`a.py:3-5`]({base}/a.py#L3) x")
        self.assertEqual(link("a.py:12:5"), f"[`a.py:12:5`]({base}/a.py#L12) x")
        self.assertEqual(link("src/my file.py:7"), f"[`src/my file.py:7`]({base}/src/my%20file.py#L7) x")
        self.assertEqual(link("app/[id]/page.tsx:12"), f"[`app/[id]/page.tsx:12`]({base}/app/%5Bid%5D/page.tsx#L12) x")
        self.assertEqual(link("a[1].py:3"), f"[`a[1].py:3`]({base}/a%5B1%5D.py#L3) x")
        self.assertEqual(link("a`b.py:3"), f"[`a`b.py:3`]({base}/a%60b.py#L3) x")
        self.assertEqual(link("the whole module"), "`the whole module` x")


if __name__ == "__main__":
    unittest.main()
