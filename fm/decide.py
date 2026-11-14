"""What the loop does with a branch in review: pure functions, no I/O. The loop (fm/loop.py) reads the facts (sessions,
files, git, CI, the check) and acts on what these return. The table is in DESIGN.md, "What the loop does with a task in
review". Runs no program and touches no file: tach lets it import only `risk`, tests/test_decide.py rejects the rest."""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, NamedTuple

from .risk import allows


class Step(NamedTuple):
    """`go` (this tip has its verdict: carry on), `wait`, `hunt`, `review` (launch that role), `block` (text: the reason),
    `back` (text: the note for its agent)."""

    do: str
    text: str = ""


@dataclass(frozen=True)
class Seen:
    """What the loop saw of a branch's review: the tips of its verdict, hunter and reviewer, and their sessions."""

    head: str
    verdict_tip: str
    hunter_tip: str
    reviewer_tip: str
    free: bool  # a slot is free and the project is not paused
    hunter_file: bool  # the hunter's findings file exists
    hunter_working: bool
    hunter_ended: bool
    reviewer_ended: bool


def review_step(seen: Seen, v: Mapping[str, Any], max_rounds: int) -> Step:
    """The hunter, then the reviewer, for each tip; with the verdict, open findings go back to its agent once before the
    last round (what is still open then goes to the owner)."""
    if seen.verdict_tip == seen.head:
        if not v.get("returned") and v["open"] > 0 and v["round"] < max_rounds:
            return Step("back", back_note(v))
        return Step("go")
    if seen.hunter_tip != seen.head:
        return Step("hunt" if seen.free else "wait")
    if seen.reviewer_tip != seen.head:
        if seen.hunter_file and not seen.hunter_working and seen.free:
            return Step("review")
        if not seen.hunter_file and seen.hunter_ended:
            return Step("block", "its hunter ended without its findings file")
        return Step("wait")
    if seen.reviewer_ended:
        return Step("block", "its reviewer ended without a verdict (`fm verdict`)")
    return Step("wait")


def back_note(v: Mapping[str, Any]) -> str:
    return (
        f"The review of your branch (round {v['round']}) leaves {v['open']} open: read {v['file']}. "
        "For each one, make the smallest change that settles it, or answer it in one line if you think it is wrong; add "
        "nothing it does not ask for. Add one `### Round N` to the «Review rounds» section of your report, one line per "
        "finding, and run `fm done` again."
    )


def conflict_note(main: str) -> str:
    return (
        f"{main} moved and your branch conflicts with it. Merge {main} into your branch, resolve keeping both changes, run the tests, and run `fm done` again."
    )


def waits_for_ci(ci: str) -> bool:
    return ci == "pending"


def checks_again(c: Mapping[str, Any], head: str, main_head: str, ci: str | None) -> bool:
    """The branch is checked again when its head or main moved since the last check, or its CI did (`ci` is None when
    the task has no PR)."""
    return c.get("head") != head or c.get("main") != main_head or (ci is not None and c.get("ci") != ci)


def after_check(c: Mapping[str, Any], autonomy: str, has_ci: bool) -> str:
    """`conflict` back to its agent; `merge` alone when the autonomy lets this risk in, no findings are open and, when the
    provider has CI, it passed (quality is not risk, but unresolved quality waits for the owner); else `owner`."""
    if c.get("conflict"):
        return "conflict"
    if str(c.get("review", "")).startswith("open"):
        return "owner"
    if has_ci and c.get("ci") != "pass":
        return "owner"
    return "merge" if allows(autonomy, c["risk"]) else "owner"
