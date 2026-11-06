# Writing to the owner

The owner knows the product, not foreman's insides, has not followed the work since your last message and may be
reading on a phone. Everything they read must make sense on its own. Write it in the owner's language
(`[project] language`), whatever the language of the code and of this file. The project's rules win when they say
otherwise.

**Covered:** the orchestrator's chat, decisions (`foreman backlog decision add`), inbox replies, `foreman progress`
lines, an issue's title and result line, PR titles and bodies, a plan's summary and its questions for the owner, and
telling the owner the orchestrator session changed. **Not covered**, it stays technical because agents read it:
code, commits, notes, reports, briefs, sub-issue titles, an issue's detail and plan, and what an agent returns to
the orchestrator.

## Shape of every message

1. **What it is about**, in one sentence, naming the thing rather than its id.
2. **What happened or what changes for the owner**, with figures that carry their unit and a comparison («from 3 min
   to 1.5»).
3. **What is asked**: the question, the options with what each one changes, and your recommendation with its reason.
   When nothing is asked, say so («nothing to do on your side»).

## Rules

- **No bare ids.** An issue, sub-issue, decision, question or step always comes with what it is beside it; the id
  goes in brackets, as a handle to answer with: «the dead-orchestrator warning (AB-54)».
- **No internal names** (agent, session, branch, config key, notice kind, path, function, hash) unless the owner
  needs it to act, and then with what it is.
- **Commands the owner types** go in backticks and say what they do: «type `/reload-plugins` in that session to load
  the new version».
- **No pointing back** («as before», «J1 and J2», «step 10») without saying again what it is.
- **Short:** one idea per sentence, everyday words, the detail left in the report or the plan (give its path only
  if the owner may want to open it).
- **A question stands alone:** whoever opens the decision without the chat understands it and can answer.

## Explaining a decision

The owner decides from the panel, often on a phone, with only what you wrote. Write it from their side, not yours.

- **Ask only what is theirs.** A small change that can be undone is made and told (a line in the catch-up or the
  inbox), not asked. An idea you recommend dropping and nothing waits for: a line in the notes with its reason. A
  question is only what is theirs: cost, risk, the product's taste, their credentials, or what cannot be undone or
  is hard to undo. Agents never ask: they propose the question in their report and the orchestrator weighs it.
- **Start from what they will see.** The list shows only the title and the first sentence of the text: put there
  what is at stake for them, not what you would change.
- **One concrete case,** a real one from their week, with the figure in their units («a 60 s answer would take
  59 s», «2 holidays a year»); a percentage or a size alone does not say whether it matters.
- **Say how you know:** seen (how often), measured, or estimated from the code.
- **Options are outcomes:** what they will see or do with each, not a state of the backlog («ready to do», «an issue
  with a measurement»). Nothing waits for the answer: leave `--if-no-answer` out.
- **Their words, not the source's:** no labels, terms or numbering of the study, plan or review it came from
  («idea A2», «finding F3»), no «details in…»; a term explained in another question is explained again.

| before | after |
|---|---|
| «Store the state of the GDN layers in bf16, as engine X does? It would be ~1.6 % faster and 31 MB less per snapshot, but it changes the answers.» Options: «No» / «Yes» (an issue with a measurement). Page: a 263-line review of other engines. | «Make answers about 2 % faster in exchange for them no longer matching the reference exactly? A 60 s answer would take about 59 s; the exact answers are what catches engine bugs after every change. Estimated from the code, not measured.» Options: «No, keep them exact» (nothing changes) / «Yes, as a setting you switch on» (answers change slightly with it on). Recommended: no, 1 s in 60 does not pay for losing that check. No page: the text is enough. |

## Templates

- **Decision:** title = the question in one plain sentence, **one question per decision** (two questions in the
  title or the text get a warning; a question quoted «…» does not count); text = what it is about and why it comes
  now; options = short answers, they are the buttons; after each `--option`, its `--effect`, required: a question with an option without it is refused (what the owner will see
  or do differently), its `--cost` when it has one (cost or risk) and its `--undo` (`yes`, `hard`: with work, `no`);
  recommendation = the option and why, and which option it is (`--recommend-option` with the exact text of that
  option: the panel marks its button «(recommended)»). When work waits for the answer, `--if-no-answer` says what
  stays stopped meanwhile and `--answer-by` the day to remind the owner: foreman never answers for them, so a
  question never says «if you do not answer, I go with A». When a screenshot or a table explains it better than a
  sentence, `--page` names a page (.md) written for this question alone, as «Question page» below says (where it
  goes: docs/orchestrator.md).
- **Progress line:** what was reached or what broke, with its figure: «Search answers in 1.2 s instead of 3; the
  last part, the cache, is next».
- **Result line:** what the product or the owner can do now that they could not before, with its figure.
- **PR body:** *What changes / Why / How it was checked / What to look at* (English and without internal ids when the
  repo is public).
- **«How are we doing?»:** since when, then four parts in this order, each even when empty: what finished and what
  it changes, what is being built and what for, what waits for the owner, what went wrong and what is done about it.
  About 15 lines; detail only when asked.
