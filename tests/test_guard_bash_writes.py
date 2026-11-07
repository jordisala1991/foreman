"""The guard on Bash commands that write a reserved file (guard._bash_file, shell.writes): the programs and
redirections it reads, the wrappers and nested commands it sees through, how a target is resolved (cwd, cd, ~,
$HOME, the line's variables, copies into a folder, trees), what a target it cannot know does, who passes (the
orchestrator its board only), its failure (O7), the hook's refusal and the table's Bash rows."""

import json
import os
import unittest
from datetime import datetime
from unittest import mock

from test_guard_files import TABLE, FileCase

from foreman import approved, guard, permissions, shell


def W(cmd):
    """[(raw, names, tree)] of shell.writes() on a line."""
    segs, _ = shell.lex(cmd)
    segs = shell.pipelines(segs)
    info = [shell.runs([t for t in s.toks if t.kind == "word"]) for s in segs]
    return [(w.tok.raw, w.names, w.tree) for w in shell.writes(segs, info)]


class WritesTest(unittest.TestCase):
    def test_what_each_program_writes(self):
        cases = {
            "echo x > a 2>&1 >/dev/null 2>/dev/stderr": [("a", None, False)],
            "cat x >> a; cat <> b; echo &> c; echo >| d": [("a", None, False), ("b", None, False),
                                                           ("c", None, False), ("d", None, False)],
            "exec 3> f; echo >&3": [("f", None, False)],
            "tee -a x y": [("x", None, False), ("y", None, False)],
            "cp cand.toml foreman.toml": [("foreman.toml", ("cand.toml",), False)],
            "cp -R a/ b": [("b", ("",), True)],
            "cp -t DIR a b": [("DIR", ("a", "b"), False)],
            "cp --target-directory=DIR a": [("DIR", ("a",), False)],
            "mv x y": [("y", ("x",), False), ("x", None, True)],
            "ln -sf a b": [("b", ("a",), False)],
            "ln -s a": [],
            "install -m 644 a b": [("b", ("a",), False)],
            "install -d x y": [("x", None, False), ("y", None, False)],
            "rsync -av a/ b": [("b", ("",), True)],
            "rsync -a a host:b": [],
            "scp host:a b": [("b", ("host:a",), False)],
            "ditto a b": [("b", ("a",), True)],
            "sed -i '' s/a/b/ f": [("f", None, False)],
            "sed -i.bak -e s/a/b/ f g": [("f", None, False), ("g", None, False)],
            "sed -I .bak s/a/b/ f": [("f", None, False)],
            "gsed --in-place s/a/b/ f": [("f", None, False)],
            "sed -n p f": [],
            "perl -pi -e s/a/b/ f": [("f", None, False)],
            "perl -i.orig script.pl f": [("f", None, False)],
            "perl -Mstrict -ne print f": [],
            "rm -rf a b": [("a", None, True), ("b", None, True)],
            "rm a; unlink b; rmdir c; shred d": [("a", None, False), ("b", None, False), ("c", None, False),
                                                 ("d", None, False)],
            "truncate -s 0 f": [("f", None, False)],
            "touch -t 202001010000 f": [("f", None, False)],
            "chmod 644 f": [("f", None, False)],
            "chmod -w f": [("f", None, False)],
            "chmod -R u+w d": [("d", None, True)],
            "chmod --reference=r f": [("f", None, False)],
            "chown me f; chgrp g f2": [("f", None, False), ("f2", None, False)],
            "dd if=a of=b bs=1": [("b", None, False)],
            "curl -o f https://x; wget -O g https://y; curl --output=h u": [("f", None, False), ("g", None, False),
                                                                           ("h", None, False)],
            "find d -name x -delete": [("d", None, False)],
            "find d e -exec rm {} ;": [("d", None, False), ("e", None, False)],
            "find d -exec cat {} ;": [],
            "sudo timeout 5 env A=1 cp a b": [("b", ("a",), False)],
            "foreman lock gpu run 'x' cp a b": [("b", ("a",), False)],
            "cat foreman.toml; grep x foreman.toml; diff a b; git status": [],
        }  # fmt: skip
        for cmd, want in cases.items():
            with self.subTest(cmd=cmd):
                self.assertEqual(W(cmd), want)

    def test_args(self):
        ops, flags, vals = shell._args(shell.lex("x -ab --c=1 --size 2 -t D -- -e f")[0][0].toks, 1, "t")
        self.assertEqual([t.value for t in ops], ["-e", "f"])
        self.assertTrue({"a", "b", "--c", "--size", "t"} <= flags)
        self.assertEqual(
            {k: [t.value for t in v] for k, v in vals.items()}, {"--c": ["1"], "--size": ["2"], "t": ["D"]}
        )
        self.assertIn("Write(", repr(shell.Write(0, ops[0])))


