"""`foreman backlog …`: the subcommands' parser, their run and the text they print (summary, one issue, project or
decision, one field)."""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING

from . import backlog_check, backlog_list, backlog_pages
from .backlog import CLOSED, Backlog, BacklogError, DecisionResolved

if TYPE_CHECKING:
    import argparse

    from .backlog import Data
    from .config import Project
    from .plain import Finding

# ---------- text output ----------


def show_summary(b: Backlog, data: Data) -> str:
    out = []
    groups = [(f"{p['id']} · {p['title']}", p["issues"]) for p in data["projects"]]
    if data.get("issues"):
        groups.append((b.s["no_project"], data["issues"]))
    rb = b.relation_blockers(data)
    holds = b.decision_holds(data)
    for head, issues in groups:
        out.append(f"## {head}")
        for i in issues:
            subs = [s for s in i["subs"] if s["status"] not in ("canceled", "moved")]
            sc = f"{sum(s['status'] == 'done' for s in subs)}/{len(subs)}" if i["subs"] else ""
            why = backlog_pages.render_blocked(b, i, rb, "by", holds, "waits for decision")
            bl = f" [blocked: {why}]" if why else ""
            title = re.sub(r"\*\*|`", "", i["title"])
            out.append(f"  {i['id']:<7} {i['status']:<12} {sc:>5}  {title[:90]}{bl}")
    if data["decisions"]:
        out.append(f"## Pending decisions ({b.project.owner})")
        for d in data["decisions"]:
            held = f" [holds: {', '.join(d['blocks'])}]" if d.get("blocks") else ""
            out.append(f"  {d['id']:<7} {d['date']}  {d['title'][:90]}{held}")
    return "\n".join(out)


def show_one(b: Backlog, data: Data, key: str, as_json: bool = False) -> str:
    if b.project_id.match(key) and not b.new_issue_id.match(key):
        obj = b.get_project(data, key)
        if as_json:
            return json.dumps(obj, ensure_ascii=False, indent=2)
        q = b.project_progress(obj)
        lines = [f"## {obj['id']} · {obj['title']}  ({q['done']}/{q['total']} done)"]
        meta = backlog_pages.render_project_meta(b, obj)
        if meta:
            lines.append(re.sub(r"^\*|\*$", "", meta, flags=re.M))
        lines.append(f"\n{obj['intro']}\n")
        lines += [f"  {i['id']:<7} {i['status']:<12} {i['title'][:100]}" for i in obj["issues"]]
        return "\n".join(lines)
    dec = next((d for d in data["decisions"] if d["id"] == key), None) or b.resolved_decision(data, key)
    if dec:
        if as_json:
            return json.dumps(dec, ensure_ascii=False, indent=2)
        done = f"\nresolved {DecisionResolved.said(dec)}" if dec.get("resolved") else ""
        return backlog_pages.render_decision(b, dec) + done
    lst, k, pid = b.find(data, b.resolve(data, key), archived=True)
    i = lst[k]
    if as_json:
        return json.dumps(i, ensure_ascii=False, indent=2)
    out = [
        f"{i['id']} · {i['title']}\nproject {pid} · status {i['status']}"
        + (f" · closed {i['closed']}" if i.get("closed") else "")
        + (f" · blocked: {i['blocked']}" if i["blocked"] else "")
        + (
            f" · blocked by relation: {', '.join(rel)}"
            if (rel := b.relation_blockers(data).get(i["id"])) and i["status"] not in CLOSED
            else ""
        )
        + (
            f" · {held}"
            if (held := backlog_pages.render_held(b, i, b.decision_holds(data), "waits for decision"))
            else ""
        )
        + (f" · github {i['github']}" if i.get("github") else "")
        + (f" · short «{i['short']}»" if i.get("short") else "")
    ]
    for line in (
        backlog_pages.render_fields(b, i),
        backlog_pages.render_outcome(b, data, i),
        backlog_pages.render_result(b, data, i),
    ):
        if line:
            out.append(re.sub(r"\*\*", "", line))
    r = b.relations_of(data, i["id"])
    rels = [f"{k.replace('_', ' ')} {', '.join(v) if isinstance(v, list) else v}" for k, v in r.items() if v]
    if rels:
        out.append("relations: " + " · ".join(rels))
    if i["plan"]:
        out.append("plan: " + " · ".join(p["path"] + (f" {p['note']}" if p["note"] else "") for p in i["plan"]))
    out += [f"  {backlog_pages.render_sub(b, s)}" for s in i["subs"]]
    if i["detail"]:
        out.append(f"detail: {i['detail']}")
    return "\n".join(out)


