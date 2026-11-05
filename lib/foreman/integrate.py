"""`foreman integrate REPO BRANCH "ID [Tn]" …`: a finished branch into main through a PR, or by fast-forward.

Forked from an earlier in-house version (one repo, squash only, its own CI script, an API-surface diff and a
privacy scanner for a public repo). Everything specific is now per repo in foreman.toml ([repos.REPO]): `merge`
(squash | merge | rebase), `remote` (the git remote, origin by default; "none" for a repo without one, see below),
`local_ci` (a command run in the worktree before anything is pushed; {wt}, {main}, {branch} and {ci} are
replaced), `ci_runs` (the runs of the local CI: each value is put in {ci}; [""] by default, one run),
`ci_summary` (a command that reads each run's output on stdin; its stdout goes into the PR comment), `main_ci`
(without a remote: what runs in the main checkout after the fast-forward instead of `local_ci`, see below),
`remote_checks` (required: wait for `gh pr checks --watch`), `risky_paths` (a branch that touches them is not merged
without the owner's approval), `require_approval` and `publish_on_merge` (merging is publishing: always the owner's
approval), `public` (title, body, comment and the branch's commit messages may not carry internal IDs, bracketed or
not; `privacy_scan` is an extra command that reads each text on stdin and fails to stop it), `pre_merge_report` (a
command whose output is shown before merging), `post_merge` (a command run in the main checkout once the branch is in
main; {main}, {branch}, {before} and {head} are replaced) and `plan_check_heading` (the section of the issue's plan to
compare with the branch's report).

Order (a failure at any step stops, never forces, and leaves the PR open if there is one):
 0. before any effect: the PR title and body and --result reach the owner, so what they name without explaining
    (`foreman plain`, docs/owner.md) is a warning on stderr that never blocks; and what the backlog close would
    reject (--verdict, --result, the issue and sub-issue, --report): a dry run of `foreman backlog integrated`; a
    failure stops with nothing done (exit 2). The --report path (under the home) must exist in the main checkout or,
    with the home inside the repo, be a file the branch adds; without --report it is the sub-issue's `report`, else the
    one the agent named with `progress --done --report`, when that one passes the same test (delivery.report_of);
 1. the main checkout is on a clean main equal to origin/main; the branch's worktree is clean; with --rebase-from
    COMMIT the branch is first rebased with `git rebase --onto main COMMIT`; the branch must contain main (without
    a remote, main may be merged into it first: see below);
 2. shows the diff stat, the risky paths it touches, the pre-merge report, the plan's check section (its heading
    may carry a section number: «## 6. How to check») and the outcome of the agent's delivery when it is known
    (`progress --done --outcome`, delivery.outcome_of); then the live permissions the merge changes (permissions_gate):
    a file of the main checkout that is the live foreman.toml or guard table of a registered product
    (guard.reserved) and that main will hold changed (merged_tree: the branch, or its clean merge with main) is
    tested as `foreman permissions apply` tests a candidate (permissions.judge, with the installed foreman; the new
    foreman.toml read tolerantly with the real home: an unknown key is a warning): it does not load (or is deleted),
    exit 2; a row fails or the lifeline loses a command, exit 1; it allows something new without --opens, exit 3
    (nothing merged, a progress line; with --opens the command is in the guard's internal group `permissions`, the
    owner's yes); else it goes on and the note says what changed. The other reserved files in the repo (Claude
    Code's settings, a state, the registry) are risky paths. Then, in a repo with `review = "auto"`, the review
    gate (review.py): the line «== review: STATE»; a branch that needs a review (delicate paths, more than
    `review_lines` lines of code, a partial delivery) goes in only with a review that covers its tip and leaves 0 H
    and 0 M open; otherwise exit 2, nothing done, with the next step. --no-review "why" lets in a branch whose only
    reason is its size and that has no review yet (never delicate paths nor a partial delivery); after round 2 with
    H or M still open only the owner's yes (--approved) lets it in as it is; both go into the note. --check shows the
    same and what it would do;
    (--check stops here, after printing what the backlog close would do);
 3. in a `public` repo, each commit message of main..BRANCH passes the public scan (a stop names the commit and the
    rule, never the text); then the local CI in the worktree (unless --no-ci): one run of `local_ci` per value of
    `ci_runs`, or per --ci=ARGS (repeatable; it replaces `ci_runs`; write it with «=» when ARGS starts with «-»), in
    order, the first failure stopping; --ci without {ci} in `local_ci` is exit 2 (arguments are never appended
    blindly). The output is also written, as it comes, to <state>/integrate/<branch>-ci.log (a «/» in the branch
    becomes «-»);
 4. push and open the PR (or reuse the open one for this branch); the body gets «Closes #n» when the issue was
    imported from GitHub (its `github` field names this repo);
 5. waits for the remote checks and comments the local CI result (with `ci_summary`, one bullet per run under
    it; the comment passes the public scan before anything is pushed);
 6. approval gate: if the repo or the branch needs the owner, without --approved "who, when" it stops here (exit 3,
    PR open and green) and says so; rerun with --approved once the owner says yes;
 7. merges with the repo's method (--match-head-commit), pulls main, deletes the remote branch, the worktree and
    the local branch; then `post_merge`, if set, in the main checkout: {before} is main before the merge and
    {head} main after the pull (full SHAs). Its output is shown and its last line goes into the note. A failure
    never undoes the merge: a warning, a progress line from the orchestrator, and the integration goes on (exit 0);
 8. the only note of the integration and the backlog close (`foreman backlog integrated`).

A repo with `remote = "none"` has no PR: never a push nor a call to gh, and --title/--body are optional (the title,
if given, goes to the note). Steps 1-3 are the same, except that main is not compared with any remote and, when the
project's home lives inside the repo, uncommitted changes under the home (backlog, notes, reports) do not make main
dirty (the branch may not touch those files, or the fast-forward would fail). When the branch does not contain
main, the home lives inside the repo, everything main has that the branch lacks lies under the home (the
orchestrator's own commits: backlog, notes, reports) and the merge is clean (`git merge-tree`), main is merged into
the branch in its worktree (`git merge --no-ff --no-edit`, never a rebase) and the integration goes on; --check only
says so. Anything else stops, saying why (the paths outside the home, or the conflicting files). Then:
 4. approval gate as in 6 (exit 3, nothing merged: the branch stays as it is);
 5. `git merge --ff-only BRANCH` in the main checkout (`merge` and `remote_checks` do not apply);
 6. the local CI again (the same runs), now in the main checkout; or `main_ci` instead, when it is set and main is
    exactly the commit the local CI passed on in the worktree (once per run if it uses {ci}, once otherwise;
    "" runs nothing); if it fails, main goes back to where it was (`git reset --keep`, only if main has not moved
    since) and it stops, the worktree and the branch untouched;
 7. removes the worktree and the local branch, then `post_merge` as above ({before}..{head} is the fast-forward);
 8. note and backlog close as above.

A measurement (a sub-issue with `kind = "measure"`, or whose agent delivered with `progress --done --kind measure`)
brings no code for main: --measure closes it instead (any repo, remote or not). Step 0 as above, with the report
required (it may also be an uncommitted file in the worktree's home); every sub-issue it closes must be a
measurement (exit 2 otherwise); the worktree must be clean, untracked files included. Then no CI, no merge, no push
and no PR: a report that is only in the branch or the worktree is copied into the home (uncommitted), the worktree
is removed (`git worktree remove`, never forced), the local branch deleted (--keep-branch keeps it for a later
sub-issue; a remote branch is never touched), the note «Measured REPO:BRANCH (ID Tn)» written and the sub-issues
closed `done` without a commit. --check says what it would do. Without --measure, sub-issues that are all `kind =
"measure"` stop with exit 2 (mark them `--kind code` to merge their branch); a measure sub-issue closed along code
ones on the same branch is closed without the commit.
FOREMAN_GH overrides the gh executable (tests).
"""

from __future__ import annotations

import functools
import inspect
import os
import re
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any

from . import backlog_pages, commands, config, delivery, guard, i18n, notes, permissions, plain, review, util
from .backlog import Backlog, BacklogError

if TYPE_CHECKING:
    import argparse

    from .config import Project
    from .util import PathLike

DOC = inspect.cleandoc(__doc__ or "")  # the help text, the same on every Python (3.13+ dedents docstrings itself)

AWAITING = 3
LIVE_KINDS = ("config", "table")  # the reserved files a merge may change that permission_changes() tests


class Stop(Exception):
    """Stops the integration; `rc` is the exit code."""

    def __init__(self, msg: str, rc: int = 1) -> None:
        super().__init__(msg)
        self.rc = rc


POST_MERGE_SECONDS: float = (
    commands.POINTS["repo.post_merge"].timeout or 600
)  # fixed (docs/extending.md); no config key


