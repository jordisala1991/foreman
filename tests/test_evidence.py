"""Images of a report: the pure rules, the `fm-evidence` branch on GitHub, the panel's /evidence and the guard."""

import functools
import json
import subprocess
import urllib.error
import urllib.request
from pathlib import Path
from types import SimpleNamespace

import pytest
from helpers import Env, git
from test_github import GH

from fm import loop, pulls
from fm.core.oversight import guard
from fm.core.review import evidence
from fm.core.tasks import backlog
from fm.providers import Change, registry
from fm.providers.github import types

PNG = b"\x89PNG\r\n\x1a\n-evidence"
BODY = "Did X\n\n![the home](x.png)\n"


class TestRules:
    def test_names_and_relink(self):
        md = "![a](x.png) ![b](http://h/y.png) ![c](d/z.png) ![d](x.png) ![e](n.txt)"
        assert evidence.names(md) == ["x.png"]
        out = evidence.relink(md, {"x.png": "U"})
        assert out == "![a](U) ![b](http://h/y.png) ![c](d/z.png) ![d](U) ![e](n.txt)"

    def test_guard_push_evidence(self):
        sc = guard.Scope("/w", [Path("/w")], "fm/ty-1", Path("/h/fm.toml"), [])
        for cmd in ("git push origin HEAD:fm-evidence", "git push origin abc123:refs/heads/fm-evidence"):
            assert guard.check_bash(cmd, sc) == "push-evidence", cmd
        assert guard.check_bash("git push origin fm/ty-1", sc) is None


