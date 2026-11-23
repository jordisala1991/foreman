"""An unblock forgets the Transient failures `loop.guarded` counted for the task, for every step it guards (DESIGN.md
«Retry policy»: the 3rd in a row blocks, so the first after an unblock is the 1st)."""

import pytest
from helpers import Env, fm
from test_push import transient

from foreman.core.tasks import backlog
from foreman.loop.guarded import guarded
from foreman.providers import registry


@pytest.mark.usefixtures("owner")
@pytest.mark.parametrize("flags", [(), ("--relaunch",)])
def test_an_unblock_forgets_the_transient_failures_of_every_guarded_step(flags: tuple[str, ...]):
    e = Env(test="true")
    tid = registry.backlog(e.p).add("x", status="ready", criteria="y")
    for f in backlog.RETRY_FIELDS:
        guarded(e.p, tid, "x", transient, f)
        guarded(e.p, tid, "x", transient, f)
    registry.backlog(e.p).update(tid, blocked="why")
    fm(e.home, "unblock", tid, *flags)
    for f in backlog.RETRY_FIELDS:
        guarded(e.p, tid, "x", transient, f)
        i = e.task(tid)
        assert (f, i[f"{f}_count"], bool(i.get("blocked"))) == (f, 1, False)
