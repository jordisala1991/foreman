# foreman's developer tools

For whoever develops foreman; **not part of the product**. No foreman command, hook, skill, agent or doc of the
product names them, and nothing in `lib/foreman` imports them. They only read (but for `snapshot`, which writes into
an empty folder): the registry, each product's state and Claude Code's transcripts, through lib/foreman's own readers
(config, transcripts, identity, orchestrator, util, i18n and the history collector, `lib/foreman/timeline.py`, which
`foreman catchup` reads).

```sh
dev/foreman-dev [--project P] stats …
dev/foreman-dev [--project P] fill SESSION [--json]
dev/foreman-dev [--project P] timeline …
dev/foreman-dev snapshot DEST [--commit REV]
```

The product is found as foreman finds it (`--project P`, `FOREMAN_HOME`, or the folder it runs in), and the same
variables point it elsewhere (`FOREMAN_REGISTRY`, `FOREMAN_STATE_HOME`, `FOREMAN_CLAUDE_PROJECTS`). Each subcommand
has its `--help`.

## stats: what the sessions spent

What the Claude Code sessions of a product spent, in tokens (calls, cache read and write, output, input, peak
context), by session, agent, issue, agent type, model or hour (`--by`), read from Claude Code's transcripts and
attributed with what foreman recorded (who orchestrated, which agent did which issue). It sees the sessions opened in
the product's folders (home, repos, worktrees) or recorded by its hooks; `--all` every product and the sessions of
none. Claude Code's own equivalent price shows on closed sessions; `--session ID` one session with its agents next to
Claude Code's own figures; `--usage` also runs `claude -p "/usage"`.

## fill: what fills one session's context

`fill SESSION` (a session or agent id, a unique prefix, or a transcript file) splits the growth of one session's
context between calls among its own output (by kind: text, commands, briefs, thinking) and what entered (tool results
by tool and Bash command, agents' final messages, prompts, attachments, hook texts), each weighed by the calls that
re-read it; the ten largest tool outputs; foreman's hook texts by notice class; the context before the first loop
pass; and «to spend less» lines when one of their rules holds (agents' final messages with a median over 1,500
characters, the context growing more than 70K tokens an hour, more than 40K before the first loop pass, one foreman
notice more than 10 times).

## timeline: what happened, with the spend

One event per line in time order (`HH:MM kind who issue: text @ pointer`), merged from the notes' headings, progress
lines and deliveries, launches, each agent's start, delivery-check refusals, stop and token spend, the orchestrator
sessions' spend, the local CI runs of `foreman integrate`, the loop passes, the inbox and the decisions. Every line
ends with where to dig (a transcript, a note's file and line, a log's line), shortened by the `paths:` legend.

- Where did this spend come from? `timeline --kind spend`, then `stats` for the breakdown.
- Where did foreman fail? `timeline --kind ci,block,stop,loop --since 2h`.
- Which agent went wrong, and with which instructions? `timeline --agent NAME` prints its brief (the first message of
  its transcript, cut at 1,500 characters) and then its events. `--issue ID` (or `ID Tn`) and `--session ID` narrow
  the same way.

The default window is the last 24 h and the last 200 lines (`--limit 0`: all); runs of skipped passes are one line;
`--json` gives the raw events.

## snapshot: the public snapshot

`snapshot DEST` writes the tracked tree at a commit (HEAD by default) into an empty folder, without `ops/` and
`tests/live/` and with a short CHANGELOG that starts the public history (CONTRIBUTING.md, «Cutting the public
snapshot»). It is the one tool here that writes, and only into that folder; it never creates a remote, pushes or
calls gh.

## Tests

`dev/tests/` (with `dev/tests/golden/`) runs in `bin/foreman selftest` with foreman's own tests, and
`bin/foreman selftest --update-golden` rewrites its golden files too. ruff and pyright cover `dev/foreman_dev`.
