"""Remote Control on demand for a session agent (`foreman launch --session`): the agent runs without it, and only when
the owner asks to see its conversation (the panel's «see conversation» button) is it turned on, which gives the
claude.ai/code link of that session.

Measured on Claude Code 2.1.318 against live `claude --bg` sessions, idle and busy (the session is not stopped nor
resumed, so no turn is cut and no copy is made):

- `claude attach SESSION` in a pseudo-terminal, then `/remote-control` and Enter typed to it, turns Remote Control on
  and shows a dialog with `https://claude.ai/code/session_X` (X is the `bridgeSessionId` without `cse_`) and
  «Continue» selected. The link is not in `claude agents --json`, and the transcript has no `bridge-session` record
  this way, so it is read from that screen.
- The dialog stays open, with the session `waitingFor: "dialog open"`, until Enter is typed to it (a busy agent keeps
  working, but a closed attach leaves it open): so the same attach types a second Enter once it has the link.
- Asking again on a session that already has it on shows the same link and the same dialog.
- Each connection sends the owner a phone push: that is why it only happens on his click, never at launch.

The pseudo-terminal is opened with `pty.openpty` and `subprocess` (a threaded server must not fork); nothing here
needs a shell or a terminal of its own."""

from __future__ import annotations

import contextlib
import fcntl
import os
import pty
import re
import select
import signal
import struct
import subprocess
import termios
import threading
import time
from typing import TYPE_CHECKING, Any

from . import agents as agents_mod
from . import identity, sessionlaunch, util

if TYPE_CHECKING:
    from .config import Project

LINK = re.compile(r"https://claude\.ai/code/session_[A-Za-z0-9]+")
ANSI = re.compile(r"\x1b\[[0-9;?<>=]*[ -/]*[@-~]|\x1b[()][A-Z0-9]|\x1b[=>]")
DIALOG = "tocontinue"  # the end of the dialog's last line: «Enter to select · Esc to continue» (see clean)
COMMAND = "/remote-control"
START_S = 8.0  # how long the attached screen may take to draw
LINK_S = 25.0  # how long the connection may take to show its link
SETTLE_S = 1.0  # the screen finishes drawing, and the command is read, before the key that follows
KEY_S = 1.5  # how long an Enter takes to be acted on
_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


class RemoteError(Exception):
    """Remote Control could not be turned on (the message is for the owner)."""


def short(sid: str) -> str:
    """The id `claude attach` takes: the first 8 characters of the session id (it refuses the whole id)."""
    return sid[:8]


def _lock(sid: str) -> threading.Lock:
    with _locks_guard:
        return _locks.setdefault(sid, threading.Lock())


def clean(raw: bytes) -> str:
    """The text of a terminal's output: no escape sequences and no white space at all (the screen paints a word's
    letters and the gaps between words with cursor moves, so how much space survives varies; a busy session's
    spinner never lets the output go quiet either): search it with words written without spaces."""
    return "".join(ANSI.sub("", raw.decode(errors="replace")).split())


def link_in(text: str) -> str | None:
    """The last claude.ai/code link in `text` (from `clean`)."""
    found = LINK.findall(text)
    return found[-1] if found else None


class Screen:
    """A `claude attach` in a pseudo-terminal: what it printed, and keys to type to it."""

    def __init__(self, argv: list[str], cwd: str | None) -> None:
        self.master, slave = pty.openpty()
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 50, 160, 0, 0))
        try:
            self.proc = subprocess.Popen(
                argv, stdin=slave, stdout=slave, stderr=slave, cwd=cwd, start_new_session=True, close_fds=True
            )
        finally:
            os.close(slave)
        self.out = b""

    def read(self, until: Any, timeout: float) -> bool:
        """Reads until `until(clean text)` is true, False on timeout or when the process ended without it."""
        end = time.time() + timeout
        while time.time() < end:
            r, _, _ = select.select([self.master], [], [], 0.2)
            if r:
                try:
                    d = os.read(self.master, 65536)
                except OSError:
                    d = b""
                if not d:
                    return until(clean(self.out))
                self.out += d
                if until(clean(self.out)):
                    return True
        return until(clean(self.out))

    def type(self, keys: str) -> None:
        try:
            os.write(self.master, keys.encode())
        except OSError as e:  # the session's screen ended
            raise RemoteError("the session closed its screen: is it still running?") from e

    def close(self) -> None:
        with contextlib.suppress(OSError, ProcessLookupError):
            os.killpg(self.proc.pid, signal.SIGTERM)
        try:
            self.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            with contextlib.suppress(OSError, ProcessLookupError):
                os.killpg(self.proc.pid, signal.SIGKILL)
            self.proc.wait()
        with contextlib.suppress(OSError):
            os.close(self.master)


