"""The reads of the backlog on GitHub (DESIGN.md «Reads and writes per pass»): the open issues in one request (`first:50`),
or one issue by number; each typed at the border as `Issue`. A list over its cap is an error naming the count, never a
silent drop."""

from typing import Any

from ...core.project.backlog_table import Board
from ...core.tasks import board
from ...core.tasks.backlog import BacklogError
from ...core.tasks.issues import Issue, Linked
from .. import ProviderError
from .api import Api, nodes, obj, text, whole

OPEN_CAP = 50
CAPS = (("labels", "labels", 10), ("subIssues", "sub-issues", 30), ("blockedBy", "blockers", 20))
VALUE = "... on ProjectV2ItemFieldSingleSelectValue{name} ... on ProjectV2ItemFieldTextValue{text} ... on ProjectV2ItemFieldNumberValue{number}"
LINKED = "number title state stateReason createdAt closedAt"
NODE = (
    "number title body state stateReason createdAt closedAt labels(first:10){totalCount} parent{number} subIssues(first:30){totalCount}"
    f" blockedBy(first:20){{totalCount nodes{{{LINKED}}}}}"
    " projectItems(first:10,includeArchived:false){nodes{project{number owner{... on User{login} ... on Organization{login}}} "
    + " ".join(f'f{n}:fieldValueByName(name:"{f.name}"){{{VALUE}}}' for n, f in enumerate(board.FIELDS))
    + "}}"
)
OPEN = f"""query Open($owner:String!,$repo:String!){{repository(owner:$owner,name:$repo){{
 issues(states:OPEN,first:{OPEN_CAP},orderBy:{{field:CREATED_AT,direction:ASC}}){{totalCount pageInfo{{hasNextPage}} nodes{{{NODE}}}}}}}
 rateLimit{{cost remaining resetAt}}}}"""
ONE = f"""query Issue($owner:String!,$repo:String!,$number:Int!){{repository(owner:$owner,name:$repo){{
 issueOrPullRequest(number:$number){{__typename ... on Issue{{{NODE}}}}}}} rateLimit{{cost remaining resetAt}}}}"""


def _repo(api: Api, b: Board, query: str, **variables: object) -> dict[str, Any]:
    owner, name = b.repo.split("/")
    repo = api.query(query, owner=owner, repo=name, **variables).get("repository")
    if repo is None:
        raise ProviderError(f"no repo {b.repo}")
    return obj(repo, "repository")


def _linked(o: dict[str, Any]) -> Linked:
    w = "blocker"
    return Linked(whole(o, "number", w), text(o, "title", w), text(o, "state", w), o.get("stateReason") or "", text(o, "createdAt", w), o.get("closedAt") or "")


def _fields(card: dict[str, Any]) -> dict[str, str | int]:
    out: dict[str, str | int] = {}
    for n, f in enumerate(board.FIELDS):
        v = _value(card.get(f"f{n}"))
        if v is not None:
            out[f.name] = v
    return out


def _value(raw: object) -> str | int | None:
    """A card's field value: the option's name, the text, or the number (GitHub sends a float); None when unset."""
    if raw is None:
        return None
    o = obj(raw, "field value")
    for key in ("name", "text"):
        if isinstance(v := o.get(key), str):
            return v
    if isinstance(v := o.get("number"), int | float):
        return int(v)
    return None


def _card(raw: dict[str, Any], b: Board) -> dict[str, Any] | None:
    for item in nodes(raw, "projectItems", "issue"):
        project = obj(item.get("project"), "issue")
        owner = obj(project.get("owner"), "issue")
        if project.get("number") == b.number and str(owner.get("login", "")).lower() == b.owner.lower():
            return item
    return None


def issue(raw: dict[str, Any], b: Board) -> Issue:
    """GitHub's issue node as an `Issue`; a BacklogError when a nested list passes its cap."""
    w = "issue"
    number = whole(raw, "number", w)
    for key, what, cap in CAPS:
        if (n := whole(obj(raw.get(key), w), "totalCount", w)) > cap:
            raise BacklogError(f"#{number} has {n} {what}: fm reads at most {cap}")
    parent = raw.get("parent")
    card = _card(raw, b)
    return Issue(
        number,
        text(raw, "title", w),
        text(raw, "body", w),
        text(raw, "state", w),
        raw.get("stateReason") or "",
        text(raw, "createdAt", w),
        raw.get("closedAt") or "",
        None if parent is None else whole(obj(parent, w), "number", w),
        tuple(_linked(n) for n in nodes(raw, "blockedBy", w)),
        _fields(card) if card else {},
        card is not None,
    )


def read_open(api: Api, b: Board) -> list[Issue]:
    """The open issues that have a card on the Board, oldest first: one request."""
    repo = _repo(api, b, OPEN)
    found = obj(repo.get("issues"), "issues")
    n = whole(found, "totalCount", "issues")
    if n > OPEN_CAP or obj(found.get("pageInfo"), "issues").get("hasNextPage"):
        raise BacklogError(f"{b.repo} has {n} open issues: fm reads at most {OPEN_CAP}")
    return [i for i in (issue(raw, b) for raw in nodes(repo, "issues", "issue")) if i.carded]


def read_one(api: Api, b: Board, number: int) -> Issue | str:
    """Issue #number, or what it is instead: "pull request", or "absent" when the repo has no such number."""
    raw = _repo(api, b, ONE, number=number).get("issueOrPullRequest")
    if raw is None:
        return "absent"
    o = obj(raw, "issue")
    return issue(o, b) if o.get("__typename") == "Issue" else "pull request"
