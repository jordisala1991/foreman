"""The built-in rules for an agent only (guard._agent_only): its git that rewrites a main checkout, a live home's or
a [repos.*] path's (RepoCheckoutTest): which subcommands, which paths reach a live file, how the checkout and the
folder of each git are found (-C, cd, --work-tree, nested commands, aliases, an unknown folder); its `foreman
integrate` (but --help, -h and --check), who passes (the orchestrator, another session), the refusal, the permissions
view, a failure of the rule and the table's rows."""

import os
import subprocess
import unittest
from unittest import mock

from test_guard_files import FileCase

from foreman import guard, permissions


class MainCheckoutCase(FileCase):
    """FileCase plus a third product, «ours», whose home is ops/ inside a git repo (the main checkout, `top`), with
    a live .claude/settings.json, a worktree of it outside (`wt`) and one nested in .claude/worktrees (`nested`)."""

    def setUp(self):
        super().setUp()
        self.top = self.tmp / "repo"
        self.ops = self.top / "ops"
        (self.ops / "tests").mkdir(parents=True)
        (self.ops / "foreman.toml").write_text(
            '[project]\nname = "ours"\nowner = "Owner"\n\n[check]\nguard_table = "tests/guard.tsv"\n'
        )
        (self.ops / "tests/guard.tsv").write_text("allow\tls\n")
        (self.top / "README.md").write_text("x\n")
        (self.top / ".claude").mkdir()
        (self.top / ".claude/settings.json").write_text("{}\n")
        subprocess.run(["git", "init", "-q", str(self.top)], check=True)
        with open(self.tmp / "projects.toml", "a") as f:
            f.write(f'\n[[project]]\nname = "ours"\nhome = "{self.ops}"\nmode = "active"\n')
        self.wt = self.tmp / "wt/ours-a1"
        self.nested = self.top / ".claude/worktrees/x"
        for w in (self.wt, self.nested):
            w.mkdir(parents=True)
            (w / ".git").write_text(f"gitdir: {self.top}/.git/worktrees/{w.name}\n")
            (w / "ops").mkdir()

    def v(self, cmd, cwd=None, role="agent"):
        """ "orchestrator" | "allow" | the verdict's kind, for a Bash command in a role and folder (default: top)."""
        got = guard.check(self.project, cmd, guard.as_role(role, str(cwd or self.top)))
        if got is None:
            return "allow"
        if got[0] == "orchestrator" and got[1] in guard.AGENT_ONLY:
            return "orchestrator"
        return f"{got[0]}:{got[1]}"


REFUSED = (
    "git merge main",
    "git merge --abort",
    "git pull",
    "git rebase main",
    "git cherry-pick abc",
    "git revert HEAD",
    "git am p.mbox",
    "git switch x",
    "git checkout x",
    "git checkout -b x",
    "git checkout -qb t1",
    "git checkout main --",
    "git checkout -f",
    "git reset --hard",
    "git reset -q --keep HEAD~1",
    "git stash",
    "git stash -u",
    "git stash pop",
    "git stash apply",
    "git stash save wip",
    "git apply p.diff",
    "git clean -fd",
    "git bisect start",
    "git read-tree -u -m HEAD",
    "git checkout-index -a",
    "git sparse-checkout set x",
    "git submodule update",
    "git filter-branch x",
    "git checkout -- ops/foreman.toml",
    "git checkout main ops/foreman.toml",
    "git restore ops/tests/guard.tsv",
    "git restore .",
    "git restore --staged --worktree ops/foreman.toml",
    "git rm ops/foreman.toml",
    "git rm -r ops",
    "git mv ops/foreman.toml x",
    "git mv x ops/foreman.toml",
    "git stash push -- ops/",
    "git checkout -- .claude/settings.json",
    "git restore .claude",
    "git checkout -- '*.toml'",
    "git checkout -- :/",
    'git restore "$F"',
    "for f in a b; do git mv ops/$f old/$f; done",
    "git checkout -- 'ops/*.toml'",
    "git $C",
    "git -c x=y merge main",
)
PASSES = (
    "git log --oneline -5",
    "git diff",
    "git show HEAD",
    "git status",
    "git stash list",
    "git stash show",
    "git reset --soft HEAD~1",
    "git reset",
    "git reset HEAD ops/foreman.toml",
    "git restore --staged ops/foreman.toml",
    "git checkout -- README.md",
    "git checkout README.md",
    "git checkout",
    "git rm --cached ops/foreman.toml",
    "git rm README.md",
    "git mv README.md b.md",
    "for f in a b; do git mv docs/$f old/$f; done",
    "git checkout -- 'docs/*.md'",
    "git stash push -- README.md",
    "git commit -m 'git merge main'",
    "git worktree add ../wt/x -b x",
    "git apply --check p.diff",
    "git apply --stat p.diff",
    "git bisect log",
    "git sparse-checkout list",
    "git submodule status",
    "git read-tree HEAD",
    "git branch -f x",
    "git fetch",
    "git grep merge",
    "git merge-base a b",
    "git",
    "git nosuch",
    "echo git merge main",
    "ls",
)


