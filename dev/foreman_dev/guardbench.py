"""`foreman-dev [--project P] guard-bench [--against DIR] [--rounds N] [--cwd DIR] [--json]`: the guard's time per
tool call by the real hook path, and `guard-bench --lines`: the size of its four files.

Time: each case is a PreToolUse event (a made-up session and agent, `agent_type` dev, the case's folder) written to
the stdin of `<checkout>/bin/foreman hook guard`, timed from the start of the process to its exit, so the figure is
what a session waits for: the launcher, Python, the imports, loading the product and the check. The checkout is the
one this tool lives in and, with `--against DIR`, another one (main's, say); their runs are interleaved (every round
runs every case on both, alternating which goes first) so that the machine's load falls on both alike. Each case
gives its median and p90 (nearest rank) and the verdict (allowed, or refused: the hook's exit 2).

What it touches: the hook runs with a copy of the registry (FOREMAN_REGISTRY) and a state root of its own
(FOREMAN_STATE_HOME), both in a temporary folder removed at the end, so the refusals it logs and the calls it records
land there. A product whose foreman.toml sets its own state folder is refused (that state is not redirected). The
commands are only handed to the guard as text: none runs, and the ones that name a push are built here in pieces.

Lines (`--lines`): for lib/foreman/{guard,shell,gitpush,pyscript}.py of each checkout, the lines, and of them the
lines of code (some token that is neither a docstring nor a comment), of docstrings (a module's, class's or
function's first string, every line it spans), comment-only lines and blank lines. A blank line inside a docstring
counts in both, so the four kinds add up to a little more than the lines.
"""

from __future__ import annotations

import argparse
import ast
import io
import json
import math
import os
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
import tokenize
from pathlib import Path
from typing import Any

from foreman import config

ROOT = Path(__file__).resolve().parents[2]  # the checkout this tool lives in
FILES = ("lib/foreman/guard.py", "lib/foreman/shell.py", "lib/foreman/gitpush.py", "lib/foreman/pyscript.py")
PUSH = "git " + "push"  # built in pieces: a guard that reads this file's commands sees no push here
PUBLISH = "npm " + "publish"
SESSION = "guard-bench-session"
AGENT = "guard-bench-agent"


class BenchError(Exception):
    pass


def cases(cwd: str, scratch: str) -> list[tuple[str, str, dict[str, Any]]]:
    """(name, what it is, tool call) of each case: a Bash command or another tool's input, from `cwd`; `scratch` is a
    folder no product owns, for the paths a command writes to (only named: nothing runs)."""
    bash = "Bash"
    long_ = (
        'for f in $(ls lib/foreman/*.py); do printf "%s %s\\n" "$f" "$(wc -l < "$f")"; done | sort -k2 -n | tail -5'
        f" && grep -rn 'def check' lib/foreman/guard.py ; git -C {cwd} log --oneline -3 > {scratch}/log.txt"
        " || echo failed"
    )
    return [
        ("floor", "ls (process, imports and all)", {"tool": bash, "command": "ls"}),
        ("simple", "ls -la lib/foreman", {"tool": bash, "command": "ls -la lib/foreman"}),
        ("git-read", "git status --short", {"tool": bash, "command": "git status --short"}),
        (
            "compound",
            "cd, git log | head; echo > file",
            {"tool": bash, "command": f"cd {cwd} && git log --oneline -5 | head -3; echo done > {scratch}/out.txt"},
        ),
        ("long-compound", "for $(…) | sort && grep; git -C log > f || echo", {"tool": bash, "command": long_}),
        (
            "py-heredoc",
            "python3 - <<'EOF' json/re/pathlib, a sentence naming a publish",
            {
                "tool": bash,
                "command": "python3 - <<'EOF'\nimport json, re\nfrom pathlib import Path\n"
                f'note = "never {PUBLISH} from here"\n'
                'rows = json.loads(Path("rows.json").read_text())\nprint(re.sub("a", "b", str(rows)), note)\nEOF',
            },
        ),
        (
            "py-heredoc-os",
            "python3 - <<'EOF' os.walk, subprocess git status",
            {
                "tool": bash,
                "command": "python3 - <<'EOF'\nimport os, subprocess\n"
                'n = sum(len(f) for _, _, f in os.walk("lib"))\nsubprocess.run(["git", "status"])\nprint(n)\nEOF',
            },
        ),
        ("push", f"{PUSH} origin bench/x", {"tool": bash, "command": f"{PUSH} origin bench/x"}),
        (
            "push-compound",
            f"cd && git fetch && {PUSH} -u origin HEAD:…",
            {"tool": bash, "command": f"cd {cwd} && git fetch origin && {PUSH} -u origin HEAD:bench/x"},
        ),
        (
            "commit-msg",
            "git -C commit -am with a publish and a push in the message",
            {"tool": bash, "command": f'git -C {cwd} commit -am "bench: never {PUBLISH} nor {PUSH} here"'},
        ),
        (
            "sed-i",
            "sed -i on guard.py && git diff --stat",
            {"tool": bash, "command": f"sed -i '' 's/x/y/' {cwd}/lib/foreman/guard.py && git -C {cwd} diff --stat"},
        ),
        ("read", "Read of guard.py", {"tool": "Read", "file_path": f"{cwd}/lib/foreman/guard.py"}),
        ("write", "Write of a file in a scratch folder", {"tool": "Write", "file_path": f"{scratch}/w.txt"}),
    ]


