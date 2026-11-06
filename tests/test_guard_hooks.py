import json
import os
import time
import unittest
from typing import ClassVar

from helpers import ROOT, ProjectCase

from foreman import guard, loop, notes, notices, pause, pyscript, shell
from foreman.backlog import Backlog

# Pathological commands (tens of thousands of characters, deep nesting) must stay linear: the slowest takes ~0.7 s of
# CPU on an idle laptop and up to ~2.2 s with three selftests running at once (CI runners are slower too); anything
# super-linear on these sizes takes tens of seconds or more, so the bound sits well above the loaded linear time.
GUARD_CPU_MAX = 10.0


def guard_cpu(project, cmd, tries=3):
    """(verdict, CPU seconds) of guard.check: the best of up to `tries` runs, stopping at the first under the limit.
    Load noise (a parallel selftest) inflates one run by a few percent; a real blow-up is slow in every run."""
    best = None
    for _ in range(tries):
        t = time.thread_time()
        v = guard.check(project, cmd)
        dt = time.thread_time() - t
        best = dt if best is None else min(best, dt)
        if best < GUARD_CPU_MAX:
            break
    return v, best


TOML = """
[project]
name = "fake"
owner = "Owner"

[paths]
worktrees = "{tmp}/wt"

[agents]
production_slots = 1
ready_target = 1

[loop]
prompt = "Fake orchestrator loop"
checks = ["echo 'project check: 2 simulators booted'"]

[resources.sim]
guard = ["npm run e2e:ios", "simctl boot", "maestro test"]

[resources.docker]
guard = ["make up", "docker compose up"]

[resources.gpu]
guard = ["xcodebuild", "swift build"]
allow = ["(^|[\\\\s/])tools/ci\\\\.sh\\\\b"]

[deny]
patterns = [
    "make deploy", "(^|\\\\s)kamal ", "git push .*(v[0-9]|--tags)", "(^|\\\\s)eas (build|update|submit)", "app:send-",
]
"""

# (command, expected): None = allowed, "deny", or the resource name the lock check names
TABLE = [
    ("ls -la", None),
    ("echo hello && git status", None),
    ("ls make", None),
    ('foreman note "Deploy" "never run make deploy without Owner"', None),
    ('git commit -m "docs: explain kamal setup"', None),
    ('git commit -m "fix: a; never make deploy | here"', None),
    ("foreman note 'Deploy' 'step 1; make deploy && step 2'", None),
    ("cat <<'EOF' > notes.md\nmake deploy\nEOF", None),
    ('foreman lock sim run "e2e" npm run e2e:ios -- --screens', None),
    ('FOREMAN_WHO=a foreman lock docker run "api" make up', None),
    ('GPU_LOCK_WHO=a bash gpu_lock.sh run "build" xcodebuild -scheme App', "gpu"),  # impl is gone
    ("tools/ci.sh wt --toy", None),
    ('echo "make up later"', None),
    ("make deploy", "deny"),
    ('bash -c "make deploy"', "deny"),
    ('sh -c "ls; make deploy"', "deny"),
    ("git status; make deploy", "deny"),
    ("cd api && kamal app logs", "deny"),
    ("git push origin v1.2.0", "deny"),
    ("git push --tags", "deny"),
    ("npx eas build -p ios", "deny"),
    ("php bin/console app:send-welcome-emails", "deny"),
    ('foreman lock sim run "x" make deploy', "deny"),
    ("npm run e2e:ios -- --flows login", "sim"),
    ("xcrun simctl boot ABCD", "sim"),
    ("cd app; maestro test flows/", "sim"),
    ("FOO=1 make up", "docker"),
    ("echo go && docker compose up -d", "docker"),
    ("/usr/bin/xcodebuild -workspace x", "gpu"),
    ("time swift build -c release", "gpu"),
    ('foreman lock docker run "x" xcodebuild', "gpu"),
    # The values of data arguments are not compared (with --project, a path, FOREMAN_WHO=, integrate's texts)
    ('foreman --project fake note "t" "never make deploy here"', None),
    ('FOREMAN_WHO=fo28 foreman --project fake progress "done; kamal app logs stays with Owner"', None),
    ('foreman --project fake backlog add E1 --id FK-9 --title "x" --detail "a; kamal app logs; b"', None),
    ('foreman integrate fake fo28/x "FK-1 T1" --note "no make deploy" --result "ok" "merge: make deploy"', None),
    ('foreman integrate fake fo28/x "FK-1 T1" --ci=--real --ci --toy --note "no make deploy"', None),
    ('foreman integrate fake fo28/x "FK-1 T1" --ci="make deploy"', "deny"),
    ('foreman guard "make deploy"', None),
    ('bin/foreman --project fake note "t" "kamal app logs"', None),
    ('foreman note "t" "$(cat <<\'EOF\'\nmake deploy\nkamal app logs\nEOF\n)"', None),
    ('git commit -m "docs: kamal app logs; make deploy"', None),
    ("git commit -m \"$(cat <<'EOF'\nfix: never make deploy\n)\nEOF\n)\"", None),
    ('foreman note "t" "make deploy" | tail -1', None),
    ('foreman lock sim run "make deploy" npm run e2e:ios', None),
    # What still runs inside or next to a text command is compared
    ("foreman --project fake note t x; kamal app logs", "deny"),
    ("foreman note t x & kamal app logs", "deny"),
    ('foreman note "t" "$(kamal app logs)"', "deny"),
    ('git commit -m "`kamal app logs`"', "deny"),
    ('foreman note t \\"; kamal app logs; echo \\"', "deny"),
    ('foreman backlog add E1 --id X --title "make deploy" | sh', "deny"),
    ('foreman integrate fake "$(kamal app logs)" FK-1', "deny"),
    ('foreman integrate fake "x; make deploy" FK-1', "deny"),
    ("foreman note t 'make deploy", "deny"),
    ("foreman note x <(kamal app logs)", "deny"),
    ('git commit -F msg.txt "make deploy"', "deny"),
    ("cat <<EOF\n$(kamal app logs)\nEOF", "deny"),
    ('foreman() { eval "$@"; }; foreman note t "make deploy"', "deny"),
    ('foreman note t "make deploy" > >(sh)', "deny"),
    ('PATH=/tmp/x foreman note t "make deploy"', "deny"),
    ('foreman note t "make deploy" > x.sh; bash x.sh', "deny"),
    ('foreman note t "make deploy" > /dev/null 2>&1', None),
    # What runs inside is a command of its own (anchored patterns find it at its start)
    ("sh -c 'kamal app logs'", "deny"),
    ('bash -lc "cd api && kamal app logs"', "deny"),
    ('eval "kamal app logs"', "deny"),
    ("bash <<'EOF'\nkamal app logs\nEOF", "deny"),
    ("cat <<EOF | sh\nkamal app logs\nEOF", "deny"),
    ("cat <<EOF |\nkamal app logs\nEOF\nsh", "deny"),
    ('bash <<< "kamal app logs"', "deny"),
    ("find . -name x -exec sh -c 'kamal app logs' \\;", "deny"),
    ("ls | xargs -I{} sh -c 'kamal app logs {}'", "deny"),
    ('sudo -u root bash -c "kamal app logs"', "deny"),
    ('foreman lock sim run "x" bash -c "kamal app logs"', "deny"),
    ('ssh host "kamal app logs"', "deny"),
    ('echo "kamal app logs" | bash', "deny"),
    ("$(cat <<'EOF'\nkamal app logs\nEOF\n)", "deny"),
    ('source <(echo "kamal app logs")', "deny"),
    ("bash -c \"foreman note t 'make deploy'\" | sh", "deny"),
    ("python3 - <<'EOF'\nimport os; os.system(\"make deploy\")\nEOF", "deny"),
    ("bash -c \"foreman note t 'make deploy'\"", None),
    ("bash -c \"foreman note t 'kamal app logs'\"", None),
    ("python3 - <<'EOF'\nimport os; os.system(\"kamal app logs\")\nEOF", None),  # O4: raw only
    ("git commit -F - <<'EOF'\nkamal app logs\nEOF", None),
    ("foreman note t \"bash -c 'kamal app logs'\"", None),
    ("bash -c 'ls; git status'", None),
]