class TestOnGitHub:
    @pytest.fixture(autouse=True)
    def set_up(self, request):
        self.g = GH(test="true")
        request.addfinalizer(self.g.close)

    def deliver(self, text=BODY, image=PNG, name="x.png"):
        g = self.g
        tid = g.work()
        path = g.e.home / "reports" / f"{tid}.md"
        path.parent.mkdir(exist_ok=True)
        path.write_text(text)
        if image is not None:
            (path.parent / tid).mkdir(exist_ok=True)
            (path.parent / tid / name).write_bytes(image)
        backlog.set_status(g.e.p, tid, "in review", report={"summary": "s", "confused": "", "file": str(path)})
        g.e.review(tid)
        return tid

    def ticks(self, n=3):
        for _ in range(n):
            loop.tick(self.g.e.p)

    def body(self):
        return self.g.state["prs"]["1"]["body"]

    def remote(self, *args):
        return subprocess.run(["git", "-C", str(self.g.bare), *args], capture_output=True, check=False).stdout

    def test_github_pushes_evidence_and_links(self):
        tid = self.deliver()
        self.ticks()
        assert f"![the home](https://github.com/o/r/blob/fm-evidence/{tid}/x.png?raw=true)" in self.body()
        assert self.remote("show", f"fm-evidence:{tid}/x.png") == PNG

    def test_evidence_leaves_task_branch_and_checkout(self):
        g = self.g
        tid = self.deliver()
        repo = g.e.repo
        before = (git(repo, "rev-parse", g.e.task(tid)["branch"]), git(repo, "status", "--porcelain"), git(repo, "rev-parse", "HEAD"), git(repo, "branch"))
        self.ticks()
        assert "fm-evidence" in self.body()
        after = (git(repo, "rev-parse", g.e.task(tid)["branch"]), git(repo, "status", "--porcelain"), git(repo, "rev-parse", "HEAD"), git(repo, "branch"))
        assert before == after
        branch = g.e.task(tid)["branch"]
        files = self.remote("ls-tree", "-r", "--name-only", branch) + self.remote("diff", "--name-only", f"main...{branch}")
        assert b"app.py" in files
        assert b".png" not in files

    def test_evidence_twice_pushes_once(self):
        tid = self.deliver()
        self.ticks()
        i = self.g.e.task(tid)
        backlog.update(self.g.e.p, tid, pr={**i["pr"], "described": ""})
        self.ticks()
        assert self.remote("rev-list", "--count", "fm-evidence").strip() == b"1"
        assert "fm-evidence" in self.body()

    def test_changed_image_is_pushed_again(self):
        tid = self.deliver()
        self.ticks()
        (self.g.e.home / "reports" / tid / "x.png").write_bytes(PNG + b"2")
        i = self.g.e.task(tid)
        backlog.update(self.g.e.p, tid, pr={**i["pr"], "described": ""})
        self.ticks()
        assert self.remote("show", f"fm-evidence:{tid}/x.png") == PNG + b"2"
        assert self.remote("rev-list", "--count", "fm-evidence").strip() == b"2"

    def test_symlinked_image_is_not_published(self):
        g = self.g
        secret = g.e.tmp / "secret.png"
        secret.write_bytes(b"secret")
        tid = g.work()
        folder = g.e.home / "reports" / tid
        folder.mkdir(parents=True)
        (folder / "k.png").symlink_to(secret)
        assert pulls.evidence_files(g.e.p, tid, "![x](k.png)") == {}
        assert self.remote("branch", "--list", "fm-evidence") == b""

    def test_non_ascii_name_is_not_published_twice(self):
        for name in ("é.png", "a b.png", "$x.png"):
            assert not evidence.bare(name), name
        assert evidence.names("![a](é.png) ![b](ok-1_2.png)") == ["ok-1_2.png"]

    def test_second_task_keeps_first(self):
        g = self.g
        first = self.deliver()
        self.ticks()
        reg = registry.get(g.e.p)
        other = g.e.tmp / "y.png"
        other.write_bytes(b"other")
        reg.evidence(Change(id="TY-99"), {"y.png": other})
        assert self.remote("show", f"fm-evidence:{first}/x.png") == PNG
        assert self.remote("show", "fm-evidence:TY-99/y.png") == b"other"

    def test_missing_image_is_left_alone(self):
        self.deliver(image=None)
        self.ticks()
        assert "![the home](x.png)" in self.body()
        assert self.remote("branch", "--list", "fm-evidence") == b""

    def test_no_images_no_push(self):
        self.deliver(text="Did X\n")
        self.ticks()
        assert self.remote("branch", "--list", "fm-evidence") == b""
        assert self.g.calls("repo", "view", "--json", "nameWithOwner") == []  # the review reads the repo url for its links

    def test_big_image_skipped(self):
        self.deliver(image=b"0" * (evidence.MAX_BYTES + 1))
        self.ticks()
        assert "![the home](x.png)" in self.body()
        assert self.remote("branch", "--list", "fm-evidence") == b""

    def test_evidence_push_failure_is_transient(self):
        g = self.g
        hook = g.bare / "hooks" / "pre-receive"
        hook.write_text("#!/bin/sh\ngrep -q fm-evidence && exit 1\nexit 0\n")
        hook.chmod(0o755)
        tid = self.deliver()
        reg = registry.get(g.e.p)
        with pytest.raises(types.TransientError):
            reg.evidence(Change(id=tid), {"x.png": g.e.home / "reports" / tid / "x.png"})
        self.ticks(2)
        assert "![the home](x.png)" in self.body()  # written, unlinked
        assert not g.e.task(tid)["pr"].get("described")
        hook.unlink()
        self.ticks(2)
        assert "?raw=true" in self.body()
        assert g.e.task(tid)["pr"].get("described")

    def test_local_has_no_evidence_push(self):
        e = Env()
        assert registry.get(e.p).evidence(Change(id="TY-1"), {"x.png": Path("x.png")}) == {}
        assert pulls.evidence_files(e.p, "TY-1", BODY) == {}