def event(call: dict[str, Any], cwd: str, n: int) -> str:
    tool = call["tool"]
    ti = {k: v for k, v in call.items() if k != "tool"}
    if tool == "Write":
        ti["content"] = "x\n"
    return json.dumps(
        {
            "session_id": SESSION,
            "agent_id": AGENT,
            "agent_type": "dev",
            "hook_event_name": "PreToolUse",
            "cwd": cwd,
            "tool_name": tool,
            "tool_input": ti,
            "tool_use_id": f"toolu_bench_{n}",
        }
    )


def nearest_rank(xs: list[float], q: float) -> float:
    s = sorted(xs)
    return s[max(0, math.ceil(q * len(s)) - 1)]


def environment(tmp: Path, python: str | None) -> dict[str, str]:
    """The hook's environment: a copy of the registry and a state root in `tmp`, no product forced."""
    reg = config.registry_path()
    if not reg.is_file():
        raise BenchError(f"no registry at {reg}")
    shutil.copy(reg, tmp / "projects.toml")
    (tmp / "state").mkdir()
    env = {k: v for k, v in os.environ.items() if k not in ("FOREMAN_HOME", "FOREMAN_PROJECT", "FOREMAN_WHO")}
    env.update(FOREMAN_REGISTRY=str(tmp / "projects.toml"), FOREMAN_STATE_HOME=str(tmp / "state"))
    if python:
        env["FOREMAN_PYTHON"] = python
    return env


