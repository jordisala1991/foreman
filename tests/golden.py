"""The golden output corpus: what foreman shows and writes, compared byte for byte with tests/golden/.

Every test of test_golden.py builds a small world in its own temporary directory (two fake products: `faro`, in
Spanish with its own decisions page, and `harbor`, in English with its decisions in a section of a page it writes by
hand), runs a fixed sequence of foreman commands, hook events and panel requests against it, and writes down each
call with its exit code, its stdout and its stderr, and at the end the files under the state root and the products'
homes. That transcript, after `normalise`, must equal its golden file under tests/golden/. The corpus runs foreman
the way a user and Claude Code do (lib/main.py in a subprocess, events on stdin, HTTP for the panel), so it does not
care how the code inside is organised: a refactor that changes nothing visible keeps it green.

- A mismatch fails the test with a short unified diff (golden → now).
- `bin/foreman selftest --update-golden` runs only these tests and writes the transcripts as they are now; it says
  which files changed. A change that means to change an output updates its golden files in the same commit
  (CONTRIBUTING.md, «Golden files»).

Only what has to vary from one run to the next is normalised, in one place (`Normaliser`); everything else is
compared as it is.
"""

import datetime as dt
import difflib
import json
import os
import re
import sys
import time
from pathlib import Path

from helpers import ENTRY, ROOT  # noqa: F401 (ENTRY: test_golden starts the panel with it)

import forkentry

GOLDEN = ROOT / "tests/golden"
UPDATE_ENV = "FOREMAN_UPDATE_GOLDEN"
DIFF_LINES = 40  # the diff a mismatch shows, at most


# The PATH every golden run uses, so the output does not depend on the host session.
GOLDEN_PATH = "/usr/local/bin:/usr/bin:/bin"


def updating():
    return os.environ.get(UPDATE_ENV) == "1"


class Normaliser:
    """Turns one run's output into the run-independent text the golden files hold. It replaces, in this order:

    1. Literal values of this run, longest first: the test's temporary directory (`<TMP>`, also as /private/… on
       macOS), foreman's checkout (`<FOREMAN>`: worktrees differ), the Python running the tests (`<PYTHON>`), its
       version (`<PYVER>`: the corpus runs on 3.9 and on the newest), foreman's own version (`<VERSION>`, from
       .claude-plugin/plugin.json: cutting a version changes no golden file), the PATH (`<PATH>`: the panel's
       service file copies it), the home directory (`<HOME>`) and
       whatever a test adds (`add`): the panel's port
       (`<PORT>`), the launch tokens foreman draws at random (`<TOKEN1>`, `<TOKEN2>`… in the order they appear).
    2. Dates near today (±3 days): `<TODAY>`, `<TODAY-1>`, `<TODAY+2>`… (a fixed date of the fixtures, years away,
       stays as it is). A date crossing midnight in the middle of a run is the one known flake.
    3. Clock times, HH:MM or HH:MM:SS (with fractions and a UTC offset if any): `<TIME>`.
    4. Epoch times within a year of now, in seconds or milliseconds (10 or 13 digits, optional fraction): `<EPOCH>`;
       and the durations foreman measures: «N.N s» (a loop pass, a wait) and the local CI's run times in whole
       seconds («in N s.», «(N s)», «, N s)»): `<SECONDS> s`; a loop pass's `"duration_s": N` in loop.jsonl:
       `"duration_s": <SECONDS>`.
    5. Process ids: «pid N», «pid=N», "pid": N and `"pid", N` → `<PID>`.
    6. The machine's figures the panel reports (`mac`: load, memory, disk): the whole value (Transcript.json).
    7. What argparse lays out differently on each Python (help and usage texts are argparse's, their words are
       foreman's): «optional arguments:» (3.9) is written «options:» (3.10+), and a usage line whose «...» 3.9 wraps
       onto a line of its own is joined.

    Nothing else is masked: a change in any other byte is a change in what foreman shows."""

    def __init__(self, tmp):
        self.literals = []
        tmp = Path(tmp)
        for path in {str(tmp), str(tmp.resolve()), os.path.realpath(str(tmp))}:
            self.add(path, "<TMP>")
            if path.startswith("/private/"):
                self.add(path[len("/private") :], "<TMP>")
        self.add(str(ROOT), "<FOREMAN>")
        self.add(os.path.realpath(str(ROOT)), "<FOREMAN>")
        self.add(sys.executable, "<PYTHON>")
        self.add(os.path.realpath(sys.executable), "<PYTHON>")
        self.add(".".join(map(str, sys.version_info[:3])), "<PYVER>")
        self.add(json.loads((ROOT / ".claude-plugin/plugin.json").read_text())["version"], "<VERSION>")
        self.add(GOLDEN_PATH, "<PATH>")
        self.add(str(Path.home()), "<HOME>")
        self.tokens = {}
        today = dt.date.today()
        self.dates = {
            (today + dt.timedelta(days=d)).isoformat(): "<TODAY" + (f"{d:+d}" if d else "") + ">" for d in range(-3, 4)
        }
        now = time.time()
        self.epoch = (now - 366 * 86400, now + 366 * 86400)

    def add(self, value, placeholder):
        if value and value not in [v for v, _ in self.literals]:
            self.literals.append((value, placeholder))
            self.literals.sort(key=lambda x: -len(x[0]))

    def token(self, value):
        """A random value foreman drew (a launch token): `<TOKENn>`, numbered in the order they are added."""
        if value not in self.tokens:
            self.tokens[value] = f"<TOKEN{len(self.tokens) + 1}>"
            self.add(value, self.tokens[value])

    DATE = re.compile(r"(?<!\d)\d{4}-\d{2}-\d{2}(?!\d)")
    TIME = re.compile(r"(?<![\d:])\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:[+-]\d{2}:?\d{2}|Z)?(?![\d])")
    SECONDS = re.compile(r"(?<![\d.,])\d+\.\d+ s\b")
    CI_SECONDS = re.compile(r"(\bin |, |\()\d+ s(?=[.)\n]|$)")
    EPOCH = re.compile(r"(?<![\w.])(\d{10}|\d{13})(\.\d+)?(?![\w.])")
    USAGE_WRAP = re.compile(r"(\})\n +\.\.\.\n")
    DURATION = re.compile(r'("duration_s": ?)\d+(?:\.\d+)?')
    PID = re.compile(r"(\bpid[ =]|\"pid\": ?|\"pid\", ?)\d+")

    def __call__(self, text):
        for value, placeholder in self.literals:
            text = text.replace(value, placeholder)
        text = self.DATE.sub(lambda m: self.dates.get(m.group(0), m.group(0)), text)
        text = self.TIME.sub("<TIME>", text)
        text = self.EPOCH.sub(self._epoch, text)
        text = self.SECONDS.sub("<SECONDS> s", text)
        text = self.CI_SECONDS.sub(r"\1<SECONDS> s", text)
        text = self.DURATION.sub(r"\1<SECONDS>", text)
        text = self.PID.sub(lambda m: m.group(1) + "<PID>", text)
        text = self.USAGE_WRAP.sub(r"\1 ...\n", text)
        return text.replace("\noptional arguments:\n", "\noptions:\n")

    def _epoch(self, m):
        n = int(m.group(1))
        n = n / 1000 if len(m.group(1)) == 13 else n
        return "<EPOCH>" if self.epoch[0] <= n <= self.epoch[1] else m.group(0)