class TestPanel:
    @pytest.fixture(autouse=True)
    def set_up(self, request):
        from test_panel import start_panel, stop_panel

        self.t = SimpleNamespace()
        start_panel(self.t)
        request.addfinalizer(functools.partial(stop_panel, self.t))
        t = self.t
        self.folder = t.p.home / "reports" / t.work
        self.folder.mkdir(parents=True)
        (self.folder / "x.png").write_bytes(PNG)
        report = t.p.home / "reports" / f"{t.work}.md"
        report.write_text(BODY)
        backlog.update(t.p, t.work, report={"summary": "s", "confused": "", "file": str(report)})
        (t.p.home / "reports" / "secret.png").write_bytes(b"s")

    def get(self, path, key=True):
        t = self.t
        url = f"{t.base}{path}" + (f"&key={t.key}" if key else "")
        try:
            with urllib.request.urlopen(url) as r:
                return r.status, r.headers.get("Content-Type"), r.read()
        except urllib.error.HTTPError as e:
            with e:
                return e.code, None, e.read()

    def q(self, name):
        from urllib.parse import quote

        return f"/evidence?p=demo&id={self.t.work}&name={quote(name)}"

    def test_panel_serves_evidence(self):
        assert self.get(self.q("x.png")) == (200, "image/png", PNG)
        assert self.get(self.q("x.png"), key=False)[0] == 403

    def test_panel_evidence_refuses_paths(self):
        for name in ("../../fm.toml", "a/b.png", "../secret.png", "..", "x.txt", "nope.png", "/etc/passwd"):
            assert self.get(self.q(name))[0] == 404, name
        assert self.get("/evidence?p=demo&id=..&name=x.png")[0] == 404
        (self.folder / "k.png").symlink_to(self.t.p.home / "reports" / "secret.png")
        assert self.get(self.q("k.png"))[0] == 404
        (self.folder / "big.png").write_bytes(b"0" * (evidence.MAX_BYTES + 1))
        assert self.get(self.q("big.png"))[0] == 404
        (self.folder / "ok.png").write_bytes(b"0" * evidence.MAX_BYTES)
        assert self.get(self.q("ok.png"))[0] == 200

    def test_panel_evidence_refuses_linked_folder(self):
        t = self.t
        other = t.p.home / "reports" / "other"
        other.mkdir()
        (other / "a.png").write_bytes(PNG)
        (t.p.home / "reports" / "FN-9").symlink_to(other)
        assert self.get("/evidence?p=demo&id=FN-9&name=a.png")[0] == 404
        assert pulls.evidence_files(t.p, "FN-9", "![a](a.png)") == {}

    def test_panel_description_links_existing_images(self):
        t = self.t
        status, _, body = self.get(f"/api/task?p=demo&id={t.work}")
        desc = json.loads(body)["task"]["description"]
        assert f"![the home](/evidence?p=demo&id={t.work}&name=x.png)" in desc


class TestLocal:
    def test_local_tick_pushes_nothing(self):
        e = Env(test="true")
        tid = backlog.add(e.p, "x", status="ready", criteria="y")
        loop.tick(e.p)
        wt = e.repo.parent / e.task(tid)["worktree"]
        with open(wt / "app.py", "a") as f:
            f.write("# change\n")
        git(wt, "commit", "-qam", "change")
        path = e.home / "reports" / f"{tid}.md"
        path.parent.mkdir(exist_ok=True)
        path.write_text(BODY)
        (path.parent / tid).mkdir()
        (path.parent / tid / "x.png").write_bytes(PNG)
        backlog.set_status(e.p, tid, "in review", report={"summary": "s", "confused": "", "file": str(path)})
        backlog.update(e.p, tid, pr={"number": 1})  # so the tick reaches the description and its images
        e.review(tid)
        loop.tick(e.p)
        loop.tick(e.p)
        assert e.task(tid)["pr"].get("described")
        assert not e.task(tid).get("pr_error")
        assert git(e.repo, "branch", "--list", "fm-evidence") == ""
        assert pulls.evidence_files(e.p, tid, BODY) == {"x.png": path.parent / tid / "x.png"}
        events = [json.loads(x) for x in (e.p.state / "events.jsonl").read_text().splitlines()]
        assert not [v for v in events if v.get("task") == tid and "error" in v["text"].lower()]