class GuardTest(ProjectCase):
    toml = TOML

    def test_table(self):
        for cmd, want in TABLE:
            with self.subTest(cmd=cmd):
                v = guard.check(self.project, cmd, {"session": "S-main"})  # a main thread: an agent never integrates
                got = None if v is None else ("deny" if v[0] == "deny" else v[1][0])
                self.assertEqual(got, want)

    def test_table_file_and_check(self):
        rows = [
            "# expect<TAB>command",
            "",
            *(
                f"{w or ('orchestrator' if c.startswith('foreman integrate') else 'allow')}\t{c}".replace("\n", "\\n")
                for c, w in ((c, w if w in (None, "deny") else f"lock:{w}") for c, w in TABLE)
            ),
        ]
        (self.home / "guard.tsv").write_text("\n".join(rows) + "\n")
        r = self.cli("guard", "--table", str(self.home / "guard.tsv"))
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn("ok (44 deny, 9 lock, 29 allow, 2 orchestrator)", r.stdout)
        with open(self.home / "guard.tsv", "a") as f:
            f.write("allow\tmake deploy\nlock:docker\txcodebuild\n")
        r = self.cli("guard", "--table", str(self.home / "guard.tsv"))
        self.assertEqual(r.returncode, 1)
        self.assertIn("want allow, got deny", r.stdout)
        self.assertIn("want lock:docker, got lock:gpu", r.stdout)
        # foreman check runs the table named in [check]
        (self.home / "rules.md").write_text("rules\n")
        self.write_toml(self.toml + '\n[check]\nguard_table = "guard.tsv"\n')
        r = self.cli("check")
        self.assertEqual(r.returncode, 1)
        self.assertIn("2 wrong", r.stdout)
        self.assertIn("rules", r.stdout)
        (self.home / "guard.tsv").write_text("maybe\tls\n")
        self.assertIn("line 1", self.cli("check").stdout)

    def test_hook_rejects_inside_and_ignores_outside(self):
        ev = {"hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": {"command": "make deploy"}}
        r = self.hook("guard", ev)
        self.assertEqual(r.returncode, 2)
        self.assertIn("make deploy", r.stderr)
        self.assertIn("Owner", r.stderr)
        r = self.hook("guard", ev, cwd=self.tmp)  # not a registered project
        self.assertEqual((r.returncode, r.stdout, r.stderr), (0, "", ""))
        ev["tool_input"]["command"] = "make up"
        r = self.hook("guard", ev)
        self.assertEqual(r.returncode, 2)
        self.assertIn("foreman lock docker run", r.stderr)
        ev["tool_input"]["command"] = "ls"
        self.assertEqual(self.hook("guard", ev).returncode, 0)

    def test_hooks_survive_garbage(self):
        for name in ("guard", "agents", "loop", "resume", "nonsense"):
            r = self.cli("hook", name, inp="not json")
            self.assertEqual(r.returncode, 0, name)


PUSH, REMOTE = "git " + "push", "git remote " + "add"  # built in pieces: the installed guard reads this file's commands
PUB, TWINE = "npm " + "publish", "twine " + "upload"
# Commands the guard used to reject and this one lets through: data values of TEXT_ARGS.
FLIPS = {
    'foreman --project fake note "t" "never make deploy here"',
    'FOREMAN_WHO=fo28 foreman --project fake progress "done; kamal app logs stays with Owner"',
    'foreman --project fake backlog add E1 --id FK-9 --title "x" --detail "a; kamal app logs; b"',
    'foreman integrate fake fo28/x "FK-1 T1" --note "no make deploy" --result "ok" "merge: make deploy"',
    'foreman integrate fake fo28/x "FK-1 T1" --ci=--real --ci --toy --note "no make deploy"',  # --ci
    'foreman guard "make deploy"',
    'foreman lock sim run "make deploy" npm run e2e:ios',
    f'foreman --project foreman note "t" "nunca {REMOTE} sin Owner"',
    f'FOREMAN_WHO=fo28 foreman --project foreman progress "hecho; {PUSH} es de Owner"',
    'foreman --project foreman backlog add E1 --id AB-99 --title "x" --detail "a; uv publish; b"',
    'foreman integrate foreman fo28/x "AB-28 T1" --note "sin npm publish ni ' + PUSH + '" --result "ok"',
    f'foreman guard "{REMOTE} origin x"',
    # T2: a note inside `bash -c "…"` is compared by the recursion, with its exemption
    "bash -c \"foreman note t 'make deploy'\"",
    f"bash -c \"foreman note t '{REMOTE} origin x'\"",
    # printers (also to a literal path), search patterns, gh pr|issue --title/--body, git tag -m
    f'echo "{PUB}" | tee -a ops/notes/x.md',
    f'{{ echo "{PUB}"; echo b; }} > ops/notes/x.md',
    f'echo "no {PUB}"',
    f'grep -rn -e "{PUB}" docs',
    f'git -C ~/Projects/foreman grep -c "{TWINE}"',
    f'gh pr create --title "x" --body "after merge: {PUB}"',
    f'gh pr edit 3 --title="{PUB} done"',
    f'git tag -am "{PUB}" v2',
    f"bash -c \"echo '{PUSH}' > ops/reviews/x.md\"",
}


# Rows the old guard let through because the command ran inside a string, a heredoc or a pipe.
NESTED = [
    "sh -c 'kamal app logs'",
    'eval "kamal app logs"',
    "bash <<'EOF'\nkamal app logs\nEOF",
    "cat <<EOF | sh\nkamal app logs\nEOF",
    'bash <<< "kamal app logs"',
    "ls | xargs -I{} sh -c 'kamal app logs {}'",
    "find . -name x -exec sh -c 'kamal app logs' \\;",
    'ssh host "kamal app logs"',
    'echo "kamal app logs" | bash',
    "$(cat <<'EOF'\nkamal app logs\nEOF\n)",
    'source <(echo "kamal app logs")',
    f'bash -c "{PUSH}"',
    f"bash <<'EOF'\n{PUSH}\nEOF",
    f'eval "{PUSH}"',
    # a group or compound piped into a reader, or a variable in the program's place: read as fed to a shell
    f'{{ foreman note t "{PUSH}"; }} | sh',
    f'{{ git commit -m "{PUSH}"; }} | bash',
    f'if true; then foreman note t "{PUSH}"; fi | sh',
    f'{{ echo "{PUSH}"; }} | sh',
    f'for x in 1; do echo "{PUSH}"; done | sh',
    f'{{ foreman note t "{PUSH}"; }} | python3 -',
    f'echo "{PUSH}" | if true; then sh; fi',
    f'echo "{PUSH}" | ( sh )',
    f'{{ sh; }} <<< "{PUSH}"',
    f'f(){{ sh; }}; echo "{PUSH}" | f',
    f'echo "{PUSH}" > >(sh)',
    f'x="{PUSH}"; $x',
    f'printf -v x "{PUSH}"; $x',
    f'for c in "{PUSH}"; do $c; done',
    f'x=$(echo "{PUSH}"); $x',
    f'read x <<< "{PUSH}"; $x',
    f'x="{PUSH}"; sh -c "$x"',
    f'x="{PUSH}"; echo "$x" | sh',
    f"trap '{PUSH}' EXIT",
    f'{{ bash -c "{PUSH}"; }}',
    f'f(){{ sh -c "$1"; }}; f "{PUSH}"',
    f'echo "{PUSH}"; $_',
    # a printer, a search or a message whose text still runs: piped or substituted into a shell, a file written and
    # run in the line, a device or a descriptor, an option outside the closed list, a program of the same name
    f'echo "{PUSH}" |& bash',
    f"printf '{PUSH}\n' | tee f.sh | sh",
    f'echo "$({PUSH})" > notes.md',
    f'eval "$(echo {PUSH})"',
    f'echo "{PUB}" >&3',
    f'printf "{PUB}" > /dev/fd/3',
    f'echo "{PUB}" > /dev/tcp/127.0.0.1/9',
    f'alias e=echo; e "{PUB}"',
    f"rg --pre {PUSH} src",
    f'git grep -O"{PUB}" x',
    f'grep -e "{PUB}" f | sh',
    f'gh pr create --title t --body "$({PUSH})"',
    f'echo "{PUSH}" > x.sh; sh x.sh',
    f'echo "{PUSH}" > x.sh && chmod +x x.sh && ./x.sh',
    f'echo "{PUSH}" | cat > x.sh && sh x.sh',
    f'echo "{PUSH}" > x.sh; sh *.sh',
    f'echo "{PUSH}" > $T; sh $T',
    f'{{ echo "{PUB}"; }} >&3',
    f'{{ echo "{PUB}"; }} > x.sh; source x.sh',
    f'echo "{PUB}" > .git/hooks/pre-commit',
    f'echo "{PUB}" | sudo tee /dev/fd/3',
    f'./echo "{PUB}"',
    f'printf "$f" "{PUB}"',
    f'grep "a" f -e "{PUB}"',
    f'git -c core.fsmonitor=sh grep "{PUB}"',
    f'rg -z "{PUB}"',
]