def dry_run_add(b: Backlog, args: argparse.Namespace) -> int:
    """`backlog add --dry-run`: the same add on a copy of the data, with the same checks as the save, and the issue
    shown as `show ID` would. Nothing is written."""
    data = b.load()
    iid = b.add_issue(
        data,
        args.project,
        args.id,
        args.title,
        args.status,
        args.detail,
        args.blocked,
        args.plan,
        args.before,
        args.claim,
        args.criterion,
        args.github,
        args.short,
    )
    errors = backlog_check.hard(backlog_check.validate(b, data, files=False))
    if errors:
        raise BacklogError("not saved: " + "; ".join(errors))
    print(f"would add {iid} (dry run)")
    print(show_one(b, data, iid))
    return 0


def get_field(b: Backlog, data: Data, iid: str, field: str) -> str:
    i = b.issue(data, b.resolve(data, iid), archived=True)
    if field == "plan":
        return "\n".join(p["path"] for p in i["plan"])
    if field == "subs":
        return backlog_pages.render_subs(b, i["subs"])
    if field not in ("title", "status", "blocked", "detail", "id", "result", "github"):
        raise BacklogError(f"unknown field {field}")
    return i.get(field, "")


# ---------- command line ----------

USAGE = """\
  show [ID] [--json]                 summary of every project, or one issue / project / decision
  list [--all-products] [--status S ...] [--open] [--group EN|- ...] [--has FIELD[=VALUE] ...]
       [--sub-status S] [--archived] [--count] [--by status|group|product] [--json]
                                     one line per issue (id, status, project, title; * archived), or how many
                                     (--by: per status, project or product; implies --count);
                                     --all-products: every product in the registry, each from its own backlog
  get ID FIELD                       one field as text (title, status, blocked, plan, subs, detail, result, github)
  status ID[/Tn] VALUE [--result R] [--verdict V]
                                     issue status (triage backlog refining ready "in progress" done canceled
                                     duplicate; idea = triage, todo = ready) or
                                     sub-issue status (todo "in progress" done canceled moved unknown, or ▶ ✓ ✗ → ?)
  sub ID Tn [--title T] [--text T] [--status S] [--commit C] [--agent A] [--branch B] [--moved-to ID]
            [--report PATH] [--kind measure|code] [--before Tm] [--remove]
                                     --kind measure: a part that only measures (no code for main); code clears it
  edit ID [--title T] [--detail T] [--append T] [--prepend T] [--plan [LABEL=]PATH ...] [--claim T]
          [--criterion T] [--verdict V] [--result R] [--github owner/repo#n] [--short T]
          [--priority urgent|high|medium|low|none] [--assignee A] [--repo R]
                                     an empty --priority, --assignee or --repo clears it
  block ID "reason" | unblock ID
  integrated ID [Tn] [--branch B] [--commit C] [--report PATH] [--result R] [--verdict V] [--dry-run]
  move ID (--before ID2 | --after ID2 | --project EN|-)   (- = no project; projects: move EN --before EM)
  add EN|- --id ID --title T [--status S] [--detail T] [--blocked T] [--plan [LABEL=]PATH] [--before ID]
         [--claim T] [--criterion T] [--github owner/repo#n] [--short T] [--dry-run]
                                     --dry-run: check it and show it as `show ID` would, writing nothing
  project add EN --title T --intro T [--before EM] [project fields]
  project edit EN [--title T] [--intro T] [project fields] | project remove EN (an empty project)
         project fields: [--status backlog|planned|"in progress"|paused|completed|canceled]
         [--priority urgent|high|medium|low|none] [--repo R ...] [--no-repo R ...]
  relate ID blocks|blocked-by|related|duplicate ID2 [--remove]
  archive ID
  decision add [--id ID | --issue ISSUE] --title T --text T --option O [--option O ...] [--recommend T]
         [--recommend-option O] [--date D] [--as-is] [--blocks REF ...]
                                     a question for the owner (docs/owner.md): --title the question in one plain
                                     sentence; --text what it is about, why it comes now and what each option
                                     changes; --recommend the option and why; --recommend-option the exact text of
                                     the recommended --option (exit 2 if it is none of them), the one the panel
                                     marks. Texts that name ids or internal names
                                     without explaining them (foreman plain) are refused (exit 2) unless --as-is,
                                     which records it anyway and marks it ("as_is": true, a warning in check).
                                     --blocks: what waits for the answer while it is pending (none by default):
                                     Tn or "issue" relative to --issue, or "ISSUE" / "ISSUE Tn"
  decision blocks ID REF ... | decision blocks ID --none [--issue ISSUE]
                                     change what a pending decision holds (REF as in --blocks)
  decision list [--json]             the pending decisions in the order to go through them with the owner: those
                                     that hold open work first, then the rest, each oldest first; with what each
                                     holds, its issue and the issue's plans («no pending decisions» when none)
  decision resolve ID [--option N|T | --answer T [--free]] [--by B]
                                     prints "freed: REF" (or "REF: still held by ID") for what it held.
                                     --option: one of its options, by number (from 1) or exact text (exit 2 if it
                                     is none); --answer T --free: the owner's own words, marked free. Both default
                                     --by to «OWNER, YYYY-MM-DD HH:MM (chat)». --answer alone: as given, with a
                                     warning when it is not one of the options. Already resolved: says so, exit 0
  page backlog|archive|decisions [--heading T] [--replace OLD NEW ...]
  import-gh REPO --project EN|- --prefix XX [--status S]   open GitHub issues of a configured repo, once each
  render [--check]                   render --check: exit 1 if a generated page is stale (`foreman check`
                                     validates the backlog: a stale page there is only a warning)
"""


