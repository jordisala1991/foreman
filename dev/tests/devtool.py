"""The developer tool's test helpers: dev/ on sys.path (foreman_dev) and `dev/foreman-dev` run the way the tests run
foreman (its own interpreter, the case's environment: a fake registry, state root and Claude Code folder)."""

import subprocess
import sys

from helpers import ROOT, ProjectCase

DEV = ROOT / "dev"
TOOL = DEV / "foreman-dev"
if str(DEV) not in sys.path:
    sys.path.insert(0, str(DEV))


def run_tool(args, env, cwd, timeout=120):
    return subprocess.run(
        [sys.executable, str(TOOL), *args],
        cwd=str(cwd),
        text=True,
        capture_output=True,
        env=env,
        timeout=timeout,
        check=False,
    )


class DevCase(ProjectCase):
    """ProjectCase with `self.dev(ARGS…)`: `foreman-dev ARGS…` in the case's home (its product)."""

    def dev(self, *args, cwd=None, env=None, timeout=120):
        e = self.env
        e.update(env or {})
        return run_tool(args, e, cwd or self.home, timeout)
