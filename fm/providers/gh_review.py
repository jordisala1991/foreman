"""The inline links of a posted review: the body's links to findings that got a comment point at that comment."""

import json
from collections.abc import Callable
from typing import Any

from .. import proc
from ..inline import LineComment, Review, resolve_links
from . import Permanent, ProviderError, Transient


def marker(key: str) -> str:
    return f"<!-- fm:{key} -->"


def link_comments(run: Callable[..., proc.Result], change: dict[str, Any], review: Review, new: list[LineComment], posted: tuple[str, str]) -> None:
    """Best effort: the body's links to findings that got an inline comment point at that comment instead of the
    file. A failure leaves the file links, which are right too."""
    base, out = posted
    if not new or "fm-link:" not in review.body:
        return
    try:
        rid = json.loads(out)["id"]
        listing = f"repos/{{owner}}/{{repo}}/pulls/{change['pr']}/reviews/{rid}/comments"
        r = run("gh", "api", listing, "--jq", r'.[] | "\(.id) \(.body | scan("<!-- fm:[a-z0-9-]+ -->"))"')
        ids: dict[str, str] = {}
        for line in r.out.splitlines():
            num, _, mark = line.partition(" ")
            ids[mark] = num
        urls = {c.place: f"{base}/pull/{change['pr']}#discussion_r{ids[marker(c.key)]}" for c in new if marker(c.key) in ids}
        body = resolve_links(review.body, base, change["head"], urls) + f"\n\n{marker(review.key)}"
        run("gh", "api", "-X", "PUT", f"repos/{{owner}}/{{repo}}/pulls/{change['pr']}/reviews/{rid}", "--input", "-", input=json.dumps({"body": body}))
    except (ValueError, KeyError, TypeError, ProviderError, Transient, Permanent):
        return