class SubcommandsTest(MainCheckoutCase):
    def test_refused_in_the_main_checkout(self):
        for cmd in REFUSED:
            for form, cwd in ((cmd, self.top), (cmd.replace("git", f"git -C {self.top}", 1), self.tmp)):
                with self.subTest(cmd=form):
                    self.assertEqual(self.v(form, cwd), "orchestrator")

    def test_what_passes_in_the_main_checkout(self):
        for cmd in PASSES:
            with self.subTest(cmd=cmd):
                self.assertEqual(self.v(cmd), "allow")

    def test_worktrees_and_other_folders_pass(self):
        for cmd in REFUSED:
            for cwd in (self.wt, self.nested, self.tmp, self.home):
                with self.subTest(cmd=cmd, cwd=str(cwd)):
                    self.assertEqual(self.v(cmd, cwd), "allow")

    def test_from_a_subfolder_and_relative_paths(self):
        self.assertEqual(self.v("git checkout -- foreman.toml", self.ops), "orchestrator")
        self.assertEqual(self.v("git checkout -- tests", self.ops), "orchestrator")
        self.assertEqual(self.v("git checkout -- ../README.md", self.ops), "allow")
        self.assertEqual(self.v("git merge main", self.ops), "orchestrator")
        self.assertEqual(self.v("git -C ops checkout -- foreman.toml"), "orchestrator")

    def test_the_local_home_form(self):
        """A home that is itself the main checkout (a home at the top of its repo): its foreman.toml at the top."""
        subprocess.run(["git", "init", "-q", str(self.home)], check=True)
        self.assertEqual(self.v("git stash", self.home), "orchestrator")
        self.assertEqual(self.v("git checkout -- foreman.toml", self.home), "orchestrator")
        self.assertEqual(self.v("git checkout -- tests/guard.tsv", self.home), "orchestrator")
        self.assertEqual(self.v("git stash push -- notes.md", self.home), "allow")


