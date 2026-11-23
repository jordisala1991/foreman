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
`[repos.<key>]` tables (`path`, `main`) instead of `[project] repo` (both forms: fm refuses). A project owns its home, every repo, its worktrees
folder and the umbrella folder set in `[project] umbrella` (explicit, never guessed). A command finds its project by `--project
NAME`, `--home`, `FOREMAN_HOME`, `FOREMAN_PROJECT`, then the registered project owning the current folder (paths resolved, the
deepest folder wins; two projects claiming it: fm refuses and `foreman health` names them), then the nearest `foreman.toml` above
it. Every setting is per project: `[agents] slots` (how many at once), models, extra session settings, `runner` (`claude`, the default); `[merge]
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
- `blocked`: a reason set by an agent (`foreman block`), by the loop (could not launch; its agent stopped without finishing,
  twice in a row: the recover step). The owner clears it (`foreman unblock [--relaunch]`); a plain unblock of a running task with
  an agent also tells the agent the block is cleared (a message the loop delivers) and starts a new run of its session.
- `blocked_by` (`foreman add/edit --blocked-by FN-n …`; `none` clears): the task is never planned or launched until every
  blocker is `done`, then the loop marks it unblocked in the history. A canceled or duplicate blocker blocks the task
  with a reason: the owner edits `--blocked-by` or cancels, then `foreman unblock`.
- `type` (`foreman add/edit --type change|bug|mechanical|definition`; default and every task without one: change) only
  changes the instructions of the task's agents, one paragraph per type; a parent has none.
- `foreman edit` changes title, goal, criteria, detail, model, effort, type, blockers or max steps (a chain parent's cap) of a task in triage, backlog or ready (never one with an agent,
  nor a closed one, except a chain parent's agent running, for `--max-steps`); criteria or detail on a backlog or ready task drops its plan and returns it to backlog.
- A task too big for one PR is a parent with subtasks (`foreman add --parent FN-n`, only while the parent is in triage or
  backlog; one level). A subtask is an ordinary task (own agent, branch, PR) that names its parent. A parent in triage
  closes by itself when every subtask has (done if any is done, else canceled). Once let in (backlog) it gets a parent
  agent (`agent.kind` `parent`, no worktree, status stays backlog): it is told each subtask merged or canceled and
  nothing else (message kind `subtask`, naming the subtask in its `subtask` field), proposes the next Go and changes to the owner, asks him with `foreman ask` what only he can decide (each Go and change as a proposal: the exact action, a recommendation and why; it ends its turn, an answer of `yes` (`questions.is_yes`) is its order to run exactly that action, any other answer a reply that runs nothing; a yes typed in its conversation counts the same), may
  file (`--triage`) and edit only its own subtasks, and closes the parent with `foreman done` (refused while a subtask is
  open) after the owner's yes (an answered `foreman ask`). It runs only when something for it happens: a wakeup is an undelivered message of the
  parent (a subtask closed, the owner's answer, an owner edit), delivered by a resume of its idle session or by a new
  session briefed with where it stands; one run per wakeup, and a run ends done when its session idles 60 s. Between
  wakeups no parent session is expected alive. `foreman cancel` of a parent cancels its open subtasks and closes their PRs. A `lens` entry files a chain: see Improvement chains.
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
   filled in from its short id on a later tick. `foreman relaunch` is the one resume that sends flags, on purpose: a
   copy under a new id with today's `--settings` (PATH, guard, env), recorded as the task's session.
4. **recover** (`foreman/steps/recover.py`, its rules pure in `foreman/core/tasks/recovery.py`): each active run (the ledger) of
   an open task that is not blocked is looked at in the runner's listing; a task waiting (an open question, a message on its
   way) is left alone, except that a run ending its turn by design is done waiting or not (below). A run is over with no outcome for one reason: its session is ENDED or GONE (`session_ended`, or
   `runner_unavailable` when the runner's epoch changed since the last pass, kept in `state/runner.json`), it never got a session
   id within 2 minutes (`launch_failed`), or it is idle and its transcript quiet for 10 minutes (`silent_too_long`); a run that
   ends its turn by design (a parent's or chain's, idle and quiet for 60 s: `turn_over`) is done, never `silent_too_long`. Every parent run ends in a delivery, a question, a block or an open subtask; anything else is a need: when that run ends done with the task open, no open subtask, no open question or undelivered message and no block, the step files an open question of kind `stopped` whose text is the run's last words (1000 characters), so `foreman answer` or `foreman edit` wakes the parent as any wakeup does (FN-422).
   The run is closed as failed with its reason. `session_ended`, `runner_unavailable` and `silent_too_long` are retried while fewer than 2 runs of that kind
   failed in a row: the session is resumed with a message naming the reason and how to finish (a new run of it, attempt + 1);
   when the runner cannot resume it, the task goes back through the launch path of its kind. Past that, or for `launch_failed`,
   the owner gets one need: the task is blocked with «its agent stopped without finishing (reason, attempt n); it last said: …»
   (the last text of its transcript), or a mechanic leaves its «Diagnosis missing» note. An unreadable listing does nothing;
   it runs right after `answers`, on a listing read again (an owner's answer or unblock resumes a session first), and on a
   loop's first pass after a restart before any launch. A hunter whose findings file is written is not judged: the queue step
   closes its run done. The launch path by kind: a planning or responsible task goes back to `backlog` or `ready`; a hunter's,
   reviewer's or mechanic's record is dropped so its step launches it again; a parent gets a new session told where it
   stands, staying in backlog.
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
   never launched here: just before, a let-in parent gets its parent agent regardless of slots (`foreman/steps/parents.py:35`, launching through `launch_parent`, `foreman/services/agents/launch.py:99`). The parents step records a wakeup (`parents.wake`) for each closed subtask no message of the parent names yet; the brief of a new session names the closed subtasks it shows (delivered messages), so they wake nothing. A parent whose session ended (ENDED or GONE in the listing, never an unreadable one) gets a new session only when a wakeup is pending, briefed with where it stands (`chains.standing`: chain step, subtasks, notices it missed); an idle one is resumed with it (`answers`, a new run). `foreman edit` of a parent files it a message (kind `edit`) with what changed: a wakeup like the others. `foreman relaunch` of a parent whose session ended is refused (no live agent): its next wakeup starts one.
   An agent waiting for the owner (open question or permission, no answer on its way) does not count against the slots;
   an answer is delivered at once, even over the slots.
   A pause stops this step, the parent agents and the mechanics; reviews, checks and merges go on.
9. **mechanic**: for each triage task health filed (`source` `health:*`) that has none yet, one mechanic session, in the
   slots launch left free (it counts as a slot while it has no diagnosis). It stops once the diagnosis is in, and a mechanic
   that ran over 30 minutes leaves a «Diagnosis missing» note; one that ended or went silent is the recover step's (resumed
   once, then the same note).
10. **folders** (after mechanic): under Orca, removes an agent folder once its agent finished its part and its session is not in use,
    and every folder of a closed task; a failure is a line in `errors.jsonl`, tried next tick (`foreman/steps/folders.py`).

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
| A delivered change or bug task whose `reports/ID/session.md` is missing, or has no command (a fenced code block) and no screenshot, before its hunter launches | returned to its agent with that reason (kind `session`); mechanical and definition tasks are not asked | `session.gap`, `tasktypes.wants_session` | `test_session_return.py` |
| A tip with no review, in rounds 1-2 | launches the hunter if a slot is free; else waits | `review_step` → `hunt` / `wait` | `test_no_hunter_launches_it` |
| The hunter's file is written and the hunter is not working | launches the reviewer if a slot is free; else waits | `review_step` → `review` / `wait` | `test_hunter_file_and_ended_launches_reviewer` |
| The hunter stopped before its findings file, or the reviewer before a verdict | the recover step resumes it, or drops its record so this table launches it again; the second time in a row, blocks the task | `steps/recover.py` | `test_a_hunter_that_ends_without_its_file_is_resumed`, `test_a_session_the_runner_cannot_resume_is_started_again` |
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
| An agent folder under Orca | goes once its agent's part is over (task ready, hunter's file or verdict written, diagnosis recorded) and no session of it is busy or recently active; every folder of a closed task goes when none is busy | `folder_goes` | `TestFolderGoes`, `test_a_done_parents_folder_goes_on_the_next_tick`, `test_a_busy_or_recently_active_session_keeps_the_folder` |

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
conversation: each finding gets the smallest change that settles it, or a one-line answer if it thinks the finding is wrong; add a `### Round N` to the report's «Review rounds» section, `foreman done` again; a fix that changes behaviour a session step shows has that step rerun and `session.md` updated first, and the Round N section says which steps were rerun.
A project's home may hold `verify.md`, its recipe for using the product live in a throwaway setup; the responsible, hunter and
reviewer briefs carry its path when it exists (`brief.verify_section`). Before the report, the responsible agent of a change or bug
task uses the feature through it (or the product's own commands without one) and saves `<home>/reports/ID/session.md`, each
command with its real output and each screenshot with what it shows; the report's Proof table has an «In use» column pointing
to the session step that shows each criterion line. The loop returns the delivery to its agent (kind `session`) before the hunter
launches when that file is missing or has no command in a fenced code block and no screenshot. The hunter of a change or bug task repeats that session in a fresh setup on the
tip and reports as Wrong each step whose output or screenshot differs, a missing session, or an «In use» step the session lacks;
the reviewer confirms such a finding by rerunning the step. In round 2 or a scoped review the hunter repeats only the steps the fixes touch, plus any step a still-open finding names.

**Review: hunter, then reviewer** (`foreman/briefs/hunter.md`, `foreman/briefs/reviewer.md`): launched by the loop, not by the responsible agent, each as
its own session in the branch's worktree, once per delivered tip; each counts against the slots (the hunter until its file exists, so its own reviewer can take its place). Both get the task's detail
and the owner's notes. The **hunter** lists everything it suspects (with a confidence each, plus the acceptance criteria table and an
inventory of what the diff adds) in `<home>/reviews/ID-rN-<tip>-hunter.md`. When that file exists and the hunter has ended,
the **reviewer** starts; it sees only the file, never the hunter's reasoning. It checks each Wrong or Missing finding
(confirmed, refuted, or neither) and each Too much item; a finding leaves only with a cited line or output that refutes it,
so what it could not refute stays in To fix (except a case nobody asked for, below). What was dropped goes under Noted and is not sent to the responsible agent. It writes
`<home>/reviews/ID-rN-<tip>.md` (header `Branch/Tip/Round/Open`, N = items under To fix) and runs
`foreman verdict --tip --open N --file`; a verdict for a tip other than the newest launch is stored in the review history but
decides nothing. There are no severities. Open findings in round 1 → back to the responsible agent (`decide.review_step`);
round 2 checks the fixes only and opens nothing new unless a fix is itself Wrong or Too much; what is still open after
round 2 goes to the owner as high risk. A hunter that ends without its file, or a reviewer without a verdict, blocks the task.
Each review session gets its own `<home>/scratch/ID-rN-<tip>-ROLE/`, told in its brief as `$scratch`; the loop removes it on the first tick where no live session owns it.

