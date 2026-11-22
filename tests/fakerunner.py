"""A fake Runner port (foreman/providers/runner.py) for tests that check what fm asks of its agents' runner, not claude: it
records each start, send and stop, and hands out one session per start with a terminal and a pane, which claude never has.
A test hands it to the code with the `fake_runner` fixture (conftest.py)."""

from pathlib import Path

from foreman.providers.runner import Agent, RunnerError, SessionRef, State


class FakeRows:
    """A snapshot: `states` by session id (GONE for the others); a session is busy when WORKING or WAITING, alive unless ENDED or GONE."""

    def __init__(self, states: dict[str, State] | None = None) -> None:
        self.states = states or {}

    def state(self, ref: SessionRef | None) -> State:
        return self.states.get((ref.session if ref else None) or "", State.GONE)

    def busy(self, ref: SessionRef | None) -> bool:
        return self.state(ref) in (State.WORKING, State.WAITING)

    def alive(self, ref: SessionRef | None) -> bool:
        return self.state(ref) in (State.WORKING, State.WAITING, State.IDLE)

    def find(self, short: str) -> str | None:
        return None

    def short(self, session: str) -> str | None:
        return session.split("-")[0]

    def has_sessions(self) -> bool:
        return True


class FakeRunner:
    def __init__(self) -> None:
        self.started: list[tuple[Agent, str, Path]] = []
        self.sent: list[tuple[SessionRef, str, Path]] = []
        self.stopped: list[SessionRef] = []
        self.removed: list[Path] = []
        self.states: dict[str, State] = {}  # by session id: what `rows()` shows
        self.fail_send = False
        self.fail_remove: set[Path] = set()  # folders `remove_folder` cannot remove

    def start(self, agent: Agent, brief: str, cwd: Path) -> SessionRef:
        self.started.append((agent, brief, cwd))
        n = len(self.started)
        return SessionRef(f"fk{n}", f"fk{n}-sid", f"T{n}", f"P{n}")

    def send(self, ref: SessionRef, message: str, cwd: Path) -> SessionRef:
        if self.fail_send:
            raise RunnerError("boom")
        self.sent.append((ref, message, cwd))
        return ref

    def relaunch(self, ref: SessionRef, agent: Agent, message: str, cwd: Path) -> SessionRef:
        return self.start(agent, message, cwd)

    def handle(self, ref: SessionRef) -> str:
        return ref.short

    def rows(self) -> FakeRows:
        return FakeRows(self.states)

    def stop(self, ref: SessionRef) -> None:
        self.stopped.append(ref)

    def remove_folder(self, folder: Path) -> None:
        if folder in self.fail_remove:
            raise RunnerError(f"cannot remove {folder.name}")
        self.removed.append(folder)
