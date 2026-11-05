"""`foreman permissions`: the product's permissions in plain bands, and a candidate change tested before it applies.

The view (`foreman permissions [--table T] [--json]`) lists what the guard (guard.py) refuses, in four bands:
- builtin: foreman's own rules, for everyone (guard.BUILTIN);
- never: `[deny] patterns`, never without the owner;
- orchestrator: each `[deny.grants]` group, only the marked orchestrator after the owner's yes (grants.py), with
  `grant_minutes` and the group's state as `foreman permissions grants` says it;
- turn: each resource's `guard` prefixes, which must run inside `foreman lock RESOURCE run`.
For each rule, how many rows of the guard table (`[check] guard_table`, or --table) it touches, read the way the guard
reads a command (guard.deny for one pattern; the resource prefixes as check() compares them), and up to two of them
as examples; a product rule with no row is a warning (nothing checks that it does what it should). Each rule says
where it comes from (`from`: foreman, machine, product or both; rules()): a rule the registry's [machine.deny] gives
is marked in the text, and the view counts them and names what the product's `[deny] drop` removed of them
(inherited()). Then the reserved
files (reserved_files): guard.reserved()'s entries for this product and the machine, one line per class with its
paths (as the settings spell them; a path a folder of its class already holds is left out), who may write it and its
refusals (rule file:<class>) of the last REFUSED_DAYS days, and how many other registered products have theirs
reserved too. Then the lifeline.

The lifeline (LIFELINE): the commands the orchestrator runs in every pass (docs/orchestrator.md), with the project's
name and home. Each one must pass for the marked orchestrator's main thread without needing any group: a rule that
refuses one, or puts it in a group (the owner would be asked every time), is a failure that names the rule. Fixed in
code: a product that needs more writes them as allow rows of its table.

The candidate (`foreman permissions --candidate FILE [--table T] [--json]`) loads FILE as the project's foreman.toml,
strictly, with the real home (config.load_project; a load error is exit 2 with its message and nothing else). It
says each rule added, removed or moved to another band (by its pattern), each row of the table (T, else the live
project's guard table) whose verdict changes for an agent or the orchestrator (before → after), the rows of that
table that fail with the candidate, and the lifeline with the candidate. Exit 1 when a row fails or the lifeline
loses a command, else 0 (also when something opens: the diff names each row that does).

The machine's candidate (`foreman permissions --machine-candidate FILE [--json]`, machine_candidate()): FILE is a
copy of the registry with its [machine] changed (only that section is taken; the products and the machine resources
stay the live registry's, and the text says when FILE differs elsewhere). A [machine] value that would be ignored or
left out (config.machine_settings) is exit 2 with its warning and nothing else. For every registered product it
loads the product twice, with the live [machine] and with FILE's, and gives the candidate's test of each (diff(),
opens()), the rules inherited before and after, each machine setting that changes (machine_values()), the rows of
its table that would fall in two or more groups other than before (more_groups(): each group needs its own grant,
so the owner is asked more than once while the row's verdict stays the same) and the warnings it would gain. Exit 2
when a product's table does not read, 1 when a product does not load, a row fails, the lifeline loses a command or
a warning is new, else 0. There is no apply: the registry changes only with the owner's yes (guard.py).

All three only read: nothing is written in the home or in <state> (test_permissions checks names and mtimes). The view
also gives the base: the first 16 hex digits of the live foreman.toml's sha256.

Applying it (`foreman permissions apply FILE [--table T] [--base HASH] [--opens] [--json]`): the candidate names the
foreman.toml it was copied from, by a line `# foreman permissions base: HASH` (dropped when it is written) or --base;
another base (the live file changed meanwhile) or none is exit 2. Then the same test as --candidate (exit 2 or 1
as there, nothing written). Then the owner's rule: a change that allows something that was not allowed
before (opens(): a rule removed or taken out of a band without staying under «never», a row whose verdict gets looser
for an agent or the orchestrator, a longer grant_minutes, a resource's `allow` regex added, a command foreman runs on
its own added or changed, a brake of the integration taken off: point_changes()) is exit 2 without --opens. With
--opens the command is in foreman's own group `permissions` (grants.INTERNAL), so the guard and the hooks module
ask the owner yes/no first (judge() gives the verdict, the same for `foreman integrate` on a branch that changes the
live files); a change that only restricts, or changes nothing that is allowed, needs no
question: the owner's yes in the chat is enough. Who may run it at all is the guard's built-in rule
(guard.PERMISSIONS_APPLY): the marked orchestrator's main thread in its own process. Only then it writes: a copy of
the live foreman.toml (and of the table it replaces) in <state>/permissions/AAAAMMDD-HHMMSS.toml|.tsv, the table
first and foreman.toml after, each from a temporary file of its folder with os.replace (util.write_text_atomic), a
strict load of the result (on a failure the copies go back, exit 1) and a note. The table goes where the
candidate's `[check] guard_table` says (--table with none there: exit 2). It prints the command that undoes it: the
copies as one more candidate, with the new base.

The refusal log (log_refusal, from the guard hook): every refusal of the guard is a line of <state>/guard.jsonl,
{at, kind: builtin|deny|orchestrator|lock|file|error, rule, group?, why?, resource?, file?, tool?, where, who:
agent|orchestrator|other}, and never the command's text (some rules match commands that carry passwords). rule is the
pattern (a built-in one for builtin, the resource's matching prefix for lock, file:<class> for a reserved file a
Write/Edit call named, with its path in file and never what it would write), group the [deny.grants] group of an
orchestrator refusal, why its guard reason, where the Hit's (the lock's: command; tool for a place another tool
names), tool the tool's name for any tool but Bash; who is agent with an agent_id,
orchestrator for the marked session, else other. A failure to write it changes nothing; past LOG_BYTES it keeps its
last LOG_LINES lines.
`foreman permissions --refused [--since 7d] [--json]` counts it by rule; the view adds each rule's count of the last
REFUSED_DAYS days.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import shutil
import sys
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

from . import config, gitpush, grants, guard, i18n, notes, orchestrator, toml_reader, util
from .shell import segments, strip_heredocs, strip_quotes, words

if TYPE_CHECKING:
    import argparse

    from .config import Project
    from .identity import Event
    from .util import PathLike

EXAMPLES = 2  # rows shown per rule
SHORT = 70  # characters of a command shown

# The orchestrator's commands of every pass (docs/orchestrator.md): {p} the project's name, {home} its home, {repo} its
# first repo (or "repo"), each already quoted for the shell.
LIFELINE = (
    "foreman --project {p} start",
    "foreman --project {p} loop",
    "foreman --project {p} check",
    "foreman --project {p} backlog show",
    "foreman --project {p} backlog add E1 --title x",
    'foreman --project {p} note "x"',
    "foreman --project {p} launch dev1 --issue X-1 --sub T0 --repo {repo} --branch dev1/x",
    'foreman --project {p} integrate {repo} dev1/x "X-1 T0" --check',
    "foreman --project {p} permissions grants",
    "foreman --project {p} permissions",
    "foreman --project {p} permissions apply candidate.toml",
    "git -C {home} status",
    "git -C {home} log -1",
    "git -C {home} add -A",
    "git -C {home} commit -m x",
    "cat {home}/foreman.toml",
    "ls {home}",
)


def lifeline(project: Project) -> list[str]:
    """LIFELINE with the project's name, home and first repo."""
    repo = next(iter(project.repos), "repo")
    q = {"p": shlex.quote(project.name), "home": shlex.quote(str(project.home)), "repo": shlex.quote(repo)}
    return [c.format(**q) for c in LIFELINE]