**Cases nobody asked for**: one shared rule, `brief.UNASKED` in `foreman/services/agents/brief.py`, rendered through the
`${unasked}` slot in the planning, responsible, hunter and reviewer briefs: before adding handling for a race, an edge case, a
bad input or a security case that no acceptance criterion asks for, or sending one to be fixed, state its concrete failure path
(the input or event, how likely, what it costs); a path reproduced with ordinary input through the project's own commands is
real, and a real case is handled where the bad value enters, not at the crash site; no compatibility shims, dual paths,
fallbacks or legacy adapters unless a criterion asks. Each brief follows the rule with its own role line: planning and
responsible leave a case without a path out; the hunter still reports every suspicion, with its path or «path: none»; the
reviewer sends an unasked case to Noted only when no path was reproduced, and a reproduced one stays in To fix naming where the
bad value enters (outside the diff: a separate task, not a guard here).

The **mechanic** (`mechanic` kind, the task's `mechanic`) diagnoses a health finding and changes nothing else: read-only by its
brief (as the hunter's), its only write is `foreman diagnose --file`, which checks the required `##` headings (`foreman/core/oversight/diagnosis.py`)
and appends the diagnosis to the task's `detail`, so `foreman show` has it. The task stays in triage; the owner decides next.

## Agents through Orca (FN-204)

Built in FN-216 as «Chat view or terminal tab (FN-218)» says; D1, D3-D7 and D9 differ there. Defined in FN-204: FN-203 wants every agent of a project set to `runner = "orca"` to be an Orca tab the owner can open
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
| Orca restarts | On a second, headless Orca (tabs of `terminal create`): the agent kept running, its handle stayed valid and its state came back. On the owner's desktop Orca (FN-217, chats of `worker-start`): quit with the app, the chat ended with it (inferred from the process list) and the next message resumed the same session (see «Seen in a real project (FN-217)») | `kill -TERM` of `orca serve`, then `serve` again on the same port: the claude process kept its pid; `terminal show` → same handle, `connected: true`, `writable: true`; a message after it → `turn_started` and answered (E7, E8); the row went `working` (`toolName: Bash`) then `done` (E8); `conversationName` was lost in E7, kept in E8 |

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
  `ui/panel/views.py:66`; the panel's agent line gains a «waiting» label: `panel/src/cards.tsx`, `panel/src/strings/*.json`, `lib.ts`); `foreman list` and `foreman show` print neither. Written once, marked
  (`agent.waiting_seen`, `steps/talk.py`), cleared when the agent leaves WAITING.
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
wraps each message («From: TERM_… Subject: …»); a send to a live chat returns only a `messageId`, and only a send after an Orca restart returns a new `dispatchId` (FN-217 on the desktop Orca: see «Seen in a real project (FN-217)»). **Restart** (on a headless Orca): chats die with Orca
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
  returns only `messageId`; a send after an Orca restart returns a new `dispatchId`, which replaces `D` (F16; FN-217: see «Seen in a real project (FN-217)»);
- D5 → `worker-stop --dispatch D`, and `worker-release` when it answers `alreadySettled`;
- D6 → not needed for chats; D7 → a restart ends every chat until its next message (F8, F16);
- never write `.claude/settings.local.json` into a repo's main checkout, and leave Orca's host settings (command override, agent env) alone (F3, F12).

### Seen in a real project (FN-217)

A sandbox task (`runner = "orca"`) ran planning, responsible, hunter and reviewer as chats of the project's coordinator tab. After the owner quit the desktop Orca, the next message (a loop send) was delivered to the same session; measured from that send, Orca was open again within 31 s and the message arrived at 32 s (who opened Orca is not shown). The chat ended with the app (inferred from the process list). The kept dispatch was the same before and after, so that answer carried no new `dispatchId` (inferred, the raw answer was not seen: the loop keeps a changed one, `services/agents/running.py:74-78` and `providers/orca/sessions.py:66-67`). The owner's desktop message was not sent, by the owner's choice. Details and files: `reports/FN-217/evidence.md`.

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

From what foreman used in the claude sessions module (now `providers/claude/`; line numbers are from before the move) (`command` :68, `start` :102, `resume` :108, `listing` :134, `state` :148,
`alive` :154, `busy` :160, `stop` :172, `find` :97, `SessionError` :31) and its callers (`services/agents/launch.py`, `running.py`,
`steps/mechanic.py`, `parents.py`, `reviewing.py`, `talk.py`, `services/delivery/merge.py`, `loop/queue.py`, `tick.py`,
`ui/panel/views.py`, `ui/cli/ops.py`). Two real implementations, so a port is allowed. Package `foreman/providers/`, module
`runner.py` (layer ports); adapters `providers/claude/` (today's claude sessions module and `login.py`, moved) and
`providers/orca/`; the registry hands it out (`registry.runner(p)`), as it does `registry.repo(p)`.

```
class State(Enum): WORKING, WAITING, IDLE, ENDED, GONE            # today: working|blocked, —, done, stopped, gone
@dataclass(frozen=True) class Agent: tid, name, model, effort, title                      # unchanged
@dataclass(frozen=True) class SessionRef:                                                 # what the backlog's agent keeps of its session
    short: str; session: str | None                                                       # orca: session known at once; claude: from the listing, maybe None
    terminal: str | None = None; pane: str | None = None                                  # orca: the tab's handle and paneKey; claude: None
    started: str | None = None                                                            # the agent's `started` (launch, each send): D3's start limit
    dispatch: str | None = None; coordinator: str | None = None                           # orca: the worker's dispatch id and the coordinator tab (FN-216)
class Rows(Protocol):                                                                     # one snapshot per tick; replaces dict rows
    def state(self, ref: SessionRef | None) -> State
    def busy(self, ref: SessionRef | None) -> bool                                        # WORKING or WAITING
    def alive(self, ref: SessionRef | None) -> bool
    def find(self, short: str) -> str | None
    def short(self, session: str) -> str | None                                          # claude: the listing's id (today merge.py:117); orca: session[:8]
    def has_sessions(self) -> bool                                                        # the snapshot holds a session: an empty listing is no listing (recover.run)
    def epoch(self) -> str                                                                # who runs the sessions now: changes when the runner restarts (orca: app pid); claude: ""
class Runner(Protocol):
    def start(self, agent: Agent, brief: str, cwd: Path) -> SessionRef
    def send(self, ref: SessionRef, message: str, cwd: Path) -> SessionRef                # today's resume; orca: a new tab when ended
    def rows(self) -> Rows | None                                                         # None: unreadable (today {}); callers do nothing, as today
    def stop(self, ref: SessionRef) -> None
class RunnerError(Exception); class RunnerUnavailable(RunnerError)                        # SessionError becomes RunnerError
```

`stop_live` and `settings` stay shared (they use only the above). No dict crosses the port: where a caller reads
`rows.get(sid).get("id")` (`merge.py:117`) or `row.get("status")`, it gets `Rows`. The backlog's agent keeps the fields of
`SessionRef` (`started` as today). The task's `sessions` list keeps its shape (session ids, `launch.py:87`), so its readers `ui/panel/views.py:83`,
`steps/metrics.py:79` and `merge.py:117` read it as today; next to it, as `session_kinds` is, a new map `session_tabs`
(session id → `[terminal, pane, dispatch, coordinator]`; FN-213's entries have the first two) is written by `launch.record_session` (`launch.py:85`) from the `SessionRef` `start` or `send` returned,
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

## The backlog on GitHub (FN-234: defined; D2, D3, D4 and D6 built)

FN-140 moves a project's backlog from `<home>/backlog.json` to the issues of its repo and one **Board** (a GitHub Project v2).
This section is the definition: where every key of a task goes, the statuses, the ids, the calls a pass makes, where the
questions live, and the subtasks that build it. D2 (the port), D3 (`[backlog]`, `foreman backlog setup` and the reads, in
`providers/github/`: api, board, issues, backlog) and D4 (the writes: one diff-based `edit()` read back, in apply, writes and files;
the pure parts in `core/tasks/`: changes, drags) and D6 (`foreman backlog cutover --repo OWNER/NAME --project N [--owner LOGIN]
[--dry-run]`: the rules in `core/tasks/cutover.py`, the filing in `providers/github/cutover.py`) are built; for a project still on
its local file (the sandbox, the tests, fm until its cutover) the lines about `backlog.json` elsewhere in this document stay true. The evidence log is `<home>/plans/FN-234-evidence.md` («E1»-«E8»); the reference design (how the
reference workspace keeps its backlog on GitHub) is `<home>/plans/FN-140-reference.md`.

### Decisions

The owner's chat for FN-140 (2026-11-21), one line each:
- `decision.truth`: GitHub is the source of truth for what a task is: title, goal, criteria, detail, status, type, model, effort, priority, parent (native sub-issues), blocked-by (native issue dependencies).
- `decision.where`: the issues of fm's repo and one Board of the owner for fm; set per project in `foreman.toml`; the old foreman's own backlog is not touched.
- `decision.local`: what an agent run produces stays local in the home, keyed by the task: agent, sessions, branch, worktree, check, report, rework, misled, review files, events, metrics.
- `decision.questions`: questions to the owner stay in foreman (answer/allow/deny, panel); while one is open the task's GitHub issue carries a label so the Board shows it.
- `decision.port`: a backlog port with two implementations: the local file (projects without GitHub settings: the sandbox, the tests) and GitHub.
- `decision.reading`: a cheap read each pass and a full read only on change; rate limits stop calls until the reset, never a retry loop; a list over its limit is an error, never a silent drop (the cheap read is replaced by FN-234's `decision.reading` below).
- `decision.writing`: every write is read back; Status set on the Board; close = done, closed as not planned = canceled, duplicate = duplicate.
- `decision.claim`: one loop (this Mac) owns the Board for now; multi-person claiming is out of scope.
- `decision.panel`: the panel keeps its API and reads through the port; the Board is an extra view, not a replacement.
- `decision.cutover`: one switch: open tasks become issues (their old id written in the issue), closed tasks stay in the old file kept read-only, then `foreman.toml` points fm at GitHub.
- `decision.ids`: left open for this definition (PRs share the issue numbering); answered below.

The owner's answers to FN-234 Q2 (2026-11-21, «all five as you recommend»):
- `decision.ids`: choice A: a task's id is `FN-<issue number>`; migrated open tasks keep their old id through a map; the cutover refuses on a collision; `foreman show` on a PR's number says so. Dropped: B, an fm counter in a title prefix (it needs allocation and repair of edited titles), and a Board `Id` field (invisible in issue lists).
- `decision.reading`: one full read per pass (`first:50`), no separate peek; more than 50 open tasks is an error.
- `decision.workflows`: 9 Status options; the setup command deletes the Board's workflows (on the real Board too); fm moves every card itself and reads it back.
- `decision.blocked`: a `Blocked` text field; the label `needs-owner` for an open question; a PR says `Task: #n`, never `Closes #n`; fm closes the issue: done = completed, canceled = not planned, duplicate = duplicate.
- `decision.term`: **Board** joins `GLOSSARY.md`.

And this definition's own:
- `decision.config`: `foreman.toml` gets a table `[backlog]` with `kind = "github"`, `repo = "owner/name"`, `project = <Board number>` and an optional `owner` (the Board owner's login, default the repo's owner). A project without the table keeps its local file, so the sandbox and the tests change nothing.
- Field and option ids are found by name when the loop starts and again when a write names one that is missing: the names are the contract, no ids in `foreman.toml`.

### How it was tried

gh 2.104.0 with a token holding `project`, `repo` and `delete_repo`, on a private scratch repo and a scratch Board titled «fm scratch (remove)»,
filled with 230 task-shaped issues (19 open, the rest closed as done or canceled; 64 sub-issue links, 21 blocked-by links) plus a probe, closed orphans and write trials: 247 issues in all (E, header). What the API allowed and refused (E1):
- A new Board has Status Todo/In Progress/Done and 6 workflows. `updateProjectV2Field(singleSelectOptions:…)` replaces the Status options (the option ids are new); `createProjectV2Field` adds the others. **Reserved names are refused: `Type` and `Repo`** (hence `Task type`, `Repo key`).
- Workflows are listed by `projectV2.workflows` and can only be deleted (`deleteProjectV2Workflow`): no mutation enables or edits one. All six were deleted. Before that, closing an issue left Status empty (the workflow's target option was gone with the replaced options): they are not relied on.
- The label: `gh label create needs-owner --color d93f0b --description "an open question to the owner" --force`.
- Reads of a node (`node(id:)`, `repository.issues`, `issue(number:)`) see a write at once; **`projectV2.items` lagged ~4 s after `addProjectV2ItemById`**, so tasks are never listed through the Board's items (a task just filed could be missing and a finding filed twice).
- A field change updates the Board item's `updatedAt`, not the issue's.
- `issueOrPullRequest(number:)` tells a PR (`__typename: PullRequest`) from an issue and from nothing (null, `NOT_FOUND`); `issue(number:)` of a PR's number is `NOT_FOUND`. fm's repo has 0 issues and 271 PRs today: its next number is #272.
- `repository.issues(...)` returns the sub-issues (`parent{number}`, `subIssues{totalCount nodes{number state stateReason}}`) and the dependencies (`blockedBy{…}`) in the same query. Writes: `createIssue`, `addProjectV2ItemById` (returns the item id), `updateProjectV2ItemFieldValue` (several aliased in one request), `updateIssue`, `addSubIssue`, `addBlockedBy`, `addLabelsToLabelable`, `removeLabelsFromLabelable`, `closeIssue(stateReason: COMPLETED|NOT_PLANNED|DUPLICATE)`, `reopenIssue` (`DUPLICATE` run: `state CLOSED, stateReason DUPLICATE`).

Kept from the reference design (E4): one issue per task, body sections, the Board's Status as the only status, native sub-issues and blocked-by, no silent pagination,
a rate limit stops calls until the reset, every write read back, a body edit reads fresh, writes and reads back. Changed: no Auto-add and no workflows (fm adds the item itself),
no separate peek, ids found by name. Not taken: assignees as the start button, a chat thread per task.

### Every key of a task

Every key of `core/tasks/backlog.py` (its docstring and the TypedDicts `Agent`, `Acceptance`, `Report`, `Check`, `NewTask`, `Chain`), of the 234 tasks
in fm's `backlog.json` and of the code's writes (E5). Board fields the setup command creates (single-select unless said): `Status` (9 options, «The statuses»),
`Model` (haiku, sonnet, opus, fable = `config.MODELS`), `Effort` (low, medium, high, xhigh, max = `config.EFFORTS`), `Priority` (urgent, high, medium, low; unset = `none`),
`Task type` (change, bug, mechanical, definition; unset = change), `Repo key` (text), `Source` (text), `Blocked` (text), `Lens` (text), `Max steps` (number), `Max lines` (number).
One label: `needs-owner`.

| Key | Goes to | Detail |
|---|---|---|
| `id` | issue number | `FN-<number>` (prefix from `[project] prefix`); migrated open tasks keep their old id («Ids») |
| `title` | issue title | |
| `acceptance.goal` | issue body `## Goal` | |
| `acceptance.criteria` | issue body `## Acceptance criteria` | one `- ` bullet per line of the text |
| `detail` | issue body `## Detail` | to the end of the body; the `decision.key=value` lines stay in it as text |
| `status` | Board field `Status` + issue state | «The statuses» |
| `blocked` | Board field `Blocked` | the reason; empty = not blocked |
| `priority` | Board field `Priority` | unset = none |
| `created` | issue `createdAt` | a migrated task's old `created` is kept local (the issue's is the cutover's); shown in today's format (`util.now`) |
| `closed` | issue `closedAt` | when fm closes a task it keeps the read-back task in `<home>/state/closed.json`, in fm's own format so the metrics and the panel keep working (`created` the `util.now` stamp, `%Y-%m-%d %H:%M:%S`; `closed` the date, as `core/tasks/backlog.py:205` writes it): with the old file, the source of closed tasks for the panel's recent list and the metrics (`ui/panel/views.py:81`, `steps/metrics.py:98`), as the pass reads only open issues |
| `model`, `effort`, `type` | Board fields `Model`, `Effort`, `Task type` | unset = the project's default / `change` |
| `repo` | Board field `Repo key` | the `[repos.K]` key |
| `chain` {lens, max_steps, max_lines} | Board fields `Lens`, `Max steps`, `Max lines` | a task is a chain parent iff `Lens` is set |
| `source` | Board field `Source` | the dedup key (`health:…`, `NAME:KEY`); dedup looks among the open tasks of the pass's read; a filing cut short before the fields leaves a card with no `Source`, so the finding is filed again (see «Reads and writes per pass») |
| `parent` | native sub-issue (the issue's parent) | a parent stores no list: its subtasks are its sub-issues; one level stays fm's rule; an open migrated parent's closed subtasks stay in the old file and the port adds them to its sub-issues (the old file's tasks whose `parent` is its legacy id) |
| `blocked_by` | native issue dependencies (blocked by) | a blocker's state and reason come with the read: closed `COMPLETED` = done; other closed reasons = closed undone; a migrated task's blockers already closed in the old file are read from there |
| open question | label `needs-owner` | on while the task has an open question; the text stays local |
| `agent` {name, short, session, cwd, started, terminal, pane, dispatch, coordinator, kind, model, effort, waiting_seen} | local | keyed by task id |
| `sessions`, `session_kinds`, `session_tabs` | local | |
| `branch`, `worktree`, `plan_file` | local | the plan is `<home>/plans/ID.md` |
| `report` {summary, review, confused, at, file}, `result` | local | `result` = the report summary kept at merge |
| `check` {tests, files, lines, risk, reasons, at, head, main, conflict, paths, commits, ci, review, tests_output, failed_checks, merged_with}, `failures`, `failure_streak`, `conflicts`, `resolving` | local | |
| `reviews` {round, tip, file, at, open, returned}, `review`, `reviewer` {name, short, session, model, effort, started, round, tip, cwd, file}, `reviewers`, `hunter`, `review_for`, `review_from`, `rework`, `misled` | local | review files stay in `<home>/reviews/` |
| `mechanic` {name, short, session, kind, model, effort, started, …}, `diagnosis` {at, file, missing} | local | `<home>/diagnoses/` |
| `pr` {number, url, draft, described, commented, …}, `pr_error` | local | the PR is found by its branch; its text says `Task: #<issue>` (never `Closes`) |
| `accepted` {by, head, at}, `merging`, `merge`, `commit`, `acceptance.verdict` | local | the owner's accept and the merge record, tied to a branch tip |
| `retry_count`, `retry_error`, `pr_count`, `check_retry_count`, `check_retry_error`, `released` | local | the loop's counters and once-flags |
| `plan`, `subs`, `defined_by`, `undelivered` (as a task key) | not carried | no code reads them; `plan` and `subs` are always `[]`; they stay in the read-only old file |
| file keys `schema_version`, `updated`, `projects`, `archive` | not carried | bookkeeping of the old file; `projects` and `archive` are empty in fm's `backlog.json` |
| `decisions`, `decisions_resolved` | local, `<home>/state/questions.json` | open questions; answered ones and the messages for an agent (kinds `note`, `subtask`, …); ids `FN-n Qk` unchanged |

Local state sits in `<home>/state/runs.json`, one object per task id holding the local keys above, written under the same file lock as today's file;
`events.jsonl`, `grants.json` and `seen.json` stay as they are.

### The statuses

| fm status | Board `Status` | Issue state | Who moves it |
|---|---|---|---|
| triage | Triage | open | filing without criteria (any filer, `core/tasks/backlog.py:165`); `foreman add --triage`; a parent agent's `foreman add --parent`; the panel when the project has several repos; the loop's health checks and sources always, recurring entries marked `triage`. A card with no Status (added by hand) reads as triage |
| backlog | Backlog | open | `foreman go`; filing with criteria (`foreman add`, the panel in a one-repo project, recurring entries not marked `triage`); `foreman edit` of criteria, detail or repo on a ready task (drops its plan); `foreman unblock --relaunch` of a refining task |
| refining | Refining | open | the loop (launch step, `services/agents/launch.py:82`) |
| ready | Ready | open | `foreman ready` (planning agent); `foreman unblock --relaunch` of an in-progress task. (`foreman add --ready` is declared and read by nothing: it files in backlog) |
| in progress | In progress | open | the loop (launch step); `foreman reject` and the loop's returns to the agent (`steps/talk.py:59`: review, conflict, failing tests or CI) |
| in review | In review | open | `foreman done` (responsible agent; a parent agent's `foreman done` closes its parent instead) |
| done | Done | closed, completed | the loop's merge step (`services/delivery/merge.py:90`) after the owner's accept or the autonomy; a parent that closes by itself or by its agent's `foreman done` with at least one subtask done (`core/tasks/subtasks.py:37`, `steps/parents.py:61`); a source's `done` |
| canceled | Canceled | closed, not planned | `foreman cancel`; the loop when a parent is canceled (its open subtasks) or a source says `cancel`; a parent that closes with no subtask done (same lines as done) |
| duplicate | Duplicate | closed, duplicate | nothing sets it today (no code path; none of the 234 tasks has it); the column and the mapping exist so a command can later |

No GitHub workflow moves anything: the setup deletes them all. `blocked` is not a status but the `Blocked` field. A close sets the issue's state
reason and the Status in one request. An open task's status is the `Status` fm last wrote, and fm moves every card. The pass's read and every command's write with its snapshot update
run under the backlog lock (`util.file_lock` on `<home>/state/backlog.lock` for a project on a Board), so the pass compares each card with a snapshot
no command changed meanwhile, and a command's move is never seen as a drag; with no snapshot yet (the first pass) no drag is detected and the snapshot is just written.
A drag is accepted only as a Go Triage → Backlog, with `foreman go`'s checks (`ui/cli/owner.py:141-148`: criteria, repo); a refused Go,
or any other drag (to a closed column too, while its issue is open), is an error line in `state/errors.jsonl` (health files it)
and fm moves the card back to the snapshot's Status. An issue closed by hand while its card is open is left alone (not built: no error line yet).

### Ids

- A new task's id is `FN-<issue number>`, found on GitHub as `gh issue view <number>`; PRs share the numbering, so ids skip the numbers PRs take.
- `foreman show FN-302` when #302 is a PR: `foreman: no task FN-302: #302 is a pull request` (one `issueOrPullRequest` read). A number that is an issue but not on the Board: `no task FN-n: #n is not on the Board`. A number the map holds: `no task FN-272: #272 is FN-140`, so a task has one id.
- Migrated open tasks keep their old id: `<home>/legacy-ids.json` (`{"FN-140": 272, …}`) is written once at the cutover and read by the port (`number("FN-140")` = the map's value, else the digits); the issue's first body line is `<!-- foreman:legacy-id=FN-140 -->` for people reading GitHub. Their local state (worktree, branch `fm/FN-140…`, `plans/FN-140.md`, reports) is keyed by the old id and needs no rename.
- The cutover refuses unless the repo's newest issue or PR number is ≥ the highest id of the old file, open or closed (today 271 ≥ 234): every number fm gives later is then above every legacy id, so an id never means two tasks.
- Closed tasks stay in the old file, read-only, under their old ids; an id is looked up in the legacy map first, then in the old file (so `FN-100`, a closed task, is never #100, a PR), then as a number.

Worked example: FN-140 is migrated as issue #272 and stays `FN-140` (`legacy-ids.json` holds `"FN-140": 272`); a task filed later as issue #301 is
`FN-301`; #302 is a PR, so `foreman show FN-302` prints `foreman: no task FN-302: #302 is a pull request`; the next task, #303, is `FN-303`.

### Reads and writes per pass

The read of one pass is one request: `repository.issues(states:OPEN, first:N)` with `totalCount`, `pageInfo{hasNextPage}`, each issue's
number, title, body, state, dates, labels, parent, sub-issues, blockers and its Board item with one alias per field, plus `rateLimit{cost remaining}` (E2).
Each run 8 times, median (min–max):

| Read | Median | Cost (points) | Response |
|---|---|---|---|
| cheap read: `number updatedAt projectItems.updatedAt` of the 19 open | 0.67 s (0.59–0.72) | 1 | 2.4 KB |
| full read, first:25 (19 open) | 0.87 s (0.80–1.07) | 1 | 60 KB |
| full read, first:50 | not timed | 2 | |
| full read, first:100 | 0.82 s (0.77–1.14) | 4 | 60 KB |
| full read without body, first:100 | 0.81 s | 4 | 10 KB |
| one task by number (`issue(number:)`, full fields) | 0.65 s | 1 | |
| cold read of every issue (247, 100 per page, 3 pages) | 4.96 s | 12 (3×4) | |

Cost grows with `first`, not with what is returned (first:25 → 1, 50 → 2, 100 → 4). The `first:50` cost was measured after the scratch Board was
deleted: the same query text (`reads.py`'s `FULL`) once on a public repo with 1081 open issues (cli/cli) gave 1, 2 and 4 points for first:25, 50 and 100. The cheap read would save 0.15 s and 0–3 points, so a pass
makes one full read with `first:50`; more than 50 open tasks, or `hasNextPage` true, is an error naming the count, and so is a nested list over its cap: a `totalCount` above
`labels(first:10)`, `subIssues(first:30)` or `blockedBy(first:20)`.

Writes, each with its read-back (E3; median over n, seconds):

| Write | Calls | Time | n |
|---|---|---|---|
| file a task: createIssue, addProjectV2ItemById, 5 field values in one request, read back | 4 | 4.74 (4.52–5.66) | 5 |
| move Status: 1 mutation + read back | 2 | 1.35 (1.29–1.62) | 5 |
| set Blocked text + read back | 2 | 1.55 | 3 |
| open a question: add label + read back | 2 | 1.39 | 1 |
| answer it: remove label + read back | 2 | 1.79 | 1 |
| edit body: read fresh, updateIssue, read back | 3 | 2.25 (1.87–2.28) | 3 |
| add a sub-issue + read back | 2 | 1.59 | 1 |
| add a dependency + read back | 2 | 1.30 | 1 |
| close as done: closeIssue + Status Done in one request, read back | 2 | 1.92 | 1 |
| reopen + Status in one request, read back | 2 | 1.92 | 1 |

- An idle pass (nothing changed) = 1 call. A task's life (file, go, refining, ready, in progress, in review, done) = 4 + 6×2 = 16 calls, about 13 s of waiting (4.74 + 5×1.35 + 1.92) spread over days.
- Filing runs in order: createIssue, addProjectV2ItemById, the fields, the read-back. One cut short leaves an issue with no card (not a task: the read skips issues with no card on the Board, though they count toward the 50) or a card with no fields (read as triage with no `Source`); the filer writes an error line naming the issue and the owner closes it. A step that writes both the Board and local keys (the merge: Status Done, then `commit` and `result`) writes the Board first, reads it back, then `state/runs.json`; a crash between leaves the local record missing, and the step is not redone, as the Status already moved.
- Headroom: the loop passes every `[health] loop_seconds` = 30 s plus the pass (`loop/tick.py:103`), so ≤ 120 passes/hour × 2 points = 240 of 5000 points/hour (4.8%) for fm's size (about 230 tasks, 20 open), counting the backlog reads only: the writes' read-backs (1 point each) and
the delivery steps' calls fm makes today (`gh pr view` and `gh api graphql`, `providers/github/provider.py:65,100,165`) share the same 5000/hour and are not counted. The GraphQL limit measured is 5000/hour (`gh api rate_limit`); a mutation request costs 1 point, several aliased mutations in one request still 1; no secondary limit was reached (247 issues created in 684 s, 23 batches of 10 with a 3 s pause).
- Measured 2026-11-21 on 247 issues, 19 open; one day's numbers, not a promise.

### Questions and the decisions list

- Questions (`foreman ask`, permissions, answers) stay in foreman: `<home>/state/questions.json`, `foreman answer/allow/deny`, the panel. While a task has an open question its GitHub issue carries `needs-owner`: the loop sets it when the question opens and removes it when none is open, both read back. The Board shows the label on the card; the question's text is on no GitHub page.
- The decisions list is the `decision.key=value` lines the owner and agents write in a task's detail: part of the issue body (`## Detail`), nothing more. The answered questions and the agents' messages (`decisions_resolved`) are local.
- Blocked: the `Blocked` column shows the reason. Agent, branch, PR and review state are not on the Board (the PR names its task with the text `Task: #n`).
- The panel and `foreman watch` read through the port, which serves them from the Board's side of each open task and the closed blockers (`<home>/state/backlog-cards.json`), written after each pass's read and after each write's read-back, so an owner's Go in the panel shows at once: the panel polls every few seconds (`panel/src/feed.ts:78`, at the repo root) and may not call GitHub. A panel request and `watch` read through `registry.view_scope`; each task's local keys, the questions and the closed tasks are read fresh from their files; with no cards file yet they say «the loop has not read the Board yet» (the panel as a `notice`). `backlog-snapshot.json` stays the drag detector's. The guard hook runs on every agent tool call (`core/oversight/guard.py:226`) and uses only the task's `worktree` and `branch` (`guard.py:189-190`): it reads them from `state/runs.json`, never GitHub nor the snapshot; a task with no entry there is «no task», as an unreadable file is today. `diagnosis.record` (`core/oversight/diagnosis.py:19`) appends to `detail`: a write, so it goes through the port. Owner commands (`list`, `show`, `add`…) read the task live (`issue(number:)`, 0.65 s, 1 point) and write through the port.

### The split

These replace FN-140's planned subtasks 2-5: 2 (the port) and 5 (migration) kept, 3 (the GitHub adapter) split in two, reads and writes, and 4
(commands and panel) reshaped into a full task seen on the sandbox. Drafted with their criteria in `<home>/reports/FN-234.md`; not filed yet.
- **D2 The backlog port, with the local file behind it** (type change; built in FN-235). Every read and write of the backlog goes through one port with the local file as its adapter, and the owner sees no difference: `foreman list, show, add, go, edit, accept, reject, answer` and the panel print the same on the sandbox, no module outside the adapter opens `backlog.json`, the panel reads through the port, the guard hook reads its task's local keys, and a tick loads the backlog once; `core/oversight/guard.py:226` stops importing the file module and `diagnosis.py:20`'s write moves behind the port (core cannot import a port).
- **D3 A project on GitHub: Board setup and reads** (after D2). With `[backlog]` pointing at a repo and Board, `foreman list`, `show` and the panel read the tasks from it: `foreman backlog setup` creates the 9 Status options, the 10 other fields and the label and deletes the workflows, idempotently; every field shown from a scratch Board of 20 open tasks; `FN-<number>` ids, a PR's number refused with its text, legacy ids through `legacy-ids.json`, an id looked up in the map, then the old file, then as a number, and `FN-272` refused as `#272 is FN-140`; the panel's recent list and the metrics read closed tasks from the old file and `state/closed.json`; one request per pass, over 50 open or a nested `totalCount` over its cap an error naming the count, a rate limit stops calls until the reset; a project without `[backlog]` still runs on its file.
- **D4 A project on GitHub: writes** (after D3). Every write of the backlog lands on the Board and is read back: `add`, `go`, `edit` (dropping the plan), `ready`, `done`, `cancel`, `block`/`unblock`, `ask`/`answer` (the label), sub-issue and blocker writes; a write that did not stick is an error naming the field; a close sets its reason and the Status in one request and keeps the read-back task in `state/closed.json` in the format of the `closed` row; a rate limit waits for the reset with no retry loop in the adapter (retrying stays in `loop.guarded`); calls per operation equal the table above (±1); the pass's read and every write with its snapshot update run under the backlog lock, a drag Triage → Backlog is a Go with `go`'s checks, any other drag is an error line and the card is moved back, and the first pass (no snapshot) detects no drag.
- **D5 A full task on GitHub, seen on the sandbox** (after D4). The loop runs a task end to end on a scratch Board: filed, let in, planned, worked, reviewed, accepted and merged, with a screenshot of the Board after each move; the panel and `foreman watch` match `foreman list`/`show` and the file's output; a project without `[backlog]` is unchanged.
- **D6 Migration and cutover of fm** (after D5, the owner present). fm's open tasks live on the real Board and `backlog.json` is no longer used: a dry run prints each open task's future issue, fields and id and writes nothing; the cutover files every open task with its legacy id, keeps the closed ones read-only in the old file and refuses on a collision («Ids»); an open migrated task's closed subtasks and blockers are read from the old file; filing runs in the order of «Reads and writes per pass», and one cut short leaves an issue with no card, skipped by the read and named in an error line; afterwards `foreman list` shows the same open tasks; pointing `foreman.toml` back restores the file; the real Board has the setup's fields and no workflows.

### Left out

- A cheap peek before the full read, assignees and multi-person claiming, a chat thread per task, comments as machine records (trace, risk): the owner's decisions and E4.
- More than 50 open tasks (paging): an error, not built.
- An issue closed by hand while its card is open: left alone. Reopening a closed task, taking a task out of its parent, a closed task's Board fields: an error naming it (D4).
- A Board `Id` field or a title prefix: the id is the number.
- The PR's link to its task beyond the text `Task: #n` (no `Closes`, no linked-branch API): not tried.
- Migrating closed tasks: they stay in the old file (an open task's closed subtasks and blockers are read from there).

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
| `publish` | `npm/pnpm/yarn/cargo/uv/poetry publish`, `twine upload`, `docker push`, `gh release/pr create|merge|edit|delete|upload`, `gh repo create|merge|edit|upload` |
| `push-main` | `git push` to the main branch (named, `HEAD` while on it, or bare while on it), or with `--force`/`--mirror`/`--all` or a ref written with a leading `+` |
| `push-evidence` | `git push` to the `foreman-evidence` branch (only the loop writes it) |
| `delete` | `rm`/`rmdir`/`unlink`/`find -delete` of a path outside its worktree, its own scratch folders (`<home>/scratch/TID-…`) and temp dirs, after resolving `cd` and `NAME=value` inside the one command (`core/oversight/shellscope.py`); a variable not set in it counts as outside; `git branch -d/-D` of another branch; `git worktree remove`, unless of a scratch or temp folder; `git push --delete`; `gh repo delete`, `gh project delete` |
| `config` | Edit/Write of the project's `foreman.toml`; a shell command that writes it (`>`, `tee`, `sed -i`, `mv`, `cp`, `rm`) |
| owner's own | `[guard] ask = [{name, pattern}]` in foreman.toml: a regex on a shell segment |

Everything else runs. A way around the list is not a hole to close: the review and the owner's accept before
the merge are the safety net. Each stop is logged (`state/guard.jsonl`) and counted in the metrics.

## Merge: risk and autonomy

Every delivered branch gets a risk score:

- **impact**, from what the change touches: high = paths in `[risk.high]`; medium = paths in `[risk.medium]` and deleted
  files; low = the rest. A review never lowers it.
- **doubt**, from the evidence: high = no changes, tests fail, CI fails; medium = no test command, or a large change: over `[risk] large_lines` (400) changed lines, not counting tests, generated and lock files; size never makes a change high by itself.
- **plan** (FN-115), from the plan's three lines (`Declared risk:`, `Areas:`, `Expected size:`, parsed by
  `risk.parse_plan` from the task's `plan_file` in `check`): a declared medium or high is that level, `declared <level> by
  the plan: <why> [plan]`, never lowered (a declared low adds nothing); a counted file outside Areas (`touches <paths> the
  plan did not announce [plan+diff]`) or counted lines over 3 times the Expected size (`<n> lines, the plan expected <m>
  [plan+diff]`) is medium. A plan without the lines adds nothing.
- Risk is the higher of the three. It shows up to 3 reasons, high ones first, each with its source (`[diff]`, `[tests]`,
  `[ci]`, `[git]`, `[review]`, `[plan]`, `[plan+diff]`). Size is only the large-change reason; file counts are not a reason.
  With several repos a path or size reason ends `[diff, repo KEY]`, and a `publish_on_merge` repo's branch has first the reason `merging publishes this repo (KEY)`.
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

Retry policy, one place (`loop.guarded`, deciding with `decide.retry_step`; every provider call: push, PR, CI, merge): a `Transient` failure is retried next tick and counted per task (`retry_count`, next to `retry_error`); the 3rd in a row blocks the task with the last error; a success resets both; `foreman unblock` sets every count back to nothing, the PR step's and the check's too (`backlog.unblocked`).

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
has no type (`foreman/core/tasks/tasktypes.py:20`) and gets its agent only once it has a subtask (`foreman/steps/parents.py:35`).
A new pure module `foreman/core/tasks/chains.py` (area `tasks`) has `is_parent(tasks, i)`: it has subtasks or `chain`;
those three sites use it, so a chain parent gets its agent before its first step. The agent's kind stays `parent`, with
planning's model and effort (`foreman/services/agents/launch.py:99`); `launch_parent` renders `briefs/chain.md` instead of `parent.md`
(`launch.py:140`), with `extra` = lens, report path, `max_steps`, `max_lines` (`foreman/services/agents/brief.py:83,87`).
`parent.md` makes every Go, add and the close wait for the owner's yes (`foreman/briefs/parent.md:15,18,21`); the chain
agent waits for none. Its steps enter backlog (today an agent's `foreman add --parent` is always triage,
`foreman/ui/cli/owner.py:33`); it closes the chain with `foreman done` (`foreman/ui/cli/agent.py:27-29`), done also with
no step (today refused, `parents.py:52-60`, `subtasks.py:32-36`); it `foreman ask`s the owner only at the cap. A parent
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
the owner is FN-112's). When it closes the agent is told (`parents.py:16-33`) with a chain wording of `subtasks.notice`
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

## Several repos (defined in FN-148; built in FN-149..154, seen acting in FN-155)

This section defines how a project with several repos runs its tasks; fm runs each task in its own repo. Two rules
carry it all: **one task = one PR in one repo**, and **the project's repos stay untouched** (their own git sees no fm or Claude file).

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
  `foreman`), `[agents]` (slots, models, `claude`, `runner`, settings), `[guard]`, `[health]`, `[resources.*]`. `[merge]` and
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
| ui | `foreman/ui/` | `main.py` (starts `foreman panel`) · `cli/`: main, owner, ops, agent, view, board, plugin, init, parser, common · `panel/`: server, actions, views | the owner's and the agents' entry points; call everything below |
| loop | `foreman/loop/` | tick, queue, launch, guarded | the process that ticks; gathers facts, acts on `decide` |
| steps | `foreman/steps/` | sources, release, talk, recover, reviewing, mechanic, folders, notify, parents, metrics, schedule, live, `health/`: health, checks, main_tests, notes, report | one step of a tick, or a view over several pieces |
| services | `foreman/services/` | `agents/`: brief, worktree, launch, running · `delivery/`: git, check, merge, pulls, mergecheck · lock, launchd, runsource, scratch | the outside world: processes, the lock, git through `Repo` and agents through `Runner`; called by steps |
| registry | `foreman/providers/registry.py` | | picks the adapter, and hands out the repo, the runner and the backlog (`registry.repo(p)`, `registry.runner(p)`, `registry.rows(p)`, `registry.backlog(p)`; `registry.tick_scope(p)` makes a tick load the backlog once) (the only importer of the adapters outside `providers/`, save `steps/health/checks.py` and `steps/health/orca.py`; enforced by `tests/test_adapters_only_through_registry.py`, not by tach) |
| adapters | `foreman/providers/` | local · `github/`: provider, types, checks, review, api, board, issues, backlog, apply, writes, files, cutover · gitrepo · backlogfile · `claude/`: sessions, rows, login · `orca/`: cli, settings, coordinator, rows, sessions | one per platform, `gitrepo` (the one module that runs git on a repo), `backlogfile` (the `Backlog`'s local adapter, the one module that names `backlog.json`), `claude` (the one that builds a claude command line) and `orca` (agents as Orca chats), the `Runner`'s adapters; they see their port, never the loop or each other |
| ports | `foreman/providers/` | `__init__.py` · repo · runner · backlog | the `Provider` interface; `Repo`, every git operation on a repo (typed: heads, diff counts, conflicts, worktrees, the merge outcome); `Runner`, every agent session (typed: `State`, `SessionRef`, `Rows`); `Backlog`, every read and write of the backlog (tasks cross it as dicts) |
| proc | `foreman/proc.py` | | the one module that runs programs |
| core | `foreman/core/` | `project/`: config, registry, repos, plugin, recurring_table, backlog_table · `tasks/`: backlog, questions, blockers, subtasks, tasktypes, taskrepo, listing, editing, recurring, board, ids, issues, changes, drags, cutover, runs, recovery · `review/`: verdict, inline, describe, evidence, reviews · `rules/`: risk, decide · `oversight/`: guard, shellscope, diagnosis, costs | vocabulary, pure rules and the plain files that do not vary |

Core areas, in their `depends_on` order: `project` (foreman.toml, the registry of homes, repos, the `[[recurring]]` tables, the plugin's text) →
`tasks` (the backlog's vocabulary and pure functions, questions, blockers, subtasks, types, edits, recurring entries) and `review` (the verdict,
inline comments, the description, evidence) → `rules` (`risk` on `project`; `decide` on `risk` only) → `oversight`
(the guard, diagnosis, costs). `schedule` (FN-162) moves with the steps.

### tach rules

`tach.toml` (the settings above `layers` stay; `layers` is unchanged). A package path gives all its files
its layer. A module with `depends_on` may import, in its own layer, only what it lists (lower layers stay open): that
fixes the order of the core areas, so no cycle can form among them, as tach does not see a cycle inside one layer
otherwise. `cli` and `panel` are two modules: the panel imports the CLI's functions (its actions run the
owner's commands), the CLI never imports the panel; `foreman/ui/main.py` starts `foreman panel`. Services and steps keep to the layer rule; inside services the order `delivery` ←
`agents` is the reviewer's.

```toml
layers = ["ui", "loop", "steps", "services", "registry", "adapters", "ports", "proc", "core"]
modules = [
  { path = "foreman.ui.cli", layer = "ui" },
  { path = "foreman.ui.panel", layer = "ui" },
  { path = "foreman.loop", layer = "loop" },
  { path = "foreman.steps", layer = "steps" },
  { path = "foreman.services.agents", layer = "services" },
  { path = "foreman.services.delivery", layer = "services" },
  { path = "foreman.services.lock", layer = "services" },
  { path = "foreman.services.launchd", layer = "services" },
  { path = "foreman.services.runsource", layer = "services" },
  { path = "foreman.services.scratch", layer = "services" },
  { path = "foreman.providers.registry", layer = "registry" },
  { path = "foreman.providers.github", layer = "adapters", depends_on = ["foreman.providers"] },
  { path = "foreman.providers.local", layer = "adapters", depends_on = ["foreman.providers"] },
  { path = "foreman.providers.gitrepo", layer = "adapters", depends_on = ["foreman.providers.repo"] },
  { path = "foreman.providers.backlogfile", layer = "adapters", depends_on = ["foreman.providers.backlog"] },
  { path = "foreman.providers.claude", layer = "adapters", depends_on = ["foreman.providers.runner"] },
  { path = "foreman.providers.orca", layer = "adapters", depends_on = ["foreman.providers.runner"] },
  { path = "foreman.providers", layer = "ports" },
  { path = "foreman.providers.repo", layer = "ports" },
  { path = "foreman.providers.runner", layer = "ports" },
  { path = "foreman.providers.backlog", layer = "ports" },
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
1. Talks to git on a repo? → the `Repo` port (`registry.repo(p)`); runs a program, or talks to gh? → `services/` (through `proc`); talks to claude (or any agent runner) or makes a platform call? → a `providers/` adapter, behind its port.
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
launcher and source plug-ins (`sources`) when they exist. Not behind a port, because they do not vary:
the config, the clock. A new port needs a second real implementation or a system outside the process; say which in the
task.
`Runner` (FN-213, built): the agents' sessions, `providers/runner.py`; adapters `claude` (`providers/claude/`) and `orca` (`providers/orca/`, FN-216); two real implementations.
`Backlog` (FN-235, built): every read and write of the backlog, `providers/backlog.py`, through `registry.backlog(p)`
(`find(id)` reads one task now, also one the open read does not hold); adapters `backlogfile` (`<home>/backlog.json`) and,
with `[backlog]`, `github/backlog` (FN-236: reads; FN-237: writes; FN-140, «The backlog on GitHub»).

### The JSON reads (FN-359)

Other programs read fm through the command line alone (the dashboard, in its own repo, reads nothing else). These outputs
are the public contract for them: `tests/test_json_contract.py` freezes each one's field names and types. Adding a field
is free; renaming, removing or retyping one is the owner's decision. Their text output stays as it was.

| Command | Shape |
|---|---|
| `foreman projects --json` | array; each `{name, prefix, home, repo, main, autonomy: str, strategy: str, loop_running: bool, slots: int}`, `strategy` the `[merge] strategy` resolved with its default (`squash`, `merge` or `rebase`), `slots` the configured `[agents] slots`, else the default, as `foreman project` prints it; a home whose foreman.toml cannot be loaded is a line on stderr |
| `foreman list --json [--all]` | array; each task `{id, status, type, title, agent, repo, parent, priority, model, effort, blocked, waiting, recurring: str, chain: {lens: str, step: int, cap: int, open: str} or null, closed: str or null, rework: int}` (`""` when the task lacks the fact, `priority` `none`; `closed` the date as stored, `YYYY-MM-DD`, null when the task has none; `rework` the owner's rejections (`foreman reject`), as `show` stores it, `0` when none; failing-CI and conflict returns are not counted), in `list`'s order (`core/tasks/listing.py`) |
| `foreman needs --json` | array, `watch`'s set now (`steps/watch.py` `found`); each `{kind, task, text, key: str, since: str or null}` (`key` is `q:` and the qid for a question); kind `question`, `stopped` (a question filed by the recover step for a parent that ended its run with neither a delivery, a question, a block nor an open subtask; its text the parent's last words), `accept`, `blocked` or `main` (a parent agent is a need only through its open question); `since` the stamp stored (the question's `asked`, the check's `at`, `ci.json`'s `since`), null for blocked; `text` as stored: the question, the first line of the delivery summary (stripped, as the review description shows it), the block; `tests fail on main` for main |
| `foreman agents --json` | array, the agents live now (`steps/live.py` `agents`): the active runs (`foreman runs`) whose session is alive, in backlog order, then start order; each `{task, kind, model, effort, started, session, handle: str, round: int or null}`. Live: its run is active, its task is open and the runner lists its session alive (idle included); a session started before the ledger has no run and is not listed. `kind` `planning`, `responsible`, `review` (the reviewer), `hunter`, `parent`, `chain` (a parent agent of a task with a chain; between wakeups it has no active run, so it is not listed) or `mechanic`; `round` the hunter's or reviewer's round, or a responsible agent's when above 1 (the round its next review will be), else null; `handle` the runner's id for the session (claude: the short id; orca: Orca's session id, from the kept pane or asked of Orca by the dispatch, `""` with neither); `started` the run's start |
| `foreman runs --json [--task ID] [--active]` | array, the run ledger (`core/tasks/runs.py`, `<home>/state/ledger.jsonl`, append-only), in start order: every agent session the loop started; `--task` only that task's, `--active` only those not ended; each `{id, task, kind, session, handle, model, effort, started, failure_reason: str, attempt: int, ended: str or null, outcome: str or null}`. `kind` as in `agents`; `attempt` counts the task's runs of that kind; `session` and `handle` `""` while unknown; `outcome` `done`, `failed` or `canceled`, null while active; `failure_reason` `""` unless failed. A run ends where fm learns it: `foreman ready`, `done`, `verdict` (the newest reviewer's), `diagnose`, the hunter's file, a parent's close, the recover step (a parent's or chain's turn over: done; failed, with its reason: `session_ended`, `runner_unavailable`, `silent_too_long`, `launch_failed`), a mechanic over 30 minutes (failed), a merge or a cancel, and a new run of the same task and kind (canceled); `return_to_agent` cancels the hunter and review runs and starts a new responsible run on the same session; `foreman relaunch` starts a new run of the copy, but not in review |
| `foreman events --json --task ID` | array in time order (`core/tasks/backlog.py` `history`), a task's events; each `{at, kind, text: str}`; `at` the `util.now` stamp; `kind` `planned` (`foreman ready` with a plan), `built` (the move to `in review`, `foreman done`), `rejected`, `conflict`, `failing` or `session` (`return_to_agent`; `text` ends with the note or the conflict text), `review` (a verdict; `text` has its round and open findings), `accepted`, `merged` (the merge requested), else `other`; rows written before kinds existed are `other`, with their text |
| `foreman reviews --json --task ID` | array in stored order (`core/review/reviews.py` `rounds`), one row per verdict of the task, i.e. per review file; each `{round: int, tip, at, open: int, text: str}`; `round`, `tip`, `open`, `at` as `show`'s `reviews` stores them (`round` restarts at 1 after `foreman review`; `tip` the full sha); `text` the review file's markdown verbatim, `""` when the file cannot be read; unknown task or none yet: `[]`; hunter files are not listed |
| `foreman health --json` | object `{loop: {time: str or null, alive: bool}, versions: {loop: str or null, this: str, installed: str or null}, recurring: [{name, title, trigger, open: str, left: int or null, next: str or null, chain: {lens, step, cap, open} as in `list`, or null}], findings: [{key, title, meaning, evidence, hypothesis, repo: str}], warnings: [str]}` (`steps/health/report.py`) |
| `foreman show ID` | object: the task as stored, with what `show` adds (`id`, `title`, `status`, `blocked`, `priority` are frozen) |

### Rules

The rules, one line each, are in `CLAUDE.md`, each marked as checked by `make check` or by the reviewer. What they need
said once:
- **One module runs programs**: `foreman/proc.py` (`run`, `run_json`; it knows nothing of git, gh or claude; a missing
  program or a timeout is a `ProcError`). ruff bans `import subprocess` elsewhere (TID251), as do `os.system`,
  `os.popen`, `os.exec*` and `os.spawn*` (all but `os.execv`, which `launcher.py` uses), `os.posix_spawn*`,
  `asyncio.create_subprocess_*`, `pty.spawn`, `pty.fork`, `os.fork` and `os.forkpty`, and the modules `webbrowser`,
  `multiprocessing`, `ctypes` and `posix` (each starts a program or can); and `concurrent.futures.ProcessPoolExecutor`
  (and `concurrent.futures.process`), `socketserver.ForkingMixIn` and its `Forking{TCP,UDP,UnixStream,UnixDatagram}Server`,
  and the C-level modules `_posixsubprocess`, `_multiprocessing`, `_ctypes`. `loop.subprocess_exec` and `subprocess_shell`, which ruff cannot resolve, fail
  `tests/test_one_module_runs_programs.py`.
  The only other exceptions in `per-file-ignores` (`pyproject.toml`) are the tests and `lock.py`, which needs a process
  group and signals to stop a whole job at its time limit (`start_new_session`, `killpg`).
- **Only adapters talk to the platform**: ruff bans `urllib.request`, `http.client`, `ssl`, `ftplib`, `smtplib`,
  `imaplib`, `poplib` and `telnetlib` (TID251, same exceptions as above), so no `foreman/` module holds an HTTP or mail
  client today, an adapter included: exempting one is the owner's call (`tests/test_only_adapters_talk_to_the_platform.py`).
  A client that needs no import of them (`asyncio.open_connection(..., ssl=True)`, `socket`) stays the reviewer's.
- **Typed at the border**: the github adapter turns `gh`'s JSON into its own types (`PullRequest`, the check buckets)
  with a small converter per shape; a shape that does not fit is a `ProviderError`. The port's operations take a
  `Change` and return `Published` / `ChangeState`: no dict crosses it.
- **`decide` runs no program and touches no file**: tach lets it import only `risk`, and `tests/test_decide.py` rejects
  any other import and any `open`/`exec`/`eval` in it. New decisions go in the core, not in `delivery` or the loop.
- **Suppressions do not work**: `make lint` runs ruff with `--ignore-noqa`, pyright has `enableTypeIgnoreComments =
  false`, and `make suppressions` fails on any `pyright:` or `pylint:` directive comment.
- **Tests are type-checked**: all of `tests/` is strict, so a new test file is strict (`tests/pyright-strict.json`,
  `make types`).
- The panel's TypeScript is outside these rules.

### Size

Modules stay at most 300 lines (`max-module-lines` in `pyproject.toml`, `make size`, no exceptions); tests 600. The
limit stays: it did its job (it forced FN-157 and others to split); what failed was that a split had no obvious place,
and packages by area give one. When FN-160 ends no module should pass 270 lines (a 10% margin): 270 is a target for
its splits, not a new limit (changing `pyproject.toml` is the owner's call).


## Watching itself

Metrics per task (`foreman metrics`, the panel): the model and effort its responsible agent ran on, cost of its agents (transcripts at list prices; planning_usd of planning sessions and responsible_usd of responsible sessions, from `session_kinds`; review rounds), time to review and to
done, rework (rejected), conflicts and failures (returns for failing tests or CI),
questions to the owner, guard stops, risk, and what misled its agents (their `--misled` words, per agent).

Health checks (`foreman/steps/health/`), each finding a triage task, deduplicated by key while open or while it persists:
loop dead (heartbeat older than 3 ticks; from outside the loop: `foreman health`, the panel), agent stuck (working, its
transcript silent for `stuck_minutes`), agent blocked (stopped without finishing, from the recover step, or could not launch), orphan worktree,
a repo that is not a git checkout and fm or Claude files in a repo (several repos only),
tests failing on main (the test command on main's head, when it moves or every `ci_every_minutes`; one task per failing
episode, from the first failing run until a pass, with the commit, the failing step and the output tail), loop step errors.
Every finding has three parts: what it means, the evidence fm gathered, and a hypothesis marked pending; none proposes a fix.

`foreman watch` (FN-221, `foreman/steps/watch.py`) prints one line, with the task id and why, each time something starts
needing the owner: an open question, a task waiting for his accept (`decide.after_check` says `owner`, so autonomy-merged
tasks print nothing), a blocked task (a failed launch is one), main's tests failing. A parent agent needs him only through its open question, which a silent end of its run files (kind `stopped`).
It decides from the backlog and `state/ci.json`, never from the text of `events.jsonl`. Every 10 s it looks again; its seen set lives in memory,
so the accepts already waiting at start are printed once and the other needs at start are not (`foreman list` shows them). `--once` exits after the first line printed after the start; without it
the command streams (a monitor).

With `[owner] notify = "<command>"` in foreman.toml (FN-310) the loop's last step (`foreman/steps/notify.py`) runs the command once for each need `watch.needs` finds new, with the need's line as its last argument: the accepts already waiting when it first runs, then each key not in `state/notified.json` (written before the commands start, so a restart never repeats one). The command is split like a shell would but run without one, in a background thread per need with a 30 s limit; a slow, failing or missing command never delays a tick, and its failure is one line in `state/errors.jsonl` whose step is `notify <command>`, not retried: health files one finding per failing command, not one per need. Without the key nothing runs. A key that is not a text is refused at load.
