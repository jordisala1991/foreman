---
name: orchestrator-pass
description: One pass of the orchestrator loop (the prompt foreman's hooks module sends, starting with the project's loop prompt): act on each line of its summary, integrate delivered branches, keep the slots and the definition queue full, notes and board. Use it on every loop prompt, when closing a batch, and when a recurring task is due.
---
# One orchestrator pass

The prompt carries the pass: after the loop prompt, «Loop pass HH:MM … Act on each line» (foreman already wrote the
`loop` heartbeat). A pass with nothing actionable never reaches you: the owner sees it as one line on screen
(never sent to the model), so a pass you get always has at least one line to act on. Lines marked `(info)` only inform (a
pause, a lock another session runs, a pipeline below target with nothing to launch or plan). Without that summary
(the hooks module not running in this session), run `foreman loop` yourself (`--json` gives each notice's `kind`
and `actionable`). The full procedure is `foreman docs orchestrator`; the
project's rules win. Whatever you tell the owner (chat, decisions, inbox replies, result lines) follows
`foreman docs owner`.

1. **Each line of the summary, in order:**
   - *Context … renew this session's memory in place*: put in the board what only your memory holds (the owner's
     open requests, agreements, what you were about to do), then `foreman renew` (`foreman docs orchestrator`,
     «Renewal»); with live agents, wait for them and launch no new production. After the compaction foreman's pack
     comes back and a pass follows; nothing to tell the owner.
     When the line says the last renewal freed too little it only informs: thin out what it names; foreman renews
     again by itself at 1.5 × the threshold.
   - *foreman X in this session, Y installed*: tell the owner, in plain words, to type `/reload-plugins` in this
     session (a terminal included), unless the line says the desktop app loads it by itself: then nothing to do.
     *Sessions of this product on an older
     foreman*: tell the owner which ones need `/reload-plugins`, or relaunch the ones you launched (a session that
     loads the new version by itself, as the desktop app does, never comes here: foreman waits 3 min after an
     install before counting one).
   - *WIP (pipeline not full)*: fill production slots from the top of `ready`, skipping what the line says waits
     for a decision (the owner's answer frees it); ask for plans if ready is short
     (skill `review-plan`). Launch each agent with `foreman launch NAME --issue ID --sub Tn --repo R --branch B`
     and start its brief with the line it prints; if the hook warns that a production agent went without it,
     `foreman agents --name AGENT_ID NAME --issue … --branch …`.
   - *Agents idle without a lock* / *of an earlier session*: wake them (SendMessage) if they wait for something that
     ended, relaunch, or `foreman agents --stop NAME`.
   - *Live production agents without a name*: give each its identity (`foreman agents` shows the id; `--json` the
     transcript: read its brief) with `foreman agents --name AGENT_ID NAME --issue ID --sub Tn --branch BRANCH`;
     until then the pass cannot tie it to its branch or its progress. One that no longer lives: `foreman agents
     --stop AGENT_ID`.
   - *Locks*: a foreign holder or a long hold: `foreman lock RESOURCE check`; explain acquire/release to another
     session if it misuses it.
   - *Ready to integrate*: with a report, `foreman integrate … --check`, then the real run; without one, ask for it.
     The report shown is the sub-issue's `report`, else the one the agent named (`progress --done --report`), else
     the one the branch adds, else one in the home's report folders (never a plan); if the agent named another only
     in its final message, set it: `foreman backlog sub ID Tn --report PATH`, one call per sub-issue. `report X not
     found`: that field or the named path points nowhere; fix it. `foreman integrate` takes that report by default.
     An agent has delivered when `foreman agents` says `delivered` (it ran `foreman progress --done`), not when
     Claude Code says «completed»: that also comes when it only stops to wait for a task of its own. Read the
     outcome next to it: `partial` or `failed` is read in the report before integrating, never merged as done.
     `integrate` stopping with `== review: STATE` (exit 2) is the review gate, not a failure: do what the state says
     (next two lines).
   - *Need a review* (`review_needed`): launch a `reviewer` with the command the line gives (`foreman launch NAME
     --review REPO:BRANCH --issue ID --sub Tn`: no worktree; the branch is held while it lives). Brief: the launch
     line, «A branch review, round N (agents/reviewer.md)», the issue, the plan and the agent's report; give it no
     opinion of yours about the branch. Round 2 (the fixes are in): resume the reviewer of round 1 with SendMessage
     («round 2: the fixes are in, check each finding of PREVIOUS_REVIEW and only what they changed»), or launch one
     with the previous review in its brief. A branch whose only reason is its size and that is a mechanical change
     proven by its tests (a split checked by the golden corpus) may go in with `foreman integrate … --no-review
     "why"` instead; never one that touches delicate paths or was delivered `partial`.
   - *Review findings* (`review_findings`), round 1: send the H and M back to the agent that made the branch, with
     SendMessage to the id the line gives: the review's path and «fix or refute each H and M with proof, a "Review,
     round 1" section in your report, commit, `foreman progress --done` again». Do not fix nor drop findings
     yourself; if you think one is wrong, say so in the message: the agent refutes it with proof and the reviewer
     judges it in round 2. If it cannot be resumed (another session, an agent of another day), the plan B command
     the line gives: a new agent on the branch's worktree, brief: the plan, its report and the review. After round
     2 with H or M open there is no third round: ask the owner with the command the line gives (`--page` the review:
     that is how the pass finds the question), written as `foreman docs owner` says, each option with its effect;
     while it is pending the line only informs. With their answer, do what the line says (in as it is with
     `--approved`, another agent and a round 3, or dropped).
   - *Measurements delivered (nothing to merge)* (`measure_delivered`): the branch only brought a report (its
     sub-issue is `kind = "measure"`, or the agent delivered with `--kind measure`). Read the report, then `foreman
     integrate REPO BRANCH "ID Tn" --measure --result "…"`: no CI, no merge; it removes the worktree and the branch
     (`--keep-branch` keeps it for a later sub-issue). If it left code that belongs in main, mark the sub-issue
     `--kind code` and integrate it as usual. A branch whose sub-issues are all closed without a commit shows in
     neither list.
   - *Unmerged branches of open sub-issues with no worktree* (`branch_no_worktree`): work in a branch whose
     worktree is gone (removed by hand, or a measurement closed with `--keep-branch` and reopened); its issue is not
     *in progress with no live agent*. Delivered: `foreman worktree REPO NAME BRANCH` (it reuses the branch), then
     integrate as above (`--measure` for a measurement). Not delivered: relaunch the sub-issue on it (`foreman launch
     … --repo REPO --branch BRANCH`). Dead: cancel the sub-issue and delete the branch with a note giving its tip.
   - *Unmerged branches with no worktree that the backlog does not name* (`loose_branches`): work only on this
     machine, not on the remote and not in main. Kept: note it in its sub-issue (`--branch`) or its issue. Not
     needed: ask the owner before deleting it (it may be their own work: agents commit with the owner's name), then
     `git branch -D` with a note giving its tip.
   - *Stopped with an incomplete delivery* (`delivery_incomplete`): the delivery check refused its stop twice and
     let it go; resume it with SendMessage saying what is missing, or finish or drop the work yourself.
   - *Worktrees the backlog does not name* / *in progress with no live agent*: note them in their sub-issue or
     remove them; back to `ready` or relaunch.
   - *foreman check, backlog*: fix the backlog with `foreman backlog` commands (`foreman check` lists every problem).
   - *Hung shells*: `foreman hung-shells --kill` after a look.
   - *Recurring tasks overdue*: the last section below.
   - *Inbox*: `foreman inbox new`, act, `foreman inbox reply`, `foreman inbox read ID`. A decision «resolved from
     the panel» needs no `decision resolve`: apply it before launching what it freed; one in «free text» is the
     owner's own words: read and interpret it first (unclear: a new `decision add`). An owner mark «wanted from
     triage»: `ready` if the brief is enough as the plan, else `refining` with a planner; owner-only questions as
     decisions; behind what is already ready. «No longer wanted» with an agent already building it: ask him before
     stopping it. Then read it.
   - *Project checks* (their own lines): what the rules say about each.
2. Once the lines not marked `(info)` are done and everything waits: stop here.
3. Notes for what only you saw (`foreman note`); rewrite the board; commit the home if it changed.
4. Exit 3 from `foreman integrate` is not a failure: the PR is green and waits for the owner; ask once (inbox or
   decision, written as `foreman docs owner` says) and do not ask again until they answer.

## Closing a batch and recurring tasks

Close a batch when its last branch is integrated, and in the next pass after 5 days without a close (without a
closed block, the review only proposes something for a concrete failure).

- **Notes into pages:** consolidate the notes into the project's pages as current state and conclusions (chronology
  at most in a short «History»), say up to which note time it goes, check with grep what a note says was «written
  down in X», and tell the owner (`foreman docs owner`).
- **Review of the way of working:** what failed in the block; for each failure, first whether a tool can fix it (a
  script or hook before a rule). At most two changes proposed to the owner, plus which rule did not help and can go,
  each with its failure in plain words; a change enters only if the owner approves. A new rule is one sentence
  (the story goes to the notes) and merges with or replaces another. An improvement removed for a failure stays as
  an issue to bring it back.
- **Recurring tasks** are the `[[recurring]]` tables of foreman.toml (`foreman recurring` shows the last run and
  which are late). Before adding one, see if an existing one fits. A task records it ran with `foreman recurring
  done NAME` (or the report its `glob` source looks for); a daily one at a fixed time uses `at = "HH:MM"`
  (`docs/config.md`).
  - *Daily reports* (watch, health check…): in the first pass after it, read the report, act on what is out of
    bounds, write the heartbeat the task names.
  - *Weekly lint* of the project's pages: a `mechanic` agent fixes broken links, figures without a source, stale
    states and orphan pages; what changes a decision goes to the owner (`foreman backlog decision add`).