def lifeline_failures(project: Project) -> list[dict[str, str]]:
    """The lifeline commands the marked orchestrator would lose: [{command, kind: deny|lock|group, rule}] (rule: the
    pattern, the resource's prefix or the group)."""
    out = []
    for cmd in lifeline(project):
        ctx = guard.as_role("orchestrator", str(project.home))
        v = guard.check(project, cmd, ctx)
        if v is None and ctx is not None and "passed" in ctx:
            out.append({"command": cmd, "kind": "group", "rule": ", ".join(grants.groups_of(project, cmd))})
        elif v is not None and v[0] == "lock":
            out.append({"command": cmd, "kind": "lock", "rule": str(v[1][0])})
        elif v is not None:
            out.append({"command": cmd, "kind": "deny", "rule": str(v[1])})
    return out


def rules(project: Project) -> list[dict[str, str]]:
    """Every rule of the project in band order: [{band, pattern, from, group?, resource?, only?}], band builtin,
    never, orchestrator or turn; the built-in rule of `permissions apply` (guard.PERMISSIONS_APPLY) has
    only=orchestrator: the marked orchestrator may, after the owner's yes in the chat (and his yes/no with --opens); so
    do the three that refuse an agent only (guard.AGENT_ONLY: its git in a main checkout with a live home, its `foreman
    integrate`, and what reaches the hidden acceptance checks, which a planner may). from: foreman for a built-in
    rule, else machine (the registry's [machine.deny]), product (its foreman.toml) or both (written in the two):
    config.DenyOrigin."""
    origin = project.deny_origin
    out = [{"band": "builtin", "pattern": p, "from": "foreman"} for p in guard.BUILTIN]
    out.append({"band": "builtin", "pattern": guard.PERMISSIONS_APPLY, "only": "orchestrator", "from": "foreman"})
    out += [{"band": "builtin", "pattern": p, "only": "orchestrator", "from": "foreman"} for p in guard.AGENT_ONLY]
    for p in project.section("deny").get("patterns", []):
        out.append({"band": "never", "pattern": str(p), "from": origin["patterns"].get(str(p), "product")})
    for g, pats in grants.groups(project).items():
        mine = origin["grants"].get(g, {})
        out += [{"band": "orchestrator", "group": g, "pattern": p, "from": mine.get(p, "product")} for p in pats]
    for r, res in project.resources.items():
        # a resource's guard prefixes are each product's own, even for a machine resource (config.machine_resources)
        pats = res.get("guard", []) or []
        out += [{"band": "turn", "resource": r, "pattern": str(p), "from": "product"} for p in pats]
    return out


def inherited(project: Project, rules_: list[dict[str, Any]]) -> dict[str, Any]:
    """What the view says of the machine: {rules: how many of rules_ come from it (machine or both), dropped: the
    entries of the product's `[deny] drop` that removed an inherited rule ("group:NAME" for a whole group)}."""
    n = sum(r["from"] in ("machine", "both") for r in rules_)
    return {"rules": n, "dropped": list(project.deny_origin["dropped"])}


def band_of(rule: dict[str, str]) -> str:
    """A rule's band as one word for the diff: never, group:NAME, turn:RESOURCE (builtin: builtin)."""
    if rule["band"] == "orchestrator":
        return f"group:{rule['group']}"
    if rule["band"] == "turn":
        return f"turn:{rule['resource']}"
    return rule["band"]


def touches(rule: dict[str, str], cmd: str, at: gitpush.Resolver) -> bool:
    """Whether a table row's command runs the rule, read as the guard reads it (at: the row's resolver, one per row:
    it caches what git says of each folder)."""
    if rule["band"] == "turn":
        plain = strip_quotes(strip_heredocs(cmd))
        return any(guard.matches_prefix(words(s), rule["pattern"]) for s in segments(plain))
    if rule["pattern"] == guard.MAIN_CHECKOUT:  # no regex: the rule itself, from the row's folder
        return guard.main_checkout_hit(at.project, cmd, next(iter(at.start)) or None) is not None
    if rule["pattern"] == guard.HOLDOUT:  # no regex either: the rule, for an agent that is not a planner
        return guard.holdout_hit(at.project, cmd, next(iter(at.start)) or None) is not None
    try:
        return guard.deny([rule["pattern"]], cmd, at) is not None
    except Exception:  # noqa: BLE001 (a push the resolver cannot read: the guard denies it, but it says nothing of this rule)
        return False


def table_of(project: Project, table: str | None = None) -> Path | None:
    """The guard table: --table, else `[check] guard_table`, else None."""
    t = table or project.section("check").get("guard_table")
    return Path(table).expanduser().resolve() if table else (project.path(t) if t else None)


def _rows(project: Project, path: Path | None, base: PathLike | None = None) -> tuple[list[guard.Row], str]:
    """(table_verdicts, error): no table is ([], "")."""
    if path is None:
        return [], ""
    try:
        return guard.table_verdicts(project, path, base), ""
    except (OSError, ValueError) as e:
        return [], f"{path}: {e}"


def _comparable(path: PathLike) -> str:
    """A path as guard.reserved() writes it: the real path, case-folded on macOS."""
    real = os.path.realpath(path)
    return real.lower() if sys.platform == "darwin" else real


def reserved_files(project: Project, refused: dict[tuple[str, str, str], int] | None = None) -> dict[str, Any]:
    """The reserved-files band (module doc): {classes: [{kind, paths, refused}], others: N}. The entries are
    guard.reserved()'s for this product and the machine, in its order, one per class; each path spelled as the
    project's settings give it (the guard compares them case-folded on macOS), a folder with a trailing /. others:
    how many other registered products have their own config, table and state reserved too. refused: counts() by
    _key, for each class's refusals (rule file:<class>)."""
    projects = config.registered_projects()
    entries = guard.reserved(project, projects)
    reg = config.registry_path()
    spelled: list[Path] = [config.state_root(), reg, reg.parent, *_claude_dirs(), Path.home() / ".claude.json"]
    for p in [project, *(q for q in projects if q.name == project.name)]:
        table = table_of(p)
        spelled += [live_file(p), p.home / "foreman.toml", p.state, *([table] if table else [])]
    shown = {_comparable(p): os.path.realpath(p) for p in spelled}
    shown.update(guard.git_spelled(project, projects))
    by: dict[str, list[tuple[str, bool]]] = {}
    for kind, where, folder, name in entries:
        if name in ("", project.name) and (where, folder) not in by.setdefault(kind, []):
            by[kind].append((where, folder))
    classes = []
    for kind in guard.FILE_KINDS:
        if kind not in by:
            continue
        folders = [w for w, f in by[kind] if f]
        paths = [
            shown.get(w, w) + (os.sep if f else "")
            for w, f in by[kind]
            if not any(o != w and _under(w, o) for o in folders)  # a folder already holds it
        ]
        refused_n = (refused or {}).get(_key("file", f"file:{kind}", None), 0)
        classes.append({"kind": kind, "paths": paths, "refused": refused_n})
    return {"classes": classes, "others": len({e[3] for e in entries if e[3] not in ("", project.name)})}


def _under(path: str, folder: str) -> bool:
    """Whether path is folder or lies under it (both as _comparable gives them)."""
    return path == folder or path.startswith(folder.rstrip(os.sep) + os.sep)


def _claude_dirs() -> list[Path]:
    """Claude Code's own folders as guard.reserved() takes them: ~/.claude, and $CLAUDE_CONFIG_DIR when set."""
    extra = os.environ.get("CLAUDE_CONFIG_DIR")
    return [Path.home() / ".claude", *([Path(os.path.expanduser(extra))] if extra else [])]


