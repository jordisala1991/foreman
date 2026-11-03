#!/bin/bash
# Example "busy" command for a machine resource `gpu` (registry [resources.gpu] busy = this script; see
# examples/machine/projects.toml and docs/config.md). Copy it next to the registry, e.g.
# ~/.config/foreman/bin/gpu-busy.sh, outside every product's repo, and set EXES below to the executables that use the
# GPU on that machine.
#
# Contract (lib/foreman/sh/lock.sh, FOREMAN_LOCK_BUSY): any stdout means busy, no stdout means free; stderr and the
# exit code are ignored. After taking the lock, `foreman lock` waits up to busy_wait s (600 by default) while this
# prints something, and shows the lines every 60 s; the panel shows them under the resource's row. It runs with
# `bash -c` in the caller's folder (the registry's folder in the panel), so it must not depend on the working
# directory.
# Output: one line per process that uses the GPU outside the lock: pid, elapsed time, working directory, command.
# Exit code, for people and scripts: 0 busy, 1 free (like pgrep). On a terminal, "free" goes to stderr.
set -u

# Executables (the process name, not its arguments: a shell or a lock script that only names one in its command line
# does not count) that use the GPU. Space-separated; GPU_BUSY_EXES overrides it.
EXES="${GPU_BUSY_EXES:-python3 my-engine}"
# Command lines that match this extended regex never count, even with a listed executable (e.g. downloads run by
# python, or foreman itself, which is python until it execs the lock). GPU_BUSY_IGNORE overrides it; empty: none.
IGNORE="${GPU_BUSY_IGNORE-bin/foreman( |$)|pip install|/hf (download|upload)}"

# The process list is taken once, before this script starts any subshell of its own (a subshell is a `bash` too).
procs=$(ps -Ao pid=,comm= 2>/dev/null)
gpu_pids() {
    local pid comm exe e
    while read -r pid comm; do
        [ -n "$pid" ] && [ "$pid" != $$ ] || continue
        exe=${comm##*/}
        for e in $EXES; do
            [ "$exe" = "$e" ] || continue
            if [ -n "$IGNORE" ] && ps -o command= -p "$pid" 2>/dev/null | grep -qE "$IGNORE"; then
                break
            fi
            echo "$pid"; break
        done
    done <<< "$procs"
}

out=$(for p in $(gpu_pids); do
    t=$(ps -o etime= -p "$p" 2>/dev/null | tr -d ' '); [ -n "$t" ] || continue   # gone since the list was taken
    printf 'pid %s, running %s, in %s: %s\n' "$p" "$t" "$(lsof -a -p "$p" -d cwd -Fn 2>/dev/null | sed -n 's|^n||p')" \
        "$(ps -o command= -p "$p" 2>/dev/null | cut -c1-140)"
done)
if [ -n "$out" ]; then printf '%s\n' "$out"; exit 0; fi
[ -t 2 ] && echo free >&2
exit 1