class FoldersTest(MainCheckoutCase):
    def test_cd_and_lists(self):
        top, wt = self.top, self.wt
        cases = {
            f"cd {wt} && git merge main": "allow",
            f"cd {top} && git merge main": "orchestrator",
            'cd "$X" && git merge main': "orchestrator",  # an empty "$X" is the current folder
            'cd "$X"; git merge main': "orchestrator",  # it may run in the main checkout
            'git -C "$X" merge main': "orchestrator",  # a folder the line cannot name keeps the one before
            f'X={top}; git -C "$X" merge main': "orchestrator",  # ... unless the line names the main checkout
            "git -C ~/nowhere merge main": "allow",
            f"(cd {wt}; git merge main)": "orchestrator",  # in a subshell the cd may not have run
            f"cd {wt}; git merge main; cd {top}; git log": "allow",
            f"cd {wt} || git merge main": "orchestrator",
        }
        for cmd, want in cases.items():
            with self.subTest(cmd=cmd):
                self.assertEqual(self.v(cmd, top), want)

    def test_home_named_with_a_tilde(self):
        with mock.patch.dict(os.environ, {"HOME": str(self.tmp)}):
            self.assertEqual(self.v("git -C ~/repo merge main", self.wt), "orchestrator")
            self.assertEqual(self.v('X=1; git -C "$X" merge main; ls ~/repo', self.wt), "orchestrator")
            self.assertEqual(self.v("git -C ~/wt/ours-a1 merge main", self.top), "allow")

    def test_work_tree_and_git_dir(self):
        top, wt = self.top, self.wt
        self.assertEqual(self.v(f"GIT_WORK_TREE={top} git --git-dir={top}/.git checkout x", wt), "orchestrator")
        self.assertEqual(self.v(f"git --work-tree={top} checkout x", wt), "orchestrator")
        self.assertEqual(self.v(f"git --work-tree {top} reset --hard", wt), "orchestrator")
        self.assertEqual(self.v(f"git --git-dir={top}/.git log", wt), "allow")

    def test_nested_commands(self):
        top = self.top
        for cmd in (
            f"sh -c 'cd {top} && git reset --hard'",
            f"echo $(git -C {top} merge x)",
            f'bash -c "git -C {top} stash"',
            f"echo git -C {top} merge x | sh",
            f"foreman lock gpu run 'x' git -C {top} merge main",
            f"env A=1 git -C {top} pull",
        ):
            with self.subTest(cmd=cmd):
                self.assertEqual(self.v(cmd, self.wt), "orchestrator")
        self.assertEqual(self.v(f"ssh host 'git -C {top} merge x'", self.wt), "allow")  # runs elsewhere

    def test_aliases(self):
        top = self.top
        subprocess.run(["git", "-C", str(top), "config", "alias.co", "checkout"], check=True)
        subprocess.run(["git", "-C", str(top), "config", "alias.up", "!git merge --ff-only"], check=True)
        subprocess.run(["git", "-C", str(top), "config", "alias.lg", "log --oneline"], check=True)
        self.assertEqual(self.v("git co x"), "orchestrator")
        self.assertEqual(self.v("git co -- README.md"), "allow")
        self.assertEqual(self.v("git up main"), "orchestrator")
        self.assertEqual(self.v("git lg"), "allow")
        self.assertEqual(self.v("git -c alias.m=merge m main"), "orchestrator")

    def test_a_line_the_lexer_cannot_read(self):
        self.assertEqual(self.v(f"git -C {self.top} merge 'x"), "orchestrator")
        self.assertEqual(self.v("git merge 'x"), "orchestrator")  # its folder is the main checkout
        self.assertEqual(self.v("git log 'x"), "allow")
        self.assertEqual(self.v("git merge 'x", self.wt), "allow")


class WhoTest(MainCheckoutCase):
    def test_orchestrator_and_other_sessions_pass(self):
        for cmd in ("git merge main", "git reset --hard", "foreman integrate fake b 'PF-1 T0'"):
            with self.subTest(cmd=cmd):
                self.assertEqual(self.v(cmd, role="orchestrator"), "allow")
                ctx = {"session": "S-other", "agent": None, "marked": "S-marked", "cwd": str(self.top)}
                self.assertIsNone(guard.check(self.project, cmd, ctx))
        sub = {"session": "S-marked", "agent": "a1", "marked": "S-marked", "cwd": str(self.top)}
        self.assertEqual(guard.check(self.project, "git merge main", sub)[0], "orchestrator")

    def test_the_refusal(self):
        v = guard.check(self.project, f"git -C {self.top} merge --ff-only main", guard.as_role("agent", str(self.wt)))
        self.assertEqual((v[0], v[1], v[1].why, v[1].kind, v[1].name), ("orchestrator", guard.MAIN_CHECKOUT,
                                                                        "agent", "merge", "ours"))  # fmt: skip
        msg = guard.message(self.project, v)
        self.assertIn(f"`git merge` changes the files of {os.path.realpath(self.top)}", msg)
        self.assertIn("a main checkout of ours (one of its repos, or the one that holds its live configuration)", msg)
        self.assertIn("git stash push -- PATH", msg)
        self.assertIn("It matched in the command `git -C ", msg)
        rec = permissions.refusal(self.project, {"agent_id": "a1"}, v)
        self.assertEqual((rec["kind"], rec["rule"], rec["who"]), ("builtin", guard.MAIN_CHECKOUT, "agent"))

    def test_a_failure_refuses_only_a_named_main_checkout(self):
        with mock.patch.object(guard, "_walk", side_effect=RuntimeError):
            self.assertEqual(self.v(f"git -C {self.top} merge main", self.wt), "orchestrator")
            self.assertEqual(self.v(f"git -C {self.top} log", self.wt), "allow")
            self.assertEqual(self.v("git merge main", self.wt), "allow")
            self.assertEqual(self.v("foreman integrate fake b x", self.wt), "orchestrator")  # the regex
            self.assertEqual(self.v("foreman integrate --help", self.wt), "allow")
        with mock.patch.object(guard, "main_checkouts", side_effect=RuntimeError):
            self.assertEqual(self.v(f"git -C {self.top} merge main", self.wt), "allow")


