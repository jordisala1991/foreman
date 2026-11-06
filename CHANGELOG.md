# Changelog

All notable changes to foreman are recorded here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)
and the version follows [Semantic Versioning](https://semver.org/). The version lives in `.claude-plugin/plugin.json`;
each release is tagged `foreman--vX.Y.Z` and the `release` branch points at the latest one (CONTRIBUTING.md).

Besides the usual sections, a release may have **Config** (new `foreman.toml` keys and the `config_schema` they need)
and **Upgrade notes** (anything a user has to do: state, backlog, service or registry changes).

## [Unreleased]

### Changed

- «A harness review» (agents/reviewer.md) has a fifth outcome for a rule: replace it with an automatic check (a
  test, a guard row, a `foreman check` item), which ends as a code sub-issue.
- The guard's refusal of an agent's git that rewrites a main checkout (`git:main-checkout`: a merge, a checkout or
  switch of a branch, `checkout -b`, `switch -c`, a hard reset, a stash…) now covers the main checkout of every
  `[repos.*]` path of every registered product, not only the one that holds a live home: a product whose home lives
  outside its code repo had that repo unprotected. Its worktrees and the orchestrator pass as before; the refusal
  says «a main checkout of PRODUCT».
- `foreman agents --misled` reads a `Misled:` line (and any other fixed line of a report) whole when the report wraps
  it over several lines, with its cost.
- The agent instructions: the rules file is found in `project.rules` (`foreman config`), not in the brief; a plan's
  claim of scope cites its search or says it was not checked; the final-message length (now 2,000 characters) is
  stated only in docs/agent.md; the schema test says the whole rule for `CONFIG_SCHEMA` (join the unreleased list,
  else bump).

## [0.60.0] - 2026-11-06

### Removed

- foreman no longer writes text copies of the backlog: the backlog page, the archive page and the pending-decisions
  page (or the decisions section of a hand-written page) are gone. The backlog is seen in the panel or with `foreman
  backlog list|show`, the questions with `foreman backlog decision list`. Every write is the backlog file alone, and
  `foreman check` and the loop pass no longer build the pages in memory to warn they are stale. A question's own page
  (`decision add --page`) stays.
- `foreman backlog render [--check]` and `foreman backlog page`: an old call exits 2 with argparse's error.

### Changed

- The texts of hidden acceptance checks: the planner writes the check when its brief asks (part of the criterion's
  rows in the plan, the rest in `<holdout>/ID/`, the plan saying only the contract and the count); the orchestrator
  reads every command before `foreman holdout seal` when it approves the plan, since `integrate` runs them with its
  power outside the guard, and sends a failing branch's agent only the symptom block; a production agent is told
  such cases exist and not to look for them (agents/planner.md, skills/review-plan, docs/orchestrator.md,
  docs/agent.md). When the behaviour has cases, a plan's acceptance criterion is a table, input → expected result.
- The `review_needed` notice, for an issue with a hidden check, says to run it first (`foreman holdout run`): a
  branch that fails it needs another review anyway.
- `foreman backlog show`, `backlog get ID subs`, `foreman start` and `foreman integrate` print a report or a
  question's page as its path in the home (`report reviews/…md`, `Page: reviews/…md`) instead of a Markdown link
  relative to the old pages folder (`../reviews/…`). The module `backlog_pages` is now `backlog_text`.

### Config

- Schema 22: `[backlog] page`, `archive_page`, `decisions_page` and `decisions_section` are no longer read. One left
  in a foreman.toml is a warning that says to delete it (the product loads; `foreman check` exits 1 until it is gone).

### Upgrade notes

- Delete `page`, `archive_page`, `decisions_page` and `decisions_section` from `[backlog]` in foreman.toml, then the
  files they named (by default `pages/backlog.md`, `pages/backlog-archive.md`, `pages/decisions.md`). Do it once no
  session of the product still runs foreman 0.59 or older (`foreman session --all --behind`): an older foreman writes
  them again on its next backlog write, and without the keys it writes them at the default paths.
- A product whose decisions were a section of a page it writes by hand (`decisions_section`): remove that section's
  generated body (say there that the questions are in the panel and `foreman backlog decision list`); the rest of the
  page is untouched.
- Do not delete the `pages` block of backlog.json: foreman 0.59 and older fail to write the backlog without it. foreman
  keeps it as it is (and no longer creates it in a new backlog); its texts, the old pages' headings and intros, are
  shown nowhere now: move any hand-written text you still want into a page of your own.

## [0.59.0] - 2026-11-06

### Fixed

- The new-version notice of the orchestrator loop (and `docs/orchestrator.md`) no longer says `/reload-plugins` is refused
  over Remote Control from the phone: it is not.

### Added

- A place for hidden acceptance checks, out of the production agents' reach: each product's `[paths] holdout`
  (default `~/.local/share/foreman/<name>/holdout/`). A built-in guard rule refuses every agent but a planner (a
  production agent, a reviewer, an agent of unknown type) any call that reaches such a folder of any registered
  product: a path in or under it in a command or a tool's place (links followed, `~`/`$HOME` expanded, wildcards that
  may match it), the folder named as text (inline code, heredocs, unreadable lines), a search that walks a folder
  above it (`grep -r`, `rg`, `find`, `ls -R`, `du`, `tar`, `rsync`, `cp -R`…; the Grep tool's path, a Glob pattern),
  any command run inside it, and `foreman holdout` but `--help`. The orchestrator, the owner's other sessions and
  planners pass; the refusal reaches the orchestrator and `guard.jsonl`. Nothing changes while no such folder
  exists. The guard hook passes the event's `agent_type` (else `<state>/agents.json` says it); `foreman guard --as
  planner` checks as a planner; `foreman permissions` lists the rule as foreman's own.
- `foreman integrate` runs an issue's hidden acceptance check: a `holdout.toml` in `<holdout>/ID/` (one shell command
  per case, with what it does on main and the symptom a user would notice, plus files copied over the branch;
  docs/config.md). `foreman holdout seal ID` hashes the folder into `<state>/holdout.json` and runs the cases on main
  (each must fail or pass there as its `on_main` says); `integrate` stops (exit 2) on a check never sealed or changed
  since, and runs it after the local CI, before anything is pushed or merged, in a detached copy of the tip that is
  always removed. Too few cases pass: exit 2, and the only text for the agent is the failing cases' symptoms, never
  a case's id, command, data, file or place; after two failed rounds, only the owner's yes (`--approved`). A pass on
  the same tip (or the same own diff) is reused; the note says k/N and the round. `integrate --check`, the CI log,
  the notes, the progress and the state never carry the cases. `foreman holdout run REPO BRANCH ID [--full]` runs it
  at once; `foreman holdout status [ID]` shows each check, its runs and logs. Two new points, `holdout.setup` (600 s)
  and `holdout.scenario` (300 s, or the spec's `timeout`).
- A recurring task that falls due when agents start running on a new model: a `[[recurring]]` source `models =
  "reviews/*-review-harness*.md"` (a glob of reviews) is late with no review, or when an agent ran after the newest
  one on a model its `Models:` line does not name (agents.json at the agent's stop, or the model a delivery line of
  `progress.jsonl` records); `foreman recurring` names the new models. With `max_h` it is also late that many hours
  after the review (`max_h = 720`: «with each new model and monthly» in one task). docs/config.md.
- The reviewer's «A harness review» (agents/reviewer.md), the task that `models` source makes due: from `foreman
  agents --misled` and the guard's refusals since the previous one, each piece of the agents' texts is kept,
  rewritten, removed or measured; its header `Models:`/`Since:`/`Lines:` is what the source reads. Changes to the
  project's rules go to the owner as one decision; those to foreman's texts, the orchestrator approves as any plan.
  Planners, reviewers, dev and mechanic agents are told to write their `Misled:` lines; the batch close starts from
  `foreman agents --misled` and a plan's review from its `Misled:` lines.

### Changed

- The panel's list of questions that say what each option changes is calmer: the title, one situation line, each option
  one full-width button with its letter and name (★ the recommended one, ⊘ before the one that cannot be undone, which
  asks to confirm) and a grey line with what it holds and the reminder's day; what each option changes, its cost and
  whether it can be undone stay in the detail. Once answered, until the orchestrator reads it, the question stays in
  its place folded to «✓ You chose A: …» with «Change»; its detail says «Answered», highlights the chosen option and
  greys the others, and «Change answer» brings the options back. A change goes through `/api/decide?…&change=1`: the
  backlog's answer changes and the orchestrator gets a new inbox message with `changed_from`. Questions without
  per-option details look as before.

### Config

- Schema 21: `[paths] holdout` (empty by default: `~/.local/share/foreman/<name>/holdout`), and the `models` source of
  `[[recurring]]`.

### Upgrade notes

- Nothing to do: config schema 21 adds only optional keys, and the backlog schema stays 5. An answer can be changed
  from the panel only while the orchestrator has not read it.

## [0.58.0] - 2026-11-05

### Added

- What misled each agent: a production report carries a line `Misled: <where>: <what> (<cost>)` per thing in its
  brief, plan, rules, docs or a foreman message that was wrong, stale or unclear, or `Misled: none`
  (docs/agent.md). The delivery check refuses a stop (not a `failed` one) whose report lacks it, saying how to write
  it; it shares the two refusals with the other items. Every product, no switch.
- `foreman agents --misled [--since YYYY-MM-DD] [--type T] [--model M] [--json]` gathers those lines from the
  reports (by default since the last `*-review-harness*.md`, else 7 days), with date, agent, type and model, and a
  footer: how many reports have something, `none` or no line at all.
- `foreman progress --done` records the agent's `type` and `model` in its progress line, so each report keeps who
  wrote it after agents.json is pruned.

- Richer questions for the owner: `foreman backlog decision add` takes, after each `--option`, its `--effect` (what
  the owner will see or do differently), `--cost` and `--undo yes|hard|no` (whether it can be undone); and
  `--if-no-answer` (what stays stopped until the owner answers), `--answer-by` (the day to remind them; foreman never
  answers for the owner) and `--page` (a .md under the reviews or notes folders, with screenshots or tables, that
  explains the question). They are stored as the optional `details`, `unanswered` and `page` of the decision, only
  when given, and pass `foreman plain` like the title. Warnings on stderr, never blocking: an option without
  `--effect`, two questions in one (a quoted «…» question does not count), a reminder date in the past, a page that
  is missing or larger than 400 KB. `backlog show`, `decision list` (with `--json`: `details`, `unanswered`, `page`
  and `page_path`) and the decisions page show them; a decision without them reads and renders as before.
- `foreman check` validates those fields (undo values, options they name, dates, the page's folder) and warns of
  unknown keys in a decision, which the next write drops.
- The panel shows those fields: the question's card lists under its buttons what each option changes and whether it
  can be undone, what waits until the answer (with the reminder date) and a link to its page; the detail shows each
  option whole (with its cost) and the page inside, with its images. A question without them looks as before
  (checked pixel by pixel at 390 and 1440 px). The detail now shows the question's whole text (it was cut at 600
  characters).
- A question that says what each option changes is told in order in the panel: its situation, what happens
  without an answer (with the reminder date and the tasks it holds, by name), then each option as a card with whether
  it can be undone, its cost, its effect and its own «Choose» button (the recommended one marked, with its reason),
  «Other answer», and its page folded under «See more detail». Only an option that cannot be undone asks for a
  confirmation, with what it changes; the others answer on one tap. In the list, its card shows each option as a row
  with its name, whether it can be undone, its cost and its own «Choose» (the recommended one marked). Questions without
  those details look as before.
- Images in the panel's documents: a report or a question's page shows its `![alt](picture.png)` from its own
  folder, through the new `/api/file` (PNG, JPEG, GIF or WebP checked by their first bytes, up to 5 MB, only from the
  reviews, notes and report folders, with the panel's key; never SVG nor an outside address).
- The `owner-decisions` skill presents a question's `details`, `unanswered` and `page` when it has them: per option
  its effect, cost and whether it can be undone, what waits for the answer and the reminder day, and a two-line
  summary of the page with where to see it. Older questions are presented as before.
- `decision_due` notice: a pending question whose `--answer-by` day has come gets one actionable notice asking the
  orchestrator to remind the owner in the chat, with what waits. It only reminds: the question stays pending.
- Review gate in `foreman integrate`, for a repo with `review = "auto"`: a branch that touches a `risky_paths` or
  `review_paths` entry, changes more than `review_lines` lines of code (tests, docs, JSON and lock files apart) or
  was delivered `partial` goes in only once a review by another agent covers its tip and leaves no high or medium
  finding open. foreman reads the review's header (`Branch:`, `Tip:`, `Round:`, `Open: H n, M n`) under the reviews
  folder; a tip with the same own patch as the reviewed one (main merged in, a clean rebase) stays covered.
  `integrate` and `--check` print `== review: STATE` and stop with exit 2, nothing done, with the next step. At most
  two rounds: after round 2 with findings open the owner decides, and `--approved` lets it in as it is.
  `--no-review "why"` lets in, noted, a branch whose only reason is its size (never delicate paths nor a partial
  delivery).
- The review loop: the loop pass no longer offers a branch whose review does not let it in under
  `ready_to_integrate`; two new notices say what to do instead. `review_needed` gives the command to launch a
  reviewer (or, for round 2, to resume the one of round 1); `review_findings` sends the high and medium findings back
  to the agent that made the branch (or a new agent on its worktree), and after round 2 with findings open asks for a
  question to the owner with the review as its page, waiting while it is pending and saying what to do with the
  answer. A branch whose `partial` delivery became `ok` keeps its rounds while its review leaves findings open.
- `foreman launch NAME --review REPO:BRANCH`: a reviewer of a branch, with no worktree and no backlog change
  (`--type reviewer` unless given; the launch line ends in «review»). While it lives the branch is held: not offered
  to integrate, and `integrate` says `in review`. Its own delivery never counts as the branch's (outcome, report,
  kind).
- `foreman launch --repo REPO --branch BRANCH` reuses the worktree foreman made for BRANCH when no live agent has it
  («reusing its worktree at …»), so a branch can go back to work with a new agent; `foreman worktree` still refuses a
  branch checked out elsewhere.
- The `reviewer` agent has a branch review mode («A branch review»): it reads the criterion, the plan and the diff
  before the agent's report, backs every line of the criterion with evidence, gives each finding its severity,
  failure scenario and the test that would prove it, dismisses a finding only by citing the line that refutes it,
  keeps style and new scope out of H and M, and in round 2 checks only the earlier findings and what the fixes
  changed; its review starts with the header foreman reads. The `dev` agent and docs/agent.md say what to do when
  resumed with a review (fix or refute each H and M with proof, a «Review, round N» section in the report), and the
  `orchestrator-pass` skill and docs/orchestrator.md how the orchestrator runs the rounds (who to launch or resume,
  SendMessage, plan B, the owner's question after round 2, the size waiver).
### Config

- Schema 20: `[repos.*] review` (`"off"` by default, or `"auto"`), `review_paths` (a list, as `risky_paths`) and
  `review_lines` (400; 0 turns the size off). The three are brakes for `foreman permissions`: `review` back to `off`,
  a `review_paths` entry removed, or `review_lines` raised or set to 0 opens.

### Upgrade notes

- Agent reports need a `Misled:` line from this version on (`Misled: none` when nothing misled them): an agent already running learns it from the first refused stop.
- Reviews before integrating stay off until a repo sets `review = "auto"` (config schema 20).
- Backlog: no schema change (still 5). An older foreman that writes the backlog drops `details`, `unanswered` and `page` of a
  decision: record such questions once every session of the product runs this version.

## [0.57.0] - 2026-11-03

### Added

- Machine settings: a `[machine]` section in the registry (`~/.config/foreman/projects.toml`), shaped like a
  foreman.toml (`[machine.project]`, `[machine.agents]`, `[machine.loop]`, `[machine.deny]`,
  `[machine.deny.grants]`, `[machine.strings]`), that every registered product inherits when it loads: what holds
  for every product (the owner, the language, the commands nobody runs without the owner) is written once. A
  product's scalar wins, lists add up, tables merge key by key. A bad value there is left out alone, with a warning
  in every product. `foreman check` says how many settings and deny rules a product inherits;
  `foreman config --json` gives `registry` (or `registry+foreman.toml` for a list both add to) as their source.
- `foreman permissions` says where each rule comes from: it marks the rules the registry's `[machine.deny]` gives
  («from the machine», «machine and product»), counts them, and lists the inherited rules the product removes with
  `[deny] drop`. `--json` adds `from` to each rule (`foreman`, `machine`, `product` or `both`) and `inherited`
  (`{rules, dropped}`).
- `foreman permissions --machine-candidate FILE`: the effect of a changed registry `[machine]` on every registered
  product, read only: the inherited rules before and after, the settings that change, each product's candidate test
  (rules, table rows, failing rows, the lifeline, what opens), the warnings it would gain, and the rows that would
  fall in two or more `[deny.grants]` groups other than before (each needs its own yes, while the row's verdict stays
  the same). Exit 1 when a product loses a row or a lifeline command, gets a warning or does not load.

### Changed

- The guard sees every tool, not only Bash and the write tools: its hook runs before any tool call (no matcher in
  `hooks/classic.json`). With one rule and no list of tools, a path or URL a call names (Read's `file_path`, Grep's
  `path`, WebFetch's `url`, a plugin's `targetPath`…) gets the verdict `cat PATH` would get in Bash, and a command
  it carries (Monitor's `command`) the verdict of that command; free text (what a Write writes, a prompt, a message)
  is never compared. Writing a secret with Write or Edit is refused as reading it. Guard tables accept
  `tool:NAME JSON` rows; `guard.jsonl` and `approved.jsonl` record the tool and the place or command that matched,
  never the rest of the input. Without a Python foreman can use, every tool call is refused while a registry exists
  (before, only Bash and the write tools were). Cost: one more hook process per call of the other tools, about
  40 ms each.
- The orchestrator is asked for the owner's yes in the chat for any tool, not only Bash: a Read, WebFetch, Monitor
  or any other call of its main loop that names a place or a command of a `[deny.grants]` group gets the same
  question a Bash command of the group gets (before, it was refused with no question). The question names the tool
  and what matched; the answer's record in `approved.jsonl` carries `tool`. In a project with groups, every call of
  another tool than Bash in a session's main loop costs one more `foreman hook mod grant` process, as a Bash call
  already did.

### Config

- Schema 19: `[deny] drop`, a list of rules inherited from the registry's `[machine.deny]` that this product removes
  (a pattern as written there, or `"group:NAME"` for a whole group). `sources` of `foreman config --json` may say
  `registry+foreman.toml` (additive: `api` stays 1).

### Upgrade notes

- The guard now runs before every tool call, so a `[deny]` pattern or grants group that names a domain, a path or
  a script name also refuses reading that file or fetching that page with any tool, as `cat` of it in Bash already
  did. Run `foreman check` and look at your rules that name documentation domains or scripts you read but do not
  run; narrow them to the command that runs them.
- Nothing to do: without a `[machine]` in the registry every product loads as before (`foreman check` adds «inherited
  from the machine (registry [machine]): 0 settings, 0 deny rules», `foreman permissions` «Rules from the
  machine (the registry's [machine.deny]): 0»). An older foreman ignores `[machine]` with
  «unknown section [machine]»: before moving a rule from the products' foreman.toml to `[machine]`, make sure every
  session runs a foreman that knows it (`foreman session --all --behind`); until then keep it in both places.

## [0.56.0] - 2026-11-03

### Added

- First public release of foreman.

### Changed

- In a repo with `public = true`, `foreman integrate` also scans the branch's commit messages (`main..BRANCH`) with
  the checks the PR title and body pass (no internal IDs, then `privacy_scan`), before the local CI and before
  anything is pushed: a public PR shows them forever, even when the merge squashes them. A hit stops with «nothing
  pushed» and names the commit and the rule, never the matched text.
- The test suite covers a whole loop pass (the hook's tick and `loop.jsonl`) when all the ready work waits for a
  pending decision: the WIP notice is information only, says which decision each issue waits for, and the pass is
  skipped; one ready issue that can be launched next to them sends the pass to the model. No behaviour changes: a
  ready issue whose remaining sub-issues are measurements (`kind = "measure"`) still counts as launchable, since an
  agent can run them.

## [0.55.0]

The version the public history starts from; the changes before it were not published.
