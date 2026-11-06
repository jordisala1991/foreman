"""Eleven known holes of the guard, one test each (H1-H11), and the controls that must keep their verdict when they are
closed.

Each hole test asserts the verdict the guard gives now that the hole is closed (they were written as expected
failures while it was open). Every hole has a control next to it: it proves the fixture reaches the rule (the same
command without the disguise is refused), so a hole test fails for the hole and not for a broken fixture.

- H1-H2 (main checkout): a `cd` to a folder that does not exist followed by `;`, and a folder the line cannot name
  (`"$WT"` may be empty: the current folder; FoldersTest.test_cd_and_lists in test_guard_main_checkout.py agrees).
- H3-H6 (where a push goes): config changed earlier on the line by a command that may not have run (a removal, a
  single-valued key), a `!` alias under --git-dir/GIT_DIR, a global alias outside a repo.
- H7-H11 (programs): a way to builtins through a module's attribute and a full-width `＿`, a module the program writes
  under a computed name and then imports, a file with a computed name the line then runs, a path with a space in a
  literal, and an interpreter (python, node) that writes a reserved file.

Commands that name a push or a publish are only passed to guard.check as strings; nothing here runs them.
"""

import unittest

from helpers import git, make_repo
from test_guard_bash_writes import BashWriteCase
from test_guard_files import TABLE
from test_guard_main_checkout import MainCheckoutCase
from test_guard_push import NOWHERE, PRIVATE, PUSH, PushCase, remote

from foreman import guard, pyscript

PUBLISH = "npm " + "publish"  # built in pieces, as test_guard_push.py does with its push
ENV = "(^|[\\s/'\"=<])\\.env($|[\\s'\"])"  # the secrets rule of a machine's [machine.deny.grants]
ENV_TOML = ENV.replace("\\", "\\\\").replace('"', '\\"')
PY_TOML = f"""
[project]
name = "fake"
owner = "Owner"
language = "en"

[deny]
patterns = ["{PUBLISH}", "{ENV_TOML}"]
"""


def py(body, head="python3 -"):
    """A Python program read from a quoted heredoc."""
    return f"{head} <<'EOF'\n{body}\nEOF"


class MainCheckoutHolesTest(MainCheckoutCase):
    """H1, H2 and their controls, as an agent (MainCheckoutCase: `top` is a main checkout, `wt` a worktree of it)."""

    def test_controls(self):
        top, wt = self.top, self.wt
        self.assertEqual(self.v("git merge main", top), "orchestrator")  # the rule H1 and H2 slip past
        self.assertEqual(self.v(f"cd {top}; git merge main", top), "orchestrator")
        for cmd in (f"cd {wt} && git merge main", "git -C ~/nowhere merge main"):
            with self.subTest(cmd=cmd):
                self.assertEqual(self.v(cmd, top), "allow")
        for cmd in ('git -C "$X" merge main', 'cd "$X" && git merge main'):  # from a worktree they never reach top
            with self.subTest(cmd=cmd):
                self.assertEqual(self.v(cmd, wt), "allow")

    def test_h1_cd_to_a_missing_folder_then_a_semicolon(self):
        """A `cd` that fails leaves the shell where it was: the merge runs in the main checkout."""
        gone = self.tmp / "wt/ours-gone"
        self.assertFalse(gone.exists())
        self.assertEqual(self.v(f"cd {gone}; git merge main", self.top), "orchestrator")

    def test_h2_a_folder_the_line_cannot_name(self):
        """An empty "$WT" is the current folder, the main checkout (the hole wins over two rows that pass it today)."""
        cases = ('git -C "$WT" merge main', 'cd "$WT" && git merge main', "git -C $WT merge main")
        got = {cmd: self.v(cmd, self.top) for cmd in cases}
        self.assertEqual(got, dict.fromkeys(got, "orchestrator"))


