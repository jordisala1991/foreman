"""What the loop does with a branch in review: pure functions, no I/O. The loop (fm/loop.py) reads the facts (sessions,
files, git, CI, the check) and acts on what these return. The table is in DESIGN.md, "What the loop does with a task in
review". Runs no program and touches no file: tach lets it import only `risk`, tests/test_decide.py rejects the rest."""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, NamedTuple

from .risk import allows

RULES = {"round": "rounds 1-2", "reject": "owner reject", "conflict": "conflict resolution"}  # why a review is launched


class Step(NamedTuple):
    """`go` (this tip has its verdict: carry on), `hold` (no review: the verdict holds for this tip), `wait`, `hunt`,
    `review` (launch that role), `block` (text: the reason), `back` (text: the note for its agent). For `hunt`, `review`
    and `wait`, `text` is the rule that launches the review (a key of RULES) and `base` what it compares with (`""`: the
    whole branch)."""

    do: str
    text: str = ""
    base: str = ""


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
    review_for: str = ""  # `reject`, `conflict`, `none` (the review is closed); "" the rounds
    merge: str = ""  # how the head follows the verdict's tip: `clean` (a merge of main git does alone), `resolved`, ""
    auto: str = ""  # git's automatic merge tree of the verdict's tip and main, when `resolved`


def launch_rule(s: Seen) -> str:
    """Why a new tip gets a review (a key of RULES), or "" when nothing would act on it: a clean merge of main keeps the
    verdict; after an owner reject or a conflict, a review of that fix; a closed review launches nothing."""
    if s.verdict_tip and s.merge == "clean":
        return ""
    if s.review_for in ("reject", "conflict"):
        return s.review_for
    return "" if s.review_for == "none" else "round"


def review_base(rule: str, s: Seen) -> str:
    """What the review compares with: git's automatic merge for a conflict resolution, the reviewed tip for a reject's
    fix, the whole branch otherwise."""
    return s.auto if rule == "conflict" else s.verdict_tip if rule == "reject" else ""


def review_step(seen: Seen, v: Mapping[str, Any], max_rounds: int) -> Step:
    """The hunter, then the reviewer, for a tip whose review result goes somewhere (launch_rule); with the verdict, open
    findings go back to its agent once before the last round (what is still open then goes to the owner)."""
    if seen.verdict_tip == seen.head:
        if not v.get("returned") and v["open"] > 0 and v["round"] < max_rounds:
            return Step("back", back_note(v))
        return Step("go")
    rule = launch_rule(seen)
    if not rule:
        return Step("hold")
    base = review_base(rule, seen)
    if seen.hunter_tip != seen.head:
        return Step("hunt" if seen.free else "wait", rule, base)
    if seen.reviewer_tip != seen.head:
        if seen.hunter_file and not seen.hunter_working and seen.free:
            return Step("review", rule, base)
        if not seen.hunter_file and seen.hunter_ended:
            return Step("block", "its hunter ended without its findings file")
        return Step("wait", rule, base)
    if seen.reviewer_ended:
        return Step("block", "its reviewer ended without a verdict (`fm verdict`)")
    return Step("wait", rule, base)


def closes_review(v: Mapping[str, Any], max_rounds: int) -> bool:
    """A verdict the loop acts on closes the review when its findings are open after the last round: they went to the
    owner, and a later tip gets a review only for an owner reject or a conflict."""
    return v.get("open", 0) > 0 and v.get("round", 0) >= max_rounds


def back_note(v: Mapping[str, Any]) -> str:
    return (
        f"The review of your branch (round {v['round']}) leaves {v['open']} open: read {v['file']}. "
        "For each one, make the smallest change that settles it, or answer it in one line if you think it is wrong; add "
        "nothing it does not ask for. Add one `### Round N` to the «Review rounds» section of your report, one line per "
        "finding, and run `fm done` again."
    )


def conflict_note(main: str) -> str:
    return (
        f"{main} moved and your branch conflicts with it. Merge {main} into your branch with a merge commit, not a rebase, "
        "resolve keeping both changes, run the tests, and run `fm done` again."
    )


def waits_for_ci(ci: str) -> bool:
    return ci == "pending"


def checks_again(c: Mapping[str, Any], head: str, main_head: str, ci: str | None) -> bool:
    """The branch is checked again when its head or main moved since the last check, or its CI did (`ci` is None when
    the task has no PR)."""
    return c.get("head") != head or c.get("main") != main_head or (ci is not None and c.get("ci") != ci)


def after_check(c: Mapping[str, Any], autonomy: str, has_ci: bool, resolving: str = "") -> str:
    """It wants to merge when no finding is open, tests do not fail, the CI passed (when the provider has CI), and either
    the owner's accept carried over a conflict (`resolving`) or the autonomy lets this risk in (quality is not risk, but
    unresolved quality waits for the owner; an accept the autonomy made is decided again, so high never merges alone).
    Wanted and conflicting: `conflict`, back to its agent; wanted: `merge`; else `owner`: a conflict nobody said should
    merge waits, shown in its check."""
    wants = (
        not str(c.get("review", "")).startswith("open")
        and c.get("tests") != "fail"
        and (not has_ci or c.get("ci") == "pass")
        and ((bool(resolving) and not resolving.startswith("autonomy ")) or allows(autonomy, c["risk"]))
    )
    if not wants:
        return "owner"
    return "conflict" if c.get("conflict") else "merge"
