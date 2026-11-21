"""The reads of the backlog on GitHub (DESIGN.md «Reads and writes per pass»): the open issues in one request (`first:50`),
with the ids a write needs (`Meta`), or one issue by number; each typed at the border as `Issue`. A list over its cap is an
error naming the count, never a silent drop."""

from dataclasses import dataclass
from typing import Any

from ... import util
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
    "id number title body state stateReason createdAt closedAt labels(first:10){totalCount nodes{name}} parent{number}"
    f" subIssues(first:30){{totalCount}} blockedBy(first:20){{totalCount nodes{{{LINKED}}}}}"
    " projectItems(first:10,includeArchived:false){nodes{id project{number owner{... on User{login} ... on Organization{login}}} "
    + " ".join(f'f{n}:fieldValueByName(name:"{f.name}"){{{VALUE}}}' for n, f in enumerate(board.FIELDS))
    + "}}"
)
OPEN = f"""query Open($owner:String!,$repo:String!,$label:String!,$board:String!,$number:Int!){{repository(owner:$owner,name:$repo){{
 id label(name:$label){{id}}
 issues(states:OPEN,first:{OPEN_CAP},orderBy:{{field:CREATED_AT,direction:ASC}}){{totalCount pageInfo{{hasNextPage}} nodes{{{NODE}}}}}}}
 repositoryOwner(login:$board){{... on ProjectV2Owner{{projectV2(number:$number){{id
  fields(first:50){{nodes{{... on ProjectV2FieldCommon{{id name}} ... on ProjectV2SingleSelectField{{options{{id name}}}}}}}}}}}}}}
 rateLimit{{cost remaining resetAt}}}}"""
ONE = f"""query Issue($owner:String!,$repo:String!,$number:Int!){{repository(owner:$owner,name:$repo){{
 issueOrPullRequest(number:$number){{__typename ... on Issue{{{NODE}}}}}}} rateLimit{{cost remaining resetAt}}}}"""


@dataclass(frozen=True)
class Meta:
    """The ids a write needs, found by name with the pass's read (never in foreman.toml)."""

    repo: str  # the repo's node id
    project: str  # the Board's
    fields: dict[str, str]  # field name → id
    options: dict[str, dict[str, str]]  # field name → option name → id
    label: str  # `needs-owner`'s id; "" when the repo has none


@dataclass(frozen=True)
class Read:
    """The open issues on the Board, and the ids to write them."""

    issues: tuple[Issue, ...]
    meta: Meta


def query(api: Api, b: Board, q: str, **variables: object) -> dict[str, Any]:
    """The answer to a query on the Board's repo (`$owner`, `$repo`); a ProviderError when there is no such repo."""
    owner, name = b.repo.split("/")
    d = api.query(q, owner=owner, repo=name, **variables)
    if d.get("repository") is None:
        raise ProviderError(f"no repo {b.repo}")
    return d


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
        text(raw, "id", w),
        text(card, "id", w) if card else "",
        tuple(text(n, "name", "label") for n in nodes(raw, "labels", w)),
    )


def _meta(d: dict[str, Any], b: Board) -> Meta:
    repo = obj(d["repository"], "repository")
    project = (util.json_object(d.get("repositoryOwner")) or {}).get("projectV2")
    if project is None:
        raise ProviderError(f"no Board {b.number} of {b.owner}")
    pr = obj(project, "Board")
    fields = {text(f, "name", "field"): f for f in nodes(pr, "fields", "field")}
    options = {
        name: {text(o, "name", "option"): text(o, "id", "option") for o in (obj(x, "option") for x in util.json_list(f["options"]))}
        for name, f in fields.items()
        if "options" in f
    }
    label = util.json_object(repo.get("label")) or {}
    return Meta(text(repo, "id", "repo"), text(pr, "id", "Board"), {n: text(f, "id", "field") for n, f in fields.items()}, options, str(label.get("id", "")))


def read_open(api: Api, b: Board) -> Read:
    """The open issues that have a card on the Board, oldest first, and the ids to write them: one request."""
    d = query(api, b, OPEN, label=board.LABEL, board=b.owner, number=b.number)
    repo = obj(d["repository"], "repository")
    found = obj(repo.get("issues"), "issues")
    n = whole(found, "totalCount", "issues")
    if n > OPEN_CAP or obj(found.get("pageInfo"), "issues").get("hasNextPage"):
        raise BacklogError(f"{b.repo} has {n} open issues: fm reads at most {OPEN_CAP}")
    return Read(tuple(i for i in (issue(raw, b) for raw in nodes(repo, "issues", "issue")) if i.carded), _meta(d, b))


def read_one(api: Api, b: Board, number: int) -> Issue | str:
    """Issue #number, or what it is instead: "pull request", or "absent" when the repo has no such number."""
    raw = obj(query(api, b, ONE, number=number)["repository"], "repository").get("issueOrPullRequest")
    if raw is None:
        return "absent"
    o = obj(raw, "issue")
    return issue(o, b) if o.get("__typename") == "Issue" else "pull request"
