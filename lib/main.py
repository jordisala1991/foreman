"""foreman's Python entry: what bin/foreman (the POSIX sh launcher) runs, as `<python> lib/main.py ARGS…`.

The launcher chooses the Python and says why when none fits; this file runs foreman. Run as a script, Python puts
this folder (lib/) first on sys.path, so `foreman` is the package next to it with no path set by hand. Callers that
already run in a Python that fits (the tests) may run it directly with `sys.executable`; everything else calls
bin/foreman.

The launcher passes its minimum version in FOREMAN_PYTHON_MIN (bin/foreman's `min_python` is the one place it is
written). On its fast path it runs the PATH's python3 without asking its version: when that one is older, this file
runs the launcher again with FOREMAN_PYTHON_PROBE=1 so that it looks for one that fits. Both variables are removed
here, so a foreman started from this one chooses its Python again. This file must parse on old Pythons (3.6+).
"""

import os
import sys

need = os.environ.pop("FOREMAN_PYTHON_MIN", "")
probed = os.environ.pop("FOREMAN_PYTHON_PROBE", "")
if need and sys.version_info[:2] < tuple(int(x) for x in need.split(".")):
    if not probed:
        os.environ["FOREMAN_PYTHON_PROBE"] = "1"
        launcher = os.path.join(os.path.dirname(os.path.dirname(os.path.realpath(__file__))), "bin", "foreman")
        os.execv(launcher, [launcher] + sys.argv[1:])
    sys.stderr.write(
        f"foreman: {sys.executable} answered as Python {need} or newer "
        f"but is {sys.version_info[0]}.{sys.version_info[1]} (set FOREMAN_PYTHON)\n"
    )
    sys.exit(0 if sys.argv[1:2] == ["hook"] else 1)

if sys.argv[1:2] == ["hook"] and sys.argv[2:3] != ["mod"]:
    # Claude Code's classic hooks (hooks/classic.json), `hook guard` on every tool call: straight to hooks.run, as
    # cli.main would, without importing the CLI (argparse and the commands' modules cost ~8 ms a call).
    from foreman import hooks

    sys.exit(hooks.run(sys.argv[2] if len(sys.argv) > 2 else ""))

from foreman.cli import main  # noqa: E402

sys.exit(main())