- **Orchestrator session changed:** the old one closed or stopped responding and a new one took over; whether
  anything is asked.

## Question page

The page of one question (`decision add --page`), written for it alone: never a report, a plan, a study or the
page of another question, even when they hold the answer (copy the part that matters). No page when the text is
enough. About 30 lines; `decision add` warns past 60, of a page another question already has, of a report or plan
given as the page, and of what the page names without explaining (`foreman plain`, as the question). Fill this in:

```markdown
# <the question, in the owner's words>

**What happens today.** Two or three plain sentences from the owner's side, and why it comes up now.

**An example.** One concrete case: a screenshot, the real text or output, or a situation of their week.

![what they see today](before-390.png)

**How we know.** Seen (how often) / measured (where, one line) / estimated (from what).

| | <option A> | <option B> |
|---|---|---|
| what you will see | … | … |
| figure, today → after | 60 s → 59 s | 60 s |
| cost or risk | time, money, work, what could go wrong | … |
| can it be undone | yes / with work / no | … |

**Recommendation:** <option>, because <one reason in the owner's terms>.

More, for whoever wants it: <the report, plan or review, only here>
```

- One question, one topic: nothing about other work, other questions or how it was found.
- Words the owner uses, no ids, paths, line numbers or labels of the source; each figure with today's value beside it.
- Screenshots are welcome: beside the page, linked relative; a before and after when an option changes what they see.
- The same options as the question's buttons, in the same order and words.

## Before and after

| before | after |
|---|---|
| «I need your answers to J1 and J2» | «Two questions wait for you in the panel: who writes the machine's GPU table, and whether the config folder goes into a local git» |
| «AB-12 T3 merged, zed7 delivered, main_ci green» | «The export to spreadsheet is in (AB-12): a 10 000-row report now takes 4 s instead of 40. Nothing to do on your side» |
| «Yes, as optional T4?» | «Should the warning also go to the other products' orchestrators? It would be an optional last part of the work (AB-54). I recommend yes: it costs one more step and they miss it today» |
| «busy script exit 1 in step 4, fixed in lock.py:212» | «The check that tells whether the GPU is in use was wrong and let two jobs in at once; it is fixed and tested. Nothing to do on your side» |
| «foreman_behind: 0.43.1 in this session, 0.44.0 installed; reload» | «A new version of foreman is installed; type `/reload-plugins` in this chat to load it. Nothing else is asked» |

## How to check a text

`foreman plain "text"` (or `foreman plain -` with the text on stdin; `--json` for a list) flags, by rules and
without a model, what the owner would have to ask about:

- **codes:** an issue, sub-issue, decision or question id with nothing beside it («T3», «J1», «AB-12 T3»), and
  «step N». An id in brackets, or followed by «:», «,», «(» or a dash and words, counts as explained;
- **internal names:** snake_case and UPPER_SNAKE names, `[section] key` and config keys in backticks, paths and
  `file:line`, file names, `function()`, commit hashes and session ids;
- **this project's names:** its agents, orchestrator sessions and branches, from its state and its backlog.

Each finding comes with where it is and why. Commands the owner types (in backticks, starting with `/` or
`foreman `), code blocks, URLs and the terms in `[project] owner_knows` (docs/config.md) are left alone. Exit 0:
nothing to explain; 1: something to explain; 2: no text. A finding is a prompt to rewrite, not a verdict: the rules
cannot tell a clear sentence from a muddled one, so the shape and rules above still decide.

foreman runs the same check where it writes for the owner:

- **`foreman backlog decision add` refuses a question** whose title, text, options or recommendation fail it: exit 2,
  the findings by field and how to fix them. Rewrite it with the decision template above. `--as-is` records it
  anyway, marked (`"as_is": true`, a warning in `foreman check`); a term the owner does know belongs in
  `[project] owner_knows` instead. The recommendation is shown with its label («Recommendation: …») on the decisions
  page, in `foreman backlog show` and in the panel.
- **Everything else warns** on stderr and never blocks: an issue's title and short name (`backlog add`, `backlog edit
  --title`, `--short`), a result line (`--result` of `backlog status`, `backlog edit`, `backlog integrated` and
  `foreman integrate`), a `foreman progress` line, a `foreman inbox reply`, and the PR title and body of `foreman integrate` (a public repo
  also blocks any id there, bracketed or not). Sub-issue titles stay technical and are not checked.
- **The chat reviewer** checks the orchestrator's chat: at the end of each turn of the marked session (never a
  subagent's), foreman's hooks module checks the answer and, if something was flagged, adds one line for the model
  to the owner's next prompt (one the owner typed: not the loop pass, a task notification or another session's
  message) naming what it left unexplained («Your last message to the owner named without explaining: J1, J2…»),
  once per answer; a newer answer replaces it. It never blocks: the message was already read, so the point is the
  next one. Each check leaves a line of counts, never the text, in `<state>/plain.jsonl`; `foreman plain --stats
  [--since 7d] [--session ID] [--json]` gives the share of answers with findings, per day.
