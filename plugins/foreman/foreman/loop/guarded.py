from collections.abc import Callable

from .. import providers
from ..core.project.config import Project
from ..core.rules import decide
from ..core.tasks import backlog
from ..providers import registry
from ..services.delivery import check, mergecheck


def guarded(p: Project, tid: str, what: str, fn: Callable[[], object], field: str = "retry") -> None:
    """One task's step: a Transient failure is retried next tick (noted once per error); the 3rd in a row blocks the
    task with the last error; a success resets the count. Any other failure blocks it at once. The others go on.
    The count lives in `{field}_count` and `{field}_error`: the background check has its own, apart from the tick's."""
    err, cnt = f"{field}_error", f"{field}_count"
    try:
        fn()
        i = backlog.get(registry.backlog(p).load(), tid)
        if i.get(err) or i.get(cnt):
            registry.backlog(p).update(tid, **{err: "", cnt: 0})
    except providers.Transient as e:
        i = backlog.get(registry.backlog(p).load(), tid)
        r = decide.retry_step(int(i.get(cnt) or 0), i.get(err), str(e))
        if r.block:
            registry.backlog(p).update(tid, blocked=f"the loop could not {what}: {e}"[:300], **{err: "", cnt: r.count})
            backlog.event(p, tid, f"blocked: could not {what} {decide.TRANSIENT_ATTEMPTS} times in a row: {e}"[:300])
            return
        registry.backlog(p).update(tid, **{cnt: r.count, err: str(e)})
        if r.say:  # the platform hiccuped: stay as is, try again next tick, say it once
            backlog.event(p, tid, f"could not {what}, will retry: {e}"[:300])
    except Exception as e:
        registry.backlog(p).update(tid, blocked=f"the loop could not {what}: {e}"[:300])
        backlog.event(p, tid, f"blocked: could not {what}: {e}"[:300])


def merged_guarded(p: Project, tid: str) -> None:
    guarded(p, tid, "check it merged with main", lambda: mergecheck.merged_check(p, tid), "check_retry")


def check_guarded(p: Project, tid: str, ci: str | None) -> None:
    """The check, in its thread: its own retry count (the tick's belongs to the queue step), 3 Transient in a row block."""
    guarded(p, tid, "check it", lambda: check.check(p, tid, ci), "check_retry")
