"""`foreman backlog setup`: makes a Board fm's (DESIGN.md «The backlog on GitHub»): the 9 Status options, the other fields,
the `needs-owner` label, and no workflows. Safe to repeat: the Status options are replaced only when they differ, a field
is created only when missing and never edited, and a second run changes nothing."""

from ... import util
from ...core.project.backlog_table import Board
from ...core.tasks import board
from .. import ProviderError
from .api import Api, nodes, obj, text

SETUP = """query Setup($ro:String!,$rn:String!,$label:String!,$owner:String!,$number:Int!){
 repository(owner:$ro,name:$rn){id label(name:$label){id}}
 repositoryOwner(login:$owner){... on ProjectV2Owner{projectV2(number:$number){id
  fields(first:50){nodes{... on ProjectV2FieldCommon{id name dataType} ... on ProjectV2SingleSelectField{options{id name}}}}
  workflows(first:50){nodes{id name}}}}}
 rateLimit{cost remaining resetAt}}"""
UPDATE_STATUS = """mutation UpdateStatus($field:ID!,$options:[ProjectV2SingleSelectFieldOptionInput!]!){
 updateProjectV2Field(input:{fieldId:$field,singleSelectOptions:$options}){projectV2Field{... on ProjectV2SingleSelectField{id}}}}"""
CREATE_FIELD = """mutation CreateField($project:ID!,$name:String!,$type:ProjectV2CustomFieldType!,$options:[ProjectV2SingleSelectFieldOptionInput!]){
 createProjectV2Field(input:{projectId:$project,name:$name,dataType:$type,singleSelectOptions:$options}){projectV2Field{... on ProjectV2FieldCommon{id}}}}"""
DELETE_WORKFLOW = """mutation DeleteWorkflow($id:ID!){deleteProjectV2Workflow(input:{workflowId:$id}){deletedWorkflowId}}"""


def _options(names: tuple[str, ...]) -> list[dict[str, str]]:
    return [{"name": n, "color": "GRAY", "description": ""} for n in names]


def setup(b: Board, api: Api) -> list[str]:
    """Makes the Board and the repo fm's; one line per change made ([] = it already was)."""
    ro, rn = b.repo.split("/")
    d = api.query(SETUP, ro=ro, rn=rn, label=board.LABEL, owner=b.owner, number=b.number)
    if d.get("repository") is None:
        raise ProviderError(f"no repo {b.repo}")
    project = (util.json_object(d.get("repositoryOwner")) or {}).get("projectV2")
    if project is None:
        raise ProviderError(f"no Board {b.number} of {b.owner}")
    pr = obj(project, "Board")
    have = {text(f, "name", "field"): f for f in nodes(pr, "fields", "field")}
    for want in board.FIELDS:  # every refusal before any change
        if want.name in have and (kind := text(have[want.name], "dataType", "field")) != want.kind:
            raise ProviderError(f"field {want.name} is {kind}, fm needs {want.kind}")
    done: list[str] = []
    status = have["Status"]  # every Board has it
    if tuple(text(obj(o, "option"), "name", "option") for o in util.json_list(status.get("options"))) != board.STATUS:
        api.query(UPDATE_STATUS, field=text(status, "id", "field"), options=_options(board.STATUS))
        done.append("Status: options replaced")
    for f in board.FIELDS[1:]:
        if f.name not in have:
            api.query(CREATE_FIELD, project=text(pr, "id", "Board"), name=f.name, type=f.kind, options=_options(f.options) if f.options else None)
            done.append(f"field {f.name} created")
    if obj(d["repository"], "repo").get("label") is None:
        api.run("label", "create", board.LABEL, "-R", b.repo, "--color", "d93f0b", "--description", "an open question to the owner")
        done.append(f"label {board.LABEL} created")
    for w in nodes(pr, "workflows", "workflow"):
        api.query(DELETE_WORKFLOW, id=text(w, "id", "workflow"))
        done.append(f"workflow {text(w, 'name', 'workflow')} deleted")
    return done
