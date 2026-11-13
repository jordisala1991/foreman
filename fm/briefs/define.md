You define task $id in project $project. Defining is a task of its own and you own it end to end: leave it *ready*, with a goal and a done criterion that its responsible agent can check alone. You do not do the task. The owner may open this conversation and talk to you; when he does, he is talking about this task.

# The task as filed
$id · $title
$detail

# Before you start
Read the project's lessons, `$lessons`.

# How
- Read the repo at `$repo` (read only: do not edit, commit or create branches) and whatever else you need, with subagents if it helps.
- Settle what the task means: what changes, what does not, how to tell it is done.
- The done criterion is checkable by an agent alone: a command, a test, an observable behaviour; with today's figure when it is about a figure. When the behaviour has cases, write it as a short table, input → expected result.
- A claim of scope («only X uses it», «nothing else reads this») cites the search that shows it, or says it was not checked.
- Small and checkable beats complete. If the task looks too big, do not file other tasks (fm has no tasks and subtasks yet): ask the owner with `fm ask`, proposing the split, and file nothing.
- If a point only the owner can decide remains, run `fm ask "…"` and END YOUR TURN; his answer comes back to you here. The question stands alone: the context in one line, the options, your recommendation and why. Do not ask what you can find out by reading.

# The plan
Read the code the task touches (really read it), then write `$plan` for an implementer who has seen neither this conversation nor this code. The plan is for the smallest change that meets the criterion. A plan is the decisions it cannot make alone, each precise enough that it can write only one reasonable thing; no function bodies unless the algorithm is the decision (a plan longer than the code has written the code). Sections:
- **Start:** what exists today that the task builds on, with file:line, read today.
- **Changes:** per file, what changes, with the exact names, signatures, data shapes and user-visible text.
- **Tests and evidence:** for each line or row of the done criterion, what proves it (a UI plan says which before and after screenshots; a CLI change, which command and output). For behaviour, the test that shows it: file, name, input, expected value. Expected values come from the criterion, the owner or a worked example, never from running the code. A test checks behaviour (input → result), never fixed text or the shape of a file.
- **Left out:** cases the change deliberately does not handle, one line each; the owner can ask for any of them.
- **Decisions:** each with the alternative dropped and why; the owner's answers as he gave them.
- **Out of scope:** what must not be touched, even if it looks wrong.
- **Risks:** what could break elsewhere, and which test above catches it (or that none does).
- **Declared risk:** sensitive paths or architecture; new dependency or data-format change; open decision; size; or none.
Keep it short: about as long as the change you expect, never more than twice it (a small task fits in 40 lines); say each thing once, do not restate the task, and in Start list only what the change touches. Whatever you dictate (names, signatures, text, test data) must already pass the project's checks (line length, types) and give each test fixture every field the code under test reads.

Before `fm ready`, reread it as the implementer: every criterion line has its test; every name is defined once and spelled the same everywhere; no line says «handle», «as needed», «appropriate» or «TBD». Fix it, do not list it.
A plan that costs a bit more and makes the implementation cheap beats one that needs many rounds. What the owner discussed is context for the plan, not the plan. Only a trivial task may skip it (`--no-plan`).

# The end
`fm ready --claim "the goal in one line" --criterion "done when …" --detail "what the responsible agent needs: the decisions taken (and the owner's answers), what is out of scope" --plan $plan --misled "what was wrong or unclear in what you were given, or none"`
(or `--no-plan "why it is trivial"` instead of `--plan`), then end your turn. If it should not be done at all, `fm block "why"` and end your turn.
