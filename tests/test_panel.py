"""The panel's server against a temporary project: the key is required, the state reads, the owner's actions write
through the same functions as `fm`."""

import json
import os
import tempfile
import threading
import urllib.error
import urllib.request
from collections.abc import Iterator
from pathlib import Path
from urllib.parse import quote

import pytest
from helpers import install_fm, version_folder

from fm import launcher, panel, util
from fm.core.project.config import Project
from fm.core.project.registry import register
from fm.core.tasks import backlog, questions


def start_panel(t) -> None:
    """A demo project with a task in progress, a question and a permission, and the panel serving it, on `t`."""
    t.tmp = tempfile.TemporaryDirectory()
    root = Path(t.tmp.name)
    t.env = {k: os.environ.get(k) for k in ("FM_REGISTRY",)}
    os.environ["FM_REGISTRY"] = str(root / "projects.json")
    home = root / "demo"
    home.mkdir()
    # `claude` is /usr/bin/false: no real sessions are listed or stopped
    (home / "fm.toml").write_text('[project]\nname = "demo"\nprefix = "DE"\nrepo = "repo"\n\n[agents]\nclaude = "/usr/bin/false"\n', encoding="utf-8")
    util.write_json(home / "backlog.json", backlog.empty())
    register(home)
    t.p = Project.load(home)
    t.triage = backlog.add(t.p, "A health finding", status="triage", criteria="it is gone")
    t.work = backlog.add(t.p, "Some work", status="ready", criteria="it works")
    backlog.set_status(t.p, t.work, "in progress", agent={"name": "de-2", "short": "abc123"})
    t.qid = questions.ask(t.p, t.work, "de-2", "Which colour?")
    t.perm = questions.ask(t.p, t.work, "de-2", "push the tag", kind="permission", action="publish")
    t.srv, t.key = panel.make_server(0, token="test-key")
    t.base = f"http://127.0.0.1:{t.srv.server_address[1]}"
    threading.Thread(target=t.srv.serve_forever, daemon=True).start()


def stop_panel(t) -> None:
    t.srv.shutdown()
    t.srv.server_close()
    for k, v in t.env.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    t.tmp.cleanup()


