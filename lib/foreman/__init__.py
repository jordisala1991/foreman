"""foreman: the common core of the orchestrator method.

The version has one source, the plugin manifest (.claude-plugin/plugin.json); the selftest checks that this reads it
(lib/foreman/release.py).
"""

from __future__ import annotations

import json as _json
from pathlib import Path as _Path

PLUGIN_MANIFEST = _Path(__file__).resolve().parents[2] / ".claude-plugin" / "plugin.json"


def _read_version() -> str:
    try:
        v = _json.loads(PLUGIN_MANIFEST.read_text()).get("version")
    except (OSError, ValueError, AttributeError):
        return "0.0.0+unknown"
    return v if isinstance(v, str) and v else "0.0.0+unknown"


__version__ = _read_version()
