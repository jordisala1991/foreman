"""Docs tied to the code: what the docs list must be what the code does (a row added to one and not to the other
fails here)."""

import argparse
import re
import shlex
import subprocess
import sys
import unittest

from helpers import ROOT, config_problems

from foreman import cli, commands, config, notices, toml_reader

LIB = ROOT / "lib/foreman"


def emitted_kinds():
    """Every kind the code emits: KINDS, and every `notice("kind", …)` in the package."""
    found = set(notices.KINDS)
    for f in LIB.glob("*.py"):
        found |= set(re.findall(r'\bnotice\(\s*"([a-z_]+)"', f.read_text()))
    return found


def documented_kinds():
    """The kinds in the first column of the notices table of docs/orchestrator.md."""
    kinds, in_table = set(), False
    for line in (ROOT / "docs/orchestrator.md").read_text().splitlines():
        if re.match(r"\s*\| kind \| actionable \|", line):
            in_table = True
            continue
        if in_table:
            if not line.strip().startswith("|"):
                break
            kinds |= set(re.findall(r"`([a-z_]+)`", line.split("|")[1]))
    return kinds


def points_table(doc):
    """{point: row cells} of the table of a doc whose header starts with `| `FOREMAN_POINT` |`."""
    rows, in_table = {}, False
    for line in (ROOT / doc).read_text().splitlines():
        if re.match(r"\s*\| `FOREMAN_POINT` \|", line):
            in_table = True
            continue
        if in_table:
            if not line.strip().startswith("|"):
                break
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            m = re.fullmatch(r"`([a-z_.]+)`", cells[0])
            if m:
                rows[m.group(1)] = cells
    return rows


def time_limit(point):
    t = commands.POINTS[point].timeout
    return "none" if t is None else f"{t:g} s"


def cited_foreman_commands(path):
    """The `foreman …` commands a page cites: inline code spans and the lines of fenced code blocks."""
    text = path.read_text()
    spans = re.findall(r"`([^`\n]+)`", re.sub(r"```.*?```", "", text, flags=re.S))
    for block in re.findall(r"```[a-z]*\n(.*?)```", text, flags=re.S):
        spans += block.splitlines()
    return {s.strip() for s in spans if re.match(r"foreman( |$)", s.strip())}


_helps = {}


def foreman_help(words):
    """(exit code, output) of `foreman WORDS… --help`, cached."""
    key = tuple(words)
    if key not in _helps:
        r = subprocess.run(
            [sys.executable, str(ROOT / "lib/main.py"), *words, "--help"], capture_output=True, text=True, check=False
        )
        _helps[key] = (r.returncode, r.stdout)
    return _helps[key]


def command_problems(command):
    """What is wrong with a cited `foreman …` command line: unknown subcommands or flags (empty: it exists)."""
    tokens = shlex.split(command)[1:]
    if tokens[:1] == ["--project"]:
        tokens = tokens[2:]
    words, rest = [], tokens
    while rest and re.fullmatch(r"[a-z][a-z-]*", rest[0]):
        _, out = foreman_help(words)
        if not out.startswith(f"usage: foreman {' '.join(words)}".rstrip()):
            break  # the last word was a positional choice (`permissions grants`): the rest are its arguments
        choices = re.search(r"positional arguments:\n\s+\{([\w,-]+)\}", out)
        if choices and rest[0] in choices.group(1).split(","):
            words.append(rest.pop(0))
            continue
        rc, own = foreman_help([*words, rest[0]])
        if rc == 0 and own.startswith(f"usage: foreman {' '.join([*words, rest[0]])}"):
            words.append(rest.pop(0))
            continue
        if choices:
            return [f"{command}: `foreman {' '.join(words)}` has no subcommand {rest[0]}"]
        break
    _, out = foreman_help(words)
    flags = [t.split("=")[0] for t in rest if t.startswith("--")]
    return [f"{command}: `foreman {' '.join(words)}` has no option {f}" for f in flags if f not in out]


