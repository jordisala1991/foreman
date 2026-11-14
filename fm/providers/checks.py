"""The open-PRs CI query answer of GitHub (GraphQL) as fm's own values."""

from typing import Any

from .. import util
from . import ProviderError

ROLLUP = {"SUCCESS": "pass", "FAILURE": "fail", "ERROR": "fail", "PENDING": "pending", "EXPECTED": "pending"}


def down(o: dict[str, Any] | None, *keys: str) -> dict[str, Any]:
    """Down the objects `keys` of a GraphQL answer; {} where one is missing or null (a PR with no checks has no rollup)."""
    for k in keys:
        o = util.json_object((o or {}).get(k))
    return o or {}


def pull(item: object) -> tuple[str, dict[str, str]]:
    """One GraphQL pull request node as (number, {head, ci}); ProviderError if it does not fit."""
    n = util.json_object(item)
    if not n or not isinstance(n.get("number"), int) or not isinstance(n.get("headRefOid"), str):
        raise ProviderError(f"gh api graphql: unexpected pull request {item!r}"[:300])
    last = util.json_object(next(iter(util.json_list(down(n, "commits").get("nodes"))), None))
    state = down(last, "commit", "statusCheckRollup").get("state")
    return str(n["number"]), {"head": n["headRefOid"], "ci": ROLLUP.get(str(state), "none")}


def open_prs(raw: object) -> dict[str, dict[str, str]]:
    """The answer of the open-PRs query as {number: {head, ci}}; ProviderError if it does not fit."""
    return dict(pull(n) for n in util.json_list(down(util.json_object(raw), "data", "repository", "pullRequests").get("nodes")))
