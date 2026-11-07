"""A pass comes at the next tick when the owner wrote since the last one (hooks_mod.owner_wrote_since)."""

from __future__ import annotations

import time
import unittest
from datetime import datetime

from helpers import ProjectCase

from foreman import hooks_mod, notes, util


class PassWakesTest(ProjectCase):
    def setUp(self):
        super().setUp()
        beat = datetime.fromtimestamp(time.time() - 120).strftime("%Y-%m-%dT%H:%M:%S")  # a pass 2 min ago
        util.write_json(self.project.state / "heartbeats.json", {"loop": beat})
        self.mark = {"session": "S1", "since": datetime.fromtimestamp(time.time() - 3600).strftime("%Y-%m-%dT%H:%M:%S")}

    def due(self) -> bool:
        return hooks_mod.pass_due(self.project, self.mark)

    def test_no_message_no_pass(self):
        self.assertFalse(self.due())

    def test_owner_message_after_the_pass(self):
        notes.inbox_add(self.project, "Aprobado desde el panel")
        self.assertTrue(self.due())

    def test_read_message_no_pass(self):
        m = notes.inbox_add(self.project, "hola")
        notes.inbox_read(self.project, [str(m["id"])])
        self.assertFalse(self.due())

    def test_message_before_the_pass_no_pass(self):
        notes.inbox_add(self.project, "antes")
        msgs = util.read_json(notes.inbox_path(self.project), [])
        msgs[-1]["time"] = datetime.fromtimestamp(time.time() - 600).strftime("%Y-%m-%d %H:%M")
        util.write_json(notes.inbox_path(self.project), msgs)
        self.assertFalse(self.due())

    def test_orchestrator_message_no_pass(self):
        notes.inbox_add(self.project, "nota mía", sender="orchestrator")
        self.assertFalse(self.due())


if __name__ == "__main__":
    unittest.main()