def view(project: Project, table: str | None = None) -> dict[str, Any]:
    """The view as a dict (render() prints it)."""
    path = table_of(project, table)
    rows, error = _rows(project, path)
    state: dict[str, str] = {}
    if grants.groups(project):  # `foreman permissions grants`: one `group: state` line each
        for line in grants.lines(project):
            g, _, st = line.partition(": ")
            state[g] = st
    at = [gitpush.Resolver(project, r[3] or str(project.home)) for r in rows]
    refused = {
        _key(c["band"], c["rule"], c.get("group") or c.get("resource")): c["n"]
        for c in counts(project, util.now() - timedelta(days=REFUSED_DAYS))
    }
    out = []
    for rule in rules(project):
        hits = [r for r, a in zip(rows, at) if touches(rule, r[2], a)]
        out.append(
            {
                **rule,
                "rows": len(hits),
                "examples": [r[2] for r in hits[:EXAMPLES]],
                "no_row": not hits and rule["band"] != "builtin",
                "refused": refused.get(
                    _key(rule["band"], rule["pattern"], rule.get("group") or rule.get("resource")), 0
                ),
            }
        )
    return {
        "project": project.name,
        "config": str(live_file(project)),
        "base": base_hash(project),
        "table": str(path) if path else None,
        "table_rows": len(rows),
        "table_error": error or None,
        "grant_minutes": grants.minutes(project, "grant_minutes", 30),
        "groups": {g: state.get(g, "closed") for g in grants.groups(project)},
        "rules": out,
        "inherited": inherited(project, out),
        "reserved": reserved_files(project, refused),
        "no_row": [r["pattern"] for r in out if r["no_row"]],
        "refused_days": REFUSED_DAYS,
        "refused": sum(refused.values()),
        "lifeline": {"commands": len(LIFELINE), "failures": lifeline_failures(project)},
    }


def _short(cmd: str) -> str:
    flat = " ".join(cmd.split())
    return flat if len(flat) <= SHORT else flat[: SHORT - 1] + "…"


def _rule_line(s: dict[str, Any], r: dict[str, Any]) -> str:
    refused = s["perm_refused_week"].format(n=r["refused"], days=REFUSED_DAYS) if r.get("refused") else ""
    where = s[f"perm_from_{r['from']}"] if r.get("from") in ("machine", "both") else ""
    if r["no_row"]:
        return f"  - «{r['pattern']}»{where}: {s['perm_no_row']}" + refused
    rows = (s["perm_rows_1"] if r["rows"] == 1 else s["perm_rows_n"]).format(n=r["rows"])
    ex = "; ".join(f"`{_short(c)}`" for c in r["examples"])
    return f"  - «{r['pattern']}»{where}: {rows}" + (f" ({ex})" if ex else "") + refused


def _inherited_lines(s: dict[str, Any], d: dict[str, Any]) -> list[str]:
    """How many rules come from the machine, and what this product's `[deny] drop` removes of them."""
    out = [s["perm_inherited"].format(n=d["rules"])]
    if d["dropped"]:
        g = config.DROP_GROUP
        names = [s["perm_band_group"].format(group=x[len(g) :]) if x.startswith(g) else f"«{x}»" for x in d["dropped"]]
        out.append(s["perm_dropped"].format(n=len(names), rules=", ".join(names)))
    return out


def _lifeline_lines(project: Project, s: dict[str, Any], life: dict[str, Any]) -> list[str]:
    head = s["perm_lifeline"].format(n=life["commands"])
    if not life["failures"]:
        return [f"{head}: {s['perm_lifeline_ok']}"]
    out = [f"{head}: " + s["perm_lifeline_lost"].format(n=len(life["failures"]))]
    for f in life["failures"]:
        out.append("  - " + s[f"perm_lost_{f['kind']}"].format(command=_short(f["command"]), rule=f["rule"]))
    return out


def _reserved_lines(project: Project, s: dict[str, Any], band: dict[str, Any]) -> list[str]:
    """The reserved-files band: one line per class (its paths, who may write it, its refusals), then the others."""
    out = [s["perm_reserved"]]
    for c in band["classes"]:
        free = s["perm_reserved_claude_free"].format(free=", ".join(f"{x}/" for x in guard.CLAUDE_FREE))
        claude = c["kind"] == "claude"
        paths = ", ".join(x + (free if claude and x.endswith(os.sep) else "") for x in c["paths"])
        if claude:
            paths += s["perm_reserved_claude_more"].format(names=", ".join(guard.CLAUDE_SETTINGS))
        who = s[f"perm_reserved_who_{c['kind']}"].format(owner=project.owner, board=project.board)
        refused = s["perm_refused_week"].format(n=c["refused"], days=REFUSED_DAYS) if c["refused"] else ""
        title = s[f"perm_reserved_{c['kind']}"]
        out.append(f"  - {title}: {paths}. {who}{refused}")
    if band["others"]:
        out.append("  " + s["perm_reserved_others"].format(n=band["others"]))
    return out


def render(project: Project, d: dict[str, Any]) -> str:
    s = i18n.for_project(project)
    lines = [s["perm_heading"].format(project=d["project"], config=d["config"])]
    if d["base"]:
        lines.append(s["perm_base"].format(sha=d["base"]))
    if d["table_error"]:
        lines.append(s["perm_table_error"].format(error=d["table_error"]))
    elif d["table"]:
        lines.append(s["perm_table"].format(table=d["table"], n=d["table_rows"]))
    else:
        lines.append(s["perm_no_table"])
    lines += _inherited_lines(s, d["inherited"])
    owner = project.owner
    by = {b: [r for r in d["rules"] if r["band"] == b] for b in ("builtin", "never", "orchestrator", "turn")}
    lines += ["", s["perm_builtin"]] + [_rule_line(s, r) for r in by["builtin"] if not r.get("only")]
    lines += ["", s["perm_never"].format(owner=owner)]
    lines += [_rule_line(s, r) for r in by["never"]] or [f"  {s['perm_none']}"]
    lines += ["", s["perm_orchestrator"].format(owner=owner, minutes=d["grant_minutes"])]
    if not d["groups"]:
        lines.append(f"  {s['perm_none']}")
    for g, state in d["groups"].items():
        lines.append(s["perm_group"].format(group=g, state=state))
        lines += ["  " + _rule_line(s, r) for r in by["orchestrator"] if r["group"] == g]
    for r in by["builtin"]:
        if r.get("only"):  # `foreman permissions apply` and the agent-only rules: foreman's own, not a group
            head = {
                guard.MAIN_CHECKOUT: "perm_main_checkout_rule",
                guard.INTEGRATE: "perm_integrate_rule",
                guard.HOLDOUT: "perm_holdout_rule",
            }
            lines.append(s[head.get(r["pattern"], "perm_apply_rule")].format(owner=owner))
            lines.append("  " + _rule_line(s, r))
    lines += ["", s["perm_turn"]]
    resources = list(dict.fromkeys(r["resource"] for r in by["turn"]))
    if not resources:
        lines.append(f"  {s['perm_none']}")
    for res in resources:
        lines.append(s["perm_resource"].format(resource=res))
        lines += ["  " + _rule_line(s, r) for r in by["turn"] if r["resource"] == res]
    lines += ["", *_reserved_lines(project, s, d["reserved"])]
    lines.append("")
    if d["no_row"]:
        lines.append(s["perm_warn_no_row"].format(n=len(d["no_row"])))
    lines.append(s["perm_refused_total"].format(n=d["refused"], days=d["refused_days"]))
    lines += _lifeline_lines(project, s, d["lifeline"])
    return "\n".join(lines)


# ---------- the candidate ----------


def label(agent: str, orch: str) -> str:
    """A row's verdict as one word when it can be: allow, deny, lock:R, orchestrator (deny for an agent, allow for
    the orchestrator), else `AGENT/ORCH`."""
    if agent == orch:
        return agent
    if (agent, orch) == ("deny", "allow"):
        return "orchestrator"
    return f"{agent}/{orch}"


