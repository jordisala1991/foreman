"""A PR review through `gh api`: one review per commit, one comment per finding, each found again by a hidden
`<!-- fm:KEY -->` marker so nothing is posted twice. `run` is the provider's own `GitHub.run` (same errors)."""

import json
from collections.abc import Callable

from .. import proc
from ..inline import LineComment, Review, resolve_links
from . import Change, Permanent, ProviderError, Transient

Run = Callable[..., proc.Result]


def marker(key: str) -> str:
    return f"<!-- fm:{key} -->"


def markers(run: Run, change: Change, what: str) -> set[str]:
    """The markers in the bodies of the PR's reviews or review comments."""
    url = f"repos/{{owner}}/{{repo}}/pulls/{change.pr}/{what}"
    r = run("gh", "api", url, "--paginate", "--jq", '.[] | (.body // "") | scan("<!-- fm:[a-z0-9-]+ -->")')
    return set(r.out.split("\n")) - {""}


def post(run: Run, change: Change, review: Review) -> int:
    """Posts `review` on `change.head` (event COMMENT); returns how many inline comments it sent. A review whose
    key is already on the PR posts nothing; a comment whose key is already there is skipped."""
    if marker(review.key) in markers(run, change, "reviews"):
        return 0
    known = markers(run, change, "comments")
    new = [c for c in review.comments if marker(c.key) not in known]
    base = run("gh", "repo", "view", "--json", "url", "-q", ".url").out.strip()
    payload = {
        "commit_id": change.head,
        "event": "COMMENT",
        "body": resolve_links(f"{review.body}\n\n{marker(review.key)}", base, change.head, {}),
        "comments": [{"path": c.path, "line": c.line, "side": "RIGHT", "body": f"{c.body}\n\n{marker(c.key)}"} for c in new],
    }
    url = f"repos/{{owner}}/{{repo}}/pulls/{change.pr}/reviews"
    posted = run("gh", "api", "-X", "POST", url, "--input", "-", input=json.dumps(payload))
    link_comments(run, change, review, new, (base, posted.out))
    return len(new)


def link_comments(run: Run, change: Change, review: Review, new: list[LineComment], posted: tuple[str, str]) -> None:
    """Best effort: the body's links to findings that got an inline comment point at that comment instead of the
    file. A failure leaves the file links, which are right too."""
    base, out = posted
    if not new or "fm-link:" not in review.body:
        return
    try:
        rid = json.loads(out)["id"]
        listing = f"repos/{{owner}}/{{repo}}/pulls/{change.pr}/reviews/{rid}/comments"
        r = run("gh", "api", listing, "--jq", r'.[] | "\(.id) \(.body | scan("<!-- fm:[a-z0-9-]+ -->"))"')
        ids: dict[str, str] = {}
        for line in r.out.splitlines():
            num, _, mark = line.partition(" ")
            ids[mark] = num
        urls = {c.place: f"{base}/pull/{change.pr}#discussion_r{ids[marker(c.key)]}" for c in new if marker(c.key) in ids}
        body = resolve_links(review.body, base, change.head, urls) + f"\n\n{marker(review.key)}"
        run("gh", "api", "-X", "PUT", f"repos/{{owner}}/{{repo}}/pulls/{change.pr}/reviews/{rid}", "--input", "-", input=json.dumps({"body": body}))
    except (ValueError, KeyError, TypeError, ProviderError, Transient, Permanent):
        return
