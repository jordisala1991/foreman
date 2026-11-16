import re
from typing import Any

from ..core.project.config import Project
from ..core.tasks import backlog, blockers, subtasks
from ..services.agents.launch import launch
from ..services.agents.running import running
from .guarded import guarded

PRIORITY = {p: n for n, p in enumerate(backlog.PRIORITIES)}


def sort_key(i: dict[str, Any]) -> tuple[int, int, int]:
    m = re.search(r"(\d+)$", i["id"])
    return (PRIORITY.get(i.get("priority", "none"), 9), 0 if i["status"] == "ready" else 1, int(m.group(1)) if m else 0)


def launch_free(p: Project) -> None:
    if p.paused:
        return
    d = backlog.load(p)
    free = int(p.get("agents", "slots")) - running(d)
    tasks = backlog.tasks(d)
    waiting = (i for i in tasks if i["status"] in ("backlog", "ready") and not i.get("blocked") and not blockers.pending(i, tasks))
    todo = sorted((i for i in waiting if not subtasks.of(tasks, i["id"])), key=sort_key)
    for i in todo[: max(0, free)]:
        guarded(p, i["id"], "launch it", lambda i=i: launch(p, i))
