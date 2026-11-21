"""The backlog port's local adapter: one `backlog.json` in the project's home, the one module that names it. Every write
goes through `edit()`, under the file lock. Shared (a tick, `registry.tick_scope`): read once, then each `load()` is a
copy of what was read or last saved."""

import contextlib
import copy
import threading
from collections.abc import Generator
from datetime import date
from typing import Any, Unpack

from .. import util
from ..core.project.config import Project
from ..core.tasks.backlog import CLOSED, STATUSES, BacklogError, Data, Local, NewTask, Task, empty, event, get, new_id, tasks
from ..core.tasks.questions import Question, message


class BacklogFile:
    def __init__(self, p: Project, shared: bool = False) -> None:
        self.p = p
        self.file = p.home / "backlog.json"
        self.shared = shared
        self.kept: Data | None = None
        self.guard = threading.Lock()  # the background check thread calls the shared instance too

    def _read(self) -> Data:
        d: Data = util.read_json(self.file, None) or empty()
        if d.get("schema_version") != 5:
            raise BacklogError(f"{self.file}: schema {d.get('schema_version')!r}, expected 5")
        for k, v in empty().items():
            d.setdefault(k, v)
        return d

    def load(self) -> Data:
        if not self.shared:
            return self._read()
        with self.guard:
            if self.kept is None:
                self.kept = self._read()
            return copy.deepcopy(self.kept)

    def reload(self) -> Data:
        d = self._read()
        if self.shared:
            with self.guard:
                self.kept = copy.deepcopy(d)
        return d

    @contextlib.contextmanager
    def edit(self) -> Generator[Data, None, None]:
        with util.file_lock(self.file):
            d = self._read()
            yield d
            d["updated"] = date.today().isoformat()
            util.write_json(self.file, d)
            if self.shared:  # under the file lock: the kept data follows the order of the writes
                with self.guard:
                    self.kept = copy.deepcopy(d)

    def create(self) -> None:
        if not self.file.exists():
            util.write_json(self.file, empty())

    def local(self, tid: str) -> Local | None:
        try:
            i = get(self._read(), tid)
        except BacklogError:
            return None
        return Local(i.get("worktree", ""), i.get("branch", ""))

    def record_misled(self, tid: str, role: str, text: str) -> None:
        t = " ".join(text.split())
        if t.lower().rstrip(".") in ("", "none", "nothing", "n/a", "-"):
            return
        with self.edit() as d:
            texts = get(d, tid).setdefault("misled", {}).setdefault(role, [])
            if t not in texts:
                texts.append(t)

    def add(self, title: str, *, status: str = "backlog", **f: Unpack[NewTask]) -> str:
        detail, criteria, goal = f.get("detail", ""), f.get("criteria", ""), f.get("goal", "")
        source, priority = f.get("source"), f.get("priority", "none")
        if status not in ("triage", "backlog", "ready"):
            raise BacklogError("a new task starts in triage, backlog or ready")
        if not criteria:
            status = "triage"  # only a task with acceptance criteria can be let in
        with self.edit() as d:
            if source and any(i.get("source") == source and i["status"] not in CLOSED for i in tasks(d)):
                return ""
            tid = new_id(self.p, d)
            task: Task = {
                "id": tid,
                "title": title,
                "status": status,
                "blocked": "",
                "detail": detail,
                "priority": priority,
                "created": util.now(),
            }
            if criteria or goal:
                task["acceptance"] = {"goal": goal, "criteria": criteria}
            if source:
                task["source"] = source
            if parent := f.get("parent"):
                task["parent"] = parent
            if ids := f.get("blocked_by"):
                task["blocked_by"] = ids
            for k in ("model", "effort", "type", "repo", "chain"):
                if v := f.get(k):
                    task[k] = v
            d["tasks"].append(task)
        event(self.p, tid, f"filed ({status}): {title}")
        return tid

    def set_status(self, tid: str, status: str, why: str = "", **keys: Any) -> Task:
        if status not in STATUSES:
            raise BacklogError(f"bad status {status!r}")
        with self.edit() as d:
            i = get(d, tid)
            if status == "canceled" and i["status"] in CLOSED:  # closed meanwhile (merged): keep it
                return i
            i.update(keys, status=status)
            if status in CLOSED:
                i["closed"] = date.today().isoformat()
        event(self.p, tid, f"→ {status}" + (f": {why}" if why else ""))
        return i

    def update(self, tid: str, **keys: Any) -> Task:
        with self.edit() as d:
            i = get(d, tid)
            i.update(keys)
        return i

    def ask(self, tid: str, agent: str, text: str, **f: Unpack[Question]) -> str:
        kind, action = f.get("kind", "question"), f.get("action", "")
        with self.edit() as d:
            i = get(d, tid)
            n = sum(1 for q in d["decisions"] + d["decisions_resolved"] if q.get("task") == tid) + 1
            qid = f"{tid} Q{n}"
            q: dict[str, Any] = {
                "id": qid,
                "task": tid,
                "agent": agent,
                "title": i["title"],
                "text": text,
                "asked": util.now(),
                "kind": kind,
            }
            if action:
                q["action"] = action
            d["decisions"].append(q)
        event(self.p, tid, f"asks the owner ({kind}): {text}")
        return qid

    def answer(self, qid: str, text: str, by: str = "owner (panel)") -> dict[str, Any]:
        with self.edit() as d:
            q = next((q for q in d["decisions"] if q["id"] == qid), None)
            if q is None:
                raise BacklogError(f"no open question {qid}")
            d["decisions"].remove(q)
            q.update({"answer": text, "resolved": date.today().isoformat(), "by": f"{by}, {util.now()}", "delivered": False})
            q["answered_at"] = util.now()
            d["decisions_resolved"].append(q)
        event(self.p, q["task"], f"owner answered {qid}: {text}")
        return q

    def note(self, tid: str, text: str, source: str) -> None:
        with self.edit() as d:
            message(d, get(d, tid), "N", ("note",), kind="note", text="signal", answer=text, source=source)
        event(self.p, tid, f"note from {source}: {text}")

    def mark_delivered(self, qid: str) -> None:
        with self.edit() as d:
            for q in d["decisions_resolved"]:
                if q["id"] == qid:
                    q["delivered"] = True