class Transcript:
    """The record of one fixed sequence: every call with its exit code and output, in order. It is kept as it came
    and normalised as a whole at the end (`value`), once the random values of the run are known."""

    TOKEN = re.compile(r"\[foreman launch ([a-z2-7]{10})\]|\"token\": \"([a-z2-7]{10})\"|dropped: ([a-z2-7]{10})")

    def __init__(self, norm):
        self.norm = norm
        self.parts = []

    def section(self, title):
        self.parts.append(f"\n######## {title}\n")

    def call(self, label, r, stdin=None):
        out = [f"$ {label}"]
        if stdin is not None:
            out.append(f"< {stdin}")
        out.append(f"[exit {r.returncode}]")
        if r.stdout:
            out.append(r.stdout.rstrip("\n"))
        if r.stderr:
            out.append("--- stderr")
            out.append(r.stderr.rstrip("\n"))
        self.parts.append("\n".join(out) + "\n")

    def text(self, label, body):
        self.parts.append(f"$ {label}\n{body.rstrip(chr(10))}\n")

    def json(self, label, code, obj):
        """An HTTP answer: its status and its JSON (keys sorted, the machine's figures masked: Normaliser)."""
        if isinstance(obj, dict) and isinstance(obj.get("mac"), dict):
            obj = dict(obj, mac="<MACHINE>")
        body = json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=True) if not isinstance(obj, str) else obj
        self.parts.append(f"$ {label}\n[{code}]\n{body}\n")

    def tree(self, label, root, skip=()):
        """Every file under `root` (sorted, relative path, then its content), as one section."""
        root = Path(root)
        self.section(f"{label}: files under {root}")
        if not root.exists():
            self.parts.append("(missing)\n")
            return
        for f in sorted(root.rglob("*")):
            rel = f.relative_to(root).as_posix()
            if any(rel == s or rel.startswith(s + "/") for s in skip):
                continue
            if f.is_dir():
                if not any(f.iterdir()):
                    self.parts.append(f"=== {rel}/ (empty)\n")
                continue
            try:
                body = f.read_text()
            except (UnicodeDecodeError, OSError) as e:
                body = f"(not text: {type(e).__name__})"
            self.parts.append(f"=== {rel}\n{body.rstrip(chr(10))}\n")

    def value(self, tokens=()):
        """The whole transcript, normalised. The launch tokens (in the launch lines, and `tokens`, e.g. the keys
        of launches.json) are numbered in the order they first appear."""
        raw = "".join(self.parts).lstrip("\n")
        for t in [next(g for g in m.groups() if g) for m in self.TOKEN.finditer(raw)] + list(tokens):
            self.norm.token(t)
        return self.norm(raw)


def compare(case, name, text, folder=GOLDEN):
    """`text` against FOLDER/NAME (tests/golden/ unless said): a failure of `case` with a short diff, or (updating)
    the file rewritten."""
    f = folder / name
    shown = f.relative_to(ROOT).as_posix()
    if updating():
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(text)
        return
    if not f.exists():
        case.fail(f"no golden file {shown}: run bin/foreman selftest --update-golden")
    want = f.read_text()
    if want == text:
        return
    diff = list(difflib.unified_diff(want.splitlines(), text.splitlines(), shown, "now", n=2, lineterm=""))
    more = len(diff) - DIFF_LINES
    case.fail(
        f"{shown} differs ({sum(1 for x in diff if x[:1] in '+-') - 2} lines):\n"
        + "\n".join(diff[:DIFF_LINES])
        + (f"\n… {more} more diff lines" if more > 0 else "")
        + "\nIf the change is meant: bin/foreman selftest --update-golden, and say why in the commit."
    )


def run_entry(args, env, cwd, stdin=None, timeout=60):
    return forkentry.run(args, str(cwd), env, stdin, timeout)