def _bands(project: Project) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for r in rules(project):
        if r["band"] != "builtin":
            out.setdefault(r["pattern"], [])
            if band_of(r) not in out[r["pattern"]]:
                out[r["pattern"]].append(band_of(r))
    return out


def diff(project: Project, cand: Project, table: str | None = None, base: PathLike | None = None) -> dict[str, Any]:
    """What the candidate changes against the live project (module doc), as a dict (render_diff() prints it). base:
    the folder a relative @DIR of the table is joined to, when the table is a copy (else the table's folder)."""
    path = table_of(project, table)
    before, after = _bands(project), _bands(cand)
    changed_rules = [{"pattern": p, "before": [], "after": b} for p, b in after.items() if p not in before]
    changed_rules += [{"pattern": p, "before": b, "after": []} for p, b in before.items() if p not in after]
    changed_rules += [
        {"pattern": p, "before": b, "after": after[p]} for p, b in before.items() if p in after and after[p] != b
    ]
    rows_before, error = _rows(project, path, base)
    rows_after, error2 = _rows(cand, path, base)
    rows = []
    for old, new in zip(rows_before, rows_after):
        a, b = label(old[4], old[5]), label(new[4], new[5])
        if a != b:
            rows.append({"line": new[0], "command": new[2], "dir": new[3], "before": a, "after": b})
    name = path.name if path else ""
    failures = [f for f in (guard.row_failure(name, r) for r in rows_after) if f]
    added, removed = table_change(project, path) if table else (0, 0)
    return {
        "project": project.name,
        "config": str(live_file(project)),
        "candidate": str(cand.config_file),
        "table": str(path) if path else None,
        "table_error": error or error2 or None,
        "rules": changed_rules,
        "rows": rows,
        "table_added": added,
        "table_removed": removed,
        "table_failures": failures,
        "lifeline": {"commands": len(LIFELINE), "failures": lifeline_failures(cand)},
    }


def table_change(project: Project, path: Path | None) -> tuple[int, int]:
    """(rows added, rows removed) of the table at path against the live one (`[check] guard_table`), each row as
    written (expect, command, DIR) and counted with its repeats; (0, 0) when either does not read or they are the
    same file."""
    live = table_of(project)
    if path is None or live is None or live.resolve() == path.resolve():
        return 0, 0
    try:
        old = Counter((e, c, f) for _, e, c, f in guard.parse_table(live.read_text()))
        new = Counter((e, c, f) for _, e, c, f in guard.parse_table(path.read_text()))
    except (OSError, ValueError):
        return 0, 0
    return sum((new - old).values()), sum((old - new).values())


def _band_text(s: dict[str, Any], bands: list[str]) -> str:
    out = []
    for b in bands:
        kind, _, name = b.partition(":")
        out.append(s[f"perm_band_{kind}"].format(group=name, resource=name))
    return ", ".join(out)


def _verdict_text(s: dict[str, Any], v: str) -> str:
    if "/" in v:
        agent, orch = v.split("/", 1)
        return s["perm_v_split"].format(agent=_verdict_text(s, agent), orchestrator=_verdict_text(s, orch))
    kind, _, res = v.partition(":")
    return s[f"perm_v_{kind}"].format(resource=res)


def render_diff(project: Project, d: dict[str, Any]) -> str:
    s = i18n.for_project(project)
    lines = [s["perm_candidate"].format(candidate=d["candidate"], config=d["config"], table=d["table"] or "-")]
    if d["table_error"]:
        lines.append(s["perm_table_error"].format(error=d["table_error"]))
    table_moved = d["table_added"] or d["table_removed"]
    if not d["rules"] and not d["rows"]:
        lines.append(s["perm_nothing_table"] if table_moved else s["perm_nothing"])
    else:
        lines.append(s["perm_changes"])
        for r in d["rules"]:
            if not r["before"]:
                lines.append("  " + s["perm_rule_added"].format(pattern=r["pattern"], band=_band_text(s, r["after"])))
            elif not r["after"]:
                lines.append(
                    "  " + s["perm_rule_removed"].format(pattern=r["pattern"], band=_band_text(s, r["before"]))
                )
            else:
                lines.append(
                    "  "
                    + s["perm_rule_moved"].format(
                        pattern=r["pattern"], before=_band_text(s, r["before"]), after=_band_text(s, r["after"])
                    )
                )
        for r in d["rows"]:
            lines.append(
                "  "
                + s["perm_row_changed"].format(
                    line=r["line"],
                    command=_short(r["command"]),
                    before=_verdict_text(s, r["before"]),
                    after=_verdict_text(s, r["after"]),
                )
            )
    if table_moved:
        lines.append(s["perm_table_change"].format(added=d["table_added"], removed=d["table_removed"]))
    if d["table_failures"]:
        lines.append(s["perm_table_fails"].format(n=len(d["table_failures"])))
        lines += [f"  {f}" for f in d["table_failures"]]
    lines += _lifeline_lines(project, s, d["lifeline"])
    return "\n".join(lines)


# ---------- applying it ----------

BASE_LINE = re.compile(r"^#[ \t]*foreman permissions base:[ \t]*([0-9a-fA-F]*)[ \t]*$\n?", re.M)
BASE_MIN = 8  # hex digits of a base that are compared
RANK = {"allow": 0, "deny": 2}  # a verdict's weight; lock:R is 1


def live_file(project: Project) -> Path:
    return project.config_file or project.home / "foreman.toml"


def base_hash(project: Project) -> str | None:
    """The first 16 hex digits of the sha256 of the live foreman.toml (None when it does not read): the base a
    candidate names (BASE_LINE, or apply --base) so that apply refuses one copied before a later change."""
    try:
        return hashlib.sha256(live_file(project).read_bytes()).hexdigest()[:16]
    except OSError:
        return None


def _roles(v: str) -> tuple[str, str]:
    """label() back to (agent, orchestrator)."""
    if v == "orchestrator":
        return "deny", "allow"
    if "/" in v:
        a, b = v.split("/", 1)
        return a, b
    return v, v


def _looser(before: str, after: str) -> bool:
    """Whether a verdict lets more through: deny → lock or allow, lock → allow, or another resource's lock."""
    rb, ra = RANK.get(before, 1), RANK.get(after, 1)
    return ra < rb or (ra == rb == 1 and before != after)


def opens(project: Project, cand: Project, d: dict[str, Any]) -> list[str]:
    """What the candidate allows that was not allowed before, one line each in the project's language (empty: it
    only restricts, or changes nothing that is allowed): a rule removed or taken out of a band (unless it stays
    under «never»), a table row whose verdict gets looser for an agent or the orchestrator, a longer grant_minutes,
    a resource's `allow` regex added (it skips the turn), and each opening entry of point_changes(): a command
    foreman runs on its own added or changed, a brake of the integration taken off."""
    s = i18n.for_project(project)
    out = []
    for r in d["rules"]:
        lost = [b for b in r["before"] if b not in r["after"]]
        if lost and "never" not in r["after"]:
            key, kw = (
                ("perm_rule_removed", {"band": _band_text(s, r["before"])})
                if not r["after"]
                else (
                    "perm_rule_moved",
                    {"before": _band_text(s, r["before"]), "after": _band_text(s, r["after"])},
                )
            )
            out.append(s[key].format(pattern=r["pattern"], **kw))
    for r in d["rows"]:
        if any(_looser(a, b) for a, b in zip(_roles(r["before"]), _roles(r["after"]))):
            out.append(
                s["perm_row_changed"].format(
                    line=r["line"],
                    command=_short(r["command"]),
                    before=_verdict_text(s, r["before"]),
                    after=_verdict_text(s, r["after"]),
                )
            )
    before, after = grants.minutes(project, "grant_minutes", 30), grants.minutes(cand, "grant_minutes", 30)
    if after > before:
        out.append(s["perm_opens_minutes"].format(before=before, after=after))
    for name, res in cand.resources.items():
        old = (project.resources.get(name) or {}).get("allow", []) or []
        out += [
            s["perm_opens_lock_allow"].format(pattern=a, resource=name)
            for a in res.get("allow", []) or []
            if a not in old and res.get("guard")
        ]
    for c in point_changes(project, cand):
        if c["opens"]:
            key = "perm_opens_brake" if c["key"] in BRAKE_KEYS else "perm_opens_command"
            out.append(s[key].format(where=c["where"], before=_value(c["before"]), after=_value(c["after"])))
    return out


