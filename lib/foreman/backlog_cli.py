"""`foreman backlog …`: the subcommands' parser, their run and the text they print (summary, one issue, project or
decision, one field)."""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING, Any

from . import backlog_list, backlog_text, issue_docs, util
from .backlog import CLOSED, UNDO, Backlog, BacklogError, DecisionResolved

if TYPE_CHECKING:
    import argparse

    from .backlog import Data, Issue, Record
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
            why = backlog_text.render_blocked(b, i, rb, "by", holds, "waits for decision")
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
        meta = backlog_text.render_project_meta(b, obj)
        if meta:
            lines.append(meta)
        lines.append(f"\n{obj['intro']}\n")
        lines += [f"  {i['id']:<7} {i['status']:<12} {i['title'][:100]}" for i in obj["issues"]]
        return "\n".join(lines)
    dec = next((d for d in data["decisions"] if d["id"] == key), None) or b.resolved_decision(data, key)
    if dec:
        if as_json:
            return json.dumps(dec, ensure_ascii=False, indent=2)
        done = f"\nresolved {DecisionResolved.said(dec)}" if dec.get("resolved") else ""
        return backlog_text.render_decision(b, dec) + done
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
            if (held := backlog_text.render_held(b, i, b.decision_holds(data), "waits for decision"))
            else ""
        )
        + (f" · github {i['github']}" if i.get("github") else "")
        + (f" · short «{i['short']}»" if i.get("short") else "")
    ]
    # plain text: the bold an owner or an older foreman wrote in a goal, criterion or result is not shown
    fields = (backlog_text.render_fields(b, i), backlog_text.render_outcome(b, i), backlog_text.render_result(b, i))
    out += [re.sub(r"\*\*", "", line) for line in fields if line]
    r = b.relations_of(data, i["id"])
    rels = [f"{k.replace('_', ' ')} {', '.join(v) if isinstance(v, list) else v}" for k, v in r.items() if v]
    if rels:
        out.append("relations: " + " · ".join(rels))
    if i["plan"]:
        out.append("plan: " + " · ".join(p["path"] + (f" {p['note']}" if p["note"] else "") for p in i["plan"]))
    out += [f"  {backlog_text.render_sub(b, s)}" for s in i["subs"]]
    docs = issue_docs.lines(documents_of(b, data, i))
    if docs:
        out += ["documents:", *docs]
    if i["detail"]:
        out.append(f"detail: {i['detail']}")
    return "\n".join(out)


def documents_of(b: Backlog, data: Data, i: Issue) -> list[dict[str, Any]]:
    """The documents of issue `i` that exist (issue_docs), as the panel's card lists them."""
    from . import panel_cards  # a `show` of an issue with documents only

    p = b.project
    ix = panel_cards.doc_index(p)
    return issue_docs.issue_docs(p, data, i, issue_docs.review_files(p), lambda t: panel_cards.doc_ref(p, t, ix))


