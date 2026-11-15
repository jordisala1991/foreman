---
name: define-task
description: Use when the owner wants to turn the conversation into a task of this project ("make it a task", "put it in the backlog", "define the task", "do it", in whatever language the owner speaks) or to define an existing triage task (give it acceptance criteria) before its Go. Writes the task with `fm` (title, goal, acceptance criteria, detail), splits it at filing time when it does not fit in one PR, then lets it in or leaves it in triage. Never plans, codes or launches an agent itself: the loop does that. Works only in a folder of a project registered with fm.
---

# Define a task

## This project's values

!`"${CLAUDE_PLUGIN_ROOT}/bin/fm" project`

Below, `{project}`, `{prefix}`, `{home}`, `{repos}`, `{main}` and `{slots}` stand for the values above; never use one
from memory. If the block above is an error (`fm: …`), tell the owner this folder is in no fm project and stop.

Turns what the owner and this chat already know into one task (or a parent with subtasks) in the backlog of
**{project}**, with `fm` only (on your PATH). Defining is the step that turns triage into something the loop can plan and do;
nobody defines it later, and the planning agent never changes what is written here.

## 1. Gather

From the conversation (and `fm show {prefix}-n` when defining an existing task), collect:

- **Title**: short, says the outcome ("fm edit: change a task before its agent starts").
- **Goal** (`--goal`): one line, what is true when it is done.
- **Acceptance criteria** (`--criteria`, one per line): product, checkable from outside: visible behaviour, a
  command and its output, a table input → result when there are cases, what must NOT change. Never the how: no
  files, functions, "a test shows". Each line is something a reviewer can check against the running tool.
- **Detail** (`--detail`): why and evidence (what was seen, ids, commits), decisions already taken ("Owner
  (date): …"), what is out of scope. Enough that an agent with no access to this chat makes no decision the owner
  should make.
- **Size**: one task is one PR. If it does not fit, split now: a parent with one subtask per PR, each a vertical
  slice (end to end, mergeable and valuable alone). If something is still undefined, say it honestly: defining it can
  be the first subtask, whose result is the next subtasks, not code. Never lower risk artificially (no splitting
  just to get under a size threshold, no dodging a path the task needs).
- **A parent's criteria** are not its subtasks' goals repeated: «every subtask is done or canceled», the rules each
  subtask must meet to close (e.g. «seen acting on a real task»), and what must hold at the end across all of them.
  Its detail lists the planned subtasks in order (adjustable) and the decisions taken as `decision.<key>=<value>`.
- **Model and effort** of the task's agent (`--model`, `--effort`), from the rubric below.
- **Blocked by** (`--blocked-by {prefix}-n`): a task that needs another one done first.

Every claim the task makes about the code (a name, a file, what exists, what is already integrated, how many) is
checked in {repos} today and cites file:line; a guess is written as one. Work that is not on `{main}` yet is a
`--blocked-by`, never «define after X» or «X adds Y». Most of what misleads agents is a task that says something the
code does not.

Ask the owner only for what is missing, and only what the acceptance criteria need: one message, in the owner's
language, each question standing alone with your recommendation. Do not ask what you can read in the conversation,
the backlog (`fm list`, `fm show`) or the code ({repos}, read only). Close every decision now; none is left for the
agent.

### Model and effort rubric

| The work | Model / effort |
|---|---|
| Mechanical or very scoped: a rename, a config value, a one-file fix with a known cause | sonnet / low |
| Scoped, with clear criteria and some logic: a command and its tests, a bounded feature | sonnet / high |
| Design, undiagnosed debugging, or a change to fm's own protocol (loop, review, risk, guard) | opus / medium, or high when hard |
| Exceptionally complex: work opus/high could not be trusted with (rare) | fable / high |

Pick the lowest row that fits; between two, take the higher. fable only for exceptional complexity, never because
opus was a close call.

## 2. Confirm and choose

Show a short draft, one line each: title, goal, criteria (one bullet each), the split if any, model/effort with the
rubric row that chose them. The owner may change any of it. Then ask, in the owner's language, whether it goes in now
or stays in triage, unless the owner already said it ("do it" → in; "note it for later" → triage). Say before filing
that what is let in is launched as soon as one of the {slots} slots is free.

## 3. File it

- New, in: `fm add "title" --goal "…" --criteria "…" --detail "…" --ready`.
- New, for later: same with `--triage`, then `fm go {prefix}-n` on the owner's Go.
- Without criteria yet: `--triage` only; it is defined here before its Go.
- Split: the parent first (it has no agent and closes itself when its subtasks do), then each subtask with
  `--parent {prefix}-n`.
- An existing triage task: `fm edit {prefix}-n --title/--goal/--criteria/--detail`, keeping the useful text, then
  `fm go {prefix}-n`. Replacing a task with a new one only when edit cannot do it; cancel the old one and name it in
  the new detail ("Was {prefix}-n").

## 4. Report

One or two lines, in the owner's language: the ids with their titles, and whether it goes to planning now or stays in
triage. Then stop.

## Never

- Write code, plans or anything in {repos}, or edit `backlog.json` by hand.
- Leave a decision open for the agent, or write criteria about how instead of what.
- Let a task in without telling the owner it will launch.
