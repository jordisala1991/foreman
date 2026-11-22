"""Claude Code's listing (`claude agents --json --all`), as the runner port's `Rows`: one snapshot per tick, by full session id."""

from typing import Any

from ..runner import SessionRef, State

STATES = {"working": State.WORKING, "blocked": State.WAITING, "done": State.IDLE, "stopped": State.ENDED}


class ClaudeRows:
    def __init__(self, rows: dict[str, dict[str, Any]]) -> None:
        self.rows = rows

    def _row(self, ref: SessionRef | None) -> dict[str, Any] | None:
        return self.rows.get((ref.session if ref else None) or "")

    def raw(self, ref: SessionRef | None) -> str:
        """Claude's own word for the state: working | blocked | done (ended its turn) | stopped | gone."""
        row = self._row(ref)
        return str(row.get("state") or "gone") if row else "gone"

    def state(self, ref: SessionRef | None) -> State:
        """Any live state claude reports that is not listed counts as WAITING (not idle: `busy` treats it so)."""
        raw = self.raw(ref)
        return State.GONE if raw == "gone" else STATES.get(raw, State.WAITING)

    def alive(self, ref: SessionRef | None) -> bool:
        """Its process still runs. claude keeps the state of a stopped session (`done`) and drops its `pid`."""
        row = self._row(ref)
        return bool(row and row.get("pid") and row.get("state") != "stopped")

    def busy(self, ref: SessionRef | None) -> bool:
        """Not idle: working, or any other live state Claude Code reports. Idle whatever the state says: status `idle` (an
        agent that asked the owner and ended its turn shows `blocked`/`idle`, and a hunter `working`/`idle`), or a
        `blocked` row with neither status nor process (a session whose process went away). Resuming a session that is not
        idle starts a copy of it, so messages wait until it is idle."""
        row = self._row(ref)
        if row is None or self.state(ref) in (State.IDLE, State.ENDED, State.GONE):
            return False
        return row.get("status") != "idle" and not (row.get("state") == "blocked" and not row.get("status") and not row.get("pid"))

    def find(self, short: str) -> str | None:
        """The full session id of a short one, in a listing."""
        return next((sid for sid, row in self.rows.items() if row.get("id") == short or sid.startswith(short)), None)

    def short(self, session: str) -> str | None:
        row = self.rows.get(session)
        return row.get("id") if row else None

    def has_sessions(self) -> bool:
        return bool(self.rows)

    def epoch(self) -> str:
        """No epoch: a claude process that dies counts as a session's end."""
        return ""
