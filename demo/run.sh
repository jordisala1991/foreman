#!/bin/sh
# Starts the loop and the panel for the demo (both in the background, logs in demo/run/).
set -eu
here=$(cd "$(dirname "$0")" && pwd)
root=$(dirname "$here")
export FM_REGISTRY="$here/run/registry.json"
nohup "$root/bin/fm" loop > "$here/run/loop.log" 2>&1 &
echo "loop pid $!"
nohup "$root/bin/fm" panel --port "${PORT:-7788}" > "$here/run/panel.log" 2>&1 &
echo "panel pid $! (see demo/run/panel.log for its URL)"