class PushHolesTest(PushCase):
    """H3-H6 and their controls, with test_guard_push.py's PUBLIC list (`git push .*pub-org`, tags and
    --follow-tags): lib's origin is pub-org, own's is a remote no rule names."""

    SAFE = "https://example.test/me/safe.git"
    PUB = "https://example.test/pub-org/x.git"

    def setUp(self):
        super().setUp()
        self.lib = self.tmp / "lib"
        self.hidden = make_repo(self.tmp / "hidden")  # its push URL is public, its URL is not
        remote(self.hidden, self.SAFE)
        git(self.hidden, "config", "remote.origin.pushurl", self.PUB)
        self.tagged = make_repo(self.tmp / "tagged")  # a safe remote, but every push sends tags
        remote(self.tagged, self.SAFE)
        git(self.tagged, "config", "push.followTags", "true")
        self.plain = self.tmp / "plain"  # a folder that is no repo
        self.plain.mkdir()

    def global_alias(self, name, value):
        with open(self.tmp / "gitconfig", "a") as f:
            f.write(f"[alias]\n\t{name} = {value}\n")

    def test_controls(self):
        for repo in (self.lib, self.hidden, self.tagged):
            with self.subTest(repo=repo.name):
                self.assertEqual(self.verdict(PUSH, repo), "deny")
        self.assertEqual(self.verdict(PUSH, self.own), "allow")
        # an unset that surely ran: the push goes to the safe URL (only one that may not have run counts both states)
        self.assertEqual(self.verdict(f"git config --unset remote.origin.pushurl && {PUSH}", self.hidden), "allow")
        cmd = f"git remote remove origin; git remote add origin https://example.test/me/new.git && {PUSH}"
        self.assertEqual(self.verdict(cmd, self.own), "allow")  # test_guard_push.py's dropped keys: stays allowed
        self.global_alias("pu", "!git " + PUSH[4:])
        self.assertEqual(self.verdict("git pu", self.lib), "deny")
        self.global_alias("ship", f"!git -C {self.lib} " + PUSH[4:])
        self.assertEqual(self.verdict("git ship", self.own), "deny")

    def test_too_many_configs(self):
        """Each change that may not have run doubles the configs to try: past gitpush.STATES the push is refused."""
        maybe = "".join(f"false && git config a.k{i} x; " for i in range(6))
        self.assertEqual(self.verdict(maybe + PUSH, self.own), "allow")  # 64 configs, all of them safe
        self.assertEqual(self.verdict("false && git config a.k9 x; " + maybe + PUSH, self.own), "deny")

    def test_h3_a_removal_that_may_not_have_run(self):
        got = {
            (f"false && git remote remove origin; {PUSH}", "lib"): self.verdict(
                f"false && git remote remove origin; {PUSH}", self.lib
            ),
            (f"false && git config --unset remote.origin.pushurl; {PUSH}", "hidden"): self.verdict(
                f"false && git config --unset remote.origin.pushurl; {PUSH}", self.hidden
            ),
            (f"git -C /no/such/dir config --unset remote.origin.pushurl; {PUSH}", "hidden"): self.verdict(
                f"git -C /no/such/dir config --unset remote.origin.pushurl; {PUSH}", self.hidden
            ),
        }
        self.assertEqual(got, dict.fromkeys(got, "deny"))

    def test_h4_a_single_valued_key_that_may_not_have_changed(self):
        cases = (
            (f"false && git config remote.pushDefault safe; {PUSH}", self.lib),
            (f"false && git config push.default nothing; {PUSH}", self.lib),
            (f"false && git config push.followTags false; {PUSH}", self.tagged),
        )
        got = {(cmd, cwd.name): self.verdict(cmd, cwd) for cmd, cwd in cases}
        self.assertEqual(got, dict.fromkeys(got, "deny"))

    def test_h5_a_shell_alias_under_another_git_dir(self):
        self.global_alias("pu", "!git " + PUSH[4:])
        cases = (f"git --git-dir={self.lib}/.git pu", f"GIT_DIR={self.lib}/.git git pu")
        got = {cmd: self.verdict(cmd, self.own) for cmd in cases}
        self.assertEqual(got, dict.fromkeys(got, "deny"))

    def test_h6_a_global_alias_outside_a_repo(self):
        self.global_alias("ship", f"!git -C {self.lib} " + PUSH[4:])
        self.assertEqual(self.verdict("git ship", self.plain), "deny")

    def test_a_change_made_in_another_repo(self):
        """A removal that surely ran, but in another repo, leaves the push's repo as it was (review round 1, F2)."""
        own, hidden = self.own, self.hidden
        cases = (
            f"git -C {own} config --unset remote.origin.pushurl; {PUSH}",
            f"cd {own}; git config --unset remote.origin.pushurl; cd {hidden}; {PUSH}",
            f"git --git-dir={own}/.git config --unset remote.origin.pushurl; {PUSH}",
            f"GIT_DIR={own}/.git git config --unset remote.origin.pushurl; {PUSH}",
        )
        got = {cmd: self.verdict(cmd, hidden) for cmd in cases}
        self.assertEqual(got, dict.fromkeys(got, "deny"))
        cmd = f"git -C {hidden} config --unset remote.origin.pushurl && {PUSH}"  # in the push's own repo: it ran
        self.assertEqual(self.verdict(cmd, hidden), "allow")

    def test_a_function_runs_where_it_is_called(self):
        """A function's body is checked from the folder of each call too (review round 1, F3)."""
        self.assertEqual(self.verdict(f'G() {{ git "$@"; }}; cd {self.lib} && G {PUSH[4:]}', self.own), "deny")
        self.assertEqual(self.verdict(f'G() {{ git "$@"; }}; G {PUSH[4:]}', self.own), "allow")

    def test_a_function_sees_the_config_of_its_call(self):
        """What the line changes in git's config between a function's definition and its call counts for the call
        (review of the git holes, round 2, F9)."""
        cmd = f'G() {{ git "$@"; }}; git remote set-url origin {self.PUB}; G {PUSH[4:]}'
        self.assertEqual(self.verdict(cmd, self.own), "deny")
        cmd = f'G() {{ git "$@"; }}; git remote set-url origin {self.SAFE}; G {PUSH[4:]}'
        self.assertEqual(self.verdict(cmd, self.own), "allow")
        cmd = f'G() {{ git "$@"; }}; G remote set-url origin {self.PUB}; {PUSH}'  # a change the body makes
        self.assertEqual(self.verdict(cmd, self.own), "deny")


