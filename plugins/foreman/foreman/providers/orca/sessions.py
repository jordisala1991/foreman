"""Agents as Orca chats (`[agents] runner = "orca"`), driven by Orca's orchestration verbs from the project's coordinator
tab (DESIGN «Chat view or terminal tab (FN-218)»): `worker-start` makes the chat, `send` messages it, `worker-stop`
stops it; the chat reads its settings and the guard from its folder (settings.py). The runner port's second adapter."""

import dataclasses
from pathlib import Path
from uuid import uuid4

from ... import util
from ...core.project.config import Project
from ..runner import Agent, RunnerError, SessionRef
from . import cli, settings
from .cli import OrcaError, orca
from .coordinator import coordinator, ensure_repos
from .rows import OrcaRows, states


def pane(dispatch: str) -> str | None:
    """The pane `worker-show` names for a dispatch; None when Orca does not know it."""
    try:
        d = util.json_object(orca(["orchestration", "worker-show", "--dispatch", dispatch, "--full"]).get("dispatch")) or {}
    except OrcaError:
        return None
    return str(d.get("assigneePaneKey") or "") or None


class Orca:
    def __init__(self, p: Project) -> None:
        self.p = p

    def start(self, agent: Agent, brief: str, cwd: Path) -> SessionRef:
        """A chat worker in `cwd` with the agent's settings carried there; a worker Orca does not start as a chat is
        stopped and refused (no fallback)."""
        cli.ensure_running()
        ensure_repos(self.p)
        handle, run = coordinator(self.p)
        first = not settings.session_file(self.p, cwd).exists()
        settings.session_file(self.p, cwd).unlink(missing_ok=True)
        settings.carry(self.p, agent, cwd)
        spec = self.p.state / "briefs" / f"{agent.name}-{uuid4().hex[:8]}.md"
        spec.parent.mkdir(parents=True, exist_ok=True)
        spec.write_text(brief, encoding="utf-8")
        w = orca(
            ["orchestration", "worker-start", "--run", run, "--from", handle, "--worktree", f"path:{cwd}", "--agent", "claude"]
            + ["--model", agent.model, "--effort", agent.effort, "--keep-open", "--task-title", agent.title, "--spec-file", str(spec)]
        )
        dispatch = str(w.get("dispatchId") or "")
        if w.get("mode") != "structured":
            self.stop(SessionRef("", None, dispatch=dispatch))
            raise RunnerError(f"Orca started a {w.get('mode')} worker, not a chat: set Claude to Chat in Orca")
        sid = settings.read_session(self.p, cwd)
        if not sid:
            self.stop(SessionRef("", None, dispatch=dispatch))
            raise RunnerError("the agent's SessionStart hook wrote no session id")
        if first:
            orca(["worktree", "set", "--worktree", f"path:{cwd}", "--display-name", agent.title])
        return SessionRef(sid[:8], sid, str(w.get("terminal") or "") or None, pane(dispatch), dispatch=dispatch, coordinator=handle)

    def send(self, ref: SessionRef, message: str, cwd: Path) -> SessionRef:
        """A message to the chat; one Orca ended (a restart) is resumed by it. Orca answers a send that had to start the
        worker again with a new dispatch id, which replaces the kept one."""
        cli.ensure_running()
        if not ref.dispatch or not ref.coordinator:
            raise RunnerError(f"session {ref.short} has no Orca dispatch to message")
        sent = orca(["orchestration", "send", "--from", ref.coordinator, "--to", f"dispatch:{ref.dispatch}", "--subject", "foreman", "--body", message])
        new = str(sent.get("dispatchId") or "")
        return dataclasses.replace(ref, dispatch=new, pane=pane(new)) if new and new != ref.dispatch else ref

    def relaunch(self, ref: SessionRef, agent: Agent, message: str, cwd: Path) -> SessionRef:
        """Today's settings reach a chat with no copy of it: claude reads its folder's file again while it runs (FN-218
        F3), so the file is carried again and the chat told; the session stays."""
        settings.carry(self.p, agent, cwd)
        return self.send(ref, message, cwd)

    def rows(self) -> OrcaRows | None:
        """None when Orca is not running or `ps` fails: the panel reads this, so it never opens Orca."""
        if not cli.running():
            return None
        try:
            return OrcaRows(states(orca(["worktree", "ps"])), pane)
        except RunnerError:
            return None

    def stop(self, ref: SessionRef) -> None:
        """`worker-stop`; a worker that already reported itself done is settled, and `worker-release` closes its chat."""
        if not ref.dispatch:
            raise RunnerError(f"session {ref.short} has no Orca dispatch to stop")
        if orca(["orchestration", "worker-stop", "--dispatch", ref.dispatch]).get("alreadySettled") is True:
            orca(["orchestration", "worker-release", "--dispatch", ref.dispatch])
