"""A `git push` judged by where it goes (gitpush.py, guard.py's resolved texts): the folder from the event's cwd,
`cd` and `git -C`, the repo's remote and the refs git would push, compared with the same [deny] lists."""

import os
import shutil
import textwrap
from unittest import mock

from helpers import ProjectCase, git, make_repo

from foreman import gitpush, grants, guard, hooks_mod
from foreman.shell import lex, runs

PUSH = "git " + "push"  # built in pieces: the installed guard reads this file's commands
MARK = "OWNER" + "_OK"
NEVER = "(^|\\\\s)" + PUSH  # foreman's own rule, as written in its TOML

# Three kinds of product lists, with their names replaced: pub-org is a public organisation, pkg-tap
# a package tap, own a private one whose main, tags and force need the owner's mark.
PUBLIC = textwrap.dedent(f"""
    [project]
    name = "fake"
    owner = "Owner"
    language = "en"

    [repos.lib]
    path = "{{tmp}}/lib"
    main = "main"

    [repos.calib]
    path = "{{tmp}}/calib"

    [repos.tap]
    path = "{{tmp}}/pkg-tap"

    [deny]
    patterns = ['{PUSH} .*pub-org', 'gh release create',
                'git\\b.*\\bpush\\b.*(--tags|--follow-tags|refs/tags/|\\sv[0-9]+\\.[0-9]+)',
                'git\\b.*(pkg-tap.*\\bpush\\b|\\bpush\\b.*pkg-tap)']
    """)
NO = f'^(?!.*{MARK}="Owner, )'
PRIVATE = textwrap.dedent(f"""
    [project]
    name = "fake"
    owner = "Owner"
    language = "en"

    [repos.app]
    path = "{{tmp}}/app"

    [repos.api]
    path = "{{tmp}}/api"

    [deny]
    patterns = ['{NO}.*git\\s+push\\s.*(\\sv\\d|--tags|refs/tags)',
                '{NO}.*git\\s+push\\s+(-\\S+\\s+)*\\S+\\s+(\\S+:)?(main|master)(\\s|$)',
                '{NO}.*git\\s+push\\s.*(--force|\\s-f(\\s|$)|--mirror|--delete|\\s:\\S)']
    """)
NOWHERE = textwrap.dedent(f"""
    [project]
    name = "fake"
    owner = "Owner"
    language = "en"

    [deny]
    patterns = ["git remote add", "{NEVER}"]
    """)


def remote(path, url, name="origin"):
    git(path, "config", f"remote.{name}.url", url)
    git(path, "config", f"remote.{name}.fetch", f"+refs/heads/*:refs/remotes/{name}/*")


class PushCase(ProjectCase):
    toml = PUBLIC

    def setUp(self):
        super().setUp()
        # a global file of the test's own (its aliases), so the identity of helpers' gitconfig is gone: one for the
        # fixture commits, as a CI runner has none to guess from
        self.git_env = mock.patch.dict(
            os.environ,
            {
                "GIT_CONFIG_GLOBAL": str(self.tmp / "gitconfig"),
                "GIT_CONFIG_NOSYSTEM": "1",
                "GIT_AUTHOR_NAME": "Foreman Tests",
                "GIT_AUTHOR_EMAIL": "tests@example.test",
                "GIT_COMMITTER_NAME": "Foreman Tests",
                "GIT_COMMITTER_EMAIL": "tests@example.test",
            },
        )
        self.git_env.start()
        self.addCleanup(self.git_env.stop)
        for name, url in (
            ("lib", "https://example.test/pub-org/lib.git"),
            ("calib", "git@example.test:pub-org/calib.git"),
            ("pkg-tap", "https://example.test/pub-org/pkg-tap.git"),
        ):
            remote(make_repo(self.tmp / name), url)
        self.own = make_repo(self.tmp / "own")  # a repo of no project, with a remote no rule names
        remote(self.own, "https://example.test/me/own.git")

    def verdict(self, cmd, cwd="-", role="agent"):
        """allow | deny | orchestrator, as an agent (or the orchestrator) from cwd ("-": no cwd at all, as a table
        run)."""
        if role != "agent":
            v = guard.check(self.project, cmd, guard.as_role(role, None if cwd == "-" else str(cwd)))
            return "allow" if v is None else v[0]
        v = guard.check(self.project, cmd, {} if cwd == "-" else {"cwd": str(cwd) if cwd else None})
        return "allow" if v is None else v[0]

    def hit(self, cmd, cwd):
        v = guard.check(self.project, cmd, {"cwd": str(cwd)})
        return v and v[1]

    def where(self, cmd, cwd=None):
        """Where the hit matched, from cwd (the repo of no project by default)."""
        hit = self.hit(cmd, cwd or self.own)
        return hit and hit.where

    def resolved(self, cmd, cwd, project=None):
        """Every resolved text of the line's segments, from cwd."""
        r = gitpush.Resolver(project or self.project, str(cwd) if cwd else None)
        segs, _ = lex(cmd)
        words_of = [[t for t in s.toks if t.kind == "word"] for s in segs]
        info = [runs(w) for w in words_of]
        here = gitpush.folders(segs, words_of, info, r.start)
        return [t for k, w in enumerate(words_of) for t, _ in r.resolve(w, info[k].prog, here[k])]