def add_parser(sp: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    import argparse

    ap = sp.add_parser(
        "backlog",
        help="the project's backlog: issues, projects, decisions for the owner, generated pages",
        description="The project's backlog.",
        epilog=USAGE,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    bp = ap.add_subparsers(dest="bcmd", required=True)
    a = bp.add_parser("show")
    a.add_argument("id", nargs="?")
    a.add_argument("--json", action="store_true")
    backlog_list.add_arguments(bp.add_parser("list"))
    a = bp.add_parser("get")
    a.add_argument("id")
    a.add_argument("field")
    a = bp.add_parser("status")
    a.add_argument("ref")
    a.add_argument("value")
    a.add_argument("--result")
    a.add_argument("--verdict")
    a = bp.add_parser("sub")
    a.add_argument("id")
    a.add_argument("sid")
    for f in ("title", "text", "status", "commit", "agent", "branch", "moved-to", "report", "before"):
        a.add_argument(f"--{f}")
    a.add_argument("--kind", choices=("measure", "code"))
    a.add_argument("--remove", action="store_true")
    a = bp.add_parser("edit")
    a.add_argument("id")
    for f in ("title", "detail", "append", "prepend", "claim", "criterion", "verdict", "result", "github", "short"):
        a.add_argument(f"--{f}")
    for f in ("priority", "assignee", "repo"):
        a.add_argument(f"--{f}")
    a.add_argument("--plan", nargs="*")
    a = bp.add_parser("block")
    a.add_argument("id")
    a.add_argument("reason")
    a = bp.add_parser("unblock")
    a.add_argument("id")
    a = bp.add_parser("integrated")
    a.add_argument("id")
    a.add_argument("sid", nargs="?")
    for f in ("commit", "branch", "report", "result", "verdict"):
        a.add_argument(f"--{f}", default="")
    a.add_argument("--dry-run", action="store_true")
    a = bp.add_parser("move")
    a.add_argument("id")
    a.add_argument("--before")
    a.add_argument("--after")
    a.add_argument("--project")
    a = bp.add_parser("add", help="add an issue and print its id")
    a.add_argument("project", nargs="?", default="", help='its project; left out, "" or - for none')
    a.add_argument("--id", help="default: the next free one, after the highest existing id")
    a.add_argument("--title", required=True)
    a.add_argument("--status", default="backlog")
    a.add_argument("--detail", default="")
    a.add_argument("--blocked", default="")
    a.add_argument("--plan", action="append", default=[])
    a.add_argument("--before")
    a.add_argument("--claim")
    a.add_argument("--criterion")
    a.add_argument("--github")
    a.add_argument("--short")
    a.add_argument("--dry-run", action="store_true")
    a = bp.add_parser("project")
    a.add_argument("action", choices=("add", "edit", "remove"))
    a.add_argument("id")
    a.add_argument("--title")
    a.add_argument("--intro")
    a.add_argument("--before")
    for f in ("status", "priority"):
        a.add_argument(f"--{f}")
    a.add_argument("--repo", action="append", default=[])
    a.add_argument("--no-repo", action="append", default=[])
    a = bp.add_parser("relate")
    a.add_argument("id")
    a.add_argument("kind")
    a.add_argument("other")
    a.add_argument("--remove", action="store_true")
    a = bp.add_parser("archive")
    a.add_argument("id")
    a = bp.add_parser("decision")
    a.add_argument("action", choices=("add", "resolve", "blocks", "list"))
    a.add_argument("id", nargs="?")
    a.add_argument("refs", nargs="*", help="decision blocks: what waits for the answer")
    a.add_argument("--id", dest="did")
    a.add_argument("--issue")
    a.add_argument("--title")
    a.add_argument("--text", default="")
    a.add_argument(
        "--option",
        action="append",
        default=[],
        help="decision add: an option (repeat it); decision resolve: the answer, by number (from 1) or exact text",
    )
    a.add_argument("--recommend", default="")
    a.add_argument("--recommend-option", default="", help="the exact text of the recommended --option")
    a.add_argument("--date")
    a.add_argument("--answer", default="")
    a.add_argument("--by", default="", help="decision resolve: who answered and when, e.g. «Owner, 21:20 (chat)»")
    a.add_argument("--as-is", action="store_true", help="record the decision although its texts fail foreman plain")
    a.add_argument("--free", action="store_true", help="decision resolve: --answer is the owner's own words")
    a.add_argument("--json", action="store_true", help="decision list: as JSON")
    a.add_argument(
        "--blocks",
        nargs="+",
        action="extend",
        default=[],
        metavar="REF",
        help="what waits for the answer: Tn or issue (with --issue), or ISSUE / ISSUE Tn",
    )
    a.add_argument("--none", action="store_true", help="decision blocks: it holds nothing")
    a = bp.add_parser("page")
    a.add_argument("key")
    a.add_argument("--heading")
    a.add_argument("--replace", nargs=2, action="append", default=[], metavar=("OLD", "NEW"))
    a = bp.add_parser("import-gh")
    a.add_argument("repo")
    a.add_argument("--project", required=True)
    a.add_argument("--prefix", required=True)
    a.add_argument("--status", default="backlog")
    a = bp.add_parser("render")
    a.add_argument("--check", action="store_true")


def refuse_decision(bad: list[tuple[str, list[Finding]]]) -> int:
    """`decision add` with texts the owner would have to ask about: the findings by field, how to fix it, exit 2."""
    import sys

    from . import plain

    print(
        "foreman backlog: decision not recorded: it names things the owner would have to ask about (foreman plain):",
        file=sys.stderr,
    )
    for field, found in bad:
        print(f"  {field}: {plain.summary(found)}", file=sys.stderr)
    print(
        "Rewrite it for the owner as docs/owner.md says (`foreman docs owner`): --title the question in one plain "
        "sentence; --text what it is about, why it comes now and what each option changes; --recommend the option "
        'and why. Or add --as-is to record it as it is (marked "as_is": true, a warning in foreman check).',
        file=sys.stderr,
    )
    return 2


def warn_owner_texts(project: Project, args: argparse.Namespace) -> None:
    """Warnings on stderr, never blocking, for the texts of this command that reach the owner: an issue's title and
    its short name and its result line (docs/owner.md). Sub-issue titles stay technical."""
    from . import plain

    c = args.bcmd
    if c in ("add", "edit"):
        plain.warn(project, "issue title", args.title)
    if c in ("add", "edit"):
        plain.warn(project, "short name", args.short)
    if c in ("edit", "integrated") or (c == "status" and "/" not in args.ref):
        plain.warn(project, "result line", args.result)


def sub_status(b: Backlog, data: Data, iid: str, sid: str) -> str | None:
    """A sub-issue's status, or None when there is no such issue or sub-issue."""
    from .backlog import find_sub

    try:
        i = b.issue(data, b.resolve(data, iid))
    except BacklogError:
        return None
    k = find_sub(i, sid)
    return i["subs"][k].get("status") if k is not None else None


def closed_without_commit(project: Project, b: Backlog, data: Data, iid: str, sid: str, was: str | None) -> str:
    """The warning for a code sub-issue just closed `done` by hand without a commit while its branch is still
    unmerged in one of the project's repos: the pass takes a branch whose sub-issues are all closed without a commit
    for one closed on purpose (a measurement), so it will not show as ready to integrate. "" when there is none."""
    from . import util
    from .backlog import find_sub

    try:
        i = b.issue(data, b.resolve(data, iid))
    except BacklogError:
        return ""
    k = find_sub(i, sid)
    s = i["subs"][k] if k is not None else {}
    br = s.get("branch", "").strip("`")
    if was == "done" or s.get("status") != "done" or s.get("commit") or s.get("kind") == "measure" or not br:
        return ""
    for key in project.repos:
        tab = project.repo(key)
        path = tab.get("path")
        if not path or not util.git(path, "rev-parse", "-q", "--verify", f"refs/heads/{br}", check=False):
            continue
        main = tab.get("main") or "main"
        if not util.git(path, "rev-parse", "-q", "--verify", f"refs/heads/{main}", check=False):
            continue
        if not util.git(path, "branch", "--merged", main, "--list", br, check=False):
            return (
                f"warning: {i['id']} {sid} closed without a commit while {key}:{br} is not merged: its branch will "
                f"not show as ready to integrate (integrate it, add --commit, or mark it `--kind measure`)"
            )
    return ""


def show_review(b: Backlog, data: Data, as_json: bool = False) -> str:
    """`decision list`: the pending decisions in review order (Backlog.pending_review), as JSON or one block each
    with its options numbered as `decision resolve --option N` takes them."""
    items = b.pending_review(data)
    if as_json:
        return json.dumps(items, ensure_ascii=False, indent=2)
    if not items:
        return "no pending decisions"
    out = [f"{len(items)} pending decision{'s' if len(items) != 1 else ''} ({b.project.owner}), in review order:"]
    for d in items:
        age = f", {d['age_days']} day{'s' if d['age_days'] != 1 else ''} ago" if d["age_days"] is not None else ""
        block = [f"\n{d['id']} · {d['title']}  ({d['date']}{age})"]
        if d["holds"]:
            block.append(f"  holds: {', '.join(d['holds'])}")
        if i := d["issue"]:
            block.append(f"  issue: {i['id']} · {i['short']} ({i['status']})")
        if d["plans"]:
            block.append(f"  plan: {' · '.join(d['plans'])}")
        if d["text"]:
            block.append(f"  {d['text']}")
        for n, o in enumerate(d["options"], 1):
            block.append(f"  {n}. {o}" + ("  (recommended)" if o == d["recommended"] else ""))
        if d["recommendation"]:
            block.append(f"  recommendation: {d['recommendation']}")
        out += block
    return "\n".join(out)


def check_resolve_args(args: argparse.Namespace) -> None:
    """`decision resolve`: --option once and without --answer or --free; --free only with --answer."""
    if len(args.option) > 1:
        raise BacklogError("decision resolve takes one --option")
    if args.option and (args.answer or args.free):
        raise BacklogError("--option goes alone: --answer T --free is for the owner's own words")
    if args.free and not args.answer.strip():
        raise BacklogError("--free needs --answer with the owner's words")


def resolve(b: Backlog, data: Data, args: argparse.Namespace) -> list[str]:
    """`decision resolve`: with --option (validated as the panel does, Backlog.answer_decision) or --answer --free,
    `by` is «OWNER, YYYY-MM-DD HH:MM (chat)» unless --by gives it; --answer alone resolves as before, with a warning
    on stderr when it is not one of the options. The lines of what it freed."""
    import sys
    from datetime import datetime

    if not args.id:
        raise BacklogError("decision resolve needs the decision id")
    by = args.by or f"{b.project.owner}, {datetime.now():%Y-%m-%d %H:%M} (chat)"
    if args.option:
        return b.answer_decision(data, args.id, b.option_of(data, args.id, args.option[0]), by)
    if args.free:
        return b.answer_decision(data, args.id, args.answer.strip(), by, free=True)
    d = next((x for x in data["decisions"] if x["id"] == args.id), None)
    if d and args.answer and args.answer not in d.get("options", []):
        print(
            f"foreman backlog: warning: {args.id}: the answer is not one of its options: use --option N, "
            "or --free for the owner's own words",
            file=sys.stderr,
        )
    return b.resolve_decision(data, args.id, args.answer, args.by)


def run_list(project: Project | None, args: argparse.Namespace) -> int:
    """`foreman backlog list` (no project with --all-products); returns the exit code."""
    import sys

    try:
        return backlog_list.run(project, args)
    except BacklogError as e:
        print(f"foreman backlog: {e}", file=sys.stderr)
        return 2


def run(project: Project, args: argparse.Namespace) -> int:
    """`foreman backlog …`; returns the exit code."""
    import sys

    if args.bcmd == "list":
        return run_list(project, args)
    b = Backlog(project)
    try:
        c = args.bcmd
        if c == "show":
            data = b.load()
            print(show_one(b, data, args.id, args.json) if args.id else show_summary(b, data))
            return 0
        if c == "get":
            print(get_field(b, b.load(), args.id, args.field))
            return 0
        if c == "integrated":
            if args.report and not (b.root / args.report).is_file():
                raise BacklogError(f"report {args.report} does not exist")
            close = (args.id, args.sid, args.commit, args.branch, args.report, args.result, args.verdict)
            if args.dry_run:
                did, doubtful = b.integrated(b.load(), *close)
            else:
                with b.mutate() as data:
                    did, doubtful = b.integrated(data, *close)
            pre = "would do: " if args.dry_run else ""
            for d in did or ["nothing to change"]:
                print(pre + d)
            for d in doubtful:
                print(f"still blocked, judge it (foreman backlog unblock ID): {d}")
            warn_owner_texts(project, args)
            return 0
        if c == "render":
            if args.check:
                s = backlog_pages.stale(b)
                for p in s:
                    print(f"stale: {b.rel(p)}")
                return 1 if s else 0
            with b.locked():
                for p in backlog_pages.render(b):
                    print(f"wrote {b.rel(p)}")
            return 0
        if c == "import-gh":
            slug = project.repo(args.repo).get("github")
            if not slug:
                raise BacklogError(f"repo {args.repo} has no `github` in foreman.toml")
            with b.mutate() as data:
                added = b.import_gh(data, slug, args.project, args.prefix, args.status)
            print(f"imported {len(added)}: {', '.join(added)}" if added else "nothing new")
            return 0
        if c == "decision" and args.action == "add" and args.title and not args.as_is:
            bad = b.decision_findings(args.title, args.text, args.option, args.recommend)
            if bad:
                return refuse_decision(bad)
        if c == "decision":
            if args.refs and args.action != "blocks":
                raise BacklogError("references go after the id only in decision blocks; decision add takes --blocks")
            if args.blocks and args.action != "add":
                raise BacklogError("--blocks goes with decision add; decision blocks ID REF ... changes it later")
            if args.none and args.action != "blocks":
                raise BacklogError("--none goes with decision blocks")
            if args.free and args.action != "resolve":
                raise BacklogError("--free goes with decision resolve")
            if args.json and args.action != "list":
                raise BacklogError("--json goes with decision list (or backlog show ID --json)")
            if args.action == "list":
                data = b.load()
                print(show_review(b, data, args.json))
                return 0
            if args.action == "resolve":
                check_resolve_args(args)
        if c == "add" and args.dry_run:
            return dry_run_add(b, args)
        freed = []
        holding: list[str] = []  # what a decision just added or changed holds (its warnings go to stderr)
        closing: tuple[str, str, str | None] | None = None  # (issue, sub, its status before) closed by hand
        with b.mutate() as data:
            if c == "status" and "/" in args.ref:
                iid, _, sid = args.ref.partition("/")
                closing = (iid, sid, sub_status(b, data, iid, sid))
            elif c == "sub" and args.status and not args.commit and not args.remove:
                closing = (args.id, args.sid, sub_status(b, data, args.id, args.sid))
            if c == "status":
                was = b.issue(data, b.resolve(data, args.ref.partition("/")[0])).get("status")
                b.set_status(data, args.ref, args.value, args.result, args.verdict)
                if "/" not in args.ref and was not in CLOSED:
                    freed = b.released(data, b.resolve(data, args.ref))
            elif c == "sub":
                b.set_sub(
                    data,
                    args.id,
                    args.sid,
                    before=args.before,
                    remove=args.remove,
                    title=args.title,
                    text=args.text,
                    status=args.status,
                    commit=args.commit,
                    agent=args.agent,
                    branch=args.branch,
                    moved_to=args.moved_to,
                    report=args.report,
                    kind=args.kind,
                )
            elif c == "edit":
                b.edit(
                    data,
                    args.id,
                    args.title,
                    args.detail,
                    args.append,
                    args.prepend,
                    args.plan,
                    args.claim,
                    args.criterion,
                    args.verdict,
                    args.result,
                    args.github,
                    args.short,
                    args.priority,
                    args.assignee,
                    args.repo,
                )
            elif c == "block":
                b.block(data, args.id, args.reason)
            elif c == "unblock":
                b.block(data, args.id, "")
            elif c == "move":
                if b.project_id.match(args.id) and not b.new_issue_id.match(args.id):
                    b.move_project(data, args.id, args.before, args.after)
                else:
                    b.move_issue(data, args.id, args.before, args.after, args.project)
            elif c == "add":
                freed.append(
                    "issue "
                    + b.add_issue(
                        data,
                        args.project,
                        args.id,
                        args.title,
                        args.status,
                        args.detail,
                        args.blocked,
                        args.plan,
                        args.before,
                        args.claim,
                        args.criterion,
                        args.github,
                        args.short,
                    )
                )
            elif c == "project":
                if args.action == "remove":
                    b.remove_project(data, args.id)
                else:
                    if args.action == "add":
                        if not args.title or not args.intro:
                            raise BacklogError("project add needs --title and --intro")
                        b.add_project(data, args.id, args.title, args.intro, args.before)
                    b.edit_project(
                        data,
                        args.id,
                        None if args.action == "add" else args.title,
                        None if args.action == "add" else args.intro,
                        args.status,
                        args.priority,
                        args.repo,
                        args.no_repo,
                    )
            elif c == "relate":
                b.relate(data, args.id, args.kind, args.other, args.remove)
            elif c == "archive":
                b.archive(data, args.id)
            elif c == "decision":
                if args.action == "add":
                    if not args.title:
                        raise BacklogError("decision add needs --title")
                    freed.append(
                        "decision "
                        + b.add_decision(
                            data,
                            args.did or args.id,
                            args.title,
                            args.text,
                            args.option,
                            args.recommend,
                            args.date,
                            args.issue,
                            as_is=args.as_is,
                            recommended=args.recommend_option,
                            blocks=args.blocks,
                        )
                    )
                    holding = data["decisions"][-1].get("blocks", [])
                elif args.action == "blocks":
                    if not args.id or bool(args.refs) == args.none:
                        raise BacklogError("decision blocks needs the decision id and either references or --none")
                    holding = b.set_decision_blocks(data, args.id, args.refs, args.issue)
                    freed.append(f"{args.id} holds: {', '.join(holding) or 'nothing'}")
                else:
                    freed += resolve(b, data, args)
            elif c == "page":
                b.edit_page(data, args.key, args.heading, args.replace)
        for line in freed:
            print(line)
        for w in b.hold_problems(b.load(), holding)[1] if holding else []:
            print(f"foreman backlog: warning: {w}", file=sys.stderr)
        if closing:
            warn = closed_without_commit(project, b, data, *closing)
            if warn:
                print(warn, file=sys.stderr)
        warn_owner_texts(project, args)
        return 0
    except DecisionResolved as e:  # by the panel, or by hand earlier: nothing to do (the data was not saved)
        print(e)
        return 0
    except BacklogError as e:
        print(f"foreman backlog: {e}", file=sys.stderr)
        return 2
