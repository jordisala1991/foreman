# Extending foreman: an example

"quayside", an invented web service, adapted without changing foreman (docs/extending.md). The folder is laid out
as its repo, with the home inside it:

| file | layer | what it shows |
|---|---|---|
| `ops/foreman.toml` | configuration | its own production type, `[loop] lock_long_min`, `[strings]`, and the commands below at their points |
| `ops/checks/state-size.sh` | a command at `loop.checks` | a JSON notice that informs or asks for action, from `FOREMAN_STATE` and `FOREMAN_PROJECT` |
| `ops/checks/worktrees.py` | a command at `loop.checks` | the resolved configuration from `"$FOREMAN_BIN" config --json` (the worktrees folder, defaults included) instead of parsing foreman.toml |
| `ops/ci-summary.sh` | a command at `repo.ci_summary` | the CI's output on stdin, one line for the PR comment |
| `.claude/agents/quayside-dev.md` | Claude Code | a production agent type with its own name, listed in `[agents] production_types` |

The selftest loads `ops/foreman.toml` strictly, checks the agent's header and runs its commands on a fake project
(tests/test_config.py).
