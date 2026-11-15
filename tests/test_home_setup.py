"""fm init sets up a home (fm.toml, backlog.json, instructions.md) and prints the two lines that install the plugin;
it makes no links. fm health warns about the links an older init made (FN-143)."""

import re
import shutil
import tempfile
import unittest
from pathlib import Path

from helpers import fm

from fm import health, plugin
from fm.config import Project

SHARED_FILES = (plugin.SHARED / "instructions.md", plugin.SHARED / "skills/define-task/SKILL.md")


class HomeSetup(unittest.TestCase):
    def home(self) -> Path:
        d = Path(tempfile.mkdtemp(prefix="fmhome-")).resolve()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        return d

    def init(self, h: Path) -> str:
        return fm(h, "init", "--prefix", "ZZ", "--repo", "r")

    def test_init_prints_the_two_install_lines_and_makes_no_links(self) -> None:
        h = self.home()
        out = self.init(h)
        for f in ("fm.toml", "backlog.json", "instructions.md"):
            self.assertTrue((h / f).is_file(), f)
        self.assertFalse((h / ".claude").exists())
        self.assertFalse((h / "CLAUDE.md").exists())
        self.assertIn("claude plugin marketplace add jordisala1991/foreman#foreman-next\nclaude plugin install fm@fm\n", out)
        self.assertNotIn("fm =", (h / "fm.toml").read_text())

        (h / "instructions.md").write_text("Habla en español.\n")
        (h / "CLAUDE.md").write_text("owner\n")
        before = {f: (h / f).read_bytes() for f in ("fm.toml", "backlog.json", "instructions.md", "CLAUDE.md")}
        self.assertIn("claude plugin install fm@fm", self.init(h))
        self.assertEqual({f: (h / f).read_bytes() for f in before}, before)
        self.assertFalse((h / ".claude").exists())

    def test_health_names_the_old_links(self) -> None:
        h = self.home()
        self.init(h)
        p = Project.load(h)
        self.assertEqual(health.old_links(p), [])
        (h / ".claude/skills").mkdir(parents=True)
        (h / ".claude/fm.md").write_text("a copy, not a link\n")
        self.assertEqual(health.old_links(p), [])
        (h / ".claude/fm.md").unlink()
        (h / ".claude/fm.md").symlink_to(SHARED_FILES[0])
        (h / ".claude/skills/define-task").symlink_to(SHARED_FILES[1].parent)
        self.assertEqual(
            health.old_links(p),
            [
                "! the home still has the old link .claude/fm.md (FN-143): the plugin replaces it; delete it",
                "! the home still has the old link .claude/skills/define-task (FN-143): the plugin replaces it; delete it",
            ],
        )
        (h / "CLAUDE.md").write_text("# x\n\n@.claude/fm.md\n")
        self.assertTrue(health.old_links(p)[0].endswith("delete it, and remove the line @.claude/fm.md from CLAUDE.md"))
        self.assertIn(".claude/skills/define-task (FN-143)", fm(h, "health"))
        (h / "CLAUDE.md").write_bytes(b"\xff\xfe\x00bad")
        self.assertEqual(len(health.old_links(p)), 2)

    def test_shared_files_are_generic(self) -> None:
        for f in SHARED_FILES:
            text = f.read_text()
            for word in ("Jordi", "foreman", "/Users/", "FN-", "ZZ", "English", "Spanish", "Catalan", *"áéíóúñ¿¡"):
                self.assertNotIn(word, text, f"{f.name}: {word}")
            self.assertLessEqual(set(re.findall(r"(?<!\$)\{(\w+)\}", text)), set(plugin.PLACEHOLDERS), f.name)
            self.assertNotRegex(text, r"(?<!\$)\{(?!(?:" + "|".join(plugin.PLACEHOLDERS) + r")\})[^}]*\}", f.name)
