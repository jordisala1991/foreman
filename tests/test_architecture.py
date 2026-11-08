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

    def test_an_adapter_cannot_import_another_adapter(self):
        self.check("providers/local.py", "from . import github")

    def test_an_adapter_cannot_import_the_loop(self):
        self.check("providers/github.py", "from .. import loop")

    def test_core_cannot_import_proc(self):
        self.check("risk.py", "from . import proc")

    def test_an_adapter_cannot_import_subprocess_it_goes_through_proc(self):
        self.check("providers/github.py", "import subprocess", tool="ruff")

    def test_a_service_cannot_import_the_loop(self):
        self.check("integrate.py", "from . import loop")


class SubprocessList(unittest.TestCase):
    def test_the_port_cannot_import_subprocess(self):
        if not UV:
            self.skipTest("uv not installed")
        d = scratch()
        self.addCleanup(shutil.rmtree, d)
        plant(d, "providers/__init__.py", "import subprocess")
        self.assertNotEqual(run(d, "ruff", "check", "--ignore-noqa", "--no-cache", "fm").returncode, 0)


@unittest.skipUnless(UV, "uv not installed")
class Suppressions(unittest.TestCase):
    """noqa is caught by `ruff --ignore-noqa` and `type: ignore` by pyright (enableTypeIgnoreComments = false); `make suppressions`
    covers what no tool does (pyright: ignore, pylint: disable)."""

    def planted(self, line: str) -> Path:
        d = scratch()
        self.addCleanup(shutil.rmtree, d)
        shutil.copy(ROOT / "Makefile", d / "Makefile")
        for cmd in (["init", "-q"], ["add", "."]):
            subprocess.run(["git", "-C", str(d), *cmd], check=True, capture_output=True)
        plant(d, "util.py", line)
        return d

    def test_a_type_ignore_comment_does_not_silence_pyright(self):
        d = self.planted("BAD: int = 'x'  # type: " + "ignore")
        self.assertNotEqual(run(d, "pyright").returncode, 0)

    def test_a_noqa_comment_does_not_silence_ruff(self):
        d = self.planted("import os  # no" + "qa: F401")
        self.assertNotEqual(run(d, "ruff", "check", "--ignore-noqa", "--no-cache", "fm").returncode, 0)

    def test_make_suppressions_fails_on_the_rest(self):
        for line in ("X = 1  # pyright: ig" + "nore", "X = 1  # pylint: dis" + "able=too-many-lines"):
            d = self.planted(line)
            r = subprocess.run(["make", "suppressions"], cwd=d, capture_output=True, text=True, check=False)
            self.assertNotEqual(r.returncode, 0, line)

    def test_make_suppressions_passes_on_the_repo(self):
        self.assertEqual(subprocess.run(["make", "suppressions"], cwd=ROOT, capture_output=True, check=False).returncode, 0)


if __name__ == "__main__":
    unittest.main()
