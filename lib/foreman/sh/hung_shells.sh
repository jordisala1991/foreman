#!/bin/bash
# Hung background shells of Claude sessions (orchestrator and agents): foreman hung-shells [--kill]
# Forked from an earlier in-house version: a long wait is legitimate if it is a resource lock of any kind.
# A shell counts as hung if:
#  - it polls (until/while ... sleep) a task output file that already ended ("[exited with code"), so it never ends; or
#  - it is a shell spawned by Claude older than HUNG_HOURS (default 3) whose descendants are not waiting for a lock
#    or running a CI (HUNG_LEGIT, an extended regex over their command lines).
# Prints one line per suspect; with --kill, kills it (and its children). Exit 1 if any suspect.
set -u
max_s=$(( ${HUNG_HOURS:-3} * 3600 ))
legit="${HUNG_LEGIT:-foreman/sh/lock\.sh|foreman lock|ci\.sh|foreman integrate}"
desc() { local c; for c in $(pgrep -P "$1"); do ps -o command= -p "$c"; desc "$c"; done; }
etime_s() { awk -F'[-:]' '{ n=NF; s=$n + 60*$(n-1); if (n>=3) s+=3600*$(n-2); if (n==4) s+=86400*$1; print s }' <<< "$1"; }
found=0
# Claude's Bash tool shells are recognised by the shell snapshot they source
for p in $(pgrep -f '\.claude/shell-snapshots/'); do
    {
        [ "$p" = $$ ] || [ "$p" = "$PPID" ] && continue
        cmd=$(ps -o command= -p "$p" 2>/dev/null) || continue
        age=$(etime_s "$(ps -o etime= -p "$p" | tr -d ' ')")
        why=""
        f=$(grep -oE '/private/tmp/claude-[^ "]*/tasks/[A-Za-z0-9]+\.output' <<< "$cmd" | head -1)
        if [ -n "$f" ] && grep -qE 'until |while ' <<< "$cmd" && tail -2 "$f" 2>/dev/null | grep -q '^\[exited with code'; then
            why="polls $(basename "$f"), which already ended"
        elif [ "$age" -gt "$max_s" ] && ! desc "$p" | grep -qE "$legit"; then
            why="older than ${HUNG_HOURS:-3} h without a lock or CI"
        fi
        [ -z "$why" ] && continue
        found=1
        printf 'pid %s, %s min: %s\n    %s\n' "$p" $((age / 60)) "$why" "$(grep -oE "eval '.{0,150}" <<< "$cmd" || cut -c1-150 <<< "$cmd")"
        if [ "${1:-}" = --kill ]; then pkill -P "$p"; kill "$p" && echo "    killed"; fi
    }
done
[ $found = 0 ] && echo "no hung shells"
exit $found
