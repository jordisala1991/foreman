"""What the loop decides: pure functions, no I/O. The loop (fm/loop/, the steps, the providers' registry) reads the facts
(sessions, files, git, CI, the check, the backlog) and acts on what these return. The table is in DESIGN.md, "What the loop does with a task in
review". Runs no program and touches no file: tach lets it import only `risk`, tests/test_decide.py rejects the rest."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, NamedTuple

from .risk import allows

RULES = {  # why a review is launched
    "round": "rounds 1-2",
    "reject": "owner reject",
    "conflict": "conflict resolution",
    "failing": "failing tests or CI",
}
MAX_FAILURE_RETURNS = 2  # failing tests or CI return a branch this many times in a row; the next failure waits for the owner


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
    free: bool  # a slot is free
    hunter_file: bool  # the hunter's findings file exists
    hunter_working: bool
    hunter_ended: bool
    reviewer_ended: bool
    review_for: str = ""  # `reject`, `conflict`, `failing`, `none` (the review is closed); "" the rounds
    merge: str = ""  # how the head follows the verdict's tip: `clean` (a merge of main git does alone), `resolved`, ""
    auto: str = ""  # git's automatic merge tree of the verdict's tip and main, when `resolved`


def launch_rule(s: Seen) -> str:
    """Why a new tip gets a review (a key of RULES), or "" when nothing would act on it: a clean merge of main keeps the
    verdict; after an owner reject, a conflict or failing tests or CI, a review of that fix; a closed review launches
    nothing."""
    if s.verdict_tip and s.merge == "clean":
        return ""
    if s.review_for in ("reject", "conflict", "failing"):
        return s.review_for
    return "" if s.review_for == "none" else "round"


def review_base(rule: str, s: Seen) -> str:
    """What the review compares with: git's automatic merge for a conflict resolution, the reviewed tip for the fix of a
    reject or of failing tests or CI (the failing tip: a check runs only once the verdict is for the head), the whole
    branch otherwise."""
    return s.auto if rule == "conflict" else s.verdict_tip if rule in ("reject", "failing") else ""


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
    """A verdict of the last round closes the review once the loop acts on it, whatever is open (what is open goes to the
    owner): no round past the last runs by itself; a later tip gets a review only for an owner reject or a conflict."""
    return v.get("round", 0) >= max_rounds


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


def failing_note(c: Mapping[str, Any], pr_url: str) -> str:
    """The note for an agent whose branch fails its checks: the last lines of the test output, each failing CI check.
    A branch that passes alone but fails merged with main is told so, and to merge main in."""
    note = "Your branch fails its checks. "
    if c.get("merged_with") and c.get("tests") == "fail":
        note = (
            f"Your branch passes its tests alone but fails them merged with main {c['merged_with'][:9]}, not on its branch "
            "alone. Merge main into your branch with a merge commit, not a rebase, fix what breaks, run the tests there, "
            "and run `fm done` again.\n"
        )
    if c.get("tests_output"):
        note += f"The test command fails; last lines of its output:\n```\n{c['tests_output']}\n```\n"
    elif c.get("tests") == "fail":
        note += "The tests fail and print nothing (the test command, or the PR's CI when there is no test command).\n"
    failed: list[Mapping[str, str]] = c.get("failed_checks") or []
    if failed:
        note += "The PR's CI fails:\n" + "".join(f"- {f['name']}: {f['url']}\n" for f in failed)
    elif c.get("ci") == "fail":
        note += f"The PR's CI fails and GitHub names no failing check: see {pr_url}\n"
    return note + "Fix it, run the tests, and run `fm done` again."


def failing(c: Mapping[str, Any]) -> bool:
    return c.get("tests") == "fail" or c.get("ci") == "fail"


def waits_for_ci(ci: str) -> bool:
    return ci == "pending"


def checks_again(c: Mapping[str, Any], head: str, main_head: str, ci: str | None) -> bool:
    """The branch is checked again when its head or main moved since the last check, or its CI did (`ci` is None when
    the task has no PR)."""
    return c.get("head") != head or c.get("main") != main_head or (ci is not None and c.get("ci") != ci)


def after_check(c: Mapping[str, Any], autonomy: str, has_ci: bool, resolving: str = "", failed: int = 0) -> str:
    """Failing tests or CI go back to its agent (`failing`), whoever wants the merge, unless they already went back
    `failed` (MAX_FAILURE_RETURNS) times in a row: then the owner sees them. Otherwise it wants to merge when no finding
    is open, tests do not fail, the CI passed (when the provider has CI), and either the owner's accept carried over a
    conflict (`resolving`) or the autonomy lets this risk in (quality is not risk, but unresolved quality waits for the
    owner; an accept the autonomy made is decided again, so high never merges alone). Wanted and conflicting:
    `conflict`, back to its agent; wanted: `merge`; else `owner`: a conflict nobody said should merge waits, shown in
    its check."""
    if failing(c) and failed < MAX_FAILURE_RETURNS:
        return "failing"
    wants = (
        not str(c.get("review", "")).startswith("open")
        and c.get("tests") != "fail"
        and (not has_ci or c.get("ci") == "pass")
        and ((bool(resolving) and not resolving.startswith("autonomy ")) or allows(autonomy, c["risk"]))
    )
    if not wants:
        return "owner"
    return "conflict" if c.get("conflict") else "merge"


def merge_gate(c: Mapping[str, Any], head: str, main_head: str, failed: int, as_today: bool) -> str:
    """At the merge step of an accepted branch: `merge`, `check` (run the tests on it merged with main as it is now),
    `failing` (back to its agent: it fails merged with main) or `owner` (past MAX_FAILURE_RETURNS: its accept lapses).
    `as_today`: no test command (the PR's CI stands in) or a conflict with main (the merge returns it, FN-133); it and
    a moved branch (the merge refuses it) go to the merge as today. A merged result counts only for this head and main."""
    if as_today or c.get("head") != head:
        return "merge"
    if c.get("merged_with") != main_head:
        return "check"
    if c.get("tests") != "fail":
        return "merge"
    return "failing" if failed < MAX_FAILURE_RETURNS else "owner"


def ran_on(c: Mapping[str, Any]) -> str:
    """What the last check ran on, for `fm show`."""
    return f"merged with main {c['merged_with'][:9]}" if c.get("merged_with") else "branch alone"


CHECKS_PER_TICK = 1  # branch checks started in one tick; the rest wait for the next ticks
TRANSIENT_ATTEMPTS = 3  # consecutive Transient failures of one task before it is blocked


def queue_turn(i: Mapping[str, Any]) -> str:
    """What the queue does with a task now: `merge` (accepted), `check`, or "" (not in review, or blocked)."""
    if i["status"] != "in review" or i.get("blocked"):
        return ""
    return "merge" if i.get("accepted") else "check"


def queue_order(tasks: Sequence[Mapping[str, Any]]) -> list[tuple[str, str]]:
    """The queue's turns, `(id, turn)`: every merge (quick) before every check, each in the backlog's order."""
    turns = [(i["id"], queue_turn(i)) for i in tasks]
    return [t for t in turns if t[1] == "merge"] + [t for t in turns if t[1] == "check"]


def queue_full(started: int) -> bool:
    """`started` checks were started in this tick: no more this tick."""
    return started >= CHECKS_PER_TICK


@dataclass(frozen=True)
class Retry:
    """After a Transient failure: `block` the task, else store `count` and note the error in the events if `say`."""

    block: bool
    count: int
    say: bool


def retry_step(count: int, last_error: str | None, error: str) -> Retry:
    """`count` Transient failures in a row before this one, the last with `last_error`: the TRANSIENT_ATTEMPTS-th blocks
    (and resets the count); before it, the task stays as is and a new error is noted once (None: none stored yet)."""
    n = count + 1
    if n >= TRANSIENT_ATTEMPTS:
        return Retry(True, 0, False)
    return Retry(False, n, last_error != error)


def tests_or_ci(tests: str, ci: str) -> str:
    """Without a test command (`none`), the PR's CI stands in for the tests once it has a result."""
    return ci if tests == "none" and ci in ("pass", "fail") else tests


def as_today(has_test: bool, conflict: bool) -> bool:
    """For merge_gate: no test command (the PR's CI stands in) or a conflict with main goes to the merge as today."""
    return not has_test or conflict


class MergeConfig(NamedTuple):
    """The `[merge]` values that pick and drive the provider (`provider` after `auto` is resolved)."""

    provider: str
    strategy: str
    pr: str


def provider_error(m: MergeConfig, merges: Mapping[str, Sequence[str]], strategies: Sequence[str], prs: Sequence[str]) -> str:
    """Empty when `[merge]` is valid, else what is wrong: `merges` is each provider's strategies, `strategies` and `prs` the
    values fm knows."""
    provider, strategy, pr = m
    if provider not in merges:
        return f"[merge] provider = {provider!r} is not valid: use one of auto, {', '.join(merges)}"
    if pr not in prs:
        return f"[merge] pr = {pr!r} is not valid: use one of {', '.join(prs)}"
    if strategy not in strategies:
        return f"[merge] strategy = {strategy!r} is not valid: use one of {', '.join(strategies)}"
    if strategy not in merges[provider]:
        return f"[merge] strategy = {strategy!r} is not valid for {provider}: use one of {', '.join(merges[provider])}"
    return ""


def main_tests_due(ci_head: str | None, head: str, age_s: float | None, every_minutes: int) -> bool:
    """Main's tests run again when main moved since the last run, or that run is older than `every_minutes`."""
    return ci_head != head or (age_s or 1e9) > 60 * every_minutes
