"""The git class of the reserved files (guard.git_entries): the hooks/, info/, config and
config.worktree of the common git directory of each protected repo (the main checkout of a live home, and each
[repos.*] path), each worktree's config.worktree, an absolute core.hooksPath and the included files, and the
machine's git config; refused to an agent only, by a Write/Edit and by a Bash command that writes one by name; the
orchestrator and the owner's other sessions pass; a failure stops nothing (O8); the refusal, the hook's log, the
permissions band, table rows; and gitdir.py, which reads it all from the disk without git."""

import json
import os
import subprocess
import unittest
from unittest import mock

from test_guard_main_checkout import MainCheckoutCase

from foreman import approved, config, gitdir, guard, i18n, permissions

GIT_ENV = ("XDG_CONFIG_HOME", "GIT_CONFIG_GLOBAL", "GIT_CONFIG_SYSTEM")


class GitDirCase(MainCheckoutCase):
    """MainCheckoutCase («ours», its home ops/ inside the repo `top`, a worktree outside `wt` and one nested in
    .claude/worktrees) with real worktree records in top/.git/worktrees, a code repo `app` as «ours»'s [repos.app]
    (J1), a repo of no product (`loose`) and a fake HOME with ~/.gitconfig and ~/.config/git/ (J2)."""

    def setUp(self):
        super().setUp()
        self.saved_git = {k: os.environ.pop(k, None) for k in GIT_ENV}
        self.git = self.top / ".git"
        for w in (self.wt, self.nested):
            rec = self.git / "worktrees" / w.name
            rec.mkdir(parents=True)
            (rec / "commondir").write_text("../..\n")
            (rec / "gitdir").write_text(f"{w}/.git\n")
            (rec / "HEAD").write_text("ref: refs/heads/x\n")
        self.app = self.tmp / "app"
        self.loose = self.tmp / "loose"
        for repo in (self.app, self.loose):
            subprocess.run(["git", "init", "-q", str(repo)], check=True)
        with open(self.ops / "foreman.toml", "a") as f:
            f.write(f'\n[repos.app]\npath = "{self.app}"\n')
        (self.user / ".gitconfig").write_text("[user]\n\tname = T\n")
        (self.user / ".config/git").mkdir(parents=True)
        (self.user / ".config/git/ignore").write_text("*.swp\n")
        self.ours = config.load_project(self.ops)

    def tearDown(self):
        for k, v in self.saved_git.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        super().tearDown()

    def file(self, path, role="agent", cwd=None):
        """("allow" | "deny", class) of a Write of path in a role, from cwd (default: the main checkout)."""
        v = guard.check_file(self.project, str(path), guard.as_role(role, str(cwd or self.top)))
        if v is None:
            return "allow", ""
        return "deny", v[1].kind if isinstance(v[1], guard.Hit) else "error"

    def bash(self, cmd, role="agent", cwd=None):
        """ "allow" | "file:<class>" | the verdict's kind, for a Bash command in a role and folder (default: top)."""
        v = guard.check(self.project, cmd, guard.as_role(role, str(cwd or self.top)))
        if v is None:
            return "allow"
        return str(v[1]) if v[0] == "file" else v[0]


