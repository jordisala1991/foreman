"""Orca's chats (`orca worktree ps`), as the runner port's `Rows`: one snapshot per tick. A chat is found from its
dispatch: `worker-show` names its pane (`assigneePaneKey`), and the `ps` row with that pane holds its state. The two keys
share the chat's own part, before the `:` (the part after it differs between them, seen on Orca 1.4.261)."""

from collections.abc import Callable
from typing import Any

from ... import util
from ..runner import SessionRef, State

STATES = {"working": State.WORKING, "done": State.IDLE}  # any other word (blocked, waiting…) is WAITING


CHAT_PREFIX = "structured-agent-session-"


def chat(pane: str) -> str:
    """The chat's part of a pane key: `structured-agent-session-<id>`."""
    return pane.split(":", 1)[0]


def session_id(pane: str) -> str:
    """Orca's session id: the `<id>` of the chat's part of a pane key."""
    return chat(pane).removeprefix(CHAT_PREFIX)


def states(ps: dict[str, Any]) -> dict[str, str]:
    """Each chat's state word in a `worktree ps` answer, by `chat(paneKey)`."""
    found: dict[str, str] = {}
    for w in util.json_list(ps.get("worktrees")):
        for a in util.json_list((util.json_object(w) or {}).get("agents")):
            row = util.json_object(a) or {}
            if row.get("paneKey"):
                found[chat(str(row["paneKey"]))] = str(row.get("state") or "")
    return found


class OrcaRows:
    def __init__(self, rows: dict[str, str], pane_of: Callable[[str], str | None]) -> None:
        self.rows = rows
        self.pane_of = pane_of  # dispatch id → its pane key, None when Orca no longer knows it
        self.panes: dict[str, str | None] = {}

    def state(self, ref: SessionRef | None) -> State:
        """GONE without a dispatch; ENDED when Orca lists no chat of it (stopped, or Orca restarted: its next message
        resumes it)."""
        if ref is None or not ref.dispatch:
            return State.GONE
        if ref.dispatch not in self.panes:  # the pane kept from worker-show at start; asked again only when none was
            self.panes[ref.dispatch] = ref.pane or self.pane_of(ref.dispatch)
        pane = self.panes[ref.dispatch]
        word = self.rows.get(chat(pane)) if pane else None
        return State.ENDED if word is None else STATES.get(word, State.WAITING)

    def busy(self, ref: SessionRef | None) -> bool:
        return self.state(ref) in (State.WORKING, State.WAITING)

    def alive(self, ref: SessionRef | None) -> bool:
        return self.state(ref) in (State.WORKING, State.WAITING, State.IDLE)

    def find(self, short: str) -> str | None:
        return None  # the session id is known at launch

    def short(self, session: str) -> str | None:
        return session[:8]

    def has_sessions(self) -> bool:
        return True
