# foreman (next)

One responsible agent per task, a loop without a model, a short guard, and a system that watches itself.

- **Backlog**: tasks are *to plan* or *ready* (`backlog.json`, today's schema 5 plus the new states).
- **Loop** (`fm loop`, a plain process launchd can keep alive): for a task to plan it launches a planning agent; for
  a ready one, its responsible agent. Each is a Claude Code session of its own (`claude --bg -n NAME`), so the owner
  can open it and talk to it.
- **Responsible agent**: does the whole task in its own worktree, asks the owner only what only he can answer
  (`fm ask`), and delivers with `fm done`.
- **Review and merge**: the loop has every delivered branch reviewed (the hunter, then the reviewer), scores its merge
  risk, and leaves it in review. The owner accepts in the panel; the project's autonomy may let low risks merge alone.
- **Guard**: a short reminder in agent sessions; publishing, pushing to the main branch, deleting what is not yours and
  changing `fm.toml` ask the owner first.
- **Shared resources**: `fm lock NAME run "what" command…`.
- **Health and metrics**: broken states become triage tasks; cost, time, rework and what misled agents per task.

Python 3.12+ standard library only; the panel is React + Vite, built into `panel/dist`.

```sh
bin/fm --home ~/my-project-home init --repo ~/src/my-repo --prefix MY
bin/fm loop            # or: bin/fm launchd --dir ~/Library/LaunchAgents  (two jobs, loop and panel: start at login, restart if they die)
bin/fm panel --port 7788
make check            # (needs uv) everything CI runs, in order: lint, format, types, arch (tach), suppressions, size, test (make fix: the mechanical fixes)
python3 -m unittest discover -s tests   # just the tests
```

Architecture: DESIGN.md "Architecture"; layers and adapters are enforced by `tach.toml` (`make arch`), and a suppression comment silences nothing (`--ignore-noqa`, pyright `enableTypeIgnoreComments = false`, `make suppressions` for the rest).

Limits (pyproject.toml): Python files 300 lines (tests 600, checked by pylint), functions complexity 10 (ruff). No exceptions: a failing rule is a design signal to refactor, and `make lint` runs ruff with `--ignore-noqa`, so a comment cannot turn a rule off. The Python dev tools (ruff, pyright, pylint, tach) are the `dev` dependency group in `pyproject.toml`, locked in `uv.lock`; to change a version, edit it and run `uv lock`. fm itself has no runtime dependencies.

`DESIGN.md` has the architecture: the states of a task, the loop, the agents' briefs, the guard list, locks, health.