def sh(
    args: Sequence[str],
    cwd: PathLike | None = None,
    check: bool = True,
    inp: str | None = None,
    env: Mapping[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    r = subprocess.run(args, cwd=cwd, capture_output=True, text=True, input=inp, env=env, check=False)
    if check and r.returncode != 0:
        raise Stop(f"{' '.join(str(a) for a in args[:4])}…: {(r.stderr or r.stdout).strip()[:400]}")
    return r


git = functools.partial(util.git, error=Stop)


class Integration:
    def __init__(
        self,
        project: Project,
        repo_key: str,
        branch: str,
        ref: str,
        args: argparse.Namespace,
        out: Callable[[str], object] = print,
    ) -> None:
        self.p = project
        self.repo = project.repo(repo_key)
        self.main_path = self.repo["path"]
        self.main = self.repo.get("main", "main")
        self.remote = self.repo.get("remote", "origin")
        self.local = self.remote == "none"
        self.branch = branch
        self.issue, _, sub = ref.partition(" ")
        self.sub = args.sub or sub or None
        self.a = args
        self.out = out
        self.gh_exe = os.environ.get("FOREMAN_GH") or "gh"
        self.merged_main: tuple[int, str] | None = (
            None  # (n commits, short sha) when main was merged into the branch first
        )
        self.backlog = Backlog(project)
        self.s = i18n.for_project(project)
        self.touched: list[str] = []
        self.perm_note = ""  # the note's part on the live permissions the branch changes (permissions_gate)
        self.outcome: str | None = None  # the agent's delivery (show())
        self.review_note = ""  # the note's part on the branch's review (review_gate)

    def gh(self, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        return sh([self.gh_exe, *args], cwd=self.main_path, check=check)

    # ---------- steps ----------

    def worktree(self) -> str:
        for block in git(self.main_path, "worktree", "list", "--porcelain").split("\n\n"):
            lines = block.splitlines()
            if f"branch refs/heads/{self.branch}" in lines:
                return lines[0].removeprefix("worktree ")
        raise Stop(f"no worktree for branch {self.branch} in {self.main_path}")

    def home_in_repo(self) -> str | None:
        """The home's path relative to the main checkout when the home lives inside the repo (else None)."""
        try:
            rel = self.p.home.resolve().relative_to(self.main_path.resolve())
        except ValueError:
            return None
        return str(rel) if str(rel) != "." else None

    def branch_adds(self, path: str) -> bool:
        """Whether the branch adds PATH (relative to the home) as a file that main lacks, with the home inside the
        repo: a report the agent committed with its work exists in the main checkout once the branch is merged."""
        home = self.home_in_repo()
        if not home:
            return False
        rel = f"{home}/{path}"
        kind = git(self.main_path, "cat-file", "-t", f"{self.branch}:{rel}", check=False)
        return kind == "blob" and git(self.main_path, "cat-file", "-t", f"{self.main}:{rel}", check=False) != "blob"

    def default_report(self) -> str | None:
        """The report to close the sub-issue with when --report is not given: the sub-issue's `report` field, else
        the one the agent named at its delivery (`progress --done --report`), when it is a path under
        `[ids] report_prefixes` (relative to the home) that exists in the main checkout or that the branch adds."""
        try:
            data = self.backlog.load()
            subs = self.backlog.issue(data, self.issue)["subs"]
        except (BacklogError, OSError, ValueError):
            data, subs = None, []
        reports, _ = delivery.sub_reports(self.p, self.branch, data)
        mine = [s for s in subs if s.get("branch", "").strip("`") == self.branch]
        if not (self.sub or mine):  # no sub-issue to close: the backlog close would refuse a report
            return None
        agent = next((s.get("agent") for s in mine), None)
        named = delivery.report_of(self.p, branch=self.branch, name=agent)
        prefixes = tuple(delivery.report_dirs(self.p))
        for r in [*reports, *([named] if named else [])]:
            if Path(r).is_absolute() or (prefixes and not r.startswith(prefixes)):
                continue
            if (self.p.home / r).is_file() or self.branch_adds(r) or (self.a.measure and self.in_worktree(r)):
                return r
        return None

    def in_worktree(self, path: str) -> bool:
        """Whether PATH (relative to the home) is a file in the home's folder of the branch's worktree (a
        measurement's report the agent did not commit)."""
        try:
            wh = delivery.worktree_home(self.p, self.worktree())
        except Stop:
            return False
        return wh is not None and (wh / path).is_file()

    def plan_check_section(self, text: str) -> str | None:
        """The body of the plan's check section, or None. Its heading is the repo's `plan_check_heading` or, by
        default, the one of either language; a leading section number is allowed («## 6. How to check»)."""
        headings = (
            [self.repo["plan_check_heading"]]
            if self.repo.get("plan_check_heading")
            else list(
                dict.fromkeys(
                    [self.s["plan_check_heading"]] + [i18n.strings(lang)["plan_check_heading"] for lang in ("en", "es")]
                )
            )
        )
        for heading in headings:
            hashes, _, title = heading.strip().partition(" ")
            m = re.search(
                rf"^{re.escape(hashes)}[ \t]+(?:\d+(?:\.\d+)*\.?[ \t]+)?{re.escape(title.strip())}[^\n]*\n"
                rf"(.*?)(?=^{re.escape(hashes)} |\Z)",
                text,
                re.S | re.M,
            )
            if m:
                return m.group(1).strip()
        return None

    def home_only_merge(self) -> tuple[bool, str, int]:
        """Whether main can be merged into the branch on its own: no remote, the home inside the repo, everything
        main has that the branch lacks under the home, and a clean merge. Returns (ok, why, n commits); writes
        nothing (`git merge-tree --write-tree` only adds loose objects)."""
        if not self.local:
            return False, f"the repo has a remote ({self.remote})", 0
        home = self.home_in_repo()
        if not home:
            return False, "the project's home is outside the repo", 0
        base = git(self.main_path, "merge-base", self.main, self.branch)
        if base == git(self.main_path, "rev-parse", self.branch):
            return False, f"{self.branch} has nothing of its own to integrate", 0
        n = int(git(self.main_path, "rev-list", "--count", f"{base}..{self.main}"))
        # every commit (also what was touched and undone) plus what main's merges leave; both sides of a rename
        paths = set(
            git(self.main_path, "log", "--no-renames", "--format=", "--name-only", f"{base}..{self.main}").splitlines()
        )
        paths |= set(git(self.main_path, "diff", "--no-renames", "--name-only", base, self.main).splitlines())
        outside = sorted(f for f in paths if f and not f.startswith(home + "/"))
        if outside:
            more = f" and {len(outside) - 5} more" if len(outside) > 5 else ""
            return False, f"{self.main} changed outside the home {home}/ ({', '.join(outside[:5])}{more})", n
        r = sh(
            [
                "git",
                *util.GIT_QUIET,
                "-C",
                str(self.main_path),
                "merge-tree",
                "--write-tree",
                "--no-messages",
                "--name-only",
                self.main,
                self.branch,
            ],
            check=False,
        )
        if r.returncode == 1:
            files = sorted(set(r.stdout.splitlines()[1:]))
            return False, f"merging {self.main} conflicts in {', '.join(files) or '?'}", n
        if r.returncode:  # an old git without --write-tree, or another error: never merged blindly
            return False, f"git merge-tree could not check the merge ({(r.stderr or r.stdout).strip()[:200]})", n
        return True, f"{n} commit{'s' if n != 1 else ''}, only under {home}/", n

    def preflight(self) -> None:
        self.wt = self.worktree()
        # without a remote the home may live inside the repo, with the backlog and notes uncommitted while the
        # orchestrator works: changes under it do not make main dirty
        home = self.home_in_repo() if self.local else None
        status = ["status", "--porcelain", "--untracked-files=no"] + (["--", ".", f":(exclude){home}"] if home else [])
        if git(self.main_path, "branch", "--show-current") != self.main or git(self.main_path, *status):
            raise Stop(
                f"{self.main_path} must be on a clean {self.main}"
                + (f" (changes under the home {home}/ do not count)" if home else "")
            )
        self.dirty_home = (
            git(self.main_path, "diff", "--name-only", "HEAD", "--", home).splitlines()
            + git(self.main_path, "ls-files", "--others", "--exclude-standard", "--", home).splitlines()
            if home
            else []
        )
        if git(self.wt, "status", "--porcelain", "--untracked-files=no"):
            raise Stop(f"{self.wt} has uncommitted changes")
        if not self.a.check and not self.local:
            git(self.main_path, "fetch", "-q", self.remote)
            upstream = f"{self.remote}/{self.main}"
            if git(self.main_path, "rev-parse", self.main) != git(self.main_path, "rev-parse", upstream):
                raise Stop(f"{self.main} differs from {upstream}: sync it (git pull --ff-only) first")
        if self.a.rebase_from:
            if sh(
                [
                    "git",
                    *util.GIT_QUIET,
                    "-C",
                    str(self.main_path),
                    "rev-parse",
                    "-q",
                    "--verify",
                    f"{self.a.rebase_from}^{{commit}}",
                ],
                check=False,
            ).returncode:
                raise Stop(f"--rebase-from: {self.a.rebase_from} is not a commit")
            if self.a.check:
                self.out(f"== --check: would run git rebase --onto {self.main} {self.a.rebase_from} in {self.wt}")
                return
            self.out(f"== git rebase --onto {self.main} {self.a.rebase_from} ({self.branch})")
            if sh(
                ["git", *util.GIT_QUIET, "-C", self.wt, "rebase", "--onto", self.main, self.a.rebase_from], check=False
            ).returncode:
                raise Stop(
                    f"rebase failed: git -C {self.wt} rebase --abort, or resolve it and rerun without --rebase-from"
                )
        if sh(
            ["git", *util.GIT_QUIET, "-C", str(self.main_path), "merge-base", "--is-ancestor", self.main, self.branch],
            check=False,
        ).returncode:
            ok, why, n = self.home_only_merge()
            if not ok:
                raise Stop(
                    f"{self.branch} does not contain {self.main} and {why}: merge {self.main} into it by hand, "
                    "or --rebase-from COMMIT"
                )
            if self.a.check:
                self.out(f"== --check: would merge {self.main} ({why}) into {self.branch}: clean")
                return
            self.out(f"== git merge --no-ff --no-edit {self.main} in {self.wt} ({why})")
            r = sh(
                ["git", *util.GIT_QUIET, "-C", self.wt, "merge", "-q", "--no-ff", "--no-edit", self.main], check=False
            )
            if r.returncode:
                sh(["git", *util.GIT_QUIET, "-C", self.wt, "merge", "--abort"], check=False)
                raise Stop(
                    f"git merge {self.main} into {self.branch} failed (aborted, nothing merged): "
                    f"{(r.stderr or r.stdout).strip()[:300]}"
                )
            self.merged_main = (n, git(self.wt, "rev-parse", "--short", "HEAD"))
        if self.dirty_home:
            changed = git(self.main_path, "diff", "--name-only", f"{self.main}...{self.branch}").splitlines()
            clash = sorted(set(self.dirty_home) & set(changed))
            if clash:
                raise Stop(
                    f"{self.branch} changes files that are uncommitted in {self.main_path}: {', '.join(clash)}; "
                    "commit or set them aside there first (the fast-forward would fail)"
                )

    def risky(self) -> list[str]:
        """Risky paths the branch touches: "dir/", "file", a glob, or "file:text" (a changed line contains text)."""
        try:
            return review.paths_touched(self.main_path, self.main, self.branch, self.repo.get("risky_paths", []))
        except RuntimeError as e:
            raise Stop(str(e)) from e

    def merged_tree(self) -> str:
        """The tree main will hold after this integration: the branch's own when it contains main, else the clean
        merge of both (`git merge-tree --write-tree`, as home_only_merge; it only adds loose objects)."""
        if not sh(
            ["git", *util.GIT_QUIET, "-C", str(self.main_path), "merge-base", "--is-ancestor", self.main, self.branch],
            check=False,
        ).returncode:
            return git(self.main_path, "rev-parse", f"{self.branch}^{{tree}}")
        r = sh(
            [
                "git",
                *util.GIT_QUIET,
                "-C",
                str(self.main_path),
                "merge-tree",
                "--write-tree",
                "--no-messages",
                self.main,
                self.branch,
            ],
            check=False,
        )
        if r.returncode or not r.stdout.strip():
            raise Stop(self.s["integ_perm_error"].format(error=(r.stderr or r.stdout).strip()[:200] or "merge-tree"), 2)
        return r.stdout.split()[0]

    def live_changes(self) -> tuple[dict[str, set[str]], list[str]]:
        """What the merge changes among the reserved files (guard.reserved: every registered product's and the
        machine's), as files of the main checkout: ({product: {config, table}} the live ones it changes, [the other
        reserved files it changes, with their class]). Compares main with merged_tree(); nothing else is read when
        none of its paths is reserved."""
        self.tree = self.merged_tree()
        changed = git(self.main_path, "diff", "--no-renames", "--name-only", self.main, self.tree).splitlines()
        live: dict[str, set[str]] = {}
        other: list[str] = []
        if not changed:
            return live, other
        try:
            projects = config.registered_projects()
        except config.ConfigError:
            projects = []
        self.products = {q.name: q for q in projects}
        entries = guard.reserved(self.p, projects)
        for rel in changed:
            e = guard.reserved_entry(self.main_path / rel, entries)
            if e is None:
                continue
            if e[0] in LIVE_KINDS:
                live.setdefault(e[3] or self.p.name, set()).add(e[0])
            else:
                other.append(f"{rel} ({self.s['integ_perm_reserved'].format(kind=e[0])})")
        return live, other

    def show(self) -> None:
        self.out(f"== diff {self.main}...{self.branch}")
        self.out(git(self.main_path, "diff", "--stat", f"{self.main}...{self.branch}") or "(no change)")
        self.live, reserved = self.live_changes()
        self.touched = self.risky() + reserved  # Claude Code's settings, a state or a registry inside the repo
        if self.touched:
            self.out("== risky paths touched (the owner approves the merge): " + ", ".join(self.touched))
        if self.repo.get("pre_merge_report"):
            self.out(self.pre_merge_report())
        try:
            data = self.backlog.load()
            issue = self.backlog.issue(data, self.issue)
        except BacklogError as e:
            raise Stop(str(e)) from e
        self.github_ref = issue.get("github", "")
        plans = [p["path"] for p in issue["plan"]]
        if plans and (self.p.home / plans[0]).is_file():
            section = self.plan_check_section((self.p.home / plans[0]).read_text())
            heading = self.repo.get("plan_check_heading") or self.s["plan_check_heading"]
            self.out(f"== what the plan asks to check ({plans[0]}): compare with the branch's report before merging")
            self.out(section if section is not None else f"(no «{heading}» section)")
        else:
            self.out(f"== {self.issue} has no plan linked in the backlog (small issue: the brief was the plan)")
        if issue["subs"]:
            self.out(f"== sub-issues of {self.issue}: {backlog_pages.render_subs(self.backlog, issue['subs'])}")
        agent = next((x.get("agent") for x in issue["subs"] if x.get("branch") == self.branch), None)
        outcome = self.outcome = delivery.outcome_of(self.p, branch=self.branch, name=agent)
        if outcome:
            note = {
                "partial": ": only part of the task, read the report before merging",
                "failed": ": the agent says it failed, read the report before merging",
            }.get(outcome, "")
            self.out(f"== the agent's delivery: outcome {outcome}{note}")

    def rel(self, path: Path) -> str | None:
        """PATH relative to the main checkout, or None when it lies outside the repo."""
        try:
            return str(path.resolve().relative_to(Path(self.main_path).resolve()))
        except ValueError:
            return None

    def blob(self, rel: str) -> bytes | None:
        """The file REL (relative to the main checkout) as merged_tree() holds it, or None when it has none."""
        r = subprocess.run(
            ["git", *util.GIT_QUIET, "-C", str(self.main_path), "cat-file", "blob", f"{self.tree}:{rel}"],
            capture_output=True,
            check=False,
        )
        return None if r.returncode else r.stdout

    def judge_live(self, name: str, kinds: set[str], tmp: Path) -> dict[str, Any]:
        """The test `foreman permissions apply` makes (permissions.judge), of what main will hold for one product: its
        live foreman.toml as merged_tree() has it (else the live project), read tolerantly with the real home (an
        unknown key is a warning, O6; a bad value or a deleted file is broken: the product would load with no
        guard), against its guard table as the tree has it (one outside the repo: from the disk; a relative @DIR of
        the copy joined to the live table's folder). Returns {product, files, verdict, d (judge's dict, or None),
        error, warnings, repo_keys, also}."""
        s = self.s
        live = self.p if name == self.p.name else self.products[name]
        cfg = permissions.live_file(live)
        cfg_rel = self.rel(cfg) or str(cfg)
        out: dict[str, Any] = {"product": name, "files": [], "d": None, "error": "", "warnings": [], "repo_keys": []}
        out["also"] = []
        cand = live
        if "config" in kinds:
            out["files"].append(cfg_rel)
            data = self.blob(cfg_rel)
            if data is None:
                return {**out, "verdict": "broken", "error": s["integ_perm_deleted"].format(file=cfg_rel)}
            f = tmp / cfg.name
            f.write_bytes(data)
            try:
                cand = config.load_project(live.home, live.mode, config_file=f)
            except config.ConfigError as e:
                return {**out, "verdict": "broken", "error": str(e).replace(str(f), cfg_rel)}
            out["warnings"] = [w for w in cand.warnings if w not in live.warnings]
            out["repo_keys"] = config.unknown_repo_keys(cand)
        table = permissions.table_of(cand) or permissions.table_of(live)
        shown, copy = None, None
        if table is not None:
            t_rel = self.rel(table)
            shown = f"{self.branch}:{t_rel}" if t_rel else str(table)
            copy = str(table)
            if t_rel is not None:
                if "table" in kinds:
                    out["files"].append(t_rel)
                (tmp / "table").mkdir()
                c = tmp / "table" / table.name
                data = self.blob(t_rel)
                if data is not None:  # none: the table does not read, as for apply
                    c.write_bytes(data)
                copy = str(c)
        d, verdict = permissions.judge(live, cand, copy, table.parent if table is not None else None)
        d["candidate"] = f"{self.branch}:{cfg_rel}" if "config" in kinds else str(cfg)
        d["table"] = shown
        if d["table_error"] and copy:
            d["table_error"] = d["table_error"].replace(copy, shown or copy)
        out["also"] = permissions.also_changes(live, cand)
        return {**out, "verdict": verdict, "d": d}

    def permission_changes(self) -> list[dict[str, Any]]:
        """judge_live() for each product whose live foreman.toml or guard table the merge changes (live_changes),
        in a temporary folder that is never the home nor <state>; [] when it changes none. A failure of the test
        itself stops (exit 2): never merged untested."""
        out = []
        for name, kinds in sorted(self.live.items()):
            try:
                with tempfile.TemporaryDirectory(prefix="foreman-integrate-") as tmp:
                    out.append(self.judge_live(name, kinds, Path(tmp)))
            except Stop:
                raise
            except Exception as e:
                raise Stop(self.s["integ_perm_error"].format(error=f"{type(e).__name__}: {e}"), 2) from e
        return out

    def permissions_gate(self) -> None:
        """Step 2b (module doc): the section on the live permissions the branch changes, and its cut-offs, before
        any CI: broken exit 2, fails exit 1, opens without --opens exit 3 (the owner's yes); with --check only
        what it would do."""
        s, a = self.s, self.a
        judged = self.permission_changes()
        if not judged:
            if a.opens:
                self.out("== " + s["integ_perm_opens_unused"])
            return
        verdict = min((j["verdict"] for j in judged), key=permissions.VERDICTS.index)
        owner = self.p.owner
        for j in judged:
            self.out(
                "== "
                + s["integ_perm_head"].format(
                    branch=self.branch, files=", ".join(j["files"]), product=j["product"], main=self.main
                )
            )
            if j["d"] is not None:
                self.out(permissions.render_diff(self.p, j["d"]))
                if j["d"]["opens"]:
                    self.out("\n".join([s["perm_opens"]] + [f"  {x}" for x in j["d"]["opens"]]))
            else:
                self.out(s["integ_perm_broken"].format(error=j["error"]))
            for w in j["warnings"]:
                self.out(s["integ_perm_warning"].format(warning=w))
            if j["repo_keys"]:
                keys = "; ".join(f"[repos.{k}] {', '.join(v)}" for k, v in j["repo_keys"])
                self.out(s["integ_perm_repo_keys"].format(keys=keys))
            if j["also"]:
                self.out(s["integ_perm_also"].format(keys=", ".join(j["also"])))
        rc = {"broken": 2, "fails": 1}.get(verdict)
        if a.check:
            if rc:
                self.out("== " + s["integ_perm_check_stop"].format(rc=rc))
            elif verdict == "opens":
                self.out("== " + s["integ_perm_check_opens"].format(owner=owner, given=" (--opens)" if a.opens else ""))
            else:
                self.out("== " + s["integ_perm_ok"])
            return
        if verdict == "broken":
            error = "; ".join(
                j["error"] or (j["d"] or {}).get("table_error") or "" for j in judged if j["verdict"] == "broken"
            )
            raise Stop(s["integ_perm_broken_stop"].format(branch=self.branch, error=error), 2)
        if verdict == "fails":
            raise Stop(s["integ_perm_fails_stop"].format(branch=self.branch), 1)
        if verdict == "opens" and not a.opens:
            notes.progress(
                self.p,
                s["integ_perm_progress"].format(repo=self.repo["key"], branch=self.branch, owner=owner),
                who="orchestrator",
            )
            raise Stop(s["integ_perm_opens_stop"].format(branch=self.branch, owner=owner), AWAITING)
        if verdict == "opens":
            self.out("== " + s["integ_perm_opens_given"].format(owner=owner))
        else:
            self.out("== " + s["integ_perm_ok"])
            if a.opens:
                self.out("== " + s["integ_perm_opens_unused"])
        parts = []
        for j in judged:
            d = j["d"]
            parts.append(
                s["integ_perm_summary"].format(
                    product=j["product"],
                    rules=len(d["rules"]),
                    rows=len(d["rows"]),
                    added=d["table_added"],
                    removed=d["table_removed"],
                )
                + (
                    "; " + s["integ_perm_note_opens"].format(opens="; ".join(d["opens"]), owner=owner)
                    if d["opens"]
                    else ""
                )
            )
        before = git(self.main_path, "rev-parse", "--short", self.main)
        shows = ", ".join(f"`git show {before}:{f}`" for j in judged for f in j["files"])
        self.perm_note = s["integ_perm_note"].format(summary="; ".join(parts), previous=shows)

    def values(self, **kw: Any) -> dict[str, Any]:
        """The values of a repo step's placeholders and FOREMAN_* variables."""
        return {"repo": self.repo["key"], "main": self.main_path, "branch": self.branch, **kw}

    def command(self, point: str, key: str, values: Mapping[str, Any], **kw: Any) -> tuple[str, commands.Result]:
        """Runs the repo's `key` at `point` through commands.run with its placeholders replaced."""
        cmd = commands.expand(point, self.repo[key], values)
        return cmd, commands.run(point, cmd, project=self.p, values=values, **kw)

    def pre_merge_report(self) -> str:
        """The `pre_merge_report` section: its stdout, and its rc and stderr when it fails or is cut (300 s)."""
        cmd, r = self.command("repo.pre_merge_report", "pre_merge_report", self.values(wt=self.wt), cwd=self.main_path)
        text = f"== pre-merge report ({cmd})\n{r.stdout.strip() or '(empty)'}"
        if r.timed_out:
            text += f"\n(pre_merge_report cut after {commands.POINTS['repo.pre_merge_report'].timeout:g} s)"
        elif r.rc != 0:  # None: it could not start
            text += f"\n(pre_merge_report failed, rc {r.rc})" + (
                f": {r.stderr.strip()[-1000:]}" if r.stderr.strip() else ""
            )
        return text

    def scan_public(self, label: str, text: str | None, detail: bool = True) -> None:
        """Stops when a public repo's `label` text carries an internal ID or fails `privacy_scan`; `detail` False
        leaves the scan's stderr out of the message (it may quote the text)."""
        if not self.repo.get("public") or not text:
            return
        if plain.ids_re(self.p).search(text):  # glossed or not: a public repo carries no internal id
            raise Stop(f"stopped: the {label} carries an internal ID; rewrite it for a public repo")
        if self.repo.get("privacy_scan"):
            _, r = self.command(
                "repo.privacy_scan", "privacy_scan", {"repo": self.repo["key"]}, cwd=self.p.home, stdin=text
            )
            if r.timed_out:  # fails closed
                limit = commands.POINTS["repo.privacy_scan"].timeout
                raise Stop(f"stopped: the privacy scan of the {label} did not end in {limit:g} s")
            if r.rc != 0:  # None (it could not start) fails closed too
                why = f" ({r.stderr.strip()[:200]})" if detail else ""
                raise Stop(f"stopped: the {label} fails the privacy scan{why}")

    def scan_commit_messages(self) -> None:
        """A public repo's PR shows the branch's commit messages forever, squash or not: each one passes the same
        scan as the title and body before anything is pushed. The stop names the commit and the rule, never the
        matched text."""
        if not self.repo.get("public"):
            return
        log = git(self.wt, "log", "--format=%H%x00%B%x1e", f"{self.main}..{self.branch}")
        for entry in log.split("\x1e"):
            sha, _, message = entry.strip().partition("\x00")
            if not sha:
                continue
            try:
                self.scan_public(f"message of commit {sha[:12]}", message, detail=False)
            except Stop as e:
                raise Stop(f"{e.args[0]} (git rebase -i {self.main}, then reword): nothing pushed") from None

    def warn_owner_texts(self) -> None:
        """Warnings on stderr, never blocking, for what reaches the owner: the PR title and body and the result line
        (docs/owner.md). A public repo's ids are blocked by scan_public."""
        body = self.a.body
        if self.a.body_file and not body:
            try:
                with open(self.a.body_file) as f:
                    body = f.read()
            except OSError:
                body = ""  # body() reports it
        for label, text in (("PR title", self.a.title), ("PR body", body), ("result line", self.a.result)):
            plain.warn(self.p, label, text)

    def body(self) -> str:
        body = self.a.body or ""
        if self.a.body_file:
            if body:
                raise Stop("--body and --body-file are exclusive", 2)
            with open(self.a.body_file) as f:
                body = f.read()
        slug = self.repo.get("github")
        if self.github_ref and slug and self.github_ref.startswith(slug + "#"):
            close = f"Closes #{self.github_ref.split('#', 1)[1]}"
            if close not in body:
                body = body.rstrip() + f"\n\n{close}"
        return body

    def ci_runs(self) -> list[str]:
        """The runs of the local CI, each the value of {ci}: the --ci options if given, else the repo's ci_runs."""
        return list(self.a.ci) if self.a.ci is not None else list(self.repo.get("ci_runs") or [""])

    TEMPLATES: tuple[tuple[str, str], ...] = (
        ("local_ci", "repo.local_ci"),
        ("main_ci", "repo.main_ci"),
        ("ci_summary", "repo.ci_summary"),
        ("pre_merge_report", "repo.pre_merge_report"),
        ("post_merge", "repo.post_merge"),
    )

    def check_templates(self) -> None:
        """Every repo step uses only its point's placeholders (exit 2 before anything is done: after the merge a bad
        main_ci or post_merge could not run)."""
        for key, point in self.TEMPLATES:
            if isinstance(self.repo.get(key), str):
                try:
                    commands.expand(point, self.repo[key])
                except commands.PlaceholderError as e:
                    raise Stop(f"[repos.{self.repo['key']}] {key}: {e}: nothing done", 2) from e

    def check_ci_options(self) -> None:
        """--ci and ci_runs need {ci} in local_ci (exit 2, before anything is done)."""
        if self.a.ci is not None and self.a.no_ci:
            raise Stop("--ci and --no-ci are exclusive", 2)
        cmd = self.repo.get("local_ci") or ""
        if "{ci}" in cmd:
            return
        if self.a.ci is not None:
            raise Stop(
                f"--ci given but [repos.{self.repo['key']}] local_ci has no {{ci}} placeholder: nothing done "
                "(the arguments are never appended blindly)",
                2,
            )
        if any(self.ci_runs()) and cmd and not self.a.no_ci:
            raise Stop(
                f"[repos.{self.repo['key']}] ci_runs has arguments but local_ci has no {{ci}} placeholder: "
                "nothing done",
                2,
            )

    def ci_log(self) -> Path:
        return self.p.state / "integrate" / f"{self.branch.replace('/', '-')}-ci.log"

    @staticmethod
    def label(ci: str) -> str:
        return f"`{ci}`" if ci else "(no arguments)"

    def run_logged(
        self, point: str, cmd: str, values: Mapping[str, Any], where: PathLike, log: PathLike
    ) -> tuple[int, str, int]:
        """Runs CMD in WHERE, its output (stdout and stderr) appended to LOG as it comes, without a time limit.
        (rc, output, seconds)."""
        with open(log, "a") as f:
            f.write(f"== {time.strftime('%Y-%m-%d %H:%M:%S')} in {where}: {cmd}\n")
        r = commands.run(point, cmd, project=self.p, values=values, cwd=where, log=log)
        secs = int(r.seconds)
        with open(log, "a") as f:
            f.write(f"== rc {r.rc} in {secs} s\n" if r.rc is not None else f"== could not start: {r.stderr}\n")
        return (r.rc if r.rc is not None else 127), r.stdout or r.stderr, secs

    def summary(self, ci: str, output: str, where: PathLike) -> str:
        """The `ci_summary` line of one run (its stdout, the run's output on stdin), as a bullet."""
        _, r = self.command("repo.ci_summary", "ci_summary", self.values(wt=where, ci=ci), cwd=where, stdin=output)
        if r.timed_out:
            cap = commands.POINTS["repo.ci_summary"].timeout
            self.out(f"ci_summary cut after {cap:g} s for {self.label(ci)}")
            text = f"(ci_summary cut after {cap:g} s)"
        elif r.rc != 0:
            self.out(f"ci_summary failed for {self.label(ci)} (rc {r.rc}): {r.stderr.strip()[:200]}")
            text = f"(ci_summary failed, rc {r.rc})"
        else:
            text = "\n  ".join(ln.rstrip() for ln in r.stdout.strip().splitlines()) or "(empty)"
        return f"- {self.label(ci)}: {text}" if ci else f"- {text}"

    def local_ci(self, where: PathLike | None = None, tmpl: str | None = None, name: str = "local CI") -> str:
        """Runs the local CI in the worktree (or in `where`, the main checkout after a fast-forward): one run per
        value of ci_runs (or --ci), in order; the first failure stops. Returns the line for the note and the PR
        comment, with the `ci_summary` bullets under it (only in the worktree of a repo with a PR). With `tmpl`
        (main_ci, on main) that command runs instead: once per run if it uses {ci}, once otherwise."""
        if tmpl is None:
            tmpl = self.repo.get("local_ci")
            if not tmpl or self.a.no_ci:
                return "local CI: " + ("already passed on this commit (--no-ci)" if self.a.no_ci else "none configured")
        where = where or self.wt
        runs = self.ci_runs() if name == "local CI" or "{ci}" in tmpl else [""]
        if where == self.wt:  # the commit the runs test (main_ci applies on main only when main is this commit)
            self.ci_passed_on = git(where, "rev-parse", "HEAD")
        log = self.ci_log()
        log.parent.mkdir(parents=True, exist_ok=True)
        if not getattr(self, "ci_log_open", False):  # one log per integration: the runs on main are appended
            log.write_text("")
            self.ci_log_open = True
        done: list[tuple[str, int, str]] = []
        point = "repo.local_ci" if name == "local CI" else "repo.main_ci"
        for n, ci in enumerate(runs, 1):
            values = self.values(wt=where, ci=ci)
            cmd = commands.expand(point, tmpl, values)
            which = f" (run {n}/{len(runs)}, {self.label(ci)})" if len(runs) > 1 or ci else ""
            self.out(f"== {name} in {where}{which}: {cmd} (log: {log})")
            rc, output, secs = self.run_logged(point, cmd, values, where, log)
            if rc:
                sys.stderr.write(output[-4000:])
                merged = (
                    f"; {self.main} merged into {self.branch} as {self.merged_main[1]}, nothing merged into {self.main}"
                    if self.merged_main
                    else ": nothing merged"
                )
                raise Stop(
                    f"{name} failed in {where}{which} (rc {rc}, {secs} s)"
                    + ("" if where != self.wt else merged if self.local else ": nothing pushed")
                )
            sys.stdout.write(output[-4000:])
            done.append((ci, secs, output))
        sha = git(where, "rev-parse", "--short", "HEAD")
        title = name[0].upper() + name[1:] if name == "local CI" else name
        if runs == [""]:
            text = f"{title} passed on {sha} in {done[0][1]} s."
        else:
            text = (
                f"{title} passed on {sha}; runs: "
                + ", ".join(f"{self.label(ci)} ({secs} s)" for ci, secs, _ in done)
                + "."
            )
        if self.repo.get("ci_summary") and not self.local and where == self.wt:
            text += "\n\n" + "\n".join(self.summary(ci, output, where) for ci, _, output in done)
        return text

    def ci_on_main(self, head: str) -> str:
        """The CI in the main checkout after the fast-forward to `head`: main_ci when it is set and main is exactly
        the commit the local CI passed on in the worktree (nothing when it is ""), the full local CI otherwise
        (main_ci unset, or main is another commit). Returns the text for the note after «On main: »."""
        main_ci = self.repo.get("main_ci")
        if main_ci is None or self.a.no_ci or not self.repo.get("local_ci"):
            return self.local_ci(self.main_path)
        passed = getattr(self, "ci_passed_on", None)
        if head != passed:
            self.out(
                f"== {self.main} is at {head[:7]}, not {(passed or '?')[:7]} where the local CI passed: the full "
                "local CI on it instead of main_ci"
            )
            return (
                self.local_ci(self.main_path) + f" (full local CI, not main_ci: {self.main} was not the commit the "
                "CI passed on)"
            )
        if main_ci == "":
            self.out(
                f"== main_ci is empty: nothing runs on {self.main} ({head[:7]}, the commit the local CI passed on)"
            )
            return f"nothing run (main_ci is empty; {head[:7]} is the commit the local CI passed on)."
        return self.local_ci(self.main_path, main_ci, "main_ci")

    def main_ci_plan(self) -> str:
        """What --check says runs on main after the fast-forward."""
        main_ci = self.repo.get("main_ci")
        if main_ci is None:
            return f", then the same on {self.main} after the fast-forward"
        if main_ci == "":
            return (
                f", then nothing on {self.main} after the fast-forward (main_ci is empty; the same runs if "
                f"{self.main} is not the commit that passed)"
            )
        return (
            f", then main_ci on {self.main} after the fast-forward (the same runs if {self.main} is not the "
            f"commit that passed): {commands.expand('repo.main_ci', main_ci, self.values(wt=self.main_path))}"
        )

    def ci_plan(self) -> str:
        """What --check says about the local CI: each run it would do, where, and the summary."""
        tmpl = self.repo.get("local_ci")
        if self.a.no_ci:
            return "== local CI: skipped (--no-ci)"
        if not tmpl:
            return "== local CI: none configured"
        runs = self.ci_runs()
        src = "--ci" if self.a.ci is not None else "ci_runs" if self.repo.get("ci_runs") != [""] else "default"
        lines = [
            f"== local CI: {len(runs)} run{'s' if len(runs) != 1 else ''} ({src}) in {self.wt}"
            + (self.main_ci_plan() if self.local else "")
            + f"; log: {self.ci_log()}"
        ]
        for n, ci in enumerate(runs, 1):
            lines.append(f"   {n}. {commands.expand('repo.local_ci', tmpl, self.values(wt=self.wt, ci=ci))}")
        if self.repo.get("main_ci") is not None and not self.local:
            lines.append("== main_ci does not apply with a remote (the merge happens on the remote, after its checks)")
        if self.repo.get("ci_summary"):
            lines.append(
                "== ci_summary does not apply without a remote (no PR comment)"
                if self.local
                else f"== ci_summary: {self.repo['ci_summary']} (one bullet per run in the PR comment)"
            )
        return "\n".join(lines)

    def post_merge_cmd(self, before: str = "{before}", head: str = "{head}") -> str:
        """The repo's post_merge with its placeholders replaced ({before} and {head} kept for --check), or ""."""
        tmpl = self.repo.get("post_merge") or ""
        return commands.expand("repo.post_merge", tmpl, self.values(before=before, head=head)) if tmpl else ""

    def post_merge(self, before: str, head: str) -> str:
        """Runs post_merge in the main checkout once the branch is in main. Returns the text for the note ("" when
        not set). A failure is a warning and a progress line: the merge is done and never undone. It runs with
        POST_MERGE_SECONDS as its time limit (FOREMAN_POST_MERGE_S only for the tests): cut, it is a failure that
        says so."""
        if not self.repo.get("post_merge"):
            return ""
        cmd = self.post_merge_cmd(before, head)
        self.out(f"== post_merge in {self.main_path} ({before[:7]}..{head[:7]}): {cmd}")
        cap = float(os.environ.get("FOREMAN_POST_MERGE_S") or POST_MERGE_SECONDS)
        r = commands.run(
            "repo.post_merge",
            cmd,
            project=self.p,
            values=self.values(before=before, head=head),
            cwd=self.main_path,
            timeout=cap,
        )
        cut = r.timed_out
        rc = r.rc if r.rc is not None else 127
        output = (r.stdout + (r.stderr if rc else "")).rstrip()
        if output:
            self.out(output[-4000:])
        last = next((ln.strip() for ln in reversed(output.splitlines()) if ln.strip()), "")[:300]
        if not rc:
            return f"post_merge: {last or 'ok, no output'}"
        if cut:
            warn = (
                f"post_merge cut after {cap:g} s after merging {self.repo['key']}:{self.branch} into {self.main} "
                f"{head[:7]}; the merge stands, run it by hand"
            )
            self.out(f"warning: {warn}")
            notes.progress(self.p, warn, who="orchestrator")
            return f"post_merge cut after {cap:g} s (time limit)"
        warn = (
            f"post_merge failed (rc {rc}) after merging {self.repo['key']}:{self.branch} into "
            f"{self.main} {head[:7]}" + (f": {last}" if last else "") + "; the merge stands, run it by hand"
        )
        self.out(f"warning: {warn}")
        notes.progress(self.p, warn, who="orchestrator")
        return f"post_merge failed (rc {rc})" + (f": {last}" if last else "")

    def merge_local(self) -> tuple[str, str, str, str]:
        """Fast-forwards main to the branch, runs the CI on main (ci_on_main) and removes the worktree and branch."""
        before = git(self.main_path, "rev-parse", self.main)
        self.out(f"== git merge --ff-only {self.branch} (no remote)")
        if sh(
            ["git", *util.GIT_QUIET, "-C", str(self.main_path), "merge", "-q", "--ff-only", self.branch], check=False
        ).returncode:
            raise Stop(f"git merge --ff-only {self.branch} failed in {self.main_path}: nothing merged")
        head = git(self.main_path, "rev-parse", self.main)
        try:
            ci_main = self.ci_on_main(head)
        except Stop as e:
            # back only if nobody moved main meanwhile; --keep never discards uncommitted changes (the home's)
            if (
                git(self.main_path, "rev-parse", self.main) == head
                and not sh(
                    ["git", *util.GIT_QUIET, "-C", str(self.main_path), "reset", "-q", "--keep", before], check=False
                ).returncode
            ):
                raise Stop(
                    f"{e.args[0]} after the fast-forward: {self.main} is back at {before[:7]}; "
                    f"{self.branch} and its worktree untouched"
                ) from e
            raise Stop(
                f"{e.args[0]} after the fast-forward, and {self.main} could not go back to {before[:7]}: "
                f"look at {self.main_path} by hand (nothing forced)"
            ) from e
        git(self.main_path, "worktree", "remove", "--force", self.wt)
        git(self.main_path, "branch", "-D", self.branch)
        short = git(self.main_path, "rev-parse", "--short", head)
        self.out(f"== merged {self.branch} into {self.main} as {short} (fast-forward); worktree and branch removed")
        return short, ci_main, before, head

    def open_pr(self, title: str, body: str) -> tuple[str, str]:
        self.out(f"== push {self.branch} and open the PR")
        # a push keeps its hooks (pre-push: git-lfs and the like), the only git here without util.GIT_QUIET
        sh(["git", "-C", self.wt, "push", "-q", "-u", self.remote, self.branch])
        r = self.gh("pr", "view", self.branch, "--json", "number,url,state", check=False)
        if r.returncode == 0:
            import json

            info = json.loads(r.stdout or "{}")
            if info.get("state") == "OPEN":
                self.out(f"== PR #{info['number']} already open: {info['url']}")
                return str(info["number"]), info["url"]
        url = (
            self.gh("pr", "create", "--base", self.main, "--head", self.branch, "--title", title, "--body", body)
            .stdout.strip()
            .splitlines()[-1]
        )
        self.out(f"== PR #{url.rsplit('/', 1)[-1]}: {url}")
        return url.rsplit("/", 1)[-1], url

    def remote_checks(self, pr: str) -> None:
        if self.repo.get("remote_checks", "required") != "required":
            return
        self.out("== remote checks")
        for tries in range(6):
            r = self.gh("pr", "checks", pr, "--watch", check=False)
            out = (r.stdout + r.stderr).strip()
            if r.returncode == 0:
                self.out(out)
                return
            if "no checks reported" in out and tries < 5:
                time.sleep(float(os.environ.get("FOREMAN_CHECKS_RETRY_S", "10")))
                continue
            self.out(out)
            raise Stop("remote checks failed: the PR stays open")

    def review_gate(self) -> None:
        """Whether the branch's review lets it in (review.state(): only `not needed` and `clean` pass), with a line
        «== review: …» when the repo's `review` is auto. Otherwise it stops, nothing done (exit 2), saying the next
        step; --check stops the same way. --no-review "why" lets in a branch whose only reason is its size and that
        has no review yet (a mechanical split proven by its tests), never one for delicate paths nor a partial
        delivery; after round 2 with H or M open only the owner's yes (--approved) lets it in as it is. Both go into
        the note."""
        a, ref = self.a, f"{self.repo['key']}:{self.branch}"
        if review.mode(self.repo) != "auto":
            if a.no_review is not None:
                self.out(f"== --no-review ignored: review is off for {self.repo['key']}")
            return
        try:
            st = review.state(self.p, self.repo, self.branch, self.outcome)
        except RuntimeError as e:
            raise Stop(f"review: {e}", 2) from e
        self.out(f"== review: {st.line()}")
        if st.passes:
            if a.no_review is not None:
                self.out(f"== --no-review ignored: the review is {st.status}")
            if st.status == review.CLEAN and st.review:
                self.review_note = f"review clean: {st.review.path}, round {st.review.round}"
            return
        if a.no_review is not None:
            why = a.no_review.strip()
            if st.size_only and st.status == review.NEEDED and why:
                self.review_note = f"no review, size only ({st.reasons[0].text}): {why}"
                self.out(f"== --no-review: {ref} goes in without a review ({why}); the note says so")
                return
            raise Stop(
                f"--no-review is only for a branch whose one reason is its size and that has no review yet, with "
                f"why: {ref} is {st.line()}; nothing done",
                2,
            )
        if st.status == review.OWNER and a.approved and st.review:
            self.review_note = f"review: the owner lets it in with {st.review.counts()} open ({st.review.path})"
            self.out(f"== review: the owner's yes ({a.approved}) lets it in with {st.review.counts()} open")
            return
        nxt = {
            review.NEEDED: f"a reviewer agent that did not write it reviews it (round {st.round}): its review under "
            f"the reviews folder starts with «Branch: {ref}», «Tip: SHA», «Round: {st.round}», «Open: H n, M n»",
            review.IN_REVIEW: "wait for its review",
            review.FINDINGS: "send the findings back to the agent that made the branch: it fixes or refutes each H "
            "and M with proof; then round 2 reviews the fixes",
            review.FIXED: f"round {review.MAX_ROUNDS}: the reviewer checks the fixes on the new tip",
            review.OWNER: "no third round: ask the owner (a question); the branch waits for the answer, and their "
            'yes to let it in as it is goes in --approved "who, when"',
        }[st.status]
        raise Stop(f"{ref} needs its review first ({st.line()}): {nxt}; nothing done", 2)

    def needs_approval(self) -> list[str]:
        why: list[str] = []
        if self.repo.get("require_approval"):
            why.append("this repo merges only with the owner's approval")
        if self.repo.get("publish_on_merge"):
            why.append("merging this repo publishes")
        if self.touched:
            why.append("risky paths: " + ", ".join(self.touched))
        return why

    def merge(self, pr: str, title: str, body: str) -> str:
        method = self.repo.get("merge", "squash")
        self.out(f"== merge ({method})")
        args = ["pr", "merge", pr, f"--{method}", "--match-head-commit", git(self.wt, "rev-parse", "HEAD")]
        if method in ("squash", "merge"):
            args += ["--subject", title, "--body", body]
        self.gh(*args)
        self.before = git(self.main_path, "rev-parse", self.main)
        git(self.main_path, "pull", "-q", "--ff-only")
        oid = self.gh("pr", "view", pr, "--json", "mergeCommit", "--jq", ".mergeCommit.oid").stdout.strip()
        if sh(
            ["git", *util.GIT_QUIET, "-C", str(self.main_path), "merge-base", "--is-ancestor", oid, self.main],
            check=False,
        ).returncode:
            raise Stop(f"merge commit {oid} is not in local {self.main}")
        head = git(self.main_path, "rev-parse", "--short", oid)
        # GitHub may have deleted it on merge already: only a branch that is still there is deleted (and warned about)
        gone = (
            sh(
                [
                    "git",
                    *util.GIT_QUIET,
                    "-C",
                    str(self.main_path),
                    "ls-remote",
                    "--exit-code",
                    "--heads",
                    self.remote,
                    f"refs/heads/{self.branch}",
                ],
                check=False,
            ).returncode
            == 2
        )
        if (
            not gone
            and sh(  # a push: with its hooks, as in open_pr
                ["git", "-C", str(self.main_path), "push", "-q", self.remote, "--delete", self.branch], check=False
            ).returncode
        ):
            self.out(f"could not delete {self.remote}/{self.branch}: delete it by hand")
        git(self.main_path, "worktree", "remove", "--force", self.wt)
        git(self.main_path, "branch", "-D", self.branch)
        self.out(f"== merged {self.branch} as {head} (PR #{pr}); remote branch, worktree and local branch removed")
        return head

    def validate(self, commit: str = "<merge>") -> list[str]:
        """Everything the backlog close would reject (verdict, issue, sub-issue, report path, an outcome without
        its criterion), checked before any effect: a dry run of `backlog integrated` on a fresh load, plus the
        verdict even when this integration does not close the issue. Returns the dry run's lines."""
        try:
            self.backlog.norm_verdict(self.a.verdict)
            did, _ = self.backlog.integrated(
                self.backlog.load(),
                self.issue,
                self.sub,
                commit,
                self.branch,
                self.a.report or "",
                self.a.result or "",
                self.a.verdict or "",
            )
        except BacklogError as e:
            raise Stop(f"nothing done, the backlog close would fail: {e}", 2) from e
        return did

    def close(self, how: str, head: str, ci_text: str, post: str = "") -> None:
        notes.note(
            self.p,
            f"Integrated {self.repo['key']}:{self.branch} ({self.ref()})",
            (self.a.note or f"Merged into {self.main} {how}. {self.a.message}")
            + f" ({self.main} {head}; {ci_text}"
            + (f"; approved: {self.a.approved}" if self.a.approved else "")
            + (f"; {self.perm_note}" if self.perm_note else "")
            + (f"; {self.review_note}" if self.review_note else "")
            + (f"; {post}" if post else "")
            + ")",
        )
        self.close_backlog(head)

    def ref(self) -> str:
        return f"{self.issue}{' ' + self.sub if self.sub else ''}"

    # ---------- measurements (--measure) ----------

    def targets(self) -> list[dict[str, Any]]:
        """The sub-issues this integration closes: the ones given («ID Tn», --sub), else the ones naming the branch
        (as `backlog integrated`); [] when the backlog cannot be read or the issue has none."""
        try:
            subs = self.backlog.issue(self.backlog.load(), self.issue)["subs"]
        except (BacklogError, OSError, ValueError):
            return []
        sids = [x for x in (self.sub or "").split(",") if x]
        if sids:
            return [s for s in subs if s["id"] in sids]
        return [s for s in subs if s.get("branch", "").strip("`") == self.branch]

    def measured(self, s: Mapping[str, Any]) -> bool:
        """Whether a sub-issue is a measurement: `kind = "measure"`, or its agent's last delivery said `--kind
        measure` (delivery.kind_of)."""
        agent = s.get("agent")
        return s.get("kind") == "measure" or bool(agent and delivery.kind_of(self.p, name=agent) == "measure")

    def refuse_measure_as_code(self) -> None:
        """Case h: a merge of sub-issues that are all `kind = "measure"` stops (exit 2), so a measurement branch's lab
        code never goes into main by mistake; one only the delivery called a measurement gets a warning."""
        subs = self.targets()
        if not subs:
            return
        names = ", ".join(f"{self.issue} {s['id']}" for s in subs)
        if all(s.get("kind") == "measure" for s in subs):
            first = subs[0]["id"]
            raise Stop(
                f"{names} {'is a measurement' if len(subs) == 1 else 'are measurements'}: close "
                f"{'it' if len(subs) == 1 else 'them'} with --measure (no merge), or mark "
                f"{'it' if len(subs) == 1 else 'each'} as code (`foreman backlog sub {self.issue} {first} --kind "
                "code`) to merge its branch",
                2,
            )
        if all(self.measured(s) for s in subs):
            self.out(
                f"== warning: the agent delivered {names} as a measurement (`progress --done --kind measure`): "
                "read the report; if nothing is for main, close it with --measure instead"
            )

    def measure_report(self) -> tuple[str, Path | None]:
        """Where the measurement's report is, relative to the home: ("home", None) when the main checkout has it,
        ("branch", None) when the branch adds it (home inside the repo), ("worktree", path) when it is only an
        uncommitted file in the worktree's home; stops (exit 2) when there is none: a measurement closes with its
        report."""
        r = self.a.report
        if not r:
            raise Stop(
                "a measurement closes with its report: none on the sub-issue nor named at delivery; pass "
                "--report reviews/… (or ask the agent for it)",
                2,
            )
        if (self.p.home / r).is_file():
            return "home", None
        if self.branch_adds(r):
            return "branch", None
        if self.in_worktree(r):
            wh = delivery.worktree_home(self.p, self.worktree())
            assert wh is not None
            return "worktree", wh / r
        raise Stop(f"--report {r} does not exist (in the home, added by {self.branch} or in its worktree)", 2)

    def run_measure(self) -> int:
        """`--measure`: closes a measurement without merging anything. Step 0 as always (the backlog dry run, the
        report); the sub-issues must be measurements; the worktree must be clean (untracked files too: it is
        removed without --force); no CI, no merge, no push, no PR. A report the branch adds or that lies only in
        the worktree is copied into the home first (uncommitted, like the backlog and the notes); then the worktree
        is removed, the branch deleted (unless --keep-branch; a remote branch is never touched), the note «Measured
        …» written and the sub-issues closed without a commit."""
        a = self.a
        subs = self.targets()
        if subs and not all(self.measured(s) for s in subs):
            code = ", ".join(f"{self.issue} {s['id']}" for s in subs if not self.measured(s))
            raise Stop(
                f"not a measurement ({code}): drop --measure to merge it, or mark it with `foreman backlog sub "
                f"{self.issue} {next(s['id'] for s in subs if not self.measured(s))} --kind measure`",
                2,
            )
        if not subs:  # the report is closed on a sub-issue (backlog integrated)
            raise Stop(f"no sub-issue of {self.issue} names {self.branch}: pass it («ID Tn»)", 2)
        where, src = self.measure_report()
        self.warn_owner_texts()
        did = self.validate(commit="")
        self.wt = self.worktree()
        dirty = [
            ln
            for ln in git(self.wt, "status", "--porcelain", "--untracked-files=all").splitlines()
            if not (src is not None and ln[3:] and (Path(self.wt) / ln[3:].strip('"')).resolve() == src.resolve())
        ]
        if dirty:
            more = f" and {len(dirty) - 5} more" if len(dirty) > 5 else ""
            raise Stop(
                f"{self.wt} has uncommitted or untracked files ({', '.join(d[3:] for d in dirty[:5])}{more}): "
                "commit, move or delete them first (the worktree is removed without --force)"
            )
        ahead = int(git(self.main_path, "rev-list", "--count", f"{self.main}..{self.branch}") or 0)
        tip = git(self.main_path, "rev-parse", "--short", self.branch)
        head = git(self.main_path, "rev-parse", "--short", self.main)
        self.out(f"== measurement {self.repo['key']}:{self.branch} ({self.ref()}): nothing is merged, no CI")
        self.out(
            f"== report: {a.report}"
            + {
                "home": "",
                "branch": " (copied from the branch into the home)",
                "worktree": " (copied from the worktree into the home)",
            }[where]
        )
        outcome = delivery.outcome_of(self.p, branch=self.branch, name=next((s.get("agent") for s in subs), None))
        if outcome:
            self.out(f"== the agent's delivery: outcome {outcome}")
        drop = (
            f"kept ({tip})"
            if a.keep_branch
            else f"deleted (tip {tip}; {ahead} commit{'s' if ahead != 1 else ''} not in {self.main})"
        )
        if a.check:
            self.out("== backlog after closing (dry run):")
            for d in did or ["nothing to change"]:
                self.out("would do: " + d)
            self.out(f"== --check: would remove the worktree {self.wt}; branch {self.branch} {drop}")
            return 0
        if where == "branch":
            rel = f"{self.home_in_repo()}/{a.report}"
            dest = self.p.home / a.report
            dest.parent.mkdir(parents=True, exist_ok=True)
            shown = subprocess.run(
                ["git", *util.GIT_QUIET, "-C", str(self.main_path), "show", f"{self.branch}:{rel}"],
                capture_output=True,
                check=False,
            )
            if shown.returncode:
                raise Stop(f"could not read {rel} from {self.branch}, nothing closed")
            dest.write_bytes(shown.stdout)
        elif where == "worktree" and src is not None:
            dest = self.p.home / a.report
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(src.read_bytes())
            src.unlink()  # copied into the home: the worktree is clean for its removal
        r = sh(["git", *util.GIT_QUIET, "-C", str(self.main_path), "worktree", "remove", self.wt], check=False)
        if r.returncode:
            raise Stop(f"git worktree remove {self.wt} failed, nothing closed: {(r.stderr or r.stdout).strip()[:300]}")
        if not a.keep_branch:
            git(self.main_path, "branch", "-D", self.branch)
        self.out(f"== worktree {self.wt} removed; branch {self.branch} {drop}")
        notes.note(
            self.p,
            f"Measured {self.repo['key']}:{self.branch} ({self.ref()})",
            (a.note or f"Closed as a measurement, nothing merged. {a.message}".rstrip())
            + f" ({self.main} {head} unchanged; report {a.report}; branch {drop})",
        )
        self.close_backlog("")
        return 0

    def close_backlog(self, head: str) -> None:
        self.out("== backlog (foreman backlog integrated):")
        try:
            with self.backlog.mutate() as data:
                did, doubtful = self.backlog.integrated(
                    data,
                    self.issue,
                    self.sub,
                    head,
                    self.branch,
                    self.a.report or "",
                    self.a.result or "",
                    self.a.verdict or "",
                )
            for d in did or ["nothing to change"]:
                self.out(d)
            for d in doubtful:
                self.out(f"still blocked, judge it (foreman backlog unblock ID): {d}")
        except BacklogError as e:
            self.out(f"foreman backlog integrated failed: {e}; update the backlog by hand")

    def run(self) -> int:
        a = self.a
        unknown = config.unknown_repo_keys(self.p)
        if unknown:  # strict here: a misspelt require_approval or risky_paths must not merge unguarded
            raise Stop(
                "unknown keys in foreman.toml, nothing done: "
                + "; ".join(f"[repos.{k}] {', '.join(keys)}" for k, keys in unknown)
                + " (a typo, or a newer foreman's keys: fix "
                "them or update foreman; `foreman check`)",
                2,
            )
        if a.keep_branch and not a.measure:
            raise Stop("--keep-branch is for --measure (a merged branch is always deleted)", 2)
        if a.measure and (
            a.rebase_from
            or a.ci
            or a.no_ci
            or a.approved
            or a.opens
            or a.title
            or a.body
            or a.body_file
            or a.no_review is not None
        ):
            raise Stop(
                "--measure merges nothing: no --rebase-from, --ci, --no-ci, --approved, --opens, --no-review, --title "
                "or --body",
                2,
            )
        if a.report is None:
            a.report = self.default_report()
            if a.report:
                self.out(f"== report: {a.report} (the sub-issue's or the delivery's; --report to give another)")
        if a.measure:
            return self.run_measure()
        self.refuse_measure_as_code()
        if a.report and not (self.p.home / a.report).is_file() and not self.branch_adds(a.report):
            raise Stop(
                f"--report {a.report} does not exist (path under the home, in the main checkout or added by "
                f"{self.branch})",
                2,
            )
        if not a.check and not self.local and not (a.title and (a.body or a.body_file)):
            raise Stop("the PR needs --title and --body (or --body-file)", 2)
        self.scan_public("PR title", a.title)
        self.scan_public("PR body", a.body)
        self.warn_owner_texts()
        self.check_templates()
        self.check_ci_options()
        did = self.validate()
        self.preflight()
        self.show()
        self.permissions_gate()
        self.review_gate()
        if a.check:
            self.out("== backlog after the merge (dry run):")
            for d in did or ["nothing to change"]:
                self.out("would do: " + d)
            why = self.needs_approval()
            if why:
                self.out("== the merge will need the owner's approval: " + "; ".join(why))
            self.out(self.ci_plan())
            if self.repo.get("post_merge"):
                self.out(
                    f"== post_merge after the merge, in {self.main_path} ({{before}}: {self.main} before, "
                    f"{{head}}: after): {self.post_merge_cmd()}"
                )
            if self.local:
                again = (
                    "the local CI again"
                    if self.repo.get("main_ci") is None
                    else "main_ci (or the local CI if main is not the commit that passed)"
                )
                self.out(f"== no remote: git merge --ff-only {self.branch}, then {again} on {self.main}")
                if self.repo.get("merge") in ("squash", "merge"):
                    self.out(f"== merge = {self.repo['merge']} does not apply without a remote: fast-forward only")
                if self.repo.get("remote_checks") == "required":
                    self.out("== remote_checks = required does not apply without a remote: ignored")
            self.out(f"== check only: {self.branch} in {self.wt} is ready to integrate")
            return 0
        if self.local:
            return self.run_local()
        body = self.body()
        self.scan_public("PR body", body)
        self.scan_commit_messages()
        ci_text = self.local_ci()
        try:
            self.scan_public("CI comment", ci_text)
        except Stop as e:
            raise Stop(f"{e.args[0]}: nothing pushed") from None
        pr, url = self.open_pr(a.title, body)
        try:
            self.remote_checks(pr)
            self.gh("pr", "comment", pr, "--body", ci_text)
            why = self.needs_approval()
            if why and not a.approved:
                notes.progress(
                    self.p,
                    f"PR #{pr} ({self.repo['key']}:{self.branch}) green, awaiting approval: " + "; ".join(why),
                    who="orchestrator",
                )
                raise Stop(
                    f"awaiting the owner's approval ({'; '.join(why)}): PR open and green, {url}; rerun with "
                    '--approved "who, when" once approved',
                    AWAITING,
                )
            head = self.merge(pr, a.title, body)
        except Stop as e:
            if e.rc != AWAITING:
                e.args = (f"{e.args[0]} (the PR stays open, nothing forced: {url})",)
            raise
        post = self.post_merge(self.before, git(self.main_path, "rev-parse", self.main))
        self.close(f"by PR #{pr} ({self.repo.get('merge')})", head, ci_text.split("\n")[0], post)
        return 0

    def run_local(self) -> int:
        """The flow for a repo without a remote: no push, no gh."""
        ci_text = self.local_ci()
        why = self.needs_approval()
        if why and not self.a.approved:
            notes.progress(
                self.p,
                f"{self.repo['key']}:{self.branch} green, awaiting approval to merge: " + "; ".join(why),
                who="orchestrator",
            )
            raise Stop(
                f"awaiting the owner's approval ({'; '.join(why)}): local CI green, nothing merged; rerun with "
                '--approved "who, when" once approved',
                AWAITING,
            )
        head, ci_main, before, after = self.merge_local()
        post = self.post_merge(before, after)
        if self.a.title:
            self.a.message = f"{self.a.title}. {self.a.message}".strip()
        how = "by fast-forward (local, no remote)"
        if self.merged_main:
            how += f", {self.main}'s home commits merged in first ({self.merged_main[0]})"
        self.close(
            how,
            head,
            f"{ci_text} On {self.main}: {ci_main}" if self.repo.get("local_ci") and not self.a.no_ci else ci_text,
            post,
        )
        return 0


def add_parser(sp: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    import argparse

    a = sp.add_parser(
        "integrate",
        help="a finished branch into main: a PR, or a fast-forward without a remote",
        description=DOC.split("\n\n")[0],
        epilog=DOC.split("\n\n", 1)[1],
        formatter_class=argparse.RawDescriptionHelpFormatter,
        allow_abbrev=False,  # --opens is read by the guard as written (grants.INTERNAL)
    )
    a.add_argument("repo")
    a.add_argument("branch")
    a.add_argument("issue", help='"ID" or "ID Tn" (or T1,T2)')
    a.add_argument("message", nargs="?", default="", help="internal merge message (note only)")
    for f in ("title", "body", "body-file", "sub", "note", "report", "result", "verdict", "rebase-from", "approved"):
        a.add_argument(f"--{f}")
    a.add_argument(
        "--ci",
        action="append",
        metavar="ARGS",
        help="one run of local_ci with ARGS as {ci}; repeatable, replaces ci_runs (--ci=--flag with «=»)",
    )
    a.add_argument("--no-ci", action="store_true")
    a.add_argument(
        "--no-review",
        metavar="WHY",
        help="integrate without a review a branch that needs one only for its size (a mechanical split proven by its "
        "tests); never for delicate paths or a partial delivery. WHY goes into the note",
    )
    a.add_argument(
        "--opens",
        action="store_true",
        help="the branch changes a live foreman.toml or guard table so that it allows something new: foreman asks the "
        "owner yes/no first (the guard's group permissions)",
    )
    a.add_argument("--check", action="store_true")
    a.add_argument(
        "--measure",
        action="store_true",
        help="close a measurement: no CI, no merge; removes the worktree and the branch",
    )
    a.add_argument("--keep-branch", action="store_true", help="with --measure: keep the branch")


def run(project: Project, args: argparse.Namespace) -> int:
    try:
        return Integration(project, args.repo, args.branch, args.issue, args).run()
    except Stop as e:
        print(f"foreman integrate: {e}", file=sys.stderr)
        return e.rc