# What judge() says of a candidate, the order of apply's (and integrate's) cut-offs: broken (the table does not read),
# fails (a row fails or the lifeline loses a command), opens (it allows something new: the owner's yes, --opens), ok.
VERDICTS = ("broken", "fails", "opens", "ok")


def judge(
    project: Project, cand: Project, table: str | None = None, base: PathLike | None = None
) -> tuple[dict[str, Any], str]:
    """The test of a candidate that `apply` and `foreman integrate` share: (diff() with its `opens` list, verdict),
    the verdict one of VERDICTS. A candidate that does not load is the caller's (broken too)."""
    d = diff(project, cand, table, base)
    d["opens"] = opens(project, cand, d)
    if d["table_error"]:
        return d, "broken"
    if d["table_failures"] or d["lifeline"]["failures"]:
        return d, "fails"
    return d, "opens" if d["opens"] else "ok"


# The keys that are commands foreman runs on its own (commands.POINTS, with the orchestrator's power and unseen by the
# guard) or brakes of an integration, besides `[loop] checks` and `[resources.*] busy`: point_changes() says which a
# candidate changes and whether each one opens (the owner's yes, as for a rule).
COMMAND_KEYS = (
    "local_ci",
    "ci_runs",
    "main_ci",
    "ci_summary",
    "privacy_scan",
    "pre_merge_report",
    "post_merge",
    "post_create",
)
BRAKE_KEYS = (
    "require_approval",
    "publish_on_merge",
    "public",
    "remote_checks",
    "risky_paths",
    "review",
    "review_paths",
    "review_lines",
)
# A brake comes off when its value goes from the strict one to another: a flag from true to false, remote_checks from
# required, review from auto (risky_paths and review_paths: an entry removed; review_lines: raised, or set to 0, which
# turns the size off).
_BRAKE_ON = {
    "require_approval": True,
    "publish_on_merge": True,
    "public": True,
    "remote_checks": "required",
    "review": "auto",
}


def _brake_off(key: str, before: Any, after: Any) -> tuple[Any, Any] | None:
    """(before, after) to show when the brake `key` comes off, else None (it stays, or gets stricter)."""
    if key in ("risky_paths", "review_paths"):
        lost = [x for x in before or [] if x not in (after or [])]
        return (lost, None) if lost else None
    if key == "review_lines":
        b, a = int(before or 0), int(after or 0)
        return (before, after) if b and (not a or a > b) else None
    on = _BRAKE_ON[key]
    return (before, after) if (bool(before) if on is True else before == on) and after != before else None


def point_changes(project: Project, cand: Project) -> list[dict[str, Any]]:
    """The commands and brakes the candidate changes, in order: `[loop] checks`, `[resources.R] busy`, then each
    repo's COMMAND_KEYS and BRAKE_KEYS. Each one {where: "[section] key", key, before, after, opens}: opens when a
    command is added or changed (emptied too: a CI that no longer runs) or a brake comes off (_brake_off). A resource
    or a repo the candidate removes changes its keys without opening anything (nothing of it runs any more); one it
    adds opens with each command it sets."""
    out: list[dict[str, Any]] = []

    def add(where: str, key: str, before: Any, after: Any, opening: bool) -> None:
        out.append({"where": where, "key": key, "before": before, "after": after, "opens": opening})

    old, new = project.section("loop").get("checks"), cand.section("loop").get("checks")
    if old != new:
        add("[loop] checks", "checks", old, new, True)
    for name in dict.fromkeys([*project.resources, *cand.resources]):
        old, new = (project.resources.get(name) or {}).get("busy"), (cand.resources.get(name) or {}).get("busy")
        if old != new:
            add(f"[resources.{name}] busy", "busy", old, new, name in cand.resources)
    for repo in dict.fromkeys([*project.repos, *cand.repos]):
        was, now = project.repos.get(repo) or config.REPO_DEFAULTS, cand.repos.get(repo)
        for k in (*COMMAND_KEYS, *BRAKE_KEYS):
            before, after = was.get(k), (now or config.REPO_DEFAULTS).get(k)
            if before == after:
                continue
            off = _brake_off(k, before, after) if k in BRAKE_KEYS else None
            opening = now is not None and (k in COMMAND_KEYS or off is not None)
            if off is not None and now is not None:
                before, after = off
            add(f"[repos.{repo}] {k}", k, before, after, opening)
    return out


def also_changes(project: Project, cand: Project) -> list[str]:
    """The commands and brakes the candidate changes without opening anything (point_changes(); a brake made
    stricter, a repo removed), one `[section] key` each: shown at integration, the owner is not asked."""
    return [c["where"] for c in point_changes(project, cand) if not c["opens"]]


def _value(v: Any) -> str:
    """A command's or a brake's value for opens(): «text» (each item of a list), or — when empty or unset."""
    items = v if isinstance(v, list) else [v]
    shown = [f"«{_short(str(x))}»" if isinstance(x, str) else str(x).lower() for x in items if x not in (None, "")]
    return ", ".join(shown) or "—"


def _backup(project: Project, files: list[tuple[Path, str]]) -> list[Path]:
    """Copies each live file to <state>/permissions/AAAAMMDD-HHMMSS(-N).SUFFIX (one stamp for all); returns them."""
    folder = project.state / "permissions"
    folder.mkdir(parents=True, exist_ok=True)
    stamp = util.now().strftime("%Y%m%d-%H%M%S")
    n, tag = 1, stamp
    while any((folder / f"{tag}{suffix}").exists() for _, suffix in files):
        n += 1
        tag = f"{stamp}-{n}"
    out = []
    for src, suffix in files:
        dst = folder / f"{tag}{suffix}"
        shutil.copy2(src, dst)
        out.append(dst)
    return out


def _note(project: Project, d: dict[str, Any], opened: list[str], table: Path | None, backup: list[Path]) -> None:
    s = i18n.for_project(project)
    lines = []
    for r in d["rules"]:
        if not r["before"]:
            lines.append(s["perm_rule_added"].format(pattern=r["pattern"], band=_band_text(s, r["after"])))
        elif not r["after"]:
            lines.append(s["perm_rule_removed"].format(pattern=r["pattern"], band=_band_text(s, r["before"])))
        else:
            lines.append(
                s["perm_rule_moved"].format(
                    pattern=r["pattern"], before=_band_text(s, r["before"]), after=_band_text(s, r["after"])
                )
            )
    if d["rows"]:
        lines.append(s["perm_note_rows"].format(n=len(d["rows"])))
    if d["table_added"] or d["table_removed"]:
        lines.append(s["perm_table_change"].format(added=d["table_added"], removed=d["table_removed"]))
    if not lines:
        lines.append(s["perm_note_other"])
    if table is not None:
        lines.append(s["perm_note_table"].format(table=table))
    if opened:
        lines.append(s["perm_note_opens"].format(owner=project.owner))
    lines.append(s["perm_note_backup"].format(backup=", ".join(str(b) for b in backup)))
    notes.note(project, s["perm_note_title"], "\n".join(f"- {x}" for x in lines))