class DocsTiedTest(unittest.TestCase):
    def test_points_are_the_table_rows(self):
        """The points table of docs/extending.md is commands.POINTS: every point a row, every row a point, each row's
        key and time limit the point's."""
        rows = points_table("docs/extending.md")
        self.assertTrue(rows, "the FOREMAN_POINT table of docs/extending.md was not found")
        self.assertEqual(sorted(set(commands.POINTS) - set(rows)), [], "in POINTS but not in docs/extending.md")
        self.assertEqual(sorted(set(rows) - set(commands.POINTS)), [], "in docs/extending.md but not in POINTS")
        for name, cells in points_table("docs/extending.md").items():
            with self.subTest(point=name):
                key = commands.POINTS[name].key
                self.assertIn(f"`{key.split(',')[0]}`", cells[1], "the key column")
                self.assertEqual(cells[-1], time_limit(name), "the time limit column")

    def test_docs_lists_every_page(self):
        """`foreman docs` lists every page of docs/, extending among them, and prints it."""
        out = subprocess.run(
            [sys.executable, str(ROOT / "lib/main.py"), "docs"], capture_output=True, text=True, check=False
        ).stdout
        self.assertEqual(out.strip(), "docs: " + ", ".join(sorted(f.stem for f in (ROOT / "docs").glob("*.md"))))
        r = subprocess.run(
            [sys.executable, str(ROOT / "lib/main.py"), "docs", "extending"],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual((r.returncode, r.stdout.splitlines()[0]), (0, "# Extending foreman without changing it"))

    def test_notice_kinds_are_the_table_rows(self):
        docs, code = documented_kinds(), emitted_kinds()
        self.assertTrue(docs, "the notices table of docs/orchestrator.md was not found")
        self.assertEqual(sorted(code - docs), [], "emitted but not in the table of docs/orchestrator.md")
        self.assertEqual(sorted(docs - code), [], "in the table of docs/orchestrator.md but never emitted")

    def test_every_command_is_in_the_readme(self):
        """The README's table of commands has every subcommand of the parser."""
        out = subprocess.run(
            [sys.executable, str(ROOT / "lib/main.py"), "--help"], capture_output=True, text=True, check=False
        ).stdout
        names = set(re.search(r"\{([\w,-]+)\}", out).group(1).split(","))
        self.assertGreater(len(names), 20)
        readme = (ROOT / "README.md").read_text()
        self.assertEqual(sorted(n for n in names if f"| `{n}` |" not in readme), [])

    def test_readme_table_is_generated_from_the_parser(self):
        """The README's table of commands is cli.command_table() as it is now, between its two markers: every command
        of the parser in exactly one COMMAND_GROUPS group, each with its own help text. A command added, removed or
        reworded without the README fails here; the message has the table to paste."""
        sp = next(a for a in cli.build_parser()._actions if isinstance(a, argparse._SubParsersAction))
        grouped = [n for names in cli.COMMAND_GROUPS.values() for n in names]
        self.assertEqual(sorted(grouped), sorted(sp.choices), "COMMAND_GROUPS must hold every command once")
        self.assertEqual(len(grouped), len(set(grouped)))
        self.assertEqual([a.dest for a in sp._choices_actions if not a.help], [], "a command without help")
        readme = (ROOT / "README.md").read_text()
        m = re.search(r"<!-- commands:[^\n]*-->\n(.*?)<!-- /commands -->", readme, flags=re.S)
        self.assertIsNotNone(m, "the README's command markers were not found")
        table = cli.command_table()
        self.assertEqual(m.group(1), table, "paste this table between the README's markers:\n" + table)

    def test_readme_and_docs_cite_only_real_commands(self):
        """Every `foreman …` the README and the docs pages cite exists, as for the setup guide below."""
        for page in [ROOT / "README.md", *sorted((ROOT / "docs").glob("*.md"))]:
            with self.subTest(page=page.name):
                cited = {
                    c
                    for c in cited_foreman_commands(page)
                    if not re.search(r"[…|/]", c) and not re.match(r"foreman (hook|approved)\b", c)
                }  # notation, and the hidden `hook` (and the old `approved`) that only hooks run
                self.assertEqual([p for c in sorted(cited) for p in command_problems(c)], [])

    def test_config_example_is_every_key(self):
        """The foreman.toml example of docs/config.md loads strictly, is written for this foreman's config schema and
        has every section and key of it (a key added without its line in the example fails here)."""
        text = (ROOT / "docs/config.md").read_text()
        block = text.split("## `foreman.toml` (in the home)", 1)[1].split("```toml\n", 1)[1].split("```", 1)[0]
        data = toml_reader.loads(block)
        self.assertEqual(config_problems(data), [])
        self.assertEqual(data["project"]["config_schema"], config.CONFIG_SCHEMA)

        def shown(key, table):  # set, or a commented line when its default is «unset»
            return key in table or re.search(rf"^# {key} = ", block, flags=re.M)

        missing = [f"{s}.{k}" for s, d in config.DEFAULTS.items() for k in d if not shown(k, data.get(s, {}))]
        missing += [f"repos.*.{k}" for k in config.REPO_DEFAULTS if not shown(k, next(iter(data["repos"].values())))]
        self.assertEqual(missing, [])
        notes = re.findall(r".{0,40}(?:\(schema \d+|schema \d+\)).{0,20}", text)
        self.assertEqual(notes, [], "no schema history notes in the reference")

    def test_setup_skill_cites_only_real_commands(self):
        """Every `foreman …` the setup guide cites (inline code and code blocks) exists: each subcommand word is a
        choice of its parent's --help (or has a --help of its own, as `panel service`), and each --flag is in the
        --help of the deepest command reached. A guide that cites a command foreman lacks strands the new owner."""
        cited = cited_foreman_commands(ROOT / "skills/setup/SKILL.md")
        self.assertGreater(len(cited), 10, cited)
        problems = [p for c in sorted(cited) for p in command_problems(c)]
        self.assertEqual(problems, [])

    def test_owner_skills_cite_only_real_commands(self):
        """The same for the owner skills (skills/owner-*): the orchestrator runs what they cite with the owner."""
        skills = sorted((ROOT / "skills").glob("owner-*/SKILL.md"))
        self.assertIn(ROOT / "skills/owner-backlog/SKILL.md", skills)
        for skill in skills:
            with self.subTest(skill=skill.parent.name):
                cited = cited_foreman_commands(skill)
                self.assertTrue(cited, skill)
                self.assertEqual([p for c in sorted(cited) for p in command_problems(c)], [])

    def test_cited_command_check_catches_mistakes(self):
        self.assertEqual(command_problems("foreman backlog decision add --title T --option O"), [])
        self.assertEqual(command_problems("foreman panel service install"), [])
        self.assertTrue(command_problems("foreman backlog decison add"))
        self.assertTrue(command_problems("foreman panel service install --open"))
        self.assertTrue(command_problems("foreman setup"))

    def test_every_module_is_in_the_module_map(self):
        text = (ROOT / "CONTRIBUTING.md").read_text()
        missing = [f.name for f in sorted(LIB.glob("*.py")) if f.name != "__init__.py" and f"`{f.name}`" not in text]
        self.assertEqual(missing, [])


if __name__ == "__main__":
    unittest.main()
