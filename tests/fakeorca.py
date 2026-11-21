#!/usr/bin/env python3
"""A fake `orca` for the Orca adapter's tests (foreman/providers/orca/): an executable script (the test points `ORCA_BIN` at
it) that keeps Orca's state in a JSON file of its folder and logs each call. Its answers have the shapes a real headless
Orca 1.4.261 gave (reports/FN-216.md): `{"ok": true, "result": …}` or `{"ok": false, "error": {"code", "message"}}` with
exit 1; a worker's pane in `worker-show` and its `ps` row share only the part before the `:`. `worker-start` runs the
SessionStart hook of the worktree's `.claude/settings.local.json`, as claude does in the chat.

The test copies it with `FakeOrca(folder)` and drives it: `down()`, `restart()` (every chat ends; the next send answers a new dispatch id),
`terminal_mode()`, `set_row(dispatch, word)`, `settle(dispatch)`, `chat_path(dispatch)`, `age(dispatch, path)`.
A chat keeps the `PATH` of the worktree's settings file from when its process started (`worker-start`, or `send` to a chat
that ended); a rewritten file reaches a running chat's hooks and env vars, not its `PATH`."""

import json
import shutil
import subprocess
import sys
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve()


def fresh() -> dict[str, Any]:
    return {"running": True, "opens": True, "mode": "structured", "repos": [], "terminals": [], "runs": [], "workers": {}, "n": 0, "fail": []}


class FakeOrca:
    """The test's side: where the fake lives and its state."""

    def __init__(self, folder: Path) -> None:
        """Copies the fake into `folder` (its state lives beside it), as `folder/orca`."""
        self.folder = folder
        self.bin = folder / "orca"
        shutil.copy(HERE, self.bin)
        self.bin.chmod(0o755)
        self.state_file = folder / "orca-state.json"
        self.calls_file = folder / "orca-calls.jsonl"
        self.save(fresh())

    def load(self) -> dict[str, Any]:
        return json.loads(self.state_file.read_text())

    def save(self, s: dict[str, Any]) -> None:
        self.state_file.write_text(json.dumps(s))

    def set(self, **kv: Any) -> None:
        self.save({**self.load(), **kv})

    def calls(self) -> list[list[str]]:
        if not self.calls_file.exists():
            return []
        return [json.loads(line) for line in self.calls_file.read_text().splitlines()]

    def verbs(self) -> list[str]:
        """Each call's verb, e.g. `orchestration worker-start`."""
        return [" ".join(a[:2]) if a[0] in ("orchestration", "repo", "terminal", "worktree") else a[0] for a in self.calls()]

    def call(self, verb: str) -> list[str]:
        """The last call of a verb."""
        return [a for a in self.calls() if " ".join(a[:2]) == verb or a[0] == verb][-1]

    def down(self, opens: bool = True) -> None:
        """Orca stops; `orca open` brings it back only when `opens`."""
        self.set(running=False, opens=opens)

    def restart(self) -> None:
        """Orca restarts: every chat ends (FN-218 F8); its next send starts it again under a new dispatch id."""
        s = self.load()
        for w in s["workers"].values():
            w["live"] = False
        self.save(s)

    def terminal_mode(self) -> None:
        self.set(mode="terminal")

    def fail(self, verb: str) -> None:
        """Every call of `verb` (e.g. `worktree set`) answers `selector_not_found`."""
        self.set(fail=[*self.load()["fail"], verb])

    def set_row(self, dispatch: str, word: str) -> None:
        s = self.load()
        s["workers"][dispatch]["state"] = word
        self.save(s)

    def settle(self, dispatch: str) -> None:
        """The worker reported itself done: `worker-stop` then answers `alreadySettled`."""
        s = self.load()
        s["workers"][dispatch]["settled"] = True
        self.save(s)

    def workers(self) -> dict[str, dict[str, Any]]:
        return self.load()["workers"]

    def chat_path(self, dispatch: str) -> str:
        """The `PATH` the chat's process started with."""
        return str(self.workers()[dispatch]["path"])

    def age(self, dispatch: str, path: str) -> None:
        """The chat started on an older build: its `PATH` is `path`."""
        s = self.load()
        s["workers"][dispatch]["path"] = path
        self.save(s)


def flag(a: list[str], name: str) -> str:
    return a[a.index(name) + 1]


def settings_path(worktree: str) -> str:
    return str(json.loads((Path(worktree) / ".claude" / "settings.local.json").read_text())["env"]["PATH"])


def run_session_hook(worktree: str, session: str) -> None:
    settings = json.loads((Path(worktree) / ".claude" / "settings.local.json").read_text())
    for entry in settings["hooks"]["SessionStart"]:
        for h in entry["hooks"]:
            subprocess.run(h["command"], shell=True, input=json.dumps({"session_id": session}), text=True, check=True)


State = dict[str, Any]


def status(s: State, a: list[str], n: int) -> dict[str, Any]:
    return {"app": {"running": s["running"]}}


def open_(s: State, a: list[str], n: int) -> dict[str, Any]:
    s["running"] = s["opens"]
    return {}


def repo_list(s: State, a: list[str], n: int) -> dict[str, Any]:
    return {"repos": s["repos"]}


def repo_add(s: State, a: list[str], n: int) -> dict[str, Any]:
    repo = {"path": flag(a, "--path"), "externalWorktreeVisibility": "hide"}
    s["repos"].append(repo)
    return {"repo": repo}


def repo_set(s: State, a: list[str], n: int) -> dict[str, Any]:
    repo = next(r for r in s["repos"] if r["path"] == flag(a, "--repo").removeprefix("path:"))
    repo["externalWorktreeVisibility"] = flag(a, "--external-worktree-visibility")
    return {"repo": repo}


