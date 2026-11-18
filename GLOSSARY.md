# fm: glossary

One name per fm concept: use the term in bold, in English, and never the words under _Avoid_. It changes only by the owner's decision.

## Work

**Task**
A unit of work in the backlog: one PR, with an id (`FN-3`) and a title.
_Chat_: tarea
_Avoid_: issue

**Subtask**
A task that names a parent: an ordinary task with its own agent, branch and PR.
_Chat_: subtarea
_Avoid_: child task, sub-issue

**Parent**
A task too big for one PR, split into subtasks. Before its Go it closes by itself when every subtask has; on its Go it gets a parent agent, and then it closes only when every subtask is done or canceled and its agent and the owner agree its goal holds.
_Chat_: tarea madre
_Avoid_: epic, umbrella task

**Goal**
The one-line aim the owner sets when filing a task.
_Chat_: objetivo
_Avoid_: claim, objective

**Acceptance criterion**
A line of observable product behaviour, settled with the owner when the task is filed. No agent writes, rewrites or splits it.
_Chat_: criterio de aceptación
_Avoid_: requirement, AC

**Plan**
The file `<home>/plans/ID.md` the planning agent writes for the responsible agent: the decisions it cannot make alone. A trivial task has none.
_Chat_: plan
_Avoid_: spec

**Chain**
A run of one lens: the parent a lens entry files and its steps, one at a time, until its parent agent closes it.
_Chat_: cadena
_Avoid_: campaign, pipeline

**Step**
A subtask of a chain: one finding fixed in one PR, with evidence before and after. Not one of the loop's steps.
_Chat_: paso
_Avoid_: iteration

**Lens**
The question a chain asks of fm, with the evidence that proves a finding.
_Chat_: lente
_Avoid_: audit

