"""The backlog port (providers/backlog.py) and its local adapter: the panel reads through it, the guard finds a task's
local keys, and a tick loads the backlog once."""

import threading
from collections.abc import Callable
from typing import Any
from unittest import mock

from helpers import Env

from foreman import util
from foreman.core.project.config import Project
from foreman.core.tasks import backlog
from foreman.core.tasks.backlog import Local
from foreman.loop import tick
from foreman.providers import registry
from foreman.providers.backlogfile import BacklogFile
from foreman.ui.panel import views


class Stub:
    def __init__(self, p: Project) -> None:
        self.p = p

    def load(self) -> backlog.Data:
        return {**backlog.empty(), "tasks": [{"id": "TY-9", "title": "t", "status": "backlog", "detail": "", "priority": "none"}]}


def test_the_panel_reads_through_the_port(monkeypatch: Any):
    e = Env()
    monkeypatch.setattr(registry, "backlog", Stub)  # the file stays empty
    assert [t["id"] for t in views.state(e.p)["tasks"]] == ["TY-9"]


def test_the_local_keys_of_a_task():
    e = Env()
    b = registry.backlog(e.p)
    tid = b.add("t", criteria="c")
    b.update(tid, worktree="/w/ty-1", branch="foreman/ty-1")
    assert b.local(tid) == Local("/w/ty-1", "foreman/ty-1")
    assert b.local("TY-99") is None


def counted(e: Env, p: Project, run: Callable[[Project], object]) -> tuple[int, int]:
    """Runs `run(p)` and returns the reads and writes of the backlog's file it made."""
    f = e.home / "backlog.json"
    with (
        mock.patch.object(util, "read_json", wraps=util.read_json) as reads,
        mock.patch.object(util, "write_json", wraps=util.write_json) as writes,
    ):
        run(p)
    return sum(c.args[0] == f for c in reads.call_args_list), sum(c.args[0] == f for c in writes.call_args_list)


def test_a_tick_loads_the_backlog_once():
    e = Env(slots=2)
    b = registry.backlog(e.p)
    b.add("vague idea", criteria="c")
    b.add("clear one", status="ready", criteria="tests pass")
    b.add("another", status="ready", criteria="x")
    b.add("a finding", status="triage")
    reads, writes = counted(e, e.p, tick.tick)
    assert writes > 0  # the tick launched the two ready tasks
    assert reads - writes == 1  # each edit reads and writes once; every other load is the one read


def test_outside_a_tick_every_load_reads():
    e = Env()

    def two_loads(p: Project) -> None:
        registry.backlog(p).load()
        registry.backlog(p).load()

    reads, _ = counted(e, e.p, two_loads)
    assert reads == 2


def test_an_edit_in_a_tick_is_seen_by_the_next_load():
    e = Env()
    tid = registry.backlog(e.p).add("t", criteria="c")
    with registry.tick_scope(e.p):
        assert backlog.get(registry.backlog(e.p).load(), tid)["status"] == "backlog"
        registry.backlog(e.p).set_status(tid, "ready")
        assert backlog.get(registry.backlog(e.p).load(), tid)["status"] == "ready"
    d = util.read_json(e.home / "backlog.json", None)
    backlog.get(d, tid)["status"] = "in progress"
    util.write_json(e.home / "backlog.json", d)
    assert backlog.get(registry.backlog(e.p).load(), tid)["status"] == "in progress"


def test_a_reload_in_a_tick_sees_another_process():
    e = Env()
    tid = registry.backlog(e.p).add("t", criteria="c")
    with registry.tick_scope(e.p):
        registry.backlog(e.p).load()
        BacklogFile(e.p).set_status(tid, "canceled", "owner")  # another process: the owner's CLI
        assert backlog.get(registry.backlog(e.p).load(), tid)["status"] == "backlog"
        assert backlog.get(registry.backlog(e.p).reload(), tid)["status"] == "canceled"
        assert backlog.get(registry.backlog(e.p).load(), tid)["status"] == "canceled"


def test_an_edit_cannot_land_between_a_reloads_read_and_its_copy():
    e = Env()
    tid = registry.backlog(e.p).add("t", criteria="c")
    read, writer = util.read_json, threading.Thread()

    def read_then_an_edit_starts(path: Any, default: Any) -> Any:
        nonlocal writer
        d = read(path, default)
        if not writer.is_alive() and writer.ident is None:  # the reload's read: another thread edits meanwhile
            writer = threading.Thread(target=lambda: registry.backlog(e.p).set_status(tid, "ready"))
            writer.start()
            writer.join(0.3)  # the edit waits for the reload's file lock
        return d

    with registry.tick_scope(e.p):
        registry.backlog(e.p).load()
        with mock.patch.object(util, "read_json", side_effect=read_then_an_edit_starts):
            registry.backlog(e.p).reload()
            writer.join()
        assert backlog.get(registry.backlog(e.p).load(), tid)["status"] == "ready"
