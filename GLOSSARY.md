# fm: glossary

One name per fm concept: use the term in bold, in English, and never the words under _Avoid_. It changes only by the owner's decision.

## Work

**Task**
A unit of work in the backlog: one PR, with an id (`FN-3`) and a title.
_Chat_: tarea
_Avoid_: issue, ticket, item

**Subtask**
A task that names a parent: an ordinary task with its own agent, branch and PR.
_Chat_: subtarea
_Avoid_: child task, sub-issue

**Parent**
A task too big for one PR, split into subtasks. It never gets an agent and closes by itself when every subtask has.
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

## Agents

**Responsible agent**
The Claude Code session that owns a task end to end, in its own worktree and branch.
_Chat_: agente responsable
_Avoid_: worker, developer, dev agent, work agent, implementer, the agent (alone)

**Planning agent**
The session that turns a task in backlog into a plan and leaves it ready.
_Chat_: agente de planificación
_Avoid_: planner, definer, defining agent

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
_Avoid_: pass, iteration, cycle

**Finding**
One item under To fix in a review file. It has no severity.
_Chat_: hallazgo
_Avoid_: issue, comment, severity label (H/M/L, an L:)

**Verdict**
The reviewer's recorded result for a tip: how many findings stay open.
_Chat_: veredicto
_Avoid_: result

**Report**
The file the responsible agent fills at `<home>/reports/ID.md`, from `fm/briefs/report.md`.
_Chat_: informe
_Avoid_: summary, write-up

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

**Send back**
Returning a delivered task to its agent with a note.
_Chat_: devolver
_Avoid_: reject, bounce

## Statuses

**triage**
A task without acceptance criteria, or one a health check or a source filed. It waits for the owner's go.
_Chat_: triaje
_Avoid_: inbox, new

**backlog**
A task with acceptance criteria, waiting for a planning agent.
_Chat_: por planificar
_Avoid_: todo

**refining**
A task whose planning agent is working.
_Chat_: planificándose
_Avoid_: planning, defining

**ready**
A task with acceptance criteria and a plan (or none, if trivial), waiting for a responsible agent.
_Chat_: lista
_Avoid_: planned

**in progress**
A task whose responsible agent is working.
_Chat_: en marcha
_Avoid_: doing, working

**in review**
A delivered task in the integration queue, waiting for its checks, the review and the owner's accept.
_Chat_: cola de integración
_Avoid_: queued, integration queue

**done**
A task whose PR was merged.
_Chat_: hecha
_Avoid_: merged, closed

**blocked**
A flag with a reason, on any status; the loop leaves the task alone until the owner clears it. Not a status.
_Chat_: bloqueada
_Avoid_: stuck, parked

**canceled**
A task closed without being delivered.
_Chat_: cancelada
_Avoid_: dropped

## System

**Owner**
The person who owns the project and decides every name, scope and merge.
_Chat_: dueño
_Avoid_: user, human, operator, boss

**Project**
A repo plus its home, served by fm.
_Chat_: proyecto
_Avoid_: product, repo

**Home**
The project's folder: `fm.toml`, `backlog.json`, `state/`, `plans/`, `worktrees/`.
_Chat_: casa
_Avoid_: project folder, workspace

**Loop**
The plain process, with no model, that moves tasks and launches agents.
_Chat_: bucle
_Avoid_: daemon, orchestrator, coordinator, scheduler

**Tick**
One run of the loop's steps.
_Chat_: pasada
_Avoid_: pass, cycle, iteration

**Slot**
One agent running at once; the project's `[agents] slots` is how many.
_Chat_: hueco
_Avoid_: seat, lane

**Health check**
Code that looks for a broken state on each tick.
_Chat_: chequeo de salud
_Avoid_: monitor, probe

**Health finding**
A broken state a health check found; fm files it as a task in triage.
_Chat_: problema de salud
_Avoid_: health task, health issue, triage issue, alert
