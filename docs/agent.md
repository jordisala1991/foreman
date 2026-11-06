# How agents work

Every agent the orchestrator launches follows this (docs/orchestrator.md): short, with one brief, leaving the detail
in its report under `reviews/` (the next sub-issue is done by a new agent that reads it). The brief brings the task,
its branch and the agent's name; the project's rules file (`project.rules` in `foreman config`) adds what is
specific and wins when they disagree.

The brief starts with foreman's launch line, `[foreman launch TOKEN] NAME · ISSUE Tn · branch B · worktree W`: that
is your name (use it as `<you>` below), your issue and sub-issue, your branch and your worktree. foreman recorded
them when it launched you, so `foreman agents` and the panel know who you are; leave the line as it is.

## At the start

- Read the project's rules file (`project.rules` in `foreman config`) and
  whatever the brief cites; `foreman backlog show ID` gives the goal, the acceptance criterion and the plan of your
  issue.
- Work in your worktree and your own scratch folder; do not touch other agents' work.

## While working

- **Who you are, for foreman:** when your brief starts with the launch line, `foreman progress` and `foreman lock`
  know your name without `FOREMAN_WHO`: the guard hook writes down which agent runs each of those calls and the
  command finds itself there. Set `FOREMAN_WHO=<you>` only when foreman says on stderr that it cannot tell which
  agent runs a call (two identical calls at once), or for a foreman called from inside a script of yours.
- **Shared resources:** anything the project declares as a resource (GPU, simulator, containers, ports) goes inside
  `foreman lock RESOURCE run "what" command…` in the same call (a hook enforces it); the commands it runs get your
  name too. A `run`
  lasts 20 min at most: split long work into takes, so short jobs in the queue pass between them.
- **Never without the owner:** what the rules and `[deny]` list (deploys, releases, sends to real users, secrets…).
  The guard rejects it; if you think you need it, say so in your report. What the project keeps for the
  orchestrator (`[deny.grants]`) is rejected for every agent too, whatever text is in front of it; the
  orchestrator hears of the attempt. Its refusal says where it matched. Text
  that only mentions such a command passes when written with the Write or Edit tool, a quoted heredoc to `cat`,
  `echo`/`printf` to a file (not run in the same call), or as the text of `foreman note` or `git commit -m`; inline code (`python -c`,
  `node -e`, a heredoc to an interpreter), `sed` scripts, loops and tests are read as commands: edit with Edit, or
  write the script to a file and run it in a separate call. The reserved files (each product's foreman.toml and guard
  table, foreman's state, the registry, Claude Code's settings) refuse any Write or Edit, and any command that writes
  them by name (`>`, `tee`, `cp`, `mv`, `sed -i`, `rm`…; reading them is free): a change of permissions is a
  candidate copy in your folder, tested with `foreman permissions --candidate`, named in your report; a change of
  Claude Code's settings or the registry, what to change and where, in your report (only the owner's chat can be
  asked for it). The hooks and configuration of git of each product's repos (`.git/hooks/`, `.git/config`,
  `.git/info/`, `config.worktree`) and the machine's git config (`~/.gitconfig`, `~/.config/git/`) are reserved for
  you too, not for the orchestrator: for one command use `git -c KEY=VALUE …`; a hook or a setting your task needs
  goes in your report. One exception: `python3 - <<'EOF'` with a program that
  imports only text modules (`re`, `pathlib`, `json`…) and cannot start a process has its sentences read as text.
  A `git push` is judged by where it goes (the repo of the folder it runs in, its remote and the branch it would
  push), not by how it is written: to push your own branch, name it, from a folder written literally.
  Integrating is the orchestrator's: `foreman integrate` is refused to you (`--check` and `--help` pass), and so is a
  git that rewrites a main checkout holding a live home (merge, pull, switch or checkout of a branch, reset --hard,
  stash, restore or rm of foreman.toml or the guard table…): do it in your worktree (`git -C <worktree> …`, `cd
  <worktree> && git …`), or limit it to the files you mean (`git stash push -- PATH`, `git checkout -- PATH`).
- **What you check yourself:** what the CI does not see: tests of what you touch and the checks the plan asks for.
  The full suite and the remote checks are the orchestrator's (`foreman integrate`). A plan with a «Hidden check»
  has more cases, run at integration, that you cannot see: meet its contract and criterion, do not look for them;
  if one fails you are sent its symptom.
- **Time limits:** `foreman time-limit SECONDS command…` inside the lock (outside, the wait counts). A server of
  yours is stopped with `kill -TERM` and, if it does not exit in ~30 s, `kill -KILL` (not `kill -INT`: a process
  started with `&` from a non-interactive shell ignores SIGINT); never an unbounded wait while holding a lock.
- **Waiting:** only for the command's completion notice or with `foreman wait` (pid, file, line, lock) in the
  background; never `tail -f`, polling the CI, or `pgrep -f`.
