"""Tasks and subtasks (pure): a subtask names its parent in `parent`; the parent stores no list."""

from . import backlog, blockers


def of(tasks: list[backlog.Task], pid: str) -> list[backlog.Task]:
    return [i for i in tasks if i.get("parent") == pid]


def ordered(tasks: list[backlog.Task]) -> list[backlog.Task]:
    """Each task in file order, followed by its subtasks."""
    ids = {i["id"] for i in tasks}
    out: list[backlog.Task] = []
    for i in tasks:
        if i.get("parent") not in ids:
            out += [i, *of(tasks, i["id"])]
    return out


def refusal(tasks: list[backlog.Task], pid: str) -> str:
    """Why a subtask cannot be filed under `pid` ("" if it can)."""
    parent = next((i for i in tasks if i["id"] == pid), None)
    if parent is None:
        return f"no task {pid}"
    if parent.get("parent"):
        return f"{pid} is itself a subtask of {parent['parent']}: only one level"
    if parent["status"] not in ("triage", "backlog"):
        return f"{pid} is {parent['status']}: a task is split before it starts (triage or backlog)"
    return ""


def outcome(subs: list[backlog.Task]) -> str:
    """How the parent closes once its subtasks have: "" while any is open or there are none."""
    if not subs or any(s["status"] not in backlog.CLOSED for s in subs):
        return ""
    return "done" if any(s["status"] == "done" for s in subs) else "canceled"


def agent_refusal(tasks: list[backlog.Task], caller: str, parent: str, what: str) -> str:
    """Why `caller` (FM_TASK; "" for the owner's shell) may not file or edit a subtask of `parent` ("" if it may): only
    the owner, or the parent's own agent for its own subtasks."""
    if not caller or any(i["id"] == caller == parent and i.get("agent") and i["agent"].get("kind") == "parent" for i in tasks):
        return ""
    return f"{what} by the owner, not from an agent's session"


def summary(sub: backlog.Task, questions: list[dict[str, str]], tasks: list[backlog.Task]) -> dict[str, object]:
    """What the parent agent reads of one subtask: status, PR, review rounds, open questions, blocks."""
    pr: dict[str, str] = sub.get("pr") or {}
    return {
        "id": sub["id"],
        "status": sub["status"],
        "title": sub["title"],
        "pr": pr.get("url", ""),
        "review_rounds": len(sub.get("reviews") or []),
        "questions": [{"id": q["id"], "text": q["text"]} for q in questions],
        "blocked": sub.get("blocked", ""),
        "waiting_for": blockers.pending(sub, tasks),
    }


def notice(sub: backlog.Task, subs: list[backlog.Task], parent: str) -> str:
    """The message the parent agent gets when one of its subtasks closes."""
    how = "merged" if sub["status"] == "done" else "canceled"
    text = f"Subtask {sub['id']} «{sub['title']}» was {how}. Subtasks of {parent}: " + ", ".join(f"{s['id']} {s['status']}" for s in subs) + "."
    if outcome(subs):
        text += (
            f" Every subtask is closed: check the goal of {parent} against what landed, tell the owner what holds and "
            "what is missing, and wait for his yes before `foreman done`."
        )
    return text
