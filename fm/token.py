"""The Claude token the agents run with. It lives only in the macOS Keychain (item `claude-oauth-token`), never in a
dotfile, a plist or a log. `CLAUDE_CODE_OAUTH_TOKEN` in the environment wins; otherwise it is read from the Keychain each
time an agent is launched, and passed to that child's environment only."""

import getpass
import os
from collections.abc import Callable, Mapping

from . import proc

ENV = "CLAUDE_CODE_OAUTH_TOKEN"
ITEM = "claude-oauth-token"
RENEW = f"claude setup-token, then: security add-generic-password -U -a $USER -s {ITEM} -w"
SETUP = (
    f"The agents' token lives only in the macOS Keychain. Create it with `claude setup-token`, then store it with\n"
    f"  security add-generic-password -U -a $USER -s {ITEM} -w\n"
    f"(the same two commands renew it). Do not put it in a dotfile or a plist."
)


def keychain() -> str:
    """The token from the Keychain; "" when there is none or `security` is missing."""
    try:
        r = proc.run(["security", "find-generic-password", "-a", os.environ.get("USER") or getpass.getuser(), "-s", ITEM, "-w"], timeout=15)
    except proc.ProcError:
        return ""
    return r.out.strip() if r.code == 0 else ""


def get(read: Callable[[], str] | None = None) -> str:
    return os.environ.get(ENV) or (read or keychain)()


def child_env(read: Callable[[], str] | None = None) -> dict[str, str]:
    """The environment for a launched agent: ours, plus the token when there is one."""
    tok = get(read)
    return {**os.environ, ENV: tok} if tok else dict(os.environ)


def works(claude: str, tok: str, env: Mapping[str, str] | None = None) -> bool:
    """A cheap real call with the token: does Claude accept it?"""
    try:
        r = proc.run([claude, "-p", "ok", "--model", "haiku"], env={**(env or os.environ), ENV: tok}, timeout=60)
    except proc.ProcError:
        return True  # cannot tell (claude missing or slow): not a token problem
    return r.code == 0
