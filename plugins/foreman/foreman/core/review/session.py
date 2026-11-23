"""The session of a delivered change or bug task (`reports/ID/session.md`): what the loop asks of its text. Pure rules: no I/O here."""

import re

from . import evidence

FENCE = re.compile(r"^[ \t]*(```|~~~)[^\n]*\n(.*?)^[ \t]*\1", re.MULTILINE | re.DOTALL)


def gap(text: str | None) -> str:
    """What the session lacks: `missing` for no file (None), `empty` for a text with no command (a line in a fenced code
    block) and no screenshot (an image named as a bare file name), and nothing for a session that has one of them."""
    if text is None:
        return "missing"
    if any(m.group(2).strip() for m in FENCE.finditer(text)) or evidence.names(text):
        return ""
    return "empty"


def note(path: str, why: str) -> str:
    """What the agent is told when its delivery has no session: what is wrong, what to save, and to deliver again."""
    wrong = (
        f"{path} does not exist." if why == "missing" else f"{path} has no command in a fenced code block and no screenshot as `![what it shows](name.png)`."
    )
    return (
        f"Your delivery has no session: {wrong} Use the feature as its user would, save the session in {path} "
        "(each command in a fenced code block with its real output, each screenshot beside it as `![what it shows](name.png)`), "
        "and run `foreman done` again."
    )