class GuardNestedTest(ProjectCase):
    """Data values of TEXT_ARGS pass and commands run inside strings, heredocs or pipes are denied, on every
    table the guard must keep green."""

    toml = TOML

    def tables(self):
        from foreman import config

        out = [(self.project, [(c, w) for c, w in TABLE])]
        for home in (ROOT / "ops",):  # foreman's own home (in this repo); never another product's
            if (home / "tests/guard.tsv").exists():
                p = config.load_project(home)
                out.append((p, [(c, e) for _, e, c, _ in guard.parse_table((home / "tests/guard.tsv").read_text())]))
        return out

    def test_data_values_pass_and_nested_runs_are_denied(self):
        """Judged per table. A data value trips no deny pattern (it may still be another caller's: `foreman
        integrate` is the orchestrator's). A nested run is refused of any kind a guard table reads as deny
        (guard._got): from a main checkout (`.git` a folder) `echo … > .git/hooks/pre-commit` is a write of the git
        class (file:git) before it is a nested run; from a worktree (`.git` a file) it is the nested run."""
        for p, rows in self.tables():
            verdicts = {cmd: guard.check(p, cmd) for cmd, _ in rows}
            with self.subTest(table=p.name):
                tripped = {c for c in FLIPS if c in verdicts and verdicts[c] is not None and verdicts[c][0] == "deny"}
                self.assertEqual(tripped, set())
                passed = {c for c in NESTED if c in verdicts and guard._got(verdicts[c]) != "deny"}
                self.assertEqual(passed, set())
        tables = self.tables()
        if len(tables) == 1:  # the public snapshot carries no ops/ (dev/foreman_dev/snapshot.py)
            self.skipTest("no ops/ home in this checkout: the cases only its guard table lists are not judged")
        listed = {c for _, rows in tables for c, _ in rows}
        self.assertEqual({c for c in (*FLIPS, *NESTED) if c not in listed}, set())  # every case is in some table

    def test_unbounded_runs_read_as_fed_to_a_shell(self):
        """A group or compound whose output or input reaches a reader, a variable that runs (with the values assigned
        in the line compared as commands), >(…), a coprocess and a function fed in the line: the whole line is read
        as if it went to a shell. What only mentions the text, with no reader and no variable run, is unchanged."""
        hit = ("deny", "(^|\\s)kamal ")
        for cmd in (
            '{ foreman note t "kamal app logs"; } | sh',
            'while true; do echo "kamal app logs"; done | sh',
            'until false; do echo "kamal app logs"; done | bash',
            '{ echo a; echo "kamal app logs"; } |& sh',
            '{ { echo "kamal app logs"; }; } | sh',
            'if true; then echo a; else echo "kamal app logs"; fi | sh',
            '{ echo "kamal app logs"; } | cat | sh',
            '{ echo "kamal app logs"; } | xargs -I{} sh -c {}',
            '{ foreman note t "kamal app logs"; } | node',
            'echo "kamal app logs" | if true; then sh; fi',
            'echo "kamal app logs" | ( sh )',
            '( sh ) <<< "kamal app logs"',
            "{ sh; } <<'EOF'\nkamal app logs\nEOF",
            'if true; then sh; fi <<< "kamal app logs"',
            'echo "kamal app logs" | while read l; do $l; done',
            'f(){ sh; }; f <<< "kamal app logs"',
            'function f { sh; }; echo "kamal app logs" | f',
            'alias s=sh; echo "kamal app logs" | s',
            '{ echo "kamal app logs"; } > >(sh)',
            'exec > >(sh); echo "kamal app logs"',
            'coproc sh; echo "kamal app logs" >&${COPROC[1]}',
            "x='kamal app logs'; $x",
            'x="kamal app logs" ; "$x"',
            'x="kamal app logs"; ${x}',
            'export x="kamal app logs"; $x',
            'local x="kamal app logs"; $x',
            'x+="kamal app logs"; $x',
            'a[0]="kamal app logs"; ${a[0]}',
            'x="kamal app logs"\n$x',
            'printf -v x "%s" "kamal app logs"; $x',
            'for c in ls "kamal app logs"; do "$c"; done',
            'select c in "kamal app logs"; do $c; done',
            'set -- "kamal app logs"; $1',
            'mapfile -t a <<< "kamal app logs"; "${a[@]}"',
            "x=$(cat <<'EOF'\nkamal app logs\nEOF\n); $x",
            'x="kamal app logs"; eval "$x"',
            'x="kamal app logs"; sh <<< "$x"',
            'x="kamal app logs"; ssh host "$x"',
            'x="kamal app logs"; echo | xargs $x',
            'x="kamal app logs"; find . -exec $x \\;',
            'read x <<< "kamal app logs"; sh -c "$x"',
            'x="kamal app logs"; trap "$x" EXIT',
            "trap -- 'kamal app logs' EXIT",
            'echo "kamal app logs"; $_',
            'git commit -m "kamal app logs"; $_',
            ': ${x:="kamal app logs"}; $x',
            'a=("kamal app logs"); "${a[@]}"',
            'echo "kamal app logs" | read x; $x',
            'foreman note t "kamal app logs" | read x; $x',
            'f(){ $1; }; f "kamal app logs"',
            'f(){ sh -c "$1"; }; f "kamal app logs"',
            'x="kamal app logs"; echo $($x)',
            'printf -vx "kamal app logs"; $x',
            'x="kamal app logs"; y=$x; $y',
            'x="kamal app logs"; ${!y}',
            '{ bash -c "kamal app logs"; }',
            '{ eval "kamal app logs"; }',
            '{ ssh host "kamal app logs"; }',
        ):
            with self.subTest(cmd=cmd):
                self.assertEqual(guard.check(self.project, cmd), hit)
        for cmd in (
            "$DC exec app ls",
            '"$PY" -m pytest',
            "x=1; echo $x",
            '{ foreman note t "kamal app logs"; } | grep x',
            'if true; then foreman note t "kamal app logs"; fi',
            'for f in a b; do echo "$f"; done | sort',
            "ls | { cat; }",
            "{ echo hi; } | sh",
            "bash -c 'echo $HOME'",
            'source .venv/bin/activate && foreman note t "kamal app logs" | tail -1',
            "a=(x y); foreman note t 'kamal app logs' | head",
            "trap - EXIT",
            "trap -p",
            # a variable that runs keeps the exemptions; only the values the line gives its variables are commands
            'S=/tmp/s; git commit -m "never kamal app logs"; $S/run.sh',
            "B='foreman backlog'; cat > notes.md <<'EOF'\nkamal app logs\nEOF\n$B edit X-1 --plan notes.md",
            "R() { foreman backlog decision resolve \"$@\"; }; R J1 --answer a; cat >> f.md <<'EOF'\n"
            "kamal app logs\nEOF",
        ):
            with self.subTest(cmd=cmd):
                self.assertIsNone(guard.check(self.project, cmd))

        def mode(cmd):
            segs = shell.pipelines(shell.lex(cmd)[0])
            info = [shell.runs([t for t in x.toks if t.kind == "word"]) for x in segs]
            fed = {k for j, r in enumerate(info) if r.stdin for k in shell.feeders(segs, j)}
            return shell.unbounded(segs, info, shell.values(segs, info), fed)

        for cmd, want in (
            ("{ echo a; } | sh", "shell"),
            ("tee >(gzip > x.gz) < f", "shell"),
            ("$DC exec app ls", "var"),
            ('x=1; ssh host "$x"', "var"),
            ("echo $HOME | sh", None),  # not assigned in the line: nothing of it to compare
            # the variables of a string that runs elsewhere are that string's own (a marker in front stays with it)
            ("OK=\"Owner, x\" ssh host 'W=$(docker ps -q); docker logs $W'", None),
            ("ls | { cat; }", "shell"),  # a group still reads its input as a shell would (as before)
        ):
            self.assertEqual(mode(cmd), want, cmd)

    def test_printers_searches_and_messages_are_text(self):
        """echo/printf, the pattern of grep, rg and git grep, gh pr|issue --title/--body and git tag -m are text, and a
        printer may write to a literal path; what runs is still compared: a file written and run in the line (its name
        again, a glob, a variable or a pipe into something that runs), a device or a descriptor, an option outside the
        closed list, a program of the same name, a word bash would split."""
        hit = ("deny", "(^|\\s)kamal ")
        for cmd in (
            'echo "kamal app logs" > x.sh; bash x.sh',
            'printf "%s" "kamal app logs" > p; sh < p',
            'mkfifo p; sh < p & echo "kamal app logs" > p',
            'echo "kamal app logs" > x; f=x; sh "$f"',
            'echo "kamal app logs" > /tmp/x.sh; ls /tmp | xargs sh',
            'echo "kamal app logs" > /tmp/x.sh; find /tmp -name "*.sh" -exec sh {} \\;',
            'echo "kamal app logs" > x.sh; $(ls -t | head -1)',
            'echo "kamal app logs" > x.sh; source $(ls -t | head -1)',
            'echo "kamal app logs" > "a b.sh"; sh a\\ b.sh',
            'echo "kamal app logs" > x; cp x y.sh; sh y.sh',
            'echo "kamal app logs" > /tmp/x; crontab /tmp/x',
            'echo "kamal app logs" | sudo tee /etc/x.sh; sh /etc/x.sh',
            'echo "kamal app logs" | head > f.sh; sh f.sh',
            'echo "kamal app logs" > f.md; $_',
            'echo "kamal app logs" | read x; eval "$x"',
            'echo "kamal app logs" | bash -c "cat | sh"',
            'echo "kamal app logs" | python3',
            'gh pr create --title t --body x --repo "$(kamal app logs)"',
            'git tag -m "x" v1 && kamal app logs',
            "echo $(kamal app logs)",
            "".join(f"echo a > f{i}; " for i in range(9)) + 'echo "kamal app logs" > g',  # too many files: may run
        ):
            with self.subTest(cmd=cmd):
                self.assertEqual(guard.check(self.project, cmd), hit)
        for cmd in (
            'echo "make deploy" > "$(mktemp)"',
            'echo "make deploy" > /proc/self/fd/3',
            'echo "make deploy" | tee /dev/tty',
            'echo "make deploy" | tee f > /dev/fd/3',
            'exec 3>&1; echo "make deploy" >&3',
            'ln -s /dev/fd/3 f; echo "make deploy" > f',
            'printf -vx "make deploy"',
            'printf -- -v x "make deploy" > f; sh f',
            "echo $x 'make deploy'",
            'grep --include=*.md "make deploy" docs',
            'rg "make deploy" --hostname-bin sh',
            'rg -e "make deploy" --pre=sh',
            'git grep --textconv "make deploy"',
            'git grep --open-files-in-pager=sh "make deploy"',
            'gh pr comment 3 --body-file <(echo "make deploy")',
            '/tmp/grep "make deploy" f',
            'sudo echo "make deploy"',
            'grep "make deploy" --pre=x f',
            'rg --files "make deploy"',  # every operand is a path
            'gh pr merge 3 --body "make deploy"',
        ):
            with self.subTest(cmd=cmd):
                self.assertEqual(guard.check(self.project, cmd), ("deny", "make deploy"))
        for cmd in (
            'echo "make deploy" > ops/x.md',
            "printf -- '%s\\n' \"make deploy\" >> notes.md",
            'echo "make deploy" >| notes.md',
            'echo "make deploy" &> notes.md',
            'echo "make deploy" 2>&1 | head -1 > /dev/null',
            'echo "make deploy" | sudo tee /etc/x',
            "echo make deploy",
            '\\echo "make deploy"',
            '/usr/bin/grep -n "make deploy" f',
            'grep -r "make deploy" --exclude-dir=.git .',
            'grep -5 "make deploy" f',
            'grep -nA3 "make deploy" f',
            'rg -uu -S "make deploy"',
            'rg -e "make deploy" -t py',
            'git grep -e a --and -e "make deploy"',
            'git grep --regexp="make deploy" HEAD -- docs',
            'egrep -e "make deploy" -f /dev/stdin f',
            "gh pr create --title \"$(echo 'make deploy')\" --body x",
            'gh issue edit 3 -t "make deploy" -b "kamal app logs"',
            'gh pr review 3 --comment --body "make deploy"',
            'git -C wt tag -a v1 --message "make deploy"',
            'echo "make deploy" > notes.md; git log -1',
        ):
            with self.subTest(cmd=cmd):
                self.assertIsNone(guard.check(self.project, cmd))
        # the words of the command and the paths of a search are still compared
        env = r"(^|[\s/'\"=<])\.env($|[\s'\"])"
        for pats, cmd, want in (
            (["gh\\s+pr\\s+(merge|review)"], 'gh pr review 3 --approve -b "ok"', True),
            ([env], "gh pr create --title x --body-file .env", True),
            ([env], 'grep -n "KEY" .env', True),
            ([env], 'grep -n -- "KEY" .env', True),
            ([env], 'git grep -n "KEY" -- .env', True),
            ([env], "rg -f .env src", True),
            ([env], 'grep -rn ".env" docs', False),
            ([env], 'echo "never commit .env" > notes.md', False),
        ):
            with self.subTest(cmd=cmd):
                self.assertEqual(guard.deny(pats, cmd) is not None, want)

        def parts(cmd):
            segs = shell.pipelines(shell.lex(cmd)[0])
            info = [shell.runs([t for t in x.toks if t.kind == "word"]) for x in segs]
            return segs, info

        for cmd, want in (
            ("echo a > x.md", ["x.md"]),
            ("echo a 2>&1 >> y | tee -a z", ["y", "z"]),
            ("echo a >&2; echo b > /dev/null", []),
            ("echo a >&3", None),
            ("echo a > $T", None),
            ("echo a > /dev/fd/3", None),
            ("echo a | sudo tee /dev/fd/3", None),
            ("echo a > .git/config", None),
        ):
            files = shell.written(*parts(cmd))
            self.assertEqual(None if files is None else [t.value for t in files], want, cmd)
        for cmd, want in (
            ("echo a > x.md; git log", False),
            ("echo a > x.md; git add x.md", True),
            ("echo a > x.md; bash -c 'ls'", False),
            ('echo a > x.md; bash -c "$c"', True),
            ("echo a > x.md; ls | sh", True),
            ("echo a > x.md; $EDITOR", True),
        ):
            segs, info = parts(cmd)
            self.assertEqual(shell.read_back(segs, info, shell.written(segs, info) or []), want, cmd)

    def test_unreadable_compares_everything(self):
        for cmd in (
            "foreman note t 'make deploy",
            "foreman note t $'x' make deploy",
            'foreman note t "$((1))" make deploy',
            "foreman note t " + "$(" * 6 + "make deploy" + ")" * 6,
            "foreman note t 'x\x01' make deploy",
            'foreman note t "make deploy',
            "foreman note t `make deploy",
            "foreman note t ${X:-$(make deploy)}",
            "foreman note t )make deploy",
            "foreman note t <<EOF\nmake deploy",
        ):
            with self.subTest(cmd=cmd):
                self.assertEqual(guard.check(self.project, cmd), ("deny", "make deploy"))
        self.assertIsNone(guard.check(self.project, "echo 'unclosed"))  # no pattern, nothing to block

    def test_refusal_says_where_and_how(self):
        """The refusal names the part of the command that matched, what kind of text it was in, and how to write
        text instead; the verdict still compares equal to ("deny", pattern)."""
        k = "(^|\\s)kamal "
        long = "import os\n" + "x = 1\n" * 30 + "s = ' kamal app logs'\n" + "y = 2\n" * 30  # os: not text (T3)
        for cmd, where, part in (
            ("kamal app logs", "command", "kamal app logs"),
            ("ls && sudo kamal app logs", "command", "sudo kamal app logs"),
            ("sed -i 's/ kamal x/y/' f.md", "sed", "sed -i 's/ kamal x/y/' f.md"),
            ("for c in a ' kamal x'; do echo $c; done", "loop", "for c in a ' kamal x'"),
            ("[ \"$x\" = ' kamal x' ] && echo y", "loop", "[ \"$x\" = ' kamal x' ]"),
            ("python3 -c \"print(' kamal x')\"", "code", "python3 -c \"print(' kamal x')\""),
            ("node -e \"console.log(' kamal x')\"", "code", "node -e \"console.log(' kamal x')\""),
            ("python3 - <<'EOF'\nimport os\nos.system(' kamal x')\nEOF", "script", "import os os.system(' kamal x')"),
            ("echo 'kamal app logs' | sh", "piped", "kamal app logs"),
            ("x='kamal app logs'; $x", "variable", "kamal app logs"),
            ("bash -c 'kamal app logs'", "command", "kamal app logs"),
            ("foreman note t 'kamal app logs", "whole", "foreman note t kamal app logs"),
        ):
            with self.subTest(cmd=cmd):
                v = guard.check(self.project, cmd)
                self.assertEqual(v, ("deny", k))
                self.assertEqual((v[1].where, v[1].part), (where, part))
        hit = guard.check(self.project, "python3 - <<'EOF'\n" + long + "EOF")[1]
        self.assertEqual(hit.where, "script")  # a long text is quoted around the match, with … where it is cut
        self.assertEqual(len(hit.part), guard.EXCERPT + 2)
        self.assertTrue(hit.part.startswith("…") and hit.part.endswith("…") and " kamal app logs" in hit.part)
        msg = guard.message(self.project, guard.check(self.project, "sed -i 's/ kamal x/y/' f"))
        self.assertIn("«(^|\\s)kamal » is on the list", msg)
        self.assertIn("It matched in a sed or awk script (`sed -i 's/ kamal x/y/' f`)", msg)
        self.assertIn("use the Edit tool", msg)
        msg = guard.message(self.project, guard.check(self.project, "x='kamal app logs'; $x"))
        self.assertIn("write it with the Write or Edit tool", msg)
        self.assertIn("never done without Owner (fake). Ask Owner first", guard.message(self.project, ("deny", k)))
        self.assertNotIn("matched", guard.message(self.project, ("deny", k)))  # a plain pattern: no where
        self.write_toml(TOML.replace('owner = "Owner"', 'owner = "Owner"\nlanguage = "es"'))
        msg = guard.message(self.project, guard.check(self.project, "echo 'kamal app logs' | sh"))
        self.assertIn("Casó en un texto que ejecuta un shell (`kamal app logs`).", msg)
        self.assertIn("Si solo es texto", msg)

    def test_heredoc_written_and_run(self):
        """A heredoc to cat or tee is text, unless the same line runs the file it writes (or a copy): then its text
        is a command (a script, for an interpreter that is not a shell), and the refusal says so."""
        k = "(^|\\s)kamal "
        body = "<<'EOF'\nkamal app logs\nEOF\n"
        for cmd, where in (
            (f"cat > x.sh {body}sh x.sh", "written"),
            (f"tee x.sh {body}bash x.sh", "written"),
            (f"cat > x.sh {body}. x.sh", "written"),
            (f"cat > x.sh {body}source x.sh", "written"),
            (f"cat > x.sh {body}chmod +x x.sh && ./x.sh", "written"),
            ("cat <<'EOF' | tee /tmp/x.sh\nkamal app logs\nEOF\n/tmp/x.sh", "written"),
            (f"cat > a.md {body}cat > x.sh {body}sh x.sh", "written"),
            (f"cat > x.sh {body}ssh h 'sh x.sh'", "written"),
            (f"cat > x.sh {body}sh -c 'cp x.sh y.sh; sh y.sh'", "written"),
            (f"cat > x.py {body}python3 -I x.py data.txt", "written"),
            (f"cat > x.py {body}python3 -c 'exec(open(\"x.py\").read())'", "written"),
            (f"sudo tee /etc/x.sh {body}sudo sh /etc/x.sh", "written"),
            (f"cat > x.sh {body}mv x.sh y.sh; ln -s y.sh z.sh; sh z.sh", "written"),
            (f"cat > x.sh {body}sh < x.sh", "written"),
            (f"cat > x.sh {body}cat x.sh | sh", "written"),
            (f"cat > x.sh {body}bash -c ./x.sh", "written"),
            (f"cat > x.sh {body}find . -name x.sh -exec sh {{}} \\;", "written"),
            (f'cat > x.sh {body}f=x.sh; sh "$f"', "written"),
            (f"cat > x.sh {body}f=x.sh; $f", "written"),
            (f"cat > x.sh {body}$(ls -t | head -1)", "written"),
            (f"cat > $T {body}sh $T", "written"),
            (f"cat > x {body}crontab x", "written"),
            (f"cat > Makefile {body}make", "written"),
            ("cat > x.sh <<< 'kamal app logs'; sh x.sh", "written"),
            ("cat > x.py <<'EOF'\nos.system(' kamal app logs')\nEOF\npython3 x.py", "written"),
            (". /dev/stdin <<< 'kamal app logs'", "command"),
            (f"F='foreman lock x run'; cat > x.sh {body}$F sh x.sh", "written"),
            (f"S=bash; cat > x.sh {body}$S x.sh", "written"),
        ):
            with self.subTest(cmd=cmd):
                v = guard.check(self.project, cmd)
                self.assertEqual(v, ("deny", k))
                self.assertEqual(v[1].where, where)
        for cmd in (
            f"cat > x.md {body}git add x.md",
            f"cat > x.md {body}$EDITOR x.md",
            f"B='foreman backlog'; cat > x.md {body}$B edit X-1 --plan x.md",
            f"cat > x.md {body}sh build.sh > out.log",
            f"cat > x.sh {body}chmod +x x.sh",
            f"cat > x.sh {body}bash other.sh",
            f"tee x.md {body}wc -l x.md && head x.md",
            f"cat > rows.tsv {body}python3 -I $S/check.py rows.tsv",  # data for a script: not run
            f"cat > a.md {body}cat > x.sh <<'EOF'\nls\nEOF\nsh x.sh",  # only the file that runs is a command
            f"cat > l.txt {body}ssh h 'tar czf b.tgz -T l.txt'",
            f"cat > c.toml {body}for f in *.toml; do python3 -c \"import tomllib; tomllib.load(open('$f'))\"; done",
            f"B='foreman backlog'; cat > p.md {body}$B edit X-1 --plan p.md && $B sub X-1 T1 --title t",
            "cat > x.py <<'EOF'\nos.system('kamal app logs')\nEOF\npython3 x.py",  # read raw, as python3 - <<EOF
        ):
            with self.subTest(cmd=cmd):
                self.assertIsNone(guard.check(self.project, cmd))
        msg = guard.message(self.project, guard.check(self.project, f"cat > x.sh {body}sh x.sh"))
        self.assertIn("It matched in text written to a file that the same command line then runs", msg)
        self.assertIn("run that file in a separate call", msg)

        def kind(cmd):
            segs = shell.pipelines(shell.lex(cmd)[0])
            info = [shell.runs([t for t in s.toks if t.kind == "word"]) for s in segs]
            self.assertTrue(any(shell.heredoc_writer(s, r) for s, r in zip(segs, info)), cmd)
            return shell.runs_written(segs, info, shell.written(segs, info) or [])

        self.assertEqual(kind(f"cat > x.sh {body}sh x.sh"), ("shell", {"x.sh"}))
        self.assertEqual(kind(f"cat > x.py {body}python3 x.py"), ("raw", {"x.py"}))
        self.assertEqual(kind(f"cat > x.py {body}python3 x.py; sh x.py"), ("shell", {"x.py"}))
        self.assertEqual(kind(f"cat > x.py {body}ls | python3"), ("raw", None))
        self.assertEqual(kind(f"cat > x.sh {body}cp x.sh y.sh; ./y.sh"), ("shell", {"x.sh"}))
        self.assertEqual(kind(f'cat > x.sh {body}sh "$f"'), ("shell", None))
        self.assertEqual(kind("".join(f"cat > f{i} <<< a; " for i in range(9))), ("shell", None))
        self.assertEqual(kind(f"cat > x.md {body}git add x.md"), (None, set()))

    def test_python_programs_as_text(self):
        """Python reading its program from a heredoc is compared with its prose blanked when the
        allow-list gate says it cannot start a process; one-word literals stay compared, the rest as before."""
        k = "make deploy"

        def py(body, head="python3 -", tail=""):
            return f"{head} <<'EOF'{tail}\n{body}\nEOF"

        for cmd in (
            py('from pathlib import Path\np = Path("notes.md")\np.write_text(p.read_text() + "never make deploy")'),
            py('import re, json\ns = re.sub("a", "run make deploy", open("x.md").read())\nopen("x.md", "w").write(s)'),
            py('"""Docs: make deploy."""\n# make deploy\nx = f"{1} make deploy {2}"'),
            py('import re\nr = re.compile("make deploy")\nfrom collections import Counter as C'),
            py('x = "make deploy"', head="python3.12 -I -u"),
            py('x = "make deploy"', head="python3"),
            py('x = "make deploy"', tail=" > out.txt"),
            py('import tomllib, glob, fnmatch, textwrap, difflib, datetime, string, math\nx = "make deploy x"'),
            "cd d && " + py('x = "make deploy"') + "\ngit diff --stat",
            py('from pathlib import Path\nPath("x.md").write_text("make deploy")') + "\ngit add x.md && cat x.md",
            "python3 - <<EOF\nx = 'make deploy'\nEOF",  # unquoted, no expansion: the same text
        ):
            with self.subTest(cmd=cmd):
                self.assertIsNone(guard.check(self.project, cmd))
        for cmd in (
            py('import os\nos.system("make deploy")'),
            py('import subprocess\nsubprocess.run(["sh", "-c", "make deploy"])'),
            py('__import__("os").system("make deploy")'),
            py('import sys\nsys.modules["os"].system("make deploy")'),
            py('import importlib\nimportlib.import_module("os").system("make deploy")'),
            py('import glob\nglob.os.system("make deploy")'),
            py('import pathlib\npathlib.Path.home()\nx = pathlib.os\ny = "make deploy"'),
            py('from pathlib import os as o\no.popen("make deploy")'),
            py('from fnmatch import posixpath\nx = "make deploy"'),
            py('import json\nx = json._default_encoder\ny = "make deploy"'),
            py('exec("x = 1")\ny = "make deploy"'),
            py('print(eval("1"), "make deploy")'),
            py('c = compile("1", "f", "eval")\ny = "make deploy"'),
            py('f = getattr(str, "upper")\ny = "make deploy"'),
            py('g = globals()\ny = "make deploy"'),
            py('s = input()\ny = "make deploy"'),
            py('import string\nf = string.Formatter().get_field("0.os.system", (0,), {})\ny = "make deploy"'),
            py('from . import x\ny = "make deploy"'),
            py('import re._compiler\ny = "make deploy"'),
            py('if __name__ == "__main__":\n    y = "make deploy"'),
            py('x = "make deploy"\nthis is not python ('),
            py('x = "make deploy"\n' + "y = 1\n" * 12000),  # longer than MAX_BODY: raw
            "python3 - <<EOF\nx = '$Y make deploy'\nEOF",
            "python3 - <<EOF\nx = 'a\\\nmake deploy'\nEOF",
            "python3 - <<-'EOF'\n\tx = 'make deploy'\n\tEOF",
            "python3 - <<< 'x = \"make deploy\"'",
            "PYTHONSTARTUP=s.py " + py('x = "make deploy"'),
            "env PYTHONPATH=lib " + py('x = "make deploy"'),
            py('x = "make deploy"', head="python3 -c pass -"),
            py('x = "make deploy"', head="python3 -X dev -"),
            py('x = "make deploy"', head="python3 tool.py"),
            py('x = "make deploy"', head="python3 - arg"),
            py('x = "make deploy"', head="./python3 -"),
            py('x = "make deploy"', tail=" 3<x"),
            py('print("make deploy")', tail=" | sh"),
            py('print("make deploy")', tail=" | tee x.sh") + "\nsh x.sh",
            py('print("make deploy")', tail=" > x.sh") + "\nsh x.sh",
            py('print("make deploy")', tail=" >&3"),
            py('print("make deploy")', tail=" > >(sh)"),
            py('from pathlib import Path\nPath("x.sh").write_text("make deploy")') + "\nsh x.sh",
            py('from pathlib import Path\nPath("bin/run").write_text("make deploy")') + "\nchmod +x bin/run; bin/run",
            py('from pathlib import Path\nPath("a").write_text("make deploy")') + "\nsh *",
            py('from pathlib import Path\nPath("re.py").write_text("x")\ny = "make deploy"'),
            "x=$(" + py('print("make deploy")') + "\n); $x",
            "python3() { sh; }; " + py('x = "make deploy"'),
            "sh -c " + shell.shlex.quote(py('print("make deploy")') + " | sh"),
        ):
            with self.subTest(cmd=cmd):
                self.assertEqual(guard.check(self.project, cmd), ("deny", k))
        hit = guard.check(self.project, py('import os\nos.system("make deploy")'))[1]
        self.assertEqual(hit.where, "script")
        env = ["(^|[\\s/'\"=<])\\.env($|[\\s'\"])"]  # a path rule: one-word literals are still compared
        self.assertTrue(guard.deny(env, py('print(open(".env").read())')))
        self.assertTrue(guard.deny(env, py("x = f\"key: {open('.env').read()} done\"")))
        self.assertIsNone(guard.deny(env, py('x = "do not read .env here"')))
        self.assertEqual(pyscript.blank('a = "x y"  # c\nb = "z"\n'), 'a = ""  \nb = "z"\n')
        self.assertEqual(pyscript.blank("a = f'p {q(\"r\")} s'\n"), "a = \"\"\n\n'r'")
        self.assertIsNone(pyscript.blank('a = """open\n'))
        self.assertIsNone(pyscript.read("import os\n"))
        self.assertEqual(
            pyscript.read('import re\np = "x.sh"\nq = "a b"\n'), ('import re\np = "x.sh"\nq = ""\n', {"x.sh"})
        )
        self.assertIsNotNone(pyscript.gate("x = '" + "a" * (pyscript.MAX_BODY - 10) + "'"))
        self.assertIsNone(pyscript.gate("x = '" + "a" * pyscript.MAX_BODY + "'"))

    def test_text_args(self):
        def is_text_segment(seg):  # a single segment whose arguments are data (data_tokens), as the guard reads it
            try:
                segs, lx = shell.lex(seg)
                segs = [x for x in segs if x.toks]
                return (
                    len(segs) == 1
                    and not lx.redefines
                    and shell.data_tokens([t for t in segs[0].toks if t.kind == "word"]) is not None
                )
            except Exception:  # noqa: BLE001 (the helper answers False on any parse failure)
                return False

        for seg in (
            "foreman note t x",
            "bin/foreman --project foreman note t x",
            "FOREMAN_WHO=a foreman progress x",
            "python3 bin/foreman inbox add x",
            "/opt/foreman/bin/foreman backlog add E1 --title x",
            "foreman integrate r b I --note x",
            "foreman lock gpu run x ls",
            "git commit -m x",
            "git -C wt commit -am x",
            "env foreman guard x",
            "echo x",
            "printf '%s' x",
            "/bin/echo -n x",
            "grep -n x f",
            "fgrep -rl x .",
            "rg -e x",
            "git -C wt grep -c x",
            "gh pr create --body x",
            "gh issue comment 3 -b x",
            "git tag -m x v1",
        ):
            self.assertTrue(is_text_segment(seg), seg)
        for seg in (
            "foreman worktree a b c",
            "foreman --project",
            "git status",
            "foreman integrate r b I --nt x",
            "sudo foreman note t x",
            "PATH=/x foreman note t x",
            "foreman note t x; ls",
            "$F note t x",
            "foreman note t 'x",
            "printf -v x y",
            "printf $f x",
            "./echo x",
            "echo *",
            "rg --pre x y",
            "grep -X x f",
            "grep $o x f",
            "grep x f -e y",
            "git -c a=b grep x",
            "gh pr merge 3 --body x",
            "gh repo create --description x",
        ):
            self.assertFalse(is_text_segment(seg), seg)
        self.assertEqual(
            shell.foreman_call(shell.words("FOREMAN_WHO=a bin/foreman --project p integrate a b")),
            ("p", "integrate", ["a", "b"]),
        )


