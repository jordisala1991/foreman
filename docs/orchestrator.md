# Orchestrator procedure

The procedure of the main session that runs a project's backlog with agents. The state lives in the backlog, the
notes and the board; each rule is in one place, without its history (that goes to the notes). The project's own
rules (`[project] rules` in foreman.toml, `rules.md` by default) fill in what this file leaves open and win when
they disagree.

## Where everything lives

| What | Where |
|---|---|
| Work: projects (E1…), issues with acceptance criterion, plan, reports and result, sub-issues | the backlog file, only through `foreman backlog`; its pages are generated; to list or count issues, of one product or all (`--all-products`), `foreman backlog list` (never a script over the file) |
| Decisions only the owner can take | `foreman backlog decision add/resolve`; generated in the decisions page |
| What happens while working | `notes/YYYY-MM-DD.md` with `foreman note` |
| Plans and reports | `reviews/` (`[paths] reviews`) |
| Project rules: repos, commands, what is never done without the owner, how each change is checked | the rules file |
| The orchestrator's working memory | the board (`<state>/board.md`, ~15 lines: what only the orchestrator knows) |
| Transient state: agents, progress, inbox, heartbeats, session | `~/.local/state/foreman/<project>/` |
| Shared resources (GPU, simulator, containers…) | `foreman lock RESOURCE …`, common to every project |
| foreman itself: its version, how to install and update it, how a version is cut | `CONTRIBUTING.md` and `CHANGELOG.md` at the root of foreman's repository |

## Starting a session

1. Run `foreman start`: one short summary of what the session needs to start, instead of reading the procedure,
   the notes and the backlog whole. It gives the session's own id (from `$CLAUDE_CODE_SESSION_ID`, else the live
   session of the folder by its heartbeat, else the newest transcript of the folder, and says which) with the exact `foreman
   session` line to run, who is marked now and whether it lives, how the loop pass comes, the board, the active
   backlog, what waits for the owner, resources, locks and live agents (dead when their session is gone), the rules file and what to read next only if
   a line asks for it: the sections of this file a notice or a case needs, docs/owner.md before the first message to
   the owner, the plan of the issue in progress. Read the rules once (they win over this file); `--json` gives the
   same fields. Mark the session: `foreman session <session id> --name <name>` (the id is the
   name of the session's `.jsonl` under `~/.claude/projects/…`; the name is the one other sessions write to with
   SendMessage, as `ListAgents` shows it; marking the same session again without `--name` keeps it). Marking your
   own session from it also ties the mark to your Claude Code process (pid and start time): a compaction, a
   renewal and `/reload-plugins` keep that process, a resume after a close starts a new one and marking again takes
   it; a `/clear` (or a resume inside the same window) goes on in the same process under a new session id, and
   foreman's hooks module moves the mark to it by itself (a note in the day's notes says so), and a copy of the session started while you run (`claude --resume`) cannot take the mark. The resume
   and loop hooks and the loop pass only act in that session; every other session that opens in the project is told
   at its start who orchestrates it (name and since when), that it is not the orchestrator and how to reach you. `foreman session`
   alone shows who is marked and whether it still runs: every session of the product writes a heartbeat from
   foreman's hooks module at each tick of its timer (once a minute, `<state>/sessions/<id>.json`) and marks it
   ended when it closes; foreman reads it with the `loop` heartbeat and says `alive` (last tick, since when the
   module runs there, idle or busy, last pass), `alive, but no loop pass since …` (more than `max(2 × every_min, 90)`
   min since the latest of the last pass, the mark and the module's start: foreman's hooks module makes no passes in
   it), `NOT ALIVE` (the session ended, or two ticks went by without a heartbeat: closed, crashed or the machine
   asleep) or `no loop pass … it may be gone` (no heartbeat at all, as when the module does not run there, and the
   `loop` heartbeat is past the limit). Claude Code's own files are not read. A marked session that is not alive stays marked (a resumed session gets its mark back, and its passes
   with it): if you come to relieve it, mark yourself; the exit code of `foreman session` stays 0. The
   other sessions of the product hear it too (below the notices table), and the panel shows it in the Now tab's
   orchestrator line and in the product's row of the System tab (red when not alive, amber without passes).
2. The loop needs nothing from you. foreman's hooks module (`hooks/mod/foreman.ts`) asks foreman every minute
   whether a pass is due in the marked session (`[loop] every_min` since the last pass, or the session was marked
   since) and sends it as a prompt: `[loop] prompt`, a blank line and the summary. The first comes within a minute of
   marking. Nothing is scheduled and nothing expires; a session that is no longer marked gets no pass from its next
   tick on. Without the module (Claude Code below foreman's minimum, plugin mods turned off, or a session that started
   before this foreman was installed) no pass comes: `loop_stale`, `foreman session` and the panel say so; ask the
   owner for `/reload-plugins` there, and run `foreman loop` by hand meanwhile. With each tick the module also draws a
   band above your prompt (in the marked session only, gone from the first tick after it loses the mark): a status
   dot (red when the pass is late or no session is marked, yellow when a question waits for the owner), the owner's
   pending questions (one answered in the panel is resolved: it leaves the count), your memory against `[loop] context_rotate`, branches ready to integrate and,
   apart, measurements to close (`integrate --measure`) when there are some, and the next pass; below, the running jobs by their issue's short name, ID and minutes. Give an issue a
   short name of two to four words with `foreman backlog edit ID --short "…"` when its title is long: without one the
   band trims the title. On a narrow terminal the band leaves out whole parts, least important first, and keeps the
   dot and the questions. `/foreman:status` prints the same summary as plain text in any session of the product.
3. **If the marked session is dead** (it exited or was closed; `foreman session` says `NOT ALIVE`, other sessions get
   `who_gone`), the owner opens a new session in the project's home and it starts as above: `foreman start` says the
   marked session is not alive and shows the agents whose session is gone as dead (`dead_agents` in the pass once it
   is marked); `foreman session <id> --name <name>` answers «relieves <old> … since … (not alive)». Then tell the
   owner, in plain words (docs/owner.md), that the orchestrator session changed and why. Nothing is unmarked
   automatically; if someone later resumes the dead session, it gets no pass: it is no longer the marked one.

