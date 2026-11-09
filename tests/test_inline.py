"""The diff's visible lines and findings as line comments: pure."""

import unittest

from fm import inline
from fm.verdict import Finding, key


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


if __name__ == "__main__":
    unittest.main()


class Threads(unittest.TestCase):
    def test_fixed_are_the_unsettled_threads_not_in_the_findings(self):
        a, b = Finding("F1", "M", "a.py:1", "a", ""), Finding("F2", "M", "a.py:1", "b", "")
        ts = [inline.Thread("1", key(a), False), inline.Thread("2", key(b), False), inline.Thread("3", "finding-c", True)]
        self.assertEqual(inline.fixed(ts, [a]), [ts[1]])

    def test_fresh_are_the_comments_without_a_thread(self):
        cs = [inline.LineComment("a", "x", 1, ""), inline.LineComment("d", "x", 2, "")]
        ts = [inline.Thread("1", "a", True), inline.Thread("2", "b", False)]
        self.assertEqual(inline.fresh(cs, ts), [cs[1]])
