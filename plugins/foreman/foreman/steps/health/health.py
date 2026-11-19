from ... import util
from ...core.project.config import Project
from ...core.tasks import backlog
from ...providers.runner import Rows
from . import repos
from .checks import Finding, agents, answers_undelivered, finding, login_check, loop_dead, loop_errors, orphan_worktrees
from .main_tests import main_tests


def findings(p: Project, rows: Rows, outside: bool = False) -> list[Finding]:
    out = [*loop_dead(p), *login_check(p)] if outside else []
    checks = (
        lambda: agents(p, rows),
        lambda: answers_undelivered(p),
        lambda: orphan_worktrees(p),
        lambda: repos.not_checkouts(p),
        lambda: repos.stray_files(p),
        lambda: main_tests(p, run=not outside),
        lambda: loop_errors(p),
    )
    for step in checks:
        try:
            out += step()
        except Exception as e:
            meaning = "A health check raised an exception, so its findings are missing."
            out.append(finding(f"health:{e!r}"[:80], "A health check fails", meaning, repr(e)))
    return out


def run(p: Project, rows: Rows, outside: bool = False) -> list[str]:
    """Files the new findings; the ids filed."""
    found = {f["key"]: f for f in findings(p, rows, outside)}
    filed: list[str] = []
    with util.locked_json(p.state / "seen.json", {}) as seen:
        for key, f in found.items():
            if key not in seen:
                tid = backlog.add(p, f["title"], detail=f["detail"], status="triage", source=f"health:{key}", repo=f.get("repo", ""))
                if tid:
                    filed.append(tid)
            seen[key] = util.now()
        for key in [k for k in seen if k not in found]:
            del seen[key]
    return filed
