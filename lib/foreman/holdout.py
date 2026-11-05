"""Hidden acceptance checks: `foreman holdout seal ID [--no-baseline] | run REPO BRANCH ID [--full] | status [ID]`,
and the gate `foreman integrate` passes a branch through (integrate.py, `hidden_gate`).

Part of an issue's acceptance may live in the product's folder of hidden checks (`[paths] holdout`, the guard keeps
every agent but a planner away from it: guard.py, HOLDOUT), one subfolder per issue:

    <holdout>/ID/holdout.toml   the spec (SPEC_KEYS, one [[scenario]] per case: id, command, on_main, symptom)
    <holdout>/ID/files/…        copied over the copy of the branch before the cases run
    <holdout>/.runs/            the copies while they run, and the full output of each run (ID/*.log)

A case is a shell command run in a copy of the tip (`bash -c`, commands.py, point `holdout.scenario`): rc 0 passes.
The planner writes the spec; the orchestrator reads it, seals it when it approves the plan and runs it at integration.

- **seal** keeps in <state>/holdout.json a hash of the spec and of every file under the issue's folder, who and when,
  and the baseline: each case run on the repo's main, where it must do what its `on_main` says (`fail`: new
  behaviour; `pass`: a regression case); a case that does not is exit 2, nothing sealed (--no-baseline skips it).
  Once sealed, any change to the folder stops `integrate` (exit 2) until it is sealed again.
- **run** (and integrate) makes a detached `git worktree` of the tip under .runs/ (hooks off), runs the repo's
  `post_create` in it, copies files/ over it, runs the spec's `setup`, takes the spec's `lock` resource if any
  (`foreman lock` acquire/release), runs each case with the spec's `timeout`, and always removes the copy. The
  output goes only to the run's log under .runs/: never to the state, the CI log, the notes or the progress.
- **What leaves the folder**: the state keeps ids, hashes, counts and dates only; `integrate --check` (an agent may
  run it) says whether the check exists, is sealed and how many cases it has; a failure prints, for the agent, only
  the failing cases' `symptom` lines (what a user would notice), never a case's id, command, data, file or place
  (agent_message). A spec whose symptom names its own command, a file under files/ or the folder does not read.
- **Rounds**: each tip a run fails on (a merge of main or a clean rebase is the same tip: review.covers) is one
  round; a pass on a tip lets that tip in (and is reused, not run again). After MAX_ROUNDS failed rounds there is no
  third: the owner decides (integrate --approved). Sealing again (a case was wrong, not the branch) starts the
  rounds again.
"""

from __future__ import annotations

import hashlib
import math
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

from . import commands, review, toml_reader, util
from .config import ConfigError

if TYPE_CHECKING:
    import argparse

    from .config import Project, Table

SPEC = "holdout.toml"
FILES = "files"
RUNS = ".runs"
STATE = "holdout.json"
MAX_ROUNDS = 2
SPEC_KEYS = {"issue", "repo", "subs", "lock", "setup", "threshold", "timeout", "scenario"}
SCENARIO_KEYS = {"id", "command", "on_main", "symptom"}
ON_MAIN = ("fail", "pass")
ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")
SETUP_SECONDS = 600.0


class HoldoutError(Exception):
    """A spec that does not read, a seal that cannot be made or a run that cannot start."""


@dataclass
class Scenario:
    id: str
    command: str
    on_main: str
    symptom: str


@dataclass
class Spec:
    issue: str
    repo: str
    folder: Path
    subs: list[str] = field(default_factory=list)
    lock: str = ""
    setup: list[str] = field(default_factory=list)
    threshold: float = 1.0
    timeout: float = 300.0
    scenarios: list[Scenario] = field(default_factory=list)

    @property
    def needed(self) -> int:
        """How many cases must pass: the threshold's share of them, rounded up."""
        n = len(self.scenarios)
        return min(n, max(1, math.ceil(round(self.threshold * n, 9)))) if n else 0

    def applies(self, repo_key: str, sub: str | None) -> bool:
        """Whether an integration of REPO_KEY closing SUB («T1», «T1,T2», or None) runs it."""
        if repo_key != self.repo:
            return False
        return not self.subs or not sub or bool(set(sub.split(",")) & set(self.subs))


