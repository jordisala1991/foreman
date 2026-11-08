"""Images of a report: the pure rules, the `fm-evidence` branch on GitHub, the panel's /evidence and the guard."""

import json
import subprocess
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from helpers import Env, git
from test_github import GH

from fm import backlog, evidence, guard, loop, pulls
from fm.providers import github, registry

PNG = b"\x89PNG\r\n\x1a\n-evidence"
BODY = "Did X\n\n![the home](x.png)\n"


class Rules(unittest.TestCase):
    def test_names_and_relink(self):
        md = "![a](x.png) ![b](http://h/y.png) ![c](d/z.png) ![d](x.png) ![e](n.txt)"
        self.assertEqual(evidence.names(md), ["x.png"])
        out = evidence.relink(md, {"x.png": "U"})
        self.assertEqual(out, "![a](U) ![b](http://h/y.png) ![c](d/z.png) ![d](U) ![e](n.txt)")

    def test_guard_push_evidence(self):
        sc = guard.Scope("/w", [Path("/w")], "fm/ty-1", Path("/h/fm.toml"), [])
        for cmd in ("git push origin HEAD:fm-evidence", "git push origin abc123:refs/heads/fm-evidence"):
            self.assertEqual(guard.check_bash(cmd, sc), "push-evidence", cmd)
        self.assertIsNone(guard.check_bash("git push origin fm/ty-1", sc))


