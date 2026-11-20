"""Orca's hooks in Claude's settings: the chats' state reaches Orca through them (D8: without them no state ever appears)."""

import json
from pathlib import Path

from ... import util

STATE_EVENTS = ("UserPromptSubmit", "PreToolUse", "PermissionRequest", "Stop")  # the events that carry an agent's state
MARKER = "claude-hook.sh"


def missing(settings: Path) -> list[str]:
    """The state events with no Orca hook in `settings`; all of them when the file cannot be read."""
    try:
        top = util.json_object(json.loads(settings.read_text(encoding="utf-8"))) or {}
    except (OSError, ValueError):
        top = {}
    hooks = util.json_object(top.get("hooks")) or {}
    return [ev for ev in STATE_EVENTS if MARKER not in json.dumps(hooks.get(ev))]
