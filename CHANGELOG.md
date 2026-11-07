# Changelog

All notable changes to foreman are recorded here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)
and the version follows [Semantic Versioning](https://semver.org/). The version lives in `.claude-plugin/plugin.json`;
each release is tagged `foreman--vX.Y.Z` and the `release` branch points at the latest one (CONTRIBUTING.md).

Besides the usual sections, a release may have **Config** (new `foreman.toml` keys and the `config_schema` they need)
and **Upgrade notes** (anything a user has to do: state, backlog, service or registry changes).

## [Unreleased]

## [0.66.0] - 2026-11-07

### Added

- The panel shows a «See conversation» button for each session agent (in its row and its detail): it turns Remote Control on for that session only when clicked (`claude attach` on a pseudo-terminal, `/remote-control`, then Enter on the dialog it leaves open; the session is not stopped, so no turn is cut and no copy is made) and opens the claude.ai/code link of its conversation in the Claude app. It sends the owner's phone a push on that click, never at launch. A failure says why and the button can be pressed again (`POST /api/agent/watch`, same key and project rules as the other writes).
- A new web panel is being built at `/next/` of the same server and key, for the products that set `[panel] next = true` (default `false`; the panel at `/` is unchanged). This first part has its shell (a sidebar on a wide screen, five tabs at the bottom on a phone, the product picker, light and dark) and «How it goes» with the cost per day and model of the last 7 or 30 days (`/api/metrics`), at list price and counting only the product's own issues. It loads nothing from outside the machine. It is written in React and TypeScript (`panel/next-src/`) and built with Vite into `panel/next/`, which ships in the plugin: installing foreman still needs only Python, Claude Code and a browser. Its shell follows the approved design: 44 px targets, the product picker as a pill with the product's status («Running», «Paused», «Not answering», «Resting»), the sidebar's own tone with its «MORE» group, a translucent bottom bar, the theme following the system.
- `/api/metrics` of the new panel now gives every series of its charts for the last 7 or 30 days, empty days as 0: cost per day and model (list-price dollars, a run's cost on the day it ended), the cheap-model share of tokens, the most expensive tasks and agent types, changes integrated and measurements, how agents' deliveries ended, reviews that asked for changes and their rounds, the guard's refusals by who and the rules refusing most, the figures of today with their comparison, the first day of data of each series, and the account's quota now. A failure inside it answers 500, no longer 400.

- The new panel's «Agents» screen (`/next/#/agents`): a card for each agent running (what it works on, its state and how long it has been at it, its last words, red when it may be stuck, «See conversation» for a session agent), the agents that finished today with their result, time, model and list-price cost, and, in the product picker, «All» to see the agents of every product with the new panel, each with its product's tag. It asks `/api/agents?p=NAME` (`*` for all) again every 10 s. A finished agent counts only when its issue is one of the product's, as the charts do.

### Config

- `[panel] next` (default `false`), the product's own; `config_schema` 24.

### Fixed

- `foreman agents --stop` on a session agent gives `claude stop` the short session id (the 8 characters `claude agents --json` shows as `id`); with the whole id Claude Code answered «No job matching…» and the session kept running.
- `foreman launch NAME --review REPO:BRANCH --session` starts the reviewer's session in the reviewed branch's worktree (the repo's main checkout when it has none) instead of the product's home, which Claude Code does not trust («Workspace not trusted»).
- `foreman integrate` (also `--measure`) stops the session of the branch's session agent once it is integrated, as `foreman agents --stop` does (short id); a failure to stop it is a warning and the integration still closes.

## [0.65.0] - 2026-11-07

### Added

- `foreman launch NAME … --session [--brief-file F]` starts the agent as a Claude Code session of its own (`claude --bg`, in its worktree, with the guard deciding its permissions) instead of through the orchestrator's Agent tool, so it keeps running when the orchestrator's session ends. It is off until the product sets `[agents] sessions = true`; with the key false, `--session` is refused and names it. The session carries its launch token in `FOREMAN_LAUNCH`, which is how the guard knows it is an agent. Remote Control is not turned on.
- A session agent is followed like a subagent: its tool calls are its activity, its `foreman progress --done` its delivery (so `foreman agents`, `--cost` and the branches ready to integrate see it), and its `Stop` goes through the delivery check (the new `Stop` hook; a session that is not a registered agent is untouched). `foreman agents` and the loop pass cross it with `claude agents --json` (a failed or stopped session shows as stopped), `foreman agents --stop` also runs `claude stop`, the orchestrator's renewal no longer waits for it, and its delivery brings the next pass within a minute.

### Config

- `[agents] sessions` (default `false`), the product's own; `config_schema` 23.

## [0.64.0] - 2026-11-07

### Changed

- The panel shows when each pending permission request was asked and when it expires, with its day; a request of an orchestrator that is no longer running is shown as such with only a Discard button (discarding it no longer returns an error); a request whose notice did not arrive says so.
- A permission request the owner did not answer in time now waits up to 24 hours (it expired after `grant_minutes`), and a late yes or no on the question still drawn in the chat applies to that very request: a yes opens the group for `grant_minutes` from the yes, only for the marked session in the same Claude Code process that asked (a /clear in that process carries the request along); a no drops it and starts the cooldown. Both leave the orchestrator an inbox message that brings its next pass within a minute. A request of an orchestrator process that has since restarted is never granted, only dropped. `foreman permissions grants off [GROUP] --pending` withdraws only the requests.
- A group's permission question now also raises a short notice through Claude Code's notification channel (a terminal that notifies, or the owner's own `Notification` hooks), without the command; whether it was sent, and why not, is recorded with the answer in `approved.jsonl` and with the pending request (`notified`).
- A group's permission question now says in plain words what the command will do («The orchestrator wants to push the branch fix/login to GitHub. Allow this, and the rest of the same permission, for 30 min without asking again?»), names the orchestrator once and never the group, and shows as a detail only the part of the command that needs the yes, not its start (a push's folder and branch are read where the push runs, after a `cd` too). Its notice says the same without the command's own words, and a request left for the panel carries that part (`part`, shown under «see the command») and those words (`summary`), unless the part itself was cut.

## [0.63.0] - 2026-11-07

### Added

- `foreman agents --cost`: what each agent run cost at API list prices (tokens by model, minutes, outcome), recorded from the transcript at `progress --done` or when the pass sees the agent stopped, kept in `costs.jsonl` beside the backlog; by issue, sub-issue, type, model, week or day, with the share of tokens on Sonnet or Haiku, as text or JSON; `--backfill DAY` records older runs. The panel's issue page shows the issue's cost.

### Changed

- `foreman backlog decision add` refuses (exit 2, naming the option) a question with an `--option` that has no `--effect`; `--as-is` still records it (and `foreman check` still warns).
- The guard reads each command once per check: its texts, with and without the pushes resolved, serve every deny
  list and group; one walk of the lines it runs serves the reserved-file, main-checkout, integrate and hidden-check
  rules; the registry is read once per call (also across a tool call's file and places), and a folder's repo in two
  git calls instead of three, shared with the main-checkout rule. A `git push` is checked in about 50 ms instead of
  55, compound commands and writes about 2 ms faster. Three verdicts change, in corners: a project with no `[deny]`
  patterns and no groups no longer runs git for a push nor refuses it when git cannot be read (there is nothing to
  deny); a git alias starting with `!` whose command the guard cannot read is judged in the folder it runs in, so an
  agent's such alias that merges in a main checkout is refused (it passed before) and one called there from a
  worktree's line is judged there; a check whose own folder is gone no longer refuses the whole command, each rule
  compares its text only.

### Fixed

- A message from the owner, including an approval given from the panel, brings the orchestrator's next loop pass at the next tick (within a minute) instead of at the next hourly pass.
- The guard sees three more ways interpreter code writes a reserved file, and stops refusing two ways it only reads.
  A call without parentheses whose target is a variable (`File.write p, "x"` in Ruby, `open my $f, ">", $p` and
  `unlink $p` in Perl) is read like one with them. When code writes a target it cannot name, a name it assigns a
  path built of literals stands for that path: a piece joined onto it (`base / "foreman.toml"`,
  `os.path.join(base, "foreman.toml")`) or written inside an f-string (`f"{base}/foreman.toml"`) makes the whole
  path, whatever comes between. A string's `.replace(a, b)` with a variable or a regex (`s.replace(old, "b")`,
  `.replace(/\t/g, " ")`) is no longer taken for a write in code that never names `os`, `importlib` or `__import__`,
  so reading a reserved file with it passes; `os.replace` (also as `import os as o`, or imported by a name the code
  builds) and a Path's `replace` still write. Perl's `open` with
  `"<"` reads. A line the guard cannot read that writes to `~/…` now reads `~` as the home, as it already read
  `$HOME`: such a write to a reserved file is refused. The three rules that find a path written as text (reserved
  files, main checkouts, hidden checks) now read the text one way.
- Panel: on a phone the product picker shows each product's name (what its orchestrator does goes under it); «Back», a tap outside and Escape close the confirmation of an option that cannot be undone; a panel reopened on the Wiki tab lists the pages instead of «wiki_failed».

## [0.62.0] - 2026-11-06

### Fixed

- The guard no longer lets six git forms past it. A git that rewrites a main checkout after `cd MISSING;`, or in a
  folder the line names with a variable (`git -C "$X" merge main`, `cd "$X" && git merge main`: an empty `$X` is the
  current folder) is refused from the main checkout; from a worktree it still passes. A `git push` is resolved
  with the config from before a change that may not have run (`false && git remote remove origin; git push`,
  `git config push.default nothing` behind `false &&`, a `-C` that does not exist) as well as with the config after
  it. A `!` alias under `--git-dir` or `GIT_DIR` is read as a push from any repo, and a global alias is read
  outside a repo too. The folder rule (`cd`, `-C`) and the alias expander are now one each, shared by the push and
  main-checkout rules.
- A shell function that wraps git with `"$@"` and is called only with literal subcommands
  (`G() { git --git-dir D "$@"; }; G grep x`) is read as those subcommands, not as a push. Its body is resolved with
  the branch and git config the line leaves before each call, and what the body changes counts after it.
- The guard no longer lets five program forms past it. A Python heredoc that reaches builtins through a module's
  attribute (`re.enum.bltns.eval`) or a full-width `＿`, that writes a file it cannot name (`Path("difflib")
  .with_suffix(".py")`, `Path("hs.x"[::-1])`) and then imports or runs it, or that names a path with a space in a
  literal (`"/opt/My App/.env"`) is compared as written. Code an interpreter runs on the line (`python3 -c`,
  `node -e`, a heredoc to `python3` or `perl`) that writes a reserved file (`foreman.toml`, the guard table) is
  refused, for every role; reading one stays free.

### Changed

- The orchestrator's instructions give a table of which job goes with which model (Haiku, Sonnet or Opus), ask for
  the same `model` in `foreman launch` and in the Agent call, and the planner names the type and model of each
  sub-issue. A sub-issue keeps its type and model (`foreman backlog sub ID Tn --type dev/sonnet`); `foreman launch`
  takes it as its default and refuses a dearer one, and the Agent call that would cost more than its launch is denied.
- A question's page is written for that question: docs/owner.md has a «Question page» template (what is happening,
  one example, a table of the options with what changes, cost, risk and whether it can be undone, the
  recommendation; about 30 lines), and `backlog decision add` warns, without blocking, of a `--page` longer than 60
  lines, already the page of another question, named or recorded as a report or a plan, or whose text fails
  `foreman plain`. docs/owner.md also gains «Explaining a decision»: ask only what is the owner's, start from what
  they will see, one concrete case in their units, how we know, options as outcomes, their words.
- Agents never add a question for the owner: they propose it in their report, and the orchestrator weighs each
  question first (docs/agent.md, docs/orchestrator.md): only what is the owner's (cost, risk, the product's taste,
  credentials, what cannot be undone) goes to them; the rest it decides with a note of why, or makes and tells.
- A branch whose last review round left serious findings open is asked about with a question page that links the
  review on its last line, not with the review as the page: the loop's notice says so, and the pass finds the
  question by a page that names the review's file (a question asked with the review itself as its page still
  counts).