def terminal_create(s: State, a: list[str], n: int) -> dict[str, Any]:
    s["terminals"].append(f"term_{n}")
    return {"terminal": {"handle": f"term_{n}", "paneKey": f"tab{n}:leaf{n}"}}


def terminal_show(s: State, a: list[str], n: int) -> dict[str, Any]:
    if flag(a, "--terminal") not in s["terminals"]:
        raise KeyError("terminal_handle_stale")
    return {"terminal": {"handle": flag(a, "--terminal")}}


def nothing(s: State, a: list[str], n: int) -> dict[str, Any]:
    return {}


def worktree_ps(s: State, a: list[str], n: int) -> dict[str, Any]:
    rows: dict[str, list[dict[str, str]]] = {}
    for w in s["workers"].values():
        if w["live"]:
            rows.setdefault(w["worktree"], []).append({"paneKey": f"{w['chat']}:row", "state": w["state"]})
    return {"worktrees": [{"path": p, "agents": agents} for p, agents in rows.items()]}


def run_create(s: State, a: list[str], n: int) -> dict[str, Any]:
    if flag(a, "--from") not in s["terminals"]:
        raise KeyError("terminal_handle_stale")
    s["runs"].append(f"run_{n}")
    return {"runId": f"run_{n}"}


def worker_start(s: State, a: list[str], n: int) -> dict[str, Any]:
    if flag(a, "--run") not in s["runs"] or flag(a, "--from") not in s["terminals"]:
        raise KeyError("consumer_fenced")
    dispatch, worktree = f"ctx_{n}", flag(a, "--worktree").removeprefix("path:")
    w = {"worktree": worktree, "chat": f"structured-agent-session-{n}", "state": "working", "live": True, "settled": False}
    s["workers"][dispatch] = {**w, "session": str(uuid.uuid4()), "path": settings_path(worktree)}
    if s["mode"] == "structured":
        run_session_hook(worktree, s["workers"][dispatch]["session"])
    return {"dispatchId": dispatch, "mode": s["mode"], "terminal": f"structworker_{n}", "state": "ready"}


def worker(s: State, a: list[str]) -> dict[str, Any]:
    w = s["workers"].get(flag(a, "--dispatch") if "--dispatch" in a else flag(a, "--to").removeprefix("dispatch:"))
    if w is None:
        raise KeyError("dispatch_not_found")
    return w


def worker_show(s: State, a: list[str], n: int) -> dict[str, Any]:
    return {"dispatch": {"assigneePaneKey": f"{worker(s, a)['chat']}:assignee"}}


def send(s: State, a: list[str], n: int) -> dict[str, Any]:
    w = worker(s, a)
    if flag(a, "--from") not in s["terminals"]:
        raise KeyError("consumer_fenced")
    w["state"] = "working"
    if w["live"]:
        return {"messageId": f"msg_{n}"}
    new: dict[str, Any] = {**w, "live": True, "chat": f"structured-agent-session-{n}"}  # Orca starts it again: claude --resume, same session
    new["path"] = settings_path(new["worktree"])
    s["workers"][f"ctx_{n}"] = new
    run_session_hook(new["worktree"], new["session"])
    return {"delivery": "follow_up", "dispatchId": f"ctx_{n}", "messageId": f"msg_{n}"}


def worker_stop(s: State, a: list[str], n: int) -> dict[str, Any]:
    w = worker(s, a)
    settled = w["settled"]
    if not settled:
        w["live"], w["settled"] = False, True
    return {"state": "stopped", "alreadySettled": settled, "processAction": "none" if settled else "closed_agent_terminal"}


def worker_release(s: State, a: list[str], n: int) -> dict[str, Any]:
    worker(s, a)["live"] = False
    return {"state": "released", "processAction": "closed_agent_terminal"}


VERBS: dict[str, Callable[[State, list[str], int], dict[str, Any]]] = {
    "status": status,
    "open": open_,
    "repo list": repo_list,
    "repo add": repo_add,
    "repo set": repo_set,
    "terminal create": terminal_create,
    "terminal show": terminal_show,
    "worktree set": nothing,
    "worktree ps": worktree_ps,
    "orchestration run-create": run_create,
    "orchestration worker-start": worker_start,
    "orchestration worker-show": worker_show,
    "orchestration send": send,
    "orchestration worker-stop": worker_stop,
    "orchestration worker-release": worker_release,
}


def answer(s: State, a: list[str]) -> dict[str, Any]:
    """The result of one call, or raises KeyError(code) for an error answer."""
    s["n"] += 1
    verb = a[0] if a[0] in ("status", "open") else " ".join(a[:2])
    if verb not in ("status", "open") and not s["running"]:
        raise KeyError("runtime_unavailable")
    if verb in s["fail"]:
        raise KeyError("selector_not_found")
    return VERBS[verb](s, a, s["n"])


def main() -> None:
    a = [x for x in sys.argv[1:] if x != "--json"]
    with open(HERE.parent / "orca-calls.jsonl", "a") as f:
        f.write(json.dumps(a) + "\n")
    state_file = HERE.parent / "orca-state.json"
    s = json.loads(state_file.read_text())
    try:
        out: dict[str, Any] = {"ok": True, "result": answer(s, a)}
    except KeyError as e:
        print(json.dumps({"ok": False, "error": {"code": e.args[0], "message": e.args[0]}}))
        state_file.write_text(json.dumps(s))
        sys.exit(1)
    state_file.write_text(json.dumps(s))
    print(json.dumps(out))


if __name__ == "__main__":
    main()
