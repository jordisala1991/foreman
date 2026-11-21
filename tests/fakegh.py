"""A stand-in for `proc.run` that answers `gh` as GitHub does for the backlog on GitHub (providers/github/api, board, issues):
a Board (fields, Status options, workflows), the repo's labels, its issues and pull requests, counters of reads and
mutations, and a switchable rate limit. Other programs pass through to the real `proc.run`. Install it with
`monkeypatch.setattr(proc, "run", fake)`; `board_env()` is a project whose foreman.toml has `[backlog]`."""

import json
import re
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from helpers import Env

from foreman import proc
from foreman.core.project.config import Project
from foreman.core.tasks import board

REAL_RUN = proc.run
FIELD_KIND = {f.name: f.kind for f in board.FIELDS}


def board_env() -> Env:
    """A project (helpers.Env, prefix TY) whose backlog is repo o/r on Board 1 of o."""
    e = Env()
    with (e.home / "foreman.toml").open("a") as f:
        f.write('[backlog]\nkind = "github"\nrepo = "o/r"\nproject = 1\n')
    e.p = Project.load(e.home)
    return e


@dataclass
class Spec:
    """An issue of the fake repo, as the test sets it."""

    number: int
    title: str = "t"
    body: str = ""
    state: str = "OPEN"
    reason: str | None = None
    created: str = "2026-11-20T08:00:00Z"
    closed: str | None = None
    parent: int | None = None
    blockers: list[int] = field(default_factory=list[int])
    fields: dict[str, str | int] = field(default_factory=dict[str, str | int])
    carded: bool = True
    labels: int = 0
    subs: int = 0
    blocked_total: int | None = None  # blockedBy.totalCount, when it is not len(blockers)


