---
name: owner-permissions
description: For the owner, loaded by the orchestrator - when the owner asks, in their own words and in any language, to see or change what is allowed («let the orchestrator do X», «nobody may do Y», «why was this blocked?», «what may the agents do?»). Shows the permissions as they are, proposes the change, tests it before it applies, applies it with their yes.
---
# Adjusting permissions with the owner (orchestrator)

Only when the owner asks. Everything here is written to them as `foreman docs owner` says (docs/owner.md), in the
project's language (`[project] language`): never a regex, a config key or a path in what they read, but the commands
an example blocks, in plain words. Only this product's list: another product's is changed in its own session.

**Lean towards what they ask.** Agents work with a fair amount of freedom, more so when the owner authorizes it. When
they ask to allow something, propose the narrowest change that does allow it, not a counter-proposal that keeps it
blocked. A real risk (it could publish, delete or reach other people's data) is one line, once; then do as they say.

1. **How it is now.** Run `foreman permissions` once. Tell them only what they asked about (the whole picture only
   if they asked for it), in its bands: what nobody does without them; what only you do, after their yes (each yes
   lasts the minutes it shows); what waits its turn; foreman's own rules. Give an example command of each rule they
   ask about. «Why was this blocked?»: `foreman permissions --refused` lists the refusals of the last days by rule
   and who ran into each (never the command); for one command, `foreman guard --as agent "<the command>"` (or
   `--as orchestrator`), for one file, `foreman guard --file PATH`, says which rule and why. An agent's integration,
   or its git that changes the product's main folder (merge, switching branch, stash…), is foreman's own rule: only
   you do it, in every product; it is not a setting to change. Tell them in one line,
   then offer the change. Note the base it shows: the copy needs it.
   **The reserved files** (their own band): this product's settings and its permission examples, foreman's state
   (its record of the work: not a scratch folder), the list of projects and Claude Code's settings. The guard
   refuses any write to them from a session, agents and you alike (your board is the one exception). Nobody edits
   them by hand from here: a permissions change goes through steps 3-6 (`foreman permissions apply`). The list of
   projects and Claude Code's settings now ask for the owner's yes: write the change in this chat (Write or Edit, or
   one short command) and foreman asks them Yes/No here, for that one change only; an agent never gets the
   question. Tell them so if they ask for one of those.
2. **Their words, as a change** (tell them in one line which one you understood):
   - «nobody may do Y» → a new rule under «never», with table rows that must be blocked, and an allowed row for a
     near miss (a text that only mentions Y);
   - «the orchestrator may do X» → X in a group: an existing one, or a new name for this kind of thing. They will be
     asked yes/no the first time in each stretch of minutes. Rows: «only the orchestrator». «Without asking me» does
     not exist (one mechanism, decided by the owner): offer the group, or taking the rule off (then agents may too);
   - «the agents may do X», «stop blocking X» → take the rule off, or narrow it so X passes and the rest stays;
     rows: allowed for X, still blocked for what stays;
   - a rule marked «from the machine» holds for every product on this machine (the list of projects' `[machine]`):
     taking it off here only is `[deny] drop` in this product's copy (it opens, like taking a rule off); for every
     product it is a change to the list of projects (their yes, as step 1 says);
   - «X must wait its turn» → a prefix in that resource's guard; rows: waits for that resource;
   - «let the agents (or you) change a reserved file» → not a rule of this list: say plainly that those files stay
     the owner's or `permissions apply`'s, and why (step 1).
3. **The candidate.** Copy the live foreman.toml and the guard table (`foreman permissions` names both) to your
   scratch folder and edit the copies, never the live files (the guard refuses it): add
   `# foreman permissions base: HASH` (the base from step 1) as the copy's first line, the rule (a backslash is
   written twice in TOML), and the rows. Then
   `foreman permissions --candidate COPY --table TABLECOPY`. Many rules or hard patterns (a product moving its old
   marked rules to groups): launch a mechanic in a worktree to write both copies and run that command, and bring
   back its output.
4. **Fix before showing.** Exit 2: the copy does not load; fix it. Exit 1: a row fails, or the orchestrator would
   lose a command it needs every day (the lifeline): narrow the rule until it passes; never apply around it.
5. **Tell them what changes**, in plain words, from its output: what becomes blocked, what only you may do after
   their yes, what is freed (each row that becomes allowed, by name), how many example rows the table gains, and
   «I can still do my daily work» (the lifeline passed). Write nothing yet; change what they correct and test again.
6. **On their yes in the chat**: `foreman permissions apply COPY --table TABLECOPY`. When the change allows
   something that was not allowed before (a rule taken off or out of «never», a row freed, a longer yes, a
   command foreman runs on its own added or changed, a brake of the integration taken off), add
   `--opens`: `foreman permissions apply COPY --table TABLECOPY --opens`. Tell them first that a yes/no question
   comes now and that only they can answer it (here or in the panel). A change that only blocks more needs no
   question: their yes in the chat is enough. If apply says it opens something and has no `--opens`, nothing was
   written: tell them what it opens, and run it with `--opens` only if they still say yes. It says «changed after
   the copy»: copy again from step 3. A change that arrives in a branch (an agent's, in a home inside the repo) is
   tested the same way by `foreman integrate`, which stops before merging when it opens something: the same yes/no
   question, with `integrate … --opens`.
7. **Check it**: `foreman check`, and their example with `foreman guard --as agent "<the command>"` (and
   `--as orchestrator` for a group). Commit the home as in every pass.
8. **Close** with one line: what changed, and «tell me "undo the last permission change"». Keep the undo command
   apply printed; on that request, run it (the same yes/no question if undoing frees something). If a change ever
   leaves you unable to run foreman, the copy apply saved in the state folder must go back by the owner's hand (the
   guard refuses that write to you): give them the one `cp` command to type in a terminal, and what it undoes.