class HolesTest(PushCase):
    """The plan's matrix (§1.3) with each product's list and the real folder of each command: every hole that
    passed before is denied, and what is allowed on purpose stays allowed."""

    def test_public_org(self):
        lib, tap, calib = self.tmp / "lib", self.tmp / "pkg-tap", self.tmp / "calib"
        rows = [
            (f"git -C {lib} push origin main", self.home),
            (f"cd {lib} && {PUSH} origin main", self.home),
            (f"cd {lib} && {PUSH}", self.home),
            (f"{PUSH} origin main", lib),
            (PUSH, lib),
            (f"cd {tap} && {PUSH}", self.home),
            (f"cd {tap} && {PUSH} origin main", self.home),
            (f"git -C {calib} push origin main", self.home),
            (f"{PUSH} origin HEAD:main", lib),
            (f"{PUSH} origin +HEAD:refs/heads/main", lib),
            (f"{PUSH} -f origin mg-x", lib),
        ]
        for cmd, cwd in rows:
            self.assertEqual(self.verdict(cmd), "allow", f"a hole before: {cmd}")  # no cwd: only the text
            self.assertEqual(self.verdict(cmd, cwd), "deny", cmd)
        self.assertEqual(self.verdict(f"{PUSH} https://example.test/pub-org/lib.git main", self.home), "deny")
        # allowed on purpose: a branch pushed from the home (no repo there)
        self.assertEqual(self.verdict(f"{PUSH} origin rp11-version", self.home), "allow")
        hit = self.hit(f"cd {lib} && {PUSH}", self.home)
        self.assertEqual((hit.where, hit.dir), ("resolved", str(lib)))
        self.assertEqual(hit.part, f"{PUSH} https://example.test/pub-org/lib.git main:main")
        self.assertEqual(  # an scp URL, and its https form
            self.resolved(f"git -C {calib} push origin main", self.home),
            [
                f"{PUSH} git@example.test:pub-org/calib.git main:main",
                f"{PUSH} https://example.test/pub-org/calib.git main:main",
            ],
        )

    def test_private_branches(self):
        self.write_toml(PRIVATE)
        app, api = make_repo(self.tmp / "app"), make_repo(self.tmp / "api")
        remote(app, "https://example.test/owner/app.git")
        remote(api, "https://example.test/owner/api.git")
        for cmd, cwd in (
            (f"cd {api} && {PUSH}", self.home),
            (PUSH, api),
            (f"{PUSH} origin HEAD", api),
            (f"{PUSH} origin refs/heads/main", api),
        ):
            self.assertEqual(self.verdict(cmd), "allow", f"a hole before: {cmd}")
            self.assertEqual(self.verdict(cmd, cwd), "deny", cmd)
        for cmd, cwd in (
            (f"{PUSH} origin main", app),
            (f"{PUSH} origin @:main", app),
            (f"{PUSH} https://example.test/owner/app.git fix:main", app),
        ):
            self.assertEqual(self.verdict(cmd, cwd), "deny", cmd)
        # an agent pushing its own branch (also from a worktree on that branch), and the owner's mark
        self.assertEqual(self.verdict(f"{PUSH} -u origin fix/badge-count", api), "allow")
        wt = self.tmp / "wt-c9"
        git(app, "worktree", "add", "-q", "-b", "fix/android-polish", str(wt))
        self.assertEqual(self.verdict(f"cd {wt}; {PUSH} -u origin fix/android-polish", self.home), "allow")
        self.assertEqual(self.verdict(f"cd {wt} && {PUSH}", self.home), "allow")
        self.assertEqual(self.verdict(f"cd {wt} && {PUSH} origin HEAD:main", self.home), "deny")
        for cmd in (
            f'{MARK}="Owner, 2026-11-01" {PUSH} origin v1.6.0',
            f'{MARK}="Owner, 2026-11-01" {PUSH} -f origin v1.6.0',
            f'{MARK}="Owner, 2026-11-01" {PUSH}',
        ):
            self.assertEqual(self.verdict(cmd, app), "allow", cmd)
            self.assertEqual(self.verdict(cmd.split(" ", 2)[2], app), "deny", cmd)

    def test_no_remote(self):
        self.write_toml(NOWHERE)
        repo = make_repo(self.tmp / "solo")
        cmd = f"git -C {repo} push origin main"
        self.assertEqual(self.verdict(cmd), "allow", "a hole before")
        self.assertEqual(self.verdict(cmd, self.tmp), "deny")
        self.assertEqual(self.hit(cmd, self.tmp).part, f"{PUSH} origin main:main")  # no such remote: as written
        self.assertEqual(self.verdict(f"git -C {repo} status", self.tmp), "allow")