class SwitchControlTest(PushCase):
    """§2.2: a switch to an existing branch before a push keeps today's verdict (PRIVATE: main needs the owner)."""

    def setUp(self):
        super().setUp()
        self.write_toml(PRIVATE)
        self.api = make_repo(self.tmp / "api")
        remote(self.api, "https://example.test/owner/api.git")
        git(self.api, "branch", "feat")
        self.wt = self.tmp / "wt/api-a1"
        git(self.api, "worktree", "add", "-q", "--detach", str(self.wt))

    def test_switch_then_push(self):
        self.assertEqual(self.verdict(f"git switch feat && {PUSH}", self.wt), "allow")
        self.assertEqual(self.verdict(f"git switch feat && {PUSH} origin HEAD", self.wt), "allow")
        self.assertEqual(self.verdict(f"git switch feat; {PUSH} origin HEAD", self.wt), "deny")  # it may not have

    def test_a_function_sees_the_branch_of_its_call(self):
        """A switch between a function's definition and its call counts for the call (review round 2, F9)."""
        git(self.wt, "switch", "-q", "feat")
        self.assertEqual(self.verdict(f'G() {{ git "$@"; }}; G {PUSH[4:]} origin HEAD', self.wt), "allow")
        cmd = f'G() {{ git "$@"; }}; git switch main && G {PUSH[4:]} origin HEAD'
        self.assertEqual(self.verdict(cmd, self.wt), "deny")
        cmd = f'G() {{ git "$@"; }}; G switch main && {PUSH} origin HEAD'  # a switch the body makes
        self.assertEqual(self.verdict(cmd, self.wt), "deny")


