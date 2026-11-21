"""A stand-in for `proc.run` that answers `gh` as GitHub does for the backlog on GitHub (providers/github/api, board, issues,
writes): a Board (fields, Status options, workflows), the repo's labels, its issues and pull requests, each write changing
them, counters of requests, reads and mutations, field writes it drops, the closes it saw, and a switchable rate limit.
Other programs pass through to the real `proc.run`. Install it with `monkeypatch.setattr(proc, "run", fake)`; `board_env()`
is a project whose foreman.toml has `[backlog]`."""

import json
import re
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from helpers import Env

from foreman import proc
from foreman.core.project.config import Project
from foreman.core.tasks import board
from foreman.providers import registry

REAL_RUN = proc.run
FIELD_KIND = {f.name: f.kind for f in board.FIELDS}


def board_env() -> Env:
    """A project (helpers.Env, prefix TY) whose backlog is repo o/r on Board 1 of o."""
    e = Env()
    with (e.home / "foreman.toml").open("a") as f:
        f.write('[backlog]\nkind = "github"\nrepo = "o/r"\nproject = 1\n')
    e.p = Project.load(e.home)
    return e


def passed(e: Env) -> None:
    """The loop's pass: one read of the Board, which saves the snapshot the panel and `watch` show."""
    with registry.tick_scope(e.p):
        registry.backlog(e.p).load()


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
    labels: list[str] = field(default_factory=list[str])
    labels_total: int | None = None  # labels.totalCount, when it is not len(labels)
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
        self.field_total: int | None = None  # fields.totalCount, when it is not the number of fields
        self.workflow_total: int | None = None
        self.limited = False
        self.reset = int(time.time()) + 3600
        self.reads = 0  # Open and Issue requests
        self.mutations = 0
        self.graphql = 0  # every GraphQL request
        self.drop: set[str] = set()  # field names whose writes are silently ignored
        self.closes: list[tuple[int, str, str]] = []  # (number, reason, the Status written in the same request)
        self.hook: Callable[[str], None] = lambda op: None  # called with each request's name before it runs
        self.calls: list[list[str]] = []  # every gh argv
        self.query = ""  # the request being answered

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
        if a[:2] == ["api", "graphql"]:
            self.graphql += 1
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
        self.hook(m[1])
        self.query = req["query"]
        out: dict[str, Any] = getattr(self, f"_{m[1]}")(req["variables"])
        return proc.Result(1 if "errors" in out else 0, json.dumps(out), "gh: errors\n" if "errors" in out else "")

    # --- the operations, by name ---

    def _Setup(self, v: dict[str, Any]) -> dict[str, Any]:
        repo = {"id": "R", "label": {"id": "L"} if v["label"] in self.labels else None}
        fields = {"totalCount": self.field_total or len(self.fields), "nodes": list(self.fields.values())}
        workflows = {"totalCount": self.workflow_total or len(self.workflows), "nodes": list(self.workflows)}
        project = {"id": "P", "fields": fields, "workflows": workflows}
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
        repo = {"id": "R", "label": {"id": "L"} if board.LABEL in self.labels else None, "issues": issues}
        project = {"id": "P", "fields": {"nodes": list(self.fields.values())}}
        return {"data": {"repository": repo, "repositoryOwner": {"projectV2": project}, "rateLimit": {"cost": 2}}}

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
        card: dict[str, Any] = {"id": f"IT{s.number}", "project": {"number": 1, "owner": {"login": "o"}}}
        for i, f in enumerate(board.FIELDS):
            val = s.fields.get(f.name)
            kind = FIELD_KIND[f.name]
            card[f"f{i}"] = None if val is None else {"name": val} if kind == "SINGLE_SELECT" else {"text": val} if kind == "TEXT" else {"number": float(val)}
        other = {"id": "IT-other", "project": {"number": 7, "owner": {"login": "o"}}, **{f"f{i}": None for i in range(len(board.FIELDS))}}
        return {
            "number": s.number,
            "title": s.title,
            "body": s.body,
            "state": s.state,
            "stateReason": s.reason,
            "createdAt": s.created,
            "closedAt": s.closed,
            "id": f"I{s.number}",
            "labels": {"totalCount": len(s.labels) if s.labels_total is None else s.labels_total, "nodes": [{"name": n} for n in s.labels]},
            "parent": None if s.parent is None else {"number": s.parent},
            "subIssues": {"totalCount": s.subs},
            "blockedBy": {"totalCount": len(s.blockers) if s.blocked_total is None else s.blocked_total, "nodes": [self.linked(n) for n in s.blockers]},
            "projectItems": {"nodes": [other, card] if s.carded else [other]},
        }

    # --- the writes (providers/github/writes.py), each changing a Spec ---

    def _spec(self, node: str) -> Spec:
        return self.issues[int(node.removeprefix("IT").removeprefix("I"))]

    def _File(self, v: dict[str, Any]) -> dict[str, Any]:
        self.mutations += 1
        n = max([*self.issues, *self.prs], default=0) + 1
        self.add(n, title=v["title"], body=v["body"], carded=False, created=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
        return {"data": {"createIssue": {"issue": {"id": f"I{n}", "number": n}}}}

    def _Card(self, v: dict[str, Any]) -> dict[str, Any]:
        self.mutations += 1
        self._spec(v["issue"]).carded = True
        self._link(v)
        return {"data": {"addProjectV2ItemById": {"item": {"id": v["issue"].replace("I", "IT", 1)}}}}

    def _Body(self, v: dict[str, Any]) -> dict[str, Any]:
        self.mutations += 1
        s = self._spec(v["id"])
        s.title, s.body = v["title"], v["body"]
        return {"data": {"updateIssue": {"clientMutationId": None}}}

    def _Fields(self, v: dict[str, Any]) -> dict[str, Any]:
        self.mutations += 1
        s = self._spec(v["item"])
        by_id = {f["id"]: f for f in self.fields.values()}
        for _, fid, key, val in re.findall(
            r"(\w+):updateProjectV2ItemFieldValue\(input:\{projectId:\$project,itemId:\$item,fieldId:\$(\w+),value:\{(\w+):\$(\w+)\}", self.query
        ):
            f, value = by_id[v[fid]], v[val]
            if f["name"] not in self.drop:
                s.fields[f["name"]] = (
                    next(o["name"] for o in f["options"] if o["id"] == value) if key == "singleSelectOptionId" else int(value) if key == "number" else value
                )
        for _, fid in re.findall(r"(\w+):clearProjectV2ItemFieldValue\(input:\{projectId:\$project,itemId:\$item,fieldId:\$(\w+)\}", self.query):
            if (name := by_id[v[fid]]["name"]) not in self.drop:
                s.fields.pop(name, None)
        if "closeIssue" in self.query:
            s.state, s.reason, s.closed = "CLOSED", v["reason"], time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            self.closes.append((s.number, v["reason"], str(s.fields.get("Status", ""))))
        return {"data": {}}

    def _Links(self, v: dict[str, Any]) -> dict[str, Any]:
        self.mutations += 1
        self._link(v)
        return {"data": {}}

    def _link(self, v: dict[str, Any]) -> None:
        """The parent and blocker mutations of the request (Links, or a Card's)."""
        s = self._spec(v["issue"])
        if "addSubIssue" in self.query:
            s.parent = self._spec(v["parent"]).number
        for op, name in re.findall(r"\w+:(addBlockedBy|removeBlockedBy)\(input:\{issueId:\$issue,blockingIssueId:\$(\w+)\}", self.query):
            n = self._spec(v[name]).number
            s.blockers = [*s.blockers, n] if op == "addBlockedBy" else [b for b in s.blockers if b != n]

    def _Label(self, v: dict[str, Any]) -> dict[str, Any]:
        self.mutations += 1
        s = self._spec(v["id"])
        s.labels = [*s.labels, board.LABEL]
        return {"data": {}}

    def _Unlabel(self, v: dict[str, Any]) -> dict[str, Any]:
        self.mutations += 1
        s = self._spec(v["id"])
        s.labels = [x for x in s.labels if x != board.LABEL]
        return {"data": {}}

    def _Nodes(self, v: dict[str, Any]) -> dict[str, Any]:
        self.reads += 1
        found = {f"n{n}": {"id": f"I{n}"} if int(n) in self.issues else None for n in re.findall(r"n(\d+):issue", self.query)}
        return {"data": {"repository": found}}