class FolderTest(PushCase):
    def test_cd_forms(self):
        lib = self.tmp / "lib"
        rel = os.path.relpath(lib, self.home)
        deny = [
            f"cd {self.tmp} && cd lib && {PUSH}",  # relative, chained
            f"cd {rel} && {PUSH}",
            f"cd ~ ; cd {lib}; {PUSH}",
            f"(cd {lib}; {PUSH})",
            f"(cd {lib}) ; {PUSH} origin x",  # the subshell's cd may or may not reach: both folders
            f"true && cd {lib} && {PUSH}",
            f"ls || cd {lib}; {PUSH}",
            f"if true; then cd {lib}; fi; {PUSH}",
            f"{{ cd {lib}; {PUSH}; }}",
            f"pushd {lib} && {PUSH}",
            f"cd -P {lib} && {PUSH}",
            f"sh -c 'cd {lib} && {PUSH}'",
            f"cd {lib} && sh -c '{PUSH}'",
            f"cd {lib} && echo $({PUSH})",
            f"cd {lib} && echo origin | xargs {PUSH}",
            f"cd {lib} && foreman lock r run 'w' {PUSH}",
            f"git -C {self.tmp} -C lib push",
            f"git --no-pager -C {lib} push",
            f"/usr/bin/git -C{lib} push",
        ]
        for cmd in deny:
            self.assertEqual(self.verdict(cmd, self.home), "deny", cmd)
        own = self.own
        allow = [
            f"cd {lib} && git status",
            f"cd {own} && {PUSH}",
            f"cd {own} && {PUSH} origin feature",
            f"cd {lib}; cd {own} && {PUSH}",
            f"git -C {own} push",
            f"ssh host 'cd {lib} && {PUSH}'",  # runs on another machine: only its text
            f"cd {lib} && git -c x=1 log",
        ]
        for cmd in allow:
            self.assertEqual(self.verdict(cmd, self.home), "allow", cmd)

    def test_sure_or_either(self):
        lib, home = str(self.tmp / "lib"), str(self.home)
        cases = {
            f"cd {lib}; X": {lib},
            f"cd {lib} && X": {lib},
            f"a && cd {lib} && X": {lib},
            f"a && cd {lib}; X": {lib, home},
            f"a || cd {lib} && X": {lib, home},
            f"(cd {lib}); X": {lib, home},
            f"cd {lib} &\nX": {lib, home},
            "cd /no/such/dir && X": {home, gitpush.UNKNOWN},
            'cd "$WT" && X': {home, gitpush.UNKNOWN},
            "cd - && X": {home, gitpush.UNKNOWN},
            "popd; X": {home, gitpush.UNKNOWN},
            "cd && X": {os.path.expanduser("~")},
        }
        start = frozenset({home})
        for cmd, want in cases.items():
            segs, _ = lex(cmd)
            words_of = [[t for t in s.toks if t.kind == "word"] for s in segs]
            got = gitpush.folders(segs, words_of, [runs(w) for w in words_of], start)
            self.assertEqual(set(got[-1]), want, cmd)

    def test_unknown_folder_checks_every_repo(self):
        cases = (
            ('cd "$WT" && ' + PUSH, self.home),
            (f"cd {self.tmp}/gone && {PUSH} origin x", self.home),
            (f"git -C {self.tmp}/lib-x push -u origin mg-x", self.home),
            (PUSH, None),  # an event without cwd
            (PUSH, self.tmp / "gone"),
        )
        for cmd, cwd in cases:
            hit = self.hit(cmd, cwd) if cwd else guard.check(self.project, cmd, {"cwd": None})[1]
            self.assertEqual(hit.where, "unknown", cmd)
            self.assertTrue(hit.dir.startswith(str(self.tmp)), hit.dir)
        msg = guard.message(self.project, ("deny", self.hit('cd "$WT" && ' + PUSH, self.home)))
        self.assertIn("could not be known", msg)
        self.assertIn("write it literally", msg)
        # an unknown folder resolves each repo with its main branch, also a repo without a remote
        texts = self.resolved("cd - && " + PUSH, self.home)
        self.assertIn(f"{PUSH} https://example.test/pub-org/lib.git main:main", texts)
        self.assertIn(f"{PUSH} https://example.test/pub-org/calib.git main:main", texts)
        self.assertIn(f"{PUSH} origin main:main", texts)  # the home: not a repo

    def test_git_that_does_not_answer(self):
        """git hangs in lib: lib is an unknown folder, so the push is checked against every repo (calib denies)."""
        fake = self.tmp / "bin"
        fake.mkdir()
        lib = self.tmp / "lib"
        real = shutil.which("git")
        (fake / "git").write_text(f'#!/bin/sh\ncase "$*" in *"{lib} "*) exec sleep 5 ;; esac\nexec {real} "$@"\n')
        (fake / "git").chmod(0o755)
        with mock.patch.object(gitpush, "TIMEOUT", 0.3), mock.patch.dict(os.environ, {"PATH": str(fake)}):
            self.assertIsNone(gitpush.read_repo(str(lib)))
            hit = self.hit(f"{PUSH} origin feature", lib)
        self.assertEqual((hit.where, hit.dir), ("unknown", str(self.tmp / "calib")))
        with mock.patch.dict(os.environ, {"PATH": str(self.tmp / "nothing")}):
            self.assertIsNone(gitpush.read_repo(str(lib)))  # no git at all

    def test_non_repo_and_odd_folders(self):
        plain = self.tmp / "plain"
        plain.mkdir()
        r = gitpush.read_repo(str(plain))
        self.assertEqual((r.git_dir, r.branch, r.config.get("remote.origin.url")), (None, None, None))
        self.assertIsNone(gitpush.read_repo(str(self.tmp / "missing")))
        self.assertEqual(self.resolved(f"{PUSH} up feature", plain), [f"{PUSH} up feature:feature"])


