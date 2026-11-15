# foreman-next: design

One responsible agent per task, a loop without a model, a short guard, and a system that watches itself.
Nothing needs keeping alive but the loop, which has no model.

## Pieces

```
            owner ──── panel (React, today's /next/ panel adapted) ── fm/panel.py (JSON API, local, key)
              │  └─── claude attach / agent view / Claude app: talks to any agent directly
              │
   backlog.json (schema 5) + state/ (events, guard log, grants, seen, ci, loop heartbeat)
              │
   fm loop (plain process, launchd KeepAlive) ── every N s, per registered project:
      sources → answers → agents → queue → health → blockers → launch
              │
   claude --bg -n NAME --settings {env, guard hook}   (one session per task, plus one per review round)
      planning agent  (cwd: the repo, read only)     → fm ready / fm ask / fm block
      responsible agent (cwd: its worktree, branch)  → subagents, fm ask, fm done / fm block
      hunter, then reviewer (launched by the loop, read only) → hunter file, review file, fm verdict
```

| File | Lines | What |
|---|---|---|
| `fm/backlog.py` | ~190 | the backlog file (today's schema 5, new statuses and keys), questions tied to a task and agent ||
| `fm/loop.py` | ~250 | the loop and the launch of agents (briefs, worktrees) |
| `fm/sessions.py` | ~120 | `claude --bg` start / resume / list (from foreman's sessionlaunch.py) |
|| `fm/delivery.py` | ~200 | check a delivered branch, risk score, autonomy, accept (records), merge step ||
| `fm/mergecheck.py` | ~30 | the merged check: tests on an accepted branch merged with main as it is at the merge (FN-113) |
| `fm/verdict.py` | ~100 | the verdict as a review body and the `Findings:` line parser (pure text) |
| `fm/inline.py` | ~80 | the diff's visible lines and findings as line comments (pure) |
| `fm/describe.py` | ~50 | the report as a description: marked block, footer, folded review rounds (pure text) |
| `fm/evidence.py` | ~35 | the images a report names: bare names, relink, size and kinds (pure text) |
| `fm/subtasks.py` | ~80 | subtasks: which belong to a parent, listing order, when and by whom a subtask may be filed or edited, how the parent closes, what its agent reads and is told (pure) |
| `fm/parents.py` | ~85 | the parent agent: the tick step that launches it on a let-in parent, tells it of each subtask merged or canceled (`notified`, message kind `subtask`) and stops it once the parent closes; `fm done` of a parent |
| `fm/blockers.py` | ~50 | which tasks a task waits for (`blocked_by`): pending, dead, refusals incl. cycles (pure) |
| `fm/release.py` | ~25 | the loop's blockers step: marks a task unblocked once, or blocks it when a blocker closed undone |
| `fm/risk.py` | ~80 | risk score and autonomy: pure functions of the facts a check found |
| `fm/decide.py` | ~110 | what the loop does with a branch in review: review steps, send-back notes, check again, merge alone or wait (pure) |
| `fm/reviewing.py` | ~60 | the loop's review step: what it sees of a branch's review, and launching, holding, blocking or sending back as `decide` says |
| `fm/providers/` | ~100 | one module per platform behind a small interface (`registry.py` picks it); `local` (git merge into the main checkout), `github` (pull requests with `gh`); `fm/pulls.py`: the loop's PR steps |
| `fm/guard.py` | ~150 | the PreToolUse hook: a handful of actions by name |
| `fm/health.py` | ~150 | checks that find broken states and file triage tasks, deduplicated |
| `fm/diagnosis.py`, `fm/mechanic.py` | ~100 | the mechanic: required sections of a diagnosis and how it is stored; the tick step that launches, times out and stops mechanics |
| `fm/costs.py`, `fm/metrics.py` | ~150 | cost from transcripts (from foreman's costs.py), per-task metrics |
| `fm/sources.py` | ~50 | the plug-in contract for tool signals (none shipped) |
| `fm/plugin.py`, `fm/cli_plugin.py` | ~80 | the Claude Code plugin: the SessionStart context of a session in a project's folder, `fm project`'s values; manifests in `.claude-plugin/`, the hook in `hooks/hooks.json` |
| `fm/cli.py`, `fm/cli_view.py` | ~300 | `fm`: the agents' commands, the owner's commands, `loop`, `guard`, `panel`; `cli_view`: `list` and `show` |
|| `fm/briefs/*.md` | ~80 | the briefs: `planning.md`, `responsible.md`, `hunter.md`, `reviewer.md`, `mechanic.md` (from the old foreman's briefs) ||
| `fm/panel.py`, `panel/src` | | the panel server and today's panel, adapted |

Python stdlib only (3.11+, `tomllib`); the panel is today's React/Vite source, built to `panel/dist`.

## A project

A *home* folder: `fm.toml` (the owner's settings), `backlog.json`, `state/`, `worktrees/`, optional `sources/`.
`fm.toml` points to the repo (`[project] repo`, `main`, `prefix`, `branch_prefix`). The registry
(`~/.config/fm/projects.json` or `$FM_REGISTRY`) lists the homes the loop and the panel serve. Several repos are
`[repos.<key>]` tables (`path`, `main`) instead of `[project] repo` (both forms: fm refuses); tasks run in the first one
listed until `## Several repos` is built (FN-144 subtask 4). A project owns its home, every repo, its worktrees folder
and the umbrella folder set in `[project] umbrella` (explicit, never guessed). A command finds its project by `--project
NAME`, `--home`, `FM_HOME`, `FM_PROJECT`, then the registered project owning the current folder (paths resolved, the
deepest folder wins; two projects claiming it: fm refuses and `fm health` names them), then the nearest `fm.toml` above
it. Every setting is per project: `[agents] slots` (how many at once), models, extra session settings; `[merge]
autonomy`, `provider`, `strategy`, `test`, `[risk.high]`, `[risk.medium]` (label → path patterns); `[guard] ask`;
`[health]` timings; `[[recurring]]` entries (see Recurring tasks). With several repos, `[merge]` and `[risk]` are the defaults a repo's own tables override (see
Several repos).
`fm init` makes `fm.toml`, `backlog.json` and `instructions.md` (the owner's own rules), never overwrites a file of the
home, and prints the two lines that install the plugin. The plugin (`fm@fm`, marketplace `fm`, from this repo's branch
`foreman-next`; no `version` anywhere, so Claude Code's version is the commit and every merge reaches it) gives a
session in any folder of a registered project the skill `fm:define-task` (its values from `fm project`), the shared
instructions with the project's values plus `<home>/instructions.md` (the SessionStart hook `fm session-context`; `bin/fm` re-executes under a newer `python3` from PATH or names the minimum, and the hook leaves that line for `fm health`), and
`fm` on the PATH; a session elsewhere gets no context. Agents run with it disabled (`enabledPlugins` in their settings).

## The states of a task

```
 triage ──go──▶ backlog ──loop──▶ refining ──fm ready──▶ ready ──loop──▶ in progress ──fm done──▶ in review
 (health,        (to plan)        (planning agent)                       (responsible agent)      (check, review,
  sources)                                                                      ▲                    risk score)
                                                                                └──── fm reject ─────┤
                                                                                                     ▼
                                                                                  done ◀── fm accept / autonomy,
                                                                                  then the merge step
 any open state ── fm cancel ──▶ canceled          `blocked` (a reason) on any state: the loop leaves it alone
```

- `triage`: filed by a health check, a source, or without acceptance criteria (the only state such a task can be in).
  Never launched until the owner says «go»; `fm go` refuses a task without acceptance criteria.
- `backlog`: to plan (it has acceptance criteria, set with the owner). `refining`: its planning agent works. It ends
  with `fm ready --plan|--no-plan`.
- `ready`: has acceptance criteria and a plan (or none, if trivial). `in progress`: its responsible agent works, in its own worktree and branch.
- `in review`: delivered. The loop checks and reviews the branch; the owner accepts it or rejects it with a note.
- `blocked`: a reason set by an agent (`fm block`), by the loop (could not launch; stopped without finishing after a
  nudge). The owner clears it (`fm unblock [--relaunch]`).
- `blocked_by` (`fm add/edit --blocked-by FN-n …`; `none` clears): the task is never planned or launched until every
  blocker is `done`, then the loop marks it unblocked in the history. A canceled or duplicate blocker blocks the task
  with a reason: the owner edits `--blocked-by` or cancels, then `fm unblock`.
- `type` (`fm add/edit --type change|bug|mechanical|definition`; default and every task without one: change) only
  changes the instructions of the task's agents, one paragraph per type; a parent has none.
- `fm edit` changes title, goal, criteria, detail, model, effort, type or blockers of a task in triage, backlog or ready (never one with an agent,
  nor a closed one); criteria or detail on a backlog or ready task drops its plan and returns it to backlog.
- A task too big for one PR is a parent with subtasks (`fm add --parent FN-n`, only while the parent is in triage or
  backlog; one level). A subtask is an ordinary task (own agent, branch, PR) that names its parent. A parent in triage
  closes by itself when every subtask has (done if any is done, else canceled). Once let in (backlog) it gets a parent
  agent (`agent.kind` `parent`, no worktree, status stays backlog): it is told each subtask merged or canceled and
  nothing else (`notified` lists those told; message kind `subtask`), proposes the next Go and changes to the owner, may
  file (`--triage`) and edit only its own subtasks, and closes the parent with `fm done` (refused while a subtask is
  open) after the owner's yes. `fm cancel` of a parent cancels its open subtasks and closes their PRs.
- A question (`fm ask`) does not change the state: it is a decision tied to the task and its agent. Its agent ends
  its turn; the owner answers in the panel (or talks to that agent directly); the loop resumes *that* session with the
  answer.

## The loop

A plain process, no model (`fm loop`; `fm launchd` prints two jobs, loop and panel, with `RunAtLoad` and `KeepAlive`; `--dir` writes them as files. Agents use the normal claude.ai login, so they show in the Claude app; the loop removes `CLAUDE_CODE_OAUTH_TOKEN` from every agent it launches. `fm health` warns 3 days before the login expires and files a finding when it does not work (`claude auth status`)); one per registry (a lock file).
Each tick, per project, each step isolated (an exception goes to `state/errors.jsonl`, which a health check turns
into a finding), and inside a step each task isolated (a failure blocks that task with the reason; the others go
on). The heartbeat is written before every step, so a long test run does not look like a dead loop.

1. **heartbeat**: `state/loop.json`.
2. **sources**: every executable in `<home>/sources/` prints JSON lines, each with a `key` (dedup). Without `task`,
   `{key, title, detail}` becomes a triage task, deduplicated by source and key. With `task` (a task id),
   `{key, task, action, text}` acts on that open task through the owner's own commands: `note` (text reaches its
   agent like an answer), `reject` (text; as `fm reject`), `accept` (as `fm accept`), `done` (closed as done, merged
   outside fm), `cancel` (as `fm cancel`). Applied once per source and key (`state/applied/`). Unknown or closed
   task, unknown action, missing key, or an action invalid for the task's state: nothing changes, logged in
   `state/errors.jsonl`, not retried. A transient failure (accept before its check, busy checkout, hung stop) is
   logged and the same key is tried again next tick.
   Cursors in `state/cursors/`. None shipped (the owner's call for now). The step `recurring` right after it
   files the recurring entries that fell due (`fm/schedule.py`, see `## Recurring tasks`).
3. **answers**: each answered question, permission or rejection not yet delivered is sent to its agent's session
   (`claude --bg --resume SID MESSAGE`, no other flag: a flag or a live process makes claude start a copy), when that
   session is not busy: the idle session is stopped, fm waits until its process is gone (+3 s), then resumes; at most
   one answer per task per tick; a copy (different short id) or an unreadable `claude agents` fails the send; dropped if the task closed meanwhile;
   a failed try is retried next tick; the answer stays not delivered and `fm health` shows it after 10 minutes. A session id not listed yet at launch is
   filled in from its short id on a later tick.
4. **agents**: an agent whose session ended its turn without `fm done`/`ready`/`ask`/`block`, and whose transcript
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
   with a reason (`fm/release.py`).
8. **launch**: while running agents < `slots` and launching is not paused, the next `ready` task (its responsible
   agent) or `backlog` task (its planning agent), by priority then id, skipping any with a blocker not `done`. If the task changed while its session
   started (the owner cancelled it), the new session is stopped. The loop never plans and never reviews. A parent is
   never launched here: just before, a let-in parent gets its parent agent regardless of slots (`fm/parents.py`).
9. **mechanic**: for each triage task health filed (`source` `health:*`) that has none yet, one mechanic session, in the
   slots launch left free (it counts as a slot while it has no diagnosis). It stops once the diagnosis is in, and a mechanic
   that ended without one or ran over 30 minutes leaves a «Diagnosis missing» note; it is never relaunched.

`fm cancel` stops its agent, closes its open questions and removes its worktree (the branch stays).

### What the loop does with a task in review

Each tick, for a task in review (not accepted, not blocked), in this order. The decisions are pure functions in
`fm/decide.py`; the loop (`queue_one`) gathers the facts (sessions, files, git, CI, the check) and acts on the answer.

A review runs only when its result goes somewhere: to the agent (rounds 1-2, an owner reject, a conflict resolution,
the fix of failing tests or CI)
or, once, to the owner when the rounds run out. Each launch event ends with the rule that launched it
(`rule: rounds 1-2`, `rule: owner reject`, `rule: conflict resolution`, `rule: failing tests or CI`: `decide.RULES`).

| Situation | What the loop does | Function | Test |
|---|---|---|---|
| A tip with no review, in rounds 1-2 | launches the hunter if a slot is free (and not paused); else waits | `review_step` → `hunt` / `wait` | `test_no_hunter_launches_it` |
| The hunter's file is written and the hunter is not working | launches the reviewer if a slot is free; else waits | `review_step` → `review` / `wait` | `test_hunter_file_and_ended_launches_reviewer` |
| The hunter ended without its findings file | blocks the task | `review_step` → `block` | `test_hunter_without_file_blocks` |
| The reviewer ended without a verdict | blocks the task | `review_step` → `block` | `test_reviewer_without_verdict_blocks` |
| Verdict with findings open before the last round | back to its agent for review: its note, once | `review_step` → `back` | `test_open_findings_go_back_once` |
| A new tip that only merges main, with no conflict | no review: the verdict holds (`review.holds`) | `launch_rule` → `hold` | `test_a_clean_merge_or_a_closed_review_holds`, `test_a_clean_merge_of_main_launches_nothing` |
| A new tip after the loop acted on a verdict of the last round, open or clean (`review_for` none) | no review: the verdict holds; what is open stays for the owner; no round 3 runs by itself (after a round-1 verdict, a new tip gets round 2) | `closes_review`, `launch_rule` → `hold` | `test_a_review_closes_after_the_last_round`, `test_after_two_clean_rounds_a_new_tip_launches_nothing`, `test_a_clean_merge_or_a_closed_review_holds`, `test_after_round_two_later_tips_launch_no_review`, `test_a_new_tip_after_a_clean_verdict_gets_a_review` |
| The agent's fix after `fm reject` | one review of the fix only (`git diff` reviewed tip..new tip), from round 1; open findings back to its agent | `launch_rule` → `reject`, `review_base` | `test_reject_and_conflict_launch_with_their_base`, `test_reject_gets_a_review_of_the_fix_and_open_findings_go_back` |
| A new tip that merges main resolving a conflict | one review of the resolution only (`git diff` git's automatic merge..new tip), from round 1; open findings back to its agent | `launch_rule` → `conflict`, `review_base` | `test_reject_and_conflict_launch_with_their_base`, `test_accept_then_conflict_resolves_and_merges_without_a_new_accept` |
| The agent's fix after failing tests or CI | one review of the fix only (`git diff` failing tip..new tip), from round 1; open findings back to its agent | `launch_rule` → `failing`, `review_base` | `test_the_fix_of_a_failure_gets_a_review_of_only_that_fix`, `test_the_fix_gets_a_review_of_only_that_fix` |
| CI pending | waits | `waits_for_ci` | `test_pending_ci_waits` |
| Head, main or CI moved since the last check | checks it again (in the background) | `checks_again` | `test_moved_branch_is_checked_again` |
| It conflicts with main and nobody said it should merge | waits for the owner; `fm show` and the panel say it conflicts and goes back when accepted | `after_check` → `owner` | `test_conflict_waits_unless_someone_wants_the_merge`, `test_a_waiting_task_that_conflicts_stays_and_says_so` |
| It conflicts and the owner accepted it (also a merge refused for a conflict) or the autonomy would merge it | back to its agent for the conflict, keeping that accept (`resolving`) | `after_check` → `conflict`, `conflict_note` | `test_conflict_goes_back`, `test_conflict_waits_unless_someone_wants_the_merge` |
| Tests or CI fail, returned fewer than 2 times in a row (`failure_streak`; without a test command a check without failures ends the row, with one only the merge does) | returned to its agent with the failing output or checks, whoever wants the merge; keeps its check (the panel shows why) and counts `failures` | `after_check` → `failing`, `failing_note` | `test_failing_tests_or_ci_go_back_up_to_the_cap`, `test_failing_note_names_the_output_and_the_checks`, `test_failing_tests_go_back_with_the_output`, `test_failing_ci_goes_back_with_each_check`, `test_a_green_check_ends_the_row_without_a_test_command` |
| Tests or CI fail a third time in a row | waits for the owner, the failure shown in its check | `after_check` → `owner` | `test_failing_tests_or_ci_go_back_up_to_the_cap`, `test_the_third_failure_waits_for_the_owner` |
| Resolved, no findings open, checks green, with the owner's accept kept | merges without a new accept (an accept the autonomy made is decided again: high never merges alone) | `after_check` → `merge` | `test_conflict_waits_unless_someone_wants_the_merge`, `test_accept_then_conflict_resolves_and_merges_without_a_new_accept` |
| The resolution's review leaves findings open, tests fail or CI is not green | the kept accept lapses; waits for the owner | `after_check` → `owner` | `test_conflict_waits_unless_someone_wants_the_merge`, `test_a_resolution_left_open_waits_for_the_owner` |
| Autonomy allows the risk, no open findings, CI green where the provider has CI | merges alone | `after_check` → `merge` | `test_autonomy_merges_alone` |
| Anything else | waits for the owner (`fm accept` / `fm reject`) | `after_check` → `owner` | `test_everything_else_waits_for_the_owner` |

### The merged check (FN-113)

A branch's own check runs its tests on the branch alone. Just before an accepted branch merges (the owner's accept or
the autonomy's, `loop.gate_merge` on both paths), the test command runs again on the branch merged with main as main
is at that moment (`fm/mergecheck.py`, in the background like the check). The result goes in the task's `check`
(`merged_with` = that main commit); `fm show` (`checked_on`) and the panel say what the last check ran on. A task not
about to merge (in review, waiting for the owner) never runs it. Accepted tasks merge one at a time, so of two waiting,
the second is checked with the first already in main.

| Situation at the merge step | What the loop does | Function | Test |
|---|---|---|---|
| No test command | merges as today: the PR's CI stands in | `merge_gate` → `merge` | `test_without_a_test_command_it_merges_as_today` |
| It conflicts with main | as today: the merge returns it to its agent (FN-133), no merged run | `merge_gate` → `merge` | `test_conflict_goes_back_without_a_merged_run` |
| No merged check on this head and main as it is now (never run, or main moved since) | runs the merged check, then decides | `merge_gate` → `check` | `test_passes_alone_and_merged_merges`, `test_main_moved_after_a_pass_checks_again` |
| Passed merged, neither main nor the branch moved since | merges without running the tests again | `merge_gate` → `merge` | `test_nothing_moved_merges_without_a_run` |
| Fails merged, fewer than 2 returns in a row | back to its agent with the output, saying it failed merged with main `<sha>`, not on its branch alone; counts in `failures` and `failure_streak` | `merge_gate` → `failing`, `failing_note` | `test_fails_merged_goes_back_saying_so`, `test_autonomy_path_checks_merged_too` |
| Fails merged a third time in a row | its accept lapses; waits for the owner, the failure shown in its check | `merge_gate` → `owner` | `test_third_merged_failure_waits_for_the_owner` |

With a test command, a green check of the branch alone no longer ends a row of failures (`failure_streak`): only the
merge does, otherwise each fix passing alone would reset the count. Without one, a green check ends it, as before
(`test_a_green_check_ends_the_row_without_a_test_command`).

The decision tests are in `tests/test_decide.py` and launch no program; the loop's in `tests/test_review_rules.py`. How
a tip follows the reviewed one is `delivery.merge_of`: a merge of the reviewed tip and a main commit whose tree equals
git's automatic merge (`git merge-tree`) is clean; any other tree is a resolution; anything else (plain commits, a
rebase) is neither, and in a conflict episode its whole branch is reviewed.

## The agents

Each is a Claude Code session of its own, named after its task (`tk-1`, `tk-3-planning`), started with
`claude --bg -n NAME --model M --effort E --permission-mode bypassPermissions --settings S PROMPT` in its cwd:

- `M` and `E` are the project's, except for the responsible agent: the task's own `model`/`effort` (optional task keys, chosen by the planning agent with the rubric in `planning.md`) win; the planner fills only the keys the owner left empty (`fm ready` never overwrites one).
- `S` sets `FM_HOME`, `FM_TASK`, `FM_AGENT` and `PATH` (so `fm` works), `worktree.bgIsolation: none`, the guard as
  a PreToolUse hook, and the project's extra settings (the demo turns off today's foreman plugin for its agents).
- The owner opens any of them: `claude attach SHORT`, `claude agents`, or the Claude app; what he says there goes to
  that agent.

**Planning agent** (`fm/briefs/planning.md`): the acceptance criteria are product (observable behaviour, never how) and
are settled with the owner when the task is filed; the planning agent never writes, rewrites or splits them, and `fm ask`s
the owner if one cannot be met as written. It reads the repo (read only), asks the owner only what only he can decide
(`fm ask`, then ends its turn), and ends with `fm ready --detail --plan PATH` (or `--no-plan "why trivial"`). It writes a
plan at `<home>/plans/ID.md` (files touched, steps, decisions with alternatives dropped, out of scope, risks, declared
risk); the task gets `plan_file`, and the responsible agent follows it and `fm ask`s when it does not hold.

`fm done`, `fm ready` and `fm verdict` take `--misled`; the text (not «none») is kept on the task under `misled`, per
agent, once; `fm metrics` shows it.

**Planning agent** also: statements of scope cite their search, questions stand alone (context, options, recommendation and why).

**Responsible agent** (`fm/briefs/responsible.md`, from the old foreman's agent brief and procedure): owns the whole task in its
worktree: splits it, uses subagents, writes the failing test first for bugs and behaviour, keeps tests green, merges
main before delivering if it moved, asks the owner only what only he can answer, writes a report
(`<home>/reports/ID.md`: the task's description, each acceptance criterion with its evidence; fm sets it as the description
inside `<!-- fm:description -->` markers, adds a footer with the commit and review state, folds its `## Review rounds`
section in `<details>`, and the panel shows it; the loop seeds the file from `fm/briefs/report.md`, or from the repo's
`.github/pull_request_template.md` plus a Review rounds section, when it launches) and ends with
`fm done --summary --report --misled`. If the review returns it or the owner rejects it, the note arrives in the same
conversation: each finding gets the smallest change that settles it, or a one-line answer if it thinks the finding is wrong; add a `### Round N` to the report's «Review rounds» section, `fm done` again.

**Review: hunter, then reviewer** (`fm/briefs/hunter.md`, `fm/briefs/reviewer.md`): launched by the loop, not by the responsible agent, each as
its own session in the branch's worktree, once per delivered tip; each counts against the slots (the hunter until its file exists, so its own reviewer can take its place). Both get the task's detail
and the owner's notes. The **hunter** lists everything it suspects (with a confidence each, plus the acceptance criteria table and an
inventory of what the diff adds) in `<home>/reviews/ID-rN-<tip>-hunter.md`. When that file exists and the hunter has ended,
the **reviewer** starts; it sees only the file, never the hunter's reasoning. It checks each Wrong or Missing finding
(confirmed, refuted, or neither) and each Too much item; a finding leaves only with a cited line or output that refutes it,
so what it could not refute stays in To fix. What was dropped goes under Noted and is not sent to the responsible agent. It writes
`<home>/reviews/ID-rN-<tip>.md` (header `Branch/Tip/Round/Open`, N = items under To fix) and runs
`fm verdict --tip --open N --file`; a verdict for a tip other than the newest launch is stored in the review history but
decides nothing. There are no severities. Open findings in round 1 → back to the responsible agent (`decide.review_step`);
round 2 checks the fixes only and opens nothing new unless a fix is itself Wrong or Too much; what is still open after
round 2 goes to the owner as high risk. A hunter that ends without its file, or a reviewer without a verdict, blocks the task.

The **mechanic** (`mechanic` kind, the task's `mechanic`) diagnoses a health finding and changes nothing else: read-only by its
brief (as the hunter's), its only write is `fm diagnose --file`, which checks the required `##` headings (`fm/diagnosis.py`)
and appends the diagnosis to the task's `detail`, so `fm show` has it. The task stays in triage; the owner decides next.

What was dropped from the old foreman because only its central session needed it: memory renewal, session marking,
interface mods and band, catch-up summaries, the review round as a message, plans written by someone else, subtasks
assigned from outside, its skills. The responsible agent keeps: its brief, `fm`, its own subagents; the review is its own sessions.

## Shared resources

`fm/lock.py`, the lean version of foreman's lock. A resource is declared in fm.toml (`[resources.NAME]`, optional
`slots = [...]` and `max_minutes`, default 20). `fm lock NAME run "what" command…` waits its turn, runs the command
holding the resource (with `FM_SLOT`), stops it at the time limit and always releases it; `fm lock NAME status`.

- Fair queue: first come, first served; a holder or waiter whose process died is cleaned up whenever anyone looks.
- Slots: one holder per slot, one queue.
- The state is common to the machine (`~/.local/state/fm/locks/NAME/`), so projects that declare the same name share
  it; every use is logged (`uses.jsonl`: who, task, what, waited, held, exit code, timeout) for the metrics
  (`lock_wait_min` per task) and the panel (System: who holds each resource and who waits).
- The responsible agent's brief lists the project's resources and the rule. No hook enforces it.

Left out from today's: the «busy» detector, short jobs jumping the queue, the turn file, the «who am I» chain, the
enforcing hook, machine tables merged with the project's.

## The guard

A reminder, not a cage (`fm/guard.py`). It runs only in agent sessions (the hook comes in `--settings`), looks at
the program each shell segment runs (never at words inside strings or messages), and denies a handful of actions
with a reason that says: do not look for another way; `fm ask --action NAME "what and why"`, end your turn. The
owner allows it once from the panel (`fm allow QID`); the loop tells its agent; it passes once.

| Action | Recognised by |
|---|---|
| `publish` | `npm/pnpm/yarn/cargo/uv/poetry publish`, `twine upload`, `docker push`, `gh release/pr/repo create|merge|edit|delete|upload` |
| `push-main` | `git push` to the main branch (named, `HEAD` while on it, or bare while on it), or with `--force`/`--mirror`/`--all` or a ref written with a leading `+` |
| `push-evidence` | `git push` to the `fm-evidence` branch (only the loop writes it) |
| `delete` | `rm`/`rmdir`/`unlink`/`find -delete` of a path outside its worktree and temp dirs; `git branch -d/-D` of another branch; `git worktree remove`; `git push --delete` |
| `config` | Edit/Write of the project's `fm.toml`; a shell command that writes it (`>`, `tee`, `sed -i`, `mv`, `cp`, `rm`) |
| owner's own | `[guard] ask = [{name, pattern}]` in fm.toml: a regex on a shell segment |

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
`low`, `medium`. `high` always waits. Where the provider `has_ci` (github yes, local no), autonomy merges only on a green CI. The owner accepts in the panel (`fm accept`), or rejects it with a note
(`fm reject`, counted as rework).

### Provider, accepted → merged

The merge goes through a *provider* module (`fm/providers/`), one per platform; the rest of fm does not know which.
Interface: `pushed` (is the branch on the platform at its tip?) and `push` (put it there: never forced, never main, 20 s; any failure is `Transient` with the platform's error; a no-op locally), `publish` (open the change), `ready`, `diff` (its unified diff; empty locally), `review` (post the verdict as one PR review with its inline comments; no-op locally), `body` / `describe` (read and set the description and title; no-ops locally), `merge(change, strategy)` (the only merge
in fm: a future queue is just another strategy) and `state` (merged? closed?). Operations take a `Change` and return `Published` / `ChangeState`: no dict crosses the port. Steps:

1. **accepted**: `fm accept` (or autonomy) only records `accepted` on the task (it stays `in review`).
2. **merge**: the loop's queue step asks the provider to merge once, and stores the merge reference.
3. **merged**: the task closes (`done`), and its worktree and branch go, only when `state` shows the merge in main,
   not when the command ran.

Retry policy, one place (`loop.guarded`, every provider call: push, PR, CI, merge): a `Transient` failure is retried next tick and counted per task (`retry_count`, next to `retry_error`); the 3rd in a row blocks the task with the last error; a success resets both.

Config: `[merge] provider = "auto"` (default: `github` if the repo's `origin` is on github.com, else `local`; or force
`local` | `github`) and `strategy = "squash"` (default), `"merge"` (`--no-ff` locally) or `"rebase"` (github only). An
unknown value is a clear config error (at accept, and in the merge step). A merge never publishes: no tag, no release.

### GitHub provider: every task is a pull request

The responsible agent only commits; the **loop** pushes the task branches (`provider.push`: `git push origin refs/heads/<b>:refs/heads/<b>`,
never forced, never main, 20 s timeout; a failure is `Transient` with git's error) and, never an agent, opens, readies,
comments on and merges PRs through `gh` (the guard keeps stopping `gh pr` for agents). `[merge] pr = "draft"`
(default): a task in progress has its commits pushed and gets a **draft PR** as soon as it has its first commit; `pr = "done"`: no PR until
`fm done`. In review, after the hunter and the reviewer: the branch is pushed at its tip if it is not yet, the PR exists, and the loop waits while CI is pending. Then the check runs: **failing CI is high risk**, and
with no `[merge] test` the PR's CI **replaces the local test**. Then the draft is marked **ready** and a pull request review
(event COMMENT) carries the verdict (decision, risk and its reasons, checks); each finding inside the diff is also a comment on its line. Accepted → `gh pr merge` with the configured type; the task
closes only when the PR is seen `MERGED` (a PR closed unmerged blocks it). Autonomy `low`/`medium` merges **only with
green CI** (no CI, pending or failing waits for the owner). After the merge the main checkout is fast-forwarded from
origin when it is clean on main (fetch only, never a push). The images a report names (`![what](x.png)`, bare file
names saved in `reports/ID/`) go, by git plumbing and never forced, to `ID/` on the orphan branch `fm-evidence`, the
only push fm makes besides the task branch (agents may not: guard `push-evidence`); the PR body links them with
`?raw=true` blob URLs and the panel serves them at `/evidence`. Conflicts with main go back to the responsible agent once accepted, also when the merge itself is refused for a conflict after acceptance: the acceptance is carried over as `resolving`, and the resolved tip merges without a new accept unless its review leaves findings open or its checks fail. Out of
scope: reading approvals or comments from GitHub, merge queues, other platforms.

## Recurring tasks

`[[recurring]]` tables in `fm.toml` (strict: `fm/recurring_table.py`; any other key, both triggers, neither or a bad interval is refused at load, naming the table and the key). Each has `name`, `title`, one trigger, and the fields `fm add` takes (`goal`, `criteria`, `detail`, `model`, `effort`, `type`), plus `triage` (true by default).
- Trigger: `every_merges = N` (N merged tasks) or `every = "12h" | "7d" | "2w"` (plain durations: h, d, w; no cron).
- Each tick (the `recurring` step after `sources`, `fm/schedule.py` over the pure rules of `fm/recurring.py`) an entry that is due files one task: in `triage` when `triage = true` (the owner's Go), `backlog` when false and it has criteria. Its `source` is `recurring:NAME`, so `backlog.add` files none while one is open, and the mark survives the entry's removal (tasks it filed stay as they are). Its detail ends with what it covers: the merged tasks since its last run (ids, titles) or the period.
- Anchor: the count or period starts when the entry's last task closed (done, canceled or duplicate), else when fm first saw the entry (`state/recurring.json`, the one store). A time trigger that fell due while fm was off files once. Merges are `done` tasks not filed by an entry and not parents; their closing time is that of the `→ done` event.
- `fm list`, `fm show` and the panel's data mark an entry's task (`recurring`: the name); `fm health` and the panel's system view list each entry with merges left or the next date. A project with no entries sees none of it.

## Several repos (defined in FN-148; not built yet)

This section defines how a project with several repos runs its tasks; FN-144 subtask 4 builds it in the subtasks
listed in FN-148's report. Until then fm reads only `path` and `main` of each repo table and runs every task in the
first repo (FN-145). Two rules carry it all: **one task = one PR in one repo**, and **the project's repos stay
untouched** (their own git sees no fm or Claude file).

### Shape of fm.toml

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
config = ["fm.toml", ".github/", "*secret*", "*.env"]

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
  `fm`), `[agents]` (slots, models, `claude`, settings), `[guard]`, `[health]`, `[resources.*]`. `[merge]` and
  `[risk]` stay as the defaults for every repo.
- **A setting given at both levels: the repo's wins, key by key; absent there, the project's; absent there, fm's
  default.** A `high` or `medium` table in a repo replaces the project's table of that level whole, never label by
  label (as a project table replaces fm's default one today). So a repo's `high` table also drops fm's default one
  (`fm.toml`, `.github/`, `*secret*`, `*.env`): a repo that wants those paths high lists them again.
- Repo tables are strict: a project-wide key or an unknown key in one is a config error naming table and key
  (`fm: [repos.api] has no setting 'slots'`). No other table changes.
- `path` and `worktrees` are relative to the home, `~` expanded. `[project] repo` alone stays the one-repo form, with
  none of these tables.
- `post_create`: each command runs with `sh -c`, cwd the new worktree, `{main}` (the repo's main checkout) and `{wt}`
  replaced by shell-quoted paths (another placeholder is a config error at load), 300 s each, once per worktree (a
  reused one does not run it again). A failing or timed-out step removes the worktree (and the branch, if this
  launch made it) and blocks the task: `post_create step failed (CMD): LAST 300 CHARS`.
- When built: `fm/repos.py` in the core, pure (parse, validate, the view below, the layout check); the commands run in
  `agents.worktree` (a service). No port: nothing varies that `providers/` does not already cover.

### One task, one PR, one repo

- A task's repo is its `repo` key (a `[repos.K]` key). The project hands every step a **view** of itself for that repo:
  `Project.for_task(i)` returns a `Project` whose `[project] repo`/`main`/`remote`/`worktrees`, `[merge]` and `[risk]`
  are the repo's, merged by the rule above; home, state, agents, guard, resources and backlog are the project's. The
  code that reads `p.repo`, `[merge]` or `[risk]` today does not learn about repos: the loop calls `for_task` once per
  task step. A task with no repo (a task that has subtasks, a loop finding) gets the project as it is, that is the first
  repo.
- Choosing: `fm add TITLE --repo KEY`; `fm edit ID --repo KEY` (triage, backlog, ready; on backlog or ready it drops the
  plan and returns to backlog, as criteria do, because a plan is about one repo). With several repos, `fm add` of a task
  with criteria and `fm go` refuse one without repo: `fm: FN-3: choose its repo first (fm edit FN-3 --repo
  app|api|site|emails)`; in triage it may have none. A task that has subtasks needs no repo (each subtask has its own):
  `fm go` lets it in without one, and it shows `-` as its repo. An unknown key is refused, naming the keys. A health
  finding about one repo carries it (main's tests, an orphan worktree); the loop's own findings carry none and are never
  let in as they are (the mechanic diagnoses, the owner files the real task).
- Showing: `fm show` prints `repo`; `fm list` adds a repo column between status and agent; the panel's `state` and
  `task` data carry `repo` per task, and `state` carries `repos` (the keys).
- **Only with more than one repo.** With one repo the key is never stored, asked, listed or sent, and `--repo` is
  refused (`fm: this project has one repo`).
- A change across repos is a parent with one subtask per repo (`fm add --parent`), ordered with `--blocked-by`: the
  dependent subtask launches once the first is merged. No PR links across repos, no merge of several repos at once.
  A planning agent that finds a task needs two repos runs `fm block` with that split proposed.

### Each moving part

| Part | Answer |
|---|---|
| Worktrees | `<the repo's worktrees folder, else the project's>/<id>`, made from the repo's checkout; the planning agent runs in the task's repo checkout (read only); parent and mechanic agents keep the first repo as cwd |
| Branch names | unchanged, `<branch_prefix><id>`: ids are unique in the project and a task has one repo, so a branch lives in one repo only |
| PR | per repo: provider (`auto` looks at that repo's `remote`; `"none"` is `local`), `pr` mode, push to that `remote`; draft PR, description, review comments and `fm-evidence` go to the task's repo |
| Merge | per repo `strategy`; the local provider merges into that repo's main checkout; the merged check runs on that repo's main. Merges stay one at a time for the whole project |
| Tests and CI | per repo `test`, in a detached tree of that repo (`state/trees/<id>`); no `test`: the PR's CI stands in and the doubt is medium, as today. Main's tests (health) run per repo that has a test command: `state/ci-<key>.json`, tree `main-<key>`, finding key `ci:<key>:<since>` (one repo keeps `ci.json` and `ci:<since>`) |
| Risk | the repo's tables and `large_lines`. `publish_on_merge` makes the impact high with the reason `merging publishes this repo (KEY)` (a fact of the score, `publishes`), so only the owner's accept merges it. fm still never tags or releases |
| Autonomy | the repo's, else the project's; `high` never merges alone, as today |
| Review | unchanged: hunter and reviewer start in the task's worktree, so they are per repo by their cwd |
| Health checks | orphan worktrees in every repo's worktree list; main's tests per repo; new: a repo path that is not a git checkout, a task whose repo key left fm.toml (blocked at launch with that reason), fm or Claude files in a repo (below). `fm health`'s «runs in the first one» note goes |
| Slots | one count for the project (`[agents] slots`), one queue by priority, then ready before backlog, then id, as today, whatever the repo; no per-repo cap. Checks and merged checks stay one at a time for the project |
| Locks | `[resources.*]` stay project-wide and machine-wide: a simulator or a port is shared by every repo; the brief lists them for every task |
| Guard | `push-main` covers every repo's `main`; the rest as today |
| A project with one repo | nothing above shows: the `[project] repo` form is untouched; a project with one `[repos.K]` table has an implicit repo (no key stored) whose `[repos.K]` settings every task gets; same files, names, columns and text, and no layout check |

### The umbrella and files kept out of repos

- The umbrella (`[project] umbrella`) holds repos cloned inside it. It is the owner's own folder: it may be a git repo
  of its own, with its own `.claude/` and `CLAUDE.md`, and may hold the home and the worktrees folder (for instance
  as ignored top-level folders). What stays clean is each repo of `[repos.*]`: no fm or Claude file goes in one.
  Checked at load for a project with more than one `[repos.*]` table (`fm: the home is inside repo app: …`): the
  home, `state/`, every worktrees folder and the registry file are outside every repo; being inside the umbrella is
  fine. A one-repo project (`[project] repo` or one `[repos.K]` table) is not checked (unchanged).
- fm writes into a repo only what git keeps out of `git status`: the entries in `.git/worktrees/` of task worktrees
  and of the detached test trees (`state/trees/<name>`, in the home), the refs `<branch_prefix><id>` and
  `fm-evidence` (written by git plumbing, without touching a checkout), the local provider's merges into main, and
  after a GitHub merge a `git fetch` and `merge --ff-only` of main when the checkout is clean on main. Everything
  else lives in the home: agent settings inline on the command line, plans, reports, reviews, the test trees.
- Safety net, because fm cannot prove that a Claude session started in a repo (planning, parent, mechanic) writes
  nothing there: a health check lists, per repo, the untracked and ignored paths (`??` and `!!` lines of `git status
  --porcelain --ignored=matching --untracked-files=all`, so a file a global ignore hides is seen) with a component named
  `.claude`, `CLAUDE.md`, `CLAUDE.local.md` or `fm.toml`, and files one finding. It looks only inside the repos, never
  at the umbrella. Tracked files are the repo's own and are not reported; a folder ignored whole (dependencies) is not
  looked into. The matching is a pure function over paths, in `fm/repos.py`.
- The owner's own chat in the umbrella is the plugin's (FN-144 subtask 2), not this.

### Left out on purpose

- Per-repo slots or locks: one queue is simpler, and the machine is what is shared.
- A check queue per repo (checks in parallel): one at a time is enough for one owner.
- Repos found by their git remote, and `fm init` for several repos: the owner writes the tables.
- A repo's own brief or agent models: the project's serve every repo.
- A setting for a GitHub repo with no CI: under autonomy `low`/`medium` it never merges alone and waits for the owner,
  as today; a `ci = "none"` key can come if needed.
- Merges across repos at once and PR links across repos: subtasks with `--blocked-by` order them.
- Moving a task to another repo after its plan: `fm edit --repo` does it and the plan is redone.

### Not kept from the old foreman

| Old | Why not |
|---|---|
| `github = "owner/repo"` | `gh` runs in the repo and reads its remote |
| `local_ci`, `ci_runs`, `ci_summary`, `main_ci` | tests run on the committed head at the check, not before a push; the setting is `test` |
| `remote_checks` | CI is the provider's |
| `require_approval` | it is autonomy `manual`, the default |
| `risky_paths` as one list with `file:text` entries | labelled path tables; the file is the unit, text in a line is not matched (`package.json` goes in a medium table) |
| `review`, `review_paths`, `review_lines` | every branch is reviewed; size is `large_lines` |
| `public`, `privacy_scan`, `pre_merge_report`, `post_merge`, `plan_check_heading` | nobody asked; each can be its own task |
| the `worktree` template with `{repo}-{name}` | a folder key is enough: names carry no repo |
| `post_create` placeholders `{repo}`, `{name}`, `{branch}` | `{main}` and `{wt}` suffice |
| the folder of repos found by rule, with the home inside it | the umbrella is explicit; the home may sit in it, never in a repo |
| the backlog's projects with `repos` lists, `launch --repo` | a task has one repo and `fm go` launches it |
| `import-gh` and `Closes #n` | fm imports nothing from GitHub |
| `remote = "none"` merging by fast-forward after the local CI | the local provider merges with the repo's `strategy`, as today; when the repo has a test command, the merged check has already run the tests on the branch merged with main |

## Architecture

Hexagonal-lite: a small core, ports only where something external varies, everything else outside. Not Clean Architecture
nor full DDD; what is shared is a vocabulary: *task* (a unit of work in the backlog), *review round* (one review of a tip), *risk* (low,
medium, high), and *accept* (a record of the owner's decision) is **not** *merge* (the provider's act). The whole vocabulary is in `GLOSSARY.md`; it changes only by the owner's decision.

Layers, outside in (`tach.toml`; a module imports its own layer and the ones below, never above):

| Layer | Modules | Role |
|---|---|---|
| ui | `cli*`, `panel*` | the owner's and the agents' entry points; call the loop and below |
| loop | `loop` | the process that ticks: does the I/O, calls the core |
| steps | `sources`, `schedule`, `talk`, `mechanic`, `health`, `metrics` | one step of a tick, or a view over several pieces |
|| services | `agents`, `delivery`, `pulls`, `sessions`, `lock` || the outside world: git, gh, claude, processes |
| registry | `providers/registry` | picks the adapter (by convention the only caller of the adapters; not enforced by tach) |
| adapters | `providers/github`, `providers/local` | one per platform; they see the port, never the loop or each other |
| ports | `providers/__init__` (the `Provider` interface) | |
| core | `risk`, `decide`, `subtasks`, `tasktypes`, `recurring`, `recurring_table`, `reviews`, `blockers`, `diagnosis`, `guard`, `backlog`, `costs`, `config`, `repos`, `util` | vocabulary and pure rules; `risk` (score, autonomy) and `decide` (what the loop does with a branch in review) have no I/O at all |

Ports: only where there is an external system or two real implementations: `providers/` (local, github), and the
launcher and source plug-ins (`sources`) when they exist. Not behind a port, because they do not vary: `backlog.json`,
the config, the clock. A new port needs a second real implementation or a system outside the process; say which in the
task.

Rules (enforced by `make check`):
- The core imports nothing above it. **One module runs programs**: `fm/proc.py` (`run`, `run_json`; it knows
  nothing of git, gh or claude; a missing program or a timeout is a `ProcError`). Everything else calls it; ruff bans
  `import subprocess` elsewhere (not `os.system`/`os.popen`). `proc` sits just above the core in the layers: the loop,
  the services and the adapters use it, the core does not. The only other exceptions in `per-file-ignores`
  (`pyproject.toml`) are the tests and `fm/lock.py`, which needs a process group and signals to stop a whole job at its
  time limit (`start_new_session`, `killpg`).
- Data from outside is typed at the border: `providers/github.py` turns `gh`'s JSON into its own types
  (`PullRequest`, the check buckets) with a small converter per shape, and a shape that does not fit is a
  `ProviderError`; only those types go inward, never `gh`'s dicts. The port's own operations take a `Change` and
  return `Published` / `ChangeState`: no dict crosses it.
  The ratchet also counts `pylint: disable`, `pyright: basic|off` and `fmt: off|skip`.
- An adapter imports neither the loop nor another adapter (tach).
- Pure decisions (risk, autonomy, state rules) go in the core as functions of their inputs; the loop and the services
  gather the facts (git, gh, claude) and apply the result. New decisions go there, not into `delivery` or `loop`.
- `fm/decide.py` runs no program and touches no file: tach lets it import only `risk`, and `tests/test_decide.py` rejects
  any other import and any `open`/`exec`/`eval` in it.
- Files stay under 300 lines (`make size`, no exceptions): a module that grows is split along these layers.
- Suppressions do not work: `make lint` runs ruff with `--ignore-noqa`, pyright has `enableTypeIgnoreComments = false`, and `make suppressions` fails on `pyright: ignore` and `pylint: disable`.
- Loosening a rule (`tach.toml`, the ruff ignores, `pyproject.toml`, the `suppressions` target) is the owner's call.

The panel's TypeScript is out of this (a dependency check there can come later).

## Watching itself

Metrics per task (`fm metrics`, the panel): the model and effort its responsible agent ran on, cost of its agents (transcripts at list prices; planning_usd of planning sessions and responsible_usd of responsible sessions, from `session_kinds`; review rounds), time to review and to
done, rework (rejected), conflicts and failures (returns for failing tests or CI), nudges (stopped without finishing),
questions to the owner, guard stops, risk, and what misled its agents (their `--misled` words, per agent).

Health checks (`fm/health.py`), each finding a triage task, deduplicated by key while open or while it persists:
loop dead (heartbeat older than 3 ticks; from outside the loop: `fm health`, the panel), agent stuck (working, its
transcript silent for `stuck_minutes`), agent blocked (stopped after a nudge, or could not launch), orphan worktree,
tests failing on main (the test command on main's head, when it moves or every `ci_every_minutes`; one task per failing
episode, from the first failing run until a pass, with the commit, the failing step and the output tail), loop step errors.
Every finding has three parts: what it means, the evidence fm gathered, and a hypothesis marked pending; none proposes a fix.