class WriteTest(GitDirCase):
    def reserved_paths(self):
        g, u = self.git, self.user
        return [
            g / "hooks/post-merge",
            g / "hooks",
            g / "config",
            g / "config.worktree",
            g / "info/attributes",
            g / "info/sparse-checkout",
            g / "worktrees/ours-a1/config.worktree",
            g / "worktrees/x/config.worktree",
            g / "worktrees/new/config.worktree",  # a worktree not made yet: the pattern
            self.app / ".git/hooks/pre-push",  # J1: a code repo of the product
            self.app / ".git/config",
            u / ".gitconfig",  # J2: the machine's
            u / ".config/git/config",
            u / ".config/git/attributes",
            u / ".config/git/ignore",
        ]

    def test_agent_refused_others_pass(self):
        for p in self.reserved_paths():
            with self.subTest(path=str(p)):
                self.assertEqual(self.file(p), ("deny", "git"))
                self.assertEqual(self.file(p, "orchestrator"), ("allow", ""))
                session = {"session": "S2", "agent": None, "cwd": str(self.top)}  # another session of the product
                self.assertIsNone(guard.check_file(self.project, str(p), session))

    def test_from_the_worktrees(self):
        for cwd in (self.wt, self.nested, self.wt / "ops"):
            rel = os.path.relpath(self.git / "hooks/pre-commit", cwd)
            with self.subTest(cwd=str(cwd)):
                self.assertTrue(rel.startswith(".."))
                self.assertEqual(self.file(rel, cwd=cwd), ("deny", "git"))
                self.assertEqual(self.file(self.git / "config", cwd=cwd), ("deny", "git"))
                self.assertEqual(self.file(rel, "orchestrator", cwd=cwd), ("allow", ""))
        self.assertEqual(self.file("~/.gitconfig", cwd=self.wt), ("deny", "git"))

    def test_what_stays_free(self):
        g = self.git
        for p in (
            g / "COMMIT_EDITMSG",
            g / "HEAD",
            g / "index",
            g / "ORIG_HEAD",
            g / "packed-refs",
            g / "refs/heads/x",
            g / "objects/ab/cdef",
            g / "logs/HEAD",
            g / "worktrees/ours-a1/HEAD",
            g / "worktrees/ours-a1/config",  # not a file git reads as config
            g / "worktrees/ours-a1/x/config.worktree",
            self.loose / ".git/hooks/pre-commit",  # a repo of no product
            self.loose / ".git/config",
            self.wt / "README.md",
            self.top / "hooks/x.py",  # a folder of the tree named hooks
            self.user / ".config/gitx/config",
            self.user / ".gitconfig.bak",
        ):
            with self.subTest(path=str(p)):
                self.assertEqual(self.file(p), ("allow", ""))

    def test_the_other_classes_stay_for_the_orchestrator(self):
        self.assertEqual(self.file(self.ops / "foreman.toml", "orchestrator"), ("deny", "config"))
        self.assertEqual(self.file(self.user / ".claude/settings.json", "orchestrator"), ("deny", "claude"))

    def test_hit(self):
        v = guard.check_file(self.project, str(self.git / "hooks/x"))
        self.assertEqual((v[0], v[1], v[1].kind, v[1].name, v[1].why), ("file", "file:git", "git", "ours", "agent"))
        v = guard.check_file(self.project, str(self.user / ".gitconfig"))
        self.assertEqual((v[1].kind, v[1].name), ("git", ""))

    def test_linked_hooks_folder(self):
        real = self.tmp / "shared-hooks"
        real.mkdir()
        (self.git / "hooks").rename(self.tmp / "old-hooks")
        (self.git / "hooks").symlink_to(real)
        self.assertEqual(self.file(self.git / "hooks/pre-commit"), ("deny", "git"))
        self.assertEqual(self.file(real / "pre-commit"), ("deny", "git"))  # by its real path
        self.assertEqual(self.bash(f"rm {self.git}/hooks"), "file:git")  # the link itself
        self.assertEqual(self.bash(f"ln -sfn /tmp/h {self.git}/hooks"), "file:git")

    def test_linked_gitconfig(self):
        dot = self.tmp / "dotfiles"
        dot.mkdir()
        (dot / ".gitconfig").write_text("[user]\n")
        (dot / "ignore").write_text("x\n")
        (self.user / ".gitconfig").unlink()
        (self.user / ".gitconfig").symlink_to(dot / ".gitconfig")
        (self.user / ".config/git/ignore").unlink()
        (self.user / ".config/git/ignore").symlink_to(dot / "ignore")
        self.assertEqual(self.file(dot / ".gitconfig"), ("deny", "git"))
        self.assertEqual(self.file(dot / "ignore"), ("deny", "git"))  # what a link in ~/.config/git/ reaches
        self.assertEqual(self.bash("rm ~/.gitconfig"), "file:git")
        self.assertEqual(self.file(dot / "other"), ("allow", ""))

    def test_what_the_configs_name(self):
        hp = self.tmp / "abs-hooks"
        (self.git / "config").write_text(
            (self.git / "config").read_text() + f'[core]\n\thooksPath = "{hp}" ; a comment\n'
        )
        (self.user / ".gitconfig").write_text(
            '[include]\n\tpath = ~/.gitconfig.local\n[includeIf "gitdir:~/w/"]\n\tpath = more.inc\n'
        )
        (self.user / "more.inc").write_text("[core]\n\thooksPath = ~/global-hooks\n[include]\n\tpath = deep.inc\n")
        for p in (
            hp / "pre-commit",
            self.user / ".gitconfig.local",  # included, though it does not exist yet
            self.user / "more.inc",
            self.user / "deep.inc",
            self.user / "global-hooks/post-merge",
        ):
            with self.subTest(path=str(p)):
                self.assertEqual(self.file(p), ("deny", "git"))
        (self.app / ".git/config").write_text("[core]\n\thooksPath = .githooks\n")  # relative: in the tree, a diff
        self.assertEqual(self.file(self.app / ".githooks/pre-commit"), ("allow", ""))

    def test_git_config_env(self):
        os.environ["GIT_CONFIG_GLOBAL"] = str(self.tmp / "g.conf")
        os.environ["XDG_CONFIG_HOME"] = str(self.tmp / "xdg")
        self.assertEqual(self.file(self.tmp / "g.conf"), ("deny", "git"))
        self.assertEqual(self.file(self.tmp / "xdg/git/config"), ("deny", "git"))
        self.assertEqual(self.file(self.user / ".config/git/config"), ("allow", ""))  # not git's any more

    def test_a_home_inside_a_worktree(self):
        # the home lives in a worktree (its .git a file): the repo is the one its commondir names
        other = self.tmp / "elsewhere"
        subprocess.run(["git", "init", "-q", str(other)], check=True)
        lw = self.tmp / "linked"
        (lw / "home").mkdir(parents=True)
        rec = other / ".git/worktrees/linked"
        rec.mkdir(parents=True)
        (rec / "commondir").write_text("../..\n")
        (lw / ".git").write_text(f"gitdir: {rec}\n")
        (lw / "home/foreman.toml").write_text('[project]\nname = "lw"\nowner = "Owner"\n')
        with open(self.tmp / "projects.toml", "a") as f:
            f.write(f'\n[[project]]\nname = "lw"\nhome = "{lw / "home"}"\nmode = "active"\n')
        self.assertEqual(self.file(other / ".git/hooks/x"), ("deny", "git"))
        self.assertEqual(self.file(rec / "config.worktree"), ("deny", "git"))

    def test_failure_stops_nothing(self):  # O8
        with mock.patch.object(gitdir, "common_dir", side_effect=RuntimeError("boom")):
            self.assertEqual(guard.git_entries(self.project, [self.ours]), [])
            self.assertEqual(self.file(self.git / "hooks/x"), ("allow", ""))
            self.assertEqual(self.file(self.ops / "foreman.toml"), ("deny", "config"))  # the rest stays
        with mock.patch.object(guard, "reserved", side_effect=RuntimeError("boom")):
            self.assertEqual(self.file(self.git / "hooks/x"), ("allow", ""))
            self.assertEqual(self.file(self.user / ".gitconfig"), ("allow", ""))
            self.assertEqual(self.bash(f"cp h {self.git}/hooks/x"), "allow")


