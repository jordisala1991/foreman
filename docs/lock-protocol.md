# Lock protocol

`foreman lock RESOURCE run|acquire|release|check` (lib/foreman/sh/lock.sh) is a fork of a GPU lock script that ran
in production from 2026-10-24; the algorithm and the on-disk format are the same, so a lock that script left behind
(held directory, tickets, turn file) is read as is. foreman runs every lock itself: there is no delegation to another
script.

## Files (per resource; default under `~/.local/state/foreman/locks/<resource>/`)

| File | Meaning |
|---|---|
| `held/` (or `held-<slot>/` per slot) | the lock: `mkdir` is the atomic take. `owner` = `who: what (YYYY-MM-DD HH:MM:SS, pid N)`; `meta` = `requested|taken|waits|what`; `slot` with slots |
| `queue/<epoch>.<micro>-<7-digit pid>` | one ticket per waiter, `who|time|pid|what|short`; sorted by name = arrival order (to the microsecond); tickets of dead pids are deleted when looked at |
| `next` | explicit turn: if it exists and does not contain your name you wait without losing your place; deleted when its owner takes the lock |
| `jumped` | a short job just jumped the queue: the next short one waits for a normal job |
| `uses.jsonl` | one JSON line per use: `requested, event (use, timeout, short-long), who, what, taken, released, rc, waits[, slot]`; rotated at 14 days |

Every file a reader looks at (`owner`, `meta`, `slot`, a ticket) is written whole: a temporary dot-file in the same
folder (`.owner.<pid>`, `.<ticket>.<pid>`) renamed over it, so a reader sees no file or the whole line. The steps of a
take are, in order: `mkdir`, `slot`, `owner`, the ticket removed, the wait for `busy`, `meta`. So a reader may see,
for an instant, a lock taken without `owner` (lock.status: holder `?`, `taking` "writing"; "stuck" if the folder is
older than 60 s: the taker died there) or the holder with its ticket still in the queue (left out of the queue: same
pid). An empty `owner` (between `mkdir` and its write) reads as being taken, and an empty ticket is left out until it
has its line.

The keys that define a lock (paths, `busy`, `busy_wait`, `short_max`, `slots`) come from the registry when the
resource is a machine resource (`[resources.NAME]` in `~/.config/foreman/projects.toml`, docs/config.md), so every
product runs lock.sh with the same ones; otherwise from the product's foreman.toml.

## Exit codes of `foreman lock RESOURCE check`

`check` writes and changes nothing; its output says who holds the lock (`who: what (time, pid N)`, or «(being taken: no
owner yet)»), or `free`, then the wait queue (`  queued: …`, `  turn: …`).

| Exit | Meaning |
|---|---|
| 0 | the lock is held (by someone, or being taken); the queue lines may follow |
| 1 | the lock is free (`free`; queued lines may still follow) |
| 2 | no check was made: unknown resource, or the arguments are missing (`foreman lock: unknown resource …` or the usage line) |

With slots, 0 means at least one slot is held. `run` exits with its command's code (1 when the lock could not be
taken); `acquire` exits 0 when taken and 1 when the wait runs out.

## Rules

1. You take the lock when it is free and: the turn file names you; or there is no turn file, your ticket is the
   oldest live one and no short job may jump; or you are a short job (`FOREMAN_LOCK_SHORT=1`), the oldest short
   one, and no short job jumped just before.
2. After taking it, if the resource's `busy` command prints something (users that did not take the lock), wait up
   to `busy_wait` seconds for them, saying who they are.
3. `run` is reentrant only for a nested call (the holder is an ancestor of the process) or a holder that is gone (a
   separate `acquire`); a second `run` with the same name started alongside waits in the queue.
4. `release` only releases your own lock (same `FOREMAN_LOCK_WHO`). `run` always releases, whatever the command
   returns, and exits with its code.
5. A short job that runs over `short_max` (300 s) is logged (`short-long`) so the label can be checked.
6. Slots (`slots = ["A", "B"]`): one holder per slot, same queue; `run` exports `FOREMAN_SLOT` (e.g. a simulator
   UDID) so two agents never get the same one.

Who you are (the same order for `foreman lock` and for `foreman progress`):
1. `FOREMAN_LOCK_WHO`: `run` gives its holder's name to the command it runs, so a `foreman progress` or a nested
   `foreman lock` inside a `run` is that holder's (not when the holder's name was only the default `agent`: then
   `run` also sets `FOREMAN_LOCK_WHO_DEFAULT=1` and its children look further down);
2. `FOREMAN_WHO`;
3. `AGENT`;
4. the call the guard hook wrote down: on every Bash call of a session the guard records, by `tool_use_id`, which
   agent runs its `foreman progress` / `foreman lock` calls and their words (`<state root>/callers.json`; the
   PostToolUse of that Bash drops the entry). The command looks up the open calls of its session
   (`CLAUDE_CODE_SESSION_ID`) with its own subcommand: one agent among them is the caller; several are told apart
   by the command's arguments; when that is not enough it uses the default and says so on stderr (it never
   guesses). The name is the agent's in `agents.json` (`agent-<id6>` for one without a name); a call of the main
   thread (the orchestrator) gives the default;
5. the default: `agent` for the lock, `orchestrator` for progress.

An explicit name (1-3) that differs from the one foreman recorded for the call still wins, with a line on stderr.
A short job says so with `FOREMAN_LOCK_SHORT=1`.
