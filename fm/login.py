"""How the agents authenticate: with the owner's normal claude.ai login and nothing else (so their sessions show in the
Claude app). `CLAUDE_CODE_OAUTH_TOKEN` is removed from the environment of every agent the loop launches, and
`login_status` reads the login without it."""

import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from . import proc, util

ENV = "CLAUDE_CODE_OAUTH_TOKEN"
SOON = timedelta(days=3)  # `fm health` warns this long before the login expires


@dataclass(frozen=True)
class Login:
    ok: bool
    expires: datetime | None = None

    def expiring(self) -> bool:
        return self.ok and self.expires is not None and self.expires - datetime.now(UTC) < SOON


def child_env() -> dict[str, str]:
    """The environment for a launched agent: ours without the token."""
    return {k: v for k, v in os.environ.items() if k != ENV}


def login_status(claude: str) -> Login:
    """Does the normal login work (`claude auth status`)? When it cannot be told (no such command, odd output) it
    counts as working."""
    try:
        r = proc.run([claude, "auth", "status", "--json"], env=child_env(), timeout=15)
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
