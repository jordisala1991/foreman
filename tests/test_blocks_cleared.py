"""Any command that clears a block forgets the Transient failures `loop.guarded` counted (DESIGN.md «Retry policy»)."""

import pytest
from helpers import Env, fm
from test_misled import run_as

from foreman.providers import registry

COUNTS = ["retry_count", "pr_count", "check_retry_count"]


@pytest.mark.usefixtures("owner")
@pytest.mark.parametrize(
    ("status", "args"),
    [("in review", ["review"]), ("triage", ["go"]), ("in progress", ["done", "--summary", "s", "--misled", "none"])],
)
def test_a_command_that_clears_a_block_forgets_the_counts(status: str, args: list[str]):
    e = Env(test="true")
    tid = registry.backlog(e.p).add("x", status="triage", criteria="y")
    registry.backlog(e.p).set_status(tid, status)
    registry.backlog(e.p).update(tid, blocked="why", **dict.fromkeys(COUNTS, 2))
    if args[0] == "done":
        run_as(e, tid, "ty-1", *args)
    else:
        fm(e.home, *args, tid)
    i = e.task(tid)
    assert [i.get(k) for k in COUNTS] == [0, 0, 0] and not i["blocked"]