class FakeGh:
    def __init__(self) -> None:
        self.fields: dict[str, dict[str, Any]] = {
            "Title": {"id": "F-title", "name": "Title", "dataType": "TITLE"},
            "Status": {"id": "F-status", "name": "Status", "dataType": "SINGLE_SELECT", "options": self._opts(("Todo", "In Progress", "Done"))},
        }
        self.workflows = [{"id": f"W{n}", "name": name} for n, name in enumerate(("Item closed", "Pull request merged", "Auto-close issue"))]
        self.labels: set[str] = set()
        self.issues: dict[int, Spec] = {}
        self.prs: set[int] = set()
        self.total: int | None = None  # issues.totalCount, when it is not the open count
        self.next_page = False
        self.limited = False
        self.reset = int(time.time()) + 3600
        self.reads = 0  # Open and Issue requests
        self.mutations = 0
        self.calls: list[list[str]] = []  # every gh argv

    @staticmethod
    def _opts(names: Sequence[str]) -> list[dict[str, str]]:
        return [{"id": f"O-{n}", "name": n} for n in names]

    def add(self, number: int, **kw: Any) -> Spec:
        s = Spec(number, **kw)
        self.issues[number] = s
        return s

    def __call__(
        self, args: Sequence[str], *, cwd: Path | None = None, timeout: float = 60, env: Mapping[str, str] | None = None, input: str | None = None
    ) -> proc.Result:
        if args[:1] != ["gh"]:
            return REAL_RUN(args, cwd=cwd, timeout=timeout, env=env, input=input)
        a = list(args[1:])
        self.calls.append(a)
        if a[:2] == ["api", "rate_limit"]:
            return proc.Result(0, f"{self.reset}\n", "")
        if self.limited:
            body = {"errors": [{"type": "RATE_LIMITED", "message": "API rate limit already exceeded for user ID 1."}]}
            return proc.Result(1, json.dumps(body), "gh: API rate limit already exceeded for user ID 1.\n")
        if a[:2] == ["label", "create"]:
            self.labels.add(a[2])
            return proc.Result(0, "", "")
        assert a == ["api", "graphql", "--input", "-"], a
        req = json.loads(input or "{}")
        m = re.match(r"\s*(?:query|mutation) (\w+)", req["query"])
        assert m, req["query"]
        out: dict[str, Any] = getattr(self, f"_{m[1]}")(req["variables"])
        return proc.Result(1 if "errors" in out else 0, json.dumps(out), "gh: errors\n" if "errors" in out else "")

    # --- the operations, by name ---

    def _Setup(self, v: dict[str, Any]) -> dict[str, Any]:
        repo = {"id": "R", "label": {"id": "L"} if v["label"] in self.labels else None}
        project = {"id": "P", "fields": {"nodes": list(self.fields.values())}, "workflows": {"nodes": list(self.workflows)}}
        return {"data": {"repository": repo, "repositoryOwner": {"projectV2": project}, "rateLimit": {"cost": 1}}}

    def _UpdateStatus(self, v: dict[str, Any]) -> dict[str, Any]:
        self.mutations += 1
        status = next(f for f in self.fields.values() if f["id"] == v["field"])
        status["options"] = self._opts([o["name"] for o in v["options"]])
        return {"data": {"updateProjectV2Field": {"projectV2Field": {"id": v["field"]}}}}

    def _CreateField(self, v: dict[str, Any]) -> dict[str, Any]:
        self.mutations += 1
        f: dict[str, Any] = {"id": f"F-{v['name']}", "name": v["name"], "dataType": v["type"]}
        if v["options"] is not None:
            f["options"] = self._opts([o["name"] for o in v["options"]])
        self.fields[v["name"]] = f
        return {"data": {"createProjectV2Field": {"projectV2Field": {"id": f["id"]}}}}

    def _DeleteWorkflow(self, v: dict[str, Any]) -> dict[str, Any]:
        self.mutations += 1
        self.workflows = [w for w in self.workflows if w["id"] != v["id"]]
        return {"data": {"deleteProjectV2Workflow": {"deletedWorkflowId": v["id"]}}}

    def _Open(self, v: dict[str, Any]) -> dict[str, Any]:
        self.reads += 1
        found = [s for s in self.issues.values() if s.state == "OPEN"]
        issues = {
            "totalCount": len(found) if self.total is None else self.total,
            "pageInfo": {"hasNextPage": self.next_page},
            "nodes": [self.node(s) for s in found],
        }
        return {"data": {"repository": {"issues": issues}, "rateLimit": {"cost": 2}}}

    def _Issue(self, v: dict[str, Any]) -> dict[str, Any]:
        self.reads += 1
        n = v["number"]
        if n in self.prs:
            return {"data": {"repository": {"issueOrPullRequest": {"__typename": "PullRequest"}}}}
        if n not in self.issues:
            return {"data": {"repository": {"issueOrPullRequest": None}}, "errors": [{"type": "NOT_FOUND", "message": f"no issue {n}"}]}
        return {"data": {"repository": {"issueOrPullRequest": {"__typename": "Issue", **self.node(self.issues[n])}}}}

    # --- an issue as GitHub answers it ---

    def linked(self, n: int) -> dict[str, Any]:
        s = self.issues[n]
        return {"number": n, "title": s.title, "state": s.state, "stateReason": s.reason, "createdAt": s.created, "closedAt": s.closed}

    def node(self, s: Spec) -> dict[str, Any]:
        card: dict[str, Any] = {"project": {"number": 1, "owner": {"login": "o"}}}
        for i, f in enumerate(board.FIELDS):
            val = s.fields.get(f.name)
            kind = FIELD_KIND[f.name]
            card[f"f{i}"] = None if val is None else {"name": val} if kind == "SINGLE_SELECT" else {"text": val} if kind == "TEXT" else {"number": float(val)}
        other = {"project": {"number": 7, "owner": {"login": "o"}}, **{f"f{i}": None for i in range(len(board.FIELDS))}}
        return {
            "number": s.number,
            "title": s.title,
            "body": s.body,
            "state": s.state,
            "stateReason": s.reason,
            "createdAt": s.created,
            "closedAt": s.closed,
            "labels": {"totalCount": s.labels},
            "parent": None if s.parent is None else {"number": s.parent},
            "subIssues": {"totalCount": s.subs},
            "blockedBy": {"totalCount": len(s.blockers) if s.blocked_total is None else s.blocked_total, "nodes": [self.linked(n) for n in s.blockers]},
            "projectItems": {"nodes": [other, card] if s.carded else [other]},
        }
