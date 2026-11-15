"""The open-PRs CI query answer of GitHub (GraphQL) as fm's own values."""

from typing import Any

from .. import util
from . import ProviderError

ROLLUP = {"SUCCESS": "pass", "FAILURE": "fail", "ERROR": "fail", "PENDING": "pending", "EXPECTED": "pending"}
FAILED_RUN = ("FAILURE", "TIMED_OUT", "STARTUP_FAILURE")  # a CheckRun's conclusion
FAILED_STATUS = ("FAILURE", "ERROR")  # a StatusContext's state


def down(o: dict[str, Any] | None, *keys: str) -> dict[str, Any]:
    """Down the objects `keys` of a GraphQL answer; {} where one is missing or null (a PR with no checks has no rollup)."""
    for k in keys:
        o = util.json_object((o or {}).get(k))
    return o or {}


def failed(rollup: dict[str, Any]) -> list[dict[str, str]]:
    """The failing checks of a status rollup as [{name, url}]: CheckRuns (GitHub Actions) and StatusContexts (others)."""
    out: list[dict[str, str]] = []
    for c in map(util.json_object, util.json_list(down(rollup, "contexts").get("nodes"))):
        if c and c.get("__typename") == "CheckRun" and c.get("conclusion") in FAILED_RUN:
            out.append({"name": str(c.get("name")), "url": str(c.get("detailsUrl") or "")})
        elif c and c.get("__typename") == "StatusContext" and c.get("state") in FAILED_STATUS:
            out.append({"name": str(c.get("context")), "url": str(c.get("targetUrl") or "")})
    return out


def pull(item: object) -> tuple[str, dict[str, Any]]:
    """One GraphQL pull request node as (number, {head, ci, failed}); ProviderError if it does not fit."""
    n = util.json_object(item)
    if not n or not isinstance(n.get("number"), int) or not isinstance(n.get("headRefOid"), str):
        raise ProviderError(f"gh api graphql: unexpected pull request {item!r}"[:300])
    last = util.json_object(next(iter(util.json_list(down(n, "commits").get("nodes"))), None))
    rollup = down(last, "commit", "statusCheckRollup")
    return str(n["number"]), {"head": n["headRefOid"], "ci": ROLLUP.get(str(rollup.get("state")), "none"), "failed": failed(rollup)}


def open_prs(raw: object) -> dict[str, dict[str, Any]]:
    """The answer of the open-PRs query as {number: {head, ci, failed}}; ProviderError if it does not fit."""
    return dict(pull(n) for n in util.json_list(down(util.json_object(raw), "data", "repository", "pullRequests").get("nodes")))