class GuardNeverRaisesTest(ProjectCase):
    """Huge or malformed commands: the guard never raises, and judges each within its CPU budget."""

    toml = TOML

    def test_never_raises(self):
        big = [
            'foreman note t "' + "x " * 25000 + '"',
            "a;" * 25000,
            "'" * 50000,
            "\\" * 50000,
            "`" * 50000,
            "<<EOF\n" * 1000,
            "$(" * 25000,
            "x | " * 12500,
            '"$(echo "' * 5000,
            "(" * 50000,
            "\x00" * 50000,
        ]
        for cmd in big + [None, 42, b"make deploy", "", "\n\n", "&&", "|"]:
            v, cpu = guard_cpu(self.project, cmd)  # the check's own CPU: a loaded machine does not count
            self.assertTrue(v is None or v[0] in ("deny", "lock"), repr(cmd)[:40])
            self.assertLess(cpu, GUARD_CPU_MAX, repr(cmd)[:40])
        self.assertEqual(guard.check(self.project, None)[0], "deny")
        bad = type("P", (), {"resources": {}, "section": lambda self, n: {"patterns": ["(unclosed"]}})()
        self.assertEqual(guard.check(bad, "ls")[0], "deny")


class GuardDepthTest(ProjectCase):
    """Runs nested to any depth: read to a depth of 4, compared whole beyond, within the CPU budget."""

    toml = TOML

    def test_nested_runs(self):
        """T2: strings, heredocs and pipes that run are commands of their own, to a depth of 4; deeper, or unreadable
        inside, the whole text is compared."""

        def nest(cmd, n):
            for _ in range(n):
                cmd = "sh -c " + shell.shlex.quote(cmd)
            return cmd

        for n in range(1, 8):
            self.assertEqual(guard.check(self.project, nest("kamal app logs", n)), ("deny", "(^|\\s)kamal "), n)
            self.assertIsNone(guard.check(self.project, nest("ls -la", n)), n)
        for cmd in (
            'bash -c "kamal app logs \'x"',
            "eval \"\\$'x' kamal app logs\"",
            "bash <<'EOF'\n$'x' kamal app logs\nEOF",
        ):
            self.assertEqual(guard.check(self.project, cmd), ("deny", "(^|\\s)kamal "), cmd)
        r = shell.runs(list(shell.lex("env -i FOO=1 nice -n 5 timeout -s KILL 60 bash -xc 'a b' n")[0][0].toks))
        self.assertEqual(([(s[1], s[2]) for s in r.strings], r.stdin), ([("a b", True)], "shell"))
        self.assertEqual(shell.runs(shell.lex("python3.12 -")[0][0].toks).stdin, "raw")
        self.assertIsNone(shell.runs(shell.lex("cat -")[0][0].toks).stdin)
        for cmd in (
            "eval " * 10000 + "x",
            "xargs " * 10000 + "x",
            "sh -c " * 10000 + "x",
            "find -exec " * 5000,
            'bash -c "ls";' * 5000,
            "bash <<EOF\n" + "x\n" * 20000 + "EOF",
            "echo a | " * 5000 + "sh",
            "ssh h " * 8000,
            "env -S " * 8000,
            "(" * 3000 + "cat <<EOF" + ")" * 3000 + " | sh\nx\nEOF",
            "{ echo a; } | " * 5000 + "sh",
            "x=$y; $x; " * 5000,
            "if true; then echo a; fi | " * 3000 + "while read l; do $l; done",
            "".join(f"echo a > f{i}; " for i in range(5000)),
            "echo a " + "".join(f"> f{i} " for i in range(10000)),
            "echo a | tee " + " ".join(f"f{i}" for i in range(12000)),
            "echo a | " + "tee f | " * 5000 + "cat",
            "grep " + "-e a " * 8000 + "f",
            "gh pr create " + "--body a " * 8000,
            "echo a > f; " * 3000 + "sh $x",
            "cat > f <<< a; " + "".join(f"$v{i}; " for i in range(5000)),
            "cat > f0 <<< a; " + "".join(f"cp f{i} f{i + 1}; " for i in range(5000)),
            "cat > f <<< a; " + "sh g; " * 5000,
            "".join(f"cat > f{i} <<< a; " for i in range(3000)) + "sh x",
            "python3 - <<'EOF'\n" + 'x = f"a {y} b"\n' * 4300 + "EOF",  # 64 KB read by the python gate
            "python3 - <<'EOF'\n" + 'x = "a b"\n' * 6700 + "EOF",  # 65 KB: raw
            "".join("python3 - <<'EOF'\n" + 'x = "a.sh"\n' * 300 + "EOF\n" for _ in range(80)) + "sh a.sh",
            "python3 - <<'EOF'\n" + 'x = "a b"\n' * 6000 + "EOF\n" + "ls; " * 5000,
        ):
            v, cpu = guard_cpu(self.project, cmd)
            self.assertTrue(v is None or v[0] in ("deny", "lock"), cmd[:30])
            self.assertLess(cpu, GUARD_CPU_MAX, cmd[:30])


