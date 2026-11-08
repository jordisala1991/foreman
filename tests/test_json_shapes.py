"""costs.usage skips a transcript line that is not an object; util.sub / util.items give {} / [] for anything of another shape."""

import json
import tempfile
import unittest
from pathlib import Path

from fm import costs, util


class Shapes(unittest.TestCase):
    def test_sub_and_items(self):
        d = {"a": {"x": 1}, "n": None, "s": "text", "l": [1, 2]}
        self.assertEqual(util.sub(d, "a"), {"x": 1})
        for k in ("n", "s", "l", "missing"):
            self.assertEqual(util.sub(d, k), {}, k)
        self.assertEqual(util.items(d, "l"), [1, 2])
        for k in ("n", "s", "a", "missing"):
            self.assertEqual(util.items(d, k), [], k)

    def test_usage_skips_lines_that_are_not_objects(self):
        good = {"type": "assistant", "message": {"id": "m1", "model": "claude-sonnet-5-5", "usage": {"input_tokens": 1000, "output_tokens": 0}}}
        f = Path(tempfile.mkdtemp()) / "s.jsonl"
        f.write_text("\n".join(["[1, 2]", '"x"', "null", "not json", json.dumps(good)]) + "\n")
        u = costs.usage([f])
        self.assertEqual((u["calls"], u["tokens"]), (1, 1000))


if __name__ == "__main__":
    unittest.main()
