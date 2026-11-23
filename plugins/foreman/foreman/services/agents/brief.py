import string
from pathlib import Path
from typing import Any

from ...core.project.config import Project
from ...core.tasks import tasktypes
from ...providers import registry
from .. import lock

BRIEFS = Path(__file__).parents[2] / "briefs"
UNASKED = (  # each brief follows it with its own role line
    "# Cases nobody asked for\nBefore adding handling for a race, an edge case, a bad input or a security case that no acceptance "
    "criterion asks for, or sending one to be fixed, state its concrete failure path: the input or event that causes it, how likely "
    "real use is to reach it, and what it costs to handle. A path reproduced with ordinary input through the project's own commands "
    "(a typo, a 0, an empty value) is real. Handle a real case where the bad value enters, not with a guard where it crashes. "
    "Add no compatibility shims, dual paths, fallbacks or legacy adapters unless a criterion asks for one.\n"
)


def agent_name(p: Project, tid: str, kind: str) -> str:
    return f"{tid.lower()}-planning" if kind == "planning" else tid.lower()


def report_file(p: Project, tid: str) -> Path:
    return p.home / "reports" / f"{tid}.md"


def seed_report(p: Project, tid: str, worktree: Path) -> None:
    """Gives a responsible agent its report as a template to fill: the repo's PR template (plus a Review rounds section) or
    fm's own. An existing report is never overwritten (a relaunch keeps its agent's work)."""
    f = report_file(p, tid)
    if f.exists():
        return
    theirs = worktree / ".github" / "pull_request_template.md"
    text = (BRIEFS / "report.md").read_text(encoding="utf-8").replace("ID", tid)
    if theirs.is_file():
        text = theirs.read_text(encoding="utf-8").rstrip() + "\n\n## Review rounds\n"
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(text, encoding="utf-8")


def lens_file(p: Project, lens: str) -> Path:
    return p.home / "reports" / f"lens-{lens}.md"


def plan_file(p: Project, tid: str) -> Path:
    return p.home / "plans" / f"{tid}.md"


def plan_section(i: dict[str, Any]) -> str:
    if i.get("plan_file"):
        return (
            f"# The plan\nThe task has a plan: `{i['plan_file']}`. Read it whole and follow it. If it does not hold "
            "on a detail (a name, a line, the order of steps), decide, and list it in your report under «Off the plan» with why; "
            "if it would change the goal, the acceptance criteria, a decision or the out of scope, `foreman ask` and end your turn."
        )
    return "# The plan\nNo plan exists for this task (it was judged trivial): work from the goal and acceptance criteria."


def owner_notes_section(p: Project, tid: str) -> str:
    """The owner's corrections on this task (rejection notes), oldest first; empty when there are none."""
    notes = [q for q in registry.backlog(p).load()["decisions_resolved"] if q.get("task") == tid and q.get("kind") == "rejection"]
    if not notes:
        return ""
    lines = "\n".join(f"- {q.get('at') or q.get('resolved', '')}: {q.get('answer', '')}" for q in notes)
    return (
        f"\n# The owner's notes on this task\n{lines}\n"
        "A note overrides the plan and the acceptance criteria where they differ; the latest note wins. A departure backed by a note "
        "is not a finding, and the report's «Off the plan» should cite it.\n"
    )


def vocabulary_section(p: Project) -> str:
    """The Vocabulary paragraph, only for a repo that has a GLOSSARY.md at its root."""
    if not (p.repo / "GLOSSARY.md").is_file():
        return ""
    return "# Vocabulary\nUse the terms of `GLOSSARY.md` at the repo root and never rename one: it changes only by the owner's decision.\n\n"


def verify_file(p: Project) -> Path:
    return p.home / "verify.md"


def session_file(p: Project, tid: str) -> Path:
    return p.home / "reports" / tid / "session.md"


def verify_section(p: Project, tid: str) -> str:
    """The Verify recipe paragraph, only for a home that has a verify.md."""
    if not verify_file(p).is_file():
        return ""
    return (
        f"# Verify recipe\nHow to use this product live in a throwaway setup: `{verify_file(p)}`. The responsible agent saves "
        f"its use of it in `{session_file(p, tid)}`, with its screenshots beside it: the agent's claim, never evidence by itself.\n\n"
    )


def brief(p: Project, i: dict[str, Any], kind: str, extra: dict[str, Any] | None = None) -> str:
    a: dict[str, Any] = i.get("acceptance") or {}
    values = {
        "id": i["id"],
        "project": p.name,
        "title": i["title"],
        "detail": i.get("detail", ""),
        "goal": a.get("goal") or i["title"],
        "criteria": a.get("criteria", ""),
        "repo": str(p.repo),
        "worktree": i.get("worktree", ""),
        "branch": i.get("branch", ""),
        "main": p.get("project", "main"),
        "test": p.get("merge", "test") or "(none configured)",
        "report": str(report_file(p, i["id"])),
        "plan": str(plan_file(p, i["id"])),
        "plan_section": plan_section(i),
        "type_section": tasktypes.section(i),
        "vocabulary": vocabulary_section(p),
        "verify": verify_section(p, i["id"]),
        "session": str(session_file(p, i["id"])),
        "unasked": UNASKED,
        "model_effort": f"{i.get('model') or 'project default'} / {i.get('effort') or 'project default'}",
        "round2": "",
        "owner_notes": owner_notes_section(p, i["id"]) if kind in ("reviewer", "hunter") else "",
        "resources": lock.describe(p.cfg),
        **(extra or {}),
    }
    if values.get("round2"):
        values["round2"] = string.Template(values["round2"]).safe_substitute(values)
    text = string.Template((BRIEFS / f"{kind}.md").read_text(encoding="utf-8")).safe_substitute(values)
    return f"[fm {kind} {i['id']}]\n{text}"
