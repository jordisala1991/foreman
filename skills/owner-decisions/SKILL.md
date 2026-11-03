---
name: owner-decisions
description: For the owner, loaded by the orchestrator - when the owner asks, in their own words and in any language, to go through their pending questions or decisions. One at a time - context, what each option changes for them, the recommendation; they may ask for another explanation; an answer in the chat resolves it as the panel does.
---
# Going through the owner's questions (orchestrator)

Only when the owner asks. Everything here is written to them as `foreman docs owner` says (docs/owner.md), in the
project's language (`[project] language`): explain the decisions' texts in other words, never translate them.

1. **Run `foreman backlog decision list --json` once.** It gives this product's pending decisions in the order to
   go through them: those that hold work first, each group oldest first. Only this product's: another product's
   questions are for that product's session (say so if they ask). None: say so in one line and stop. Else one line:
   «N questions; I'll go one by one, those that hold work first».
2. **One message per question**, short enough for a phone:
   - «Question k of N: <its title in plain words>»;
   - what it is about and why it comes now: the decision's text and its issue's title; when that is not enough, the
     plan's summary for the owner and the paragraph of this question (its id in brackets) - read those parts of
     `plans`, not the whole plan;
   - each option, numbered: what changes for them, concretely (what they will see or do differently);
   - the recommendation and why; what waits for the answer (`holds`), by name, not by id;
   - how to answer: the option (number or words), their own words, «skip», «explain it another way», or «whatever
     you think».
3. **Their answer** closes it now, before the next question:
   - an option → `foreman backlog decision resolve ID --option N` (`by` becomes «Owner, … (chat)»); tell them in one
     line what it frees;
   - their own words that clearly pick an option → that `--option N`, and say which one you took;
   - their own words that do not → `--answer "<their words>" --free`, and say how you will apply them; if they do
     not settle the question, keep talking until they do, or leave it pending;
   - «whatever you think» («lo que tú veas») → close it with the recommendation, recorded as chosen by you at their
     request: `--option <the recommended option> --by "Orchestrator at the owner's request, YYYY-MM-DD HH:MM
     (chat)"`; when the recommendation is not one of the options, `--answer "<the recommendation>" --free` with the
     same `--by`; with no recommendation, ask them or leave it pending. Tell them which one you took and that a
     change of mind later is a new question, not a reopening;
   - «skip» → leave it pending; next one;
   - the command says «already resolved» (answered in the panel meanwhile) → tell them what they answered there.
4. **«Explain it another way»**: change the angle, never repeat: (a) a concrete example of next week with each
   option; (b) what happens if nobody answers; (c) the cost or risk of each, with figures; (d) a comparison with
   something of this product they know. After two tries offer to skip it, or a short `planner` agent to find out
   what is missing, and bring back only its conclusion. Note it (`foreman note "question hard to answer: ID, why"`)
   so the next questions are written better.
5. **At the end**: one line with how many were answered and skipped. Then apply the answers as the inbox procedure
   says (`foreman docs orchestrator`, «The owner»: the plan, the sub-issues, launch what they freed); never launch
   in the middle of the conversation.