def add_from_args(b: Backlog, data: Data, args: argparse.Namespace) -> str:
    """`backlog add`: the issue its arguments describe, added to `data`; its id."""
    return b.add_issue(
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


def dry_run_add(b: Backlog, args: argparse.Namespace) -> int:
    """`backlog add --dry-run`: the same add on a copy of the data, with the same checks as the save, and the issue
    shown as `show ID` would. Nothing is written."""
    data = b.load()
    iid = add_from_args(b, data, args)
    b.check_or_raise(data)
    print(f"would add {iid} (dry run)")
    print(show_one(b, data, iid))
    return 0


def get_field(b: Backlog, data: Data, iid: str, field: str) -> str:
    i = b.issue(data, b.resolve(data, iid), archived=True)
    if field == "plan":
        return "\n".join(p["path"] for p in i["plan"])
    if field == "subs":
        return backlog_text.render_subs(b, i["subs"])
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
                                     duplicate; todo = ready) or
                                     sub-issue status (todo "in progress" done canceled moved unknown, or ▶ ✓ ✗ → ?)
  sub ID Tn [--title T] [--text T] [--status S] [--commit C] [--agent A] [--branch B] [--moved-to ID]
            [--report PATH] [--type T[/M]] [--kind measure|code] [--before Tm] [--remove]
                                     --type: the agent type and model it is for (dev/sonnet): launch uses it
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
         [--effect T] [--cost T] [--undo yes|hard|no] (after the --option they describe)
         [--if-no-answer T] [--answer-by D] [--page PATH]
                                     a question for the owner (docs/owner.md): --title the question in one plain
                                     sentence; --text what it is about, why it comes now and what each option
                                     changes; --recommend the option and why; --recommend-option the exact text of
                                     the recommended --option (exit 2 if it is none of them), the one the panel
                                     marks. Texts that name ids or internal names
                                     without explaining them (foreman plain) are refused (exit 2) unless --as-is,
                                     which records it anyway and marks it ("as_is": true, a warning in check).
                                     An --option without --effect is refused too (exit 2, naming it) unless --as-is.
                                     --blocks: what waits for the answer while it is pending (none by default):
                                     Tn or "issue" relative to --issue, or "ISSUE" / "ISSUE Tn"
                                     Each --option is a short answer (a button); after it, --effect what the
                                     owner will see or do differently, --cost its cost or risk, --undo whether it
                                     can be undone (yes; hard: with work; no). --if-no-answer: what stays stopped
                                     until the owner answers (foreman never answers for them); --answer-by: the
                                     day to remind them. --page: a .md under the reviews or notes folders
                                     (screenshots, tables) that explains the question. Warnings on stderr, never
                                     blocking: two questions in one, a reminder date
                                     in the past, a page that is missing, larger than 400 KB or not written
                                     for the question (over 60 lines, another question's page, a report or a
                                     plan, or names left unexplained: docs/owner.md «Question page»)
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
  import-gh REPO --project EN|- --prefix XX [--status S]   open GitHub issues of a configured repo, once each