class IntegrateTest(MainCheckoutCase):
    def test_an_agent_never_integrates(self):
        for cmd in (
            "foreman integrate fake b 'PF-1 T0'",
            "bin/foreman integrate fake b 'PF-1 T0' --report r.md",
            "foreman --project fake integrate fake b 'PF-1 T0'",
            "python3 bin/foreman integrate fake b 'PF-1 T0'",
            "'foreman' 'integrate' fake b",
            "foreman integrate fake b 'PF-1 T0 --check'",
            "foreman integrate fake b x --note=--check",
            "foreman integrate x --check; foreman integrate fake b 'PF-1 T0'",
            "foreman integrate fake b 'PF-1 T0' --measure",
            "sh -c 'foreman integrate fake b x'",
        ):
            for cwd in (self.wt, self.tmp, self.top):
                with self.subTest(cmd=cmd, cwd=str(cwd)):
                    self.assertEqual(self.v(cmd, cwd), "orchestrator")
        v = guard.check(self.project, "foreman integrate fake b x", None)
        self.assertEqual((v[0], v[1], v[1].why), ("orchestrator", guard.INTEGRATE, "agent"))
        msg = guard.message(self.project, v)
        self.assertIn("`foreman integrate` merges a branch into main", msg)
        self.assertIn("foreman progress --done", msg)

    def test_what_an_agent_may_run(self):
        for cmd in (
            "foreman integrate --help",
            "foreman integrate -h",
            "foreman integrate fake b 'PF-1 T0' --check",
            "foreman integrate fake b x '--check'",
            "foreman note 'foreman integrate x'",
            "git commit -m 'foreman integrate fake b'",
            "foreman docs integrate",
            "grep -n 'foreman integrate' docs/orchestrator.md",
            "sed -i '' 's/foreman integrate a/foreman integrate b/' tests/x.ts",
            "python3 - <<'EOF'\nprint('foreman integrate fake b x')\nEOF",
            "git merge --no-ff abc -m 'Merge x: pieces for foreman integrate'",
            "cat > x.sh <<'EOF'\n# for foreman integrate\nEOF",
        ):
            with self.subTest(cmd=cmd):
                self.assertEqual(self.v(cmd, self.wt), "allow")

    def test_opens_stays_in_the_group(self):
        self.assertEqual(self.v("foreman integrate fake b x --opens", self.wt), "orchestrator")
        got = guard.check(self.project, "foreman integrate fake b x --opens", guard.as_role("orchestrator"))
        self.assertIsNone(got)


class TableAndViewTest(MainCheckoutCase):
    def test_rows(self):
        table = self.tmp / "rows.tsv"
        table.write_text(
            f"orchestrator@{self.top}\tgit merge --ff-only fo1/x\n"
            f"orchestrator\tgit -C {self.top} stash\n"
            f"allow@{self.wt}\tgit merge main\n"
            f"allow@{self.top}\tgit log --oneline -5\n"
            "orchestrator\tforeman integrate fake fo1/x 'PF-1 T0'\n"
            "allow\tforeman integrate --help\n"
        )
        counts, failures = guard.run_table(self.project, table)
        self.assertEqual(failures, [])
        self.assertEqual(counts, {"orchestrator": 3, "allow": 3})

    def test_permissions_view(self):
        rules = [r for r in permissions.rules(self.project) if r["pattern"] in guard.AGENT_ONLY]
        self.assertEqual([(r["band"], r["only"]) for r in rules], [("builtin", "orchestrator")] * 3)
        (self.home / "tests/guard.tsv").write_text(f"orchestrator@{self.top}\tgit merge main\nallow\tls\n")
        d = permissions.view(self.project)
        rows = {r["pattern"]: r["rows"] for r in d["rules"] if r["pattern"] in guard.AGENT_ONLY}
        self.assertEqual(rows, {guard.MAIN_CHECKOUT: 1, guard.INTEGRATE: 0, guard.HOLDOUT: 0})
        text = permissions.render(self.project, d)
        self.assertIn("an agent's git that rewrites a main checkout", text)
        self.assertIn("`foreman integrate` (but --check and --help)", text)