class RepoTest(PushCase):
    """What git says of a real repo: remotes, URLs and the refs each form pushes."""

    def setUp(self):
        super().setUp()
        self.repo = make_repo(self.tmp / "r")
        remote(self.repo, "https://example.test/pub-org/r.git")
        git(self.repo, "branch", "feat")

    def texts(self, cmd):
        return self.resolved(cmd, self.repo)

    def test_remote_and_urls(self):
        r = self.repo
        self.assertEqual(self.texts(PUSH), [f"{PUSH} https://example.test/pub-org/r.git main:main"])
        git(r, "config", "remote.origin.pushurl", "git@push.test:pub-org/r.git")
        self.assertEqual(
            self.texts(f"{PUSH} origin feat"),
            [f"{PUSH} git@push.test:pub-org/r.git feat:feat", f"{PUSH} https://push.test/pub-org/r.git feat:feat"],
        )
        git(r, "config", "--unset", "remote.origin.pushurl")
        remote(r, "gh:pub-org/other.git", "up")
        git(r, "config", "url.https://example.test/.insteadOf", "gh:")
        self.assertEqual(self.texts(f"{PUSH} up feat"), [f"{PUSH} https://example.test/pub-org/other.git feat:feat"])
        git(r, "config", "url.ssh://git@example.test/.pushInsteadOf", "gh:")
        self.assertEqual(
            self.texts(f"{PUSH} up feat"),
            [
                f"{PUSH} ssh://git@example.test/pub-org/other.git feat:feat",
                f"{PUSH} https://example.test/pub-org/other.git feat:feat",
            ],
        )
        self.assertEqual(
            self.texts(f"{PUSH} gh:pub-org/x.git feat")[0], f"{PUSH} ssh://git@example.test/pub-org/x.git feat:feat"
        )
        # which remote without one written
        git(r, "config", "branch.main.remote", "up")
        self.assertIn("other.git main:main", self.texts(PUSH)[0])
        git(r, "config", "remote.pushDefault", "origin")
        self.assertIn("/r.git main:main", self.texts(PUSH)[0])
        git(r, "config", "branch.main.pushRemote", "up")
        self.assertIn("other.git main:main", self.texts(PUSH)[0])
        self.assertIn("other.git main:main", self.texts(f"{PUSH} --repo=up")[0])
        # a remote the line computes: every configured remote
        got = self.texts(f'{PUSH} "$R" feat')
        self.assertTrue(any("/r.git" in t for t in got) and any("other.git" in t for t in got), got)

    def test_push_default_and_config_refspecs(self):
        r = self.repo
        git(r, "checkout", "-q", "feat")
        cases = {
            None: ["feat:feat"],
            "current": ["feat:feat"],
            "matching": ["main:main", "feat:feat"],
            "nothing": [],
        }
        for mode, want in cases.items():
            if mode:
                git(r, "config", "push.default", mode)
            self.assertEqual([t.rsplit(" ", 1)[1] for t in self.texts(PUSH)], want, mode)
        git(r, "config", "push.default", "upstream")
        git(r, "config", "branch.feat.merge", "refs/heads/main")
        self.assertEqual(self.texts(PUSH)[0].rsplit(" ", 1)[1], "feat:main")
        git(r, "config", "--unset", "branch.feat.merge")
        self.assertEqual(self.texts(PUSH)[0].rsplit(" ", 1)[1], "feat:feat")
        git(r, "config", "remote.origin.push", "refs/heads/feat:refs/heads/main")
        self.assertEqual(self.texts(PUSH)[0].rsplit(" ", 1)[1], "feat:main")
        git(r, "config", "push.followTags", "true")
        self.assertIn(f"{PUSH} https://example.test/pub-org/r.git refs/tags/*:refs/tags/* --tags", self.texts(PUSH))

    def test_detached_head_and_worktree(self):
        git(self.repo, "checkout", "-q", "--detach")
        self.assertEqual(self.texts(PUSH)[0].rsplit(" ", 1)[1], "main:main")
        self.assertEqual(self.texts(f"{PUSH} origin HEAD")[0].rsplit(" ", 1)[1], "main:main")
        wt = self.tmp / "wt"
        git(self.repo, "worktree", "add", "-q", str(wt), "feat")
        got = self.resolved(PUSH, wt)
        self.assertEqual(got, [f"{PUSH} https://example.test/pub-org/r.git feat:feat"])  # the repo's remote

    def test_refspec_forms(self):
        u = "https://example.test/pub-org/r.git"
        cases = {
            f"{PUSH} origin feat": ["feat:feat"],
            f"{PUSH} origin +feat:refs/heads/main": ["feat:main --force"],
            f"{PUSH} origin @": ["main:main"],
            f"{PUSH} origin :old": [":old --delete"],
            f"{PUSH} -d origin old": [":old --delete"],
            f"{PUSH} --delete origin old other": [":old --delete", ":other --delete"],
            f"{PUSH} origin tag v1.2": ["refs/tags/v1.2:refs/tags/v1.2"],
            f"{PUSH} origin refs/tags/v1:refs/tags/v1": ["refs/tags/v1:refs/tags/v1"],
            f"{PUSH} -f origin feat": ["feat:feat --force"],
            f"{PUSH} -uf origin feat": ["feat:feat --force"],
            f"{PUSH} --force-with-lease origin feat": ["feat:feat --force"],
            f"{PUSH} --force-with-lease=feat:abc origin feat": ["feat:feat --force"],
            f"{PUSH} -o ci.skip origin feat": ["feat:feat"],
            f"{PUSH} -oci.skip origin feat": ["feat:feat"],
            f"{PUSH} --push-option x origin feat": ["feat:feat"],
            f"{PUSH} --all origin": ["main:main", "main:main"],
            f"{PUSH} --mirror origin": [
                "main:main --force --mirror",
                "main:main --force --mirror",
                "refs/tags/*:refs/tags/* --tags --mirror",
            ],
            f"{PUSH} --tags origin feat": ["feat:feat", "refs/tags/*:refs/tags/* --tags"],
            f"{PUSH} --follow-tags": ["main:main", "refs/tags/*:refs/tags/* --tags"],
            f"{PUSH} origin :": ["main:main", "main:main"],
            f"{PUSH} origin 'refs/heads/*:refs/heads/*'": ["*:*", "main:main"],
            f"{PUSH} origin $B": ["main:main --force", "main:main --force"],
            f"{PUSH} -- origin feat": ["feat:feat"],
        }
        for cmd, want in cases.items():
            got = [t[len(f"{PUSH} {u} ") :] for t in self.texts(cmd)]
            self.assertEqual(got, want, cmd)
        self.assertEqual(self.texts(f"X=1 {PUSH} origin feat"), [f"X=1 {PUSH} {u} feat:feat"])
        self.assertEqual(self.texts(f"git log && {PUSH}x"), [])
        self.assertEqual(self.texts("git status"), [])
        self.assertEqual(self.texts("git"), [])
        self.assertEqual(self.texts("$G push"), [])

    def test_https_form(self):
        for url, want in (
            ("git@example.test:o/r.git", "https://example.test/o/r.git"),
            ("example.test:o/r", "https://example.test/o/r"),
            ("ssh://git@example.test:22/o/r.git", "https://example.test/o/r.git"),
            ("git+ssh://example.test/o/r", "https://example.test/o/r"),
            ("https://example.test/o/r.git", None),
            ("file:///srv/r.git", None),
            ("/srv/r.git", None),
            ("./a:b", None),
            ("origin", None),
        ):
            self.assertEqual(gitpush.https(url), want, url)


