"""costs.usage skips a transcript line that is not an object; util.json_object / json_list give None / [] for anything of another shape."""

import json
import tempfile
import unittest
from pathlib import Path

from fm import costs, util


class Shapes(unittest.TestCase):
    def test_json_object_and_list(self):
        self.assertEqual(util.json_object({"x": 1}), {"x": 1})
        for v in (None, "text", [1], 3):
            self.assertIsNone(util.json_object(v), repr(v))
        self.assertEqual(util.json_list([1, 2]), [1, 2])
        for v in (None, "text", {"a": 1}, 3):
            self.assertEqual(util.json_list(v), [], repr(v))

    def test_usage_skips_lines_that_are_not_objects(self):
        good = {"type": "assistant", "message": {"id": "m1", "model": "claude-sonnet-5-5", "usage": {"input_tokens": 1000, "output_tokens": 0}}}
        f = Path(tempfile.mkdtemp()) / "s.jsonl"
        f.write_text("\n".join(["[1, 2]", '"x"', "null", "not json", json.dumps(good)]) + "\n")
        u = costs.usage([f])
        self.assertEqual((u["calls"], u["tokens"]), (1, 1000))


if __name__ == "__main__":
    unittest.main()
