---
name: owner-catchup
description: For the owner, loaded by the orchestrator - when the owner asks, in their own words and in any language, to catch up (what happened, how are we doing, what did I miss). Always the same short four-part summary - done, running, waiting for them, what went wrong - from one `foreman catchup`.
---
# Catching the owner up (orchestrator)

Only when the owner asks: never on your own when they come back, nor as part of a pass.

1. **Run `foreman catchup` once.** Without `--since` it counts from the last time the owner was caught up (none: the
   last 24 h; at most 7 days) and moves that mark, so only this skill runs it that way. When they name a moment
   («since Friday», «today»), pass `--since` (24h, 2h, HH:MM, a date): it neither reads nor moves the mark. Outside a
   foreman product it says «no project»: tell them so in one line and stop.
2. **Answer** as `foreman docs owner` says (docs/owner.md), in the project's language (the language of the command's
   headings), with its four parts in this order, each one even when empty («nothing» is a valid answer):
   - **Done**: what finished and what it changes for them, with the figure of its result line; one line per issue.
   - **Running**: what is being built and what for; planners as «being planned: …».
   - **Waiting for you**: their questions (how many, the oldest, what each one holds), permissions, reloads,
     approvals; offer to go through the questions now.
   - **Went wrong**: what failed or stopped, and what you are doing about it.

   Start with one line saying since when («Since yesterday 18:00»). Add what only your board knows (something they
   asked that is still open) under the part it belongs to.
3. **Short**: about 15 lines at most, one per thing. Names, not ids (an id only in brackets after its name); no agent
   names, branches, paths or config keys; rewrite an issue title that carries internal names. The command cuts each
   part at 8 items: say «and N more» rather than listing them.
4. **Detail only when asked.** Read `foreman catchup --since … --json` (each item has a `ref`: where to dig) or
   `foreman backlog show ID` (goal, plan, sub-issues with their report and result) for what they then ask about. If it needs reading code or measuring, launch a short
   agent and bring back only its conclusion.
5. **Nothing is acted on here**: no launch, integration or decision as part of the summary; do what they ask next.