"""


def add_parser(sp: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    import argparse

    ap = sp.add_parser(
        "backlog",
        help="the project's backlog: issues, projects, decisions for the owner",
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
    for f in ("title", "text", "status", "commit", "agent", "branch", "moved-to", "report", "before", "type"):
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

    class OptionAction(argparse.Action):
        """--option: one more option, and an empty detail for the --effect, --cost and --undo that follow it."""

        def __call__(
            self,
            parser: argparse.ArgumentParser,
            ns: argparse.Namespace,
            values: Any,
            option_string: str | None = None,
        ) -> None:
            ns.option = [*ns.option, values]
            ns.details = [*ns.details, {}]

    class DetailAction(argparse.Action):
        """--effect, --cost, --undo: a detail of the last --option given (an error before any, or given twice)."""

        def __call__(
            self,
            parser: argparse.ArgumentParser,
            ns: argparse.Namespace,
            values: Any,
            option_string: str | None = None,
        ) -> None:
            if not ns.details:
                parser.error(f"{option_string} goes after the --option it describes")
            if self.dest in ns.details[-1]:
                parser.error(f"{option_string} given twice for option {len(ns.details)}")
            ns.details[-1][self.dest] = values

    dp = bp.add_parser("decision").add_subparsers(dest="dcmd", required=True)
    a = dp.add_parser("add")
    a.set_defaults(details=[])
    a.add_argument("--id", dest="did")
    a.add_argument("--issue")
    a.add_argument("--title")  # needed, but checked in run: argparse 3.9 and 3.13+ wrap a required one apart
    a.add_argument("--text", default="")
    a.add_argument("--option", action=OptionAction, default=[], help="an option (repeat it)")
    a.add_argument("--effect", action=DetailAction, help="what the --option before it changes")
    a.add_argument("--cost", action=DetailAction, help="the cost or risk of the --option before it")
    a.add_argument(
        "--undo",
        action=DetailAction,
        choices=("yes", "hard", "no"),
        help="whether the --option before it can be undone (hard: with work)",
    )
    a.add_argument("--if-no-answer", default="", help="what stays stopped until the owner answers")
    a.add_argument("--answer-by", default="", help="the day to remind the owner (YYYY-MM-DD)")
    a.add_argument("--page", default="", help="a .md under the reviews or notes folders explaining it")
    a.add_argument("--recommend", default="")
    a.add_argument("--recommend-option", default="", help="the exact text of the recommended --option")
    a.add_argument("--date")
    a.add_argument("--as-is", action="store_true", help="record the decision although its texts fail foreman plain")
    a.add_argument(
        "--blocks",
        nargs="+",
        action="extend",
        default=[],
        metavar="REF",
        help="what waits for the answer: Tn or issue (with --issue), or ISSUE / ISSUE Tn",
    )
    a = dp.add_parser("resolve")
    a.add_argument("id")
    a.add_argument("--option", action="append", default=[], help="the answer, by number (from 1) or exact text")
    a.add_argument("--answer", default="")
    a.add_argument("--by", default="", help="who answered and when, e.g. «Owner, 21:20 (chat)»")
    a.add_argument("--free", action="store_true", help="--answer is the owner's own words")
    a = dp.add_parser("blocks")
    a.add_argument("id")
    a.add_argument("refs", nargs="*", help="what waits for the answer")
    a.add_argument("--issue")
    a.add_argument("--none", action="store_true", help="it holds nothing")
    dp.add_parser("list").add_argument("--json", action="store_true")
    a = bp.add_parser("import-gh")
    a.add_argument("repo")
    a.add_argument("--project", required=True)
    a.add_argument("--prefix", required=True)
    a.add_argument("--status", default="backlog")


def missing_effects(options: list[str], details: list[dict[str, str]]) -> list[tuple[int, str]] | None:
    """The (number, text) of each --option given without --effect, None when there is none."""
    out = [(n, o) for n, (o, det) in enumerate(zip(options, details), 1) if not (det.get("effect") or "").strip()]
    return out or None


def refuse_no_effect(lacking: list[tuple[int, str]]) -> int:
    """`decision add` with an option that does not say what it changes: name each one, how to fix it, exit 2."""
    import sys

    print("foreman backlog: decision not recorded: an option has no --effect:", file=sys.stderr)
    for n, o in lacking:
        print(f"  option {n} («{o}»)", file=sys.stderr)
    print(
        "Say what the owner will see or do differently with each one: --effect right after its --option. "
        "Or add --as-is to record it as it is.",
        file=sys.stderr,
    )
    return 2


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
        plain.warn(project, "short name", args.short)
    if c in ("edit", "integrated") or (c == "status" and "/" not in args.ref):
        plain.warn(project, "result line", args.result)


def issue_sub(b: Backlog, data: Data, iid: str, sid: str) -> tuple[Issue, Record]:
    """(issue, sub-issue) of `iid` and `sid`; ({}, {}) when there is no such issue or sub-issue."""
    from .backlog import find_sub

    try:
        i = b.issue(data, b.resolve(data, iid))
    except BacklogError:
        return {}, {}
    k = find_sub(i, sid)
    return (i, i["subs"][k]) if k is not None else ({}, {})


def closed_without_commit(project: Project, b: Backlog, data: Data, iid: str, sid: str, was: str | None) -> str:
    """The warning for a code sub-issue just closed `done` by hand without a commit while its branch is still
    unmerged in one of the project's repos: the pass takes a branch whose sub-issues are all closed without a commit
    for one closed on purpose (a measurement), so it will not show as ready to integrate. "" when there is none."""
    from . import util

    i, s = issue_sub(b, data, iid, sid)
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
    with its options numbered as `decision resolve --option N` takes them, each with its effect when it has one,
    what waits without an answer and the page's path."""
    items = b.pending_review(data)
    if as_json:
        return json.dumps(items, ensure_ascii=False, indent=2)
    if not items:
        return "no pending decisions"
    out = [f"{util.plural(len(items), 'pending decision')} ({b.project.owner}), in review order:"]
    for d in items:
        age = f", {util.plural(d['age_days'], 'day')} ago" if d["age_days"] is not None else ""
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
            x = (d.get("details") or {}).get(o) or {}
            extra = [x["cost"]] if x.get("cost") else []
            extra += [b.s[f"undo_{x['undo']}"]] if x.get("undo") in UNDO else []
            if x.get("effect") or extra:
                block.append(
                    "     " + " ".join(filter(None, [x.get("effect"), f"({'; '.join(extra)})" if extra else ""]))
                )
        if d["recommendation"]:
            block.append(f"  recommendation: {d['recommendation']}")
        if u := d.get("unanswered"):
            block.append(f"  if no answer: {u['waits']}" + (f" (reminder on {u['by']})" if u.get("by") else ""))
        if d.get("page"):
            block.append(f"  page: {d['page_path']}")
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
        if c == "import-gh":
            slug = project.repo(args.repo).get("github")
            if not slug:
                raise BacklogError(f"repo {args.repo} has no `github` in foreman.toml")
            with b.mutate() as data:
                added = b.import_gh(data, slug, args.project, args.prefix, args.status)
            print(f"imported {len(added)}: {', '.join(added)}" if added else "nothing new")
            return 0
        d = args.dcmd if c == "decision" else ""
        if d == "list":
            print(show_review(b, b.load(), args.json))
            return 0
        if d == "add" and not args.title:
            raise BacklogError("decision add needs --title")
        if d == "add" and not args.as_is:
            bad = b.decision_findings(
                args.title, args.text, args.option, args.recommend, args.details, args.if_no_answer
            )
            if bad:
                return refuse_decision(bad)
            if (lacking := missing_effects(args.option, args.details)) is not None:
                return refuse_no_effect(lacking)
        if d == "resolve":
            check_resolve_args(args)
        if d == "blocks" and bool(args.refs) == args.none:
            raise BacklogError("decision blocks takes either references or --none")
        if c == "add" and args.dry_run:
            return dry_run_add(b, args)
        freed = []
        holding: list[str] = []  # what a decision just added or changed holds (its warnings go to stderr)
        advice: list[str] = []  # what to fix in a decision just added (Backlog.decision_warnings), on stderr
        closing: tuple[str, str, str | None] | None = None  # (issue, sub, its status before) closed by hand
        with b.mutate() as data:
            if c == "status" and "/" in args.ref:
                iid, _, sid = args.ref.partition("/")
                closing = (iid, sid, issue_sub(b, data, iid, sid)[1].get("status"))
            elif c == "sub" and args.status and not args.commit and not args.remove:
                closing = (args.id, args.sid, issue_sub(b, data, args.id, args.sid)[1].get("status"))
            if c == "status":
                was = b.issue(data, b.resolve(data, args.ref.partition("/")[0])).get("status")
                b.set_status(data, args.ref, args.value, args.result, args.verdict)
                if "/" not in args.ref and was not in CLOSED:
                    freed = b.released(data, b.resolve(data, args.ref))
            elif c == "sub":
                if args.report and not args.remove:
                    from . import delivery

                    if w := delivery.name_warning(args.report, args.id):
                        advice.append(w)
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
                freed.append("issue " + add_from_args(b, data, args))
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
            elif d == "add":
                freed.append(
                    "decision "
                    + b.add_decision(
                        data,
                        args.did,
                        args.title,
                        args.text,
                        args.option,
                        args.recommend,
                        args.date,
                        args.issue,
                        as_is=args.as_is,
                        recommended=args.recommend_option,
                        blocks=args.blocks,
                        details=args.details,
                        waits=args.if_no_answer,
                        answer_by=args.answer_by,
                        page=args.page,
                    )
                )
                new = data["decisions"][-1]
                holding = new.get("blocks", [])
                advice = [f"{new['id']}: {w}" for w in b.decision_warnings(new, data=data)]
            elif d == "blocks":
                holding = b.set_decision_blocks(data, args.id, args.refs, args.issue)
                freed.append(f"{args.id} holds: {', '.join(holding) or 'nothing'}")
            elif d == "resolve":
                freed += resolve(b, data, args)
        for line in freed:
            print(line)
        for w in (b.hold_problems(data, holding)[1] if holding else []) + advice:
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