class FunctionArgsTest(PushCase):
    """Over-refusal: a shell function that wraps git with "$@" and is called with a reading subcommand passes (it was
    read as a push: a subcommand the line computes); a call that may push, or one the line hides, stays refused."""

    toml = NOWHERE

    def test_a_function_that_reads(self):
        wrap = f'G() {{ git --git-dir {self.own}/.git "$@"; }}; '
        ok = (
            wrap + 'G grep -n "version_texts\\|def pending" 8e4b546 -- lib/ | head -60',
            wrap + "G log -1; G status --short",
            'L() { git -C x "$1"; }; L status',
        )
        refused = (
            wrap + "G " + PUSH[4:],
            wrap + "G grep x; G " + PUSH[4:] + " origin main",
            wrap + "G grep x; echo $(G " + PUSH[4:] + ")",  # a call the line hides in a substitution
            'G() { shift; git "$@"; }; G x ' + PUSH[4:],
            'G() { git "$@"; }; G "$SUB"',
            'git "$@"',  # no function: the line's own arguments
            # unquoted, the shell splits the argument; "$*" joins them: words the line computes (round 1, F1)
            f'G() {{ git $@; }}; G "{PUSH[4:]} origin main"',
            f'G() {{ git $*; }}; G "{PUSH[4:]} x"',
            f'G() {{ git $1; }}; G "{PUSH[4:]} x"',
            f'G() {{ git "$*"; }}; G {PUSH[4:]}',
        )
        for cmd in ok:
            with self.subTest(cmd=cmd):
                self.assertEqual(self.verdict(cmd, self.own), "allow")
        for cmd in refused:
            with self.subTest(cmd=cmd):
                self.assertEqual(self.verdict(cmd, self.own), "deny")


class FunctionMainCheckoutTest(MainCheckoutCase):
    def test_a_function_in_the_main_checkout(self):
        self.assertEqual(self.v('G() { git "$@"; }; G grep -n x -- lib/', self.top), "allow")
        self.assertEqual(self.v('G() { git "$@"; }; G merge main', self.top), "orchestrator")
        self.assertEqual(self.v('G() { git "$@"; }; G grep x; eval "G merge main"', self.top), "orchestrator")
        for body in ("git $1", "git $@"):  # unquoted: the shell splits "merge main" (review round 1, F1)
            with self.subTest(body=body):
                self.assertEqual(self.v(f'G() {{ {body}; }}; G "merge main"', self.top), "orchestrator")

    def test_a_function_runs_where_it_is_called(self):
        """Defined in the worktree, called after a cd to the main checkout (review round 1, F3)."""
        self.assertEqual(self.v(f'G() {{ git "$@"; }}; cd {self.top} && G merge main', self.wt), "orchestrator")
        self.assertEqual(self.v(f'G() {{ git "$@"; }}; cd {self.wt} && G merge main', self.wt), "allow")