class RepoCheckoutTest(MainCheckoutCase):
    """A product whose home lies outside its code repo (its [repos.*] path): that repo's main checkout is protected
    too, though it holds no live file, for an agent of any product; its worktrees and the orchestrator pass."""

    def setUp(self):
        super().setUp()
        self.code = self.tmp / "code"
        (self.code / "lib").mkdir(parents=True)
        (self.code / "README.md").write_text("x\n")
        subprocess.run(["git", "init", "-q", str(self.code)], check=True)
        self.code_home = self.tmp / "code-ops"
        self.code_home.mkdir()
        (self.code_home / "foreman.toml").write_text(
            '[project]\nname = "coded"\nowner = "Owner"\n\n'
            f'[repos.app]\npath = "{self.code}"\n\n[repos.gone]\npath = "{self.tmp}/outer/no-such-repo"\n'
        )
        with open(self.tmp / "projects.toml", "a") as f:
            f.write(f'\n[[project]]\nname = "coded"\nhome = "{self.code_home}"\nmode = "active"\n')
        self.code_wt = self.tmp / "wt/code-a1"
        self.code_wt.mkdir(parents=True)
        (self.code_wt / ".git").write_text(f"gitdir: {self.code}/.git/worktrees/code-a1\n")

    def test_a_branch_in_the_repo_main_checkout(self):
        for cmd in ("git checkout -b x", "git switch -c x", "git merge main", "git stash", "git reset --hard"):
            with self.subTest(cmd=cmd):
                self.assertEqual(self.v(cmd, self.code), "orchestrator")
                self.assertEqual(self.v(cmd, self.code / "lib"), "orchestrator")
                self.assertEqual(self.v(cmd.replace("git", f"git -C {self.code}", 1), self.tmp), "orchestrator")
                self.assertEqual(self.v(cmd, self.code_wt), "allow")
                self.assertEqual(self.v(cmd, self.code, role="orchestrator"), "allow")

    def test_what_passes_there(self):
        for cmd in ("git log", "git status", "git checkout -- README.md", "git fetch", "git worktree add ../w -b x"):
            with self.subTest(cmd=cmd):
                self.assertEqual(self.v(cmd, self.code), "allow")
        self.assertEqual(self.v("git checkout -- .claude/settings.json", self.code), "orchestrator")

    def test_the_refusal_names_the_repo_and_its_product(self):
        v = guard.check(self.project, "git checkout -b x", guard.as_role("agent", str(self.code)))
        self.assertEqual((v[1].kind, v[1].dir, v[1].name), ("checkout", os.path.realpath(self.code), "coded"))
        msg = guard.message(self.project, v)
        self.assertIn(f"`git checkout` changes the files of {os.path.realpath(self.code)}, a main checkout of", msg)
        self.assertIn("coded", msg)

    def test_a_missing_repo_path_protects_nothing_above_it(self):
        subprocess.run(["git", "init", "-q", str(self.tmp / "outer")], check=True)  # a repo above the missing path
        tops = guard.main_checkouts(self.project)
        self.assertIn(guard._fold(os.path.realpath(self.code)), tops)
        self.assertNotIn(guard._fold(str(self.tmp / "outer")), tops)
        self.assertEqual(self.v("git checkout -b x", self.tmp / "outer"), "allow")

    def test_rows(self):
        table = self.tmp / "rows.tsv"
        table.write_text(
            f"orchestrator@{self.code}\tgit checkout -b x\n"
            f"orchestrator@{self.code}\tgit switch -c x\n"
            f"allow@{self.code_wt}\tgit checkout -b x\n"
            f"allow@{self.code_wt}\tgit switch -c x\n"
        )
        counts, failures = guard.run_table(self.project, table)
        self.assertEqual(failures, [])
        self.assertEqual(counts, {"orchestrator": 2, "allow": 2})


class CostTest(MainCheckoutCase):
    def test_no_registry_without_git_or_integrate(self):
        with mock.patch.object(guard, "main_checkouts", side_effect=AssertionError) as m:
            self.assertEqual(self.v("ls -la && cat x | wc -l"), "allow")
            self.assertEqual(self.v("git log --oneline"), "allow")  # it reads: no checkout is looked up
            m.assert_not_called()


if __name__ == "__main__":
    unittest.main()
