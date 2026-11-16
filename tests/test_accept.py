"""`foreman accept` through the repo port: git's refusal reaches the owner as foreman's message, not a traceback."""

import pytest
from helpers import Env, fm

from fm.core.tasks import backlog


def test_accept_of_a_missing_branch_exits_with_foreman_message():
    e = Env()
    tid = backlog.add(e.p, "x", status="ready", criteria="y")
    backlog.update(e.p, tid, status="in review", branch="nope")
    with pytest.raises(SystemExit) as r:
        fm(e.home, "accept", tid)
    assert isinstance(r.value.code, str) and r.value.code.startswith("foreman: ")