class BashTest(GitDirCase):
    def test_writes_by_name(self):
        g = self.git
        for cmd, cwd in (
            (f"cp h {g}/hooks/pre-commit", None),
            (f"ln -s /tmp/h {g}/hooks/pre-push", None),
            (f"chmod +x {g}/hooks/x", None),
            (f"echo '[core]' >> {g}/config", None),
            (f"sed -i '' 's/a/b/' {g}/config", None),
            (f"rm -r {g}/hooks", None),
            (f"rm -rf {g}", None),  # a tree that holds them
            (f"cd {g} && tee hooks/x < /dev/null", None),
            ("cp h .git/hooks/post-merge", None),
            ("cp h ../../repo/.git/hooks/pre-commit", self.wt),
            (f"install -m 755 h {g}/hooks/pre-commit", self.nested),
            (f"sh -c 'cat x > {g}/info/attributes'", self.wt),
            (f"echo x > {g}/worktrees/ours-a1/config.worktree", self.wt),
            (f"cp h {self.app}/.git/hooks/pre-push", None),
            ("echo '[alias]' >> ~/.gitconfig", None),
            ("cp x ~/.config/git/attributes", None),
        ):
            with self.subTest(cmd=cmd, cwd=str(cwd)):
                self.assertEqual(self.bash(cmd, cwd=cwd), "file:git")
                self.assertEqual(self.bash(cmd, "orchestrator", cwd=cwd), "allow")

    def test_what_passes(self):
        g = self.git
        for cmd in (
            f"cat {g}/config",
            f"ls {g}/hooks",
            f"cp {g}/config /tmp/c",
            "git config --get core.bare",
            f"echo x > {g}/COMMIT_EDITMSG",
            f"cp h {self.loose}/.git/hooks/pre-commit",
            "cat ~/.gitconfig",
        ):
            with self.subTest(cmd=cmd):
                self.assertEqual(self.bash(cmd), "allow")

    def test_the_orchestrator_still_meets_the_other_classes(self):
        self.assertEqual(
            self.bash(f"cp h {self.git}/hooks/x && cp c {self.ops}/foreman.toml", "orchestrator"), "file:config"
        )

    def test_hit(self):
        v = guard.check(self.project, f"cp h {self.git}/hooks/pre-commit", guard.as_role("agent", str(self.top)))
        self.assertEqual((v[1].kind, v[1].name, v[1].why, v[1].bash), ("git", "ours", "agent", True))


