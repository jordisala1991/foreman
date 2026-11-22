"""`[owner] notify = "<command>"`: the loop's last step runs it once for each need `watch.needs` finds new, with the need's line as
its last argument. The needs at the first run are the accepts already waiting (as `foreman watch` prints at start); later, each
key not in `state/notified.json`, which is written before any command starts: a restart never repeats a notification. The command
is split like a shell would but run without one, in a background thread with a time limit; a failure is one line in
errors.jsonl, not retried, whose step is `notify <command>`: health files one finding per failing command, not one per need."""

import shlex
import time

from .. import proc, util
from ..core.project.config import Project
from ..providers.runner import NO_ROWS, Rows
from . import watch

LIMIT_S = 30


def run(p: Project, rows: Rows) -> None:
    cmd = p.get("owner", "notify")
    if not cmd.strip():
        return
    found = watch.needs(p, rows)
    file = p.state / "notified.json"
    stored = util.json_object(util.read_json(file, None))  # None: the first run
    fresh = watch.at_start(found) if stored is None else {k: v for k, v in found.items() if k not in stored}
    if stored is not None and rows is NO_ROWS:  # the listing was unreadable: the parents are not known to be gone
        watch.keep_parents(found, stored)
    util.write_json(file, found)
    for key, line in fresh.items():
        # a key of its own each time: a need that returns while its last command still runs is not dropped
        util.start_background(f"{p.state}:notify:{key}:{time.time_ns()}", p.state / "errors.jsonl", lambda line=line: send(p, cmd, line))


def send(p: Project, cmd: str, line: str) -> None:
    """A failure is an error line of its own: the step and the error are the same for every need and every kind of failure
    (health keys a finding by them); the exit code or the cause goes in the trace."""
    try:
        r = proc.run([*shlex.split(cmd), line], cwd=p.home, timeout=LIMIT_S)
        if r.code == 0:
            return
        detail = f"exited {r.code}: {r.err.strip()[:200]}"
    except (proc.ProcError, ValueError) as e:  # ValueError: a command that cannot be split
        detail = str(e)
    util.append_jsonl(p.state / "errors.jsonl", {"time": util.now(), "step": f"notify {cmd}", "error": "notify command failed", "trace": detail})
