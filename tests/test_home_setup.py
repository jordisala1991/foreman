"""fm init sets up a home (fm.toml, backlog.json, instructions.md) and prints the two lines that install the plugin;
it makes no links. fm health warns about the links an older init made (FN-143)."""

import functools
import re
import shutil
import tempfile
from pathlib import Path

import pytest
from helpers import fm

from foreman.core.project import plugin
from foreman.core.project.config import Project
from foreman.steps.health import notes

SHARED_FILES = (plugin.SHARED / "instructions.md", plugin.SHARED / "skills/define-task/SKILL.md")


class TestHomeSetup:
    @pytest.fixture(autouse=True)
    def keep_request(self, request):
        self.request = request  # for the cleanups of helpers

    def home(self) -> Path:
        d = Path(tempfile.mkdtemp(prefix="fmhome-")).resolve()
        self.request.addfinalizer(functools.partial(shutil.rmtree, d, ignore_errors=True))
        return d

    def init(self, h: Path) -> str:
        return fm(h, "init", "--prefix", "ZZ", "--repo", "r")

    def test_init_prints_the_two_install_lines_and_makes_no_links(self) -> None:
        h = self.home()
        out = self.init(h)
        for f in ("fm.toml", "backlog.json", "instructions.md"):
            assert (h / f).is_file(), f
        assert not (h / ".claude").exists()
        assert not (h / "CLAUDE.md").exists()
        assert "claude plugin marketplace add jordisala1991/foreman#foreman-next\nclaude plugin install fm@fm\n" in out
        assert "fm =" not in (h / "fm.toml").read_text()

        (h / "instructions.md").write_text("Habla en español.\n")
        (h / "CLAUDE.md").write_text("owner\n")
        before = {f: (h / f).read_bytes() for f in ("fm.toml", "backlog.json", "instructions.md", "CLAUDE.md")}
        assert "claude plugin install fm@fm" in self.init(h)
        assert {f: (h / f).read_bytes() for f in before} == before
        assert not (h / ".claude").exists()

    def test_health_names_the_old_links(self) -> None:
        h = self.home()
        self.init(h)
        p = Project.load(h)
        assert notes.old_links(p) == []
        (h / ".claude/skills").mkdir(parents=True)
        (h / ".claude/fm.md").write_text("a copy, not a link\n")
        assert notes.old_links(p) == []
        (h / ".claude/fm.md").unlink()
        (h / ".claude/fm.md").symlink_to(SHARED_FILES[0])
        (h / ".claude/skills/define-task").symlink_to(SHARED_FILES[1].parent)
        assert notes.old_links(p) == [
            "! the home still has the old link .claude/fm.md (FN-143): the plugin replaces it; delete it",
            "! the home still has the old link .claude/skills/define-task (FN-143): the plugin replaces it; delete it",
        ]
        (h / "CLAUDE.md").write_text("# x\n\n@.claude/fm.md\n")
        assert notes.old_links(p)[0].endswith("delete it, and remove the line @.claude/fm.md from CLAUDE.md")
        assert ".claude/skills/define-task (FN-143)" in fm(h, "health")
        (h / "CLAUDE.md").write_bytes(b"\xff\xfe\x00bad")
        assert len(notes.old_links(p)) == 2

    def test_health_names_old_links_in_a_repo(self) -> None:
        h = self.home()
        code = h / "code"
        (h / "fm.toml").write_text(f'[project]\nrepo = "{code}"\n')
        p = Project.load(h)
        code.mkdir()
        assert notes.old_links(p) == []
        (code / ".claude/skills").mkdir(parents=True)
        (code / ".claude/fm.md").write_text("a copy, not a link\n")
        (code / ".claude/skills/define-task").mkdir()
        assert notes.old_links(p) == []
        (code / ".claude/fm.md").unlink()
        (code / ".claude/skills/define-task").rmdir()
        (code / ".claude/fm.md").symlink_to(SHARED_FILES[0])
        (code / ".claude/skills/define-task").symlink_to(SHARED_FILES[1].parent)
        (code / "CLAUDE.md").write_text("@.claude/fm.md\n")
        (h / ".claude/skills").mkdir(parents=True)
        (h / ".claude/fm.md").symlink_to(SHARED_FILES[0])
        assert notes.old_links(p) == [
            "! the home still has the old link .claude/fm.md (FN-143): the plugin replaces it; delete it",
            f"! {code} still has the old link .claude/fm.md (FN-143): the plugin replaces it; delete it",
            f"! {code} still has the old link .claude/skills/define-task (FN-143): the plugin replaces it; delete it",
        ]

    def test_shared_files_are_generic(self) -> None:
        for f in SHARED_FILES:
            text = f.read_text()
            for word in ("Jordi", "/Users/", "FN-", "ZZ", "English", "Spanish", "Catalan", *"áéíóúñ¿¡"):
                assert word not in text, f"{f.name}: {word}"
            assert set(re.findall(r"(?<!\$)\{(\w+)\}", text)) <= set(plugin.PLACEHOLDERS), f.name
            assert not re.search(r"(?<!\$)\{(?!(?:" + "|".join(plugin.PLACEHOLDERS) + r")\})[^}]*\}", text), f.name
