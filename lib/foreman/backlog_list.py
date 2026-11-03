"""`foreman backlog list`: the issues of one product, or of every product in the registry, filtered by status,
project, field or sub-issue status; one line each, or counted (in all or by status, project or product),
as text or JSON. It only reads: each product's backlog is loaded with Backlog.load, wherever its foreman.toml puts
it, and walked with Backlog.iter_issues (the projects, the issues without project, the archive with --archived)."""

from __future__ import annotations

import json
import re
import sys
from typing import TYPE_CHECKING, Any

from .backlog import (
    CLOSED,
    EXTRA_ISSUE_KEYS,
    ISSUE_KEYS,
    ISSUE_STATUSES,
    Backlog,
    BacklogError,
    norm_sub_status,
)

if TYPE_CHECKING:
    import argparse

    from .backlog import Issue
    from .config import Project

BY = ("status", "group", "product")
FIELDS = (*ISSUE_KEYS, *EXTRA_ISSUE_KEYS, "acceptance")  # what --has can ask about
NO_GROUP = "-"  # the group of an issue without project (as `move --project -` writes it)
TITLE_WIDTH = 80

Row = dict[str, Any]  # {product, group, archived, issue}


def add_arguments(a: argparse.ArgumentParser) -> None:
    a.add_argument("--all-products", action="store_true", help="every product in the registry, not only this one")
    a.add_argument("--status", nargs="+", action="extend", default=[], metavar="S", help="any of these statuses")
    a.add_argument("--open", action="store_true", help="not done, canceled or duplicate")
    a.add_argument(
        "--group", nargs="+", action="extend", default=[], metavar="EN", help="in any of these projects (- = none)"
    )
    a.add_argument(
        "--has",
        action="append",
        default=[],
        metavar="FIELD[=VALUE]",
        help="the field is set (or equals VALUE, or VALUE is one of its items); repeat it for all of them",
    )
    a.add_argument("--sub-status", metavar="S", help="with at least one sub-issue in this status")
    a.add_argument("--archived", action="store_true", help="the archive too")
    a.add_argument("--count", action="store_true", help="how many, instead of the lines")
    a.add_argument("--by", choices=BY, help="count by status, project or product (implies --count)")
    a.add_argument("--json", action="store_true")


def products(args: argparse.Namespace, project: Project | None) -> list[Project]:
    """The products to read: `project`, or every registered one with a backlog (--all-products); the registry's
    entries that do not load are said on stderr and left out."""
    from . import config

    if not args.all_products:
        assert project is not None
        return [project]
    if args.global_project:
        raise BacklogError("--all-products reads every product: leave out --project")
    errors: list[str] = []
    out = [p for p in config.registered_projects(errors=errors) if Backlog(p).exists()]
    for e in errors:
        print(f"foreman backlog list: left out: {e}", file=sys.stderr)
    return out


def has(i: Issue, spec: str) -> bool:
    """--has FIELD (set: not empty) or FIELD=VALUE (equal, or one of the items or keys of a list or table)."""
    field, eq, value = spec.partition("=")
    v = i.get(field)
    if not eq:
        return v not in (None, "", [], {})
    if isinstance(v, (list, dict)):
        return value in [str(x) for x in v]
    return v is not None and str(v) == value


class Filter:
    """The filters of one `backlog list`, checked once (bad status, field or sub-issue status: BacklogError)."""

    def __init__(self, args: argparse.Namespace) -> None:
        self.status = {Backlog.norm_status({}, s) for s in args.status}
        self.open = args.open
        self.groups = {NO_GROUP if g in ("", NO_GROUP) else g for g in args.group}
        for spec in args.has:
            if spec.partition("=")[0] not in FIELDS:
                raise BacklogError(f"--has: unknown field {spec.partition('=')[0]!r}: {', '.join(FIELDS)}")
        self.has = list(args.has)
        self.sub_status = norm_sub_status(args.sub_status) if args.sub_status else None

    def __call__(self, row: Row) -> bool:
        i = row["issue"]
        return (
            (not self.status or i["status"] in self.status)
            and (not self.open or i["status"] not in CLOSED)
            and (not self.groups or (row["group"] or NO_GROUP) in self.groups)
            and all(has(i, spec) for spec in self.has)
            and (not self.sub_status or any(s.get("status") == self.sub_status for s in i.get("subs") or []))
        )


def rows(project: Project, archived: bool) -> list[Row]:
    b = Backlog(project)
    return [
        {"product": project.name, "group": pid, "archived": arch, "issue": i}
        for pid, i, arch in b.iter_issues(b.load(), archived)
    ]


def key(row: Row, by: str, many: bool) -> str:
    """The --by key of a row: a project is named with its product when the list has several (product/EN)."""
    i = row["issue"]
    if by == "status":
        return i["status"]
    if by == "product":
        return row["product"]
    k = row["group"] or NO_GROUP
    return f"{row['product']}/{k}" if many else k


def counts(found: list[Row], by: str, many: bool) -> dict[str, int]:
    """{key: n}: statuses in workflow order, projects and products in the order they first appear (the file's, the
    registry's)."""
    out: dict[str, int] = {}
    for r in found:
        k = key(r, by, many)
        out[k] = out.get(k, 0) + 1
    if by == "status":
        return {s: out[s] for s in sorted(out, key=lambda s: ISSUE_STATUSES.index(s) if s in ISSUE_STATUSES else 99)}
    return out


def line(row: Row, many: bool) -> str:
    i = row["issue"]
    title = re.sub(r"\*\*|`", "", str(i.get("title") or ""))
    title = title if len(title) <= TITLE_WIDTH else title[: TITLE_WIDTH - 1] + "…"
    group = (row["group"] or NO_GROUP) + ("*" if row["archived"] else "")
    head = f"{row['product']:<10} " if many else ""
    return f"{head}{i['id']:<8} {i['status']:<12} {group:<6} {title}"


def total(s: dict[str, Any], n: int) -> str:
    return (s["backlog_list_total_1"] if n == 1 else s["backlog_list_total_n"]).format(n=n)


def render(found: list[Row], args: argparse.Namespace, many: bool, s: dict[str, Any]) -> str:
    if args.count:
        by = counts(found, args.by, many) if args.by else None
        if args.json:
            return json.dumps({"total": len(found), **({"by": args.by, "counts": by} if by is not None else {})})
        if by is None:
            return total(s, len(found))
        width = max(len(k) for k in by) if by else 0
        return "\n".join([f"{k:<{width}}  {n}" for k, n in by.items()] + [total(s, len(found))])
    if args.json:
        return json.dumps(
            [{"product": r["product"], "group": r["group"], "archived": r["archived"], **r["issue"]} for r in found],
            ensure_ascii=False,
            indent=2,
        )
    if not found:
        return s["backlog_list_none"]
    return "\n".join([line(r, many) for r in found] + [total(s, len(found))])


def run(project: Project | None, args: argparse.Namespace) -> int:
    """`foreman backlog list`: prints the list or the count (--by implies --count); 0, or BacklogError for a bad
    filter or backlog (with --all-products a product whose backlog does not load is left out, said on stderr)."""
    from . import config, i18n

    args.count = args.count or bool(args.by)
    keep = Filter(args)
    found: list[Row] = []
    for p in products(args, project):
        try:
            found += [r for r in rows(p, args.archived) if keep(r)]
        except BacklogError as e:
            if not args.all_products:
                raise
            print(f"foreman backlog list: left out: {p.name}: {e}", file=sys.stderr)
    here = project or config.maybe_resolve()
    s = i18n.for_project(here) if here else i18n.strings()
    print(render(found, args, args.all_products, s))
    return 0