def dialog_open(sid: str) -> bool | None:
    """Whether Claude Code lists the session as waiting on an open dialog; None when it cannot be told."""
    rows = agents_mod.claude_rows()
    if rows is None:
        return None
    for r in rows:
        if r.get("sessionId") == sid:
            return r.get("waitingFor") == "dialog open"
    return None


def turn_on(claude: str, sid: str, cwd: str | None) -> str:
    """Turns Remote Control on for the live session `sid` and closes its dialog; the link. Raises RemoteError."""
    s = Screen([claude, "attach", short(sid)], cwd)
    try:
        if not s.read(lambda t: "permissionson" in t or "❯" in t or DIALOG in t, START_S):
            raise RemoteError("the session did not open: is it still running?")
        s.read(lambda t: False, SETTLE_S)  # the screen finishes drawing
        if DIALOG in clean(s.out)[-300:]:  # a dialog a past try left open: close it before typing the command
            s.type("\r")
            s.read(lambda t: False, KEY_S)
        s.out = b""
        s.type(COMMAND)
        s.read(lambda t: False, SETTLE_S)
        s.type("\r")
        if not s.read(lambda t: link_in(t) is not None and DIALOG in t, LINK_S):
            raise RemoteError("Remote Control did not give a link in time")
        link = link_in(clean(s.out))
        assert link is not None
        s.type("\r")  # «Continue»: the session is not left waiting on its dialog
        s.read(lambda t: False, KEY_S)
        return link
    finally:
        s.close()


def connect(project: Project, aid: str) -> str:
    """The link of the conversation of session agent `aid` (its agents.json key), with Remote Control turned on for
    it now; the link is recorded in its entry (`link`, `link_at`). One try at a time per session. Raises RemoteError."""
    f = agents_mod.agents_file(project)
    h = agents_mod.agents(project).get(aid)
    if not isinstance(h, dict) or not h.get(identity.SESSION_MARK):
        raise RemoteError(f"{aid} is not an agent that runs as a session of its own")
    if agents_mod.agent_state(h) == "stopped":
        raise RemoteError(f"{h.get('name') or aid} is stopped: there is no session to open")
    try:
        claude = sessionlaunch.find_claude()
    except sessionlaunch.SessionError as e:
        raise RemoteError(str(e)) from e
    lock = _lock(aid)
    if not lock.acquire(timeout=1):
        raise RemoteError("already connecting this session: wait a few seconds")
    try:
        link = turn_on(claude, aid, h.get("cwd") if isinstance(h.get("cwd"), str) else None)
        for _ in range(3):  # the dialog must be closed: otherwise the session waits for an Enter nobody gives
            if dialog_open(aid) is not True:
                break
            s = Screen([claude, "attach", short(aid)], None)
            try:
                s.read(lambda t: DIALOG in t, START_S)
                s.type("\r")
                s.read(lambda t: False, KEY_S)
            finally:
                s.close()
        else:
            if dialog_open(aid) is True:
                raise RemoteError("Remote Control is on but its dialog stays open: press Enter in `claude attach`")
        with util.locked_json(f, {}) as d:
            e = d.get(aid)
            if isinstance(e, dict):
                e["link"], e["link_at"] = link, util.now().strftime("%Y-%m-%dT%H:%M:%S")
                util.write_json(f, d)
        return link
    finally:
        lock.release()