- **An older foreman:** if a hook says your session runs a foreman older than the installed one, say so in your
  progress and your report, and do not count on what the newer version brings (only the owner can `/reload-plugins`).
- **Progress for the owner:** `foreman progress "text"`: a result with its figure, something that
  breaks or changes the plan, a step reached, a blocker or the delivery. One or two plain lines written as
  docs/owner.md says (the owner reads them), at most one every 15–30 min; no routine steps.
- **Notes:** what must last (figures with their source, corrections) goes with `foreman note`.

## At the end

- Deliver what you have at about 2 h. Before each long take of a resource, a `wip` commit.
- Commits with the git identity already configured: never touch `user.name`, `user.email` or `GIT_AUTHOR_*` /
  `GIT_COMMITTER_*`; no `Co-Authored-By`.
- Report in `reviews/YYYY-MM-DD-<task>.md`: what changes, how it was checked, figures, whether it meets the issue's
  acceptance criterion, what fails or is missing, and every abandoned run with its reason. When the change touches
  code: the total coverage and that of the touched modules (`bin/foreman selftest --coverage`). No report, not delivered.
  It stays technical (the orchestrator and the next agent read it); a question in it for the owner follows
  docs/owner.md.
- **What misled you**, in the report, at the start of a line: `Misled: <where>: <what> (<cost>)`, one line per thing
  in what you were given that was wrong, stale or unclear and cost you time; `<where>` is one of `brief`, `plan`,
  `issue`, `rules` (the project's), `procedure` (this page or your agent type's definition), `docs`, `foreman` (a
  message of a command, a hook or the guard), `code` (a misleading comment or name) or `environment`; the text in the
  owner's language. Nothing did: `Misled: none` (say it, so «nothing» differs from «not thought of»). It is not a
  deviation (what you did differently): it is what you were given wrong. For example `Misled: plan: it said the
  config schema stays; the schema test wants it raised (one selftest round, 10 min)`. `foreman agents --misled`
  gathers these lines to review the rules.
- No live processes, simulators, containers or leftovers in your scratch folder.
- Your last command is `foreman progress --done "result" --outcome ok|partial|failed --report reviews/<file>.md`,
  a result line as docs/owner.md says: that line is your delivery (the agents hook marks you delivered, so you stop
  counting as live and your branch shows as ready to integrate). `--report` names your report (relative to the
  home, under `[ids] report_prefixes`; it must exist, in the home or in your worktree, or nothing is written): the
  pass, the delivery check and `foreman integrate` take that file, whatever its name or folder. Leave it out only
  when the project keeps no reports or the task failed with none. The outcome says how it went: `ok` (the default) the task is done,
  `partial` part of it, `failed` it could not be done (say why in the line and the report); never hide a failure in
  the prose. A task that only measures (a report, no code for main) delivers with `--kind measure`: it needs no
  commits, only its report. Only at the very end: stopping to wait for a background job of yours is not a delivery. If you are
  resumed afterwards, run it again when you finish.
- **Resumed with a review:** a branch that touches delicate paths, is large or was delivered `partial` may need a
  review by another agent before it is integrated; its high (H) and medium (M) findings come back to you, by a
  message that names the review (`reviews/…-review-…md`), or in the brief of a new agent launched on your branch's
  worktree. Read the review whole. For each H and M: fix it (a commit, with the test the review asks for when it
  names one) or refute it with proof (a test that passes, or the `file:line` that disproves it); nothing else, no
  new scope. L findings: fix them if they are cheap, else list them as debts. Add to your report a section «Review,
  round N» (N is the review's `Round:`) with one line per finding: its id, fixed (commit, test) or refuted (the
  proof), and the debts. Commit, then `progress --done` again with the same report. The reviewer checks your fixes
  in round 2; there is no round 3: what is still open then goes to the owner.
- **The delivery check:** when you stop, foreman checks that your branch has commits beyond main (not for a
  measurement: `--kind measure`, or a sub-issue planned as one), that your report
  is in it (when the project keeps reports; the one you named with `--report`, when you named one) and has a
  `Misled:` line, and that you ran `progress --done`. If something is missing, your stop is refused with what is missing and the command to fix it: fix it, run
  `progress --done` again and stop.
- **The final message** (what the orchestrator's context pays for, so it is short and fixed-shaped): at most
  2,000 characters, never a recap of the report. Everything else goes in the report file, which the orchestrator reads
  only when it needs to. foreman records its length (`final_chars` in agents.json) at your stop; it never blocks it.

  ```
  Delivered: <branch>, <n> commits (<last sha>); report <path>
  Checks: <check> <figure>; <check> <figure>
  Deviations: <none | one line each>
  Debts: <none | one line each: what is missing, what the CI must still do>
  Result: <the one line given to progress --done (with --report <path>)>
  ```