class HooksTest(ProjectCase):
    toml = TOML

    def setUp(self):
        super().setUp()
        self.transcript = self.tmp / "sess1" / "agent.jsonl"
        self.transcript.parent.mkdir()
        line = self.cli("launch", "zed7", "--issue", "AB-1", "--no-backlog").stdout.strip().splitlines()[-1]
        self.transcript.write_text(json.dumps({"text": f"{line}\nFollow docs/agent.md. Issue: AB-1 T1."}) + "\n")

    def agent_event(self, name, **kw):
        return {
            "hook_event_name": name,
            "agent_id": "abc123",
            "agent_type": "dev",
            "agent_transcript_path": str(self.transcript),
            **kw,
        }

    def test_agents_hook(self):
        self.hook("agents", self.agent_event("SubagentStart"))
        a = loop.agents(self.project)["abc123"]
        self.assertEqual((a["type"], a["name"], a["transcript"]), ("dev", "zed7", str(self.transcript)))
        self.hook("agents", self.agent_event("PostToolUse", tool_name="Bash"))
        self.assertEqual(loop.agents(self.project)["abc123"]["tool"], "Bash")
        self.assertEqual([h["id"] for h in loop.live_agents(self.project)], ["abc123"])
        self.hook("agents", self.agent_event("SubagentStop", last_assistant_message="done"))
        self.assertEqual(loop.agents(self.project)["abc123"]["final"], "done")
        # outside the project: nothing written anywhere
        self.hook("agents", {**self.agent_event("SubagentStart"), "agent_id": "zzz"}, cwd=self.tmp)
        self.assertNotIn("zzz", loop.agents(self.project))
        # untyped flashes are skipped
        self.hook("agents", {"hook_event_name": "SubagentStart", "agent_id": "flash"})
        self.assertNotIn("flash", loop.agents(self.project))

    def test_stop_agent_and_stalled(self):
        self.hook("agents", self.agent_event("SubagentStart"))
        f = loop.agents_file(self.project)
        d = json.loads(f.read_text())
        d["abc123"]["start"] = d["abc123"]["activity"] = time.strftime(
            "%Y-%m-%dT%H:%M:%S", time.localtime(time.time() - 3600)
        )
        f.write_text(json.dumps(d))
        os.utime(self.transcript, (time.time() - 3600,) * 2)
        self.assertEqual(loop.stalled(self.project), ["zed7 (60 min)"])
        self.assertEqual(loop.stop_agent(self.project, "zed7"), ["abc123"])
        self.assertEqual(loop.live_agents(self.project), [])

    def test_resume_only_in_orchestrator_session(self):
        self.project.board.parent.mkdir(parents=True, exist_ok=True)
        self.project.board.write_text("BOARD: next is AB-2")
        self.cli("session", "S1")
        r = self.hook("resume", {"hook_event_name": "SessionStart", "session_id": "S1", "source": "compact"})
        self.assertIn("BOARD: next is AB-2", r.stdout)
        self.assertIn("orchestrator", r.stdout)
        r = self.hook("resume", {"hook_event_name": "SessionStart", "session_id": "S2", "source": "compact"})
        self.assertEqual(r.stdout, "")
        r = self.hook("resume", {"hook_event_name": "SessionStart", "session_id": "S1", "source": "startup"})
        self.assertEqual(r.stdout, "")

    def who(self, session, source="startup", cwd=None):
        r = self.hook("who", {"hook_event_name": "SessionStart", "session_id": session, "source": source}, cwd=cwd)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)["hookSpecificOutput"]["additionalContext"] if r.stdout.strip() else ""

    def test_who_orchestrates(self):
        # no orchestrator marked: said, nothing invented
        ctx = self.who("S2")
        self.assertIn("no orchestrator now", ctx)
        self.assertIn("foreman lock", ctx)
        self.assertEqual(self.cli("session").returncode, 1)
        # marked with a name: the orchestrator (and its subagents, same session id) gets nothing; another session does
        r = self.cli("session", "S1", "--name", "fake-2a")
        self.assertEqual(r.returncode, 0, r.stderr)
        rec = json.loads((self.project.state / "orchestrator.json").read_text())
        self.assertEqual((rec["session"], rec["name"]), ("S1", "fake-2a"))
        self.assertEqual((self.project.state / "session").read_text().strip(), "S1")
        for source in ("startup", "resume", "compact"):
            self.assertEqual(self.who("S1", source), "")
            ctx = self.who("S2", source)
            self.assertIn("orchestrated by the session fake-2a (since " + rec["since"][11:16] + ")", ctx)
            self.assertIn("SendMessage", ctx)
            self.assertIn("you are not its orchestrator", ctx)
        self.assertEqual(self.who("S2", "clear"), "")
        self.assertIn("orchestrated by fake-2a", self.cli("session").stdout)
        # outside the project: nothing
        self.assertEqual(self.who("S2", cwd=self.tmp), "")
        # marked again without a name: the name it had stays; Claude Code's own files are not read for one
        self.cli("session", "S3", "--name", "fake-3b")
        self.cli("session", "S3")
        self.assertIn("session fake-3b", self.who("S2"))
        # no name given: the short id, without a SendMessage address
        self.cli("session", "S4")
        ctx = self.who("S2")
        self.assertIn("session S4…", ctx)
        self.assertNotIn("write to", ctx)
        # the id written by an older tool (no record): still known, since = the file's time
        (self.project.state / "session").write_text("S5\n")
        self.assertEqual(self.who("S5"), "")
        self.assertIn("session S5…", self.who("S2"))

    def test_loop_pass_and_notices(self):
        b = Backlog(self.project)
        b.init()
        with b.mutate() as d:
            b.add_project(d, "E1", "One", "intro")
            b.add_issue(d, "E1", "AB-1", "orphan", status="in progress")
        self.cli("session", "S1")
        notes.inbox_add(self.project, "please look at AB-1")
        r = self.cli("hook", "mod", "tick", inp=json.dumps({"cwd": str(self.home), "session": "S1"}))
        ctx = json.loads(r.stdout)["submit"]  # the pass the module submits
        self.assertTrue(ctx.startswith("Fake orchestrator loop\n\nLoop pass"), ctx)
        self.assertIn("WIP (pipeline not full): production 0/1", ctx)
        self.assertIn("`in progress` with no live agent or branch: AB-1", ctx)
        self.assertIn("Inbox: 1 unread from Owner", ctx)
        self.assertIn("project check: 2 simulators booted", ctx)
        self.assertIn("loop", notes.heartbeats(self.project))
        # the submitted pass gets nothing more; an ordinary prompt: the in-between notices do not repeat what the
        # pass said (inbox, WIP)
        r = self.hook("loop", {"hook_event_name": "UserPromptSubmit", "session_id": "S1", "prompt": ctx})
        self.assertEqual(r.stdout, "")
        r = self.hook("loop", {"hook_event_name": "UserPromptSubmit", "session_id": "S1", "prompt": "hello"})
        self.assertEqual(r.stdout, "")
        notes.inbox_add(self.project, "and another thing")
        r = self.hook("loop", {"hook_event_name": "UserPromptSubmit", "session_id": "S1", "prompt": "hello"})
        ctx = json.loads(r.stdout)["hookSpecificOutput"]["additionalContext"]
        self.assertIn("Inbox: 1 unread", ctx)
        self.assertNotIn("WIP", ctx)
        # another session: its prompts get nothing, the loop prompt too, and its tick runs no pass
        for prompt in ("hello", "Fake orchestrator loop"):
            r = self.hook("loop", {"hook_event_name": "UserPromptSubmit", "session_id": "S9", "prompt": prompt})
            self.assertEqual(r.stdout, "")
        r = self.cli("hook", "mod", "tick", inp=json.dumps({"cwd": str(self.home), "session": "S9"}))
        self.assertEqual(json.loads(r.stdout), {"marked": False, "grants": False})

    def test_no_handover_command(self):
        """`foreman handover` is gone: an unknown command, nothing written to the notes."""
        self.cli("session", "S1", "--name", "orch-old")
        for args in (("handover",), ("handover", "--note")):
            r = self.cli(*args)
            self.assertEqual(r.returncode, 2)
            self.assertIn("invalid choice: 'handover'", r.stderr)
        self.assertFalse((self.home / "notes").exists() and list((self.home / "notes").glob("*.md")))

    def test_session_says_whom_it_relieves(self):
        """Marking a session over another says whom it relieves and since when; the same session again
        does not, and keeps its name without --name."""
        r = self.cli("session", "S1", "--name", "orch-old")
        self.assertNotIn("relieves", r.stdout)
        since = json.loads((self.project.state / "orchestrator.json").read_text())["since"]
        r = self.cli("session", "S2", "--name", "orch-new")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("orchestrated by orch-new (session S2", r.stdout)
        self.assertIn(f"relieves orch-old (session S1) since {since}", r.stdout)
        r = self.cli("session", "S2")
        self.assertIn("orchestrated by orch-new (session S2", r.stdout)
        self.assertNotIn("relieves", r.stdout)
        self.assertNotIn("relieves", self.cli("session").stdout)
        # the one relieved had no name: its short form, still said
        (self.project.state / "orchestrator.json").unlink()
        self.assertIn("relieves (no name) (session S2)", self.cli("session", "S3", "--name", "orch-3").stdout)


