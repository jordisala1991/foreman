# foreman (next)

One responsible agent per task, a loop without a model, a short guard, and a system that watches itself.

- **Backlog**: tasks are *to define* or *ready* (`backlog.json`, today's schema 5 plus the new states).
- **Loop** (`fm loop`, a plain process launchd can keep alive): for a task to define it launches a defining agent; for
  a ready one, its responsible agent. Each is a Claude Code session of its own (`claude --bg -n NAME`), so the owner
  can open it and talk to it.
- **Responsible agent**: does the whole task in its own worktree, asks the owner only what only he can answer
  (`fm ask`), and delivers with `fm done`.
- **Review and integration**: the loop has every delivered branch reviewed by an independent reviewer session, scores
  its merge risk, and puts it in the integration queue. The owner accepts in the panel; the project's autonomy level
  may let low risks in alone.
- **Guard**: a short reminder in agent sessions; publishing, pushing to the main branch, deleting what is not yours and
  changing `fm.toml` ask the owner first.
- **Shared resources**: `fm lock NAME run "what" command…`.
- **Health and metrics**: broken states become triage tasks; cost, time, rework and what misled agents per task.

Python 3.11+ standard library only; the panel is React + Vite, built into `panel/dist`.

```sh
bin/fm --home ~/my-project-home init --repo ~/src/my-repo --prefix MY
bin/fm loop            # or: bin/fm launchd > ~/Library/LaunchAgents/dev.fm.loop.plist
bin/fm panel --port 7788
python3 -m unittest discover -s tests
```

`DESIGN.md` has the architecture: the states of a task, the loop, the agents' briefs, the guard list, locks, health.