## [0.61.0] - 2026-11-06

### Changed

- «A branch review» (agents/reviewer.md) also looks where reviews missed known bugs: time and concurrency (a
  blocking call in a timer tick or heartbeat), absent data read as empty, and a guard or gate that refuses too much;
  a finding that leaves a safety check or a main function of the change not working is never L.
- The `mechanic` agent now runs at `effort: low` (set in its definition, since a model other than the session's does
  not take the session's level); the orchestrator guide says so.
- «A harness review» (agents/reviewer.md) has a fifth outcome for a rule: replace it with an automatic check (a
  test, a guard row, a `foreman check` item), which ends as a code sub-issue.
- The guard's refusal of an agent's git that rewrites a main checkout (`git:main-checkout`: a merge, a checkout or
  switch of a branch, `checkout -b`, `switch -c`, a hard reset, a stash…) now covers the main checkout of every
  `[repos.*]` path of every registered product, not only the one that holds a live home: a product whose home lives
  outside its code repo had that repo unprotected. Its worktrees and the orchestrator pass as before; the refusal
  says «a main checkout of PRODUCT».
- `foreman launch` refuses production work of an issue without its worktree: a `dev` (or a production type, or
  `--production`) or a `mechanic` launched with `--issue` and without `--repo/--branch` is exit 2, nothing made.
  Such a launch recorded no branch, so the delivery check never ran for the agent. A mechanic that changes no code
  takes `--no-production`; planners, reviewers and `--review` launch as before.
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