class ProgramHolesTest(BashWriteCase):
    """H7-H11 and their controls: a product whose list has a publish and the secrets rule; its foreman.toml and guard
    table are reserved files (FileCase)."""

    toml = PY_TOML

    def deny(self, cmd, role="agent"):
        """The rule or the reserved file's class that refuses cmd from the home, or "allow"."""
        v = guard.check(self.project, cmd, guard.as_role(role, str(self.home)))
        if v is None:
            return "allow"
        return f"file:{v[1].kind}" if v[0] == "file" else v[1]

    def test_controls(self):
        cases = {
            py(f'import subprocess\nsubprocess.run("{PUBLISH}", shell=True)'): PUBLISH,
            py(
                f'from pathlib import Path\nPath("difflib.py").write_text("import os; os.system(\'{PUBLISH}\')")\n'
                "import difflib"
            ): PUBLISH,  # fmt: skip
            py(f'from pathlib import Path\nPath("x.sh").write_text("{PUBLISH}")') + "\nsh x.sh": PUBLISH,
            py('print(open("/opt/MyApp/.env").read())'): ENV,
            'cat "/opt/My App/.env"': ENV,
            "sed -i '' 's/a/b/' foreman.toml": "file:config",
            f"echo x >> {TABLE}": "file:table",
            # what stays allowed (§2.2): reading a reserved file, writing an ordinary one with a rule's words in it
            py("import tomllib\nprint(tomllib.load(open('foreman.toml', 'rb')))"): "allow",
            py(
                f'from pathlib import Path\np = Path("ops/rules.md")\np.write_text(p.read_text().replace("x", '
                f'"never {PUBLISH}"))'
            ): "allow",  # fmt: skip
            py(
                f'import re\ns = open("CHANGELOG.md").read()\nopen("CHANGELOG.md", "w").write(re.sub("a", '
                f'"{PUBLISH} later", s))',
                head="python3 -I -",
            ): "allow",  # fmt: skip
        }
        for cmd, want in cases.items():
            with self.subTest(cmd=cmd):
                self.assertEqual(self.deny(cmd), want)

    def test_h7_builtins_through_a_module_or_a_full_width_underscore(self):
        cases = (
            py(
                'import re\nre.enum.bltns.eval("_"+"_import_"+"_(\'subprocess\').run(\'' + PUBLISH + "', shell=True)\")"
            ),
            py('_\uff3fimport_\uff3f("subprocess").run("' + PUBLISH + '", shell=True)'),
        )
        got = {cmd: self.deny(cmd) for cmd in cases}
        self.assertEqual(got, dict.fromkeys(got, PUBLISH))

    def test_h8_a_module_written_under_a_computed_name(self):
        payload = f"import os; os.system('{PUBLISH}')"
        cases = (
            py(f'from pathlib import Path\nPath("difflib").with_suffix(".py").write_text("{payload}")\nimport difflib'),
            py(f'from pathlib import Path\nPath("difflib" + ".py").write_text("{payload}")\nimport difflib'),
        )
        got = {cmd: self.deny(cmd) for cmd in cases}
        self.assertEqual(got, dict.fromkeys(got, PUBLISH))

    def test_h9_a_computed_file_the_line_runs(self):
        cmd = py(f'from pathlib import Path\nPath("hs.x"[::-1]).write_text("{PUBLISH}")') + "\nsh x.sh"
        self.assertEqual(self.deny(cmd), PUBLISH)

    def test_h10_a_path_with_a_space_in_a_literal(self):
        cmd = py('print(open("/opt/My App/.env").read())')
        self.assertEqual(self.deny(cmd), ENV)

    def test_h11_an_interpreter_that_writes_a_reserved_file(self):
        """Nobody writes a reserved file, whatever writes it: the agent and the orchestrator alike."""
        cases = {
            py("s = open('foreman.toml').read()\nopen('foreman.toml', 'w').write(s)"): "file:config",
            "python3 -c \"from pathlib import Path; p = Path('foreman.toml'); p.write_text('x')\"": "file:config",
            f"node -e \"require('fs').appendFileSync('{TABLE}', 'allow\\tls\\n')\"": "file:table",
        }
        got = {(cmd, role): self.deny(cmd, role) for cmd in cases for role in ("agent", "orchestrator")}
        self.assertEqual(got, {(cmd, role): cases[cmd] for cmd, role in got})

    def test_interpreters_that_read_or_write_elsewhere(self):
        """H11's rule by what the code writes: reading a reserved file passes; Python that passes the gate is judged
        by the files it names as written, other code by its quoted literals when it has a word that writes."""
        cases = {
            "node -e \"console.log(require('fs').readFileSync('foreman.toml', 'utf8'))\"": "allow",
            "python3 -c \"import sys; print(open('foreman.toml', 'rb').read(), file=sys.stderr)\"": "allow",
            py("s = open('foreman.toml').read()\nopen('notes.md', 'w').write(s)"): "allow",
            py("import json\nprint(json.dumps(open('foreman.toml').read()))", head="python3 -I -"): "allow",
            # a file it cannot name: any of its literals may be it
            py("import re\ns = open('foreman.toml').read()\nopen(re.sub('x', 'y', 'a'), 'w').write(s)"): "file:config",
            'perl -e \'open(my $f, ">", "foreman.toml")\'': "file:config",
            "python3 -c \"import os; os.rename('x', 'foreman.toml')\"": "file:config",  # not through the gate
            "cd tests && node -e \"require('fs').writeFileSync('guard.tsv', '')\"": "file:table",
            f'ruby -e \'File.write("{TABLE}", "")\'': "file:table",
        }
        got = {cmd: self.deny(cmd) for cmd in cases}
        self.assertEqual(got, cases)


