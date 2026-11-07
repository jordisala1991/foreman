---
name: review-plan
description: Plans for backlog issues (orchestrator): when to ask for a plan, how to review and approve it, Definition of Ready, blocks and the definition queue. Use it when a planning agent delivers or the number of unblocked ready issues drops below the target.
---
# Plans and the definition queue (orchestrator)

An issue goes to `ready` when it meets the **Definition of Ready** (approved plan or a brief that is the plan, its
acceptance criterion when the project requires one, sub-issues that can each be integrated alone) and to `done`
when it meets the **Definition of Done** (merged, checked as the plan says, report and one-line result). Approving or
asking for changes to a plan is the orchestrator's; approved means it can run now.

- **Who writes it:** a short `planner` agent (reads code, builds and runs nothing) in
  `reviews/YYYY-MM-DD-plan-<ID>.md`: starting state checked in the code (no invented names), goal and acceptance
  criterion with its baseline (a table input → expected result when the behaviour has cases), approach, what it touches with file:line, collisions with branches in progress, risks,
  how it is checked (agent and orchestrator), the API line if the project uses one, **integrable sub-issues** (T0,
  T1…, each checkable alone) and open questions. **No hour estimates.** Small or measure-only issues: the brief is
  the plan. A review by a `reviewer` agent that lists fix sub-issues counts as that plan once you have added your
  «Review by the orchestrator» section.
- **Review:** read it (its `Misled:` lines first, then its «Repaso» section before approving: what misled the planner will mislead the agent, so fix it in the
  plan or the brief), correct or approve it with a «Review by the orchestrator (date)» section at its end, answer
  its open questions (the owner's go to `foreman backlog decision add`, each one written so it stands alone, as
  `foreman docs owner` says; with `--blocks Tn` for the sub-issues the plan says wait for that answer, never the
  whole issue by habit) and link it:
  `foreman backlog edit ID --plan reviews/…md` and `foreman backlog sub ID Tn --title …` (never by hand). A
  sub-issue that only measures (a report, no code for main) gets `--kind measure` there (or at
  `foreman launch … --kind measure`). The plan names each sub-issue's agent type and model: `--type T[/M]` (`dev/sonnet`) there;
  launch uses it and the Agent call may not be dearer. The brief comes from the plan. Third-party figures cited by a plan are re-checked at their source that day.
  A plan whose criterion changes the meaning of the issue's acceptance block (the goal and criterion agreed with
  the owner) asks them before you approve it, as a decision; a figure, how it is measured or a narrower wording
  that keeps the meaning needs no question.
  A question the owner found hard to answer (notes, «question hard to answer»): write the next ones with what each
  option changes in their day.
- **A hidden check** (docs/config.md, «`[paths] holdout`»): for behaviour a command can judge, ask the planner in
  its brief for one: some rows of the criterion's table go in the plan, the others in `<holdout>/ID/`, where
  production agents and reviewers cannot look. Before approving, read `holdout.toml` and every file under it:
  each command and `setup` step runs with your power, outside the guard, at every integration of the issue, so seal
  only what you would run yourself. The cases test only the contract the plan's «Hidden check» section writes,
  and each `symptom` tells what a user would notice, not the test. Then `foreman holdout seal ID`: the run on main
  must fail the new cases and pass the regression ones, else the case or the plan is wrong. Never quote the hidden
  part in a brief, note, decision or the backlog.
- **Blocks:** apart, with `foreman backlog block ID "reason"` (and `unblock ID`), or by relation with
  `foreman backlog relate A blocks B`: B stays blocked while A is open and is freed when A closes (nothing to
  unblock by hand). A `ready` issue may be blocked. The ready target counts only unblocked ones, and not those
  whose every sub-issue to do waits for a pending decision (`--blocks`).
- **Definition queue:** always `[agents] ready_target` unblocked `ready` issues ahead of the production slots; if it
  drops, planning agents (at most 2 at a time) for the next in order; while a plan is written or waits for review
  the issue is `refining`. Plan the next one even if it is blocked by a branch about to land.
  A triage issue the owner marked as wanted (inbox, «wanted from triage») is planned like any other, but it queues
  **behind** the `ready` ones already there; if its brief is enough as the plan, it goes straight to `ready`.
- **Old plans:** before launching one, check the files it cites have not changed (`git diff --stat <plan commit>
  main`); if they did, review it against main.
- **Integrating:** `foreman integrate … --check` shows the plan's check section to compare with the agent's report,
  and whether the issue's hidden check is sealed (docs/orchestrator.md, «Every pass», step 2).