class WiringTest(PushCase):
    """The cwd reaches the guard from the hook event, from the hooks module and from `foreman guard --cwd`; a table
    run (no cwd) compares only the text, as before."""

    def test_hook_event_cwd(self):
        lib = self.tmp / "lib"
        (self.tmp / "projects.toml").write_text(f'[[project]]\nname = "fake"\nhome = "{self.home}"\nmode = "active"\n')
        ev = {"hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": {"command": f"cd {lib} && {PUSH}"}}
        r = self.hook("guard", ev)
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn(f"as git would do it from `{lib}`", r.stderr)
        self.assertIn("name it (`git push origin BRANCH`)", r.stderr)
        ev["tool_input"]["command"] = PUSH
        self.assertEqual(self.hook("guard", ev, cwd=lib).returncode, 2)  # the event's own cwd
        self.assertEqual(self.hook("guard", ev).returncode, 0)  # the home: no repo
        ev["tool_input"]["command"] = f"cd {self.own} && {PUSH}"
        self.assertEqual(self.hook("guard", ev).returncode, 0)

    def test_module_passes_cwd(self):
        self.write_toml(PUBLIC + "\n[deny.grants]\ndeploy = ['make deploy']\n")
        seen = []

        def spy(project, cmd, ctx=None):
            seen.append(dict(ctx or {}))

        with mock.patch.object(guard, "check", spy):
            hooks_mod.grant({"cwd": str(self.home), "session": "S1", "phase": "check", "command": "ls"})
        self.assertEqual(seen[0].get("cwd"), str(self.home))

    def test_cli_cwd(self):
        lib = self.tmp / "lib"
        r = self.cli("guard", "--cwd", str(lib), PUSH)
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("as git would do it", r.stdout)
        self.assertEqual(self.cli("guard", "--cwd", str(self.own), PUSH).returncode, 0)
        self.assertEqual(self.cli("guard", PUSH, cwd=lib).returncode, 2)  # default: the current folder
        self.assertEqual(self.cli("guard", PUSH).returncode, 0)  # from the home: no repo, no remote
        # Spanish
        self.write_toml(PUBLIC.replace('language = "en"', 'language = "es"'))
        r = self.cli("guard", "--cwd", str(lib), PUSH)
        self.assertIn("tal como la haría git desde", r.stdout)

    def test_table_rows_run_in_their_folder(self):
        """A row runs in its @DIR (~ expanded, relative to the table's folder), else in the project's home."""
        lib, own = self.tmp / "lib", self.tmp / "own"
        table = self.home / "guard.tsv"
        table.write_text(
            f"deny\tgit -C {lib} push origin main\n"  # from the home, -C still names the repo
            f"allow\t{PUSH} origin topic\n"  # the home: no repo, the remote as written
            f"deny@{lib}\t{PUSH}\n"
            f"deny@../lib\t{PUSH} origin topic\n"  # relative to the table's folder
            f"allow@{own}\t{PUSH}\n"
            f"allow@../own\tgit -C ../lib status\n"
            f"deny@../nowhere\t{PUSH} origin topic\n"  # an unknown folder: every repo of the project
        )
        counts, failures = guard.run_table(self.project, table)
        self.assertEqual((counts, failures), ({"deny": 4, "allow": 3}, []))
        table.write_text(f"allow@{lib}\t{PUSH}\ndeny@../own\t{PUSH}\n")
        _, failures = guard.run_table(self.project, table)
        self.assertEqual(
            failures,
            [
                f"guard.tsv:1: want allow@{lib}, got deny: {PUSH!r}",
                f"guard.tsv:2: want deny@{own}, got allow: {PUSH!r}",
            ],
        )
        self.assertEqual(guard.as_role("agent"), None)
        self.assertEqual(guard.as_role("agent", "/x"), {"cwd": "/x"})
        self.assertEqual(guard.as_role("orchestrator", "/x")["cwd"], "/x")

    def test_table_both_roles_get_the_folder(self):
        """With [deny.grants], the orchestrator's run of a row gets its folder too."""
        self.write_toml(
            PUBLIC.split("[deny]")[0]
            + "[deny]\npatterns = []\n\n[deny.grants]\npub = ['git push \\S+pub-org\\S* main:main']\n"
        )
        lib = self.tmp / "lib"
        table = self.home / "guard.tsv"
        table.write_text(f"orchestrator@{lib}\t{PUSH}\nallow\t{PUSH}\n")
        self.assertEqual(guard.run_table(self.project, table), ({"orchestrator": 1, "allow": 1}, []))

    def test_parse_table_folder(self):
        home = os.path.expanduser("~")
        rows = guard.parse_table("deny@~/x\tls\nallow@a/../b\tls\nlock:gpu@/abs\tls\nallow\tls\n", "/base")
        self.assertEqual([r[3] for r in rows], [os.path.join(home, "x"), os.path.normpath("/base/b"), "/abs", None])
        self.assertEqual([r[1] for r in rows], ["deny", "allow", "lock:gpu", "allow"])
        self.assertEqual(guard.parse_table("deny@rel\tls\n")[0][3], "rel")  # no base: as written
        for bad in ("deny@\tls\n", "nope@/x\tls\n", "deny@/x\n"):
            with self.assertRaisesRegex(ValueError, r"\[@DIR\]"):
                guard.parse_table(bad)

    def test_orchestrator_lists_see_the_push(self):
        """[deny.grants] patterns read the resolved push too."""
        self.write_toml(
            PUBLIC.split("[deny]")[0]
            + "[deny]\npatterns = []\n\n[deny.grants]\norchestrator = ['git push \\S+pub-org\\S* main:main']\n"
        )
        lib = self.tmp / "lib"
        v = guard.check(self.project, PUSH, {"session": "S", "agent": "a", "cwd": str(lib)})
        self.assertEqual((v[0], v[1].why, v[1].where), ("orchestrator", "agent", "resolved"))
        ctx = {**guard.as_role("orchestrator"), "cwd": str(lib)}
        self.assertIsNone(guard.check(self.project, PUSH, ctx))
        self.assertEqual(ctx["passed"].where, "resolved")
        self.assertIsNone(guard.check(self.project, PUSH, {**guard.as_role("orchestrator"), "cwd": str(self.home)}))
        self.write_toml(
            PUBLIC.split("[deny]")[0] + "[deny]\npatterns = []\n\n[deny.grants]\npub = ['git push \\S+pub-org']\n"
        )
        r = gitpush.Resolver(self.project, str(lib))
        self.assertEqual(grants.groups_of(self.project, PUSH, r), ["pub"])
        self.assertEqual(grants.groups_of(self.project, PUSH), [])

    def test_resolver_failure_is_a_deny(self):
        """A failure inside the resolver is never a pass (the whole-text fallback would not see the remote)."""
        lib = self.tmp / "lib"
        with mock.patch.object(gitpush.Resolver, "resolve", side_effect=RuntimeError("x")):
            v = guard.check(self.project, f"cd {lib} && {PUSH} origin feature", {"cwd": str(self.home)})
            self.assertEqual(v, ("deny", "(the guard could not check this command: Failed)"))
            self.assertIsNone(guard.check(self.project, "ls", {"cwd": str(self.home)}))