def apply(project: Project, args: argparse.Namespace) -> int:
    """`foreman permissions apply FILE [--table T] [--base HASH] [--opens]` (module doc): exit 0 applied (or nothing
    to write), 1 a row fails, the lifeline loses a command or the reload failed (the previous files back), 2 the
    candidate does not load, names no base or another one, the table does not read or has nowhere to go, or it
    opens something without --opens. Nothing is written but on 0."""
    s = i18n.for_project(project)

    def refuse(text: str, rc: int = 2) -> int:
        print(f"foreman permissions apply: {text}", file=sys.stderr)
        return rc

    try:
        cand = config.load_project(project.home, project.mode, config_file=args.file, strict=True)
        raw = Path(args.file).expanduser().read_text()
    except (config.ConfigError, OSError) as e:
        return refuse(str(e))
    named = [m.group(1).lower() for m in BASE_LINE.finditer(raw)]
    base = (args.base or (named[0] if named else "")).lower()
    sha_full = hashlib.sha256(live_file(project).read_bytes()).hexdigest()
    if len(base) < BASE_MIN:
        return refuse(s["perm_apply_no_base"])
    if not sha_full.startswith(base):
        return refuse(s["perm_apply_changed"].format(base=base, sha=sha_full[:16]))
    d, verdict = judge(project, cand, args.table)
    opened = d["opens"]
    print(json.dumps(d, ensure_ascii=False, indent=1) if args.json else render_diff(project, d))
    if opened and not args.json:
        print("\n".join([s["perm_opens"]] + [f"  {x}" for x in opened]))
    if verdict == "broken":
        return 2
    if verdict == "fails":
        return refuse(s["perm_apply_fails"], 1)
    if opened and not args.opens:
        return refuse(s["perm_apply_opens"].format(owner=project.owner))
    live = live_file(project)
    text = BASE_LINE.sub("", raw)
    src = Path(args.table).expanduser().resolve() if args.table else None
    dest = table_of(cand) if src else None
    if src and dest is None:
        return refuse(s["perm_apply_no_table"].format(table=src))
    new_table = src.read_text() if src and dest and src != dest.resolve() else None
    if new_table is not None and dest is not None and dest.exists() and dest.read_text() == new_table:
        new_table = None
    if text == live.read_text() and new_table is None:
        print(s["perm_apply_same"])
        return 0
    saved = [(live, ".toml")] + ([(dest, ".tsv")] if new_table is not None and dest and dest.exists() else [])
    backup = _backup(project, saved)
    if new_table is not None and dest is not None:  # the table first: the guard reads only foreman.toml
        util.write_text_atomic(dest, new_table)
    util.write_text_atomic(live, text)
    try:
        config.load_project(project.home, project.mode, config_file=project.config_file, strict=True)
    except config.ConfigError as e:
        for (orig, _), copy in zip(saved, backup):
            util.write_text_atomic(orig, copy.read_text())
        return refuse(s["perm_apply_reload"].format(error=e), 1)
    _note(project, d, opened, dest if new_table is not None else None, backup)
    undo = f"foreman --project {shlex.quote(project.name)} permissions apply {shlex.quote(str(backup[0]))}"
    if len(backup) > 1:
        undo += f" --table {shlex.quote(str(backup[1]))}"
    undo += f" --base {base_hash(project)}"
    files = str(live) + (f", {dest}" if new_table is not None else "")
    print(s["perm_applied"].format(files=files, backup=", ".join(str(b) for b in backup), undo=undo))
    return 0


# ---------- the refusal log ----------

LOG_LINES = 10_000  # lines kept once the log passes LOG_BYTES
LOG_BYTES = 2 * 1024 * 1024
REFUSED_DAYS = 7  # the view's count
# log kind → band
BAND = {"builtin": "builtin", "deny": "never", "orchestrator": "orchestrator", "lock": "turn", "file": "file"}


def log_file(project: Project) -> Path:
    return project.state / "guard.jsonl"


def refusal(project: Project, ev: Event, verdict: guard.Verdict) -> dict[str, Any]:
    """The log line of a refusal (module doc): what refused it and who, without the command's text."""
    kind, detail = verdict
    rec: dict[str, Any] = {"at": util.now().strftime("%Y-%m-%dT%H:%M:%S"), "kind": kind}
    if ev.get("tool_name") not in (None, "Bash"):
        rec["tool"] = ev.get("tool_name")  # a Write's file, a Read's path or a Monitor's command: never its text
    if kind == "lock":
        name, seg = detail
        res = project.resources.get(name) or {}
        pats = [str(p) for p in res.get("guard", []) or [] if guard.matches_prefix(seg.split(), p)]
        rec.update(rule=pats[0] if pats else name, resource=name, where="command")
    elif not isinstance(detail, guard.Hit):  # the guard itself failed: detail names the exception, not the command
        rec.update(kind="error", rule=str(detail), where=None)
    else:
        rec.update(rule=str(detail), where=detail.where)
        if detail in guard.BUILTIN or detail == guard.PERMISSIONS_APPLY or detail in guard.AGENT_ONLY:
            rec["kind"] = "builtin"
        elif kind == "orchestrator":
            group = getattr(detail, "group", None) or next(
                (g for g, pats in grants.groups(project, True).items() if str(detail) in pats), None
            )
            rec.update(group=group, why=getattr(detail, "why", None))
        elif kind == "file":  # a reserved file (guard.check_file): its path, never what the call would write
            rec.update(file=detail.path, why=detail.why or None)
    if ev.get("agent_id"):
        rec["who"] = "agent"
    elif ev.get("session_id") and ev.get("session_id") == (orchestrator.current(project) or {}).get("session"):
        rec["who"] = "orchestrator"
    else:
        rec["who"] = "other"
    return rec


def log_refusal(project: Project, ev: Event, verdict: guard.Verdict) -> None:
    """A line in <state>/guard.jsonl for a refusal of the guard hook. Never raises: the verdict stands without it."""
    try:
        f = log_file(project)
        line = json.dumps(refusal(project, ev, verdict), ensure_ascii=False) + "\n"
        f.parent.mkdir(parents=True, exist_ok=True)
        with notes.file_lock(f):
            with open(f, "a") as out:
                out.write(line)
            if f.stat().st_size > LOG_BYTES:
                util.write_text_atomic(f, "".join(f.read_text(errors="replace").splitlines(True)[-LOG_LINES:]))
    except Exception:  # noqa: BLE001 (the log never changes the guard's verdict)
        pass


def refusals(project: Project, since: datetime) -> list[dict[str, Any]]:
    """The log's lines at or after since (naive local time), oldest first; unreadable lines are skipped."""
    cut = since.strftime("%Y-%m-%dT%H:%M:%S")
    try:
        text = log_file(project).read_text(errors="replace")
    except OSError:
        return []
    out = []
    for ln in text.splitlines():
        try:
            rec = json.loads(ln)
        except ValueError:
            continue
        if isinstance(rec, dict) and str(rec.get("at", "")) >= cut:
            out.append(rec)
    return out


def _key(band: str, rule: object, owner: object) -> tuple[str, str, str]:
    """What a refusal and a rule of the view are matched by: (band, pattern, group or resource)."""
    return band, str(rule), str(owner or "")