class ProgramGateTest(unittest.TestCase):
    """pyscript's rules behind H7-H10: the gate (identifiers from the tree, a module's members), the files a
    program writes, and the words with a `/` kept from prose."""

    def test_gate(self):
        ok = (
            "import re\nr = re.compile('x')",
            "import collections.abc\nx = collections.abc.Mapping",
            "from collections import Counter as C",
            "import json as j\nj.dumps(1)",
            "x = '__init__.py'",  # a string is not an identifier
            "from pathlib import Path\nPath('x').read_text().replace('a', 'b')",
        )
        refused = (
            "import re\nre.enum",
            "import re\nre.enum.bltns.eval('1')",
            "import glob\nglob.os",
            "import json\njson.decoder",
            "from json import decoder",
            "from re import *",
            "import re\nm = re\nm.enum",
            "import re\nprint(re)",
            "x = 1\nx.eval('1')",  # a builtin's name as the attribute of something that is no module
            "_\uff3fimport_\uff3f('os')",  # Python reads a full-width low line as `_`
            "def f(__x): pass",
        )
        for body in ok:
            with self.subTest(body=body):
                self.assertIsNotNone(pyscript.gate(body))
        for body in refused:
            with self.subTest(body=body):
                self.assertIsNone(pyscript.gate(body))

    def test_writes(self):
        P = "from pathlib import Path\n"
        cases = {
            "print(open('a').read())": set(),
            "open('a', 'rb')": set(),
            "open('a', 'w')": {"a"},
            "open('a', mode='a+')": {"a"},
            "open(file='a', mode='x')": {"a"},
            "import json\njson.dump(1, open('a', 'w'))": {"a"},
            "with open('a', 'w') as f:\n    f.write('x')": {"a"},
            "open('a', m)": {"a"},  # a mode it cannot read: a write
            P + "p = Path('a')\np.write_text(p.read_text())": {"a"},
            P + "p = q = Path('a')\nq.touch()": {"a"},
            P + "Path('d', 'a').unlink()": {"d/a"},
            P + "Path('a').rename('b')": {"a", "b"},
            P + "Path('a').replace(target='b')": {"a", "b"},
            P + "Path('a').open('w')": {"a"},
            P + "Path('a').open()": set(),
            "import pathlib\npathlib.Path('a').mkdir()": {"a"},
            "s = 'a b'.replace('a', 'b')\nd = {}.copy()": set(),
            # what it cannot name
            "open(n, 'w')": None,
            "o = open\no('a', 'w')": None,
            "open(*a)": None,
            P + "p = Path('a')\np = Path(n)\np.touch()": None,
            P + "for p in [Path('a')]:\n    p.touch()": None,
            P + "w = Path('a').write_text\nw('x')": None,
            P + "Path('a').rename(**k)": None,
            P + "Path('a').with_suffix('.py').touch()": None,
            P + "Path('a' + 'b').touch()": None,
            P + "def Path(x):\n    return x\nPath('a').touch()": None,
            "from json import JSONDecoder as Path\nPath('a').touch()": None,
        }
        for body, want in cases.items():
            with self.subTest(body=body):
                tree = pyscript.gate(body)
                self.assertIsNotNone(tree)
                got = pyscript.writes(tree)
                self.assertEqual(None if got is None else set(got), want)

    def test_paths_in_prose(self):
        self.assertEqual(pyscript.blank('x = "/opt/My App/a b"\n'), "x = \"\"\n\n'/opt/My'\n'App/a'")
        self.assertEqual(pyscript.blank('x = "never git push"\n'), 'x = ""\n')


if __name__ == "__main__":
    unittest.main()
