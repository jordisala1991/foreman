"""The verifier's review against the hunter's file: a finding dropped without a cited refutation is still open (pure text)."""

import re

FINDING = re.compile(r"^\W*(F\d+)\b", re.M)
CITATION = re.compile(r"[\w./-]+\.\w+:\d+|\boutput\b|\bno wrong result\b")


def dropped_without_proof(hunter: str, review: str) -> list[str]:
    """Ids of hunter findings the review put under Noted with no file:line or output cited (nor «no wrong result»)."""
    if "## Noted" not in review:
        return []
    ids = set(FINDING.findall(hunter))
    noted = review.split("## Noted", 1)[1]
    return [m.group(1) for line in noted.splitlines() if (m := FINDING.match(line)) and m.group(1) in ids and not CITATION.search(line)]