class TestPanel:
    @pytest.fixture(autouse=True)
    def set_up(self) -> Iterator[None]:
        start_panel(self)
        yield
        stop_panel(self)

    def call(self, path: str, body: str | None = None, key: bool = True, headers: dict[str, str] | None = None) -> tuple[int, object]:
        url = self.base + path + ((("&" if "?" in path else "?") + "key=" + self.key) if key else "")
        req = urllib.request.Request(url, data=body.encode() if body is not None else None, method="POST" if body is not None else "GET", headers=headers or {})
        try:
            with urllib.request.urlopen(req) as r:
                text = r.read().decode()
                return r.status, json.loads(text) if r.headers.get_content_type() == "application/json" else text
        except urllib.error.HTTPError as e:
            with e:
                return e.code, e.read().decode()

    def test_task_view_carries_the_description(self) -> None:
        report = Path(self.tmp.name) / "r.md"
        report.write_text("Did X")
        done = {"summary": "s", "confused": "", "file": str(report)}
        backlog.set_status(self.p, self.work, "in review", report=done, check={"head": "0123456789abcdef", "at": "then"})
        _, data = self.call(f"/api/task?p=demo&id={self.work}")
        assert "Did X" in data["task"]["description"]
        assert "012345678" in data["task"]["description"]
        _, other = self.call(f"/api/task?p=demo&id={self.triage}")
        assert "description" not in other["task"]

    def test_key_required(self) -> None:
        assert self.call("/api/state", key=False)[0] == 403
        assert self.call("/api/state?key=wrong", key=False)[0] == 403
        assert self.call("/api/go?id=" + self.triage, "", key=False)[0] == 403
        code, _ = self.call("/api/state", key=False, headers={"Cookie": "fm_key=test-key"})
        assert code == 200

    def test_state(self, request) -> None:
        code, d = self.call("/api/projects")
        assert code == 200
        assert d["projects"][0]["name"] == "demo"
        assert d["projects"][0]["needs"] == 3  # two open questions and the triage task
        code, d = self.call("/api/state?p=demo")
        assert code == 200
        assert {i["id"] for i in d["tasks"]} == {self.triage, self.work}
        assert [q["id"] for q in d["questions"]] == [self.qid, self.perm]
        code, d = self.call(f"/api/task?p=demo&id={self.work}")
        assert code == 200
        assert d["task"]["agent"]["short"] == "abc123"
        assert any("asks the owner" in e["text"] for e in d["events"])
        code, d = self.call("/api/system?p=demo")
        assert code == 200
        assert not d["loop"]["alive"]
        assert "loop-dead" in [f["key"] for f in d["findings"]]
        assert len(d["metrics"]) == 2
        assert d["versions"] == {"loop": None, "panel": launcher.ROOT.name, "installed": None}
        install_fm(request, version_folder(Path(self.tmp.name), "ccc"))  # helpers' HOME: never the machine's own record
        assert self.call("/api/system?p=demo")[1]["versions"]["installed"] == "ccc"
        util.write_json(self.p.state / "loop.json", {"version": "aaa"})
        assert self.call("/api/system?p=demo")[1]["versions"]["loop"] == "aaa"
        assert self.call("/api/state?p=nope")[0] == 404

    def test_answer_and_go(self) -> None:
        code, _ = self.call(f"/api/answer?p=demo&id={quote(self.qid)}", "Blue")
        assert code == 200
        d = backlog.load(self.p)
        q = next(x for x in d["decisions_resolved"] if x["id"] == self.qid)
        assert (q["answer"], q["delivered"]) == ("Blue", False)  # the loop gives it back to de-2
        assert self.call(f"/api/answer?p=demo&id={quote(self.perm)}", "yes")[0] == 409  # a permission: allow/deny
        code, _ = self.call(f"/api/go?p=demo&id={self.triage}", "")
        assert code == 200
        assert backlog.get(backlog.load(self.p), self.triage)["status"] == "backlog"  # it had criteria: planned first
        assert self.call(f"/api/go?p=demo&id={self.triage}", "")[0] == 409  # no longer in triage

    def test_allow_add_pause(self) -> None:
        assert self.call(f"/api/allow?p=demo&id={quote(self.perm)}", "")[0] == 200
        grants = util.read_json(self.p.state / "grants.json", [])
        assert [(g["task"], g["action"]) for g in grants] == [(self.work, "publish")]
        code, d = self.call("/api/add?p=demo", json.dumps({"title": "New", "criteria": "done"}))
        assert code == 200
        assert backlog.get(backlog.load(self.p), d["id"])["status"] == "backlog"
        assert self.call("/api/pause?p=demo&on=1", "")[0] == 200
        assert (self.p.state / "paused").exists()
        flying = sum(1 for i in backlog.tasks(backlog.load(self.p)) if i["status"] in ("in progress", "refining", "in review"))
        _, system = self.call("/api/system?p=demo")
        assert isinstance(system, dict)
        assert system["in_flight"] == flying
        evil = {"Origin": "http://127.0.0.1:1"}
        assert self.call("/api/pause?p=demo&on=0", "", headers=evil)[0] == 403
        assert (self.p.state / "paused").exists()


class TestWatchVersion:
    @pytest.fixture(autouse=True)
    def keep_request(self, request):
        self.request = request  # for the cleanups of helpers

    def serving(self, stale) -> threading.Thread:
        srv, _ = panel.make_server(0, token="k")
        self.request.addfinalizer(srv.server_close)
        th = threading.Thread(target=srv.serve_forever, daemon=True)
        th.start()
        panel.watch_version(srv, 0.05, stale=stale)
        th.join(2)
        if th.is_alive():
            self.request.addfinalizer(srv.shutdown)
        return th

    def test_a_new_version_stops_the_panel(self) -> None:
        assert not self.serving(lambda: "fm@fm bbb is installed now; this runs aaa").is_alive()

    def test_the_same_version_keeps_it_serving(self) -> None:
        assert self.serving(lambda: "").is_alive()