class BashWriteCase(FileCase):
    def bash(self, cmd, role="agent", cwd=None):
        """("allow" | "deny", class) of a Bash command in a role (a table run's ctx)."""
        v = guard.check(self.project, cmd, guard.as_role(role, str(cwd or self.home)))
        if v is None:
            return "allow", ""
        if v[0] == "file":
            return "deny", v[1].kind
        return v[0], ""


class BashWritesTest(BashWriteCase):
    def test_the_plan_rows(self):
        st = self.state / "fake"
        for cmd, kind in (
            ("echo x > ~/.claude/settings.json", "claude"),
            ("cp cand.toml foreman.toml", "config"),
            (f"sed -i '' s/a/b/ {TABLE}", "table"),
            (f"tee -a {st}/grants.json", "state"),
            (f"mv x {self.tmp}/projects.toml", "registry"),
        ):
            for role in ("agent", "orchestrator"):
                with self.subTest(cmd=cmd, role=role):
                    self.assertEqual(self.bash(cmd, role), ("deny", kind))
        for cmd in (
            "cp foreman.toml $TMPDIR/cand.toml",
            "cp foreman.toml /tmp/cand.toml",
            f"cat foreman.toml {TABLE}",
            f"grep deny {TABLE}",
            f"diff foreman.toml {self.tmp}/cand.toml",
            "cat ~/.claude/settings.json",
            f"cp {self.tmp}/projects.toml /tmp/x",
        ):
            with self.subTest(cmd=cmd):
                self.assertEqual(self.bash(cmd), ("allow", ""))

    def test_every_writer_on_a_reserved_file(self):
        cfg = self.home / "foreman.toml"
        for cmd in (
            f"echo x >> {cfg}",
            f"cat <<'EOF' > {cfg}\nx\nEOF",
            f"cp a {cfg}",
            f"cp -t {self.home} foreman.toml",
            f"mv {cfg} /tmp/x",
            f"ln -sf /tmp/x {cfg}",
            f"install a {cfg}",
            f"rsync a {cfg}",
            f"sed -i s/a/b/ {cfg}",
            f"gsed -i -e s/a/b/ {cfg}",
            f"perl -pi -e s/a/b/ {cfg}",
            f"rm {cfg}",
            f"unlink {cfg}",
            f"truncate -s 0 {cfg}",
            f"touch {cfg}",
            f"chmod 600 {cfg}",
            f"dd if=/dev/zero of={cfg}",
            f"curl -o {cfg} https://example.com/x",
        ):
            with self.subTest(cmd=cmd):
                self.assertEqual(self.bash(cmd), ("deny", "config"))

    def test_relative_cd_tilde_home_and_variables(self):
        self.assertEqual(self.bash("echo x > foreman.toml"), ("deny", "config"))
        self.assertEqual(self.bash("echo x > foreman.toml", cwd=self.tmp), ("allow", ""))
        self.assertEqual(self.bash(f"cd {self.home} && echo x > foreman.toml", cwd=self.tmp), ("deny", "config"))
        self.assertEqual(self.bash(f"pushd {self.home}; cp a foreman.toml", cwd=self.tmp), ("deny", "config"))
        self.assertEqual(self.bash("cd tests && sed -i s/a/b/ guard.tsv"), ("deny", "table"))
        self.assertEqual(self.bash("echo x > ../home/./foreman.toml"), ("deny", "config"))
        # a folder the line creates, and one a variable of the line names, are followed too
        self.assertEqual(self.bash(f"mkdir -p {self.tmp}/new && cd {self.tmp}/new && cp a foreman.toml"), ("allow", ""))
        self.assertEqual(self.bash(f"D={self.tmp}/new; mkdir -p $D && cd $D && chmod -R go-rwx ."), ("allow", ""))
        self.assertEqual(self.bash(f"D={self.home}; cd $D && cp a foreman.toml", cwd=self.tmp), ("deny", "config"))
        for cmd in (
            "echo x > $HOME/.claude/settings.json",
            'echo x > "${HOME}/.claude/settings.json"',
            "f=~/.claude/settings.json; echo x > $f",
            'd="$HOME/.claude"; export f=$d/settings.json; cp a "$f"',
            "cp a ~/.claude/",
        ):
            with self.subTest(cmd=cmd):
                self.assertEqual(self.bash(cmd), ("deny", "claude"))
        self.assertEqual(self.bash("echo x > '~/.claude/x.json'", cwd=self.tmp), ("allow", ""))  # a quoted ~ is a name
        self.assertEqual(self.bash("echo x > '~/.claude/settings.json'", cwd=self.tmp), ("deny", "claude"))  # .claude/

    def test_a_target_it_cannot_know(self):
        st = self.state / "fake"
        for cmd, kind in (
            ("echo x > $X/.claude/settings.json", "claude"),
            (f"echo x > ${{D:-{st}}}/grants.json", "state"),
            (f"rm {st}/*.json", "state"),
            ("cd $SOMEWHERE && echo x > .claude/settings.local.json", "claude"),
        ):
            with self.subTest(cmd=cmd):
                self.assertEqual(self.bash(cmd), ("deny", kind))
        for cmd in (
            "echo x > $X/foreman.toml",
            "cp a $(mktemp)",
            f"echo x > {self.user}/.claude/projects/$P/memory/x.md",
        ):
            with self.subTest(cmd=cmd):
                self.assertEqual(self.bash(cmd), ("allow", ""))

    def test_copies_into_a_folder_and_trees(self):
        # a cd it cannot follow adds a folder it cannot name, as for a push: the event's folder stays one of them
        self.assertEqual(self.bash("cd $SOMEWHERE && echo x > foreman.toml"), ("deny", "config"))
        self.assertEqual(self.bash("cd $SOMEWHERE && echo x > foreman.toml", cwd=self.tmp), ("allow", ""))
        self.assertEqual(self.bash(f"cp {self.tmp}/x/foreman.toml {self.home}/"), ("deny", "config"))
        self.assertEqual(self.bash(f"cp {self.tmp}/x/cand.toml {self.home}/"), ("allow", ""))
        self.assertEqual(self.bash(f"rsync -a {self.tmp}/new/ {self.home}"), ("deny", "config"))
        self.assertEqual(self.bash(f"rsync -a {self.tmp}/new {self.home}"), ("allow", ""))
        self.assertEqual(self.bash(f"rm -rf {self.home}"), ("deny", "config"))
        self.assertEqual(self.bash(f"rm -rf {self.home}/reviews"), ("allow", ""))
        self.assertEqual(self.bash(f"mv {self.home} {self.tmp}/old"), ("deny", "config"))
        self.assertEqual(self.bash(f"chmod -R a-w {self.user}"), ("deny", "claude"))
        repo = self.tmp / "repo/.claude"
        repo.mkdir(parents=True)
        (repo / "settings.local.json").write_text("{}")
        self.assertEqual(self.bash(f"rm -rf {repo}"), ("deny", "claude"))
        self.assertEqual(self.bash(f"rm -rf {repo}/worktrees/x"), ("allow", ""))
        self.assertEqual(self.bash(f"find {self.state} -name '*.lock' -delete"), ("deny", "state"))
        self.assertEqual(self.bash("find . -name '*.pyc' -delete"), ("allow", ""))  # the home holds a reserved file
        self.assertEqual(self.bash(f"rm -rf {self.user}/.claude/plans/x {self.user}/.claude/projects"), ("allow", ""))

    def test_symbolic_and_hard_links(self):
        link = self.tmp / "link.toml"
        link.symlink_to(self.home / "foreman.toml")
        self.assertEqual(self.bash(f"echo x > {link}"), ("deny", "config"))
        hard = self.tmp / "hard.toml"
        os.link(self.home / "foreman.toml", hard)
        self.assertEqual(self.bash(f"sed -i s/a/b/ {hard}"), ("deny", "config"))
        dangling = self.tmp / "away"
        os.symlink("/nonexistent/x", self.home / "tests/away")
        self.assertEqual(self.bash(f"rm {self.home}/tests/away"), ("allow", ""))
        (self.tmp / "out").symlink_to(dangling)
        self.assertEqual(self.bash(f"ln -sf {self.tmp}/z {self.home}/foreman.toml"), ("deny", "config"))

    def test_nested_commands(self):
        cfg = self.home / "foreman.toml"
        for cmd in (
            f"sh -c 'echo x > {cfg}'",
            f'bash -lc "cd {self.home} && cp a foreman.toml"',
            f"eval 'rm {cfg}'",
            f"echo $(cp a {cfg})",
            f"bash <<'EOF'\ncp a {cfg}\nEOF",
            f"echo 'cp a {cfg}' | sh",
            f"ls | xargs -I{{}} cp {{}} {cfg}",
            f"find . -name x -exec cp {{}} {cfg} ';'",
            f"cat <<EOF\n$(rm {cfg})\nEOF",
            f"timeout 10 sudo -u me sh -c 'tee {cfg} < /dev/null'",
        ):
            with self.subTest(cmd=cmd):
                self.assertEqual(self.bash(cmd), ("deny", "config"))
        for cmd in (
            f"ssh host 'rm {cfg}'",  # elsewhere
            f"cat <<'EOF' > {self.tmp}/notes.md\nrm {cfg}\nEOF",  # a quoted heredoc to cat is text
            f'git commit -m "never cp a {cfg}"',
            f'foreman note t "echo x > {cfg}"',
            f"echo 'cp a {cfg}'",
        ):
            with self.subTest(cmd=cmd):
                self.assertEqual(self.bash(cmd), ("allow", ""))

    def test_a_line_the_lexer_cannot_read(self):
        cfg = self.home / "foreman.toml"
        self.assertEqual(self.bash(f"echo $'a' > {cfg}"), ("deny", "config"))
        self.assertEqual(self.bash(f"sh -c \"echo $'a' > {cfg}\""), ("deny", "config"))
        self.assertEqual(self.bash(f"echo $'a' && cat {self.tmp}/x"), ("allow", ""))
        self.assertEqual(self.bash("echo $'a' > /tmp/x"), ("allow", ""))
        # `~` is the home in such a text too, as $HOME is (guard._spelled, read the same by every rule)
        for cmd in ("echo $'a' > ~/.claude.json", "echo $'a' > $HOME/.claude.json", "x=~/.claude.json; echo $'a' > $x"):
            with self.subTest(cmd=cmd):
                self.assertEqual(self.bash(cmd), ("deny", "claude"))
        self.assertEqual(self.bash("echo $'a' > ~/notes.md"), ("allow", ""))

    def test_the_board_only_for_the_orchestrator(self):
        board = self.project.board
        cmd = f"cat > {board} <<'EOF'\n# board\nEOF"
        self.assertEqual(self.bash(cmd, "orchestrator"), ("allow", ""))
        self.assertEqual(self.bash(cmd, "agent"), ("deny", "state"))
        self.assertEqual(
            self.bash(f"echo x >> {board}; echo y > {self.state}/fake/x", "orchestrator"), ("deny", "state")
        )
        self.mark("S1")
        ctx = {"session": "S1", "agent": None, "cwd": str(self.home)}
        self.assertIsNone(guard.check(self.project, cmd, ctx))
        for c, why in (({**ctx, "agent": "a1"}, "agent"), ({**ctx, "session": "S2"}, "session"), (None, "agent")):
            v = guard.check(self.project, cmd, c)
            self.assertEqual((v[0], v[1].why, v[1].bash), ("file", why, True))

    def test_before_the_deny_lists_and_after_the_builtins(self):
        self.write_toml(self.toml + f'\n[check]\nguard_table = "{TABLE}"\n\n[deny]\npatterns = ["^rm "]\n')
        v = guard.check(self.project, f"rm {self.home}/foreman.toml", guard.as_role("agent", str(self.home)))
        self.assertEqual(v[0], "file")
        v = guard.check(self.project, f"foreman hook mod x > {self.home}/foreman.toml")
        self.assertEqual(v[0], "deny")
        self.assertEqual(guard.check(self.project, "rm /tmp/x")[0], "deny")

    def test_a_failure_denies_only_what_looks_reserved(self):
        with mock.patch.object(guard, "reserved", side_effect=RuntimeError("boom")):
            self.assertEqual(self.bash("echo x > ~/.claude/settings.json"), ("deny", "claude"))
            self.assertEqual(self.bash(f"cp a {self.state}/x"), ("deny", "state"))
            self.assertEqual(self.bash("echo x > /tmp/y"), ("allow", ""))
            self.assertEqual(self.bash("cat ~/.claude/settings.json"), ("allow", ""))
        with (
            mock.patch.object(guard, "_machine", side_effect=RuntimeError("boom")),
            mock.patch.object(guard, "reserved", side_effect=RuntimeError("boom")),
        ):
            self.assertEqual(self.bash("echo x > ~/.claude/settings.json"), ("allow", ""))

    def test_the_message(self):
        v = guard.check(self.project, "cp cand.toml foreman.toml", guard.as_role("agent", str(self.home)))
        text = guard.message(self.project, v)
        self.assertIn("this command writes `foreman.toml`", text)
        self.assertIn("cp cand.toml foreman.toml", text)
        self.assertIn("permissions --candidate", text)
        v = guard.check(self.project, "echo x > ~/.claude/settings.json", guard.as_role("orchestrator", str(self.home)))
        self.assertIn("Claude Code's own settings", guard.message(self.project, v))


