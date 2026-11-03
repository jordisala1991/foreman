#!/bin/bash
# Resource lock shared by every project and agent: one holder at a time (or one per slot), queue in arrival order.
# Forked from an earlier in-house version (2026-10-30): the same algorithm and the same on-disk protocol, with
# every path, the "busy" detector and the slots given by the caller (`foreman lock RESOURCE …` sets them from
# foreman.toml). docs/lock-protocol.md describes the protocol.
#   lock.sh run "what" cmd args...   takes the lock, runs the command and always releases it (preferred form);
#                                    if it is already yours (an ancestor holds it), just runs
#   lock.sh acquire "what" [seconds=7200]   waits for the lock; exits 0 if held, 1 if the wait runs out
#   lock.sh release                  releases it only if it is yours (same FOREMAN_LOCK_WHO)
#   lock.sh check                    exits 0 if held and says by whom; then the wait queue
# Environment (paths are required; the rest have defaults):
#   FOREMAN_LOCK_DIR     the lock directory (mkdir is the atomic take); with slots, DIR-<slot> per slot
#   FOREMAN_LOCK_QUEUE   ticket directory: one file per waiter, NAME = <epoch.micro>-<7-digit pid>, "who|time|pid|what|short"
#   FOREMAN_LOCK_NEXT    explicit turn file: if it exists and does not name you, you wait without losing your place
#   FOREMAN_LOCK_JUMPED  marker: a short job just jumped the queue, the next short one waits for a normal one
#   FOREMAN_LOCK_LOG     JSON lines, one per use (requested, event, who, what, taken, released, rc, waits)
#   FOREMAN_LOCK_WHO (default agent), FOREMAN_LOCK_SHORT=1 (a job of SHORT_MAX s or less), FOREMAN_LOCK_SHORT_MAX
#   (300), FOREMAN_LOCK_POLL (5 s), FOREMAN_LOCK_BUSY (shell command: output = something outside the lock still
#   uses the resource; after taking the lock it waits FOREMAN_LOCK_BUSY_WAIT s, default 600, for it to end),
#   FOREMAN_LOCK_SLOTS (space-separated: a slot per holder, e.g. simulator UDIDs; `run` exports FOREMAN_SLOT),
#   FOREMAN_LOCK_BUSY_TIMEOUT (5 s: a busy command still running then is cut with its process group and counts as
#   free), FOREMAN_LOCK_BIN and FOREMAN_LOCK_PROJECT (given to busy as FOREMAN_BIN and FOREMAN_PROJECT),
#   FOREMAN_LOCK_POINT_VARS (an outer point's variables, unset for busy: it is a point of its own).
set -u
who="${FOREMAN_LOCK_WHO:-agent}"
LOCK="${FOREMAN_LOCK_DIR:?FOREMAN_LOCK_DIR}"; QUEUE="${FOREMAN_LOCK_QUEUE:?FOREMAN_LOCK_QUEUE}"
NEXT="${FOREMAN_LOCK_NEXT:?FOREMAN_LOCK_NEXT}"; JUMPED="${FOREMAN_LOCK_JUMPED:?FOREMAN_LOCK_JUMPED}"
LOG="${FOREMAN_LOCK_LOG:?FOREMAN_LOCK_LOG}"; POLL="${FOREMAN_LOCK_POLL:-5}"
SHORT="${FOREMAN_LOCK_SHORT:-0}"; SHORT_MAX="${FOREMAN_LOCK_SHORT_MAX:-300}"
BUSY="${FOREMAN_LOCK_BUSY:-}"; BUSY_WAIT="${FOREMAN_LOCK_BUSY_WAIT:-600}"; SLOTS="${FOREMAN_LOCK_SLOTS:-}"
BUSY_TIMEOUT="${FOREMAN_LOCK_BUSY_TIMEOUT:-5}"; HERE="$(cd "$(dirname "$0")" && pwd)"
SLOT=""
now() { date '+%Y-%m-%dT%H:%M:%S'; }
# arrival: epoch seconds with 6 decimals, so that tickets sorted by name are in arrival order (with whole seconds,
# two arrivals in the same second went by pid, which the system does not hand out in order); whole seconds only
# where neither date nor perl gives more (they still sort among the others: "-" sorts before ".")
arrival() {
    local t; t=$(date '+%s.%N' 2>/dev/null)
    case "$t" in *[!0-9.]*|.*|*.) t=$(perl -MTime::HiRes=time -e 'printf "%.6f", time' 2>/dev/null) || t=$(date +%s);; esac
    case "$t" in *.*) local f="${t#*.}000000"; echo "${t%%.*}.${f:0:6}";; *) echo "$t";; esac
}
# put FILE TEXT: FILE holds TEXT and a newline, written whole: a temporary dot-file in the same folder renamed over it,
# so a reader sees the old file, no file or the whole new one, never an empty or half-written one
put() { local tmp; tmp="$(dirname "$1")/.$(basename "$1").$$"; printf '%s\n' "$2" > "$tmp" && mv -f "$tmp" "$1" || { rm -f "$tmp"; return 1; }; }
jstr() { printf '%s' "$1" | tr '\n\t' '  ' | sed 's/\\/\\\\/g; s/"/\\"/g' | cut -c1-200; }
rotate_log() {
    local first cut; first=$(head -c 60 "$LOG" 2>/dev/null | sed -nE 's/.*"requested":"([0-9-]*)T.*/\1/p' | head -1)
    cut=$(date -v-14d '+%F' 2>/dev/null || date -d '14 days ago' '+%F'); [ -n "$first" ] && [ "$first" \< "$cut" ] || return 0
    grep -E '"requested":"[0-9-]+T' "$LOG" | awk -v c="$cut" 'match($0, /"requested":"/) { if (substr($0, RSTART + RLENGTH, 10) >= c) print }' > "$LOG.tmp" && mv "$LOG.tmp" "$LOG"
}
# log_event EVENT REQUESTED TAKEN RC WAITS WHAT; never breaks the lock
log_event() {
    { mkdir -p "$(dirname "$LOG")"; rotate_log
      printf '{"requested":"%s","event":"%s","who":"%s","what":"%s","taken":"%s","released":"%s","rc":%s,"waits":%s%s}\n' \
        "$2" "$1" "$(jstr "$who")" "$(jstr "$6")" "$3" "$(now)" "${4:-null}" "${5:-0}" "${SLOT:+,\"slot\":\"$(jstr "$SLOT")\"}" >> "$LOG"; } 2>/dev/null || true
}
# The lock directories: one, or one per slot.
held_dirs() { if [ -z "$SLOTS" ]; then echo "$LOCK"; else for s in $SLOTS; do echo "$LOCK-$s"; done; fi; }
# The directory this caller holds (owner line starts with "who:"), if any.
mine_dir() { local d; for d in $(held_dirs); do grep -q -- "^$who:" "$d/owner" 2>/dev/null && { echo "$d"; return 0; }; done; return 1; }
# the busy command is a product command (lib/foreman/commands.py, point resource.busy): FOREMAN_API, FOREMAN_POINT,
# FOREMAN_BIN and FOREMAN_PROJECT, and its time limit (time_limit.sh: past it the whole process group is stopped and
# what it printed does not count). Without perl (time_limit.sh needs it) it runs without a limit, as it used to.
busy_out() {
    [ -n "$BUSY" ] || return 0
    local cap=() drop=() v out rc; command -v perl >/dev/null 2>&1 && cap=(bash "$HERE/time_limit.sh" "$BUSY_TIMEOUT")
    for v in ${FOREMAN_LOCK_POINT_VARS:-}; do drop+=(-u "$v"); done
    out=$(env ${drop[@]+"${drop[@]}"} FOREMAN_API=1 FOREMAN_POINT=resource.busy ${FOREMAN_LOCK_BIN:+"FOREMAN_BIN=$FOREMAN_LOCK_BIN"} \
        ${FOREMAN_LOCK_PROJECT:+"FOREMAN_PROJECT=$FOREMAN_LOCK_PROJECT"} ${cap[@]+"${cap[@]}"} bash -c "$BUSY" 2>/dev/null); rc=$?
    [ ${#cap[@]} -gt 0 ] && [ $rc = 124 ] && return 0
    [ -n "$out" ] && printf '%s\n' "$out"; return 0
}
busy() { [ -n "$(busy_out)" ]; }
live_queue() {
    local f; for f in $(ls "$QUEUE" 2>/dev/null); do
        if kill -0 "${f##*-}" 2>/dev/null; then echo "$f"; else rm -f "$QUEUE/$f"; fi
    done
}
short_queue() {
    local f; for f in $(live_queue); do [ "$(cut -d'|' -f5 "$QUEUE/$f" 2>/dev/null)" = short ] && echo "$f"; done
}
# take: mkdir of the lock (or of the first free slot); sets TAKEN_DIR and SLOT
take() {
    local d s
    if [ -z "$SLOTS" ]; then mkdir "$LOCK" 2>/dev/null && { TAKEN_DIR="$LOCK"; return 0; }; return 1; fi
    for s in $SLOTS; do d="$LOCK-$s"; mkdir "$d" 2>/dev/null && { TAKEN_DIR="$d"; SLOT="$s"; put "$d/slot" "$s"; return 0; }; done
    return 1
}
acquire() {
    local what="$1" secs="${2:-7200}" end=$((SECONDS + ${2:-7200})) waits=0 requested tk; requested=$(now)
    tk="$(arrival)-$(printf '%07d' $$)"
    mkdir -p "$QUEUE" 2>/dev/null || true
    put "$QUEUE/$tk" "$(jstr "$who" | tr '|' '/')|$requested|$$|$(jstr "$what" | tr '|' '/')|$([ "$SHORT" = 1 ] && echo short)" 2>/dev/null || true
    trap 'rm -f "$QUEUE/$tk"' EXIT; trap 'exit 130' INT TERM HUP
    while [ $SECONDS -lt $end ]; do
        local mine=0 ok=0 jump=0
        [ -e "$NEXT" ] && grep -q -- "$who" "$NEXT" 2>/dev/null && mine=1
        if [ $mine = 1 ]; then ok=1                                  # explicit turn is yours: go ahead
        elif [ ! -e "$NEXT" ] && [ "$(live_queue | head -1)" = "$tk" ] && { [ -e "$JUMPED" ] || [ -z "$(short_queue)" ] || [ "$SHORT" = 1 ]; }; then
            ok=1                                                     # no foreign turn, you are the oldest and no short job may jump
        elif [ ! -e "$NEXT" ] && [ "$SHORT" = 1 ] && [ ! -e "$JUMPED" ] && [ "$(short_queue | head -1)" = "$tk" ]; then
            ok=1; jump=1; fi                                         # oldest short job and no jump just before
        if [ $ok = 1 ] && take; then
            # owner is the holder for every reader: written whole, after slot and before the ticket goes (a reader
            # may see the lock taken without owner yet, or the holder with its ticket still there; lock.status says
            # both); meta comes after the wait for users outside the lock
            put "$TAKEN_DIR/owner" "$who: $what ($(date '+%F %T'), pid $$)"
            [ $mine = 1 ] && rm -f "$NEXT"
            if [ $jump = 1 ]; then touch "$JUMPED"; elif [ "$SHORT" != 1 ]; then rm -f "$JUMPED"; fi
            rm -f "$QUEUE/$tk"; trap - EXIT INT TERM HUP
            # Waits for users of the resource outside the lock (started without it, or still alive); says which.
            local t0=$SECONDS said=-60; while busy && [ $((SECONDS - t0)) -lt "$BUSY_WAIT" ]; do
                [ $((SECONDS - said)) -ge 60 ] && { said=$SECONDS; echo "waiting for users outside the lock ($((SECONDS - t0)) s):"; busy_out | sed 's/^/  /'; } >&2
                sleep "$POLL"
            done
            busy && { echo "still busy after $BUSY_WAIT s, going ahead:"; busy_out | sed 's/^/  /'; } >&2
            put "$TAKEN_DIR/meta" "$requested|$(now)|$waits|$what" 2>/dev/null || true
            echo "LOCKED ($waits waits)"; [ -n "$SLOT" ] && echo "SLOT $SLOT"
            return 0
        fi
        sleep "$POLL"; waits=$((waits+1))
    done
    rm -f "$QUEUE/$tk"; trap - EXIT INT TERM HUP
    log_event timeout "$requested" "" null "$waits" "$what"
    echo "TIMEOUT after $secs s; held by: $(cat $(held_dirs | sed 's|$|/owner|') 2>/dev/null | tr '\n' ';' || echo '?')"; return 1
}
release() {
    local d; d=$(mine_dir)
    if [ -n "$d" ]; then
        local req taken nwaits what; IFS='|' read -r req taken nwaits what < "$d/meta" 2>/dev/null || true
        SLOT=$(cat "$d/slot" 2>/dev/null || true)
        [ -n "${req:-}" ] && log_event use "$req" "$taken" "${FOREMAN_LOCK_RC:-null}" "${nwaits:-0}" "${what:-}"
        rm -rf "$d"; echo RELEASED
    else
        local any=0; for d in $(held_dirs); do [ -d "$d" ] && { any=1; echo "NOT yours, not released: $(cat "$d/owner" 2>/dev/null)"; }; done
        [ $any = 1 ] && return 1; echo "not held"
    fi
}
case "${1:-}" in
    run) shift; what="$1"; shift
        # Reentrant: if the lock is already yours, run without taking or releasing it. Only for a nested call (the
        # holder is an ancestor of this process) or a holder that is gone (a separate `acquire`): a second `run`
        # with the same FOREMAN_LOCK_WHO started alongside the first one waits in the queue.
        if d=$(mine_dir); then
            holder=$(sed -n 's/.*, pid \([0-9]*\))$/\1/p' "$d/owner")
            nested=0; p=$$
            while [ -n "$p" ] && [ "$p" -gt 1 ]; do
                [ "$p" = "$holder" ] && { nested=1; break; }
                p=$(ps -o ppid= -p "$p" 2>/dev/null | tr -d ' ')
            done
            if [ $nested = 1 ] || [ -z "$holder" ] || ! kill -0 "$holder" 2>/dev/null; then
                FOREMAN_SLOT=$(cat "$d/slot" 2>/dev/null || true) && export FOREMAN_SLOT; "$@"; exit $?
            fi
        fi
        acquire "$what" >&2 || exit 1
        export FOREMAN_SLOT="$SLOT"
        trap 'FOREMAN_LOCK_RC=$rc release >&2' EXIT; rc=null; t_run=$SECONDS; "$@"; rc=$?
        [ "$SHORT" = 1 ] && [ $((SECONDS - t_run)) -gt "$SHORT_MAX" ] && log_event short-long "$(now)" "" $rc 0 "$what ($((SECONDS - t_run)) s)"
        exit $rc ;;
    acquire|take) acquire "${2:-no description}" "${3:-7200}" ;;
    release) release ;;
    check) rc=1
        for d in $(held_dirs); do [ -d "$d" ] && { cat "$d/owner" 2>/dev/null || echo "(being taken: no owner yet)"; rc=0; }; done
        [ $rc = 1 ] && echo free
        for f in $(live_queue); do IFS='|' read -r q h p w k < "$QUEUE/$f" 2>/dev/null && echo "  queued: $q: $w (since ${h#*T}, pid $p)${k:+ [short]}"; done
        [ -e "$NEXT" ] && echo "  turn: $(cat "$NEXT")"
        [ -e "$JUMPED" ] && echo "  (a short job just jumped the queue: the next short one waits for a normal one)"
        exit $rc ;;
    *) sed -n '2,12p' "$0"; exit 2 ;;
esac
