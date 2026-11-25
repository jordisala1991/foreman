"""`gh` for the GitHub adapter, the one place it runs: the backlog's GraphQL requests and the provider's commands, and
the rate limit's stop. When GitHub says the limit is spent, the reset is kept in the stop file and every call until then
raises `RateLimited` without running gh: each foreman process obeys it, and nothing here waits or tries again (retrying
is `loop.guarded`'s). The reset kept is the earliest of the resources whose remaining is 0 (what was spent). A secondary limit
shows no spent resource: it stops for the wait GitHub gives (Retry-After, "wait N", at most LONGEST) or else SHORT, doubling up to LONGEST
when another comes right after; so does a limit message with no resource at 0. The gh text that caused a stop is logged once."""

import contextlib
import json
import re
import time
from pathlib import Path
from typing import Any

from ... import proc, util
from .. import ProviderError
from .types import PermanentError, RateLimited, TransientError, failure

STOP_FILE = "github-rate-limit.json"
GRAPHQL = ("api", "graphql", "--input", "-")
SHORT = 60  # seconds a stop lasts when GitHub gives no wait and no resource is spent
LONGEST = 900  # the cap of its doubling on each new secondary limit


def _secondary(text: str) -> bool:
    return "secondary rate limit" in text.lower()


def _given(text: str) -> int | None:
    """The seconds GitHub asks to wait (`Retry-After: N`, `wait N [minute(s)]`), at most LONGEST; None if the text gives none."""
    m = re.search(r"(?:retry-after:?\s*|wait\s+)(\d+)(\s*minutes?)?", text, re.I)
    return min(int(m.group(1)) * (60 if m.group(2) else 1), LONGEST) if m else None


def _reset(out: str) -> int | None:
    """The earliest reset among the resources of `gh api rate_limit` with none remaining; None if there is none."""
    try:
        resources = util.json_object((util.json_object(json.loads(out)) or {}).get("resources")) or {}
    except ValueError:
        return None
    spent = [r for v in resources.values() if (r := util.json_object(v)) and r.get("remaining") == 0 and isinstance(r.get("reset"), int)]
    return min((r["reset"] for r in spent), default=None)


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
            step = f"gh {' '.join(args[:3])}"
            f = failure(step, r.err or r.out)
            if not isinstance(f, PermanentError) and _spent(r.err):
                raise self._limited(step, r.err)
            raise f
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
            raise self._limited("gh api graphql", (r.err or r.out).strip()[:300])
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

    def _limited(self, step: str, text: str) -> RateLimited:
        """The one place a stop is decided and written, for `run` and `query`: the reset of what was spent, else the wait
        GitHub gives, else SHORT (doubled while secondary limits follow each other). Logs the gh text once."""
        now = int(time.time())
        secondary = _secondary(text)
        at = None
        if not secondary:
            with contextlib.suppress(proc.ProcError):
                at = _reset(proc.run(["gh", "api", "rate_limit"]).out)
        stop: dict[str, int] = {"reset": at} if at else {}
        if not at:
            wait = _given(text)
            if wait is None:
                old = util.json_object(util.read_json(self.stop, None)) or {}
                before, reset = old.get("wait"), old.get("reset")
                follows = isinstance(before, int) and isinstance(reset, int) and now < reset + before
                wait = min(2 * before, LONGEST) if follows and isinstance(before, int) else SHORT
                stop = {"reset": now + wait, "wait": wait}
            else:
                stop = {"reset": now + wait}
        util.write_json(self.stop, stop)
        util.log_error(self.stop.with_name("errors.jsonl"), step, text.strip()[:300], trace="")
        return _until(stop["reset"])


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
