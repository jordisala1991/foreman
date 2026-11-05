# Changelog

All notable changes to foreman are recorded here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)
and the version follows [Semantic Versioning](https://semver.org/). The version lives in `.claude-plugin/plugin.json`;
each release is tagged `foreman--vX.Y.Z` and the `release` branch points at the latest one (CONTRIBUTING.md).

Besides the usual sections, a release may have **Config** (new `foreman.toml` keys and the `config_schema` they need)
and **Upgrade notes** (anything a user has to do: state, backlog, service or registry changes).

## [Unreleased]

### Added

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
  confirmation, with what it changes; the others answer on one tap. In the list, its card shows a chip per option,
  the recommended one and a «Decide» button (quick answers only when every option can be undone). Questions without
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

### Config

- Schema 20: `[repos.*] review` (`"off"` by default, or `"auto"`), `review_paths` (a list, as `risky_paths`) and
  `review_lines` (400; 0 turns the size off). The three are brakes for `foreman permissions`: `review` back to `off`,
  a `review_paths` entry removed, or `review_lines` raised or set to 0 opens.

### Upgrade notes

- No schema change (still 5). An older foreman that writes the backlog drops `details`, `unanswered` and `page` of a
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