def product(cwd: str, env: dict[str, str], tmp: Path) -> config.Project:
    """The product the hook will find for `cwd` with the bench's environment; refused when its state would not be in
    `tmp`."""
    saved = {k: os.environ.get(k) for k in ("FOREMAN_REGISTRY", "FOREMAN_STATE_HOME")}
    os.environ.update({k: env[k] for k in saved})
    try:
        p = config.project_for_cwd(cwd)
        if p is None:
            raise BenchError(f"no active product owns {cwd}: the hook would check nothing (--cwd)")
        if tmp.resolve() not in p.state.resolve().parents:
            raise BenchError(f"{p.name} keeps its state in {p.state}, which the bench cannot redirect")
        return p
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def bench(checkouts: list[Path], cwd: str, rounds: int, python: str | None, only: list[str]) -> dict[str, Any]:
    """Run every case `rounds` times on every checkout (after one round not counted), interleaved."""
    with tempfile.TemporaryDirectory(prefix="guard-bench-") as t:
        tmp = Path(t).resolve()
        scratch = tmp / "scratch"
        scratch.mkdir()
        env = environment(tmp, python)
        p = product(cwd, env, tmp)
        todo = [c for c in cases(cwd, str(scratch)) if not only or c[0] in only]
        if not todo:
            raise BenchError(f"no case named {', '.join(only)}")
        times: dict[tuple[str, int], list[float]] = {(c[0], k): [] for c in todo for k in range(len(checkouts))}
        verdicts: dict[tuple[str, int], set[str]] = {key: set() for key in times}
        n = 0
        for r in range(rounds + 1):
            for name, _, call in todo:
                order = list(range(len(checkouts)))
                if r % 2:
                    order.reverse()
                for k in order:
                    n += 1
                    data = event(call, cwd, n)
                    t0 = time.perf_counter()
                    done = subprocess.run(
                        [str(checkouts[k] / "bin/foreman"), "hook", "guard"],
                        input=data,
                        text=True,
                        capture_output=True,
                        cwd=cwd,
                        env=env,
                        check=False,
                    )
                    dt = (time.perf_counter() - t0) * 1000
                    if r:
                        times[(name, k)].append(dt)
                    verdicts[(name, k)].add(
                        "allowed"
                        if done.returncode == 0
                        else "refused"
                        if done.returncode == 2
                        else f"exit {done.returncode}"
                    )
    rows = []
    for name, what, _ in todo:
        row: dict[str, Any] = {"case": name, "what": what, "runs": []}
        for k in range(len(checkouts)):
            xs = times[(name, k)]
            row["runs"].append(
                {
                    "median": round(statistics.median(xs), 1),
                    "p90": round(nearest_rank(xs, 0.9), 1),
                    "verdict": "/".join(sorted(verdicts[(name, k)])),
                }
            )
        rows.append(row)
    return {"product": p.name, "cwd": cwd, "rounds": rounds, "checkouts": [str(c) for c in checkouts], "cases": rows}


def count(src: str) -> dict[str, int]:
    """Lines of `src`: total, code, docstrings, comment-only and blank (a blank line in a docstring is in both)."""
    lines = src.splitlines()
    doc_lines: set[int] = set()
    doc_starts: set[tuple[int, int]] = set()
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and node.body:
            first = node.body[0]
            if (
                isinstance(first, ast.Expr)
                and isinstance(first.value, ast.Constant)
                and isinstance(first.value.value, str)
            ):
                doc_starts.add((first.lineno, first.col_offset))
                doc_lines.update(range(first.lineno, (first.end_lineno or first.lineno) + 1))
    code: set[int] = set()
    comments: set[int] = set()
    skip = {tokenize.NL, tokenize.NEWLINE, tokenize.INDENT, tokenize.DEDENT, tokenize.ENDMARKER, tokenize.ENCODING}
    for tok in tokenize.generate_tokens(io.StringIO(src).readline):
        if tok.type == tokenize.COMMENT:
            comments.add(tok.start[0])
        elif tok.type not in skip and tok.start not in doc_starts:
            code.update(range(tok.start[0], tok.end[0] + 1))
    blank = {i for i, line in enumerate(lines, 1) if not line.strip()}
    return {
        "lines": len(lines),
        "code": len(code - blank),
        "docstrings": len(doc_lines),
        "comments": len(comments - code - doc_lines),
        "blank": len(blank),
    }


def lines(checkout: Path) -> dict[str, dict[str, int]]:
    out = {f: count((checkout / f).read_text()) for f in FILES}
    out["total"] = {k: sum(v[k] for v in out.values()) for k in next(iter(out.values()))}
    return out