@dataclass
class Outcome:
    """A run: the cases that passed and failed (ids, in the spec's order), where its full output is and its commit."""

    passed: list[str]
    failed: list[str]
    log: Path
    tip: str
    seconds: int = 0

    @property
    def total(self) -> int:
        return len(self.passed) + len(self.failed)


def folder(project: Project, issue: str) -> Path:
    return project.holdout_dir / issue


def exists(project: Project, issue: str) -> bool:
    """Whether the issue has a hidden check: its spec in the folder, or a seal in the state."""
    return (folder(project, issue) / SPEC).is_file() or issue in load_state(project)["issues"]


def spec(project: Project, issue: str) -> Spec | None:
    """The issue's spec, or None without one; HoldoutError when it does not read (the message may quote it: it is
    for the orchestrator)."""
    d = folder(project, issue)
    f = d / SPEC
    if not f.is_file():
        return None
    try:
        raw = toml_reader.loads(f.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise HoldoutError(f"{f}: {e}") from e
    bad = sorted(set(raw) - SPEC_KEYS)
    if bad:
        raise HoldoutError(f"{f}: unknown keys {', '.join(bad)} (known: {', '.join(sorted(SPEC_KEYS))})")
    if raw.get("issue", issue) != issue:
        raise HoldoutError(f"{f}: issue = {raw.get('issue')!r}, but the folder is {issue}")
    repo = raw.get("repo")
    if not isinstance(repo, str) or repo not in project.repos:
        raise HoldoutError(f"{f}: repo = {repo!r} is not a repo of {project.name} ({', '.join(project.repos)})")
    s = Spec(issue, repo, d)
    for key in ("subs", "setup"):
        v = raw.get(key, [])
        if not isinstance(v, list) or not all(isinstance(x, str) and x.strip() for x in v):
            raise HoldoutError(f"{f}: {key} must be a list of strings")
        setattr(s, key, list(v))
    s.lock = raw.get("lock", "")
    if not isinstance(s.lock, str):
        raise HoldoutError(f"{f}: lock must be a resource name")
    if s.lock and s.lock not in project.resources:
        raise HoldoutError(f"{f}: lock = {s.lock!r} is not a resource of {project.name}")
    for key, low in (("threshold", 0.0), ("timeout", 0.0)):
        v = raw.get(key, getattr(s, key))
        if isinstance(v, bool) or not isinstance(v, (int, float)) or v <= low or (key == "threshold" and v > 1):
            raise HoldoutError(f"{f}: {key} = {v!r} out of range")
        setattr(s, key, float(v))
    cases = raw.get("scenario", [])
    if not isinstance(cases, list) or not cases:
        raise HoldoutError(f"{f}: no [[scenario]]")
    for n, c in enumerate(cases, 1):
        where = f"{f}: scenario {n}"
        if not isinstance(c, dict):
            raise HoldoutError(f"{where}: not a table")
        extra = sorted(set(c) - SCENARIO_KEYS)
        if extra:
            raise HoldoutError(f"{where}: unknown keys {', '.join(extra)}")
        sid, cmd, on_main, symptom = (c.get(k) for k in ("id", "command", "on_main", "symptom"))
        if not isinstance(sid, str) or not ID_RE.match(sid):
            raise HoldoutError(f"{where}: id {sid!r} must be short, lowercase letters, digits, - or _")
        if sid in {x.id for x in s.scenarios}:
            raise HoldoutError(f"{where}: id {sid} twice")
        if not isinstance(cmd, str) or not cmd.strip():
            raise HoldoutError(f"{where} ({sid}): no command")
        if on_main not in ON_MAIN:
            raise HoldoutError(f"{where} ({sid}): on_main must be one of {', '.join(ON_MAIN)}")
        if not isinstance(symptom, str) or not symptom.strip():
            raise HoldoutError(f"{where} ({sid}): no symptom (what a user would notice when it fails)")
        s.scenarios.append(Scenario(sid, cmd.strip(), on_main, " ".join(symptom.split())))
    problems = leaks(project, s)
    if problems:
        raise HoldoutError(f"{f}: a symptom would tell the agent the check itself: {'; '.join(problems)}")
    return s


def files(d: Path) -> list[Path]:
    """Every file of an issue's folder (the spec, files/…), sorted, links not followed."""
    return sorted(p for p in d.rglob("*") if p.is_file() or p.is_symlink())


def leaks(project: Project, s: Spec) -> list[str]:
    """What a symptom carries that it must not: a case's command, a file under files/ (its path or its name), the
    folder of hidden checks. The symptoms are the only text that leaves the folder for the agent."""
    home = str(Path.home())
    hidden = str(project.holdout_dir)
    words = {hidden, hidden.replace(home, "~", 1), FILES + "/", SPEC}
    for p in files(s.folder / FILES) if (s.folder / FILES).is_dir() else []:
        rel = p.relative_to(s.folder / FILES)
        words |= {str(rel)} | ({p.name} if len(p.stem) >= 4 else set())
    out = []
    for c in s.scenarios:
        low = c.symptom.casefold()
        for w in sorted(words | {x.command for x in s.scenarios}):
            if w.casefold() in low:
                out.append(f"the symptom of {c.id} names {w!r}")
    return out


def fingerprint(d: Path) -> tuple[str, int]:
    """(sha256 of every file's relative path and content, number of files) of an issue's folder."""
    h = hashlib.sha256()
    found = files(d)
    for p in found:
        h.update(str(p.relative_to(d)).encode() + b"\0")
        h.update((os.readlink(p).encode() if p.is_symlink() else p.read_bytes()) + b"\0")
    return h.hexdigest(), len(found)


# ---------- the state: ids, hashes, counts, dates (never a case's text) ----------


def state_file(project: Project) -> Path:
    return project.state / STATE


def load_state(project: Project) -> dict[str, Any]:
    data = util.read_json(state_file(project), {})
    if not isinstance(data, dict) or not isinstance(data.get("issues"), dict):
        data = {"issues": {}}
    return data


def save_state(project: Project, data: dict[str, Any]) -> None:
    util.write_json(state_file(project), data)


def sealed(project: Project, issue: str) -> dict[str, Any] | None:
    return load_state(project)["issues"].get(issue, {}).get("seal")


@dataclass
class Status:
    """Where an issue's hidden check stands, for integrate (nothing of its content): `ok` it can run."""

    issue: str
    spec: Spec | None = None
    error: str = ""  # the spec does not read (for the orchestrator only)
    seal: dict[str, Any] | None = None
    changed: bool = False

    @property
    def ok(self) -> bool:
        return self.spec is not None and self.seal is not None and not self.changed and not self.error

    def line(self) -> str:
        """The line of `integrate` (also --check, which an agent may run): never the content."""
        n = f"{len(self.spec.scenarios)} cases" if self.spec else "?"
        if self.error:
            return f"does not read: nothing done (`foreman holdout status {self.issue}`, the orchestrator's)"
        if self.spec is None:
            return "sealed, but its spec is gone: nothing done (write it again from the plan and seal it)"
        if self.seal is None:
            return f"{n}, not sealed: nothing done (the orchestrator seals it: `foreman holdout seal {self.issue}`)"
        if self.changed:
            return (
                f"{n}, changed since it was sealed: nothing done (seal it again: `foreman holdout seal {self.issue}`)"
            )
        return f"sealed, {n} (needs {self.spec.needed}), runs after the local CI"


def status(project: Project, issue: str) -> Status:
    st = Status(issue, seal=sealed(project, issue))
    try:
        st.spec = spec(project, issue)
    except HoldoutError as e:
        st.error = str(e)
    if st.seal is not None and folder(project, issue).is_dir():
        st.changed = fingerprint(folder(project, issue))[0] != st.seal.get("digest")
    elif st.seal is not None:
        st.changed = True
    return st


# ---------- running the cases ----------


def _safe(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", text).strip("-") or "x"


def run_cases(
    project: Project,
    s: Spec,
    ref: str,
    label: str,
    out: Callable[[str], object] = print,
) -> Outcome:
    """Runs the cases of S on REF of its repo in a copy (module doc); HoldoutError when the copy cannot be made.
    LABEL names the run's log (.runs/ID/LABEL-SHA8.log)."""
    from . import lock

    repo = project.repo(s.repo)
    main = repo["path"]
    tip = util.git(main, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}", check=False)
    if not tip:
        raise HoldoutError(f"{s.repo}: no commit {ref}")
    runs = project.holdout_dir / RUNS
    logs = runs / s.issue
    logs.mkdir(parents=True, exist_ok=True)
    log = logs / f"{_safe(label)}-{tip[:8]}.log"
    log.write_text("")
    copy = runs / f"{_safe(s.issue)}-{_safe(s.repo)}-{tip[:8]}-{os.getpid()}"
    values = {"main": main, "wt": copy, "repo": s.repo, "name": "holdout", "branch": ref}
    held: tuple[list[str], dict[str, str]] | None = None
    passed: list[str] = []
    failed: list[str] = []
    seconds = 0.0

    def logged(point: str, cmd: str, timeout: float) -> commands.Result:
        with open(log, "a") as f:
            f.write(f"== {util.now():%Y-%m-%d %H:%M:%S} {point}: {cmd}\n")
        r = commands.run(point, cmd, project=project, values=values, cwd=copy, timeout=timeout, log=log)
        with open(log, "a") as f:
            f.write(f"== rc {r.rc}{' (cut)' if r.timed_out else ''} in {r.seconds:.0f} s\n")
        return r

    r = subprocess.run(
        ["git", *util.GIT_QUIET, "-C", str(main), "worktree", "add", "-q", "--detach", str(copy), tip],
        capture_output=True,
        text=True,
        check=False,
    )
    if r.returncode:
        raise HoldoutError(f"could not make the copy of {tip[:8]}: {(r.stderr or r.stdout).strip()[:300]}")
    try:
        for step in repo.get("post_create", []):
            cmd = commands.expand("repo.post_create", step, values)
            if logged("repo.post_create", cmd, commands.POINTS["repo.post_create"].timeout or 600).rc != 0:
                raise HoldoutError(f"the repo's post_create failed in the copy ({cmd}); see {log}")
        if (s.folder / FILES).is_dir():
            shutil.copytree(s.folder / FILES, copy, dirs_exist_ok=True, symlinks=True)
        for step in s.setup:
            if logged("holdout.setup", step, SETUP_SECONDS).rc != 0:
                raise HoldoutError(f"a setup step failed in the copy; see {log}")
        if s.lock:
            res = lock.resource_config(project, s.lock)
            held = lock.command(s.lock, res, [], who=f"orchestrator (hidden check {s.issue})", project=project)
            out(f"== hidden check {s.issue}: waiting for {s.lock}")
            got = subprocess.run(
                [*held[0], "acquire", f"hidden check {s.issue}"],
                env=held[1],
                capture_output=True,
                text=True,
                check=False,
            )
            if got.returncode:
                held = None
                raise HoldoutError(f"could not take {s.lock}: {(got.stderr or got.stdout).strip()[:300]}")
        for c in s.scenarios:
            r2 = logged("holdout.scenario", c.command, s.timeout)
            seconds += r2.seconds
            (passed if r2.rc == 0 else failed).append(c.id)
    finally:
        if held is not None:
            subprocess.run([*held[0], "release"], env=held[1], capture_output=True, text=True, check=False)
        gone = subprocess.run(
            ["git", *util.GIT_QUIET, "-C", str(main), "worktree", "remove", "--force", str(copy)],
            capture_output=True,
            check=False,
        )
        if gone.returncode:
            shutil.rmtree(copy, ignore_errors=True)
        subprocess.run(["git", *util.GIT_QUIET, "-C", str(main), "worktree", "prune"], capture_output=True, check=False)
    return Outcome(passed, failed, log, tip, int(seconds))


def agent_message(s: Spec, failed: list[str]) -> str:
    """What the agent who made the branch is told when cases fail: only the symptom of each failing case, as a
    user would tell it; never its id, command, data, file or place."""
    symptoms = list(dict.fromkeys(c.symptom for c in s.scenarios if c.id in failed))
    return "\n".join(
        [
            "Part of the acceptance runs at integration with checks you cannot see. On your branch, a user would "
            "notice:",
            *[f"- {x}" for x in symptoms],
            "Fix the behaviour against the contract and the criterion in the plan; do not look for the checks.",
        ]
    )


# ---------- rounds ----------


def record(project: Project, s: Spec, repo: Table, branch: str, o: Outcome) -> tuple[int, bool]:
    """Writes the run in the state and returns (its round, whether the tip passes). Only runs under the current seal
    count; a failure on a tip that already failed (review.covers) is that tip's round again."""
    data = load_state(project)
    entry = data["issues"].setdefault(s.issue, {})
    seal = entry.get("seal") or {}
    mine = [
        x
        for x in entry.get("runs", [])
        if x.get("digest") == seal.get("digest")
        and x.get("repo") == repo["key"]
        and x.get("branch") == branch
        and not x.get("ok")
    ]
    passes = len(o.passed) >= s.needed
    main = repo.get("main", "main")
    same = next((x for x in mine if review.covers(repo["path"], main, o.tip, x.get("tip", ""))), None)
    rnd = same["round"] if same else max((x.get("round", 0) for x in mine), default=0) + 1
    entry.setdefault("runs", []).append(
        {
            "repo": repo["key"],
            "branch": branch,
            "tip": o.tip,
            "digest": seal.get("digest"),
            "passed": len(o.passed),
            "total": o.total,
            "failed": o.failed,
            "ok": passes,
            "round": rnd,
            "at": util.now().isoformat(timespec="seconds"),
            "log": o.log.name,
        }
    )
    entry["runs"] = entry["runs"][-50:]
    save_state(project, data)
    return rnd, passes


def passed_on(project: Project, s: Spec, repo: Table, branch: str) -> dict[str, Any] | None:
    """A passing run under the current seal that covers BRANCH's tip (the same commit or own diff), or None."""
    entry = load_state(project)["issues"].get(s.issue, {})
    digest = (entry.get("seal") or {}).get("digest")
    for x in reversed(entry.get("runs", [])):
        if (
            x.get("digest") == digest
            and x.get("repo") == repo["key"]
            and x.get("ok")
            and review.covers(repo["path"], repo.get("main", "main"), branch, x.get("tip", ""))
        ):
            return x
    return None


# ---------- the command ----------


def seal(project: Project, issue: str, baseline: bool = True, out: Callable[[str], object] = print) -> int:
    from . import identity

    s = spec(project, issue)
    if s is None:
        raise HoldoutError(f"no {SPEC} in {folder(project, issue)}")
    digest, n = fingerprint(s.folder)
    base: dict[str, Any] | None = None
    if baseline:
        repo = project.repo(s.repo)
        main = repo.get("main", "main")
        o = run_cases(project, s, main, f"baseline-{main}", out)
        wrong = []
        for c in s.scenarios:
            got = "pass" if c.id in o.passed else "fail"
            if got != c.on_main:
                wrong.append(f"{c.id} {'passes' if got == 'pass' else 'fails'} on {main} (on_main = {c.on_main})")
        if wrong:
            out(f"== baseline on {main} {o.tip[:8]}: " + "; ".join(wrong) + f" (full output: {o.log})")
            raise HoldoutError(
                f"not sealed: {len(wrong)} case{'s' if len(wrong) != 1 else ''} do not do on {main} what on_main says"
            )
        if fingerprint(s.folder)[0] != digest:
            raise HoldoutError("not sealed: the folder changed while the baseline ran")
        base = {"main": o.tip, "fail": [c.id for c in s.scenarios if c.on_main == "fail"], "log": o.log.name}
        out(f"== baseline on {main} {o.tip[:8]}: as expected ({len(o.failed)} fail, {len(o.passed)} pass)")
    data = load_state(project)
    entry = data["issues"].setdefault(issue, {})
    entry["seal"] = {
        "digest": digest,
        "files": n,
        "cases": len(s.scenarios),
        "repo": s.repo,
        "by": identity.who("orchestrator", warn=False)[0],
        "at": util.now().isoformat(timespec="seconds"),
        "baseline": base,
    }
    save_state(project, data)
    out(
        f"== hidden check {issue} sealed: {len(s.scenarios)} cases, {n} files, sha256 {digest[:12]}"
        + ("" if base else " (no baseline: --no-baseline)")
    )
    return 0


def show_status(project: Project, issue: str | None, out: Callable[[str], object] = print) -> int:
    data = load_state(project)["issues"]
    root = project.holdout_dir
    if issue is not None:
        names = [issue]
    else:
        found = {p.name for p in root.iterdir() if p.is_dir() and p.name != RUNS} if root.is_dir() else set()
        names = sorted(found | set(data))
    if not names:
        out(f"no hidden checks in {root}")
        return 0
    for name in names:
        st = status(project, name)
        out(f"{name}: {st.error or st.line()}")
        for x in data.get(name, {}).get("runs", [])[-5:]:
            current = (st.seal or {}).get("digest") == x.get("digest")
            out(
                f"  {x.get('at')} {x.get('repo')}:{x.get('branch')} {str(x.get('tip'))[:8]} "
                f"{x.get('passed')}/{x.get('total')} round {x.get('round')}"
                + (f", failed {', '.join(x['failed'])}" if x.get("failed") else "")
                + ("" if current else " (an older seal)")
                + f"; log {root / RUNS / name / str(x.get('log'))}"
            )
    return 0


def add_parser(sp: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    import argparse

    a = sp.add_parser(
        "holdout",
        help="the hidden acceptance checks of an issue (the orchestrator's): seal, run, status",
        description=__doc__.split("\n\n")[0] if __doc__ else None,
        epilog="\n\n".join((__doc__ or "").split("\n\n")[1:]),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    hs = a.add_subparsers(dest="hcmd", required=True)
    s = hs.add_parser("seal", help="hash the issue's folder and run the baseline on main")
    s.add_argument("issue")
    s.add_argument("--no-baseline", action="store_true", help="seal without running the cases on main")
    r = hs.add_parser("run", help="run the cases on a branch now, as integrate does (the run counts as a round)")
    r.add_argument("repo")
    r.add_argument("branch")
    r.add_argument("issue")
    r.add_argument("--full", action="store_true", help="print the run's full output")
    t = hs.add_parser("status", help="each issue's check: sealed or not, its last runs and their logs")
    t.add_argument("issue", nargs="?")


def run_command(project: Project, args: argparse.Namespace) -> int:
    try:
        if args.hcmd == "seal":
            return seal(project, args.issue, baseline=not args.no_baseline)
        if args.hcmd == "status":
            return show_status(project, args.issue)
        st = status(project, args.issue)
        if not st.ok or st.spec is None:
            print(f"foreman holdout: {args.issue}: {st.error or st.line()}", file=sys.stderr)
            return 2
        repo = project.repo(args.repo)
        if args.repo != st.spec.repo:
            print(f"foreman holdout: {args.issue}'s check is for repo {st.spec.repo}", file=sys.stderr)
            return 2
        o = run_cases(project, st.spec, args.branch, f"{args.repo}-{args.branch}")
        rnd, ok = record(project, st.spec, repo, args.branch, o)
        print(
            f"== hidden check {args.issue}: {len(o.passed)} of {o.total} pass (needs {st.spec.needed}) on "
            f"{o.tip[:8]}, round {rnd}" + (f"; failed: {', '.join(o.failed)}" if o.failed else "")
        )
        if o.failed:
            print("== for the agent, as it is:\n" + agent_message(st.spec, o.failed))
        if args.full:
            print(f"== full output ({o.log}):\n" + o.log.read_text(errors="replace"))
        return 0 if ok else 1
    except (HoldoutError, ConfigError) as e:
        print(f"foreman holdout: {e}", file=sys.stderr)
        return 2
