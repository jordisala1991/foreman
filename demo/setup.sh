#!/bin/sh
# Builds the demo from zero in demo/run/: a toy repo (textkit), a project home with three tasks (two ready, one to
# define) and a registry of its own. Then: `demo/run.sh` starts the loop and the panel.
set -eu
here=$(cd "$(dirname "$0")" && pwd)
root=$(dirname "$here")
run="$here/run"
# The toy repo is an orphan branch (next/demo-textkit) of the git repo around this folder, in a worktree: Claude Code
# trusts it because it trusts that repo, so the background agents start without the interactive trust prompt.
# (A repo of its own would need `claude` run once in it to accept the prompt: see SUMMARY.md.)
for wt in $(git -C "$root" worktree list --porcelain | sed -n "s|^worktree \($run/.*\)|\1|p"); do
  git -C "$root" worktree remove --force "$wt"
done
for b in $(git -C "$root" for-each-ref --format='%(refname:short)' 'refs/heads/next/demo-*'); do
  git -C "$root" branch -D -q "$b"
done
rm -rf "$run"
mkdir -p "$run"
git -C "$root" worktree add -q --orphan -b next/demo-textkit "$run/repo"
cd "$run/repo"
cat > textkit.py <<'PY'
"""textkit: small text helpers."""


def word_count(text):
    """The number of words in text."""
    return len(text.split(" "))


def shout(text):
    """TEXT IN CAPITALS, with an exclamation mark."""
    return text.upper() + "!"
PY
cat > cli.py <<'PY'
import sys

import textkit

if __name__ == "__main__":
    print(getattr(textkit, sys.argv[1])(" ".join(sys.argv[2:])))
PY
cat > test_textkit.py <<'PY'
import unittest

import textkit


class TextkitTest(unittest.TestCase):
    def test_word_count(self):
        self.assertEqual(textkit.word_count("one two three"), 3)

    def test_shout(self):
        self.assertEqual(textkit.shout("hi"), "HI!")


if __name__ == "__main__":
    unittest.main()
PY
git add -A
git commit -qm "textkit: word_count, shout and a tiny CLI"

export FM_REGISTRY="$run/registry.json"
fm="$root/bin/fm"
"$fm" --home "$run/home" init --repo "$run/repo" --prefix TK >/dev/null
cat > "$run/home/fm.toml" <<TOML
[project]
name = "textkit"
prefix = "TK"
repo = "$run/repo"
main = "next/demo-textkit"
branch_prefix = "next/demo-"

[agents]
slots = 2
model = "sonnet"
planning_model = "sonnet"
# the demo agents run without today's foreman plugin: this prototype replaces it
settings = { enabledPlugins = { "foreman@foreman" = false } }

[merge]
autonomy = "manual"
test = "python3 -m unittest -q"

[health]
loop_seconds = 20
stuck_minutes = 30
TOML
"$fm" --home "$run/home" add --ready "word_count counts wrong on empty text and repeated spaces" \
  --goal "word_count gives the number of words for any text" \
  --criteria "word_count('') == 0 and word_count('  a   b ') == 2, with tests for both; all tests pass" \
  --detail "textkit.word_count splits on a single space."
"$fm" --home "$run/home" add --ready "Add slugify(text)" \
  --goal "textkit has slugify: lowercase words joined by hyphens, punctuation dropped" \
  --criteria "slugify('Hello, World!') == 'hello-world' and slugify('  Many   spaces ') == 'many-spaces', tested; all tests pass"
"$fm" --home "$run/home" add "The CLI should be friendlier" \
  --detail "Running cli.py with no arguments or with an unknown function crashes with a traceback."
echo "demo ready in $run"