class MessageTest(GitDirCase):
    def test_messages(self):
        def msg(path=None, cmd=None, project=None):
            p = project or self.project
            v = (
                guard.check_file(p, str(path), guard.as_role("agent", str(self.top)))
                if path
                else guard.check(p, cmd, guard.as_role("agent", str(self.top)))
            )
            return guard.message(p, v)

        m = msg(self.git / "hooks/post-merge")
        self.assertIn(f"`{self.git}/hooks/post-merge` is part of the git hooks or configuration of a repo of ours", m)
        self.assertIn("which an agent does not change", m)
        self.assertIn("`git -c KEY=VALUE …`", m)
        self.assertIn("say so in your report", m)
        self.assertIn("this machine's git configuration", msg(self.user / ".gitconfig"))
        m = msg(cmd=f"cp h {self.git}/hooks/pre-commit")
        self.assertIn("this command writes", m)
        self.assertIn("an agent does not change it", m)
        self.assertIn("(`cp h …/pre-commit`)", m)
        with mock.patch.object(i18n, "for_project", return_value=i18n.strings("es")):
            m = msg(self.git / "config")
            self.assertIn("parte de los hooks o de la configuración de git de un repo de ours", m)
            self.assertIn("`git -c CLAVE=VALOR …`", m)
            self.assertIn("la configuración de git de este ordenador", msg(self.user / ".gitconfig"))

    def test_keys_in_both_languages(self):
        for lang in ("en", "es"):
            s = i18n.strings(lang)
            for k in (
                "guard_file_agent",
                "guard_file_bash_agent",
                "guard_file_what_git",
                "guard_file_what_git_machine",
                "guard_file_how_git",
                "perm_reserved_git",
                "perm_reserved_who_git",
            ):
                self.assertIn(k, s, f"{lang}: {k}")


class HookAndBandTest(GitDirCase):
    def test_hook_refusal_logs_and_notice(self):
        self.mark("S1")
        ev = {
            "hook_event_name": "PreToolUse",
            "tool_name": "Write",
            "session_id": "S1",
            "agent_id": "a1",
            "tool_use_id": "T1",
            "cwd": str(self.home),
            "tool_input": {"file_path": str(self.git / "hooks/post-merge"), "content": "SECRET-CONTENT"},
        }
        r = self.hook("guard", ev, cwd=self.home)
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("git hooks or configuration of a repo of ours", r.stderr)
        log = [json.loads(x) for x in approved.log_file(self.project).read_text().splitlines()]
        self.assertEqual([(x["why"], x["pattern"]) for x in log], [("agent", "file:git")])
        refused = [json.loads(x) for x in permissions.log_file(self.project).read_text().splitlines()]
        self.assertEqual([(x["rule"], x["who"]) for x in refused], [("file:git", "agent")])
        r = self.hook("guard", {**ev, "agent_id": None}, cwd=self.home)  # a main thread of the product: passes
        self.assertEqual((r.returncode, r.stderr), (0, ""))

    def test_band(self):
        band = permissions.reserved_files(self.ours)
        (git,) = [c for c in band["classes"] if c["kind"] == "git"]
        for p in (
            f"{self.git}/hooks/",
            f"{self.git}/info/",
            f"{self.git}/config",
            f"{self.git}/worktrees/*/config.worktree",
            f"{self.app}/.git/hooks/",
            f"{self.user}/.gitconfig",
            f"{self.user}/.config/git/",
        ):
            self.assertIn(p, git["paths"])
        text = "\n".join(permissions._reserved_lines(self.ours, i18n.strings("en"), band))
        self.assertIn("git hooks and configuration: ", text)
        self.assertIn("an agent never", text)
        # the fake product (no repo of its own) shows the machine's only, and «ours» among the others
        (git,) = [c for c in permissions.reserved_files(self.project)["classes"] if c["kind"] == "git"]
        self.assertNotIn(f"{self.git}/hooks/", git["paths"])
        self.assertIn(f"{self.user}/.gitconfig", git["paths"])

    def test_table_rows(self):
        table = self.home / "tests/guard.tsv"
        table.write_text(
            f"orchestrator\tfile:{self.git}/hooks/post-merge\n"
            f"orchestrator@{self.wt}\tfile:../../repo/.git/config\n"
            f"orchestrator\tfile:{self.app}/.git/hooks/pre-push\n"
            "orchestrator\tfile:~/.gitconfig\n"
            f"allow\tfile:{self.git}/COMMIT_EDITMSG\n"
            f"orchestrator@{self.wt}\tcp h ../../repo/.git/hooks/pre-commit\n"
            f"allow@{self.top}\tcat .git/config\n"
        )
        counts, failures = guard.run_table(self.project, table)
        self.assertEqual(failures, [])
        self.assertEqual(counts, {"orchestrator": 5, "allow": 2})

    def test_integrate_sees_no_git_file_in_a_diff(self):
        entries = guard.reserved(self.ours)
        self.assertIsNone(guard.reserved_entry(self.top / "ops/x.md", entries))
        self.assertEqual(guard.reserved_entry(self.git / "hooks/x", entries)[0], "git")


