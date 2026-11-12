"""How the agents authenticate. By default with the normal claude.ai login (their sessions then show in the Claude app).
The long-lived token from `claude setup-token` can only make model requests: agents run with it do not show in the app,
so it is a fallback, used only while the login does not work. It lives only in the macOS Keychain (item
`claude-oauth-token`), never in a dotfile, a plist or a log; `CLAUDE_CODE_OAUTH_TOKEN` in the environment also counts as
the token. It goes into a launched agent's environment only when the login does not work, and it is removed from it when
the login works."""

import getpass
import json
import os
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from . import proc, util

ENV = "CLAUDE_CODE_OAUTH_TOKEN"
ITEM = "claude-oauth-token"
RENEW_CMD = f"`claude setup-token`, then `security add-generic-password -U -a $USER -s {ITEM} -w`"
AUTH_ERROR = re.compile(
    r"failed to authenticate|authentication_error|invalid bearer token|invalid api key|oauth token has (expired|been revoked)|please run /login", re.I
)
NOT_FOUND = 44  # `security`'s exit code for "item not found"
SETUP = (
    f"Agents use your normal Claude login (so they show in the Claude app). As a fallback for when it expires, keep a\n"
    f"long-lived token in the macOS Keychain: create it with `claude setup-token`, then store it with\n"
    f"  security add-generic-password -U -a $USER -s {ITEM} -w\n"
    f"(the same two commands renew it). Do not put it in a dotfile or a plist."
)


SOON = timedelta(days=3)  # `fm health` warns this long before the login expires


@dataclass(frozen=True)
class Login:
    ok: bool
    expires: datetime | None = None

    def expiring(self) -> bool:
        return self.ok and self.expires is not None and self.expires - datetime.now(UTC) < SOON


def login_status(claude: str) -> Login:
    """Does the normal login work (`claude auth status`, read without the token in the environment)? When it cannot be
    told (no such command, odd output) it counts as working: the default stays the login."""
    env = {k: v for k, v in os.environ.items() if k != ENV}
    try:
        r = proc.run([claude, "auth", "status", "--json"], env=env, timeout=15)
        data = util.json_object(json.loads(r.out))
    except (proc.ProcError, ValueError):
        return Login(True)
    if data is None or "loggedIn" not in data:
        return Login(True)
    try:
        expires = datetime.fromisoformat(str(data["loginExpiresAt"]).replace("Z", "+00:00"))
    except (KeyError, ValueError):
        expires = None
    gone = expires is not None and expires <= datetime.now(UTC)
    return Login(bool(data["loggedIn"]) and not gone, expires)


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


def child_env(claude: str, read: Callable[[], str] | None = None, login: Callable[[str], Login] = login_status) -> dict[str, str]:
    """The environment for a launched agent: ours without the token while the login works; with the token (the
    fallback) when it does not."""
    env = {k: v for k, v in os.environ.items() if k != ENV}
    if login(claude).ok:
        return env
    try:
        tok = get(read)
    except Unreadable:
        tok = ""  # launch anyway: the agent then fails with Claude's own message, and `fm health` says why
    return {**env, ENV: tok} if tok else env


def works(claude: str, tok: str, env: Mapping[str, str] | None = None) -> bool:
    """A cheap real call with the token. False only when Claude's own words say the token is refused; a rate limit, no
    network or a slow call is not a token problem."""
    try:
        r = proc.run([claude, "-p", "ok", "--model", "haiku"], env={**(env or os.environ), ENV: tok}, timeout=60)
    except proc.ProcError:
        return True
    return r.code == 0 or not AUTH_ERROR.search(r.out + r.err)
