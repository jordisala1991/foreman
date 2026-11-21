"""The writes of the backlog on GitHub (DESIGN.md «Reads and writes per pass»): one GraphQL request per step, named after
it, with the ids the pass's read found (`Meta`). Several field values, links or a close travel aliased in one request.
Nothing here reads back, waits or tries again: `apply` reads back, the `Api` stops calls over a rate limit."""

from collections.abc import Mapping
from dataclasses import dataclass, field

from ...core.project.backlog_table import Board
from ...core.tasks import board
from ...core.tasks.backlog import BacklogError
from .api import Api, obj, text, whole
from .issues import Meta, query

KIND = {f.name: f.kind for f in board.FIELDS}
VALUE = {"SINGLE_SELECT": ("singleSelectOptionId", "String!"), "TEXT": ("text", "String!"), "NUMBER": ("number", "Float!")}
CREATE = """mutation File($repo:ID!,$title:String!,$body:String!){
 createIssue(input:{repositoryId:$repo,title:$title,body:$body}){issue{id number}}}"""
BODY = """mutation Body($id:ID!,$title:String!,$body:String!){updateIssue(input:{id:$id,title:$title,body:$body}){clientMutationId}}"""
LABEL = """mutation Label($id:ID!,$label:ID!){addLabelsToLabelable(input:{labelableId:$id,labelIds:[$label]}){clientMutationId}}"""
UNLABEL = """mutation Unlabel($id:ID!,$label:ID!){removeLabelsFromLabelable(input:{labelableId:$id,labelIds:[$label]}){clientMutationId}}"""


@dataclass(frozen=True)
class Target:
    """Where a write goes: the repo and Board, and their ids."""

    api: Api
    b: Board
    meta: Meta


@dataclass(frozen=True)
class Links:
    """Issue nodes: the parent to set ("" for none) and the blockers to add and to remove."""

    parent: str = ""
    add: list[str] = field(default_factory=list[str])
    remove: list[str] = field(default_factory=list[str])


def _missing(what: str) -> BacklogError:
    return BacklogError(f"the Board has no {what}: run foreman backlog setup")


def create(t: Target, title: str, body: str) -> tuple[str, int]:
    """Files the issue: its node id and number."""
    o = obj(obj(t.api.query(CREATE, repo=t.meta.repo, title=title, body=body).get("createIssue"), "createIssue").get("issue"), "issue")
    return text(o, "id", "issue"), whole(o, "number", "issue")


def card(t: Target, issue: str, to: Links) -> str:
    """Adds the issue to the Board, and its parent and blockers, in one request: its card's id."""
    defs, parts, v = _links(to)
    q = f"mutation Card($project:ID!,$issue:ID!{''.join(',' + d for d in defs)}){{"
    q += "addProjectV2ItemById(input:{projectId:$project,contentId:$issue}){item{id}} " + " ".join(parts) + "}"
    o = obj(t.api.query(q, project=t.meta.project, issue=issue, **v).get("addProjectV2ItemById"), "card")
    return text(obj(o.get("item"), "card"), "id", "card")


def body(t: Target, issue: str, title: str, text_: str) -> None:
    t.api.query(BODY, id=issue, title=title, body=text_)


def fields(t: Target, issue: str, item: str, values: Mapping[str, str | int | None], close: str = "") -> None:
    """Sets each field (None clears it) in one request; `close` (COMPLETED | NOT_PLANNED | DUPLICATE) closes the issue in it."""
    defs, parts = ["$project:ID!", "$item:ID!"], list[str]()
    v: dict[str, object] = {"project": t.meta.project, "item": item}
    on = "projectId:$project,itemId:$item"
    for n, (name, value) in enumerate(values.items()):
        if name not in t.meta.fields:
            raise _missing(f"field {name}")
        defs.append(f"$f{n}:ID!")
        v[f"f{n}"] = t.meta.fields[name]
        if value is None:
            parts.append(f"f{n}:clearProjectV2ItemFieldValue(input:{{{on},fieldId:$f{n}}}){{clientMutationId}}")
            continue
        key, kind = VALUE[KIND[name]]
        if key == "singleSelectOptionId":
            if str(value) not in (options := t.meta.options.get(name, {})):
                raise _missing(f"option {value} of {name}")
            value = options[str(value)]
        defs.append(f"$v{n}:{kind}")
        v[f"v{n}"] = value
        parts.append(f"f{n}:updateProjectV2ItemFieldValue(input:{{{on},fieldId:$f{n},value:{{{key}:$v{n}}}}}){{clientMutationId}}")
    if close:
        defs += ["$issue:ID!", "$reason:IssueClosedStateReason!"]
        v |= {"issue": issue, "reason": close}
        parts.append("close:closeIssue(input:{issueId:$issue,stateReason:$reason}){clientMutationId}")
    t.api.query(f"mutation Fields({','.join(defs)}){{{' '.join(parts)}}}", **v)


def _links(to: Links) -> tuple[list[str], list[str], dict[str, object]]:
    """The variables, mutations and values that make `$issue` the parent's sub-issue and change its blockers."""
    defs, parts, v = list[str](), list[str](), dict[str, object]()
    if to.parent:
        defs.append("$parent:ID!")
        v["parent"] = to.parent
        parts.append("parent:addSubIssue(input:{issueId:$parent,subIssueId:$issue,replaceParent:true}){clientMutationId}")
    for op, ids in (("addBlockedBy", to.add), ("removeBlockedBy", to.remove)):
        for n, node in enumerate(ids):
            name = f"{op[:3]}{n}"
            defs.append(f"${name}:ID!")
            v[name] = node
            parts.append(f"{name}:{op}(input:{{issueId:$issue,blockingIssueId:${name}}}){{clientMutationId}}")
    return defs, parts, v


def links(t: Target, issue: str, to: Links) -> None:
    """In one request: the issue becomes the parent's sub-issue (unless none), and its blockers change."""
    defs, parts, v = _links(to)
    t.api.query(f"mutation Links($issue:ID!{''.join(',' + d for d in defs)}){{{' '.join(parts)}}}", issue=issue, **v)


def label(t: Target, issue: str, on: bool) -> None:
    """Puts `needs-owner` on the issue, or takes it off."""
    if not t.meta.label:
        raise BacklogError(f"the repo has no label {board.LABEL}: run foreman backlog setup")
    t.api.query(LABEL if on else UNLABEL, id=issue, label=t.meta.label)


def nodes(t: Target, numbers: list[int]) -> dict[int, str]:
    """The node ids of issues by number, in one request; a BacklogError naming a number that is no issue."""
    q = "query Nodes($owner:String!,$repo:String!){repository(owner:$owner,name:$repo){" + " ".join(f"n{n}:issue(number:{n}){{id}}" for n in numbers) + "}}"
    repo = obj(query(t.api, t.b, q)["repository"], "repository")
    out: dict[int, str] = {}
    for n in numbers:
        if repo.get(f"n{n}") is None:
            raise BacklogError(f"there is no issue #{n}")
        out[n] = text(obj(repo[f"n{n}"], "issue"), "id", "issue")
    return out
