"""`foreman backlog setup`: makes a Board fm's (DESIGN.md «The backlog on GitHub»): the 9 Status options, the other fields,
the `needs-owner` label, and no workflows. Safe to repeat: the Status options are replaced only when they differ, a field
is created only when missing and never edited, and a second run changes nothing."""

from typing import Any

from ... import util
from ...core.project.backlog_table import Board
from ...core.tasks import board
from .. import ProviderError
from .api import Api, nodes, obj, text, whole

PAGE = 50  # fields and workflows read in one page
SETUP = """query Setup($ro:String!,$rn:String!,$label:String!,$owner:String!,$number:Int!){
 repository(owner:$ro,name:$rn){id label(name:$label){id}}
 repositoryOwner(login:$owner){... on ProjectV2Owner{projectV2(number:$number){id
  fields(first:50){totalCount nodes{... on ProjectV2FieldCommon{id name dataType} ... on ProjectV2SingleSelectField{options{id name}}}}
  workflows(first:50){totalCount nodes{id name}}}}}
 rateLimit{cost remaining resetAt}}"""
UPDATE_STATUS = """mutation UpdateStatus($field:ID!,$options:[ProjectV2SingleSelectFieldOptionInput!]!){
 updateProjectV2Field(input:{fieldId:$field,singleSelectOptions:$options}){projectV2Field{... on ProjectV2SingleSelectField{id}}}}"""
CREATE_FIELD = """mutation CreateField($project:ID!,$name:String!,$type:ProjectV2CustomFieldType!,$options:[ProjectV2SingleSelectFieldOptionInput!]){
 createProjectV2Field(input:{projectId:$project,name:$name,dataType:$type,singleSelectOptions:$options}){projectV2Field{... on ProjectV2FieldCommon{id}}}}"""
DELETE_WORKFLOW = """mutation DeleteWorkflow($id:ID!){deleteProjectV2Workflow(input:{workflowId:$id}){deletedWorkflowId}}"""


def _options(names: tuple[str, ...]) -> list[dict[str, str]]:
    return [{"name": n, "color": "GRAY", "description": ""} for n in names]


def _board(b: Board, api: Api) -> tuple[bool, dict[str, Any], dict[str, dict[str, Any]]]:
    """(the repo has the label, the Board, its fields by name), after every refusal: nothing is changed before them."""
    ro, rn = b.repo.split("/")
    d = api.query(SETUP, ro=ro, rn=rn, label=board.LABEL, owner=b.owner, number=b.number)
    if d.get("repository") is None:
        raise ProviderError(f"no repo {b.repo}")
    project = (util.json_object(d.get("repositoryOwner")) or {}).get("projectV2")
    if project is None:
        raise ProviderError(f"no Board {b.number} of {b.owner}")
    pr = obj(project, "Board")
    for key in ("fields", "workflows"):
        if (n := whole(obj(pr.get(key), "Board"), "totalCount", "Board")) > PAGE:
            raise ProviderError(f"Board {b.number} of {b.owner} has {n} {key}: fm reads at most {PAGE}")
    have = {text(f, "name", "field"): f for f in nodes(pr, "fields", "field")}
    if "Status" not in have:
        raise ProviderError(f"Board {b.number} of {b.owner} has no Status field")
    for want in board.FIELDS:
        if want.name in have and (kind := text(have[want.name], "dataType", "field")) != want.kind:
            raise ProviderError(f"field {want.name} is {kind}, fm needs {want.kind}")
    return obj(d["repository"], "repo").get("label") is not None, pr, have


def setup(b: Board, api: Api) -> list[str]:
    """Makes the Board and the repo fm's; one line per change made ([] = it already was)."""
    labelled, pr, have = _board(b, api)
    done: list[str] = []
    status = have["Status"]
    if tuple(text(obj(o, "option"), "name", "option") for o in util.json_list(status.get("options"))) != board.STATUS:
        api.query(UPDATE_STATUS, field=text(status, "id", "field"), options=_options(board.STATUS))
        done.append("Status: options replaced")
    for f in board.FIELDS[1:]:
        if f.name not in have:
            api.query(CREATE_FIELD, project=text(pr, "id", "Board"), name=f.name, type=f.kind, options=_options(f.options) if f.options else None)
            done.append(f"field {f.name} created")
    if not labelled:
        api.run("label", "create", board.LABEL, "-R", b.repo, "--color", "d93f0b", "--description", "an open question to the owner")
        done.append(f"label {board.LABEL} created")
    for w in nodes(pr, "workflows", "workflow"):
        api.query(DELETE_WORKFLOW, id=text(w, "id", "workflow"))
        done.append(f"workflow {text(w, 'name', 'workflow')} deleted")
    return done
