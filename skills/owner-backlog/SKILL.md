---
name: owner-backlog
description: For the owner, loaded by the orchestrator - when the owner asks, in their own words and in any language, to note down, add or define work (an idea, «I want X», «we should…»). A guided chat that turns it into backlog issues with a goal and a done criterion, written only after they confirm the draft.
---
# Defining work with the owner (orchestrator)

Only when the owner brings an idea to note down or do, not for a passing «what if…» in a chat about something else
(ask in one line if it is unclear). Everything here is written to them as `foreman docs owner` says (docs/owner.md),
in the project's language (`[project] language`): the chat and what goes into the backlog. It runs in this session:
subagents cannot talk to them. A triage idea they marked «wanted» in the panel does not come here: it is planned as
the definition queue says (`foreman docs orchestrator`).

1. **Their words first.** Keep them as they are: they go, quoted, with the date and «(chat)», into the detail. Look
   for the same thing in the backlog (`foreman backlog`, `foreman backlog show ID`, the pages): if it is there, offer
   to add to that issue instead (`foreman backlog edit ID --detail …`) and stop here on their yes.
2. **Questions**, as many as are needed and no more, one per message, each with your own proposal they can accept
   with an «ok»:
   - **what for**: what changes for them once it is done (the goal, `--claim`);
   - **how we will know it is done**: something checkable, with a figure when there is one; give today's figure if
     you know it, else «the plan measures today's figure» (the done criterion, `--criterion`);
   - **what is out** (in the detail, «Out: …»);
   - **now or just noted**: now is status `backlog` (planned after what is already ready); noted is `triage`.

   Never ask what they already said, in this chat or when telling the idea; never ask the same thing twice. When
   they say to note it as it is, at any moment: stop asking, and it goes to `triage` with their words and no goal or
   criterion (steps 4 and 5, without the questions).
3. **Several pieces of work**: two results that make sense apart, or a criterion with two unrelated checks, become
   one issue each, with its own goal and criterion; propose the split and let them choose. Each goes in the project
   of the closest existing issue. Never create a project here: propose it, and decide it as the orchestrator
   afterwards.
4. **The draft.** First, for each issue:
   `foreman backlog add PROJECT --title … --short … --detail … --claim … --criterion … --status … --dry-run`; it shows the id, position and status it would get, and any validation error
   (a project that requires a criterion, a duplicate id) before they see anything. Then show it in a few plain lines
   per issue: name, what for, done when, out, now or noted. Write nothing yet; change what they correct and show it
   again.
5. **On their yes**, the same command without `--dry-run`. Title and short name are plain words for them (no ids,
   paths or internal names); a `foreman plain` warning on either: rewrite it and fix it with
   `foreman backlog edit ID --title …` or `--short …` before telling them. A goal or criterion you proposed and they
   accepted says so in the detail («criterion proposed in the chat, accepted by the owner»).
6. **What needs digging** (is it feasible, what is today's figure, how big): write the issue with what is agreed,
   tell them you will come back with the answer, and send it to a planner or a short agent as usual; bring back
   only its conclusion. Never hold the chat waiting for an agent.
7. **Close** with one line per issue: its name, its id in brackets, and what happens next («planned after what is
   ready», «noted as an idea»). Nothing is launched in the middle of the chat. What they agreed is the issue's
   commitment: the planner starts its plan from it, and asks them only if it must change its meaning.