class OnGitHub(unittest.TestCase):
    def setUp(self):
        self.g = GH(test="true")
        self.addCleanup(self.g.close)

    def deliver(self, text=BODY, image=PNG, name="x.png"):
        g = self.g
        iid = g.work()
        path = g.e.home / "reports" / f"{iid}.md"
        path.parent.mkdir(exist_ok=True)
        path.write_text(text)
        if image is not None:
            (path.parent / iid).mkdir(exist_ok=True)
            (path.parent / iid / name).write_bytes(image)
        backlog.set_status(g.e.p, iid, "in review", report={"summary": "s", "confused": "", "file": str(path)})
        g.e.review(iid)
        return iid

    def ticks(self, n=3):
        for _ in range(n):
            loop.tick(self.g.e.p)

    def body(self):
        return self.g.state["prs"]["1"]["body"]

    def remote(self, *args):
        return subprocess.run(["git", "-C", str(self.g.bare), *args], capture_output=True, check=False).stdout

    def test_github_pushes_evidence_and_links(self):
        iid = self.deliver()
        self.ticks()
        self.assertIn(f"![the home](https://github.com/o/r/blob/fm-evidence/{iid}/x.png?raw=true)", self.body())
        self.assertEqual(self.remote("show", f"fm-evidence:{iid}/x.png"), PNG)

    def test_evidence_leaves_task_branch_and_checkout(self):
        g = self.g
        iid = self.deliver()
        repo = g.e.repo
        before = (git(repo, "rev-parse", g.e.issue(iid)["branch"]), git(repo, "status", "--porcelain"), git(repo, "rev-parse", "HEAD"), git(repo, "branch"))
        self.ticks()
        self.assertIn("fm-evidence", self.body())
        after = (git(repo, "rev-parse", g.e.issue(iid)["branch"]), git(repo, "status", "--porcelain"), git(repo, "rev-parse", "HEAD"), git(repo, "branch"))
        self.assertEqual(before, after)
        self.assertNotIn(b"fm-evidence", self.remote("branch", "--list", g.e.issue(iid)["branch"]))

    def test_evidence_twice_pushes_once(self):
        iid = self.deliver()
        self.ticks()
        i = self.g.e.issue(iid)
        backlog.update(self.g.e.p, iid, pr={**i["pr"], "described": ""})
        self.ticks()
        self.assertEqual(self.remote("rev-list", "--count", "fm-evidence").strip(), b"1")
        self.assertIn("fm-evidence", self.body())

    def test_changed_image_is_pushed_again(self):
        iid = self.deliver()
        self.ticks()
        (self.g.e.home / "reports" / iid / "x.png").write_bytes(PNG + b"2")
        i = self.g.e.issue(iid)
        backlog.update(self.g.e.p, iid, pr={**i["pr"], "described": ""})
        self.ticks()
        self.assertEqual(self.remote("show", f"fm-evidence:{iid}/x.png"), PNG + b"2")
        self.assertEqual(self.remote("rev-list", "--count", "fm-evidence").strip(), b"2")

    def test_symlinked_image_is_not_published(self):
        g = self.g
        secret = g.e.tmp / "secret.png"
        secret.write_bytes(b"secret")
        iid = g.work()
        folder = g.e.home / "reports" / iid
        folder.mkdir(parents=True)
        (folder / "k.png").symlink_to(secret)
        self.assertEqual(pulls.evidence_files(g.e.p, iid, "![x](k.png)"), {})
        self.assertEqual(registry.get(g.e.p).evidence({"id": iid}, {"k.png": folder / "k.png"}), {})
        self.assertEqual(self.remote("branch", "--list", "fm-evidence"), b"")

    def test_non_ascii_name_is_not_published_twice(self):
        for name in ("é.png", "a b.png", "$x.png"):
            self.assertFalse(evidence.bare(name), name)
        self.assertEqual(evidence.names("![a](é.png) ![b](ok-1_2.png)"), ["ok-1_2.png"])

    def test_second_task_keeps_first(self):
        g = self.g
        first = self.deliver()
        self.ticks()
        reg = registry.get(g.e.p)
        other = g.e.tmp / "y.png"
        other.write_bytes(b"other")
        reg.evidence({"id": "TY-99"}, {"y.png": other})
        self.assertEqual(self.remote("show", f"fm-evidence:{first}/x.png"), PNG)
        self.assertEqual(self.remote("show", "fm-evidence:TY-99/y.png"), b"other")

    def test_missing_image_is_left_alone(self):
        self.deliver(image=None)
        self.ticks()
        self.assertIn("![the home](x.png)", self.body())
        self.assertEqual(self.remote("branch", "--list", "fm-evidence"), b"")

    def test_no_images_no_push(self):
        self.deliver(text="Did X\n")
        self.ticks()
        self.assertEqual(self.remote("branch", "--list", "fm-evidence"), b"")
        self.assertEqual(self.g.calls("repo"), [])

    def test_big_image_skipped(self):
        self.deliver(image=b"0" * (evidence.MAX_BYTES + 1))
        self.ticks()
        self.assertIn("![the home](x.png)", self.body())
        self.assertEqual(self.remote("branch", "--list", "fm-evidence"), b"")

    def test_evidence_push_failure_is_transient(self):
        g = self.g
        hook = g.bare / "hooks" / "pre-receive"
        hook.write_text("#!/bin/sh\ngrep -q fm-evidence && exit 1\nexit 0\n")
        hook.chmod(0o755)
        iid = self.deliver()
        reg = registry.get(g.e.p)
        with self.assertRaises(github.TransientError):
            reg.evidence({"id": iid}, {"x.png": g.e.home / "reports" / iid / "x.png"})
        self.ticks(2)
        self.assertIn("![the home](x.png)", self.body())  # written, unlinked
        self.assertFalse(g.e.issue(iid)["pr"].get("described"))
        hook.unlink()
        self.ticks(2)
        self.assertIn("?raw=true", self.body())
        self.assertTrue(g.e.issue(iid)["pr"].get("described"))

    def test_local_has_no_evidence_push(self):
        e = Env()
        self.assertEqual(registry.get(e.p).evidence({"id": "TY-1"}, {"x.png": Path("x.png")}), {})
        self.assertEqual(pulls.evidence_files(e.p, "TY-1", BODY), {})


class Panel(unittest.TestCase):
    def setUp(self):
        from test_panel import PanelTest

        self.t = PanelTest()
        self.t.setUp()
        self.addCleanup(self.t.tearDown)
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
        self.assertEqual(self.get(self.q("x.png")), (200, "image/png", PNG))
        self.assertEqual(self.get(self.q("x.png"), key=False)[0], 403)

    def test_panel_evidence_refuses_paths(self):
        for name in ("../../fm.toml", "a/b.png", "../secret.png", "..", "x.txt", "nope.png", "/etc/passwd"):
            self.assertEqual(self.get(self.q(name))[0], 404, name)
        self.assertEqual(self.get("/evidence?p=demo&id=..&name=x.png")[0], 404)
        (self.folder / "k.png").symlink_to(self.t.p.home / "reports" / "secret.png")
        self.assertEqual(self.get(self.q("k.png"))[0], 404)

    def test_panel_description_links_existing_images(self):
        t = self.t
        status, _, body = self.get(f"/api/issue?p=demo&id={t.work}")
        desc = json.loads(body)["issue"]["description"]
        self.assertIn(f"![the home](/evidence?p=demo&id={t.work}&name=x.png)", desc)
