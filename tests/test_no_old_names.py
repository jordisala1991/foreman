"""The glossary's names, with no compatibility: an old flag, environment variable, config section or stored key is
not read."""

import os
import unittest
from unittest import mock

from helpers import Env, fm

from fm import backlog, util
from fm.config import Project


class OldNames(unittest.TestCase):
    def setUp(self) -> None:
        self.e = Env()
        patch = mock.patch.dict(os.environ)
        patch.start()
        self.addCleanup(patch.stop)
        for k in ("FM_TASK", "FM_ISSUE"):
            os.environ.pop(k, None)

    def test_old_flag_fails(self):
        with self.assertRaises(SystemExit) as r:
            fm(self.e.home, "add", "t", "--claim", "x")
        self.assertEqual(r.exception.code, 2)
        tid = fm(self.e.home, "add", "t", "--goal", "x").strip()
        self.assertEqual(self.e.task(tid)["acceptance"]["goal"], "x")

    def test_old_env_is_not_read(self):
        tid = backlog.add(self.e.p, "t", criteria="c")
        os.environ["FM_ISSUE"] = tid
        with self.assertRaises(SystemExit) as r:
            fm(self.e.home, "block", "why")
        self.assertIn("FM_TASK", str(r.exception.code))
        os.environ["FM_TASK"] = tid
        fm(self.e.home, "block", "why")
        self.assertEqual(self.e.task(tid)["blocked"], "why")

    def test_old_config_section_is_not_read(self):
        toml = self.e.home / "fm.toml"
        toml.write_text('[integrate]\nautonomy = "medium"\n')
        self.assertEqual(Project.load(self.e.home).get("merge", "autonomy"), "manual")
        toml.write_text('[merge]\nautonomy = "low"\n')
        self.assertEqual(Project.load(self.e.home).get("merge", "autonomy"), "low")

    def test_old_stored_key_is_not_read(self):
        util.write_json(self.e.p.backlog_file, {"schema_version": 5, "issues": [{"id": "TY-1", "title": "t", "status": "backlog"}]})
        self.assertEqual(backlog.tasks(backlog.load(self.e.p)), [])


if __name__ == "__main__":
    unittest.main()