class PauseTest(ProjectCase):
    """While <state>/pause exists (the panel writes it) the loop, the guard and the pause-agents hook respect
    it."""

    toml = TOML

    BLOCKED: ClassVar[list] = [
        "foreman integrate foreman fo1/x 'AB-1 T1'",
        "bin/foreman integrate repo br 'AB-1'",
        "/opt/foreman/bin/foreman worktree app ab1 ab1/x",
        "foreman --project fake integrate app br 'AB-1'",
        "cd /tmp && FOREMAN_WHO=me foreman worktree app ab1 ab1/x",
        'bash -c "foreman integrate app br AB-1"',
        "python3 bin/foreman integrate app br AB-1",
        "foreman time-limit 60 foreman integrate app br AB-1",
        '"${CLAUDE_PLUGIN_ROOT}/bin/foreman" integrate app br AB-1',
        'echo "$(foreman integrate app br AB-1)"',
    ]
    ALLOWED: ClassVar[list] = [
        "foreman backlog show AB-1",
        'foreman note "x" "then foreman integrate app br"',
        "git status",
        "foreman worktree --list",
        "foreman integrate --help",
        "foreman --project other integrate app br AB-1",
        "grep -n 'foreman integrate' docs/orchestrator.md",
        "ls integrate",
        'foreman --project fake note "x" "then foreman integrate app br"',
        'git commit -m "foreman integrate x"',
    ]

    def pause(self, text="paused at 21:07\n"):
        self.project.state.mkdir(parents=True, exist_ok=True)
        (self.project.state / "pause").write_text(text)

    def guard(self, cmd, cwd=None):
        """The guard hook on a Bash call of a main thread (another session: an agent never integrates)."""
        ev = {
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "session_id": "S-main",
            "tool_input": {"command": cmd},
        }
        return self.hook("guard", ev, cwd=cwd)

    def agent(self, kind, session="S1", cwd=None, tool="Agent"):
        ev = {
            "hook_event_name": "PreToolUse",
            "tool_name": tool,
            "session_id": session,
            "tool_input": {"description": "x", "prompt": "y", **({"subagent_type": kind} if kind else {})},
        }
        r = self.hook("pause-agents", ev, cwd=cwd)
        self.assertEqual((r.returncode, r.stderr), (0, ""))
        return json.loads(r.stdout)["hookSpecificOutput"] if r.stdout.strip() else None

    def test_since(self):
        self.assertIsNone(pause.since(self.project))
        self.pause()
        self.assertEqual(pause.since(self.project), "21:07")
        self.pause("")  # an empty file still pauses: the time comes from the file
        self.assertRegex(pause.since(self.project), r"^\d\d:\d\d$")

    def test_guard(self):
        for cmd in self.BLOCKED + self.ALLOWED:
            self.assertEqual(self.guard(cmd).returncode, 0, cmd)
        self.pause()
        for cmd in self.BLOCKED:
            r = self.guard(cmd)
            self.assertEqual(r.returncode, 2, cmd)
            self.assertIn("paused from the panel (since 21:07)", r.stderr)
            self.assertIn("Owner", r.stderr)
        for cmd in self.ALLOWED:
            self.assertEqual(self.guard(cmd).returncode, 0, cmd)
        # the deny list still answers first; outside the project nothing is checked
        self.assertIn("never done without", self.guard("make deploy && foreman integrate a b c").stderr)
        r = self.guard(self.BLOCKED[0], cwd=self.tmp)
        self.assertEqual((r.returncode, r.stderr), (0, ""))
        # `foreman guard` says the same; the guard table of `foreman check` does not depend on the pause
        r = self.cli("guard", "--as", "orchestrator", "foreman integrate app br AB-1")
        self.assertEqual(r.returncode, 2)
        self.assertIn("paused", r.stdout)
        self.assertIsNone(guard.check(self.project, "foreman integrate app br AB-1", {"session": "S-main"}))

    def test_guard_other_project(self):
        other = self.tmp / "other"
        other.mkdir()
        (other / "foreman.toml").write_text('[project]\nname = "other"\nowner = "Ann"\n')
        with open(self.tmp / "projects.toml", "a") as f:
            f.write(f'[[project]]\nname = "other"\nhome = "{other}"\nmode = "active"\n')
        from foreman import config

        o = config.load_project(other)
        o.state.mkdir(parents=True, exist_ok=True)
        (o.state / "pause").write_text("paused at 08:00\n")
        r = self.guard("foreman --project other integrate app br AB-1")
        self.assertEqual(r.returncode, 2)
        self.assertIn("other is paused", r.stderr)
        self.assertEqual(self.guard("foreman integrate app br AB-1").returncode, 0)

    def test_loop(self):
        b = Backlog(self.project)
        b.init()
        self.cli("session", "S1")

        def tick():  # a due pass, as the module's timer asks for it
            (self.project.state / "heartbeats.json").unlink(missing_ok=True)
            r = self.cli("hook", "mod", "tick", inp=json.dumps({"cwd": str(self.home), "session": "S1"}))
            return json.loads(r.stdout)["submit"]

        ctx = tick()
        self.assertIn("WIP (pipeline not full)", ctx)
        self.assertNotIn("PAUSED", ctx)
        self.pause()
        ctx = tick()
        self.assertTrue(ctx.split("\n")[3].startswith(f"- {notices.INFO}PAUSED from the panel (since 21:07)"), ctx)
        self.assertEqual(next(n["kind"] for n in loop.notices_for_pass(self.project, mark=False)), "paused")
        self.assertNotIn("WIP", ctx)
        self.assertIn("project check", ctx)  # the rest of the pass is still there
        (self.project.state / "notices-last").unlink(missing_ok=True)
        r = self.hook("loop", {"hook_event_name": "UserPromptSubmit", "session_id": "S1", "prompt": "hello"})
        self.assertNotIn("WIP", r.stdout)
        # Spanish
        self.write_toml(TOML.replace('owner = "Owner"', 'owner = "Owner"\nlanguage = "es"'))
        self.assertTrue(
            loop.notices_for_pass(self.project)[0]["text"].startswith("PAUSADO desde el panel (desde las 21:07)")
        )

    def test_pause_agents(self):
        self.cli("session", "S1")
        self.assertIsNone(self.agent("dev"))  # not paused
        self.pause()
        for kind in ("dev", "foreman:dev"):
            out = self.agent(kind)
            self.assertEqual((out["hookEventName"], out["permissionDecision"]), ("PreToolUse", "deny"), kind)
            self.assertIn("fake is paused from the panel (since 21:07)", out["permissionDecisionReason"])
        self.assertEqual(self.agent("dev", tool="Task")["permissionDecision"], "deny")
        for kind in ("Explore", "foreman:planner", "general-purpose", None):
            self.assertIsNone(self.agent(kind), kind)
        self.assertIsNone(self.agent("dev", session="S2"))  # another session
        self.assertIsNone(self.agent("dev", cwd=self.tmp))  # outside the project
        # [agents] production_types counts too, with or without the plugin prefix
        self.write_toml(TOML.replace("production_slots = 1", 'production_slots = 1\nproduction_types = ["builder"]'))
        self.assertEqual(self.agent("builder")["permissionDecision"], "deny")
        self.assertEqual(self.agent("myplugin:builder")["permissionDecision"], "deny")
        # garbage never fails the call
        r = self.cli("hook", "pause-agents", inp="not json")
        self.assertEqual((r.returncode, r.stdout, r.stderr), (0, "", ""))
        r = self.hook("pause-agents", {"tool_name": "Agent", "session_id": "S1", "tool_input": "oops"})
        self.assertEqual((r.returncode, r.stdout), (0, ""))

    def test_hooks_json(self):
        from helpers import ROOT

        pre = json.loads((ROOT / "hooks/classic.json").read_text())["hooks"]["PreToolUse"]
        m = [h for h in pre if "pause-agents" in h["hooks"][0]["command"]]
        self.assertEqual([h["matcher"] for h in m], ["Agent|Task"])
        # the guard sees every tool: one entry with no matcher (a matcher would leave some tool unseen)
        g = [h for h in pre if h["hooks"][0]["command"].endswith(" hook guard")]
        self.assertEqual(len(g), 1)
        self.assertNotIn("matcher", g[0])
        # the classic hooks live apart from the module (an old Claude Code reading `modules` cannot drop them)
        self.assertEqual(json.loads((ROOT / "hooks/hooks.json").read_text()), {"modules": ["./mod/foreman.ts"]})
        self.assertEqual(json.loads((ROOT / ".claude-plugin/plugin.json").read_text())["hooks"], "./hooks/classic.json")


if __name__ == "__main__":
    unittest.main()
