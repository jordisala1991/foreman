"""Where foreman is installed, a panel that follows it, and the panel as a user service.

**The trace** (`record`, called by the hooks). Claude Code runs every hook as `${CLAUDE_PLUGIN_ROOT}/bin/foreman`, so
a hook knows the plugin root and version the sessions run right now, whatever the install path (a marketplace from
GitHub, a `git` marketplace pinned to a branch, a folder loaded in place). Claude Code's own files
(installed_plugins.json) are not read: for a plugin loaded in place they do not say where it runs from. A hook records
`<state root>/install/installs.json` ({root: {version, seen, last}}) and `<state root>/install/root`: the chosen root
on the first line, then the other known roots that still exist, best first (fallbacks for the service launcher).
The chosen root is the highest version among the roots that exist, still have `bin/foreman`, are not a git working
copy (`.git`, a folder or a worktree's file) and are not marked `.orphaned_at` (Claude Code marks a replaced cache
folder so and deletes it 14 days later); between equal versions, the one recorded last. A working copy is a clone
loaded in place (a folder marketplace, or a session opened before switching to an installed copy): its hooks record
it like any root, but it runs unpublished code, and its version may already be the next one before any install has
it. So it ranks behind every installed copy, whatever its version or when it was seen, and is chosen only when
nothing else is usable. `installed_roots` applies the same rule again when reading `install/root` (a trace written
by an older foreman may list a working copy first), and so does the service launcher. A plugin loaded with
`--plugin-dir` (its data folder ends in `-inline`) is ignored, and so is
a root no hook has reported for 14 days. Files are only written when something changes (or once an hour, to keep
`last`), atomically; `record` never raises and never prints: hooks call it in every session of an active project.
The trace is per root, not per session: which session runs which version is versions.py's
`<state root>/install/sessions.json`.

**Following** (`Follower`, `foreman panel --follow-installed`): every 30 s the panel compares the chosen root and
its version with the code it runs. When they differ, it smoke-tests the new root (`bin/foreman --version` and
importing its panel module, with a time limit); if that passes it stops serving and re-executes itself with
`<root>/bin/foreman panel` and the same arguments (same pid, so a service manager sees no restart); the page reloads
itself when the static files change. If the smoke test fails it keeps running, logs it once and says so in
`/api/projects` (`foreman_warning`), and tries again only when the root or version changes or after 30 min. If the
folder it runs from disappears, the next check moves to the chosen root; with no usable root it keeps running and
warns. FOREMAN_FOLLOW_EVERY replaces the 30 s (tests).

**The service** (`foreman panel service install|uninstall|status|print`): a LaunchAgent (macOS) or a systemd user unit
(Linux) that runs `<state root>/install/panel-launcher`, a small sh script that reads `install/root` and execs the
first root with `bin/foreman` that is not a working copy (else the first with `bin/foreman`) as
`panel --follow-installed --host H --port P`. The unit never names a plugin folder
(those are versioned and deleted after an update) nor the key: the panel reads it from `<state root>/panel/token`
(mode 600). `install` writes the launcher, the trace (if there is none yet: the running root), the key file and the
unit, then loads it (`launchctl bootout` + `bootstrap gui/<uid>`, or `systemctl --user daemon-reload`, `enable`,
`restart`). Before writing, `install` looks at the other panel units (install_check): one of this state root on the
same port is refused (exit 2) unless `--replace`, which unloads and removes it first; one of another state root on
the same port is always refused; others are named. Without `--label`, `install`, `uninstall` and `status` resolve the
label alike (`resolve_label`: the only panel unit of this state root, whatever its name; several: exit 2), and
reinstalling keeps the unit's own host and port unless given. `uninstall` unloads it and removes the unit, and the
launcher unless another panel unit in the service folder still runs it (`panel_units` finds them: every unit
foreman writes names the launcher); `status` shows the files, asks the service manager, shows the unit's address
and, when the unit is not running but something answers there, who holds it (`lsof`'s pid matched against the
other units' pids), and lists the other panel units; `print` only prints the unit for the given values (default
label and address: it reads no unit). FOREMAN_SERVICE_DIR replaces the unit folder, FOREMAN_LAUNCHCTL,
FOREMAN_SYSTEMCTL and FOREMAN_LSOF the executables (tests).

launchd unloads asynchronously: right after `bootout` a `bootstrap` of the same label fails with "5: Input/output
error". So `install` waits until `launchctl print` no longer finds the label (at most UNLOAD_WAIT seconds), then
tries `bootstrap` BOOTSTRAP_TRIES times with a pause, and if it still fails says how to load it by hand.
FOREMAN_LAUNCHD_WAIT and FOREMAN_LAUNCHD_PAUSE replace the wait limit and the pause (tests).

The unit's PATH is the installer's, cleaned (see service_env): the panel runs git, gh, bash, a Python 3.11+ and the
projects' `busy` commands, which may live anywhere (Homebrew, mise, asdf, nix…), while launchd and systemd
start services with only the system folders.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import __version__, config, util
from .util import PathLike

Installs = dict[str, dict[str, Any]]  # installs.json: {root: {version, seen, last}}
Env = Mapping[str, str]  # an environment: os.environ or a copy of it

ROOT = Path(__file__).resolve().parents[2]
STALE_DAYS = 14  # a root no hook reported for this long is forgotten (Claude Code's cache cleanup delay)
REFRESH_SECONDS = 3600  # `last` is rewritten at most this often for a root that did not change
FOLLOW_EVERY = 30  # seconds between two checks of the trace
RETRY_SECONDS = 1800  # a smoke test that failed is retried after this long (or when the trace changes)
SMOKE_TIMEOUT = 20
DEFAULT_LABEL = "foreman.panel"
UNLOAD_WAIT = 10.0  # seconds to wait for launchd to unload the old service before loading the new one
BOOTSTRAP_TRIES = 4  # launchctl bootstrap attempts
BOOTSTRAP_PAUSE = 1.0  # seconds between bootstrap attempts (and between checks while unloading: a fifth of it)
SYSTEM_PATH = ("/usr/bin", "/bin", "/usr/sbin", "/sbin")


def install_dir() -> Path:
    return config.state_root() / "install"


def roots_file() -> Path:
    return install_dir() / "root"


def installs_file() -> Path:
    return install_dir() / "installs.json"


def launcher_file() -> Path:
    return install_dir() / "panel-launcher"


# ---------- versions and roots ----------


def version_key(v: object) -> tuple[Any, ...]:
    """Sort key of a semantic version: X.Y.Z > X.Y.Z-pre; anything unparsable sorts first."""
    m = re.match(r"^(\d+)\.(\d+)\.(\d+)(?:-([0-9A-Za-z.-]+))?", str(v or ""))
    if not m:
        return (-1, -1, -1, 0, ())
    pre = m.group(4)
    return (
        int(m.group(1)),
        int(m.group(2)),
        int(m.group(3)),
        0 if pre else 1,
        tuple((0, int(x), "") if x.isdigit() else (1, 0, x) for x in pre.split(".")) if pre else (),
    )


def root_version(root: PathLike) -> str | None:
    """The version in <root>/.claude-plugin/plugin.json, or None."""
    try:
        v = json.loads((Path(root) / ".claude-plugin" / "plugin.json").read_text()).get("version")
    except (OSError, ValueError, AttributeError):
        return None
    return v if isinstance(v, str) and v else None


def usable(root: PathLike) -> bool:
    """A root a panel can run from: it exists and has an executable bin/foreman."""
    return os.access(Path(root) / "bin" / "foreman", os.X_OK)


def working_copy(root: PathLike) -> bool:
    """A git working copy (a clone or a worktree: `.git` is a folder or a file), not an installed copy."""
    return (Path(root) / ".git").exists()


def ranked(installs: Installs) -> list[str]:
    """The usable roots of installs.json, best first: installed before working copies, not orphaned, highest
    version, recorded last."""

    def key(item: tuple[str, dict[str, Any]]) -> tuple[Any, ...]:
        root, e = item
        orphaned = (Path(root) / ".orphaned_at").exists()
        return (not working_copy(root), not orphaned, version_key(e.get("version")), e.get("seen", ""))

    return [r for r, _ in sorted(((r, e) for r, e in installs.items() if usable(r)), key=key, reverse=True)]


def _read_installs() -> Installs:
    d = util.read_json(installs_file(), {})
    return {k: v for k, v in d.items() if isinstance(v, dict)} if isinstance(d, dict) else {}


def record(env: Env | None = None, now: float | None = None) -> str | None:
    """The hooks' trace (see the module doc). Returns the chosen root, or None; never raises."""
    try:
        env = os.environ if env is None else env
        root = env.get("CLAUDE_PLUGIN_ROOT")
        if not root:
            return None
        data = env.get("CLAUDE_PLUGIN_DATA") or ""
        if data.rstrip("/").endswith("-inline"):  # --plugin-dir: one session's working tree, not an install
            return None
        root = os.path.realpath(root)
        version = root_version(root)
        if not version:
            return None
        now = now if now is not None else time.time()
        stamp = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(now))
        old = _read_installs()

        def ago(seconds: float) -> str:
            return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(now - seconds))

        stale = ago(STALE_DAYS * 86400)
        # forget roots that are gone or that no hook reported for STALE_DAYS
        installs = {
            r: e for r, e in old.items() if r == root or (usable(r) and (e.get("last") or e.get("seen") or "") >= stale)
        }
        e = installs.get(root)
        if not e or e.get("version") != version:
            installs[root] = {"version": version, "seen": stamp, "last": stamp}
        elif (e.get("last") or "") < ago(REFRESH_SECONDS):
            installs[root] = {**e, "last": stamp}
        if installs != old:
            util.write_text_atomic(installs_file(), json.dumps(installs, indent=1, sort_keys=True) + "\n")
        best = ranked(installs)
        text = "".join(r + "\n" for r in best)
        try:
            current = roots_file().read_text()
        except OSError:
            current = None
        if best and text != current:
            util.write_text_atomic(roots_file(), text)
        return best[0] if best else None
    except Exception:  # noqa: BLE001 (a hook never fails nor prints because of the trace)
        return None


