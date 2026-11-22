"""The loop: a plain process, no model. Each tick, for each registered project:

1. heartbeat (state/loop.json), read by the health check «loop dead» and by the panel
Then the steps, in this order (a broken one is logged in state/errors.jsonl and does not stop the others):
2. sessions: a session not listed at launch gets its full id (services/agents/launch.py)
3. scratch: removes the scratch folder of every review round that is over (steps/reviewing.py)
4. sources: plug-in signals, and recurring entries that fell due, become tasks (steps/sources.py, steps/schedule.py)
5. answers: the owner's answers go back to its agent (its own session, resumed) (steps/talk.py)
6. agents: one that ended its turn without finishing (no foreman done / ready / ask / block) is nudged once, then blocked;
   waiting: under Orca, an agent that waits for an answer in its chat gets one event (steps/talk.py)
7. pulls: with a provider that has PRs (GitHub), a running task's branch is pushed and it gets a draft PR (services/delivery/pulls.py)
8. queue: accepted tasks are merged first; then a branch whose head or main's moved is checked (tests, diff, risk) in
   the background, one at a time, and the autonomy may merge it. A check slower than util.BACKGROUND_WAIT_S does not hold
   the tick up: it finishes on its own and the next ticks go on (loop/queue.py)
9. health: broken states become triage tasks, deduplicated (steps/health/health.py)
10. blockers: a task whose blockers are all done is released (steps/release.py)
11. parents: a closed parent's session is stopped; a let-in parent gets its parent agent outside the slots, told of each
    subtask merged or canceled (steps/parents.py)
12. launch: while there are free slots (an agent waiting for the owner holds none), a planning agent for a `backlog` task,
    the responsible agent for a `ready` one; a task whose blockers are not all done waits. The loop never plans, never
    reviews, never talks to the owner (loop/launch.py)
13. mechanic: a diagnosing helper for each health finding in triage, in the slots launch left free (steps/mechanic.py)
14. folders: under Orca, an agent folder goes once its agent finished its part and its session is not in use, and every folder
    of a closed task; a failed removal is a line in errors.jsonl, tried next tick (steps/folders.py)
15. notify: with `[owner] notify`, the owner's command runs once for each need that starts, in the background (steps/notify.py)
"""

import fcntl
import os
import time
import traceback

from .. import launcher, providers, util
from ..core.project.config import Project
from ..core.project.registry import registered, registry_file
from ..providers import registry
from ..providers.runner import NO_ROWS
from ..services.agents.launch import fill_sessions
from ..services.delivery import pulls
from ..steps import folders, mechanic, notify, parents, release, sources
from ..steps.health import health
from ..steps.reviewing import sweep_scratch
from ..steps.talk import deliver_answers, watch_agents, watch_waiting
from .guarded import guarded
from .launch import launch_free
from .queue import run_queue


def beat(p: Project, step: str) -> None:
    util.write_json(
        p.state / "loop.json",
        {"pid": os.getpid(), "time": util.now(), "epoch": time.time(), "step": step, "version": launcher.ROOT.name},
    )


def tick(p: Project) -> None:
    beat(p, "start")
    try:
        rows = registry.rows(p)
    except providers.ConfigError as e:  # a bad [agents] runner: no agent step can run; health files it
        util.append_jsonl(p.state / "errors.jsonl", {"time": util.now(), "step": "runner", "error": repr(e), "trace": ""})
        steps = [("health", lambda: health.run(p, NO_ROWS))]
    else:
        steps = [
            ("sessions", lambda: fill_sessions(p, rows)),
            ("scratch", lambda: sweep_scratch(p)),
            ("sources", lambda: sources.run(p)),
            ("answers", lambda: deliver_answers(p, rows)),
            ("agents", lambda: watch_agents(p, rows)),
            ("waiting", lambda: watch_waiting(p, rows)),
            ("pulls", lambda: run_pulls(p)),
            ("queue", lambda: run_queue(p, rows)),
            ("health", lambda: health.run(p, rows)),
            ("blockers", lambda: release.run(p)),
            ("parents", lambda: parents.run(p, rows)),
            ("launch", lambda: launch_free(p)),
            ("mechanic", lambda: mechanic.run(p, rows)),
            ("folders", lambda: folders.run(p, rows)),
            ("notify", lambda: notify.run(p, rows)),
        ]
    with registry.tick_scope(p):  # the tick loads the backlog once
        for name, step in steps:
            beat(p, name)
            try:
                step()
            except Exception as e:  # one broken step must not stop the others, health files it
                util.append_jsonl(p.state / "errors.jsonl", {"time": util.now(), "step": name, "error": repr(e), "trace": traceback.format_exc()[-2000:]})


def run_pulls(p: Project) -> None:
    for i in pulls.running(p):
        guarded(p, i["id"], "push it or open its PR", lambda i=i: pulls.sync(p.for_task(i), i))


def forever(homes: list[str] | None = None, once: bool = False) -> None:
    with open(str(registry_file()) + ".loop.lock", "a") as held:  # the lock lasts as long as the loop
        try:
            fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SystemExit("foreman: another loop is running for this registry") from None
        while True:
            if why := launcher.stale():  # another version installed: exit, launchd starts the new one
                raise SystemExit(f"foreman: {why}")
            projects = [Project.load(h) for h in (homes or registered())]
            for p in projects:
                tick(p)
            if once:
                return
            time.sleep(min((int(p.get("health", "loop_seconds")) for p in projects), default=30))