class GitDirTest(GitDirCase):
    def test_checkout_and_common_dir(self):
        self.assertEqual(gitdir.checkout_of(str(self.ops / "tests")), str(self.top))
        self.assertEqual(gitdir.checkout_of(str(self.wt / "ops/new/dir")), str(self.wt))
        self.assertIsNone(gitdir.checkout_of("/"))
        real = os.path.realpath(self.git)
        self.assertEqual(gitdir.common_dir(str(self.top)), real)
        self.assertEqual(gitdir.common_dir(str(self.wt)), real)  # the worktree's commondir
        self.assertEqual(gitdir.common_dir(str(self.nested)), real)
        sub = self.tmp / "sub"
        (self.git / "modules/sub").mkdir(parents=True)
        sub.mkdir()
        (sub / ".git").write_text(f"gitdir: {os.path.relpath(self.git / 'modules/sub', sub)}\n")
        self.assertEqual(gitdir.common_dir(str(sub)), os.path.realpath(self.git / "modules/sub"))  # a submodule
        (sub / ".git").write_text("nonsense\n")
        self.assertIsNone(gitdir.common_dir(str(sub)))
        self.assertIsNone(gitdir.common_dir(str(self.tmp / "nowhere")))

    def test_config_values(self):
        f = self.tmp / "c"
        f.write_text(
            "# comment\n"
            "[Core]\n"
            '\tHooksPath = "/a b/h" # trailing\n'
            "\tbare = false\n"
            "[core] hookspath = /second\n"
            '[includeIf "gitdir:~/x/"]\n'
            "\tpath = x.inc\n"
            "[include.sub]\n"  # the old form: still the include section
            "\tpath = old.inc\n"
            "[alias]\n"
            "\tpath = not-this\n"
            '\tx = "!echo \\"q\\""\n'
            "garbage line without section\n"
        )
        self.assertEqual(
            gitdir.config_values(str(f)),
            [
                ("core.hookspath", "/a b/h"),
                ("core.hookspath", "/second"),
                ("includeif.path", "x.inc"),
                ("include.path", "old.inc"),
            ],
        )
        self.assertEqual(gitdir.config_values(str(self.tmp / "missing")), [])

    def test_config_paths_follow_includes_but_not_forever(self):
        a, b = self.tmp / "a.inc", self.tmp / "b.inc"
        a.write_text(f"[include]\n\tpath = {b}\n[core]\n\thooksPath = %(prefix)/x\n")
        b.write_text(f"[include]\n\tpath = {a}\n")  # a cycle
        got = gitdir.config_paths(str(a))
        self.assertEqual(len(got), gitdir.MAX_INCLUDES + 1)
        self.assertEqual({p for p, _ in got}, {str(a), str(b)})


class CostTest(GitDirCase):
    def test_reserved_reads_no_git(self):
        with mock.patch.object(subprocess, "run", side_effect=AssertionError("git ran")):
            entries = guard.reserved(self.project)
        self.assertTrue(any(e[0] == "git" for e in entries))


if __name__ == "__main__":
    unittest.main()