def add_parser(sp: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    p = sp.add_parser(
        "guard-bench",
        help="the guard's time per call by the real hook path (or --lines: the size of its files)",
        description="Time `bin/foreman hook guard` on a fixed set of tool calls, with a copy of the registry and a "
        "throwaway state; with --against, another checkout interleaved. Nothing is run: the commands are only "
        "handed to the guard.",
    )
    p.add_argument("--against", metavar="DIR", help="another checkout of foreman to compare with (main's, say)")
    p.add_argument("--rounds", type=int, default=25, help="timed runs of each case on each checkout (default 25)")
    p.add_argument("--cwd", help="the folder of the events (default: the product's first repo, else its home)")
    p.add_argument("--case", action="append", default=[], metavar="NAME", help="only this case (repeatable)")
    p.add_argument("--python", help="the Python the launcher runs (FOREMAN_PYTHON)")
    p.add_argument("--lines", action="store_true", help="count the lines of the guard's four files instead")
    p.add_argument("--json", action="store_true", help="the figures as JSON")


def _checkouts(args: argparse.Namespace) -> list[Path]:
    out = [ROOT]
    if args.against:
        other = Path(args.against).expanduser().resolve()
        if not (other / "bin/foreman").is_file():
            raise BenchError(f"{other} is no checkout of foreman (no bin/foreman)")
        out.append(other)
    return out


def _default_cwd(project_name: str | None) -> str:
    p = config.resolve(project_name)
    for r in p.repos.values():
        if r.get("path"):
            return str(p.path(r["path"]))
    return str(p.home)


def run(args: argparse.Namespace, project_name: str | None) -> int:
    try:
        checkouts = _checkouts(args)
        if args.lines:
            got = {str(c): lines(c) for c in checkouts}
            print(json.dumps(got, indent=2) if args.json else render_lines(got))
            return 0
        if args.rounds < 1:
            raise BenchError("--rounds must be at least 1")
        cwd = str(Path(args.cwd).expanduser().resolve()) if args.cwd else _default_cwd(project_name)
        got = bench(checkouts, cwd, args.rounds, args.python, args.case)
    except (BenchError, config.ConfigError, OSError, SyntaxError) as e:
        print(f"foreman-dev guard-bench: {e}", file=sys.stderr)
        return 2
    print(json.dumps(got, indent=2) if args.json else render(got))
    return 0


def render_lines(got: dict[str, dict[str, dict[str, int]]]) -> str:
    out = []
    for checkout, files in got.items():
        out.append(checkout)
        out.append(f"  {'file':<24} {'lines':>6} {'code':>6} {'docs':>6} {'comm':>6} {'blank':>6}")
        for f, c in files.items():
            name = f.rsplit("/", 1)[-1]
            out.append(
                f"  {name:<24} {c['lines']:>6} {c['code']:>6} {c['docstrings']:>6} {c['comments']:>6} {c['blank']:>6}"
            )
    return "\n".join(out)


def render(got: dict[str, Any]) -> str:
    heads = ["this"] + (["against"] if len(got["checkouts"]) > 1 else [])
    out = [f"{got['product']}, from {got['cwd']}, {got['rounds']} rounds (ms: median p90)"]
    for h, c in zip(heads, got["checkouts"]):
        out.append(f"  {h}: {c}")
    head = f"{'case':<14}" + "".join(f" {h:>8} {'p90':>6}" for h in heads)
    if len(heads) > 1:
        head += f" {'Δmed':>7}"
    out.append(head + "  verdict")
    for row in got["cases"]:
        runs = row["runs"]
        line = f"{row['case']:<14}" + "".join(f" {r['median']:>8.1f} {r['p90']:>6.1f}" for r in runs)
        if len(runs) > 1:
            base = runs[1]["median"]
            line += f" {(runs[0]['median'] - base) / base * 100:>+6.1f}%" if base else f" {'-':>7}"
        verdicts = {r["verdict"] for r in runs}
        line += "  " + (verdicts.pop() if len(verdicts) == 1 else " | ".join(r["verdict"] for r in runs))
        out.append(line)
    return "\n".join(out)