**Lens report**
The living file `<home>/reports/lens-NAME.md`, one per lens, never re-created: Direction (the owner's), Frontier, History.
_Chat_: informe de la lente
_Avoid_: chain report

## Agents

**Responsible agent**
The Claude Code session that owns a task end to end, in its own worktree and branch.
_Chat_: agente responsable
_Avoid_: worker, developer, dev agent, work agent, implementer, the agent (alone)

**Planning agent**
The session that turns a task in backlog into a plan and leaves it ready.
_Chat_: agente de planificación
_Avoid_: planner, definer, defining agent

**Parent agent**
The session that follows a parent and its subtasks with the owner: reports from real data, proposes the next subtask and changes, never codes, reviews or merges.
_Chat_: agente de la tarea madre
_Avoid_: coordinator, supervisor

**Hunter**
The first of the two review sessions: it lists every suspicion about a branch, with a confidence each.
_Chat_: hunter
_Avoid_: first reviewer, first pass

**Reviewer**
The second review session: it sees only the hunter's file and checks each finding, keeping what it cannot refute.
_Chat_: revisor
_Avoid_: second pass, verifier, independent reviewer

**Mechanic**
The read-only agent that diagnoses the task behind a health finding (defined by FN-111; not yet in the code).
_Chat_: mecánico
_Avoid_: debugger, diagnoser

## Review

**Review**
The hunter and then the reviewer, run once on each delivered tip of a branch.
_Chat_: revisión
_Avoid_: code review, review pass

**Review round**
One review of a tip. Round 1 reviews the branch; round 2 checks only the fixes.
_Chat_: ronda de revisión
_Avoid_: iteration

**Finding**
One item under To fix in a review file. It has no severity.
_Chat_: hallazgo
_Avoid_: issue, severity label (H/M/L, an L:)

**Verdict**
The reviewer's recorded result for a tip: how many findings stay open.
_Chat_: veredicto

**Report**
The file the responsible agent fills at `<home>/reports/ID.md`, from `foreman/briefs/report.md`.
_Chat_: informe
_Avoid_: write-up

**Description**
The report as fm publishes it: the marked block plus fm's footer, used as the PR body.
_Chat_: descripción
_Avoid_: PR body, PR text

## Integration

**Risk**
The merge risk of a delivered branch: low, medium or high, the higher of impact and doubt.
_Chat_: riesgo
_Avoid_: severity, criticality

**Autonomy**
The project setting that says which risks merge without the owner: manual, low or medium.
_Chat_: autonomía
_Avoid_: autonomy level, auto-merge

**Accept**
The owner's recorded decision that a task in review may merge. The task stays in review.
_Chat_: aceptar
_Avoid_: approve, sign off

**Merge**
The provider's act of putting the branch into main. Accept is not merge.
_Chat_: merge
_Avoid_: integrate, land

**Reject**
The owner's act of returning a delivered task to its agent with a note.
_Chat_: rechazar
_Avoid_: send back, devolver, bounce

## Statuses

**triage**
A task without acceptance criteria, or one a health check or a source filed. It waits for the owner's go.
_Chat_: triaje
_Avoid_: inbox

**backlog**
A task with acceptance criteria, waiting for a planning agent.
_Chat_: por planificar

**refining**
A task whose planning agent is working.
_Chat_: planificándose
_Avoid_: defining

**ready**
A task with acceptance criteria and a plan (or none, if trivial), waiting for a responsible agent.
_Chat_: lista

**in progress**
A task whose responsible agent is working.
_Chat_: en marcha
_Avoid_: doing

**in review**
A delivered task waiting for its checks, the review and the owner's accept.
_Chat_: cola de integración
_Avoid_: integration queue

**done**
A task whose PR was merged.
_Chat_: hecha

**blocked**
A flag with a reason, on any status; the loop leaves the task alone until the owner clears it. Not a status.
_Chat_: bloqueada
_Avoid_: parked

**canceled**
A task closed without being delivered.
_Chat_: cancelada

## System

**Owner**
The person who owns the project and files its tasks.
_Chat_: dueño
_Avoid_: human, operator, boss

**Project**
A repo plus its home, served by fm.
_Chat_: proyecto

**foreman**
The command that runs fm (`bin/foreman` of the plugin). Not `fm`: macOS ships its own /usr/bin/fm.
_Chat_: foreman
_Avoid_: fm (as the command)

**Home**
The project's folder: `foreman.toml`, `backlog.json`, `state/`, `plans/`, `worktrees/`.
_Chat_: casa
_Avoid_: project folder, workspace

**Loop**
The plain process, with no model, that moves tasks and launches agents.
_Chat_: bucle
_Avoid_: orchestrator, coordinator, scheduler

**Tick**
One run of the loop's steps.
_Chat_: pasada
_Avoid_: iteration

**Slot**
One agent running at once; the project's `[agents] slots` is how many.
_Chat_: hueco
_Avoid_: seat, lane

**Health check**
Code that looks for a broken state on each tick.
_Chat_: chequeo de salud
_Avoid_: monitor

**Health finding**
A broken state a health check found; fm files it as a task in triage.
_Chat_: problema de salud
_Avoid_: health task, health issue, triage issue

## Old names

Not read by any code; `tests/test_no_old_names.py` proves it.

- Variables `FM_TASK`, `FM_HOME`, `FM_SLOT`, `FM_PROJECT`, `FM_AGENT`, `FM_REGISTRY`, `FM_PINNED`, `FM_LOCKS`, `FM_CURSOR`, `FM_RESOURCE`, `FM_CLAUDE_PROJECTS`: now `FOREMAN_*`.
- Project file `fm.toml`: now `foreman.toml`.
- `~/.config/fm/` (`projects.json`, `panel.token`): now `~/.config/foreman/`.
- `~/.local/state/fm/locks`: now `~/.local/state/foreman/locks`.
- launchd jobs `dev.fm.loop`, `dev.fm.panel` and logs `fm-loop.log`, `fm-panel.log`: now `dev.foreman.*` and `foreman-*.log`.
- Plugin `fm@fm`, marketplace `fm`: now `foreman@foreman`, marketplace `foreman`.
- Panel cookie `fm_key`: now `foreman_key`.
- Default branch prefix `fm/`: now `foreman/`.
- GitHub markers `<!-- fm:KEY -->` and `fm-link:`, the evidence branch `fm-evidence`, the project name `fm` in both `pyproject.toml`, the panel's saved project `fm-project`: now `<!-- foreman:KEY -->`, `foreman-link:`, `foreman-evidence`, `foreman`, `foreman-project` (the branch `fm-evidence` already on GitHub is left as it is).