def counts(project: Project, since: datetime) -> list[dict[str, Any]]:
    """The refusals since, by rule, most first: [{kind, band, rule, group?, resource?, n, who: {who: n}}]."""
    by: dict[tuple[str, str, str], dict[str, Any]] = {}
    for rec in refusals(project, since):
        kind = str(rec.get("kind"))
        band = BAND.get(kind, "error")
        owner = rec.get("group") if band == "orchestrator" else rec.get("resource") if band == "turn" else None
        c = by.setdefault(
            _key(band, rec.get("rule"), owner),
            {"kind": kind, "band": band, "rule": str(rec.get("rule")), "n": 0, "who": {}},
        )
        if owner:
            c["group" if band == "orchestrator" else "resource"] = owner
        c["n"] += 1
        who = rec.get("who") if rec.get("who") in ("agent", "orchestrator") else "other"
        c["who"][who] = c["who"].get(who, 0) + 1
    return sorted(by.values(), key=lambda c: -c["n"])


def refused_view(project: Project, since_arg: str) -> dict[str, Any]:
    """`--refused` as a dict (render_refused prints it); ValueError for a --since it cannot read."""
    since = util.parse_when(since_arg, util.now().astimezone()).astimezone().replace(tzinfo=None)
    rules_ = counts(project, since)
    return {
        "project": project.name,
        "log": str(log_file(project)),
        "since": since.strftime("%Y-%m-%dT%H:%M:%S"),
        "total": sum(c["n"] for c in rules_),
        "rules": rules_,
    }


def render_refused(project: Project, d: dict[str, Any]) -> str:
    s = i18n.for_project(project)
    lines = [s["perm_refused_heading"].format(since=d["since"].replace("T", " ")[:16], log=d["log"], n=d["total"])]
    if not d["rules"]:
        lines.append(f"  {s['perm_none']}")
    for c in d["rules"]:
        band = {"orchestrator": f"group:{c.get('group')}", "turn": f"turn:{c.get('resource')}"}.get(c["band"])
        who = ", ".join(s[f"perm_who_{w}"].format(n=n) for w, n in sorted(c["who"].items(), key=lambda x: -x[1]))
        lines.append(
            s["perm_refused_line"].format(n=c["n"], rule=c["rule"], band=_band_text(s, [band or c["band"]]), who=who)
        )
    return "\n".join(lines)


# ---------- the machine's candidate ----------


def machine_values(project: Project) -> dict[str, Any]:
    """The values a [machine] may set (config.MACHINE_SETTINGS) as the project has them after the merge, by
    `section.key`; deny's patterns and grants are left out (they are rules: diff() compares them)."""
    out: dict[str, Any] = {}
    for sec, keys in config.MACHINE_SETTINGS.items():
        table = project.section(sec)
        for k in keys if keys is not None else sorted(table):
            if k in table and not (sec == "deny" and k in ("patterns", "grants")):
                out[f"{sec}.{k}"] = table[k]
    return out


def setting_changes(project: Project, cand: Project) -> list[dict[str, Any]]:
    """[{key, before, after}] of each machine_values() key that differs (None: unset)."""
    before, after = machine_values(project), machine_values(cand)
    keys = list(before) + [k for k in after if k not in before]
    return [{"key": k, "before": before.get(k), "after": after.get(k)} for k in keys if before.get(k) != after.get(k)]


def more_groups(project: Project, cand: Project, rows: list[guard.Row]) -> list[dict[str, Any]]:
    """The Bash rows of the candidate's table verdicts (rows) that would fall in two or more [deny.grants] groups,
    and not in the same ones as before: [{line, command, before, after}] (the groups). Each group needs its own live
    grant (guard._why), so such a row asks the owner more than once while its verdict, and the diff, stay the same.
    A row that «never» refuses anyway, or a file:/tool: row, is left out."""
    out = []
    for n, _, cmd, folder, _agent, orch in rows:
        if cmd.startswith(("file:", "tool:")) or orch == "deny":
            continue
        cwd = folder or str(project.home)
        after = grants.groups_of(cand, guard.row_text(cand, cmd), gitpush.Resolver(cand, cwd))
        if len(after) < 2:
            continue
        before = grants.groups_of(project, guard.row_text(project, cmd), gitpush.Resolver(project, cwd))
        if set(after) != set(before):
            out.append({"line": n, "command": cmd, "before": before, "after": after})
    return out


def _outside(reg: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in reg.items() if k != "machine"}


def machine_candidate(path: PathLike) -> tuple[dict[str, Any], int, dict[str, Project]]:
    """`--machine-candidate FILE` (module doc): (the result, exit, the live products by name). FILE is a copy of the
    registry with [machine] changed; only its [machine] is taken, the products and the machine resources stay the
    live registry's. A FILE that does not read, or whose [machine] has a value that would be ignored or left out
    (config.machine_settings), is ({error}, 2, {}). For each registered product, the live load against one with the
    candidate's settings: diff() with opens(), the inherited rules before and after, the settings that change, the
    rows that would need more groups' grants (more_groups) and the new warnings. Exit 2 when a product's table does
    not read, 1 when a product does not load, a row fails, the lifeline loses a command or a warning is new (`foreman
    check` would fail), else 0. Reads only."""
    f = Path(path).expanduser()
    try:
        cand_reg = toml_reader.loads(f.read_text())
    except (OSError, toml_reader.TOMLDecodeError) as e:
        return {"error": f"{f}: {e}"}, 2, {}
    after = config.machine_settings(cand_reg)
    if after[1]:
        return {"error": f"{f}: " + "; ".join(after[1])}, 2, {}
    reg = config.read_registry()
    entries, broken, _ = config.registry_scan(reg)
    machine = config.machine_resources(reg)
    before = config.machine_settings(reg)
    products: list[dict[str, Any]] = []
    loaded: dict[str, Project] = {}
    code = 0
    for e in entries:
        try:
            live = config.load_entry(e, machine=machine, settings=before)
            cand = config.load_entry(e, machine=machine, settings=after)
        except config.ConfigError as err:
            products.append({"project": e["name"], "error": str(err)})
            code = max(code, 1)
            continue
        loaded[live.name] = live
        d = diff(live, cand)
        d["opens"] = opens(live, cand, d)
        d["inherited"] = {
            "before": inherited(live, rules(live))["rules"],
            "after": inherited(cand, rules(cand))["rules"],
        }
        d["settings"] = setting_changes(live, cand)
        rows, _ = _rows(cand, table_of(cand))
        d["more_groups"] = more_groups(live, cand, rows)
        d["warnings"] = [w for w in cand.warnings if w not in live.warnings]
        products.append(d)
        if d["table_error"]:
            code = 2
        elif d["table_failures"] or d["lifeline"]["failures"] or d["warnings"]:
            code = max(code, 1)
    outside = _outside(cand_reg)
    out = {
        "candidate": str(f),
        "registry": str(config.registry_path()),
        "outside": bool(outside) and outside != _outside(reg),
        "products": products,
        "skipped": [{"project": b["name"], "error": b["error"]} for b in broken],
    }
    return out, code, loaded