class SwitchTest(PushCase):
    """A branch switched earlier on the same line is the one pushed, only when the switch surely ran there."""

    def setUp(self):
        super().setUp()
        self.write_toml(PRIVATE)
        self.api = make_repo(self.tmp / "api")
        remote(self.api, "https://example.test/owner/api.git")

    def test_switch_then_push(self):
        """As the orchestrator: api is the main checkout of a [repos.*] path, where an agent switches no branch."""
        api = self.api
        self.assertEqual(self.verdict(f"git switch -q -c chore/x && {PUSH} -u origin HEAD", api), "orchestrator")
        allow = [
            f"git switch -q -c chore/x && git commit -qm x && {PUSH} -q -u origin HEAD",
            f"git checkout -b fix/y && {PUSH} -u origin HEAD",
            f"git switch --create=fix/z && {PUSH}",
            f"cd {api} && git switch -c fix/w && {PUSH} -u origin @",
            f"git -C {api} switch -c fix/v && git -C {api} push -u origin HEAD",
        ]
        for cmd in allow:
            self.assertEqual(self.verdict(cmd, api, "orchestrator"), "allow", cmd)
        deny = [
            f"git switch -c fix/x; {PUSH} -u origin HEAD",  # the switch may fail: main is still possible
            f"git switch -c fix/x || true && {PUSH} -u origin HEAD",
            f"ls || git switch -c fix/x && {PUSH} -u origin HEAD",
            f"(git switch -c fix/x) && {PUSH} -u origin HEAD",
            f"git checkout fix/x && {PUSH} -u origin HEAD",  # may restore a file named fix/x
            f"git switch --detach && {PUSH} origin HEAD",
            f'git switch -c "$B" && {PUSH} origin HEAD',
            f"git -C {self.own} switch -c fix/x && {PUSH} origin HEAD",  # another folder
            f"git switch -c fix/x && {PUSH} origin main",
            f"git switch -c fix/x && git switch main && {PUSH} origin HEAD",
        ]
        for cmd in deny:
            self.assertEqual(self.verdict(cmd, api, "orchestrator"), "deny", cmd)
        self.assertEqual(self.verdict(f"git checkout -- x && {PUSH} origin HEAD", api, "orchestrator"), "deny")
        self.assertEqual(self.verdict(f"git switch && {PUSH} origin fix/q", api, "orchestrator"), "allow")


