"""A fake Runner port (foreman/providers/runner.py) for tests that check what fm asks of its agents' runner, not claude: it
records each start, send and stop, and hands out one session per start with a terminal and a pane, which claude never has.
A test hands it to the code with the `fake_runner` fixture (conftest.py)."""

from pathlib import Path

from foreman.providers.runner import Agent, RunnerError, SessionRef, State


class FakeRows:
    """A snapshot: `states` by session id (GONE for the others); nothing is busy or alive."""

    def __init__(self, states: dict[str, State] | None = None) -> None:
        self.states = states or {}

    def state(self, ref: SessionRef | None) -> State:
        return self.states.get((ref.session if ref else None) or "", State.GONE)

    def busy(self, ref: SessionRef | None) -> bool:
        return False

    def alive(self, ref: SessionRef | None) -> bool:
        return False

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
        self.fail_send = False

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

    def rows(self) -> FakeRows:
        return FakeRows()

    def stop(self, ref: SessionRef) -> None:
        self.stopped.append(ref)