def installed_roots() -> list[str]:
    """The roots of `install/root` that are usable, chosen first ([] if there is no trace). Working copies go after
    the installed ones even if the file lists them first (written by a foreman before this rule)."""
    try:
        lines = roots_file().read_text().splitlines()
    except OSError:
        return []
    roots = [ln for ln in (x.strip() for x in lines) if ln and usable(ln)]
    return sorted(roots, key=working_copy)  # stable: the file's order within each group


def ensure_trace(root: PathLike = ROOT) -> None:
    """`install/root` names at least `root` (the service's first start, before any hook has run)."""
    if not installed_roots():
        util.write_text_atomic(roots_file(), str(Path(root).resolve()) + "\n")


# ---------- following the installed version ----------


def smoke_test(root: PathLike, timeout: float = SMOKE_TIMEOUT) -> str | None:
    """None if <root> can run the panel (`bin/foreman --version` and importing its panel module), else why not."""
    exe = Path(root) / "bin" / "foreman"
    if not usable(root):
        return f"{exe} is missing or not executable"
    checks = (
        [str(exe), "--version"],
        [
            sys.executable,
            "-c",
            "import sys; sys.path.insert(0, sys.argv[1]); import foreman.panel",
            str(Path(root) / "lib"),
        ],
    )
    env = {k: v for k, v in os.environ.items() if k != "FOREMAN_REEXEC"}
    for cmd in checks:
        try:
            r = subprocess.run(
                cmd, capture_output=True, text=True, timeout=timeout, env=env, cwd=str(root), check=False
            )
        except (OSError, subprocess.SubprocessError) as e:
            return f"{' '.join(cmd[1:3])}: {e}"
        if r.returncode != 0:
            tail = (r.stderr or r.stdout).strip().splitlines()[-1:] or [f"exit {r.returncode}"]
            return f"{Path(cmd[1]).name if cmd[1] != '-c' else 'import foreman.panel'}: {tail[0][:300]}"
    return None


