"""`foreman launchd`: the two launchd jobs (`loop`, `panel`) that start at login and are restarted if they exit.

Run from a version folder of the installed plugin, a job's command names no version: it carries the launcher
(`python3 -c`, foreman/launcher.py), which runs the version of fm@fm installed at each start. Run from a plain checkout, it
names that checkout's bin/foreman."""

import os
from pathlib import Path
from xml.sax.saxutils import escape

from .. import launcher, proc


def program() -> tuple[str, ...]:
    """What python3 runs: the launcher's text when this is the installed plugin, else this checkout's bin/foreman."""
    return ("-c", launcher.source()) if launcher.from_plugin() else (str(launcher.ROOT / "bin/foreman"),)


def plist(job: str = "loop", args: tuple[str, ...] = ()) -> str:
    """A launchd job (`loop` or `panel`). Only PATH goes in the environment."""
    py = proc.run(["/usr/bin/which", "python3"]).out.strip() or "python3"
    argv = "".join(f"<string>{escape(a)}</string>" for a in (py, *program(), job, *args))
    log = f"{escape(str(Path.home()))}/Library/Logs/fm-{job}.log"
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>dev.fm.{job}</string>
  <key>ProgramArguments</key><array>{argv}</array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>EnvironmentVariables</key><dict><key>PATH</key><string>{escape(os.environ.get("PATH", ""))}</string></dict>
  <key>StandardOutPath</key><string>{log}</string>
  <key>StandardErrorPath</key><string>{log}</string>
</dict></plist>
"""