class SameLineTest(PushCase):
    """Config the line changes before the push (`git -c`, --git-dir, GIT_DIR, an earlier `git config` or `git
    remote`), and what cannot be read that way, which counts as an unknown folder."""

    PUB = "https://example.test/pub-org/x.git"

    def test_dash_c(self):
        own = self.own
        self.assertEqual(self.verdict(PUSH, own), "allow")  # the baseline: its remote names no rule
        for cmd in (
            f"git -c remote.origin.url={self.PUB} push",
            f"git -c Remote.origin.URL={self.PUB} push origin main",  # section and name in any case
            f"git -c remote.origin.pushurl={self.PUB} push",
            "git -c url.https://example.test/pub-org/.insteadOf=https://example.test/me/ push",
            "git -c url.https://example.test/pub-org/.pushInsteadOf=https://example.test/me/ push",
            f"git -c remote.pushDefault=pub -c remote.pub.url={self.PUB} push",
        ):
            self.assertEqual((self.verdict(cmd, own), self.where(cmd)), ("deny", "resolved"), cmd)
        for cmd in (
            "git -c include.path=/elsewhere push",
            "git -c includeIf.onbranch:main.path=/elsewhere push",
            "git --config-env=remote.origin.url=U push",
            'git -c "remote.origin.url=$U" push',
            'git "--config-env=$K" push',
        ):
            self.assertEqual((self.verdict(cmd, own), self.where(cmd)), ("deny", "unknown"), cmd)
        for cmd in (
            "git -c user.name=x -c core.sshCommand=ssh push origin main",
            "git -c remote.origin.url push",  # no value: true, a name git cannot reach
            "git -c nodot=1 push",
            "git -c push",  # "push" is the value of -c: no subcommand, git runs nothing
        ):
            self.assertEqual(self.verdict(cmd, own), "allow", cmd)

    def test_git_dir(self):
        own, lib = self.own, self.tmp / "lib"
        for cmd in (
            f"git --git-dir={lib}/.git push origin main",
            f"git --git-dir {lib}/.git push",
            f"GIT_DIR={lib}/.git {PUSH}",
            f"env GIT_DIR={lib}/.git {PUSH}",
            "git --git-dir=../lib/.git push",  # relative to the folder
        ):
            self.assertEqual((self.verdict(cmd, own), self.where(cmd)), ("deny", "resolved"), cmd)
        for cmd in (
            f"GIT_DIR=$D {PUSH}",
            f"git --git-dir=$D {PUSH[4:]}",
            f"git --git-dir={self.tmp}/missing push",  # it may be made earlier on the line
            f"GIT_DIR={lib}/.git sh -c '{PUSH}'",  # a nested push may read it: not followed
        ):
            self.assertEqual((self.verdict(cmd, own), self.where(cmd)), ("deny", "unknown"), cmd)
        self.assertEqual(self.verdict(f"git --git-dir={own}/.git push", self.tmp / "lib"), "allow")  # own's repo
        self.assertEqual(self.verdict("git --git-dir=.git push", own), "allow")
        # the work tree changes neither the repo nor its config
        self.assertEqual(self.verdict(f"git --work-tree={lib} push", own), "allow")
        self.assertEqual(self.verdict(f"GIT_WORK_TREE={lib} {PUSH}", own), "allow")

    def test_environment(self):
        own = self.own
        for cmd in (
            f"GIT_CONFIG_GLOBAL=/x {PUSH}",
            f"GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=remote.origin.url GIT_CONFIG_VALUE_0={self.PUB} {PUSH}",
            f"GIT_CONFIG_PARAMETERS=x {PUSH}",
            f"HOME=/x {PUSH}",
            f"env XDG_CONFIG_HOME=/x {PUSH}",
            f"GIT_COMMON_DIR=/x {PUSH}",
            f"export GIT_DIR={self.tmp}/lib/.git; {PUSH}",
            f"export HOME=/x && {PUSH}",
            f"GIT_CONFIG_GLOBAL=/x; {PUSH}",  # it may be exported already
            f"export GIT_CONFIG_GLOBAL=/x && sh -c '{PUSH}'",
        ):
            self.assertEqual((self.verdict(cmd, own), self.where(cmd)), ("deny", "unknown"), cmd)
        self.assertEqual(self.verdict(f"LC_ALL=C GIT_TRACE=1 {PUSH}", own), "allow")
        self.assertEqual(self.verdict(f"export FOO=1 && {PUSH}", own), "allow")

    def test_earlier_on_the_line(self):
        own = self.own
        for cmd in (
            f"git remote set-url origin {self.PUB} && {PUSH}",
            f"git remote set-url --push origin {self.PUB}; {PUSH}",
            f"git remote add pub {self.PUB} && {PUSH} pub main",
            f"git remote add -f -t main pub {self.PUB} && {PUSH} pub main",
            f"git config remote.origin.url {self.PUB} && {PUSH}",
            f"git config --add remote.origin.pushurl {self.PUB} && {PUSH}",
            f"git config set remote.origin.url {self.PUB} && {PUSH}",
            f"git config --global url.{self.PUB}.insteadOf https://example.test/me/own.git && {PUSH}",
            f"(git remote set-url origin {self.PUB}) || true; {PUSH}",  # anywhere before: it may have run
            f"git remote set-url origin {self.PUB} && sh -c '{PUSH}'",  # a nested push sees it
        ):
            self.assertEqual((self.verdict(cmd, own), self.where(cmd)), ("deny", "resolved"), cmd)
        self.assertEqual(self.verdict(f"git config alias.p '!git push {self.PUB}' && git p", own), "deny")
        for cmd in (
            f"git remote rename origin old && {PUSH}",
            f"git remote set-url --delete origin x && {PUSH}",
            f"git config --file .git/config remote.origin.url {self.PUB} && {PUSH}",
            f"git config -e && {PUSH}",
            f"git config edit && {PUSH}",
            f"git config --rename-section remote.origin remote.x && {PUSH}",
            f"git config remote.origin.url $U && {PUSH}",
            f"git remote set-url origin $U && {PUSH}",
            f"git config include.path /x && {PUSH}",
            f"git branch -u up/main && {PUSH}",
            f"git branch --set-upstream-to=up/main && {PUSH}",
            f"git checkout --track up/x && {PUSH}",
            f"git $SUB remote.origin.url x && {PUSH}",
        ):
            self.assertEqual((self.verdict(cmd, own), self.where(cmd)), ("deny", "unknown"), cmd)
        for cmd in (
            f"git config --get remote.origin.url && {PUSH}",
            f"git config remote.origin.url && {PUSH}",  # a read
            f"git config get remote.origin.url && {PUSH}",
            f"git config --list && {PUSH}",
            f"git config --file x.cfg --get a.b && {PUSH}",
            f"git config user.name x && {PUSH}",
            f"git remote -v && git remote show origin && {PUSH}",
            f"git remote add && {PUSH}",
            f"{PUSH} && git remote set-url origin {self.PUB}",  # after the push
            f"git config nodot x && {PUSH}",
            f"git config --unset nodot && {PUSH}",
            f"git status && git log -1 && {PUSH}",
        ):
            self.assertEqual(self.verdict(cmd, own), "allow", cmd)

    def test_dropped_keys(self):
        """A push URL that hides the remote's URL: dropping it on the line pushes to the URL."""
        own = self.own
        git(own, "config", "remote.origin.url", self.PUB)
        git(own, "config", "remote.origin.pushurl", "https://example.test/me/safe.git")
        self.assertEqual(self.verdict(PUSH, own), "allow")
        for cmd in (
            f"git config --unset remote.origin.pushurl && {PUSH}",
            f"git config unset --all remote.origin.pushurl && {PUSH}",
        ):
            self.assertEqual((self.verdict(cmd, own), self.where(cmd)), ("deny", "resolved"), cmd)
        cmd = f"git remote remove origin; git remote add origin https://example.test/me/new.git && {PUSH}"
        self.assertEqual(self.verdict(cmd, own), "allow")  # the section is gone: only the new URL
        cmd = f"git config --remove-section remote.origin && git remote add origin {self.PUB} && {PUSH}"
        self.assertEqual(self.verdict(cmd, own), "deny")
        self.assertEqual(self.verdict(f"git config --remove-section core && {PUSH}", own), "allow")

    def test_mirror(self):
        """A remote added with --mirror=push mirrors on a plain push (remote.<name>.mirror)."""
        own = self.own
        cmd = f"git remote add --mirror=push bk https://example.test/me/bk.git && {PUSH} bk"
        segs, _ = lex(cmd)
        words_of = [[t for t in s.toks if t.kind == "word"] for s in segs]
        info = [runs(w) for w in words_of]
        r = gitpush.Resolver(self.project, str(own))
        before = gitpush.changes(words_of, info)
        got = r.resolve(words_of[1], info[1].prog, r.start, None, before[1])
        self.assertIn(f"{PUSH} https://example.test/me/bk.git main:main --force --mirror", [t for t, _ in got])
        git(own, "config", "remote.origin.mirror", "true")
        self.assertTrue(any(t.endswith("--tags --mirror") for t in self.resolved(PUSH, own)))

    def test_unreadable_line(self):
        """A line the lexer cannot read that has `git … push`: every branch and tag of every repo is tried."""
        own, lib = self.own, self.tmp / "lib"
        cmd = f"cd {lib} && {PUSH} $'x"
        self.assertEqual(self.verdict(cmd), "allow")  # no cwd: only the text, as before
        self.assertEqual((self.verdict(cmd, own), self.where(cmd)), ("deny", "unknown"))
        self.assertIn(self.hit(cmd, own).dir, {str(self.home), str(lib)})  # a repo of the project
        self.assertEqual(self.verdict("echo $'x", own), "allow")  # no push
        with mock.patch.object(gitpush.Resolver, "blind", side_effect=RuntimeError("x")):
            self.assertEqual(self.verdict(cmd, own), "deny")


