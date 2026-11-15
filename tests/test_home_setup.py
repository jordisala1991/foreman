"""fm init sets up a home: the shared define-task skill and instructions linked from fm/shared/, the owner's files kept."""

import re
import shutil
import tempfile
import tomllib
import unittest
from pathlib import Path

from helpers import ROOT, fm

from fm import config, homesetup

SKILL = ".claude/skills/define-task/SKILL.md"
SHARED_FILES = (homesetup.SHARED / "instructions.md", homesetup.SHARED / "skills/define-task/SKILL.md")
PLACEHOLDERS = {"project", "prefix", "repo", "main", "fm", "slots"}
ROW = re.compile(r"^\| `\{(\w+)\}` \| `\[(\w+)\] (\w+)` \| (.+) \|$", re.M)


class HomeSetup(unittest.TestCase):
    def home(self) -> Path:
        d = Path(tempfile.mkdtemp(prefix="fmhome-")).resolve()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        return d

    def init(self, h: Path) -> str:
        return fm(h, "init", "--prefix", "ZZ", "--repo", "r")

    def test_new_home_has_skill_and_instructions(self) -> None:
        h = self.home()
        self.init(h)
        self.assertEqual((h / SKILL).read_text(), SHARED_FILES[1].read_text())
        self.assertEqual((h / ".claude/fm.md").read_text(), SHARED_FILES[0].read_text())
        self.assertIn("@.claude/fm.md", (h / "CLAUDE.md").read_text().splitlines())
        fm_bin = tomllib.loads((h / "fm.toml").read_text())["project"]["fm"]
        self.assertEqual(fm_bin, str(ROOT / "bin/fm"))
        self.assertTrue(Path(fm_bin).is_file())

    def test_owner_claude_md_is_kept(self) -> None:
        h = self.home()
        (h / "CLAUDE.md").write_text("Habla en español.\n")
        out = self.init(h)
        self.assertEqual((h / "CLAUDE.md").read_text(), "Habla en español.\n")
        self.assertIn("add the line @.claude/fm.md", out)
        (h / "CLAUDE.md").write_text("Habla en español.\n\n@.claude/fm.md\n")
        self.assertNotIn("does not pull in", self.init(h))

    def test_init_again_changes_nothing_that_exists(self) -> None:
        h = self.home()
        self.init(h)
        links = {p: (h / p).readlink() for p in (".claude/fm.md", ".claude/skills/define-task")}
        toml, claude_md = (h / "fm.toml").read_bytes(), (h / "CLAUDE.md").read_bytes()
        self.assertNotIn("linked", self.init(h))
        self.assertEqual({p: (h / p).readlink() for p in links}, links)
        self.assertEqual(((h / "fm.toml").read_bytes(), (h / "CLAUDE.md").read_bytes()), (toml, claude_md))

        h = self.home()
        (h / SKILL).parent.mkdir(parents=True)
        (h / SKILL).write_text("owner text\n")
        (h / "fm.toml").write_text('[project]\nname = "x"\nprefix = "ZZ"\nrepo = "r"\n')
        out = self.init(h)
        self.assertEqual((h / SKILL).read_text(), "owner text\n")
        self.assertIn(".claude/skills/define-task differs from the shared one: left as it is", out)
        self.assertIn("-owner text", out)
        self.assertTrue((h / ".claude/fm.md").is_symlink())
        self.assertEqual((h / "fm.toml").read_text(), '[project]\nname = "x"\nprefix = "ZZ"\nrepo = "r"\n')
        self.assertIn(f'fm = "{ROOT / "bin/fm"}"', out)

    def test_a_copy_of_the_shared_file_is_reported_as_a_copy(self) -> None:
        h = self.home()
        (h / ".claude").mkdir()
        shutil.copy(SHARED_FILES[0], h / ".claude/fm.md")
        self.assertIn(".claude/fm.md is a copy of the shared one: left as it is", self.init(h))
        self.assertFalse((h / ".claude/fm.md").is_symlink())

    def test_a_merge_reaches_the_home(self) -> None:
        shared = self.home()
        (shared / "skills/define-task").mkdir(parents=True)
        (shared / "instructions.md").write_text("old instructions\n")
        (shared / "skills/define-task/SKILL.md").write_text("old skill\n")
        saved, homesetup.SHARED = homesetup.SHARED, shared
        self.addCleanup(setattr, homesetup, "SHARED", saved)
        h = self.home()
        self.init(h)
        (shared / "instructions.md").write_text("new instructions\n")
        (shared / "skills/define-task/SKILL.md").write_text("new skill\n")
        self.assertEqual((h / ".claude/fm.md").read_text(), "new instructions\n")
        self.assertEqual((h / SKILL).read_text(), "new skill\n")

    def test_shared_files_are_generic(self) -> None:
        for f in SHARED_FILES:
            text = f.read_text()
            for word in ("Jordi", "foreman", "/Users/", "FN-", "ZZ", "English", "Spanish", "Catalan", *"áéíóúñ¿¡"):
                self.assertNotIn(word, text, f"{f.name}: {word}")
            self.assertLessEqual(set(re.findall(r"\{(\w+)\}", text)), PLACEHOLDERS, f.name)
            self.assertNotRegex(text, r"\{(?!(?:" + "|".join(PLACEHOLDERS) + r")\})[^}]*\}", f.name)

    def test_placeholders_match_the_table_and_real_keys(self) -> None:
        for f in SHARED_FILES:
            text = f.read_text()
            rows = ROW.findall(text)
            self.assertEqual({r[0] for r in rows}, set(re.findall(r"\{(\w+)\}", text)), f.name)
            for name, section, key, default in rows:
                self.assertTrue(key in config.DEFAULTS.get(section, {}) or (section, key) == ("project", "fm"), name)
                self.assertTrue(default.strip(), name)
            self.assertIn("read `fm.toml` in this folder; never use one from memory", text)
