"""The architecture rules (tach.toml, ruff TID251) and the suppression ratchet really fail on a violation: each case plants
one in a scratch copy of the repo and runs the real check there. Needs uv (the dev tools); skipped without it."""

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
UV = shutil.which("uv")


def scratch() -> Path:
    d = Path(tempfile.mkdtemp())
    for name in ("tach.toml", "pyproject.toml"):
        shutil.copy(ROOT / name, d / name)
    shutil.copytree(ROOT / "fm", d / "fm", ignore=shutil.ignore_patterns("__pycache__"))
    return d


def run(d: Path, *cmd: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["uv", "run", "--locked", "--project", str(ROOT), *cmd], cwd=d, capture_output=True, text=True, check=False)


def plant(d: Path, module: str, line: str) -> None:
    f = d / "fm" / module
    f.write_text(f.read_text() + f"\n{line}\n")


@unittest.skipUnless(UV, "uv not installed")
class Architecture(unittest.TestCase):
    def check(self, module: str, line: str, tool: str = "tach") -> None:
        d = scratch()
        self.addCleanup(shutil.rmtree, d)
        self.assertEqual(run(d, "tach", "check").returncode, 0, "the clean copy must pass")
        plant(d, module, line)
        r = run(d, "tach", "check") if tool == "tach" else run(d, "ruff", "check", "--ignore-noqa", "--no-cache", "fm")
        self.assertNotEqual(r.returncode, 0, f"{module}: {line} should fail {tool}\n{r.stdout}{r.stderr}")

    def test_core_cannot_import_the_loop(self):
        self.check("risk.py", "from . import loop")

    def test_core_cannot_import_cli(self):
        self.check("util.py", "from . import cli")

    def test_core_cannot_import_panel(self):
        self.check("backlog.py", "from . import panel")

    def test_core_cannot_import_an_adapter(self):
        self.check("risk.py", "from .providers import github")

    def test_core_cannot_import_subprocess(self):
        self.check("risk.py", "import subprocess", tool="ruff")

    def test_core_modules_do_not_import_subprocess_today(self):
        d = scratch()
        self.addCleanup(shutil.rmtree, d)
        self.assertEqual(run(d, "ruff", "check", "--ignore-noqa", "--no-cache", "fm").returncode, 0)

    def test_an_adapter_cannot_import_another_adapter(self):
        self.check("providers/local.py", "from . import github")

    def test_an_adapter_cannot_import_the_loop(self):
        self.check("providers/github.py", "from .. import loop")

    def test_a_service_cannot_import_the_loop(self):
        self.check("integrate.py", "from . import loop")


class SubprocessList(unittest.TestCase):
    def test_per_file_ignores_match_design(self):
        import re
        import tomllib

        ign = tomllib.loads((ROOT / "pyproject.toml").read_text())["tool"]["ruff"]["lint"]["per-file-ignores"]
        self.assertEqual(
            ign,
            {
                "fm/{loop,integrate,pulls,sessions,lock,sources}.py": ["TID251"],
                "fm/providers/{github,local,registry}.py": ["TID251"],
                "tests/*.py": ["TID251"],
            },
        )
        design = (ROOT / "DESIGN.md").read_text()
        for name in ("loop", "integrate", "pulls", "sessions", "lock", "sources", "providers/github"):
            self.assertTrue(re.search(rf"`{re.escape(name)}`", design), name)

    def test_the_port_cannot_import_subprocess(self):
        if not UV:
            self.skipTest("uv not installed")
        d = scratch()
        self.addCleanup(shutil.rmtree, d)
        plant(d, "providers/__init__.py", "import subprocess")
        self.assertNotEqual(run(d, "ruff", "check", "--ignore-noqa", "--no-cache", "fm").returncode, 0)


@unittest.skipUnless(shutil.which("git"), "git not installed")
class Suppressions(unittest.TestCase):
    def repo(self) -> Path:
        d = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, d)
        (d / "fm").mkdir()
        (d / "fm" / "a.py").write_text("x = 1\n")
        for cmd in (["init", "-q", "-b", "foreman-next"], ["add", "."], ["-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "base"]):
            subprocess.run(["git", "-C", str(d), *cmd], check=True)
        return d

    def bin(self, d: Path) -> subprocess.CompletedProcess[str]:
        return subprocess.run([str(ROOT / "bin" / "suppressions"), "--root", str(d)], capture_output=True, text=True, check=False)

    def test_equal_passes_and_one_more_fails(self):
        d = self.repo()
        self.assertEqual(self.bin(d).returncode, 0)
        for mark in (
            "# no" + "qa: E501",
            "# type: ig" + "nore",
            "# pyright: ig" + "nore",
            "# pylint: dis" + "able=too-many-lines",
            "# pyright: ba" + "sic",
            "# fmt: sk" + "ip",
        ):
            (d / "fm" / "a.py").write_text(f"x = 1  {mark}\n")
            self.assertEqual(self.bin(d).returncode, 1, mark)

    def test_fewer_than_base_passes(self):
        d = self.repo()
        (d / "fm" / "a.py").write_text("x = 1  # no" + "qa\n")
        subprocess.run(["git", "-C", str(d), "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qam", "more"], check=True)
        subprocess.run(["git", "-C", str(d), "checkout", "-qb", "work"], check=True)
        (d / "fm" / "a.py").write_text("x = 1\n")
        self.assertEqual(self.bin(d).returncode, 0)

    def test_no_base_branch_fails(self):
        d = self.repo()
        subprocess.run(["git", "-C", str(d), "branch", "-m", "other"], check=True)
        self.assertEqual(self.bin(d).returncode, 1)


if __name__ == "__main__":
    unittest.main()