def render_machine(project: Project, m: dict[str, Any], code: int, loaded: dict[str, Project]) -> str:
    """The text of machine_candidate(): a heading in this project's language, then each product in its own."""
    s = i18n.for_project(project)
    lines = [s["perm_machine_heading"].format(candidate=m["candidate"], registry=m["registry"], n=len(m["products"]))]
    if m["outside"]:
        lines.append(s["perm_machine_outside"])
    for d in m["products"]:
        lines.append("")
        p = loaded.get(d["project"])
        if "error" in d or p is None:
            lines.append(s["perm_machine_error"].format(project=d["project"], error=d.get("error", "-")))
            continue
        ps = i18n.for_project(p)
        lines.append(
            ps["perm_machine_product"].format(project=d["project"], config=d["config"], table=d["table"] or "-")
        )
        lines.append("  " + ps["perm_machine_inherited"].format(**d["inherited"]))
        if d["settings"]:
            lines.append("  " + ps["perm_machine_settings"])
            lines += [
                "    "
                + ps["perm_machine_setting"].format(key=c["key"], before=_value(c["before"]), after=_value(c["after"]))
                for c in d["settings"]
            ]
        lines += ["  " + x for x in render_diff(p, d).splitlines()[1:]]
        if d["more_groups"]:
            lines.append("  " + ps["perm_machine_groups"].format(n=len(d["more_groups"])))
            for r in d["more_groups"]:
                lines.append(
                    "    "
                    + ps["perm_machine_groups_row"].format(
                        line=r["line"],
                        command=_short(r["command"]),
                        before=", ".join(r["before"]) or s["perm_none"],
                        after=", ".join(r["after"]),
                    )
                )
        if d["opens"]:
            lines.append("  " + ps["perm_opens"])
            lines += [f"    {x}" for x in d["opens"]]
        if d["warnings"]:
            lines.append("  " + ps["perm_machine_warnings"])
            lines += [f"    {w}" for w in d["warnings"]]
    for b in m["skipped"]:
        lines.append(s["perm_machine_error"].format(project=b["project"], error=b["error"]))
    lines.append("")
    lines.append(s["perm_machine_fails"] if code else s["perm_machine_ok"])
    return "\n".join(lines)


# ---------- the command ----------


def add_parser(sp: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    a = sp.add_parser(
        "permissions",
        help="what the guard refuses, in plain bands; --candidate: what a new foreman.toml would change; "
        "apply FILE: test it and put it in place; grants: the owner's grants",
        allow_abbrev=False,  # --opens is read by the guard as written (grants.INTERNAL)
    )
    a.add_argument(
        "action",
        nargs="?",
        choices=["apply", "grants"],
        help="apply FILE: test the candidate and put it in place; grants [off [GROUP]]: the owner's grants for the "
        "[deny.grants] groups (granted until, cooling down, waiting, closed), off removes them (every group or GROUP)",
    )
    a.add_argument("file", nargs="?", metavar="FILE", help="the candidate foreman.toml (with apply); off (with grants)")
    a.add_argument("group", nargs="?", metavar="GROUP", help="with grants off: only this group")
    a.add_argument(
        "--candidate",
        metavar="FILE",
        help="a candidate foreman.toml, loaded strictly with the real home: the rules and table rows it changes, the "
        "rows that fail with it and the lifeline (exit 1 if one fails, 2 if it does not load)",
    )
    a.add_argument(
        "--machine-candidate",
        metavar="FILE",
        help="a copy of the registry with its [machine] changed: for every registered product, the rules, settings and "
        "table rows it changes, the rows that would need more groups' grants, what it opens and the lifeline; only "
        "reads (exit 1 if a product loses a row or a lifeline command or gets a warning, 2 if FILE or a table does "
        "not read)",
    )
    a.add_argument(
        "--table",
        metavar="T",
        help="the guard table to use (default: [check] guard_table); with apply, the candidate table that replaces it",
    )
    a.add_argument(
        "--base",
        metavar="HASH",
        help="with apply: the hash of the foreman.toml the candidate was copied from (`foreman permissions` shows it; "
        "else the candidate's line `# foreman permissions base: HASH`)",
    )
    a.add_argument(
        "--opens",
        action="store_true",
        help="with apply: the change allows something that was not allowed before; the guard asks the owner yes/no",
    )
    a.add_argument(
        "--refused",
        action="store_true",
        help="the guard's refusals by rule, from <state>/guard.jsonl (the rule and who, never the command)",
    )
    a.add_argument("--since", metavar="WHEN", help="with --refused: from when (7d, 24h, HH:MM, a date; default 7d)")
    a.add_argument(
        "--json",
        action="store_true",
        help="every rule, row and failure as JSON; with grants: {grants, cooldowns, pending, once} as in "
        "<state>/grants.json",
    )


def grants_command(p: Project, action: str | None, group: str | None, as_json: bool) -> int:
    """`foreman permissions grants [off [GROUP]] [--json]` (grants.py)."""
    from . import approved

    if action != "off":
        if action is not None or group:
            print("usage: foreman permissions grants [off [GROUP]] [--json]", file=sys.stderr)
            return 2
        if as_json:
            print(json.dumps(grants.read(p), ensure_ascii=False, indent=1))
        else:
            print("\n".join(grants.lines(p)))
        return 0
    if group and group not in grants.groups(p, True):
        print(f"foreman permissions grants: no [deny.grants] group {group!r} in {p.name}", file=sys.stderr)
        return 2
    gone = grants.revoke(p, group)
    session = os.environ.get("CLAUDE_CODE_SESSION_ID") or None
    for g in gone:
        approved.granted(p, {"verdict": "revoke", "group": g, "session": session})
    if gone:
        notes.note(p, "-", f"grants removed with `foreman permissions grants off`: {', '.join(gone)}")
    print(f"removed: {', '.join(gone)}" if gone else "no grant to remove")
    return 0


def run(args: argparse.Namespace, project: Project) -> int:
    if args.machine_candidate:
        if (
            args.action
            or args.file
            or args.candidate
            or args.table
            or args.base
            or args.opens
            or args.refused
            or args.since
        ):
            print("foreman permissions: --machine-candidate FILE goes alone (with --json)", file=sys.stderr)
            return 2
        m, code, loaded = machine_candidate(args.machine_candidate)
        if "error" in m:
            print(f"foreman permissions: --machine-candidate {m['error']}", file=sys.stderr)
            return code
        print(json.dumps(m, ensure_ascii=False, indent=1) if args.json else render_machine(project, m, code, loaded))
        return code
    if args.action == "grants":
        if args.candidate or args.table or args.base or args.opens or args.refused or args.since:
            print("foreman permissions grants: usage: permissions grants [off [GROUP]] [--json]", file=sys.stderr)
            return 2
        return grants_command(project, args.file, args.group, args.json)
    if args.group:
        print("foreman permissions: GROUP goes with grants off", file=sys.stderr)
        return 2
    if args.action == "apply":
        if not args.file or args.candidate or args.refused or args.since:
            print(
                "foreman permissions apply: usage: permissions apply FILE [--table T] [--base HASH] [--opens]",
                file=sys.stderr,
            )
            return 2
        return apply(project, args)
    if args.file or args.base or args.opens:
        print("foreman permissions: FILE, --base and --opens go with apply", file=sys.stderr)
        return 2
    if args.since and not args.refused:
        print("foreman permissions: --since goes with --refused", file=sys.stderr)
        return 2
    if args.refused:
        if args.candidate:
            print("foreman permissions: --refused goes alone", file=sys.stderr)
            return 2
        try:
            r = refused_view(project, args.since or f"{REFUSED_DAYS}d")
        except ValueError:
            print(f"foreman permissions: --since {args.since}: use 7d, 24h, HH:MM or a date", file=sys.stderr)
            return 2
        print(json.dumps(r, ensure_ascii=False, indent=1) if args.json else render_refused(project, r))
        return 0
    if args.candidate:
        try:
            cand = config.load_project(project.home, project.mode, config_file=args.candidate, strict=True)
        except config.ConfigError as e:
            print(f"foreman permissions: {e}", file=sys.stderr)
            return 2
        d = diff(project, cand, args.table)
        print(json.dumps(d, ensure_ascii=False, indent=1) if args.json else render_diff(project, d))
        if d["table_error"]:
            return 2
        return 1 if d["table_failures"] or d["lifeline"]["failures"] else 0
    d = view(project, args.table)
    print(json.dumps(d, ensure_ascii=False, indent=1) if args.json else render(project, d))
    return 0