class AliasTest(PushCase):
    """A git alias that runs a push is resolved as that push; a `!` alias is compared as a command."""

    def setUp(self):
        super().setUp()
        self.lib = self.tmp / "lib"
        git(self.lib, "config", "alias.p", "push")
        git(self.lib, "config", "alias.pp", "p origin main")
        git(self.lib, "config", "alias.pf", "--no-pager push --force")
        git(self.own, "config", "alias.ship", f"!git push {SameLineTest.PUB}")
        git(self.own, "config", "alias.mine", "!git push")
        git(self.own, "config", "alias.loop", "loop2")
        git(self.own, "config", "alias.loop2", "loop")
        git(self.own, "config", "alias.bad", "push 'unclosed")
        git(self.own, "config", "alias.st", "status")

    def test_alias_to_push(self):
        lib, own = self.lib, self.own
        for cmd, cwd in (
            ("git p", lib),
            ("git P", lib),  # alias names are case-insensitive
            ("git pp", lib),
            ("git pf origin feature", lib),
            (f"git -C {lib} p", self.home),
            (f"cd {lib} && git pp", self.home),
            ("git $SUB origin main", lib),  # a computed subcommand may be push
        ):
            self.assertEqual((self.verdict(cmd, cwd), self.where(cmd, cwd)), ("deny", "resolved"), cmd)
        self.assertIn(f"{PUSH} https://example.test/pub-org/lib.git feature:feature --force",
                      self.resolved("git pf origin feature", lib))  # fmt: skip
        for cmd in ("git loop", "git bad", "git st", "git nosuch", "git $SUB", "git --version", "git -C"):
            self.assertEqual(self.verdict(cmd, own), "allow", cmd)

    def test_shell_alias(self):
        own = self.own
        self.assertEqual((self.verdict("git ship", own), self.where("git ship", own)), ("deny", "command"))
        self.assertEqual(self.verdict("git mine", own), "allow")  # its own remote
        self.assertEqual(self.verdict("git mine", self.lib), "allow")  # no alias there
        git(self.lib, "config", "alias.mine", "!git push")
        self.assertEqual(self.verdict("git mine", self.lib), "deny")
        self.assertEqual(self.verdict(f"git -c 'alias.go=!git push {SameLineTest.PUB}' go", own), "deny")
        self.assertEqual(self.verdict("git -c alias.go=status go", own), "allow")

    def test_unknown_folder_and_global_aliases(self):
        (self.tmp / "gitconfig").write_text("[alias]\n\tgp = push origin main\n")
        self.assertEqual(self.verdict("git gp", self.own), "allow")
        self.assertEqual(self.verdict("git gp", self.lib), "deny")
        self.assertEqual((self.verdict('cd "$X" && git p', self.home), self.where('cd "$X" && git p', self.home)),
                         ("deny", "unknown"))  # fmt: skip

    def test_builtins_run_no_git(self):
        with mock.patch.object(gitpush, "read_repo", side_effect=AssertionError("git was run")):
            for cmd in ("git status", "git -C x log -1", "git commit -m push", "git config alias.p push"):
                self.assertEqual(self.verdict(cmd, self.own), "allow", cmd)
