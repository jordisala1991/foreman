# foreman-next: design

One responsible agent per task, a loop without a model, a short guard, and a system that watches itself.
Nothing needs keeping alive but the loop, which has no model.

## Pieces

```
            owner ──── panel (React) ── the panel server (JSON API, local, key)
              │  └─── claude attach / agent view / Claude app: talks to any agent directly
              │
   backlog.json (schema 5) + state/ (events, guard log, grants, seen, ci, loop heartbeat)
              │
   foreman loop (plain process, launchd KeepAlive) ── every N s, per registered project:
      sources → answers → agents → queue → health → blockers → launch
              │
   claude --bg -n NAME --settings {env, guard hook}   (one session per task, plus one per review round)
      planning agent  (cwd: the repo, read only)     → foreman ready / foreman ask / foreman block
      responsible agent (cwd: its worktree, branch)  → subagents, foreman ask, foreman done / foreman block
      hunter, then reviewer (launched by the loop, read only) → hunter file, review file, foreman verdict
```

Python stdlib only (3.11+, `tomllib`); the panel is React/Vite source in `panel/src`, built to `plugins/foreman/panel/dist`.

## A project

A *home* folder: `foreman.toml` (the owner's settings), `backlog.json`, `state/`, `worktrees/`, optional `sources/`.
`foreman.toml` points to the repo (`[project] repo`, `main`, `prefix`, `branch_prefix`). The registry
(`~/.config/foreman/projects.json` or `$FOREMAN_REGISTRY`) lists the homes the loop and the panel serve. Several repos are
`[repos.<key>]` tables (`path`, `main`) instead of `[project] repo` (both forms: fm refuses); tasks run in the first one
listed until `## Several repos` is built (FN-144 subtask 4). A project owns its home, every repo, its worktrees folder
and the umbrella folder set in `[project] umbrella` (explicit, never guessed). A command finds its project by `--project
NAME`, `--home`, `FOREMAN_HOME`, `FOREMAN_PROJECT`, then the registered project owning the current folder (paths resolved, the
deepest folder wins; two projects claiming it: fm refuses and `foreman health` names them), then the nearest `foreman.toml` above
it. Every setting is per project: `[agents] slots` (how many at once), models, extra session settings; `[merge]
autonomy`, `provider`, `strategy`, `test`, `[risk.high]`, `[risk.medium]` (label → path patterns); `[guard] ask`;
`[health]` timings; `[[recurring]]` entries (see Recurring tasks). With several repos, `[merge]` and `[risk]` are the defaults a repo's own tables override (see
Several repos).
`foreman init` makes `foreman.toml`, `backlog.json` and `instructions.md` (the owner's own rules), never overwrites a file of the
home, and prints the two lines that install the plugin. The plugin (`foreman@foreman`, marketplace `foreman`, from this repo's branch
`foreman-next`; no `version` anywhere, so Claude Code's version is the commit and every merge reaches it) gives a
session in any folder of a registered project the skill `foreman:define-task` (its values from `foreman project`), the shared
instructions with the project's values plus `<home>/instructions.md` (the SessionStart hook `foreman session-context`; `bin/foreman` re-executes under a newer `python3` from PATH or names the minimum, and the hook leaves that line for `foreman health`), and
`foreman` on the Bash tool's PATH (Claude Code adds the plugin's `bin/`, which ships `foreman` and no executable `fm`: macOS's `/usr/bin/fm` stays macOS's; no hook writes PATH); a session elsewhere gets no context. Agents run with it disabled (`enabledPlugins` in their settings).

A project's repo is its own clone, beside its home: `~/Projects/<name>/repo` and `~/Projects/<name>/home`.

## The states of a task

```
 triage ──go──▶ backlog ──loop──▶ refining ──foreman ready──▶ ready ──loop──▶ in progress ──foreman done──▶ in review
 (health,        (to plan)        (planning agent)                       (responsible agent)      (check, review,
  sources)                                                                      ▲                    risk score)
                                                                                └──── foreman reject ─────┤
                                                                                                     ▼
                                                                                  done ◀── foreman accept / autonomy,
                                                                                  then the merge step
 any open state ── foreman cancel ──▶ canceled          `blocked` (a reason) on any state: the loop leaves it alone
```

- `triage`: filed by a health check, a source, or without acceptance criteria (the only state such a task can be in).
  Never launched until the owner says «go»; `foreman go` refuses a task without acceptance criteria.
- `backlog`: to plan (it has acceptance criteria, set with the owner). `refining`: its planning agent works. It ends
  with `foreman ready --plan|--no-plan`.
- `ready`: has acceptance criteria and a plan (or none, if trivial). `in progress`: its responsible agent works, in its own worktree and branch.
- `in review`: delivered. The loop checks and reviews the branch; the owner accepts it or rejects it with a note.
- `blocked`: a reason set by an agent (`foreman block`), by the loop (could not launch; stopped without finishing after a
  nudge). The owner clears it (`foreman unblock [--relaunch]`).
- `blocked_by` (`foreman add/edit --blocked-by FN-n …`; `none` clears): the task is never planned or launched until every
  blocker is `done`, then the loop marks it unblocked in the history. A canceled or duplicate blocker blocks the task
  with a reason: the owner edits `--blocked-by` or cancels, then `foreman unblock`.
- `type` (`foreman add/edit --type change|bug|mechanical|definition`; default and every task without one: change) only
  changes the instructions of the task's agents, one paragraph per type; a parent has none.
- `foreman edit` changes title, goal, criteria, detail, model, effort, type or blockers of a task in triage, backlog or ready (never one with an agent,
  nor a closed one); criteria or detail on a backlog or ready task drops its plan and returns it to backlog.
- A task too big for one PR is a parent with subtasks (`foreman add --parent FN-n`, only while the parent is in triage or
  backlog; one level). A subtask is an ordinary task (own agent, branch, PR) that names its parent. A parent in triage
  closes by itself when every subtask has (done if any is done, else canceled). Once let in (backlog) it gets a parent
  agent (`agent.kind` `parent`, no worktree, status stays backlog): it is told each subtask merged or canceled and
  nothing else (`notified` lists those told; message kind `subtask`), proposes the next Go and changes to the owner, may
  file (`--triage`) and edit only its own subtasks, and closes the parent with `foreman done` (refused while a subtask is
  open) after the owner's yes. `foreman cancel` of a parent cancels its open subtasks and closes their PRs. A `lens` entry files a chain: see Improvement chains.
- A question (`foreman ask`) does not change the state: it is a decision tied to the task and its agent. Its agent ends
  its turn; the owner answers in the panel (or talks to that agent directly); the loop resumes *that* session with the
  answer.

## The loop

A plain process, no model (`foreman loop`; `foreman launchd` prints two jobs, loop and panel, with `RunAtLoad` and `KeepAlive`; `--dir` writes them as files. Run from the installed plugin, a job's command names no version: at each start it runs the version of `foreman@foreman` Claude Code records as installed (`~/.claude/plugins/installed_plugins.json`, `foreman/launcher.py`), and it exits when another version is installed so launchd starts the new one (the loop before its next tick, the panel within 30 s); each version folder never changes, so a job never mixes two versions. Run from a plain checkout, the jobs name that checkout. Agents use the normal claude.ai login, so they show in the Claude app; the loop removes `CLAUDE_CODE_OAUTH_TOKEN` from every agent it launches. `foreman health` warns 3 days before the login expires and files a finding when it does not work (`claude auth status`)); one per registry (a lock file).
Each tick, per project, each step isolated (an exception goes to `state/errors.jsonl`, which a health check turns
into a finding), and inside a step each task isolated (a failure blocks that task with the reason; the others go
on). The heartbeat is written before every step, so a long test run does not look like a dead loop.

1. **heartbeat**: `state/loop.json`.
2. **sources**: every executable in `<home>/sources/` prints JSON lines, each with a `key` (dedup). Without `task`,
   `{key, title, detail}` becomes a triage task, deduplicated by source and key. With `task` (a task id),
   `{key, task, action, text}` acts on that open task through the owner's own commands: `note` (text reaches its
   agent like an answer), `reject` (text; as `foreman reject`), `accept` (as `foreman accept`), `done` (closed as done, merged
   outside fm), `cancel` (as `foreman cancel`). Applied once per source and key (`state/applied/`). Unknown or closed
   task, unknown action, missing key, or an action invalid for the task's state: nothing changes, logged in
   `state/errors.jsonl`, not retried. A transient failure (accept before its check, busy checkout, hung stop) is
   logged and the same key is tried again next tick.
   Cursors in `state/cursors/`. None shipped (the owner's call for now). The same step first files the
   recurring entries that fell due (`foreman/steps/schedule.py`, see `## Recurring tasks`).
3. **answers**: each answered question, permission or rejection not yet delivered is sent to its agent's session
   (`claude --bg --resume SID MESSAGE`, no other flag: a flag or a live process makes claude start a copy), when that
   session is not busy: the idle session is stopped, fm waits until its process is gone (+3 s), then resumes; at most
   one answer per task per tick; a copy (different short id) or an unreadable `claude agents` fails the send; dropped if the task closed meanwhile;
   a failed try is retried next tick; the answer stays not delivered and `foreman health` shows it after 10 minutes. A session id not listed yet at launch is
   filled in from its short id on a later tick.
4. **agents**: an agent whose session ended its turn without `foreman done`/`ready`/`ask`/`block`, and whose transcript
   has been quiet for 10 minutes (so not while the owner is talking to it), is nudged once, then blocked.
5. **queue**: accepted tasks are merged first, one at a time; with a test command, each only once its tests pass on it
   merged with main as main is at that moment (the merged check, `loop.gate_merge`: see below). Then a branch in review whose head or main's head changed is checked,
   one at a time, in the background (a tick waits for it only `BACKGROUND_WAIT_S`, so answers, launches and merges keep
   flowing; each task is read again just before its turn): tests on exactly its committed head
   (a fresh detached worktree, never uncommitted files), diff, deleted files,
   dependencies, the paths it touches, conflicts with main, the review's verdict → risk `low|medium|high` with
   reasons. A branch that conflicts with main waits, shown, until someone wants it merged (the owner's accept or the
   autonomy): then it goes back to its agent with a note to merge main, and merges once resolved without a new accept.
   A branch whose tests or CI fail is returned to its agent with the last lines of the test output or each failing
   CI check's name and link, twice in a row at most; the third failure waits for the owner.
   If the project's autonomy allows the risk, it merges (`git merge --no-ff` into the main checkout),
   removes the worktree and branch and closes the task.
6. **health**: the checks below; the tests of main run in the background the same way (the tick waits for them
   only `BACKGROUND_WAIT_S`; the result lands in `ci.json`).
7. **blockers**: a task whose blockers are all `done` is marked unblocked once; one whose blocker closed undone is blocked
   with a reason (`foreman/steps/release.py`).
8. **launch**: while running agents < `slots` and launching is not paused, the next `ready` task (its responsible
   agent) or `backlog` task (its planning agent), by priority then id, skipping any with a blocker not `done`. If the task changed while its session
   started (the owner cancelled it), the new session is stopped. The loop never plans and never reviews. A parent is
   never launched here: just before, a let-in parent gets its parent agent regardless of slots (`foreman/steps/parents.py`).
   An agent waiting for the owner (open question or permission, no answer on its way) does not count against the slots;
   an answer is delivered at once, even over the slots.
   A pause stops this step, the parent agents and the mechanics; reviews, checks and merges go on.
9. **mechanic**: for each triage task health filed (`source` `health:*`) that has none yet, one mechanic session, in the
   slots launch left free (it counts as a slot while it has no diagnosis). It stops once the diagnosis is in, and a mechanic
   that ended without one or ran over 30 minutes leaves a «Diagnosis missing» note; it is never relaunched.

`foreman cancel` stops its agent, closes its open questions and removes its worktree (the branch stays).

### What the loop does with a task in review

Each tick, for a task in review (not accepted, not blocked), in this order. The decisions are pure functions in
`foreman/core/rules/decide.py`; the loop (`queue_one`) gathers the facts (sessions, files, git, CI, the check) and acts on the answer.

A review runs only when its result goes somewhere: to the agent (rounds 1-2, an owner reject, a conflict resolution,
the fix of failing tests or CI)
or, once, to the owner when the rounds run out. Each launch event ends with the rule that launched it
(`rule: rounds 1-2`, `rule: owner reject`, `rule: conflict resolution`, `rule: failing tests or CI`: `decide.RULES`).

| Situation | What the loop does | Function | Test |
|---|---|---|---|
| A tip with no review, in rounds 1-2 | launches the hunter if a slot is free; else waits | `review_step` → `hunt` / `wait` | `test_no_hunter_launches_it` |
| The hunter's file is written and the hunter is not working | launches the reviewer if a slot is free; else waits | `review_step` → `review` / `wait` | `test_hunter_file_and_ended_launches_reviewer` |
| The hunter ended without its findings file | blocks the task | `review_step` → `block` | `test_hunter_without_file_blocks` |
| The reviewer ended without a verdict | blocks the task | `review_step` → `block` | `test_reviewer_without_verdict_blocks` |
| Verdict with findings open before the last round | back to its agent for review: its note, once | `review_step` → `back` | `test_open_findings_go_back_once` |
| A new tip that only merges main, with no conflict | no review: the verdict holds (`review.holds`) | `launch_rule` → `hold` | `test_a_clean_merge_or_a_closed_review_holds`, `test_a_clean_merge_of_main_launches_nothing` |
| A new tip after the loop acted on a verdict of the last round, open or clean (`review_for` none) | no review: the verdict holds; what is open stays for the owner; no round 3 runs by itself (after a round-1 verdict, a new tip gets round 2) | `closes_review`, `launch_rule` → `hold` | `test_a_review_closes_after_the_last_round`, `test_after_two_clean_rounds_a_new_tip_launches_nothing`, `test_a_clean_merge_or_a_closed_review_holds`, `test_after_round_two_later_tips_launch_no_review`, `test_a_new_tip_after_a_clean_verdict_gets_a_review` |
| The agent's fix after `foreman reject` | one review of the fix only (`git diff` reviewed tip..new tip), from round 1; open findings back to its agent | `launch_rule` → `reject`, `review_base` | `test_reject_and_conflict_launch_with_their_base`, `test_reject_gets_a_review_of_the_fix_and_open_findings_go_back` |
| A new tip that merges main resolving a conflict | one review of the resolution only (`git diff` git's automatic merge..new tip), from round 1; open findings back to its agent | `launch_rule` → `conflict`, `review_base` | `test_reject_and_conflict_launch_with_their_base`, `test_accept_then_conflict_resolves_and_merges_without_a_new_accept` |
| The agent's fix after failing tests or CI | one review of the fix only (`git diff` failing tip..new tip), from round 1; open findings back to its agent | `launch_rule` → `failing`, `review_base` | `test_the_fix_of_a_failure_gets_a_review_of_only_that_fix`, `test_the_fix_gets_a_review_of_only_that_fix` |
| CI pending | waits | `waits_for_ci` | `test_pending_ci_waits` |
| Head, main or CI moved since the last check | checks it again (in the background) | `checks_again` | `test_moved_branch_is_checked_again` |
| It conflicts with main and nobody said it should merge | waits for the owner; `foreman show` and the panel say it conflicts and goes back when accepted | `after_check` → `owner` | `test_conflict_waits_unless_someone_wants_the_merge`, `test_a_waiting_task_that_conflicts_stays_and_says_so` |
| It conflicts and the owner accepted it (also a merge refused for a conflict) or the autonomy would merge it | back to its agent for the conflict, keeping that accept (`resolving`) | `after_check` → `conflict`, `conflict_note` | `test_conflict_goes_back`, `test_conflict_waits_unless_someone_wants_the_merge` |
| Tests or CI fail, returned fewer than 2 times in a row (`failure_streak`; without a test command a check without failures ends the row, with one only the merge does) | returned to its agent with the failing output or checks, whoever wants the merge; keeps its check (the panel shows why) and counts `failures` | `after_check` → `failing`, `failing_note` | `test_failing_tests_or_ci_go_back_up_to_the_cap`, `test_failing_note_names_the_output_and_the_checks`, `test_failing_tests_go_back_with_the_output`, `test_failing_ci_goes_back_with_each_check`, `test_a_green_check_ends_the_row_without_a_test_command` |
| Tests or CI fail a third time in a row | waits for the owner, the failure shown in its check | `after_check` → `owner` | `test_failing_tests_or_ci_go_back_up_to_the_cap`, `test_the_third_failure_waits_for_the_owner` |
| Resolved, no findings open, checks green, with the owner's accept kept | merges without a new accept (an accept the autonomy made is decided again: high never merges alone) | `after_check` → `merge` | `test_conflict_waits_unless_someone_wants_the_merge`, `test_accept_then_conflict_resolves_and_merges_without_a_new_accept` |
| The resolution's review leaves findings open, tests fail or CI is not green | the kept accept lapses; waits for the owner | `after_check` → `owner` | `test_conflict_waits_unless_someone_wants_the_merge`, `test_a_resolution_left_open_waits_for_the_owner` |
| Autonomy allows the risk, no open findings, CI green where the provider has CI | merges alone | `after_check` → `merge` | `test_autonomy_merges_alone` |
| Anything else | waits for the owner (`foreman accept` / `foreman reject`) | `after_check` → `owner` | `test_everything_else_waits_for_the_owner` |

### The merged check (FN-113)

A branch's own check runs its tests on the branch alone. Just before an accepted branch merges (the owner's accept or
the autonomy's, `loop.gate_merge` on both paths), the test command runs again on the branch merged with main as main
is at that moment (`foreman/services/delivery/mergecheck.py`, in the background like the check). The result goes in the task's `check`
(`merged_with` = that main commit); `foreman show` (`checked_on`) and the panel say what the last check ran on. A task not
about to merge (in review, waiting for the owner) never runs it. Accepted tasks merge one at a time, so of two waiting,
the second is checked with the first already in main.

| Situation at the merge step | What the loop does | Function | Test |
|---|---|---|---|
| No test command | merges as today: the PR's CI stands in | `as_today`, `merge_gate` → `merge` | `test_without_a_test_command_it_merges_as_today` |
| It conflicts with main | as today: the merge returns it to its agent (FN-133), no merged run | `as_today`, `merge_gate` → `merge` | `test_conflict_goes_back_without_a_merged_run` |
| No merged check on this head and main as it is now (never run, or main moved since) | runs the merged check, then decides | `merge_gate` → `check` | `test_passes_alone_and_merged_merges`, `test_main_moved_after_a_pass_checks_again` |
| Passed merged, neither main nor the branch moved since | merges without running the tests again | `merge_gate` → `merge` | `test_nothing_moved_merges_without_a_run` |
| Fails merged, fewer than 2 returns in a row | back to its agent with the output, saying it failed merged with main `<sha>`, not on its branch alone; counts in `failures` and `failure_streak` | `merge_gate` → `failing`, `failing_note` | `test_fails_merged_goes_back_saying_so`, `test_autonomy_path_checks_merged_too` |
| Fails merged a third time in a row | its accept lapses; waits for the owner, the failure shown in its check | `merge_gate` → `owner` | `test_third_merged_failure_waits_for_the_owner` |

With a test command, a green check of the branch alone no longer ends a row of failures (`failure_streak`): only the
merge does, otherwise each fix passing alone would reset the count. Without one, a green check ends it, as before
(`test_a_green_check_ends_the_row_without_a_test_command`).

### The loop's other decisions (FN-176)

| Situation | What the loop does | Function | Test |
|---|---|---|---|
| Tasks in review at the queue step | accepted ones merge first, then the others are checked, each in the backlog's order; a blocked one waits | `queue_order`, `queue_turn` | `TestQueue` |
| A branch check was started this tick | the other checks wait for the next ticks | `queue_full` | `TestQueue`, `test_a_failure_after_the_check_does_not_start_a_second_check_in_the_tick` |
| A provider call fails `Transient` | counted, the new error noted once; the 3rd in a row blocks the task | `retry_step` | `TestRetry`, `test_any_provider_call_failing_transient_three_times_blocks_with_the_error` |
| No test command and the PR's CI has a result | the CI's result stands in for the tests in the check | `tests_or_ci` | `TestTestsOrCi`, `test_ci_replaces_the_local_test_without_a_test_command` |
| `[merge]` names an unknown provider, pr or strategy, or a strategy its provider lacks | a clear config error naming the value | `provider_error` | `TestProviderError`, `test_unknown_merge_or_provider_is_a_clear_config_error` |
| Main moved since its tests ran, or they ran over `ci_every_minutes` ago | runs main's tests again (in the background) | `main_tests_due` | `TestMainTestsDue`, `test_failing_main_is_one_task_until_it_passes` |

The decision tests are in `tests/test_decide.py` and launch no program; the loop's in `tests/test_review_rules.py`. How
a tip follows the reviewed one is `delivery.merge_of`: a merge of the reviewed tip and a main commit whose tree equals
git's automatic merge (`git merge-tree`) is clean; any other tree is a resolution; anything else (plain commits, a
rebase) is neither, and in a conflict episode its whole branch is reviewed.

## The agents

Each is a Claude Code session of its own, named after its task (`tk-1`, `tk-3-planning`), started with
`claude --bg -n NAME --model M --effort E --permission-mode bypassPermissions --settings S PROMPT` in its cwd:

- `M` and `E` are the project's, except for the responsible agent: the task's own `model`/`effort` (optional task keys, chosen by the planning agent with the rubric in `planning.md`) win; the planner fills only the keys the owner left empty (`foreman ready` never overwrites one).
- `S` sets `FOREMAN_HOME`, `FOREMAN_TASK`, `FOREMAN_AGENT` and `PATH` (so `foreman` works), `worktree.bgIsolation: none`, the guard as
  a PreToolUse hook, and the project's extra settings.
- The owner opens any of them: `claude attach SHORT`, `claude agents`, or the Claude app; what he says there goes to
  that agent.

**Planning agent** (`foreman/briefs/planning.md`): the acceptance criteria are product (observable behaviour, never how) and
are settled with the owner when the task is filed; the planning agent never writes, rewrites or splits them, and `foreman ask`s
the owner if one cannot be met as written. It reads the repo (read only), asks the owner only what only he can decide
(`foreman ask`, then ends its turn), and ends with `foreman ready --detail --plan PATH` (or `--no-plan "why trivial"`). It writes a
plan at `<home>/plans/ID.md` (files touched, steps, decisions with alternatives dropped, out of scope, risks, declared
risk); the task gets `plan_file`, and the responsible agent follows it and `foreman ask`s when it does not hold. Its statements of scope cite their search; its questions stand alone (context, options, recommendation and why).

`foreman done`, `foreman ready` and `foreman verdict` take `--misled`; the text (not «none») is kept on the task under `misled`, per
agent, once; `foreman metrics` shows it.

**Responsible agent** (`foreman/briefs/responsible.md`): owns the whole task in its
worktree: splits it, uses subagents, writes the failing test first for bugs and behaviour, keeps tests green, merges
main before delivering if it moved, asks the owner only what only he can answer, writes a report
(`<home>/reports/ID.md`: the task's description, each acceptance criterion with its evidence; fm sets it as the description
inside `<!-- foreman:description -->` markers, adds a footer with the commit and review state, folds its `## Review rounds`
section in `<details>`, and the panel shows it; the loop seeds the file from `foreman/briefs/report.md`, or from the repo's
`.github/pull_request_template.md` plus a Review rounds section, when it launches) and ends with
`foreman done --summary --report --misled`. If the review returns it or the owner rejects it, the note arrives in the same
conversation: each finding gets the smallest change that settles it, or a one-line answer if it thinks the finding is wrong; add a `### Round N` to the report's «Review rounds» section, `foreman done` again.

**Review: hunter, then reviewer** (`foreman/briefs/hunter.md`, `foreman/briefs/reviewer.md`): launched by the loop, not by the responsible agent, each as
its own session in the branch's worktree, once per delivered tip; each counts against the slots (the hunter until its file exists, so its own reviewer can take its place). Both get the task's detail
and the owner's notes. The **hunter** lists everything it suspects (with a confidence each, plus the acceptance criteria table and an
inventory of what the diff adds) in `<home>/reviews/ID-rN-<tip>-hunter.md`. When that file exists and the hunter has ended,
the **reviewer** starts; it sees only the file, never the hunter's reasoning. It checks each Wrong or Missing finding
(confirmed, refuted, or neither) and each Too much item; a finding leaves only with a cited line or output that refutes it,
so what it could not refute stays in To fix. What was dropped goes under Noted and is not sent to the responsible agent. It writes
`<home>/reviews/ID-rN-<tip>.md` (header `Branch/Tip/Round/Open`, N = items under To fix) and runs
`foreman verdict --tip --open N --file`; a verdict for a tip other than the newest launch is stored in the review history but
decides nothing. There are no severities. Open findings in round 1 → back to the responsible agent (`decide.review_step`);
round 2 checks the fixes only and opens nothing new unless a fix is itself Wrong or Too much; what is still open after
round 2 goes to the owner as high risk. A hunter that ends without its file, or a reviewer without a verdict, blocks the task.
Each review session gets its own `<home>/scratch/ID-rN-<tip>-ROLE/`, told in its brief as `$scratch`; the loop removes it on the first tick where no live session owns it.

The **mechanic** (`mechanic` kind, the task's `mechanic`) diagnoses a health finding and changes nothing else: read-only by its
brief (as the hunter's), its only write is `foreman diagnose --file`, which checks the required `##` headings (`foreman/core/oversight/diagnosis.py`)
and appends the diagnosis to the task's `detail`, so `foreman show` has it. The task stays in triage; the owner decides next.

## Agents through Orca (FN-204)

Defined, not built. FN-203 wants every agent of a project set to `runner = "orca"` to be an Orca tab the owner can open
and write to (also from Orca on the phone), while foreman keeps launching, messaging, watching and stopping it. This
section answers how, from commands run on this Mac; the evidence log is `<home>/plans/FN-204-evidence.md` («E1»-«E8» below
are its sections; E8 and E9 hold the review's re-runs). The owner's questions Q1-Q5 at the end gate the Orca adapter (subtasks 2-3), not this definition.

### How it was tried

Orca 1.4.261 and Claude Code 2.1.325 on this Mac. A scratch git repo with worktrees made by git (as foreman makes them),
registered in the owner's Orca; restarts on a second, headless Orca (`ORCA_USER_DATA_PATH=… orca serve --port 6790`) so
the owner's tabs were never touched; the owner's `~/.claude/settings.json` holds Orca's hooks for every event and
`permissions.defaultMode: "auto"`. Terminals and worktrees made were closed and removed and the second runtime killed;
Orca has no verb to delete a repo or a Run, so the scratch repo (hidden with `orca repo hide`) and one Run named
«fn-204 scratch (remove)» stay in the owner's Orca (E, last section).
E8 and E9 (rounds 1 and 2 of the review) ran D1's exact line, a multi-line send, titles, a restart and the word after `exec` on a
second headless Orca only.

### Answers

`T` is a terminal handle (`term_…`), `WT` a worktree path, `REPO` the repo's main checkout.

| Question | Answer (the design) | Command → decisive output |
|---|---|---|
| Launch with model, effort, permission mode, settings (the guard hook) and the brief | A tab made by `orca terminal create` running `unset CLAUDE_CODE_OAUTH_TOKEN; exec claude …` with every flag foreman passes today (D1). Orca's own verbs cannot: `agent launch` and `orchestration worker-start` take only model and effort | `orca terminal create --worktree path:WT --title "FN-77 responsible · test" --command "$LINE" --json`, `LINE` = `claude --model sonnet --effort low --permission-mode bypassPermissions --settings JSON PROMPT` → claude's footer `⏵⏵ bypass permissions on`; the agent's `echo task=$FOREMAN_TASK` → `task=FN-77`; the stand-in guard hook received the PreToolUse JSON (`session_id`, `cwd`); a 24,700-byte brief passed as `"$(cat brief.md)"` → the agent answered `4242`, its last line. `agent launch --help`, `worker-start --help`: no flag for settings or permission mode (E2). D1's exact line, run (E8): row with the `paneKey` `terminal create` returned, `state: done`, `4242`, the hook's `session_id` = the `--session-id` given, the transcript holds `task=FN-77`; with `exec env -u … claude` instead, every send → `provider: "unsupported"`, no `turn_started` |
| Send a message as foreman does today | `orca terminal send` to a live idle tab; an ended session gets a new tab with `--resume SID` and the same flags (D4) | `orca terminal send --terminal T --text MSG --enter --wait-submit 10 --json` → `accepted: true`, `prompt.stages: ["input_accepted","turn_started"]` (E3); a three-line `--text $'…\n…\n…'` → the same stages, one turn, all three lines answered (E8); `exec claude --resume 11111111-… --model sonnet --effort low --permission-mode bypassPermissions --settings JSON "msg"` → the same transcript grew 46 → 79 lines, no new file, the hook's `session_id` the same (E2) |
| Tell idle, busy, waiting for input and ended apart | `orca worktree ps --json`, `agents[].state`, matched by `paneKey`; ended = the terminal gone from `terminal list` (D3) | busy: `state: "working"`, `toolName: "Bash"`; idle: `state: "done"`; waiting: `state: "waiting"` (`toolName: "ExitPlanMode"`; the trust dialog with no tool in E2, on the desktop Orca, launch line not recorded), worktree `sidebarStatus: "permission"`; but with D1's own line on the headless Orca the trust dialog left no row at all (`agents: []`, E8), so the adapter also reads the screen (D3); ended: `/exit` in an `exec claude` tab → `terminal wait --for exit` → `status: "exited"`, `exitCode: 0`, `terminal list` → `totalCount: 0`. `terminal wait --for tui-idle` → `satisfied: true` while the row said `working`: not a guide (E4) |
| Stop it and close its tab | `orca terminal close --terminal T --tab` (D5) | → `closeMode: tab`; then `terminal list` → `totalCount: 0`, `ps` shows no claude of it. An orphaned tab: `close --tab` → `terminal_handle_stale`, `close` → `tab_not_found`, and it was gone from `terminal list` (E5) |
| Must Orca know the repo; how a foreman worktree becomes an Orca worktree without Orca making one | Yes: `repo add` once, then `path:<worktree>` addresses foreman's own worktree; Orca creates none (D2) | unregistered: `terminal create --worktree path:WT2 …` → `"code": "selector_not_found"`; after `orca repo add --path REPO --json` → `orca worktree show --worktree path:WT --json` → the existing worktree, `isMainWorktree: false`; `worktree list` shows it only after `orca repo set --repo path:REPO --external-worktree-visibility show` (E1) |
| Titles | `--title` at launch, `orca terminal rename` once after, the worktree's display name (D9) | `--title` names the tab and becomes the agent's `conversationName`; `terminal list` later shows claude's own title (`FOREMAN_TASK echo test`); `orca terminal rename --terminal T --title "…"` sets the tab's custom title (E6), and `terminal list` shows it after a turn (E8). Foreman finds a tab by its stored handle, never by its title (D3) |
| Orca not running | Every call but `status` fails with `runtime_unavailable`: launches wait, nothing is blocked (D7) | `orca status --json` → exit 0, `app.running: false`; any other → exit 1, `error.code: "runtime_unavailable"` («Start the Orca app first»); `terminal send` → «no input was sent» (E7) |
| Orca restarts | Shown on a second, headless Orca only: the agent kept running, its handle stayed valid and its state came back. The owner's desktop Orca was not restarted (inferred: the same terminal daemon design) | `kill -TERM` of `orca serve`, then `serve` again on the same port: the claude process kept its pid; `terminal show` → same handle, `connected: true`, `writable: true`; a message after it → `turn_started` and answered (E7, E8); the row went `working` (`toolName: Bash`) then `done` (E8); `conversationName` was lost in E7, kept in E8 |

**The design** (each line answers a row above or a gap below):
- **D1 Launch.** `orca terminal create --worktree path:<cwd> --title "<title>" --command '<line>' --json`, `<line>` =
  `unset CLAUDE_CODE_OAUTH_TOKEN; exec <claude> --session-id <uuid4> --model M --effort E --permission-mode bypassPermissions --settings '<json>' "$(cat <brief file>)"`
  (`shlex.join` for all but the brief; the brief in `<home>/state/briefs/<task>-<role>-<uuid>.md`; the JSON the same dict `sessions.settings` builds).
  `exec` makes the tab end with claude (E4, ended (b): without it the shell stays and no agent row is left); the word after `exec` must be the bare name `claude`:
  Orca reports a send's delivery (`turn_started`) only then. Run (E9): `exec claude` and `PATH=<dir>:$PATH exec claude` (a wrapper named
  `claude` in `<dir>`, which ran) → `provider: "claude"`, `turn_started`; `exec /abs/path/claude` (the same binary), `exec <wrapper>` and
  `exec env -u … claude` → `provider: "unsupported"`, `input_accepted` only, while rows and states still came. So when `[agents] claude`
  is a path, the adapter writes `PATH=<its folder>:$PATH exec claude`; a value whose file name is not `claude` is a config error under
  `runner = "orca"` (D10). Not run: that `PATH` change for a `[agents] claude` in a folder shared with other programs. `terminal create` returns the tab's `handle` and `paneKey`; both are kept (the port below). Not `agent launch`
  (no settings nor permission mode; Orca refused `haiku` + `low`, E2) nor `worker-start` (the same, plus a
  structured chat worker with Orca's own flags that needs a calling Orca terminal, `--from`, which the launchd loop is not).
- **D2 Repo.** The adapter's `ensure_repo(path)`: `orca repo list --json`, else `orca repo add --path P --json`; then
  `orca repo set --repo path:P --external-worktree-visibility show` (foreman's worktrees show in the sidebar). Planning, mechanic
  and parent agents run in the repo's main checkout, the worktree Orca lists as `main`.
- **D3 State.** One `orca worktree ps --json` per tick; a row is matched to its terminal by `paneKey` (what `terminal create`
  returned). `working` → WORKING, `waiting` → WAITING, `done` → IDLE; a terminal listed with no row: the adapter reads its screen (`orca terminal read --terminal T --screen --json`), and
  `Yes, I trust this folder` on it → WAITING (D6 answers it when it may), anything else → WORKING (starting) until 2 minutes after
  `SessionRef.started` (a limit chosen, not measured: no start was timed), then WAITING;
  a terminal absent from `orca terminal list` → ENDED; a session with no terminal kept → GONE. The row has no claude session id
  (`orcaSessionId` is Orca's own, E8), so the adapter goes from the kept `pane` to the row and from the kept `terminal` to the tab. WORKING and WAITING are busy:
  nothing is sent to them. Any field but `state` and `paneKey` may be missing (`hasLivePty` was).
- **D4 Send.** Only to IDLE: `orca terminal send --terminal T --text MSG --enter --wait-submit 10 --json`; `turn_started` missing
  from `prompt.stages` → `RunnerError`, retried next tick as `running.send` is today. Ended: a new tab with D1's line, `--resume SID`
  instead of `--session-id`; `send` returns its new `terminal` and `pane`, which foreman keeps. A multi-line message is one turn (E8).
- **D5 Stop.** `orca terminal close --terminal T --tab`; `terminal_handle_stale` or `tab_not_found` count as closed when
  `terminal list` no longer has the handle. `stop_live` stays best-effort and never raises.
- **D6 Trust dialog.** Right after start, while the tab has no agent row (E8) or its row is WAITING with no tool (E2), the adapter reads
  `orca terminal read --terminal T --screen --json`; when the screen holds `Yes, I trust this folder` it sends `$'\x1b[B'` then `$'\r'`
  (E2, E8: the agent went on and answered its brief). Only for a folder inside the project's repos or worktrees folder. Pending Q2.
  A dialog D6 may not answer stays WAITING (D3), so nothing is sent to the agent. For any WAITING agent the loop writes one event on its
  task, «NAME waits in its Orca tab» (the tab shows why); the owner sees it in the panel, with the agent's state WAITING (`agent_state`,
  `ui/panel/views.py:66`; the panel's agent line gains a «waiting» label: `panel/src/cards.tsx`, `panel/src/strings/*.json`, `lib.ts`); `foreman list` and `foreman show` print neither. Written once, marked as a nudge is (`agent.waiting_seen`, as
  `agent.nudged` in `steps/talk.py`), cleared when the agent leaves WAITING.
- **D7 Orca down.** `orca status --json` says it (`app.running: false`); any other call's `runtime_unavailable` → `RunnerUnavailable(RunnerError)`.
  A launch that gets it leaves the task as it is (no block), writes one event and is retried next tick; a send is retried as any
  failure. Foreman never runs `orca open` (Q3). The binary is `$ORCA_BIN`, else `orca` on PATH, else
  `/Applications/Orca.app/Contents/Resources/bin/orca`, because launchd's PATH is short.
- **D8 Health**, `runner = "orca"` only: the binary is found; `orca status` answers (a warning, not a finding, when Orca is merely
  closed); Orca's hooks are in `~/.claude/settings.json`: without them no state ever appears and every agent would read WAITING once past D3's start limit
  (inferred: the owner's file holds them for every event and `ps` showed no other source).
- **D9 Titles.** `--title` at launch, then `orca terminal rename --terminal T --title "<title>"` once (claude overwrites the shown pty
  title, E6; the renamed title held in `terminal list`, E8); a responsible agent's worktree gets `orca worktree set --worktree path:W --display-name "<id> <task title>"` (E1).
- **D10 Config.** `[agents] runner = "claude" | "orca"` per project, default `claude`; under `orca`, `[agents] claude` must name a file
  called `claude` (D1); an unknown value is a config error naming it
  (as `provider_error` does). No fallback from one runner to the other.

### Chat view or terminal tab (FN-218)

Answers Q1 (below) from commands run on this Mac; the evidence log is `<home>/plans/FN-218-evidence.md` («F1»-«F17» below are its
sections). Q1's reason («the chat … cannot carry the guard hook, foreman's environment or a session id», E2) no longer holds: E2 tried
only Orca's launch flags, and the chat reads them from a settings file (the answers below). What remains of Q1 for the owner is the choice
itself, now between the two launches compared here. Orca 1.4.261, Claude Code 2.1.325, a scratch git repo with worktrees made by git, and a headless Orca of its own
(`ORCA_USER_DATA_PATH=… orca serve --port 6795`, started from an environment without foreman's variables); the owner's Orca,
`~/.claude` settings and repo untouched. Every chat: `orca agent launch --agent claude --surface chat --worktree path:WT --prompt P
--model sonnet --effort low --json` → `outcome.kind: "structured"` (never a terminal fallback). A guard stand-in hook logged its env and
stdin and denied any Bash command with `BLOCKME`; `P` asked for `echo task=$FOREMAN_TASK`, `echo BLOCKME`, `touch probe.txt` and the agent's skills.
The chat's process: `claude --output-format stream-json … --permission-prompt-tool=stdio --setting-sources=user,project,local …
--session-id=<uuid Orca picks> --replay-user-messages --dangerously-skip-permissions --settings {"fastMode":false}` (F1).

**The eight answers** (for the chat view; (a) is the worktree's `.claude/settings.local.json`):

| Question | Answer | Command → decisive output |
|---|---|---|
| Reads the worktree's `.claude/settings.local.json` | Yes, and live: a rewrite reaches a running chat. A session in a git worktree also reads the main checkout's file (hooks of both run, the main checkout's env wins); Claude Code does this, not Orca | (a) → hook `ev=start TASK=FN-901 … cwd=…/wt/fn-1` (F2); planning chat started as `FN-903`, the file rewritten for the parent → its next call `task=FN-904 agent=parent`; a chat in a worktree with no file → `TASK=FN-904 … tag=main`; the same with `claude -p` outside Orca (F3) |
| Its guard PreToolUse hook blocks a tool call | Yes; the real `foreman guard` reads the chat's hook input as it is | `echo BLOCKME` → `PreToolUse:Bash hook error: guard stand-in: BLOCKME denied` (F2); the captured stdin with `git push origin main` → `foreman guard` → `permissionDecision: deny` (push-main); without `FOREMAN_HOME` → allowed (F13) |
| `permissions.defaultMode = bypassPermissions` honoured | Not by the file: the mode comes from Orca's host setting `nativeChatPermissionMode`. At its default `full-access` Orca adds `--dangerously-skip-permissions`; at `ask` it adds `--permission-mode=default`, which beats the file, and the agent waits on a prompt | `full-access` → `touch probe.txt` ran under an `ask` rule, `permissionMode: bypassPermissions` (F2); `settings.update {nativeChatPermissionMode: ask}` → argv `--permission-mode=default`, transcript `"permissionMode":"default"`, `worktree ps` `state: blocked`, `sidebarStatus: permission` (F5) |
| The `FOREMAN_*` env reaches the agent | Yes, from (a) or a `--settings` file; from (b) only to the agent's commands, through `$CLAUDE_ENV_FILE`, not to the guard hook; not from a shared config (c) | `task=FN-901 home=…/S/home` (F2); (b) → `task= home=` (F9), with a SessionStart hook writing `$CLAUDE_ENV_FILE` → `task=FN-1`, the PreToolUse hook `env_TASK=` (F15); (c) → `task= home=…/S/home` (F10) |
| The Claude session id: recorded, how foreman reads it | Orca picks it (`--session-id`, no launch flag); the `worktree ps` row has only Orca's id (`claude_d3123690_…`). Foreman reads it from the hook's stdin `session_id` (a SessionStart hook in the same settings); the newest transcript of the folder is wrong (Orca's naming call writes one) | `--session-id=32b47fdb-…` = hook `session_id` = transcript `32b47fdb-….jsonl`; a send's result `providerItemId: "claude:be82ef77-…"` (F6, F8) |
| A message from Orca on the phone reaches the agent | Yes: the owner, from Orca on his phone, talks to a Claude Code session in Orca's chat view on this Mac and his messages arrive (FN-218 Q3, 2026-11-19); a script paired as the phone pairs did the same on the scratch Orca | owner's answer (F17); `phone.js` with the pairing link, capability `agent-session.structured.v1`, `agentSession.send` → `dispatchState: accepted`; transcript 49 → 91 lines, `PHONE` (F8) |
| The foreman plugin kept off | Yes with `enabledPlugins {"foreman@foreman": false}` in the carried settings; not when the guard lives in the plugin (b) | (a) → no `foreman:*` skill, no `session-context` (F2); (b) → `foreman:define-task` listed, SessionStart ran in the agent's session (F9) |
| An agent in the main checkout (planning, parent) | Needs a folder of its own: two agents sharing one file swap tasks (row 1), and a file in the main checkout reaches every worktree. A detached worktree per agent works, and Orca accepts it by `path:` | `git worktree add --detach …/agents/fn-903-planning main` (and `fn-904-parent`), two chats at once → `TASK=FN-903 AGENT=planning` and `TASK=FN-904 AGENT=parent` (F3) |

Foreman's own side, which Q1 also needs. **Not** through `orca agent launch` chats: `orca chat` has only `rename`; `terminal send` →
`terminal_handle_stale`; `orchestration send` without `--from` → `no_active_sender_terminal`; `terminal close --all` → `closed: 0` (F7);
Orca's internal `agentSession.send`/`close` work, but the CLI is kept out of them (`agent-session.rename-only.v1`, F7). **Through Orca's
orchestration verbs, yes** (F16), from an Orca terminal foreman keeps as coordinator (`orca terminal create … --command "sleep …"`, its
handle `H`, a Run from `run-create --from H`): **launch** `orca orchestration worker-start --run R --from H --worktree path:WT --agent claude
--model M --effort E --keep-open --spec …` → `"mode": "structured"`, and the chat read (a)'s file (`TASK=FN-2`, `BLOCKME denied`); **send**
`orchestration send --from H --to dispatch:D` → transcript 51 → 70 lines, answered; **state** the `worktree ps` row whose `paneKey` is
`worker-show`'s `assigneePaneKey`, `done` / `blocked` (F5, F16); **stop** `worker-stop --dispatch D` → `closed_agent_terminal`, or
`worker-release` once the agent reported itself done (`worker-stop` then answers `alreadySettled`). Its terms: `worker-start` has no
`--surface`: it makes a chat only when Orca's Chat|CLI choice for claude is Chat (a fresh Orca's is `terminal` → `"mode": "terminal"`);
Orca puts its own worker instructions before the brief («You are a dispatched worker … Reach it with the `orca orchestration` …») and
wraps each message («From: TERM_… Subject: …»); a send to a live chat returns only a `messageId`, and only a send after an Orca restart returns a new `dispatchId`. **Restart** (on a headless Orca): chats die with Orca
(tabs did not, E7); the next message makes Orca run `claude --resume=<same id>` with the settings read again (F8, F16). No trust dialog
showed in folders claude had never seen (F2).

**The alternatives on the chat view** (each run end to end with `P`; bypassPermissions and the phone message come from Orca, not from the
alternative: every alternative's `touch probe.txt` ran under `full-access` (F2, F9, F10, F12, F15) and the phone path of F8 is the same for all):

| Alternative | Works? | Command → decisive output |
|---|---|---|
| (a) the worktree's `.claude/settings.local.json` | Works for settings, guard, env, plugin off; bypass only through Orca's setting; main-checkout agents need their own folder | F2, F3, F5, F16 above |
| (b) the guard as a hook of the installed foreman plugin, told by the folder | Does not: the hook acts only in agent folders and its SessionStart can give the agent's commands their env, but the guard hook gets no task (it must find it from the folder) and the plugin's skills and SessionStart reach the agent | scratch config with a plugin copy: agent folder → `plugin-hook ACT task=FN-1 env_TASK=`, `BLOCKME denied`; owner's folder → `PASS`, `BLOCKME` ran; both list `foreman:define-task` (F9); `$CLAUDE_ENV_FILE` → `task=FN-1`, guard hook `env_TASK=` (F15) |
| (c) a Claude config folder for agents (`CLAUDE_CONFIG_DIR`, managed account) | Does not: one config for every chat Orca starts, so no per-agent `FOREMAN_TASK`; transcripts leave `~/.claude/projects` | Orca run with `CLAUDE_CONFIG_DIR=…/cfgc` → `TASK= HOME_=…/S/home`, `BLOCKME denied`, transcript in `cfgc/projects/…` (F10). `orca account add --agent claude` → «could not be set up» without `claude login` in a terminal; `account select` picks the account of all new launches (F10) |
| (d) an `orca.yaml` setup script | Does not: Orca runs it only for worktrees it makes, never for foreman's | git-made `fn-3` + chat → no setup log, no `.claude`; `orca worktree create --setup run` → `setup ran in ~/orca/workspaces/repo/d2`, `TASK=FN-909` (F11) |
| (e) Orca's own settings | Command override: works, with a wrapper that adds `--settings <file foreman keeps for that folder>`; env and arguments: host-wide, arguments not passed to chats; `agent hooks`: only Orca's status hooks; permission prompts: the host setting of F5 | `mutateAgentLaunch {type: command}` + env `FOREMAN_TASK=FN-907` → wrapper ran with `FOREMAN_TASK=FN-907`, no `--append-system-prompt`; wrapper with `<home>/state/orca-settings/<cwd hash>.json` → `task=FN-908`, `BLOCKME denied`, no foreman skill (F12) |

| Alternative | What foreman must do | What it changes for the owner's own sessions | Risks |
|---|---|---|---|
| (a) | write the file in each agent's folder; a detached worktree per main-checkout agent; never a file in a main checkout; read the session id from a SessionStart hook | his own sessions in an agent's folder get the guard, env and plugin off | an agent edits its file and the guard is gone (F4: `mv` → next `BLOCKME` ran; the `--settings` flag of D1 falls the same way to a local `disableAllHooks`); a main-checkout file leaks to all worktrees (F3); the file stays out of git only by the owner's global ignore (F3) |
| (b) | a PreToolUse hook in the plugin mapping folder → task; a SessionStart hook writing `$CLAUDE_ENV_FILE` | a hook process on every tool call of every session | the guard's task comes from a folder map; skills and SessionStart reach agents |
| (c) | a config folder with the guard; Orca started with it or a managed account selected | all his Orca chats and tabs run with the agents' config | no per-agent task; `costs.files_of` reads `~/.claude/projects` |
| (d) | let Orca make the worktrees under `~/orca/workspaces`, an `orca.yaml` committed in each repo | every worktree he makes in Orca runs it | does not run for foreman's worktrees |
| (e) | set Orca's command override for claude (a host setting) to a wrapper named `claude`; keep one settings file per agent folder outside it | every claude Orca starts passes through foreman's wrapper (it passes through when no file) | a wrapper in the path of all Orca agents; Orca's naming call gets the settings too (F12) |

**Recommendation on Q1: the chat view, launched with `orchestration worker-start` and carried by (a).** Every answer above works for it
through Orca's CLI (F2-F6, F16, F17), the owner prefers it, and a chat needs no trust dialog. Its terms, which D1 does not have:
the owner's Orca keeps Claude on Chat and chats on `full-access` (FN-204's `worker-start` answered `mode: structured` and its claude had
`--dangerously-skip-permissions`, E2) (inferred: E2's run was on the owner's Orca); Orca's worker instructions sit before foreman's brief;
and the phone was seen on an ordinary chat-view session, not yet on one started by `worker-start` («seen acting», subtask 3). If the owner will not keep those two settings, D1 stands as written.

FN-216's launch must then (D1-D10 themselves are not edited here):
- D1 → per project, one coordinator tab `orca terminal create --worktree path:<repo> --command "sleep …"` and a Run `run-create --from H`; per
  agent, write (a) (the dict `sessions.settings` builds, plus `permissions.defaultMode: bypassPermissions` and a SessionStart hook that records
  `session_id`), then `worker-start --run R --from H --worktree path:<folder> --agent claude --model M --effort E --keep-open --spec-file <brief>` (run with `--spec`; `--spec-file` not run);
  `"mode"` other than `structured` → `RunnerError` (no fallback, D10); planning, mechanic and parent agents get a detached worktree each
  (`git worktree add --detach <home>/…/<task>-<role> <main>`), never the main checkout (F3);
- D3 → the row whose `paneKey` is `worker-show`'s `assigneePaneKey`; `blocked` → WAITING;
- D4 → `orchestration send --from H --to dispatch:D --subject … --body MSG`, `D` the dispatch id kept from `worker-start`; a send to a live chat
  returns only `messageId`; a send after an Orca restart returns a new `dispatchId`, which replaces `D` (F16);
- D5 → `worker-stop --dispatch D`, and `worker-release` when it answers `alreadySettled`;
- D6 → not needed for chats; D7 → a restart ends every chat until its next message (F8, F16);
- never write `.claude/settings.local.json` into a repo's main checkout, and leave Orca's host settings (command override, agent env) alone (F3, F12).

### What the claude runner does that Orca cannot

| Claude runner (today) | Orca | What foreman does |
|---|---|---|
| `claude agents --json --all` keeps ended sessions | lists live terminals only | ended = the terminal absent after `terminal wait --for exit` or from `terminal list`; the backlog's agent gains `terminal` and `pane` (today it keeps `short` and `session`, `launch.py:69`), written from the port's `SessionRef`; foreman never asks Orca about a session it forgot |
| resume under the same id needs stop, wait, then a flagless `claude --bg --resume` | a live idle tab takes `terminal send` | an ended one gets a new tab `exec claude --resume SID …flags… "message"`, which kept the id and took the flags (E2) |
| `--bg` skips the trust dialog | a tab shows it for every folder claude never saw (E2) | D6 |
| sessions run with Orca closed | tabs need Orca's runtime to be read or written, though a restarted server left the process running (E7) | D7 |
| the state comes from claude itself | the state comes only from Orca's hooks in `~/.claude/settings.json` (inferred: the owner's file holds them for every event and `ps` showed no other source) | D8 |
| `busy` = `status idle` of a `working` row | Orca keeps `working` ~30 s while a background task runs, and `tui-idle` is no guide (E4) | the adapter trusts `agents[].state` only |
| the short id for `claude attach SHORT` | no short id | `agent.short` = the first 8 characters of the session id foreman chose (`--session-id`), so `fill_sessions` has nothing to fill |
| `login.child_env()` strips `CLAUDE_CODE_OAUTH_TOKEN` | the tab's shell inherits Orca's environment (inferred: a pty child's) | `unset CLAUDE_CODE_OAUTH_TOKEN;` before `exec` in the tab's command (D1; `env -u … exec claude` fails: `env: exec: No such file or directory`; `exec env -u … claude` hides claude from Orca's delivery report, E8, E9) |

### The port

From what foreman uses today in `services/claude/sessions.py` (`command` :68, `start` :102, `resume` :108, `listing` :134, `state` :148,
`alive` :154, `busy` :160, `stop` :172, `find` :97, `SessionError` :31) and its callers (`services/agents/launch.py`, `running.py`,
`steps/mechanic.py`, `parents.py`, `reviewing.py`, `talk.py`, `services/delivery/merge.py`, `loop/queue.py`, `tick.py`,
`ui/panel/views.py`, `ui/cli/ops.py`). Two real implementations, so a port is allowed. Package `foreman/providers/`, module
`runner.py` (layer ports); adapters `providers/claude/` (today's `services/claude/sessions.py` and `login.py`, moved) and
`providers/orca/`; the registry hands it out (`registry.runner(p)`), as it does `registry.repo(p)`.

```
class State(Enum): WORKING, WAITING, IDLE, ENDED, GONE            # today: working|blocked, —, done, stopped, gone
@dataclass(frozen=True) class Agent: tid, name, model, effort, title                      # unchanged
@dataclass(frozen=True) class SessionRef:                                                 # what the backlog's agent keeps of its session
    short: str; session: str | None                                                       # orca: session known at once; claude: from the listing, maybe None
    terminal: str | None = None; pane: str | None = None                                  # orca: the tab's handle and paneKey; claude: None
    started: str | None = None                                                            # the agent's `started` (launch, each send): D3's start limit
class Rows(Protocol):                                                                     # one snapshot per tick; replaces dict rows
    def state(self, ref: SessionRef | None) -> State
    def busy(self, ref: SessionRef | None) -> bool                                        # WORKING or WAITING
    def alive(self, ref: SessionRef | None) -> bool
    def find(self, short: str) -> str | None
    def short(self, session: str) -> str | None                                          # claude: the listing's id (today merge.py:117); orca: session[:8]
class Runner(Protocol):
    def start(self, agent: Agent, brief: str, cwd: Path) -> SessionRef
    def send(self, ref: SessionRef, message: str, cwd: Path) -> SessionRef                # today's resume; orca: a new tab when ended
    def rows(self) -> Rows | None                                                         # None: unreadable (today {}); callers do nothing, as today
    def stop(self, ref: SessionRef) -> None
class RunnerError(Exception); class RunnerUnavailable(RunnerError)                        # SessionError becomes RunnerError
```

`stop_live` and `settings` stay shared (they use only the above). No dict crosses the port: where a caller reads
`rows.get(sid).get("id")` (`merge.py:117`) or `row.get("status")`, it gets `Rows`. The backlog's agent keeps the five fields of
`SessionRef` (`started` as today). The task's `sessions` list keeps its shape (session ids, `launch.py:87`), so its readers `ui/panel/views.py:83`,
`steps/metrics.py:79` and `merge.py:117` read it as today; next to it, as `session_kinds` is, a new map `session_tabs`
(session id → `[terminal, pane]`) is written by `launch.record_session` (`launch.py:85`) from the `SessionRef` `start` or `send` returned,
only when its `terminal` is set (never for claude); the adapter writes nothing to the backlog; `record_session` takes that `SessionRef` instead of the id. `merge.stop_all` builds each old session's `SessionRef` from the id,
`Rows.short(id)` and its `session_tabs` entry; an id with none (every entry written before, and every claude one) has `terminal` and `pane`
empty, which the claude adapter never reads and the orca adapter reads as GONE; an id with an entry gets its tab closed, a replaced
review's too.

### Questions for the owner

- **Q1** The agent's tab is Claude Code's terminal screen, not Orca's chat view: the chat (structured) worker cannot carry the guard
  hook, foreman's environment or a session id (E2). Is a terminal tab acceptable, also from Orca on the phone? Recommend yes: without
  it the guard hook is lost; «seen acting» (subtask 3) checks the phone.
  Superseded: see «Chat view or terminal tab (FN-218)»; the recommendation is now the chat view.
- **Q2** May foreman press «Yes, I trust this folder» for its own worktrees (the click the owner would make; claude records the folder
  in `~/.claude.json`)? Recommend yes, only inside the project's repos and worktrees folder; otherwise every new agent waits until
  the owner clicks.
- **Q3** When Orca is not running, may foreman start it (`orca open` opens a window on the owner's screen)? Recommend no: launches wait
  with one event and a health warning; agents already running keep running (E7).
- **Q4** May foreman add the project's repos to Orca and show their external worktrees (the sidebar gains each project)? Recommend
  yes, automatic, only the repos in `foreman.toml`; it is what makes the tabs visible.
- **Q5** Add «Runner» to `GLOSSARY.md`: «The way a project's agents run, chosen by `[agents] runner` (`claude` or `orca`); the port
  `Runner` has one adapter for each.» Avoid: engine, backend. Recommend yes (the owner's call).

### The reference workspace

An Orca-driven setup on this Mac, read at a fixed commit; `ref:` paths are relative to its package.

| Question | What it does | Foreman |
|---|---|---|
| launch | `orchestration worker-start --keep-open --worktree path: --agent claude` with only `--model/--effort` (`ref:services/orca/workers.py:141-145`, `ref:steps/run/start.py:40-45`); permission mode and settings are Orca's own | differs: guard hook, env, session id (D1) |
| its own run | an Orca terminal tab made by `terminal_create` (`ref:entry/trigger.py:76-86`; `ref:services/orca/client.py:41-44`) | differs: the loop is launchd, no terminal |
| the binary, Orca reachable | `find_binary` (`ref:services/orca/call.py:15-21`), `reachable()` (`ref:services/orca/client.py:24-27`), gate before a run (`ref:entry/trigger.py:78`) | as is (D7) |
| a failed call | a `Failure` value with Orca's reason trimmed to 300 characters (`ref:services/orca/call.py:24-49`) | adapts: `RunnerError(reason)` |
| send | `orchestration send --to dispatch:` (`ref:services/orca/workers.py:125-135`); a not-ready agent is retried quietly, a stuck one flagged (`ref:services/orca/workers.py:13-33`, commit ed5a762); a follow-up gets a new dispatch id, so the newest row per worktree is read (`ref:steps/run/closing/topic.py:30-39`) | adapts: retry next tick (D4); terminals have stable handles |
| states | `worker-show --full`: `stage.activity` `done` → idle, `liveness.verdict` (`ref:services/orca/workers.py:161-179`); Orca keeps `working` with background work | adapts: `worktree ps` `state` (D3) |
| closing | an idle agent released, an open task stopped, a fenced one's tabs closed, an agent silent 15 min is quiet (`ref:steps/run/closing/topic.py:50-117`, `ref:core/router_run.py:129-136`; commits 905f1b9, 686b2bf, 1f62905) | adapts: every tab is foreman's, so `terminal close --tab` always (D5); no fence, no quiet rule (`stop_live` is unconditional) |
| repo, worktree and titles | repo by origin key (`ref:services/orca/worktrees.py:66-83`); Orca creates the worktree in `worker-start new-top-level` (`ref:services/orca/workers.py:147-159`); display name (`ref:services/orca/worktrees.py:103-105`); nesting (`ref:services/orca/worktrees.py:123-125`) | differs: foreman creates worktrees with git and Orca adopts `path:` (D2); display name as is (D9); nesting left out |
| one agent per task | a launch never starts a second agent where one ever started (`ref:steps/run/start.py:16-25`) | adapts: before `start`, a live terminal whose handle the task's agent keeps (in `terminal list`) is adopted, never duplicated; never matched by title |
| Orca closed or restarted | a run does nothing when Orca is not reachable (`ref:entry/trigger.py:76-80`); nothing about a restart | as is for closed (D7); restart answered by E7 |

Left out: other agents than Claude; nesting sub-agent tabs under a parent agent's worktree; Orca's chat view; a quiet rule for
agents Orca keeps `working`.

### Next subtasks

Drafted in `<home>/reports/FN-204.md`, to be filed under FN-203 in triage:
1. The runner port, with today's claude adapter behind it (after FN-153 and FN-154).
2. The Orca adapter (after 1; gated by Q1-Q5).
3. Seen acting in a real project (after 2).

## Shared resources

`foreman/services/lock.py`. A resource is declared in foreman.toml (`[resources.NAME]`, optional
`slots = [...]` and `max_minutes`, default 20). `foreman lock NAME run "what" command…` waits its turn, runs the command
holding the resource (with `FOREMAN_SLOT`), stops it at the time limit and always releases it; `foreman lock NAME status`.

- Fair queue: first come, first served; a holder or waiter whose process died is cleaned up whenever anyone looks.
- Slots: one holder per slot, one queue.
- The state is common to the machine (`~/.local/state/foreman/locks/NAME/`), so projects that declare the same name share
  it; every use is logged (`uses.jsonl`: who, task, what, waited, held, exit code, timeout) for the metrics
  (`lock_wait_min` per task) and the panel (System: who holds each resource and who waits).
- The responsible agent's brief lists the project's resources and the rule. No hook enforces it.

## The guard

A reminder, not a cage (`foreman/core/oversight/guard.py`). It runs only in agent sessions (the hook comes in `--settings`), looks at
the program each shell segment runs (never at words inside strings or messages), and denies a handful of actions
with a reason that says: do not look for another way; `foreman ask --action NAME "what and why"`, end your turn. The
owner allows it once from the panel (`foreman allow QID`); the loop tells its agent; it passes once.

| Action | Recognised by |
|---|---|
| `publish` | `npm/pnpm/yarn/cargo/uv/poetry publish`, `twine upload`, `docker push`, `gh release/pr/repo create|merge|edit|delete|upload` |
| `push-main` | `git push` to the main branch (named, `HEAD` while on it, or bare while on it), or with `--force`/`--mirror`/`--all` or a ref written with a leading `+` |
| `push-evidence` | `git push` to the `foreman-evidence` branch (only the loop writes it) |
| `delete` | `rm`/`rmdir`/`unlink`/`find -delete` of a path outside its worktree and temp dirs; `git branch -d/-D` of another branch; `git worktree remove`; `git push --delete` |
| `config` | Edit/Write of the project's `foreman.toml`; a shell command that writes it (`>`, `tee`, `sed -i`, `mv`, `cp`, `rm`) |
| owner's own | `[guard] ask = [{name, pattern}]` in foreman.toml: a regex on a shell segment |

Everything else runs. A way around the list is not a hole to close: the review and the owner's accept before
the merge are the safety net. Each stop is logged (`state/guard.jsonl`) and counted in the metrics.

## Merge: risk and autonomy

Every delivered branch gets a risk score:

- **impact**, from what the change touches: high = paths in `[risk.high]`; medium = paths in `[risk.medium]` and deleted
  files; low = the rest. A review never lowers it.
- **doubt**, from the evidence: high = no changes, tests fail, CI fails; medium = no test command, or a large change: over `[risk] large_lines` (400) changed lines, not counting tests, generated and lock files; size never makes a change high by itself.
- Risk is the higher of the two. It shows up to 3 reasons, high ones first, each with its source (`[diff]`, `[tests]`,
  `[ci]`, `[git]`, `[review]`). Size is only the large-change reason; file counts are not a reason.
- Open review findings are a gate, not a level: they go back to the responsible agent and add a `[review]` reason, but never change the risk.
  After the last round, an open finding never merges alone, at any autonomy or risk (`decide.after_check`).
- A conflict with main is not a level either: it is the first reason (`conflicts with main: it goes back to its agent
  when accepted [git]`), and it goes back to its agent only when the owner accepts or the autonomy would merge it.

The project's `[merge] autonomy` decides what merges without the owner: `manual` (nothing; the default),
`low`, `medium`. `high` always waits. Where the provider `has_ci` (github yes, local no), autonomy merges only on a green CI. The owner accepts in the panel (`foreman accept`), or rejects it with a note
(`foreman reject`, counted as rework).

### Provider, accepted → merged

The merge goes through a *provider* module (`foreman/providers/`), one per platform; the rest of fm does not know which.
Interface: `pushed` (is the branch on the platform at its tip?) and `push` (put it there: never forced, never main, 20 s; any failure is `Transient` with the platform's error; a no-op locally), `publish` (open the change), `ready`, `diff` (its unified diff; empty locally), `review` (post the verdict as one PR review with its inline comments; no-op locally), `body` / `describe` (read and set the description and title; no-ops locally), `merge(change, strategy)` (the only merge
in fm: a future queue is just another strategy) and `state` (merged? closed?). Operations take a `Change` and return `Published` / `ChangeState`: no dict crosses the port. Steps:

1. **accepted**: `foreman accept` (or autonomy) only records `accepted` on the task (it stays `in review`).
2. **merge**: the loop's queue step asks the provider to merge once, and stores the merge reference.
3. **merged**: the task closes (`done`), and its worktree and branch go, only when `state` shows the merge in main,
   not when the command ran.

Retry policy, one place (`loop.guarded`, deciding with `decide.retry_step`; every provider call: push, PR, CI, merge): a `Transient` failure is retried next tick and counted per task (`retry_count`, next to `retry_error`); the 3rd in a row blocks the task with the last error; a success resets both.

Config: `[merge] provider = "auto"` (default: `github` if the repo's `remote` is on github.com (always `local` with `remote = "none"`), else `local`; or force
`local` | `github`) and `strategy = "squash"` (default), `"merge"` (`--no-ff` locally) or `"rebase"` (github only). An
unknown value is a clear config error (at accept, and in the merge step). A merge never publishes: no tag, no release.

### GitHub provider: every task is a pull request

The responsible agent only commits; the **loop** pushes the task branches (`provider.push`: `git push <remote> refs/heads/<b>:refs/heads/<b>`, `<remote>` being the repo's `remote`,
never forced, never main, 20 s timeout; a failure is `Transient` with git's error) and, never an agent, opens, readies,
comments on and merges PRs through `gh` (the guard keeps stopping `gh pr` for agents). `[merge] pr = "draft"`
(default): a task in progress has its commits pushed and gets a **draft PR** as soon as it has its first commit; `pr = "done"`: no PR until
`foreman done`. In review, after the hunter and the reviewer: the branch is pushed at its tip if it is not yet, the PR exists, and the loop waits while CI is pending. Then the check runs: **failing CI is high risk**, and
with no `[merge] test` the PR's CI **replaces the local test**. Then the draft is marked **ready** and a pull request review
(event COMMENT) carries the verdict (decision, risk and its reasons, checks); each finding inside the diff is also a comment on its line. Accepted → `gh pr merge` with the configured type; the task
closes only when the PR is seen `MERGED` (a PR closed unmerged blocks it). Autonomy `low`/`medium` merges **only with
green CI** (no CI, pending or failing waits for the owner). After the merge the main checkout is fast-forwarded from
the repo's `remote` when it is clean on main (fetch only, never a push). The images a report names (`![what](x.png)`, bare file
names saved in `reports/ID/`) go, by git plumbing and never forced, to `ID/` on the orphan branch `foreman-evidence`, the
only push fm makes besides the task branch (agents may not: guard `push-evidence`); the PR body links them with
`?raw=true` blob URLs and the panel serves them at `/evidence`. Conflicts with main go back to the responsible agent once accepted, also when the merge itself is refused for a conflict after acceptance: the acceptance is carried over as `resolving`, and the resolved tip merges without a new accept unless its review leaves findings open or its checks fail. Out of
scope: reading approvals or comments from GitHub, merge queues, other platforms.

## Recurring tasks

`[[recurring]]` tables in `foreman.toml` (strict: `foreman/core/project/recurring_table.py`; any other key, both triggers, neither or a bad interval is refused at load, naming the table and the key). Each has `name`, `title`, one trigger, and the fields `foreman add` takes (`goal`, `criteria`, `detail`, `model`, `effort`, `type`), plus `triage` (true by default).
- Trigger: `every_merges = N` (N merged tasks) or `every = "12h" | "7d" | "2w"` (plain durations: h, d, w, at most 100 years; no cron).
- Each tick (the `sources` step, which calls `foreman/steps/schedule.py` over the pure rules of `foreman/core/tasks/recurring.py`) an entry that is due files one task: in `triage` when `triage = true` (the owner's Go), `backlog` when false and it has criteria. Its `source` is `recurring:NAME`, so `backlog.add` files none while one is open, and the mark survives the entry's removal (tasks it filed stay as they are). Its detail ends with what it covers: the merged tasks since its last run (ids, titles) or the period.
- Anchor: the count or period starts when the entry's last task closed (done, canceled or duplicate), else when fm first saw the entry (`state/recurring.json`, the one store). A time trigger that fell due while fm was off files once. Merges are `done` tasks not filed by an entry and not parents; their closing time is that of the `→ done` event.
- `foreman list`, `foreman show` and the panel's data mark an entry's task (`recurring`: the name); `foreman health` and the panel's system view list each entry with merges left or the next date. A project with no entries sees none of it. A `lens` entry files a chain: see Improvement chains.

## Improvement chains (defined in FN-207; not built yet)

This section defines how fm improves itself one finding at a time; the subtasks listed in FN-207's report build it. Until
then the `design-audit` entry files one task every 10 merges (`<home>/foreman.toml:40-42`). Paths are under `plugins/foreman/`; a `file:line` names
today's code.

A **lens** is a question asked of fm, with the evidence that proves a finding. A **chain** is a run of one lens: the
parent a lens entry files and its **steps**, its subtasks, one at a time, until its parent agent closes it. The **lens
report** carries a lens from one chain to the next. Nothing new is stored but `chain` on the parent: its steps are its
subtasks (`foreman/core/tasks/subtasks.py:6`).

### The entry and its trigger

```toml
[[recurring]]
name = "rules"
title = "Lens rules: do CLAUDE.md and the configs hold?"
lens = "rules"
max_steps = 5        # the cap of steps per chain
max_lines = 150      # the size budget of each step
every_merges = 10
type = "change"      # every step's type
goal = "…"           # what each step meets, filed unchanged on every step
criteria = "…"
detail = "…"         # the lens's question and the evidence of a finding
```

`lens`, `max_steps`, `max_lines` and `criteria` come together, else the entry is refused at load naming the key (as the other keys
today, `foreman/core/project/recurring_table.py:11-12,57-66`). A chain starts on its own: a lens entry files in backlog,
`triage` defaults to false for it and `triage = true` is refused; `criteria` is required because a task without it is
filed in triage (`foreman/core/tasks/backlog.py:149-150`). The count starts when the previous chain closed
(`foreman/core/tasks/recurring.py:48`) and no chain is filed while one is open (`:47,55`, `backlog.py:152`).
**Steps never count as merges for any entry**: today a merge is a done task not filed by an entry and not a parent
(`recurring.py:52`), so a step would count; the rule becomes «and not the subtask of a task an entry filed».

### The chain parent and its agent

A due lens entry files its parent in backlog with `chain = {lens, max_steps, max_lines}` (`schedule._fields`,
`foreman/steps/schedule.py:34-44`). Today a parent is a task with subtasks: it is never planned (`foreman/loop/launch.py:25`),
has no type (`foreman/core/tasks/tasktypes.py:20`) and gets its agent only once it has a subtask (`foreman/steps/parents.py:72`).
A new pure module `foreman/core/tasks/chains.py` (area `tasks`) has `is_parent(tasks, i)`: it has subtasks or `chain`;
those three sites use it, so a chain parent gets its agent before its first step. The agent's kind stays `parent`, with
planning's model and effort (`parents.py:18`); `parents.launch` renders `briefs/chain.md` instead of `parent.md`
(`parents.py:21`), with `extra` = lens, report path, `max_steps`, `max_lines` (`foreman/services/agents/brief.py:83,87`).
`parent.md` makes every Go, add and the close wait for the owner's yes (`foreman/briefs/parent.md:18,24,31`); the chain
agent waits for none. Its steps enter backlog (today an agent's `foreman add --parent` is always triage,
`foreman/ui/cli/owner.py:33`); it closes the chain with `foreman done` (`foreman/ui/cli/agent.py:27-29`), done also with
no step (today refused, `parents.py:76-84`, `subtasks.py:32-36`); it `foreman ask`s the owner only at the cap. A parent
with an agent is not closed by its subtasks (`foreman/services/delivery/merge.py:130`): a chain ends only by its agent.

### The audit and a step

The audit is the agent's first act: it reads the lens report (writes it with its three headings if missing; the first
`rules` report starts from `<home>/plans/FN-205-findings.md`), checks each Frontier item against main, adds what is new,
drops what no longer holds, then files the first step, or closes the chain «enough for now» when no item has evidence.
A step is `foreman add "title" --parent ID --detail "the finding and its evidence"`: on a chain parent fm fills goal,
criteria, model, effort and type from the parent (today they come from the flags, `owner.py:39-52`), so the agent
writes only title and detail and no agent writes criteria. A step fixes exactly one finding within `max_lines` changed
lines (not counting tests, generated and lock files, as `large_lines`); its report shows the evidence before and after;
it writes its History line in the lens report. It is planned, built, reviewed and accepted as any task (merging without
the owner is FN-112's). When it closes the agent is told (`parents.py:42-58`) with a chain wording of `subtasks.notice`
(`subtasks.py:62-71`): «file the next step or close the chain»; it reads the report, rewrites the Frontier, and files
the next step or closes.

### The lens report

`<home>/reports/lens-NAME.md`, one per lens, kept across chains and never re-created; task reports are `ID.md`
(`brief.py:17`), so the names never meet. One writer per section:

| Section | Writer | What |
|---|---|---|
| `## Direction` | the owner only, whenever he wants; agents only read it | where the lens should look, what it should leave |
| `## Frontier` | the chain agent, rewritten after every step | the next steps, each with its evidence and its size |
| `## History` | each step; the chain agent for a canceled step | done: step, PR, what it learned, what it would do next; rejected or canceled: the kind and the owner's reason, read before proposing so a rejected kind is not proposed again |

### Lenses

| Lens | Question | Evidence that makes a finding |
|---|---|---|
| `rules` (first) | do CLAUDE.md and the configs hold? | a deliberate violation that passes `make check`: its file:line and the command that passes |
| stale or duplicated prose | does a name, path or command in prose still exist; is a rule stated once? | the grep or command that finds nothing, or the two places |
| simplification | unused code, duplication, functions near the limits? | the metric before and after: lines, copies, longest function |

A finding without its evidence is not filed. Later, not now: tests that do not protect (a mutation not caught),
redundant tests, brittle tests.

### Brakes

| Brake | Number | Enforced by |
|---|---|---|
| one open step per chain; the next only after the previous merged | 1 | code: `chains.refusal` refuses `--parent` filing while a step is open |
| cap of steps per chain | 5 | code: the sixth is refused with «ask the owner to renew»; renewal is the owner's `foreman edit PARENT --max-steps N`, only on a chain parent and only from his shell (an agent may not edit a parent, `owner.py:63`); a chain parent is the one task with an agent that `foreman edit` accepts (an exception to «never one with an agent» in The states of a task), and `--max-steps` joins the edit fields (`foreman/core/tasks/editing.py:8`) |
| no evidence, no next step | | brief and review: a Frontier item has its evidence or is dropped; a step whose after-evidence does not show the fix is rejected |
| a canceled step ends the chain | | code: no further step; the agent writes the owner's reason in History and closes |
| size budget per step | 150 changed lines | criteria and review; `large_lines` (400, `foreman/core/project/config.py:44`) stays the risk mark |
| one chain per entry at a time | 1 | code: `backlog.add` files none while the entry's task is open (`backlog.py:152`) |
| steps never count as merges | | code: the merges rule above |
| how often a chain starts | every 10 merges | the entry's `every_merges`, as design-audit (owner, 2026-11-15) |

### Seen in list, show and health

`foreman list`: the parent row adds `[chain: rules, step 2 of 5]` after `[recurring: NAME]` (`foreman/ui/cli/view.py:23`),
its steps indented under it as every subtask (`view.py:16,21`). `foreman show PARENT`: the stored `chain` gains `step`
(steps filed) beside `subtasks` (`view.py:36-37`). `foreman health` (`schedule.line`, `schedule.py:70-75`, printed by
`foreman/ui/cli/ops.py:28-29`): `recurring rules: every 10 merges; waits for FN-300 to close (chain step 2 of 5, FN-305
open)`; the same rows feed the panel's system data (`foreman/ui/panel/views.py:103,113`); the panel's UI is untouched.

### Rollout and left out

The first lens entry runs beside design-audit; the lens entries replace design-audit once the first chain has closed and
the owner has looked at it (his edit of foreman.toml, a guarded `config` action). Left out on purpose: lenses on tests,
merging steps without the owner, a second open chain per lens, per-step models, a port or a store for chains.

## Several repos (defined in FN-148; not built yet)

This section defines how a project with several repos runs its tasks; FN-144 subtask 4 builds it in the subtasks
listed in FN-148's report. Until then fm reads only `path` and `main` of each repo table and runs every task in the
first repo (FN-145). Two rules carry it all: **one task = one PR in one repo**, and **the project's repos stay
untouched** (their own git sees no fm or Claude file).

### Shape of foreman.toml

```toml
[project]
name = "shop"
prefix = "SH"
umbrella = "~/src/shop"              # optional: the folder that holds the repos
worktrees = "~/src/shop/worktrees"   # outside every repo; it may sit in the umbrella

[merge]                              # defaults for every repo
autonomy = "manual"

[repos.app]
path = "~/src/shop/app"
post_create = ["cp -c -R {main}/deps {wt}/", "cp {main}/.env {wt}/"]   # copy dependencies

[repos.app.merge]
strategy = "squash"
test = "make lint test"

[repos.app.risk.high]                # replaces the default high table: list again what stays high
release = ["app.config", ".github/workflows/"]
secrets = ["*secret*", "*.env"]

[repos.api]
path = "~/src/shop/api"

[repos.api.merge]
strategy = "merge"                   # no test: the PR's CI stands in

[repos.api.risk.high]                # replaces the default high table: list again what stays high
schema = ["migrations/"]
config = ["foreman.toml", ".github/", "*secret*", "*.env"]

[repos.site]
path = "~/src/shop/site"
publish_on_merge = true              # its host deploys main

[repos.site.merge]
strategy = "merge"
test = "make build"

[repos.emails]
path = "~/src/shop/emails"
remote = "none"                      # no remote: the local provider

[repos.emails.merge]
test = "make build"
```

| Key | Level | Default | Meaning |
|---|---|---|---|
| `path` | `[repos.K]` | required | the repo's main checkout |
| `main` | `[repos.K]` | `"main"` | its main branch |
| `remote` | `[repos.K]` | `"origin"` | where task branches are pushed; `"none"`: no remote, provider `local` |
| `worktrees` | `[repos.K]` | `[project] worktrees` | the folder of this repo's task worktrees |
| `post_create` | `[repos.K]` | `[]` | shell commands run once in each new worktree |
| `publish_on_merge` | `[repos.K]` | `false` | merging into its main publishes (a host deploys it) |
| `provider`, `strategy`, `pr`, `test`, `autonomy` | `[repos.K.merge]` | as `[merge]` | same values and meaning as `[merge]` |
| `large_lines`, `[repos.K.risk.high]`, `[repos.K.risk.medium]` | `[repos.K.risk]` | as `[risk]` | as `[risk]`; patterns relative to the repo's root, as diff paths are |

- Project-wide only, never in a repo table: `[project]` (`name`, `prefix`, `branch_prefix`, `worktrees`, `umbrella`,
  `foreman`), `[agents]` (slots, models, `claude`, settings), `[guard]`, `[health]`, `[resources.*]`. `[merge]` and
  `[risk]` stay as the defaults for every repo.
- **A setting given at both levels: the repo's wins, key by key; absent there, the project's; absent there, fm's
  default.** A `high` or `medium` table in a repo replaces the project's table of that level whole, never label by
  label (as a project table replaces fm's default one today). So a repo's `high` table also drops fm's default one
  (`foreman.toml`, `.github/`, `*secret*`, `*.env`): a repo that wants those paths high lists them again.
- Repo tables are strict: a project-wide key or an unknown key in one is a config error naming table and key
  (`foreman: [repos.api] has no setting 'slots'`). No other table changes.
- `path` and `worktrees` are relative to the home, `~` expanded. `[project] repo` alone stays the one-repo form, with
  none of these tables.
- `post_create`: each command runs with `sh -c`, cwd the new worktree, `{main}` (the repo's main checkout) and `{wt}`
  replaced by shell-quoted paths (another placeholder is a config error at load), 300 s each, once per worktree (a
  reused one does not run it again). A failing or timed-out step removes the worktree (and the branch, if this
  launch made it) and blocks the task: `post_create step failed (CMD): LAST 300 CHARS`.
- When built: `foreman/core/project/repos.py` in the core, pure (parse, validate, the view below, the layout check); the commands run in
  `agents.worktree` (a service). No port: nothing varies that `providers/` does not already cover.

### One task, one PR, one repo

- A task's repo is its `repo` key (a `[repos.K]` key). The project hands every step a **view** of itself for that repo:
  `Project.for_task(i)` returns a `Project` whose `[project] repo`/`main`/`worktrees`/`post_create`, `[merge]` and `[risk]`
  are the repo's (`remote` too), merged by the rule above; home, state, agents, guard, resources
  and backlog are the project's. The code that reads `p.repo`, `[merge]` or `[risk]` today does not learn about repos: the loop calls `for_task` once per
  task step. A task with no repo (a task that has subtasks, a loop finding) gets the project as it is, that is the first
  repo.
- Choosing: `foreman add TITLE --repo KEY`; `foreman edit ID --repo KEY` (triage, backlog, ready; on backlog or ready it drops the
  plan and returns to backlog, as criteria do, because a plan is about one repo). With several repos, `foreman add` of a task
  with criteria and `foreman go` refuse one without repo: `foreman: FN-3: choose its repo first (foreman edit FN-3 --repo
  app|api|site|emails)`; in triage it may have none. A task that has subtasks needs no repo (each subtask has its own):
  `foreman go` lets it in without one, and it shows `-` as its repo. An unknown key is refused, naming the keys. A health
  finding about one repo carries it (main's tests, an orphan worktree); the loop's own findings carry none and are never
  let in as they are (the mechanic diagnoses, the owner files the real task).
- Showing: `foreman show` prints `repo`; `foreman list` adds a repo column between status and agent; the panel's `state` and
  `task` data carry `repo` per task, and `state` carries `repos` (the keys).
- **Only with more than one repo.** With one repo the key is never stored, asked, listed or sent; `--repo KEY` with the
  one `[repos.K]` key is accepted and ignored, any other key is refused naming it, and the `[project] repo` form, which has
  no key, refuses every `--repo` (`foreman: this project has one repo`).
- A change across repos is a parent with one subtask per repo (`foreman add --parent`), ordered with `--blocked-by`: the
  dependent subtask launches once the first is merged. No PR links across repos, no merge of several repos at once.
  A planning agent that finds a task needs two repos runs `foreman block` with that split proposed.

### Each moving part

| Part | Answer |
|---|---|
| Worktrees | `<the repo's worktrees folder, else the project's>/<id>`, made from the repo's checkout; the planning agent runs in the task's repo checkout (read only); parent and mechanic agents keep the first repo as cwd |
| Branch names | unchanged, `<branch_prefix><id>`: ids are unique in the project and a task has one repo, so a branch lives in one repo only |
| PR | per repo: provider (`auto` looks at that repo's `remote`; `"none"` is `local`), `pr` mode, push to that `remote`; draft PR, description, review comments and `foreman-evidence` go to the task's repo |
| Merge | per repo `strategy`; the local provider merges into that repo's main checkout; the merged check runs on that repo's main. Merges stay one at a time for the whole project |
| Tests and CI | per repo `test`, in a detached tree of that repo (`state/trees/<id>`); no `test`: the PR's CI stands in and the doubt is medium, as today. Main's tests (health) run per repo that has a test command: `state/ci-<key>.json`, tree `main-<key>`, finding key `ci:<key>:<since>` (one repo keeps `ci.json` and `ci:<since>`) |
| Risk | the repo's tables and `large_lines`. `publish_on_merge` makes the impact high with the reason `merging publishes this repo (KEY)` (a fact of the score, `publishes`), so only the owner's accept merges it. fm still never tags or releases |
| Autonomy | the repo's, else the project's; `high` never merges alone, as today |
| Review | unchanged: hunter and reviewer start in the task's worktree, so they are per repo by their cwd |
| Health checks | orphan worktrees in every repo's worktree list; main's tests per repo; new: a repo path that is not a git checkout, a task whose repo key left foreman.toml (blocked at launch with that reason), fm or Claude files in a repo (below). `foreman health`'s «runs in the first one» note goes |
| Slots | one count for the project (`[agents] slots`), one queue by priority, then ready before backlog, then id, as today, whatever the repo; no per-repo cap. Checks and merged checks stay one at a time for the project |
| Locks | `[resources.*]` stay project-wide and machine-wide: a simulator or a port is shared by every repo; the brief lists them for every task |
| Guard | `push-main` covers every repo's `main`; the rest as today |
| A project with one repo | nothing above shows: the `[project] repo` form is untouched; a project with one `[repos.K]` table has an implicit repo (no key stored) whose `[repos.K]` settings every task gets; same files, names, columns and text, and no layout check |

### The umbrella and files kept out of repos

- The umbrella (`[project] umbrella`) holds repos cloned inside it. It is the owner's own folder: it may be a git repo
  of its own, with its own `.claude/` and `CLAUDE.md`, and may hold the home and the worktrees folder (for instance
  as ignored top-level folders). What stays clean is each repo of `[repos.*]`: no fm or Claude file goes in one.
  Checked at load for a project with more than one `[repos.*]` table (`foreman: the home is inside repo app: …`): the
  home, `state/`, every worktrees folder and the registry file are outside every repo; being inside the umbrella is
  fine. A one-repo project (`[project] repo` or one `[repos.K]` table) is not checked (unchanged).
- fm writes into a repo only what git keeps out of `git status`: the entries in `.git/worktrees/` of task worktrees
  and of the detached test trees (`state/trees/<name>`, in the home), the refs `<branch_prefix><id>` and
  `foreman-evidence` (written by git plumbing, without touching a checkout), the local provider's merges into main, and
  after a GitHub merge a `git fetch` and `merge --ff-only` of main when the checkout is clean on main. Everything
  else lives in the home: agent settings inline on the command line, plans, reports, reviews, the test trees.
- Safety net, because fm cannot prove that a Claude session started in a repo (planning, parent, mechanic) writes
  nothing there: a health check lists, per repo, the untracked and ignored paths (`??` and `!!` lines of `git status
  --porcelain --ignored=matching --untracked-files=all`, so a file a global ignore hides is seen) with a component named
  `.claude`, `CLAUDE.md`, `CLAUDE.local.md` or `foreman.toml`, and files one finding. It looks only inside the repos, never
  at the umbrella. Tracked files are the repo's own and are not reported; a folder ignored whole (dependencies) is not
  looked into. The matching is a pure function over paths, in `foreman/core/project/repos.py`.
- The owner's own chat in the umbrella is the plugin's (FN-144 subtask 2), not this.

### Left out on purpose

- Per-repo slots or locks: one queue is simpler, and the machine is what is shared.
- A check queue per repo (checks in parallel): one at a time is enough for one owner.
- Repos found by their git remote, and `foreman init` for several repos: the owner writes the tables.
- A repo's own brief or agent models: the project's serve every repo.
- A setting for a GitHub repo with no CI: under autonomy `low`/`medium` it never merges alone and waits for the owner,
  as today; a `ci = "none"` key can come if needed.
- Merges across repos at once and PR links across repos: subtasks with `--blocked-by` order them.
- Moving a task to another repo after its plan: `foreman edit --repo` does it and the plan is redone.

## Architecture

Hexagonal-lite: a small core, ports only where something external varies, everything else outside. Not Clean Architecture
nor full DDD; what is shared is a vocabulary: *task* (a unit of work in the backlog), *review round* (one review of a tip), *risk* (low,
medium, high), and *accept* (a record of the owner's decision) is **not** *merge* (the provider's act). The whole vocabulary is in `GLOSSARY.md`; it changes only by the owner's decision.

The plugin is `plugins/foreman/` (`bin/`, `foreman/`, `hooks/`, `panel/dist/`, `.claude-plugin/plugin.json`, `pyproject.toml`) and is all
that is installed; the repo root keeps the development files. Every `foreman/…`, `bin/` and `hooks/` path in this document, in
`CLAUDE.md` and in the briefs is relative to `plugins/foreman/`.

### Layers and packages

Layer = folder: the package says the layer, its sub-packages (areas) say which part of fm. Outside in; a module imports
its own layer and the ones below, never above. Three leaf files stay at `foreman/`'s root, for everyone to import: `proc.py`
(layer proc), `util.py` and `launcher.py` (layer core; `launcher` imports nothing of fm, as it runs alone under
`python3 -c`).

| Layer | Package | Areas → modules | Responsibility |
|---|---|---|---|
| ui | `foreman/ui/` | `main.py` (starts `foreman panel`) · `cli/`: main, owner, ops, agent, view, plugin, init, parser, common · `panel/`: server, actions, views | the owner's and the agents' entry points; call everything below |
| loop | `foreman/loop/` | tick, queue, launch, guarded | the process that ticks; gathers facts, acts on `decide` |
| steps | `foreman/steps/` | sources, release, talk, reviewing, mechanic, parents, metrics, schedule, `health/`: health, checks, main_tests, notes | one step of a tick, or a view over several pieces |
| services | `foreman/services/` | `agents/`: brief, worktree, launch, running · `claude/`: sessions, login · `delivery/`: git, check, merge, pulls, mergecheck · lock, launchd, runsource, scratch | the outside world: claude, processes, the lock, and git through `Repo`; called by steps |
| registry | `foreman/providers/registry.py` | | picks the adapter, and hands out the repo (`registry.repo(p)`) (by convention the only caller of the adapters; not enforced by tach) |
| adapters | `foreman/providers/` | local · `github/`: provider, types, checks, review · gitrepo | one per platform, and `gitrepo` (the one module that runs git on a repo); they see their port, never the loop or each other |
| ports | `foreman/providers/` | `__init__.py` · repo | the `Provider` interface; `Repo`, every git operation on a repo (typed: heads, diff counts, conflicts, worktrees, the merge outcome) |
| proc | `foreman/proc.py` | | the one module that runs programs |
| core | `foreman/core/` | `project/`: config, registry, repos, plugin, recurring_table · `tasks/`: backlog, questions, blockers, subtasks, tasktypes, taskrepo, editing, recurring · `review/`: verdict, inline, describe, evidence, reviews · `rules/`: risk, decide · `oversight/`: guard, diagnosis, costs | vocabulary, pure rules and the plain files that do not vary |

Core areas, in their `depends_on` order: `project` (foreman.toml, the registry of homes, repos, the `[[recurring]]` tables, the plugin's text) →
`tasks` (the backlog file, questions, blockers, subtasks, types, edits, recurring entries) and `review` (the verdict,
inline comments, the description, evidence) → `rules` (`risk` on `project`; `decide` on `risk` only) → `oversight`
(the guard, diagnosis, costs). `schedule` (FN-162) moves with the steps.

### tach rules

`tach.toml` (the settings above `layers` stay; `layers` is unchanged). A package path gives all its files
its layer. A module with `depends_on` may import, in its own layer, only what it lists (lower layers stay open): that
fixes the order of the core areas, so no cycle can form among them, as tach does not see a cycle inside one layer
otherwise. `cli` and `panel` are two modules: the panel imports the CLI's functions (its actions run the
owner's commands), the CLI never imports the panel; `foreman/ui/main.py` starts `foreman panel`. Services and steps keep to the layer rule; inside services the order `claude` ← `delivery` ←
`agents` is the reviewer's.

```toml
layers = ["ui", "loop", "steps", "services", "registry", "adapters", "ports", "proc", "core"]
modules = [
  { path = "foreman.ui.cli", layer = "ui" },
  { path = "foreman.ui.panel", layer = "ui" },
  { path = "foreman.loop", layer = "loop" },
  { path = "foreman.steps", layer = "steps" },
  { path = "foreman.services.agents", layer = "services" },
  { path = "foreman.services.claude", layer = "services" },
  { path = "foreman.services.delivery", layer = "services" },
  { path = "foreman.services.lock", layer = "services" },
  { path = "foreman.services.launchd", layer = "services" },
  { path = "foreman.services.runsource", layer = "services" },
  { path = "foreman.services.scratch", layer = "services" },
  { path = "foreman.providers.registry", layer = "registry" },
  { path = "foreman.providers.github", layer = "adapters", depends_on = ["foreman.providers"] },
  { path = "foreman.providers.local", layer = "adapters", depends_on = ["foreman.providers"] },
  { path = "foreman.providers.gitrepo", layer = "adapters", depends_on = ["foreman.providers.repo"] },
  { path = "foreman.providers", layer = "ports" },
  { path = "foreman.providers.repo", layer = "ports" },
  { path = "foreman.proc", layer = "proc" },
  { path = "foreman.util", layer = "core" },
  { path = "foreman.launcher", layer = "core" },
  { path = "foreman.core.project", layer = "core", depends_on = ["foreman.util"] },
  { path = "foreman.core.tasks", layer = "core", depends_on = ["foreman.core.project", "foreman.util"] },
  { path = "foreman.core.review", layer = "core", depends_on = ["foreman.util"] },
  { path = "foreman.core.rules.risk", layer = "core", depends_on = ["foreman.core.project"] },
  { path = "foreman.core.rules.decide", layer = "core", depends_on = ["foreman.core.rules.risk"] },
  { path = "foreman.core.oversight", layer = "core", depends_on = ["foreman.core.project", "foreman.core.tasks", "foreman.core.review", "foreman.util"] },
]
```

### Where new code goes

Ask in this order; the first yes places it:
1. Talks to git on a repo? → the `Repo` port (`registry.repo(p)`); runs a program, or talks to claude or gh? → `services/` (through `proc`); a platform call → a `providers/` adapter.
2. A decision from facts, reading no file and running no program? → `core/rules/`, or the core area it is about.
3. The shape of a plain file (backlog, foreman.toml, the registry)? → `core/<area>`.
4. One step of the tick? → `steps/`. 5. A command or a panel route? → `ui/`.

A module that grows past its size is split along its area's seams, inside its area.

- **Per-repo settings and execution (FN-150..154):** the repo key rules, `Project.for_task` and the strict tables →
  `core/project/repos.py` and `config.py`; `--repo` → `ui/cli/parser.py` and `owner.py`, shown by `view.py` and
  `ui/panel/views.py`; dropping the plan on edit → `core/tasks/editing.py`; the shown repo and the Go and add refusals →
  `core/tasks/taskrepo.py`; worktree and `post_create` →
  `services/agents/worktree.py`; check, merged check, PR and merge in the task's repo → `services/delivery/*` and
  `providers/registry.py`; the `publishes` fact → `core/rules/risk.py`; per-repo health checks → `steps/health/`
  (a new `repos.py`), the path matching pure in `core/project/repos.py`; the loop calls `for_task` once per task step,
  in `loop/tick.py` and `loop/queue.py`, and in `services/delivery/merge.py` for the owner's accept and close.
- **Task types (FN-157, built):** the type names and rules → `core/tasks/tasktypes.py`; each type's paragraph stays data
  in `foreman/briefs/*.md`, read by `services/agents/brief.py`; `--type` → `ui/cli/parser.py`. A new type is one entry and
  one paragraph, no other layer.
- **Plugin entry points (FN-146, FN-159):** the SessionStart hook and `foreman project` → `ui/cli/plugin.py`; the session's
  context text → `core/project/plugin.py`; the installed version's start → `foreman/launcher.py`; the launchd jobs' text →
  `services/launchd.py`; `bin/foreman` and `hooks/hooks.json` are in `plugins/foreman/`.

### Ports

Only where there is an external system or two real implementations: `providers/` (local, github), and the
launcher and source plug-ins (`sources`) when they exist. Not behind a port, because they do not vary: `backlog.json`,
the config, the clock. A new port needs a second real implementation or a system outside the process; say which in the
task.
`Runner` (FN-203, planned): the agents' sessions; adapters `claude` and `orca`; two real implementations.

### Rules

The rules, one line each, are in `CLAUDE.md`, each marked as checked by `make check` or by the reviewer. What they need
said once:
- **One module runs programs**: `foreman/proc.py` (`run`, `run_json`; it knows nothing of git, gh or claude; a missing
  program or a timeout is a `ProcError`). ruff bans `import subprocess` elsewhere (TID251; not `os.system`/`os.popen`).
  The only other exceptions in `per-file-ignores` (`pyproject.toml`) are the tests and `lock.py`, which needs a process
  group and signals to stop a whole job at its time limit (`start_new_session`, `killpg`).
- **Typed at the border**: the github adapter turns `gh`'s JSON into its own types (`PullRequest`, the check buckets)
  with a small converter per shape; a shape that does not fit is a `ProviderError`. The port's operations take a
  `Change` and return `Published` / `ChangeState`: no dict crosses it.
- **`decide` runs no program and touches no file**: tach lets it import only `risk`, and `tests/test_decide.py` rejects
  any other import and any `open`/`exec`/`eval` in it. New decisions go in the core, not in `delivery` or the loop.
- **Suppressions do not work**: `make lint` runs ruff with `--ignore-noqa`, pyright has `enableTypeIgnoreComments =
  false`, and `make suppressions` fails on `pyright: ignore|basic|off` and `pylint: disable`.
- **Tests are type-checked**: standard on all of `tests/`; every test file not in `tests/standard-only.txt` is strict, so a
  new test file is strict. The list only shrinks (`tests/pyright-*.json`, `make types`).
- The panel's TypeScript is outside these rules.

### Size

Modules stay at most 300 lines (`max-module-lines` in `pyproject.toml`, `make size`, no exceptions); tests 600. The
limit stays: it did its job (it forced FN-157 and others to split); what failed was that a split had no obvious place,
and packages by area give one. When FN-160 ends no module should pass 270 lines (a 10% margin): 270 is a target for
its splits, not a new limit (changing `pyproject.toml` is the owner's call).


## Watching itself

Metrics per task (`foreman metrics`, the panel): the model and effort its responsible agent ran on, cost of its agents (transcripts at list prices; planning_usd of planning sessions and responsible_usd of responsible sessions, from `session_kinds`; review rounds), time to review and to
done, rework (rejected), conflicts and failures (returns for failing tests or CI), nudges (stopped without finishing),
questions to the owner, guard stops, risk, and what misled its agents (their `--misled` words, per agent).

Health checks (`foreman/steps/health/`), each finding a triage task, deduplicated by key while open or while it persists:
loop dead (heartbeat older than 3 ticks; from outside the loop: `foreman health`, the panel), agent stuck (working, its
transcript silent for `stuck_minutes`), agent blocked (stopped after a nudge, or could not launch), orphan worktree,
tests failing on main (the test command on main's head, when it moves or every `ci_every_minutes`; one task per failing
episode, from the first failing run until a pass, with the commit, the failing step and the output tail), loop step errors.
Every finding has three parts: what it means, the evidence fm gathered, and a hypothesis marked pending; none proposes a fix.

`foreman watch` (FN-221, `foreman/steps/watch.py`) prints one line, with the task id and why, each time something starts
needing the owner: an open question, a task waiting for his accept (`decide.after_check` says `owner`, so autonomy-merged
tasks print nothing), a blocked task (a failed launch is one), main's tests failing, a parent agent whose session is alive
and idle (it ended its turn waiting for him in its own chat). It decides from the backlog, `state/ci.json` and the session
listing (`sessions.listing`), never from the text of `events.jsonl`. Every 10 s it looks again; its seen set lives in memory,
so what needed the owner at start is not printed (`foreman list` shows it). `--once` exits after the first line; without it
the command streams (a monitor).
