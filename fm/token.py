"""The Claude token the agents run with. It lives only in the macOS Keychain (item `claude-oauth-token`), never in a
dotfile, a plist or a log. `CLAUDE_CODE_OAUTH_TOKEN` in the environment wins; otherwise it is read from the Keychain each
time an agent is launched, and passed to that child's environment only."""

import getpass
import os
import re
from collections.abc import Callable, Mapping

from . import proc

ENV = "CLAUDE_CODE_OAUTH_TOKEN"
ITEM = "claude-oauth-token"
RENEW_CMD = f"`claude setup-token`, then `security add-generic-password -U -a $USER -s {ITEM} -w`"
AUTH_ERROR = re.compile(
    r"failed to authenticate|authentication_error|invalid bearer token|invalid api key|oauth token has (expired|been revoked)|please run /login", re.I
)
NOT_FOUND = 44  # `security`'s exit code for "item not found"
SETUP = (
    f"The agents' token lives only in the macOS Keychain. Create it with `claude setup-token`, then store it with\n"
    f"  security add-generic-password -U -a $USER -s {ITEM} -w\n"
    f"(the same two commands renew it). Do not put it in a dotfile or a plist."
)


class Unreadable(Exception):
    """The Keychain could not be read right now (locked, slow, `security` missing): not the same as having no token."""


def keychain() -> str:
    """The token from the Keychain; "" when there is no such item; Unreadable when the read itself failed."""
    try:
        r = proc.run(["security", "find-generic-password", "-a", os.environ.get("USER") or getpass.getuser(), "-s", ITEM, "-w"], timeout=5)
    except proc.ProcError as e:
        raise Unreadable(str(e)) from e
    if r.code == NOT_FOUND:
        return ""
    if r.code != 0:
        raise Unreadable(f"security exited {r.code}")
    return r.out.strip()


def get(read: Callable[[], str] | None = None) -> str:
    return os.environ.get(ENV) or (read or keychain)()


def child_env(read: Callable[[], str] | None = None) -> dict[str, str]:
    """The environment for a launched agent: ours, plus the token when there is one."""
    try:
        tok = get(read)
    except Unreadable:
        tok = ""  # launch anyway: the agent then fails with Claude's own message, and `fm health` says why
    return {**os.environ, ENV: tok} if tok else dict(os.environ)


def works(claude: str, tok: str, env: Mapping[str, str] | None = None) -> bool:
    """A cheap real call with the token. False only when Claude's own words say the token is refused; a rate limit, no
    network or a slow call is not a token problem."""
    try:
        r = proc.run([claude, "-p", "ok", "--model", "haiku"], env={**(env or os.environ), ENV: tok}, timeout=60)
    except proc.ProcError:
        return True
    return r.code == 0 or not AUTH_ERROR.search(r.out + r.err)