## Every pass

1. **State:** at each due pass foreman writes the `loop` heartbeat and computes the pass as notices (agents, locks,
   branches ready, recurring tasks, inbox, leftovers, backlog problems and the project's own checks), each one with a
   `kind` and whether it is `actionable` (table below), and records it in `loop.jsonl`. If none is actionable, the
   pass never reaches you: the owner sees one line in the session, «Loop pass HH:MM: nothing to do (skipped without
   calling the model)» with the information-only notices (the module's `$.ui.log`; the model never reads it).
   Otherwise it comes as the prompt, every notice in it and the information-only ones marked `(info)`: act on each of the others (skill `orchestrator-pass`). A pass interrupted
   with Esc, or dropped before it started, is sent again once, when your next turn ends. `foreman loop [--json]`
   shows what a pass would get without using it up.

   foreman never decides by the text: each check sets `actionable`, and in doubt it is actionable. The kinds are
   foreman's, the same for every product; a product's own checks give their own kinds (`[loop] checks`, docs/config.md).

   | kind | actionable |
   |---|---|
   | `paused` | no (information; the rest of the pass still counts) |
   | `config_warnings` | yes when they appear or change, then no (also a shared resource that the registry's projects declare differently) |
   | `context_rotate` | yes when the session's context crosses `[loop] context_rotate` and again when its case changes (no agent of the session left live, or 1.5 × the threshold reached); no while a renewal is asked or under way, or after one that freed too little (the next one waits for 1.5 × the threshold); otherwise no. It asks for the renewal in place («Renewal»), and says why when the last ones went wrong |
   | `foreman_behind` | this session runs an older foreman than the installed one, past the grace after the install: no while the host loads it here by itself (the desktop app, «Reload»); otherwise yes once per installed version (tell the owner, in plain words, to type `/reload-plugins` in this session), then no; an estimated version («≈»), no |
   | `sessions_behind` | other live sessions of the product run an older foreman (an estimated version with «≈»): yes once per set, then no |
   | `loop_stale` | only between passes, in the marked session: yes once per case when it is alive and no pass came for `max(2 × every_min, 90)` min since the latest of the last pass, the mark and the module's start in it (foreman's hooks module does not run its timer there: ask the owner for `/reload-plugins` in that session and run `foreman loop` by hand meanwhile) |
   | `wip_production` | yes if production < slots and a launchable ready issue is there (unblocked, and not held by a pending decision; the text names what waits for which decision); no while the session waits for its renewal (`context_rotate`) |
   | `wip_ready` | yes if ready < target, an unblocked backlog/refining issue can be planned and no planner is live; with only triage, no |
   | `dead_agents`, `stalled_agents` | yes (`dead_agents`: a live agent whose session's heartbeat is gone or missing, or that this session's `$.agent.list()` reports failed or killed; `stalled_agents` adds its status there, as `idle`) |
   | `delivery_incomplete` | yes: a production agent the delivery check let stop after 2 tries with something still missing (commits, report, `progress --done`); until it is resumed or delivers complete |
   | `orchestrator_only_denied` | yes, once per refusal: an agent, another session or another Claude Code process of yours (a copy started with `--resume`) tried a command of a `[deny.grants]` group and the guard refused it (`<state>/approved.jsonl`) |
   | `nameless_agents` | live production agents with no name by any path (no launch line, not named by hand): yes once per set, then no |
   | `lock_foreign` | yes, once per case; no if the foreign name holds it through a live `run`; none for a holder whose owner line is not written yet |
   | `lock_long` | a lock held over `[loop] lock_long_min` minutes (60): yes, once per case |
   | `lock_queue` | no while every holder holds it through a live `run` or is just taking it; yes if a holder is dead, took it without `run`, gives no pid, never wrote its owner line, or nobody holds it |
   | `ready_to_integrate`, `measure_delivered`, `loose_worktrees`, `branch_no_worktree`, `loose_branches`, `orphan_in_progress`, `backlog_problems`, `backlog_unreadable`, `hung_shells`, `recurring_late`, `inbox` | yes |
   | `review_needed`, `review_findings` | a delivered branch of a repo with `review = "auto"` whose review does not let it in yet (docs/config.md, «`[repos.*] review`»): it is not in `ready_to_integrate`. `review_needed`, yes: launch a reviewer with the command it gives (`foreman launch NAME --review REPO:BRANCH`: no worktree; the branch is held while it lives), or for round 2 resume the one of round 1. `review_findings`, yes: send the H and M back to the agent that made the branch (SendMessage), or the plan B command it gives (a new agent on the branch's worktree); after round 2 with H or M open, a question for the owner with the review as its page, then no while it is pending, then yes with their answer |
   | `decision_due` | yes, once per question on its reminder day (`--answer-by`, stored as `unanswered.by`) or the first pass after it while it is still pending: remind the owner in the chat; the notice says what waits. foreman never answers for them: the question stays pending |
   | `disk_low` | yes when it crosses `[loop] disk_low_gb`, then no until it goes back above |
   | `check` (or the kind a check line gives) | what the line says (`[loop] checks`, docs/config.md): a JSON line's `actionable`, a plain line yes; a line that gives one of the kinds of this table is a `check` |
   | `check_failed` | yes (a check that times out or ends with a code other than 0 or 1) |
   | `summary_failed` | yes (the pass could not be computed: never skipped) |

   Between passes you get only the actionable notices of the cheap checks (new inbox messages, locks, the session's
   context, pipeline, idle agents, a loop with no pass) whose case was not said yet, by the pass or before: each
   case once, again only if it goes away and comes back or changes. They come with your other prompts (the prompt
   hook) and, between prompts, with the result of your next own tool call (not your subagents'): foreman's hooks
   module computes them at its tick, once a minute, so a notice may arrive up to a minute after its case appears.
   The two `wip_*` notices are the exception: each is said once per class until the next pass whatever its counts
   (the pass gives the detail), and when both are due they come as one line.

   At the end of each of your turns the module also checks your answer with `foreman plain` (docs/owner.md, «How to
   check a text») and, if it named something without explaining it, adds `plain_echo` to the owner's next prompt
   (one they typed: not the pass, a task notification or another session's message): what it left unexplained. Say
   what each is if you write about it again; do not resend the message. `foreman plain --stats --since 7d` is the
   share of your answers with findings, per day.

   The other sessions of the product (not the marked one, not their subagents) get these hook texts instead:

   | text | when |
   |---|---|
   | `who_other` / `who_none` | at SessionStart: who orchestrates and since when, or that nobody is marked |
   | `who_gone` | the marked session is not alive (`gone`: no live file of it while this session's own file is live): at SessionStart instead of `who_other`, and once per case on a prompt of a session already open; it says the last pass and the command to mark this session, or to tell the owner |
   | `who_silent` | the same for `silent` (the files cannot tell and no pass for `max(2 × every_min, 90)` min): it may be gone |

   Every session of the product, the marked one included (not their subagents), also gets `version_behind` once per
   installed version when it runs an installed foreman older than the installed one (or its plugin folder was
   replaced: `version_replaced`), at its start, on a prompt or, when its plugin folder was replaced, with the result
   of a tool call after the module's next tick (up to a minute later); the owner sees one line in the session. Only the owner can type `/reload-plugins` there.
   None of this comes in the first 3 minutes after the new version was installed (the grace, below «Reload»): a
   session that loads it by itself in that time (the desktop app does) is never told, never counted as behind and
   never makes the owner type anything; one still on the old version after it is told as above, the marked
   session too. The pass carries `foreman_behind` until the session runs the installed version: information while
   the host loads it by itself (the desktop app, once the session is free); otherwise actionable once per installed version: tell the
   owner, in plain words, to type `/reload-plugins` in this session, then information. Other sessions of the product behind come as `sessions_behind`: tell the owner which ones need
   `/reload-plugins`, or relaunch the ones you launched. `foreman session` says which foreman the marked session runs,
   and `foreman session --all [--behind] [--json]` lists the live sessions of every registered product with theirs. The
   panel's System tab lists the live sessions behind (an estimated version with «≈»), and the product row and the
   orchestrator line in Now carry an amber tag.

   A live orchestrator, or one whose state cannot be told and is within the limit, changes none of them. On a
   prompt the processes are only checked when the heartbeat is past the limit or no file names the marked session
   (Claude Code deletes it on exit): a clean exit is heard on the next prompt, a crash that leaves its file behind
   once the heartbeat is late.
2. **Integrate** each branch whose agent delivered a report (no report, not delivered: ask for it). The pass names
   the report it finds, with the delivery check's rule: the `report` of the branch's sub-issue, else the one the
   agent named at delivery (`progress --done --report`), else a file the branch adds under `[ids] report_prefixes`
   (home inside the repo), else a recent one under the prefixes in the home that names the issue or agent or cites
   the branch (never a plan, `plan-ID.md`). A report elsewhere, or one the pass did not pick: `foreman backlog sub
   ID Tn --report PATH` (one call per sub-issue); `report X not found` means that field, or the path the agent
   named, points nowhere. `foreman integrate` without `--report` takes the sub-issue's, else the delivery's.
   Integrating is yours: the guard refuses `foreman integrate` to every agent (`--check` and `--help` pass), and an
   agent's git that rewrites a main checkout holding a live home (merge, pull, checkout of a branch, reset --hard,
   stash…; docs/internals.md «The main checkout»); you hear of each attempt (`orchestrator_only_denied`).
   - `foreman integrate REPO BRANCH "ID Tn" --check`: the branch contains main (or can take it, below), the diff,
     the risky paths it touches, the pre-merge report and the plan's check section. Compare with the report; what
     is missing is measured first or written down as debt (`foreman backlog edit ID --append`).
   - **A branch that needs a review** (a repo with `review = "auto"`: delicate paths, over `review_lines` lines of
     code, or delivered `partial`; docs/config.md, «`[repos.*] review`») is not offered as ready: the pass says
     `review_needed` or `review_findings` instead, and `integrate` (and `--check`) prints `== review: STATE` and
     stops with exit 2, nothing done, until a review of its tip leaves no H or M open. The rounds:
     - **Round 1:** launch a `reviewer` with the command the notice gives, `foreman launch NAME --review REPO:BRANCH
       --issue ID --sub Tn` (no worktree, no backlog change; the branch is held while it lives). Brief: the launch
       line, «A branch review, round 1» (agents/reviewer.md: its rules and the header foreman reads), the issue, the
       plan and the agent's report, and nothing of what you think of the branch. Its review,
       `reviews/YYYY-MM-DD-review-ID-Tn.md`, starts with `Branch:`, `Tip:`, `Round:` and `Open: H n, M n`; foreman
       takes the verdict from `Open:`, never from the prose.
     - **Findings go back to the agent that made the branch**, with SendMessage (its id is in the notice and in
       `foreman agents`), so it fixes them with its whole context: the review's path and «fix or refute each H and
       M with proof (a test, or the line that disproves it), a "Review, round N" section in your report, commit,
       `foreman progress --done` again» (docs/agent.md, «Resumed with a review»). Resumed, it is live again and the
       branch leaves the lists until it delivers. You neither fix nor drop a finding: one you think wrong, say so in
       the message; the agent refutes it and the reviewer judges. L findings never block. No id to resume (another
       session, an agent of another day): plan B, the command the notice gives, `foreman launch NAME --repo REPO
       --branch BRANCH --issue ID --sub Tn`, which reuses the worktree foreman made for the branch when no live agent
       has it; brief: the plan, its report and the review.
     - **Round 2:** once the fixes are in, resume the reviewer of round 1 with SendMessage («round 2: check each
       finding of the review and only what the fixes changed»), or launch one with the previous review in its
       brief. A merge of main or a clean rebase keeps a review valid (same own patch); other commits need round 2.
     - **No third round:** round 2 with H or M open makes the state `for the owner`. Ask them with the command the
       notice gives (`foreman backlog decision add` with `--page` the review: the pass finds the question by its
       page), written as docs/owner.md says, with the options in as it is with the problem noted, fixed by another
       agent and reviewed again, or dropped, each with its `--effect`. The branch waits; while the question is
       pending the notice only informs. With their answer: in as it is, `foreman integrate … --approved "OWNER,
       DATE (ID)"`; another agent, plan B and then a review with `Round: 3`; dropped, cancel the sub-issue and
       remove the branch with a note giving its tip.
     - **Size waiver:** `--no-review "why"`, noted in the integration, only for a branch whose only reason is its
       size and that has no review yet: a mechanical change proven by its tests (a split checked by the golden
       corpus). Never for delicate paths nor a `partial` delivery; those wait for their review or the owner.
   - **A hidden check** (an issue with a `holdout.toml`, written by its planner and sealed by you when you approved
     the plan: skill `review-plan`; docs/config.md, «`[paths] holdout`»): `--check` prints `== hidden check (ID):
     …` and stops while it is not sealed or changed since; `integrate` runs it after the local CI, before anything
     is pushed or merged. On a branch that also needs a review, run it first (`foreman holdout run REPO BRANCH ID`,
     as the notice says): it is cheap, and a branch that fails it needs another review anyway. When it fails, read
     the log (`foreman holdout status ID`) before anything else: a wrong case (tied to a name the agent chose, a
     wrong expected value) is fixed by the planner or you and sealed again, the rounds start over and the agent
     hears nothing. Else send the agent, with SendMessage, the block after «send the agent only this», as it is;
     two rounds, then the owner (`--approved`), as with reviews. The cases, their commands and files never go in a
     brief, a message, a note, a decision or the backlog; a reviewer gets at most «hidden check k/N».
   - Then without `--check`, with `--title`, `--body` (English for public repos, without internal IDs),
     `--report reviews/…`, `--note "…"` and, if it closes the issue, `--result "…"` (and `--verdict` if it has an
     acceptance criterion); title, body and result are read by the owner: docs/owner.md. The command runs the local CI, pushes, opens the PR, waits for the remote checks, merges
     with the repo's method, cleans up, writes the only note and closes the backlog. If anything fails the PR stays
     open and nothing is forced. Exit 3 = waiting for the owner's approval (repo that requires it, publishing repo or
     risky paths): ask through the inbox or a decision and rerun with `--approved "who, when"`.
   - **A branch that changes the live permissions** (the `foreman.toml` or guard table of a registered product, in a
     home inside the repo): integrate tests it as `foreman permissions apply` would, on what main will hold, before
     the CI (docs/config.md, «Seeing the permissions and testing a change»). It stops if the new file does not load
     (exit 2) or a row fails (exit 1); if it allows something new, exit 3: tell the owner in the chat what it opens
     (the yes/no question only shows the command), and with his yes rerun with `--opens` (the guard asks him;
     `--approved` does not stand for it). One that only restricts or adds rows goes on, and the note says so.
   - A repo with `remote = "none"`: no PR and no push; `--title`/`--body` optional, the branch goes into main by
     `git merge --ff-only` after the local CI, which runs again on main, or the repo's `main_ci` instead when main
     is exactly the commit that passed (uncommitted changes in a home inside it do not count as a dirty main). If the branch does not contain main only because of your own commits under a
     home inside the repo (backlog, notes, reports) and the merge is clean, integrate merges main into the branch
     first (`git merge --no-ff`, in its worktree) and goes on; `--check` says «would merge main (N commits, only
     under HOME/) into BRANCH: clean» and writes nothing. Anything else (main changed outside the home, a conflict,
     a repo with a remote) stops and says why: merge main into the branch by hand, or `--rebase-from COMMIT`.
   - **No git hooks**: integrate and `foreman worktree` run git without the repo's hooks (merge, rebase, checkout,
     reset, worktree add and remove) and without fsmonitor, so a hook left in the shared `.git/hooks` never runs
     with your power; only `git push` keeps its hooks (`pre-push`, for git-lfs and the like). A step after the merge
     goes in the repo's `post_merge` (docs/internals.md, «The git foreman runs»).
   - The local CI runs once per value of the repo's `ci_runs` (in `{ci}` of `local_ci`); `--ci=ARGS`, repeatable,
     replaces them for this integration (`--ci=--quick --ci=--full`, with `=`), and `--check` lists the runs. Its
     output is written as it comes to `<state>/integrate/<branch>-ci.log`: follow a long one with `foreman wait`.
   - **A measurement** (a sub-issue with `kind = "measure"`, or one whose agent delivered with `progress --done
     --kind measure`) brings a report and no code for main: read the report, then close it with `foreman integrate
     REPO BRANCH "ID Tn" --measure --result "…"` (`--check` first if in doubt). No CI, no merge, no push, no PR: the
     report (required; one only in the branch or the worktree is copied into the home), the worktree removed (it must
     be clean, untracked files too: never forced), the local branch deleted (`--keep-branch` keeps it for a later
     sub-issue), the note «Measured REPO:BRANCH (ID Tn)» and the sub-issue `done` without a commit. Without
     `--measure`, sub-issues that are all `kind = "measure"` stop with exit 2: if the measurement left tools that do
     belong in main, mark it as code (`foreman backlog sub ID Tn --kind code`) and integrate it as usual. On a branch
     with code and measurement sub-issues, the merge closes the measurements without the commit. Closing a code
     sub-issue by hand (`backlog sub … --status done` without `--commit`) while its branch is unmerged warns on
     stderr: a branch whose sub-issues are all closed without a commit no longer shows as ready to integrate.
   - Last sub-issue of an issue or a batch: last section of the skill `orchestrator-pass`.
3. **Plans** delivered and the definition queue: skill `review-plan`.
4. **Fill the slots** (`[agents] production_slots`): the first unblocked `ready` issue from the top, and its first
   sub-issue to do that no pending decision holds (`backlog show ID` says «T3 waits for decision …»). What collides in files with a live agent waits. `triage` (the old `idea`) only if nothing else is unblocked.
   - **Launch with `foreman launch NAME --issue ID --sub Tn --repo REPO --branch BRANCH [--base BASE] [--model M]`**: it makes
     the worktree (as `foreman worktree`: an existing BRANCH, local or only on the remote, such as a PR's branch
     opened by another session, is reused without moving it and BASE is ignored; one checked out in another
     worktree is an error with its path), marks the sub-issue ▶ with its agent and branch and the issue `in
     progress` (`--no-backlog` leaves the backlog alone), and prints the **launch line**. A planner, a reviewer or a mechanic
     without a worktree: `foreman launch NAME --issue ID --type planner`; a reviewer agent without production
     takes no `--repo`, and `--issue ID --no-backlog` when the review hangs from an issue and must not move it;
     the reviewer of a branch the pass holds for review, `foreman launch NAME --review REPO:BRANCH --issue ID --sub
     Tn` (step 2). A NAME a live agent or a pending launch already has is refused (`foreman launch NAME --cancel`
     drops a launch you did not use).
   - **The brief starts with the launch line**, as printed: `[foreman launch k3f9q2m7xa] zed7 · AB-1 T1 · branch
     ab1/x · worktree /…/app-zed7 · production`. foreman generates its token and the agents hook ties it to the new
     agent, so `foreman agents` knows its name, issue, sub-issue, branch and worktree whatever the language of the
     rest of the brief. Then: «Follow `<foreman>/docs/agent.md` and `<rules>`», the plan, the conditions of this
     task. Types: `dev` (production), `planner` (plans), `reviewer` (reviews of code or of the product, read only), `mechanic` (checked code changes, cheaper model: see «Which agent type and model»). The brief
     ends with «last command: `foreman progress --done "result" --outcome ok|partial|failed --report
     reviews/<file>.md`» (the agent names its report; the pass and `foreman integrate` take it).
   - A production agent launched without the line gets you a warning right after the launch: give it its identity
     with `foreman agents --name AGENT_ID NAME --issue ID --sub Tn --branch BRANCH` (the id, or a unique prefix of
     it, is in `foreman agents`). Without either the agent has no name (the `nameless_agents` notice); foreman
     still guesses its issue from «Issue: ID» in the brief. `foreman worktree REPO NAME BRANCH` still works and
     records a launch too (without issue): paste its line the same way.
   - The pass, `foreman agents` (its `identity`) and the panel read that recorded identity first and guess from the
     brief only what it lacks. A branch the backlog names is not *Ready to integrate* while a live agent has it as
     its branch, its sub-issue names a live agent or, for a live agent with no recorded identity, its brief names
     the worktree's folder. A live production agent with no name at all is a `nameless_agents` notice with the
     `foreman agents --name` command.
   - An agent's state is a datum (`foreman agents` prints one line per agent: id, name, state, issue/sub, branch,
     minutes, model and, once delivered, the outcome; `--json` or `--full` gives the full records, with the
     transcript): `working`, `waiting`, `delivered` or `stopped`; `delivered` once
     it ran `foreman progress --done`. Claude Code's «completed» notification is not a delivery: it also comes each
     time an agent stops to wait for a background task of its own (that one is `waiting`, and still live for 3 h at
     most). When you stop one: `foreman agents --stop NAME`. What misled the agents: `foreman agents --misled
     [--since D] [--type T] [--model M]`, the reports' `Misled:` lines with date, agent, type and model (from the
     delivery that named the report), and a footer counting the reports with something, `none` and without the line.
   - **The delivery check**: when a production agent with a launch line and a branch stops, the agents hook
     checks its delivery from local files and git: a `progress --done` recorded, commits on its branch beyond main,
     and the report under `[ids] report_prefixes` in its branch (with the home outside the repo: written in the home
     since it started; the one it named with `progress --done --report`, when it named one), with a `Misled:` line
     (what misled it, or `Misled: none`; docs/agent.md). If something is missing the stop is
     blocked and the agent goes back to work with what is missing and the command to fix it; `--outcome failed`
     needs no commits or report, and a measurement (`progress --done --kind measure`, a sub-issue with
     `kind = "measure"`, or a branch whose open sub-issues are all measurements) needs no commits, only its report.
     After 2 blocks it is let go and the pass says `delivery_incomplete`. An agent that
     stops to wait for a background task of its own is not checked, and Claude Code sends no stop event for one cut
     by `maxTurns`. The outcome (`ok`, `partial`, `failed`) shows in *Ready to integrate*, in `foreman integrate
     --check` and in the panel: a `partial` or `failed` delivery is read before integrating, not merged as done.
5. **Notes** of what only the orchestrator sees (a failed CI, a corrected figure, a decision) with `foreman note`;
   the integration already leaves its own. Rewrite the **board**: only what the hook does not give back. It is the
   one file of the state you write; the product's foreman.toml and guard table change only with `foreman
   permissions apply` (the guard refuses a Write or Edit of them, yours too: docs/internals.md, «The reserved files»).
6. **The owner:** everything the owner reads (chat, decisions, inbox replies, issue titles and results) follows
   docs/owner.md (`foreman docs owner`).
   - **Owner skills** (`owner-` prefix: `owner-catchup`, `owner-decisions`, `owner-backlog`, `owner-permissions`, and the others like it): procedures for what the owner asks
     in their own words and in any language. Load one only when they ask, never on your own; each cites
     docs/owner.md, answers in `[project] language`, sends what needs digging to an agent and brings back only its
     conclusion, and acts on nothing they did not ask for. A new one keeps the prefix and starts its description with
     «For the owner, loaded by the orchestrator - when the owner asks, in their own words and in any language, to …»,
     what triggers it within the first 200 characters.
   - «What happened?», «How are we doing?»: skill `owner-catchup` (one `foreman catchup`; four parts: done, running,
     waiting for them, went wrong). Looking for yourself, pass `--since`: only that skill moves the owner's mark.
   - «Let's go through my questions»: skill `owner-decisions` (one `decision list --json`, this product's only; one
     question per message with what each option changes and the recommendation; each answer closed in the chat with
     `decision resolve`; «whatever you think» closes it with the recommendation, `by` «Orchestrator at the owner's
     request, … (chat)»; the answers are applied at the end, not in the middle).
   - «Note this down», «I have an idea», «I want…»: skill `owner-backlog` (questions one per message, as many as
     needed and never what they already said, each with a proposal: what for, how we will know it is done, what is
     out, now or noted; the draft checked with `backlog add --dry-run` and written only on their yes; «note it as it
     is» stops and leaves it in triage with their words). The planner starts from that goal and criterion and asks
     them only when it must change its meaning. A triage idea marked «wanted» in the panel does not go through it.
   - «Let the orchestrator do X», «nobody may do Y», «why was this blocked?»: skill `owner-permissions` (`foreman
     permissions` for how it is; leaning towards what they ask; a copy tested with `--candidate` before anything
     applies; `permissions apply` only on their yes in the chat, with `--opens`, and so their own yes/no question,
     only when the change allows something that was not allowed before; this product's list only).
   - Inbox: `foreman inbox new`, answer with `foreman inbox reply`. A decision answered with a button in the panel
     is already resolved in the backlog («resolved from the panel; freed: …»; `by` says who and when): apply it (the
     plan, the sub-issues, launch what it freed) and `foreman inbox read ID`; if it cannot be applied as it is, ask
     again with a new `decision add` rather than reopening it. One answered in his own words (the panel's «Other
     answer» box: «free text: interpret it», `free` in the message and in the resolved decision) is resolved too,
     with his text as the answer: read it and decide what it means for the plan before launching what it freed;
     when it does not settle the question, ask again with a new `decision add`. Until you mark it read he may change
     it in the panel: the old message is marked read and a new one says «answer changed, it was «…»»; apply the new
     one and undo what the old one started, if anything. An answer the owner gives in the chat
     closes it the way the panel does: `foreman backlog decision resolve ID --option N` (its number in `foreman
     backlog decision list`, or its exact text; exit 2 if it is none of its options) or, for his own words,
     `--answer "…" --free`; both record `by` «Owner, YYYY-MM-DD HH:MM (chat)» and leave no inbox message. One he
     wrote as an inbox message: the same, with `--by "Owner, HH:MM (message)"`. `--answer` alone still works and
     warns when it is not one of the options. On a decision already resolved (say, from the panel meanwhile) it says
     so, exits 0 and changes nothing. `decision list [--json]` gives the pending ones in the order to go through
     them with the owner: those that hold work first, each group oldest first, with what each holds, its issue and
     its plans.
   - An owner mark «wants it» on a triage issue (the panel's «I want to do it»; the notice counts it as «wanted from
     triage»): the status did not change, so act on it. If its brief is enough as the plan, make it `ready` (as in
     the review of plans); if not, `refining` with a planner (the status shows the owner it was accepted, even when
     both planner slots are taken: launch it when one frees up). Questions only the owner can answer go to him as
     decisions. It enters the ready queue **behind** what is already ready; he moves it up by dragging. Then
     `foreman inbox read ID`. «No longer wants it»: if nothing was started, it goes back to `triage` (stop its
     planner, keep its plan); if a production agent is already building it, **ask him with a decision before
     stopping the agent**, and meanwhile let it run.
   - A question they do not answer on the spot: `foreman backlog decision add` (options = buttons); remind after 3
     days. `--title` is the question in one plain sentence, `--text` what it is about, why now and what each option
     changes, `--recommend` the option and why (shown with its label), and `--recommend-option` with the exact text
     of that `--option` so the panel marks its button (exit 2 if it matches none). Each `--option` stays a short
     answer; what it changes goes after it: `--effect` (what the owner will see or do differently), `--cost` (cost
     or risk, when there is one) and `--undo yes|hard|no` (can it be undone; `hard`: with work). When work waits for
     the answer, `--if-no-answer` says what stays stopped meanwhile and `--answer-by YYYY-MM-DD` the day to remind
     them (instead of the 3 days): you only remind, the owner always answers, so a question never announces an
     option it will take by itself. When screenshots or a table explain it better, write a page and give it with
     `--page`: one folder per question, `<reviews>/YYYY-MM-DD-<ID with hyphens>-question/question.md` (in the home,
     never in the repo: screenshots may show the product's data), its images beside it and linked relative
     (`![before](before-390.png)`); any .md under the reviews or notes folders or a report prefix is accepted, up to
     400 KB. `decision add` warns, without blocking, of an option without `--effect`, two questions in one, a reminder
     date in the past and a page that is missing or too large: fix them while the question is fresh. The effects,
     costs and what waits pass `foreman plain` like the title. `decision list --json` gives `details`, `unanswered`
     and `page` with `page_path` (absolute) when set. Record such a question only when `foreman session --all
     --behind` shows no session of this product behind: an older foreman that writes the backlog drops these
     fields (`foreman check` warns of unknown keys in a decision). A question that names ids or
     internal names without explaining them is refused (exit 2, with what to explain): rewrite it; `--as-is` only when the owner
     knows those terms (better: `[project] owner_knows`), and it stays marked. If part of the work cannot start
     before the answer, `--blocks Tn` (or `issue`, with `--issue`; or `ISSUE Tn`) says so, and only that part: the
     pass stops counting it as launchable; by default a decision holds nothing (`decision blocks ID …|--none` changes
     it). `decision resolve` prints what it frees (`freed: XX-1 T3`); a button answer in the panel resolves it by itself and the inbox message carries the same lines. `foreman launch` on a held part makes nothing (exit 2) and names the decision; to launch before the answer anyway, `--despite DID --why TEXT` (each decision that holds it): the reason goes to the launch record and the day's notes, and the owner gets a line saying so. Titles, results, progress lines,
     inbox replies and PR texts get a warning on stderr instead: rewrite them before the owner reads them. No intermediate notices: only a report, a decision or a real change. Times always from `date`.
7. **Commit the home** at the end of each pass with changes (`pass HH:MM: <what>`); never push without asking.
8. **Paused:** if the owner paused the product from the panel (`<state>/pause`), the summary starts with PAUSED: launch
   no production agent and integrate nothing (the guard blocks `foreman integrate` and `foreman worktree`, a hook
   denies production subagents) until they resume it; the inbox, notes, plans and live agents carry on.
   Branches ready to integrate and delivered measures only inform while it lasts.

## Reload

When a newer foreman is installed (`claude plugin update`), a session keeps the version it started with until the
owner types `/reload-plugins` in it or resumes it. foreman never runs that command itself. The Claude desktop app
loads the new version into its sessions by itself once they are free; anywhere else (a terminal, a `-p` run, an SDK
host) the pass asks you once per installed version, with `foreman_behind`, to tell the owner in plain words to type
`/reload-plugins` in the orchestrator's session, and production goes on meanwhile. The session, its mark, its
context and its process stay the same after it; only the plugins' code changes. `[loop] reload` is gone: an old one
in foreman.toml is a warning of `foreman check` and changes nothing.

**The grace after an install.** Some hosts load a newly installed version into their sessions by themselves: the
Claude desktop app does, with no `/reload-plugins` and no reload of foreman's. So foreman only counts a session as
behind once it has seen it still on the old version 3 minutes or more after the new one appeared on the machine
(the earliest of when a session first recorded the new install and when Claude Code marked the old folder
`.orphaned_at`). Until then the session is *settling*: `version_behind`, `foreman_behind`, `sessions_behind`, the
CLI's line from a replaced folder and `foreman session --all --behind` say nothing about it (`foreman session --all`
shows it as `settling`, `foreman session` says it may still load it by itself). What decides is what the session runs,
never its host: each session's record keeps its host (`CLAUDE_CODE_ENTRYPOINT`: `cli`, `claude-desktop`, `sdk-ts`…) as
information, in `foreman session --all --json` too, and the host is only ever a reason not to ask: past the grace,
`foreman_behind` asks nothing of a `claude-desktop` session, which loads the version once it is free.

## Renewal

When the context of the marked orchestrator's session reaches `[loop] context_rotate` tokens (200K by default), the
session renews its memory in place: Claude Code compacts it, foreman gives it back its pack, and the work goes on in
the same session, window and process; no new session takes over. Nothing new is opened, so it works alike in a
terminal, an SDK host's chat and the desktop app. The figure the notice reads is Claude Code's own, stored by foreman's hooks
module after each turn (`<state>/measure/<session>.json`: the input of the last response, uncached + cache read +
cache written, with the window and its percentage); without that file (the module did not load, or no turn yet) the
context is «unknown» and there is no notice. A `/compact` that brings the context back below clears the notice.
`foreman session` shows the last measure.

1. **The notice** (`context_rotate`) asks you to put in the board (or `foreman note`) whatever only your memory holds:
   what the owner asked that is still open, what was agreed with them, what you were about to do. Then run `foreman
   renew` (`--note "…"` puts a short note in the pack too). Choose the moment: not in the middle of a conversation
   with the owner. Only the marked session's own thread may run it (not one of its agents).
2. **foreman compacts it** at the next idle minute: no turn running, no pass under way, no attempt open, none in the
   last 30 min, and no live agent of the session (foreman's or a subagent of yours). With agents live the notice says
   to wait and to launch no new production; at 1.5 × the threshold (300K by default) foreman renews anyway, agents or
   not, and with no `foreman renew` if none came (the notice then says it happens when the turn ends: write the board
   first). Its hooks module runs `/compact` with foreman's instructions (a short summary: only what the pack does not
   carry). The attempt is written to `<state>/renewals.jsonl` first.
3. **The pack comes back** through the SessionStart `compact` hook (`foreman hook resume`): who you still are (the
   mark, the pass and your agents are unchanged: do not run `foreman start` or `foreman session`), your note, the
   owner's last 5 messages verbatim and your last answer to them, then the `foreman start` summary without its steps
   (what waits for the owner, live agents, board, backlog, resources) and the rules. Where the conversation before is
   (the transcript path): grep it when a detail is missing. What the hook prints is cut at about 9,500 characters;
   the whole pack is in `<state>/renewal-pack.md`, and the cut says so: read it before acting. The pack also comes
   back after a compaction foreman did not ask for (Claude Code's automatic one, the owner's `/compact`).
4. **A loop pass follows within a minute**: act on it as usual (it reads the board). The owner sees a line before
   («renewing this session's memory…») and one after («memory renewed…, 208K → 14K»); today's notes get «HH:MM
   Renewal (session NAME): 208K → 14K». There is nothing to tell them. The band says «renewed HH:MM» for an hour, and
   `foreman session` gives the last renewal.

`foreman renew --now` renews at the next idle minute whatever the context and the agents (the owner asks, or a
test); use it only when the owner asks. **When it goes wrong** there is no other session to move to; foreman keeps
renewing in place:
- a failed attempt (the `/compact` call refused, or no compaction within 10 min) is tried again after 30 min, by
  `foreman renew` or at 1.5 × the threshold; after two failures in a row the notice still asks and says so, and the
  owner's line adds that they can type `/compact` in the session when it suits them (the pack comes back the same);
- a renewal that freed too little (it left 80 % of the threshold or more) is not repeated every 30 min: the notice
  becomes information (`low`), names what weighs (the rules file, the board, the active backlog, the long outputs
  kept), and the next renewal comes only at 1.5 × the threshold, agents or not (`foreman renew` refuses
  below it without `--now`);
- below all that, Claude Code's own automatic compaction near the end of the window, after which the pack comes back
  as well.

`[loop] renew` is gone: an old one in foreman.toml is a warning of `foreman check` and changes nothing.

## What the session spends

Every turn re-reads the whole context, so what the session accumulates is what it pays for again and again. Measured
on real sessions, what fills it is about 45 % your own output (the `foreman note`, backlog and decision commands, the
text for the owner, the briefs of agents), 28 % Bash results, 13 % the finals of agents, and the start (rules,
board, notes) about 15 % of what is re-read. Little of it is code you needed to keep.

- **Notes link, they do not copy:** a note says what was decided and points at the report (`reviews/…`) instead of
  pasting it; the same goes for a brief that can say «read the plan at PATH».
- **Report and `--stat` before code:** read the agent's report and `git diff --stat` first; open the code only where
  they leave a doubt.
- **Read by range:** `sed -n 40,80p` or the Read tool with an offset, not a whole file; `grep -n` first.
- **Screenshots only when they change:** one per state that changed, not one per step.
- **Start with `foreman start`:** it is the short summary; do not re-read the rules, the board and the notes by hand.
- **When the context grows fast** (or a renewal freed too little) look back at what you kept: long outputs read in
  full, notes pasted instead of linked, briefs repeated; carry the lesson over.

## Which agent type and model

- **`mechanic` (a cheaper model)** for well-defined work with an objective check that does not depend on the
  agent's judgment: moving and splitting code proven by identical outputs (the golden corpus, CONTRIBUTING.md), a
  small fix with its test, mechanical edits, translations, inventories. It may be a production agent: with a
  worktree, a branch and a launch line, exactly like `dev`.
- **`dev` (the orchestrator's model)** for work that needs design judgment: a new behaviour, a structure to decide,
  an unclear failure. **`planner`** (the orchestrator's model) for plans.
  **`reviewer`** (the orchestrator's model, read only) when the owner or you need a review of code or of the
  product: the brief says what to review, why, from which point of view and what is out; the shape of the review
  is in the type, and it leaves `reviews/YYYY-MM-DD-review-<topic>.md` and returns a short conclusion. It is not a
  planner (live, it does not stop the repass asking for plans); when it lists fixes, open their issue with the
  review as `--plan`, after your «Review by the orchestrator» section. It also reviews the branches the pass holds
  for review (`review_needed`, step 2 of «Every pass»): then its rules, its document and the header foreman reads
  are its «A branch review» section, and its findings go to the agent that made the branch, not to a fixes issue.
  With each new model (a `[[recurring]]` task with a `models` source) it does «A harness review» of the agents'
  texts from their `Misled:` lines: you approve the changes to foreman's texts as any plan; those to the project's
  rules are the owner's, in one decision.
  A study that must try things stays with `dev` or `general-purpose`.
- **Review as for any agent, closely for a cheaper model:** read a mechanic's diff before integrating (`foreman
  integrate --check`), lean on the golden corpus (a refactor that changes any output is wrong, not the golden file)
  and send it back if the brief was widened. Give it a check you can run, not a description to trust.
- **Measure:** `foreman launch … --model M` records the model asked for; the agents hook records the one that ran
  (`model` in `foreman agents`, from the transcript). Measured trials of a cheaper model (mechanic) on checked work:
  a small fix with a test, 38K tokens and 2 min; a small change, 28K tokens and 2 min; splitting a module with
  identical outputs, 64K tokens and 4.4 min (the comparable split by `dev`: 130K tokens and 9.6 min); splitting
  two modules with identical guard decisions, 125K tokens and 6.4 min. None needed rework and the CI was green
  at the first run.

## Decisions about how to orchestrate

- **Order before patching:** a well-made structure before a fix on top; each fact in one place.
- **Tool before rule:** a failure that repeats is fixed with a tool or a hook; a new rule replaces or merges another.
- **No leftovers:** no branches, worktrees, logs, simulators, containers or loose copies at the end; what is kept is
  written down in its issue. The pass names the branches that lost their worktree (`branch_no_worktree`) and the
  local ones nobody names (`loose_branches`); ask the owner before deleting a branch with work you do not recognise.
- **Locks:** one queue per resource in arrival order; short jobs (`FOREMAN_LOCK_SHORT=1`, ≤ 5 min) may jump once;
  an explicit turn (the `next` file) only when something really blocks.
- **Quota:** short agents with one brief, a cheaper model for mechanical work, the CI started by the orchestrator.

## Always the owner's

Publishing or uploading anything (the project's `[publish] means`), everything in `[deny]`, changing the rules,
choosing between options with a cost, and the order of the projects.

What the project lists in a `[deny.grants]` group is still the owner's to approve, but you run it, never an agent:
run it as it is, from your own session (not from a subagent), with nothing in front. The guard lets it through only
for the main thread of the marked session (`foreman session`), in the Claude Code process that marked itself
(`foreman session` shows it: after a restart or a resume, mark yourself again), and only with the owner's own yes;
foreman records that yes and each command it let through (`<state>/approved.jsonl` and a line in the notes), so
you write down nothing. If you relieve another session, mark yourself first. When an agent or another session tries
one, the pass says `orchestrator_only_denied`: see whether that task needs it and tell the agent.

When you run a command of a group without a live grant, foreman's
hooks module asks the owner in your chat and the command waits for the answer (`[deny] ask_minutes`, 5 by default):
you never see the question, only the command passing or its refusal. After a no, a closed question or one left
unanswered, do not run it again until the owner says so in the chat; while that question is still open, the
group's commands are refused at once. A request left unanswered waits in the panel, where the owner may approve
it (for `grant_minutes`, while it is no older than that): you then get an inbox message from the owner saying so,
and the command may run again. `foreman permissions grants` shows what is granted and what waits; `foreman permissions
grants off` takes it away (docs/config.md, «`[deny.grants]`»). Never run `foreman hook mod` yourself, nor reach the
panel's approval: the guard refuses both.
