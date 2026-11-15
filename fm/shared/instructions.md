# fm: what every home shares

Before using a value below, read `fm.toml` in this folder; never use one from memory.

| placeholder | key | default when absent |
|---|---|---|
| `{project}` | `[project] name` | the folder's name |
| `{prefix}` | `[project] prefix` | FM |
| `{repo}` | `[project] repo` | repo (relative to this folder if not absolute) |
| `{main}` | `[project] main` | main |
| `{fm}` | `[project] fm` | none: `fm init` prints the line to add |
| `{slots}` | `[agents] slots` | 2 |

## This folder

This folder is the home of the project **{project}**, run with `fm`. Here you talk with the owner, in the owner's
language, in plain words. The code lives in `{repo}`; work is integrated into its branch `{main}`.

## How fm is run

`fm` is `{fm}`; from this folder it finds the project by itself. `{fm} --help` lists every command.
A new repo needs `claude` opened in it once, so that it trusts the folder.

## The backlog

The backlog is `backlog.json` and **is changed only with `fm`**: never by hand, nor in another file or tool.

- `fm list` (and `--all`): the open tasks and the pending questions. `fm show {prefix}-3`: one task whole.
- A task with acceptance criteria goes through planning (an agent writes the plan, without touching the criteria) and
  then is ready (its responsible agent does it end to end). Without criteria it can only be in triage. Triage tasks
  wait for the owner's Go (`fm go {prefix}-n`).
- What is not in triage, the loop launches as soon as there is a free slot ({slots} at once): say so before filing it.

## Defining tasks with the owner

Tasks are defined **here, with the owner, when they are filed**, with the `define-task` skill: follow it whole. No
agent defines them later. One task is one PR: what does not fit in one is split now, into a parent with one subtask
per PR. Agents never split tasks.

## What the owner can ask here

- **Answer an agent:** `fm answer "{prefix}-3 Q1" "text"`. Allow or deny an action the guard stopped:
  `fm allow "{prefix}-3 Q2"` / `fm deny …`.
- **The integration queue:** `fm accept {prefix}-3` (the loop merges its PR) or `fm reject {prefix}-3 "note"` (back to
  its agent; the note counts as the owner's decision for the reviewer).
- **Others:** `fm metrics`, `fm health`, `fm pause` / `fm pause --off`, `fm cancel {prefix}-n`,
  `fm unblock {prefix}-n --relaunch`.
- **Talk with a task's agent:** `claude attach <short id>` (in `fm show`, under `agent.short`), or in the panel.

## What is not done here

- No coding in this folder nor in `{repo}`, which is where work is integrated and must stay clean. The code is written
  by the tasks' agents.
- No hand edit of `backlog.json`.
