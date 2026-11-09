"""GitHub review threads as fm sees them: the GraphQL calls and the typed border. A thread is fm's when its first comment
carries the `<!-- fm:finding-KEY -->` marker; it is settled when resolved or when fm already replied «fixed» in it."""

import json
import re

from .. import util
from ..inline import Thread
from . import ProviderError

READ = (
    "query($owner:String!,$repo:String!,$number:Int!,$endCursor:String){repository(owner:$owner,name:$repo)"
    "{pullRequest(number:$number){reviewThreads(first:100,after:$endCursor){pageInfo{hasNextPage endCursor} "
    "nodes{id isResolved comments(first:50){nodes{body}}}}}}}"
)
JQ = ".data.repository.pullRequest.reviewThreads.nodes[] | {id, resolved: .isResolved, bodies: [.comments.nodes[].body]} | @json"
RESOLVE = "mutation($id:ID!){resolveReviewThread(input:{threadId:$id}){thread{id}}}"
REPLY = "mutation($id:ID!,$body:String!){addPullRequestReviewThreadReply(input:{pullRequestReviewThreadId:$id,body:$body}){comment{id}}}"
_KEY = re.compile(r"<!-- fm:(finding-[a-z0-9]+) -->")


def fixed_text(key: str, sha: str) -> str:
    return f"Fixed in {sha[:9]}.\n\n<!-- fm:fixed-{key} -->"


def _thread(line: str) -> tuple[str, bool, list[str]]:
    """One output line as (id, resolved, comment bodies); ProviderError on a misfit."""
    try:
        o = util.json_object(json.loads(line))
    except ValueError:
        raise ProviderError(f"gh api graphql: not JSON: {line[:200]!r}") from None
    bodies = util.json_list(o.get("bodies")) if o else []
    if o is None or not isinstance(o.get("id"), str) or not isinstance(o.get("resolved"), bool) or not all(isinstance(b, str) for b in bodies):
        raise ProviderError(f"gh api graphql: unexpected thread {line[:200]!r}")
    return str(o["id"]), bool(o["resolved"]), [str(b) for b in bodies]


def parse(out: str) -> list[Thread]:
    """The fm threads in `gh api graphql --jq ... | @json` output (one JSON object per line); ProviderError on a misfit."""
    found: list[Thread] = []
    for line in out.splitlines():
        if not line.strip():
            continue
        tid, resolved, bodies = _thread(line)
        m = _KEY.search(bodies[0]) if bodies else None
        if m:
            key = m.group(1)
            found.append(Thread(tid, key, resolved or any(f"<!-- fm:fixed-{key} -->" in b for b in bodies)))
    return found
