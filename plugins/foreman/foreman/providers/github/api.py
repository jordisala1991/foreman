"""`gh` for the GitHub adapter, the one place it runs: the backlog's GraphQL requests and the provider's commands, and
the rate limit's stop. When GitHub says the limit is spent, the reset is kept in the stop file and every call until then
raises `RateLimited` without running gh: each foreman process obeys it, and nothing here waits or tries again (retrying
is `loop.guarded`'s)."""

import json
import time
from pathlib import Path
from typing import Any

from ... import proc, util
from .. import ProviderError
from .types import PermanentError, RateLimited, TransientError, failure

STOP_FILE = "github-rate-limit.json"
GRAPHQL = ("api", "graphql", "--input", "-")


def _spent(text: str) -> bool:
    return "rate limit" in text.lower() or "RATE_LIMITED" in text


def _until(reset: int) -> RateLimited:
    return RateLimited(f"GitHub's rate limit is spent: no call until {time.strftime('%H:%M:%S', time.localtime(reset))}")


class Api:
    def __init__(self, stop: Path, cwd: Path | None = None) -> None:
        self.stop = stop
        self.cwd = cwd  # where gh runs: the repo, for `{owner}/{repo}` placeholders

    def run(self, *args: str, input: str | None = None) -> proc.Result:
        """`gh <args>`; a non-zero exit is a failure with gh's error."""
        r = self._gh(args, input)
        if r.code != 0:
            if _spent(r.err):
                raise self._limited()
            raise failure(f"gh {' '.join(args[:3])}", r.err or r.out)
        return r

    def query(self, text: str, **variables: object) -> dict[str, Any]:
        """The `data` of a GraphQL request. Errors that are all NOT_FOUND leave their nulls in `data` for the caller."""
        r = self._gh(GRAPHQL, json.dumps({"query": text, "variables": variables}))
        try:
            body = util.json_object(json.loads(r.out))
        except ValueError:
            body = None
        errors = [o for e in util.json_list((body or {}).get("errors")) if (o := util.json_object(e)) is not None]
        if _spent(r.err) or any(_spent(str(e.get("message", ""))) or e.get("type") == "RATE_LIMITED" for e in errors):
            raise self._limited()
        if body is None:
            raise failure("gh api graphql", r.err or r.out)
        if any(e.get("type") != "NOT_FOUND" for e in errors):
            raise failure("gh api graphql", "; ".join(str(e.get("message", e)) for e in errors))
        data = util.json_object(body.get("data"))
        if data is None:
            raise ProviderError(f"gh api graphql: no data in {r.out!r}"[:300])
        return data

    def _gh(self, args: tuple[str, ...], input: str | None) -> proc.Result:
        reset = util.json_object(util.read_json(self.stop, None)) or {}
        if isinstance(at := reset.get("reset"), int) and at > time.time():
            raise _until(at)
        try:
            return proc.run(["gh", *args], cwd=self.cwd, input=input)
        except proc.ProcError as e:
            raise (TransientError if e.timed_out else PermanentError)(str(e)) from None

    def _limited(self) -> RateLimited:
        """Reads the reset once and keeps it in the stop file."""
        try:
            r = proc.run(["gh", "api", "rate_limit", "--jq", ".resources.graphql.reset"])
            at = int(r.out.strip())
        except (proc.ProcError, ValueError):
            at = int(time.time()) + 3600
        util.write_json(self.stop, {"reset": at})
        return _until(at)


def obj(v: object, what: str) -> dict[str, Any]:
    """A JSON object of GitHub's answer; a ProviderError naming `what` when it is not one."""
    o = util.json_object(v)
    if o is None:
        raise ProviderError(f"gh api graphql: unexpected {what} {v!r}"[:300])
    return o


def nodes(o: dict[str, Any], key: str, what: str) -> list[dict[str, Any]]:
    """`o[key].nodes`, each a JSON object."""
    group = obj(o.get(key), what)
    if not isinstance(group.get("nodes"), list):
        raise ProviderError(f"gh api graphql: unexpected {what} {o!r}"[:300])
    return [obj(n, what) for n in util.json_list(group["nodes"])]


def text(o: dict[str, Any], key: str, what: str) -> str:
    v = o.get(key)
    if not isinstance(v, str):
        raise ProviderError(f"gh api graphql: unexpected {what} {o!r}"[:300])
    return v


def whole(o: dict[str, Any], key: str, what: str) -> int:
    v = o.get(key)
    if not isinstance(v, int) or isinstance(v, bool):
        raise ProviderError(f"gh api graphql: unexpected {what} {o!r}"[:300])
    return v
