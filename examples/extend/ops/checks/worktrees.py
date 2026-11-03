#!/usr/bin/env python3
"""A [loop] checks command (docs/extending.md, docs/config.md «[loop] checks»): how many worktrees of this product
are left in its worktrees folder. At WORKTREES_INFO or more, a notice that only informs; at WORKTREES_ACT or more,
one that asks for action (old branches to integrate or remove).

It asks foreman for the folder and the repos (`"$FOREMAN_BIN" config --json`: the configuration as foreman resolved
it, defaults included) instead of parsing foreman.toml: the folder is right even when foreman.toml leaves it out, and
stays right when foreman's default changes. `foreman check --run-checks` runs it once and validates its lines.

Exit 0 or 1 is normal; any other code (or taking longer than the point's 30 s) is a check_failed notice.
"""

import json
import os
import subprocess
import sys

if int(os.environ.get("FOREMAN_API") or 0) < 1 or not os.environ.get("FOREMAN_BIN"):
    print("worktrees.py: run by foreman (FOREMAN_API >= 1)", file=sys.stderr)
    sys.exit(2)
info = int(os.environ.get("WORKTREES_INFO") or 4)
act = int(os.environ.get("WORKTREES_ACT") or 10)

# FOREMAN_HOME is set for every command, so this `foreman config` reads the same project
out = subprocess.run(
    [os.environ["FOREMAN_BIN"], "config", "--json"], capture_output=True, text=True, timeout=20, check=True
).stdout
d = json.loads(out)
folder = d["config"]["paths"]["worktrees"]  # absolute, after foreman's default
# each repo's worktree template cut before its first placeholder after {repo}: "{worktrees}/{repo}-{name}" -> ".../web-"
cuts = [
    r["worktree"].replace("{worktrees}", folder).replace("{repo}", key).split("{")[0]
    for key, r in d["config"]["repos"].items()
]
found = set()
for cut in cuts:
    parent = os.path.dirname(cut)
    if os.path.isdir(parent):
        found |= {e.path for e in os.scandir(parent) if e.is_dir() and e.path.startswith(cut)}
n = len(found)
if n < info:
    sys.exit(0)
# the key names the case, so the notice is not new on every pass while only the count changes
notice = {
    "kind": "worktrees",
    "actionable": n >= act,
    "key": f"worktrees:{n >= act}",
    "text": f"{n} worktrees of {d['name']} in {folder}",
}
print(json.dumps(notice))
