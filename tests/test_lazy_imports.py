"""Each foreman command loads only what it uses: clean processes count the foreman modules that
`import foreman.cli` and the first steps of a command bring in."""

import json
import subprocess
import sys
import unittest

from helpers import ROOT

PRELUDE = f"import sys; sys.path.insert(0, {str(ROOT / 'lib')!r})\n"
REPORT = "\nprint(json.dumps(sorted(m for m in sys.modules if m.split('.')[0] == 'foreman')))\n"

HEAVY = {"integrate", "holdout", "permissions", "grants", "review", "plain", "delivery", "loop", "backlog"}


def modules(code):
    """The foreman modules a clean process has loaded after `code` (short names, without the package)."""
    r = subprocess.run(
        [sys.executable, "-I", "-c", PRELUDE + "import json\n" + code + REPORT],
        capture_output=True,
        text=True,
        check=True,
        cwd=str(ROOT),
    )
    return {
        m.removeprefix("foreman.")
        for m in json.loads(r.stdout.splitlines()[-1])
        if m != "foreman" and not m.startswith("foreman._tomli")
    }  # the vendored tomli is not ours


class LazyImportsTest(unittest.TestCase):
    def test_import_cli(self):
        got = modules("import foreman.cli")
        self.assertLessEqual(len(got), 6, got)
        self.assertFalse(
            got & (HEAVY | {"backlog_check", "backlog_cli", "backlog_list", "backlog_text", "inspect"}), got
        )

    def test_version(self):
        got = modules("import foreman.cli as c\ntry:\n    c.main(['--version'])\nexcept SystemExit:\n    pass")
        self.assertLessEqual(len(got), 6, got)

    def test_hook_mod(self):
        got = modules(
            "import foreman.cli as c, foreman.hooks_mod as h\nh.run = lambda a: 0\nc.main(['hook', 'mod', 'x'])"
        )
        self.assertLessEqual(len(got), 10, got)
        self.assertFalse(got & {"install", "versions"}, got)

    def test_hook_light_path(self):
        """lib/main.py runs Claude Code's classic hooks without the CLI, and `hook guard` (every tool call) without
        what only the other hooks use."""
        entry = ROOT / "lib/main.py"
        for name in ("guard", "agents", "who", "loop", "resume", "pause-agents"):
            with self.subTest(hook=name):
                got = modules(
                    "import io, runpy\nsys.argv = [" + repr(str(entry)) + ", 'hook', " + repr(name) + "]\n"
                    'sys.stdin = io.StringIO(\'{"cwd": "/nonexistent-fo"}\')\n'
                    "try:\n    runpy.run_path(sys.argv[0], run_name='__main__')\nexcept SystemExit:\n    pass"
                )
                self.assertIn("hooks", got)
                self.assertFalse(got & (HEAVY | {"cli", "orchestrator", "approved", "notes", "hooks_mod"}), got)
        got = modules(
            "import io, runpy\nsys.argv = [" + repr(str(entry)) + ", 'hook', 'mod', 'nothing']\n"
            "sys.stdin = io.StringIO('{}')\n"
            "try:\n    runpy.run_path(sys.argv[0], run_name='__main__')\nexcept BaseException:\n    pass"
        )
        self.assertIn("cli", got)  # `hook mod` still goes through cli.main

    def test_backlog_show(self):
        got = modules(
            "import foreman.cli as c\ntry:\n    c.main(['backlog', 'show', 'X'])\nexcept BaseException:\n    pass"
        )
        self.assertIn("backlog_cli", got)
        self.assertFalse(got & {"integrate", "holdout", "permissions"}, got)


if __name__ == "__main__":
    unittest.main()