class BashWriteHookTest(BashWriteCase):
    def event(self, cmd, agent="a1"):
        return {
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "session_id": "S1",
            **({"agent_id": agent} if agent else {}),
            "tool_use_id": "toolu_1",
            "cwd": str(self.home),
            "tool_input": {"command": cmd},
        }

    def test_hook_refuses_and_tells_the_orchestrator(self):
        self.mark("S1")
        r = self.hook("guard", self.event("cp x foreman.toml"))
        self.assertEqual(r.returncode, 2)
        self.assertIn("this command writes `foreman.toml`", r.stderr)
        line = json.loads(approved.log_file(self.project).read_text().splitlines()[-1])
        self.assertEqual((line["pattern"], line["file"], line["why"]), ("file:config", "foreman.toml", "agent"))
        log = [json.loads(x) for x in permissions.log_file(self.project).read_text().splitlines()]
        self.assertEqual((log[-1]["kind"], log[-1]["rule"], log[-1]["file"]), ("file", "file:config", "foreman.toml"))
        refused = {(c["band"], c["rule"]): c["n"] for c in permissions.counts(self.project, datetime(2000, 1, 1))}
        self.assertEqual(refused[("file", "file:config")], 1)  # the reserved-files band counts it as a Write's
        r = self.hook("guard", self.event("cp x foreman.toml", agent=None))  # the orchestrator: no notice
        self.assertEqual(r.returncode, 2)
        self.assertIn("--candidate FILE", r.stderr)
        self.assertEqual(len(approved.log_file(self.project).read_text().splitlines()), 1)
        r = self.hook("guard", self.event("cp x /tmp/y"))
        self.assertEqual((r.returncode, r.stderr), (0, ""))


if __name__ == "__main__":
    unittest.main()
