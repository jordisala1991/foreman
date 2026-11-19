# fm: the project {project}

This session is in a folder of the project **{project}** (task prefix {prefix}), run with `foreman`. Its home is `{home}`;
its code lives in {repos}; work is integrated into the branch `{main}`. Here you talk with the owner, in the owner's
language, in plain words.

## How fm is run

`foreman` is on your PATH in this session and finds the project from the folder you are in; `foreman --help` lists every
command. A new repo needs `claude` opened in it once, so that it trusts the folder.

## The backlog

The backlog is `backlog.json` in the home and **is changed only with `foreman`**: never by hand, nor in another file or tool.

- `foreman list` (and `--all`): the open tasks and the pending questions. `foreman show {prefix}-3`: one task whole.
- A task with acceptance criteria goes through planning (an agent writes the plan, without touching the criteria) and
  then is ready (its responsible agent does it end to end). Without criteria it can only be in triage. Triage tasks
  wait for the owner's Go (`foreman go {prefix}-n`).
- What is not in triage, the loop launches as soon as there is a free slot ({slots} at once): say so before filing it.

## Defining tasks with the owner

Tasks are defined **here, with the owner, when they are filed**, with the `define-task` skill: follow it whole. No
agent defines them later. One task is one PR: what does not fit in one is split now, into a parent with one subtask
per PR. Agents never split tasks.

## What the owner can ask here

- **Answer an agent:** `foreman answer "{prefix}-3 Q1" "text"`. Allow or deny an action the guard stopped:
  `foreman allow "{prefix}-3 Q2"` / `foreman deny …`.
- **The integration queue:** `foreman accept {prefix}-3` (the loop merges its PR) or `foreman reject {prefix}-3 "note"` (back to
  its agent; the note counts as the owner's decision for the reviewer).
- **Others:** `foreman watch` (one line each time something starts needing you: run it with `--once`, relaunched, as a background command, or without `--once` in a monitor), `foreman metrics`, `foreman health`, `foreman pause` / `foreman pause --off`, `foreman cancel {prefix}-n`,
  `foreman unblock {prefix}-n --relaunch`.
- **Talk with a task's agent:** `claude attach <short id>` (in `foreman show`, under `agent.short`), or in the panel.

## What is not done here

- No coding in this session: not in the home nor in {repos}, where work is integrated and must stay clean. The code
  is written by the tasks' agents.
- No hand edit of `backlog.json`.