class Follower:
    """The watcher of `foreman panel --follow-installed`. `check()` does one round and returns the root to move to
    (or None); `run(stop)` is the thread that calls `check` every FOLLOW_EVERY seconds and `stop(root)` when it has
    to move. The panel then closes its server and calls `reexec(root)`."""

    def __init__(
        self,
        args: Iterable[str],
        root: PathLike = ROOT,
        version: str = __version__,
        smoke: Callable[[str], str | None] | None = None,
        log: Callable[[str], object] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.args = list(args)  # the panel's arguments, given again to the new root
        self.root = str(Path(root).resolve())
        self.version = version
        self.smoke = smoke or smoke_test
        self.log = log or (lambda msg: print(f"foreman panel: {msg}", file=sys.stderr, flush=True))
        self.clock = clock
        self.warning: str | None = None  # shown by the panel: a new version that did not pass the smoke test
        self.failed: dict[tuple[str, str | None], float] = {}  # (root, version) -> monotonic time of the failure

    def target(self) -> tuple[str, str | None] | None:
        """(root, version) the trace chooses now, or None when it agrees with the running code or names nothing."""
        roots = installed_roots()
        if not roots:
            if not usable(self.root):
                self.set_warning(
                    f"the folder this panel runs from ({self.root}) is gone and no installed foreman "
                    f"is recorded in {roots_file()}"
                )
            return None
        root = str(Path(roots[0]).resolve())
        version = root_version(root)
        if root == self.root and version == self.version:
            self.clear_warning()
            return None
        return root, version

    def set_warning(self, text: str) -> None:
        if text != self.warning:
            self.log(text)
        self.warning = text

    def clear_warning(self) -> None:
        self.warning = None

    def check(self) -> str | None:
        t = self.target()
        if not t:
            return None
        root, version = t
        at = self.failed.get(t)
        if at is not None and self.clock() - at < RETRY_SECONDS:
            return None
        why = self.smoke(root)
        if why:
            self.failed[t] = self.clock()
            self.set_warning(
                f"foreman {version or '?'} at {root} did not pass the smoke test, still running "
                f"{self.version} from {self.root}: {why}"
            )
            return None
        self.failed.pop(t, None)
        self.log(f"moving from foreman {self.version} ({self.root}) to {version} ({root})")
        return root

    def run(
        self, stop: Callable[[str], object], every: float | None = None, done: threading.Event | None = None
    ) -> str | None:
        every = every or float(os.environ.get("FOREMAN_FOLLOW_EVERY") or FOLLOW_EVERY)
        done = done or threading.Event()
        while not done.wait(every):
            try:
                root = self.check()
            except Exception as e:  # noqa: BLE001 (the watcher never takes the panel down)
                self.log(f"follow-installed: {e}")
                continue
            if root:
                stop(root)
                return root

    def argv(self, root: PathLike) -> list[str]:
        """The new root's launcher, which chooses its own Python (an older root's bin/foreman is a Python script
        with a `python3` shebang: run the same way)."""
        return [str(Path(root) / "bin" / "foreman"), "panel", *self.args]

    def reexec(self, root: PathLike, execv: Callable[[str, list[str]], object] = os.execv) -> None:
        argv = self.argv(root)
        execv(argv[0], argv)


def info(follower: Follower | None = None) -> dict[str, Any]:
    """What /api/projects says about the running foreman."""
    return {
        "foreman_version": __version__,
        "foreman_root": str(ROOT),
        "foreman_follows": follower is not None,
        "foreman_warning": follower.warning if follower else None,
    }


# ---------- the panel as a user service ----------

LAUNCHER = """#!/bin/sh
# Written by `foreman panel service install`. Runs the panel from the installed foreman, the first root listed in
#   {roots}
# that has bin/foreman and is not a git working copy (no .git), else the first one with bin/foreman. The hooks keep
# that file up to date; the panel follows it while running.
roots={roots_q}
if [ -r "$roots" ]; then
  for pass in installed any; do
    while IFS= read -r r <&3 || [ -n "$r" ]; do
      if [ -n "$r" ] && [ -x "$r/bin/foreman" ] && {{ [ "$pass" = any ] || ! [ -e "$r/.git" ]; }}; then
        exec "$r/bin/foreman" panel --follow-installed "$@" 3<&-
      fi
    done 3< "$roots"
  done
fi
echo "foreman panel: no installed foreman listed in $roots (open a Claude Code session with the plugin, or run" \\
  "foreman panel service install again)" >&2
exit 1
"""


def sh_quote(s: object) -> str:
    return "'" + str(s).replace("'", "'\\''") + "'"


def launcher_text() -> str:
    return LAUNCHER.format(roots=roots_file(), roots_q=sh_quote(roots_file()))


def platform_name(forced: str | None = None) -> str:
    if forced:
        return forced
    return {"darwin": "macos", "linux": "linux"}.get(sys.platform, sys.platform)


def service_dir(platform: str) -> Path:
    if os.environ.get("FOREMAN_SERVICE_DIR"):
        return Path(os.environ["FOREMAN_SERVICE_DIR"]).expanduser()
    if platform == "macos":
        return Path.home() / "Library" / "LaunchAgents"
    xdg = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(xdg).expanduser() / "systemd" / "user"


def unit_file(platform: str, label: str) -> Path:
    return service_dir(platform) / (f"{label}.plist" if platform == "macos" else f"{label}.service")


WILDCARD_HOSTS = ("0.0.0.0", "::", "")  # listening on every address: clashes with any host on the same port


@dataclass(frozen=True)
class Unit:
    """A panel service unit found in the service folder: one whose command runs a `panel-launcher` (every unit
    foreman ever wrote does). `launcher` is the launcher path it names, which may belong to another state root."""

    label: str
    path: Path
    launcher: str
    host: str
    port: int

    @property
    def shares_launcher(self) -> bool:
        """It runs this state root's launcher (so uninstalling another unit must keep it)."""
        return os.path.realpath(self.launcher) == os.path.realpath(launcher_file())

    def clashes(self, host: str, port: int) -> bool:
        """A panel on host:port could not listen while this unit holds its own address."""
        return port == self.port and (host == self.host or host in WILDCARD_HOSTS or self.host in WILDCARD_HOSTS)


def unit_command(platform: str, raw: bytes) -> tuple[str | None, list[str]]:
    """The label (None: take the file name) and the command of a unit file; (None, []) if it cannot be read. On
    macOS the label is the plist's `Label` (an XML or binary plist); on Linux the file name, and the command is
    `ExecStart=` with systemd_quote undone."""
    if platform == "macos":
        import plistlib  # not at the top: the hooks import this module for the trace

        try:
            d = plistlib.loads(raw)
        except Exception:  # noqa: BLE001 - a broken or foreign plist is skipped, never raised
            return None, []
        args = d.get("ProgramArguments") if isinstance(d, dict) else None
        if not isinstance(args, list):
            return None, []
        label = d.get("Label")
        return (label if isinstance(label, str) and label else None), [str(x) for x in args]
    import shlex

    text = raw.decode("utf-8", "replace")
    line = next((ln.strip() for ln in text.splitlines() if ln.strip().startswith("ExecStart=")), "")
    try:
        args = shlex.split(line.split("=", 1)[1]) if line else []
    except ValueError:
        return None, []
    return None, [a.replace("%%", "%").replace("$$", "$") for a in args]


def read_unit(platform: str, path: Path) -> Unit | None:
    """The panel unit in `path`, or None if it is not one (or cannot be read)."""
    try:
        raw = path.read_bytes()
    except OSError:
        return None
    if b"panel-launcher" not in raw:  # cheap filter: most units in the folder belong to other programs
        return None
    label, args = unit_command(platform, raw)
    launcher = next((a for a in args if Path(a).name == "panel-launcher"), None)
    if launcher is None:
        return None
    rest = args[args.index(launcher) + 1 :]
    host, port = "127.0.0.1", 8766  # service_main's defaults
    for flag, value in zip(rest, rest[1:]):
        if flag == "--host":
            host = value
        elif flag == "--port":
            try:
                port = int(value)
            except ValueError:
                return None
    return Unit(label or path.name.rsplit(".", 1)[0], path, launcher, host, port)


def panel_units(platform: str) -> list[Unit]:
    """The panel units in the service folder, by label; read only, never raises."""
    suffix = ".plist" if platform == "macos" else ".service"
    try:
        files = sorted(p for p in service_dir(platform).iterdir() if p.name.endswith(suffix) and p.is_file())
    except OSError:
        return []
    units = [u for u in (read_unit(platform, p) for p in files) if u]
    return sorted(units, key=lambda u: (u.label, str(u.path)))


def service_path(path: str | None = None) -> str:
    """The installer's PATH, kept so the service finds the same git, gh, Python and lock-script tools, minus what
    cannot work or must not last: relative entries (the service runs from /), folders that do not exist, the
    temporary folder, foreman's own plugin folders (versioned, deleted after an update) and duplicates; the system
    folders are appended if missing (ps, sysctl, memory_pressure)."""
    import tempfile

    root = str(ROOT)
    tmp = os.path.realpath(tempfile.gettempdir()) + os.sep
    keep: list[str] = []
    for p in (os.environ.get("PATH", "") if path is None else path).split(os.pathsep):
        real = os.path.realpath(p) + os.sep if p else ""
        if (
            not p
            or not os.path.isabs(p)
            or not os.path.isdir(p)
            or real.startswith((root + os.sep, tmp))
            or "/plugins/cache/" in p
        ):
            continue
        keep.append(p)
    keep += [p for p in SYSTEM_PATH if p not in keep]
    return os.pathsep.join(dict.fromkeys(keep))


def service_env() -> dict[str, str]:
    """The environment the service needs from whoever installs it: PATH (service_path) and foreman's own location
    overrides. Never the panel key."""
    env = {"PATH": service_path()}
    for k in ("FOREMAN_STATE_HOME", "FOREMAN_REGISTRY", "FOREMAN_PYTHON"):
        if os.environ.get(k):
            env[k] = os.environ[k]
    return env


def panel_args(host: str, port: int) -> list[str]:
    return ["--host", host, "--port", str(port)]


def plist_text(label: str, host: str, port: int) -> str:
    import plistlib  # not at the top: the hooks import this module for the trace

    log = config.state_root() / "panel" / "panel.log"
    d = {
        "Label": label,
        "ProgramArguments": ["/bin/sh", str(launcher_file()), *panel_args(host, port)],
        "EnvironmentVariables": service_env(),
        "RunAtLoad": True,
        "KeepAlive": True,
        "ThrottleInterval": 30,
        "ProcessType": "Background",
        "StandardOutPath": str(log),
        "StandardErrorPath": str(log),
    }
    return plistlib.dumps(d, sort_keys=False).decode()


def systemd_quote(s: object) -> str:
    s = str(s).replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%").replace("$", "$$")
    return f'"{s}"'


def unit_text(label: str, host: str, port: int) -> str:
    cmd = " ".join(systemd_quote(x) for x in ["/bin/sh", str(launcher_file()), *panel_args(host, port)])
    env = "".join(f"Environment={systemd_quote(f'{k}={v}')}\n" for k, v in service_env().items())
    return (
        f"[Unit]\nDescription=foreman panel ({label}): follows the installed foreman\n"
        f"After=network-online.target\n\n"
        f"[Service]\nType=simple\nExecStart={cmd}\n{env}Restart=always\nRestartSec=30\n\n"
        f"[Install]\nWantedBy=default.target\n"
    )


def service_text(platform: str, label: str, host: str, port: int) -> str:
    return plist_text(label, host, port) if platform == "macos" else unit_text(label, host, port)


def _run(cmd: Sequence[str], check: bool = True) -> tuple[int, str]:
    r = subprocess.run(cmd, capture_output=True, text=True, check=False)
    out = (r.stdout + r.stderr).strip()
    if check and r.returncode:
        raise RuntimeError(f"{' '.join(cmd)}: exit {r.returncode}" + (f": {out}" if out else ""))
    return r.returncode, out


def launchctl() -> str:
    return os.environ.get("FOREMAN_LAUNCHCTL") or "launchctl"


def systemctl() -> str:
    return os.environ.get("FOREMAN_SYSTEMCTL") or "systemctl"


def ensure_token() -> Path:
    """The panel key in <state root>/panel/token, mode 600 (created if missing; FOREMAN_PANEL_TOKEN does not count:
    the service does not see the installer's environment)."""
    from . import panel

    f = panel.token_file()
    if not f.exists():
        panel.write_token(f)
    elif f.stat().st_mode & 0o077:
        f.chmod(0o600)
    return f


def service_install(platform: str, label: str, host: str, port: int) -> Path:
    ensure_trace()
    ensure_token()
    util.write_text_atomic(launcher_file(), launcher_text())
    launcher_file().chmod(0o755)
    unit = unit_file(platform, label)
    unit.parent.mkdir(parents=True, exist_ok=True)
    util.write_text_atomic(unit, service_text(platform, label, host, port))
    if platform == "macos":
        launchd_reload(f"gui/{os.getuid()}", label, unit)
    else:
        _run([systemctl(), "--user", "daemon-reload"])
        _run([systemctl(), "--user", "enable", unit.name])
        _run([systemctl(), "--user", "restart", unit.name])
    return unit


def _env_seconds(name: str, default: float) -> float:
    try:
        return max(0.0, float(os.environ.get(name, "")))
    except ValueError:
        return default


def launchd_loaded(target: str) -> bool:
    return _run([launchctl(), "print", target], check=False)[0] == 0


def launchd_wait_unloaded(
    target: str,
    wait: float | None = None,
    pause: float | None = None,
    sleep: Callable[[float], object] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> bool:
    """Wait (at most `wait` seconds) until launchd no longer finds `target` after a `bootout`: it unloads
    asynchronously. True if it is gone."""
    wait = _env_seconds("FOREMAN_LAUNCHD_WAIT", UNLOAD_WAIT) if wait is None else wait
    pause = _env_seconds("FOREMAN_LAUNCHD_PAUSE", BOOTSTRAP_PAUSE) if pause is None else pause
    deadline = clock() + wait
    while launchd_loaded(target):
        if clock() >= deadline:
            return False
        sleep(pause / 5)
    return True


def launchd_reload(
    domain: str,
    label: str,
    unit: PathLike,
    wait: float | None = None,
    pause: float | None = None,
    tries: int = BOOTSTRAP_TRIES,
    sleep: Callable[[float], object] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> int:
    """Unload `label` if loaded, wait for launchd to finish unloading (it does so asynchronously) and load `unit`,
    retrying the load a few times. Raises RuntimeError with the commands to load it by hand."""
    import shlex

    pause = _env_seconds("FOREMAN_LAUNCHD_PAUSE", BOOTSTRAP_PAUSE) if pause is None else pause
    target = f"{domain}/{label}"
    _run([launchctl(), "bootout", target], check=False)  # not loaded yet is fine
    launchd_wait_unloaded(target, wait, pause, sleep, clock)
    rc: int | None = None
    out = ""  # tries < 1: no attempt, the error below says so
    for attempt in range(1, tries + 1):
        rc, out = _run([launchctl(), "bootstrap", domain, str(unit)], check=False)
        if rc == 0:
            return attempt
        if attempt < tries:
            sleep(pause)
    raise RuntimeError(
        f"launchctl bootstrap {domain} {unit}: exit {rc}"
        + (f": {out}" if out else "")
        + f" ({tries} attempts; launchd may still be unloading the old {label}). Load it by hand: "
        + f"launchctl bootout {target}; sleep 5; launchctl bootstrap {domain} {shlex.quote(str(unit))}"
    )


def service_unload(platform: str, label: str, path: Path | None = None) -> list[str]:
    """Unload `label` and remove its unit file (`path`, when a plist's Label is not its file name), leaving the
    launcher; the files removed."""
    unit = path or unit_file(platform, label)
    if platform == "macos":
        _run([launchctl(), "bootout", f"gui/{os.getuid()}/{label}"], check=False)
    else:
        _run([systemctl(), "--user", "disable", "--now", unit.name], check=False)
    removed = [str(unit)] if unit.exists() else []
    unit.unlink(missing_ok=True)
    return removed


def install_check(
    platform: str, label: str, host: str, port: int, units: Sequence[Unit], replace: bool = False
) -> tuple[list[Unit], list[str], str]:
    """What `install` of `label` on host:port must do about the other panel units, before writing anything: the
    units `--replace` removes, lines to print, and an error ("" if none; install then writes nothing and exits 2).
    Another unit of this state root on the same port is refused (the new one could not listen there and would retry
    every 30 s) unless `replace`; one of another state root is always refused (another foreman's: not ours to
    remove). Other units elsewhere are only named; a port held by something that is no panel unit is only a warning
    (a panel started by hand, which the user may stop next)."""
    unit = unit_file(platform, label)
    others = [u for u in units if u.path != unit]
    clashing = [u for u in others if u.clashes(host, port)]
    far = [u for u in clashing if not u.shares_launcher]
    if far:
        u = far[0]
        error = (
            f"port {port} is taken by the panel service {u.label} ({u.host}:{u.port}) of another state root "
            f"(it runs {u.launcher}): uninstall it from there, or choose another --port"
        )
        return [], [], error
    if clashing and not replace:
        one = len(clashing) == 1
        names = ", ".join(f"{u.label} ({u.host}:{u.port})" for u in clashing)
        error = (
            f"port {port} is taken by the panel service{'' if one else 's'} {names}. To reinstall "
            f"{'that one' if one else 'one of them'}: foreman panel service install --label {clashing[0].label}; "
            f"to replace {'it' if one else 'them'} with {label}: add --replace"
        )
        return [], [], error
    notes = [f"note      another panel service: {u.label} on {u.host}:{u.port}" for u in others if u not in clashing]
    own = next((u for u in units if u.path == unit), None)
    if not clashing and not (own and own.clashes(host, port)) and port_in_use(host, port):
        owner = port_owner(port)
        notes.append(
            f"note      port {port} is in use{f' by pid {owner}' if owner else ''}, not by a panel service: "
            "the service starts once it is free"
        )
    return clashing, notes, ""


def service_uninstall(platform: str, label: str) -> tuple[list[str], list[str]]:
    """Unload and remove `label`'s unit, and the launcher unless another unit still runs it. Returns the files
    removed and the labels of the units that keep the launcher (uninstalling one of two units sharing it must not
    leave the other unable to start)."""
    removed = service_unload(platform, label)
    users = [u.label for u in panel_units(platform) if u.shares_launcher]
    if not users and launcher_file().exists():
        launcher_file().unlink()
        removed.append(str(launcher_file()))
    if platform != "macos":
        _run([systemctl(), "--user", "daemon-reload"], check=False)
    return removed, users


def resolve_label(explicit: str | None, units: Sequence[Unit]) -> tuple[str | None, str]:
    """The label a command without --label means, and a line saying why when it is not the one given or the default.
    Only the units that run this state root's launcher count (a unit of another state root is another foreman's):
    none, or one named DEFAULT_LABEL, mean DEFAULT_LABEL; exactly one other means that one (whoever installed it
    under another name still means it when giving no name); several others give (None, why)."""
    if explicit:
        return explicit, ""
    ours = [u.label for u in units if u.shares_launcher]
    if not ours or DEFAULT_LABEL in ours:
        return DEFAULT_LABEL, ""
    if len(ours) == 1:
        return ours[0], f"the only panel service here is {ours[0]} (--label to choose another)"
    return None, f"several panel services here ({', '.join(ours)}): choose one with --label"


def lsof() -> str | None:
    """The lsof to ask who listens on a port (FOREMAN_LSOF in tests), or None if there is none."""
    import shutil

    return (
        os.environ.get("FOREMAN_LSOF")
        or shutil.which("lsof")
        or next((p for p in ("/usr/sbin/lsof", "/usr/bin/lsof") if os.access(p, os.X_OK)), None)
    )


def connect_host(host: str) -> str:
    """The address to connect to for a panel listening on `host` (a wildcard: this machine)."""
    return {"": "127.0.0.1", "0.0.0.0": "127.0.0.1", "::": "::1"}.get(host, host)


def port_in_use(host: str, port: int, timeout: float = 0.5) -> bool:
    """Something accepts connections on host:port (portable, no external tool)."""
    import socket

    try:
        with socket.create_connection((connect_host(host), port), timeout=timeout):
            return True
    except OSError:
        return False


def port_owner(port: int, timeout: float = 3.0) -> str | None:
    """The pid listening on TCP `port` according to lsof, or None if it cannot be known (no lsof, too slow, no
    answer)."""
    exe = lsof()
    if not exe:
        return None
    try:
        r = subprocess.run(
            [exe, "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-t"],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return next((ln.strip() for ln in r.stdout.splitlines() if ln.strip().isdigit()), None)


def unit_state(platform: str, label: str) -> tuple[str, str, bool]:
    """What the service manager says of `label`: (state, pid or "", running)."""
    if platform == "macos":
        rc, out = _run([launchctl(), "print", f"gui/{os.getuid()}/{label}"], check=False)

        def field(name: str) -> str:
            pre = f"{name} ="
            return next((ln.split("=", 1)[1].strip() for ln in out.splitlines() if ln.strip().startswith(pre)), "")

        state = field("state") or ("not loaded" if rc else "loaded")
        return state, field("pid"), rc == 0 and state == "running"
    name = unit_file(platform, label).name
    rc, out = _run([systemctl(), "--user", "is-active", name], check=False)
    running = rc == 0
    state = out.splitlines()[-1].strip() if out.strip() else ("active" if running else "inactive")
    pid = ""
    if running:
        _, shown = _run([systemctl(), "--user", "show", "-p", "MainPID", "--value", name], check=False)
        pid = shown.strip() if shown.strip().isdigit() and shown.strip() != "0" else ""
    return state, pid, running


def describe(state: str, pid: str) -> str:
    return state + (f", pid {pid}" if pid else "")


def service_status(platform: str, label: str, units: Sequence[Unit] | None = None) -> tuple[list[str], int]:
    """Lines describing the service, and 0 if it is installed and running. Also a `port` line (the address in the
    unit; when the unit is not running but something answers there, who: the pid lsof gives and which panel unit it
    is) and an `other` line for every other panel unit in the service folder (another label, or another state
    root's launcher), whose state does not change the exit code."""
    units = panel_units(platform) if units is None else units
    unit = unit_file(platform, label)
    roots = installed_roots()
    lines = [
        f"unit      {unit}: " + ("present" if unit.exists() else "missing"),
        f"launcher  {launcher_file()}: " + ("present" if launcher_file().exists() else "missing"),
        f"installed {roots[0] + ' (' + (root_version(roots[0]) or '?') + ')' if roots else 'none recorded'}"
        f" [{roots_file()}]",
    ]
    state, pid, running = unit_state(platform, label)
    manager = "launchd  " if platform == "macos" else "systemd  "
    lines.append(f"{manager} {label if platform == 'macos' else unit.name}: {describe(state, pid)}")
    others = [u for u in units if u.path != unit]
    states = {u.label: unit_state(platform, u.label) for u in others}
    own = next((u for u in units if u.path == unit), None)
    if own:
        where = f"{own.host}:{own.port}"
        if not running and port_in_use(own.host, own.port):
            owner = port_owner(own.port)
            holder = next((lbl for lbl, (_, p, _) in states.items() if owner and p == owner), None)
            where += f": in use by pid {owner} ({holder or 'not a foreman service'})" if owner else ": in use"
        elif not running:
            where += ": free"
        lines.append(f"port      {where}")
    lines += [other_line(u, states[u.label]) for u in others]
    return lines, 0 if unit.exists() and running else 1


def other_line(u: Unit, state: tuple[str, str, bool]) -> str:
    """`status`'s line for a panel unit other than the one described."""
    far = "" if u.shares_launcher else f", another state root ({u.launcher})"
    return f"other     {u.label}: port {u.port}, {describe(*state[:2])}{far}"


def install_main(
    platform: str, label: str, host: str | None, port: int | None, units: Sequence[Unit], replace: bool
) -> int:
    """`install` of `label`: host and port default to the unit's own when it exists (reinstalling keeps its address),
    else 127.0.0.1:8766; refused (2) on a clash with another panel unit (install_check), whose units `--replace`
    unloads and removes first."""
    own = next((u for u in units if u.path == unit_file(platform, label)), None)
    host = host or (own.host if own else "127.0.0.1")
    port = port if port is not None else (own.port if own else 8766)
    replaced, notes, error = install_check(platform, label, host, port, units, replace)
    if error:
        print(f"foreman panel service install: {error}", file=sys.stderr)
        return 2
    for u in replaced:
        gone = service_unload(platform, u.label, u.path)
        if platform == "macos":  # launchd unloads asynchronously: let it free the port before the new one starts
            launchd_wait_unloaded(f"gui/{os.getuid()}/{u.label}")
        print(f"replaced  {u.label} ({u.host}:{u.port}): removed {', '.join(gone) or 'nothing'}")
    for line in notes:
        print(line)
    unit = service_install(platform, label, host, port)
    print(
        f"installed {unit} on {host}:{port}; the panel runs from {installed_roots()[0]} and follows the installed "
        f"foreman; its key is in {ensure_token()}"
    )
    return 0


def service_main(argv: Sequence[str]) -> int:
    ap = argparse.ArgumentParser(
        prog="foreman panel service",
        description="Run the panel as a user service that follows the installed foreman "
        "(LaunchAgent on macOS, systemd user unit on Linux).",
    )
    ap.add_argument("action", choices=("install", "uninstall", "status", "print"))
    ap.add_argument(
        "--label",
        help=f"service name; without it: {DEFAULT_LABEL}, or the only panel service here if it has another name "
        "(print: always the default)",
    )
    ap.add_argument(
        "--host",
        help="0.0.0.0 to reach it from the phone (the key still applies); default: the unit's own when reinstalling, "
        "else 127.0.0.1",
    )
    ap.add_argument("--port", type=int, help="default: the unit's own when reinstalling, else 8766")
    ap.add_argument(
        "--replace",
        action="store_true",
        help="install: unload and remove the other panel services of this state root on the same port",
    )
    ap.add_argument("--platform", choices=("macos", "linux"), help="default: this machine's")
    a = util.no_abbrev(ap).parse_args(argv)
    if a.label is not None and not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", a.label):
        print(f"foreman panel service: bad label {a.label!r} (letters, digits, . _ -)", file=sys.stderr)
        return 2
    if a.replace and a.action != "install":
        print("foreman panel service: --replace only goes with install", file=sys.stderr)
        return 2
    platform = platform_name(a.platform)
    if platform not in ("macos", "linux"):
        print(f"foreman panel service: {platform} is not supported (macOS or Linux)", file=sys.stderr)
        return 2
    try:
        if a.action == "print":  # writes nothing and reads no unit: what install writes with these values
            label = a.label or DEFAULT_LABEL
            host, port = a.host or "127.0.0.1", 8766 if a.port is None else a.port
            sys.stdout.write(service_text(platform, label, host, port))
            print(f"# {unit_file(platform, label)}; it runs {launcher_file()}:", file=sys.stderr)
            sys.stderr.write(launcher_text())
            return 0
        units = panel_units(platform)
        resolved, why = resolve_label(a.label, units)
        if resolved is None:  # several panel units of this state root and no --label: never guess
            if a.action != "status":
                print(f"foreman panel service {a.action}: {why}", file=sys.stderr)
                return 2
            print(f"label     {why}")
            print("\n".join(other_line(u, unit_state(platform, u.label)) for u in units))
            return 2
        if why:
            print(
                f"label     {why}"
                + {"install": ": reinstalling it", "uninstall": ": uninstalling it"}.get(a.action, "")
            )
        if a.action == "install":
            return install_main(platform, resolved, a.host, a.port, units, a.replace)
        if a.action == "uninstall":
            removed, users = service_uninstall(platform, resolved)
            print("removed " + (", ".join(removed) if removed else "nothing (not installed)"))
            if users and launcher_file().exists():
                print(f"launcher kept: used by {', '.join(users)}")
            return 0
        lines, rc = service_status(platform, resolved, units)
        print("\n".join(lines))
        return rc
    except (OSError, RuntimeError) as e:
        print(f"foreman panel service {a.action}: {e}", file=sys.stderr)
        return 1
