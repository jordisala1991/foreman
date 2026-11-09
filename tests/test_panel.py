"""The panel's server against a temporary project: the key is required, the state reads, the owner's actions write
through the same functions as `fm`."""

import json
import os
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock
from urllib.parse import quote

from fm import backlog, panel, token, util
from fm.config import Project, register


class PanelTest(unittest.TestCase):
    def setUp(self) -> None:
        patcher = mock.patch.object(token, "keychain", lambda: "")  # never the real Keychain
        patcher.start()
        self.addCleanup(patcher.stop)
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.env = {k: os.environ.get(k) for k in ("FM_REGISTRY",)}
        os.environ["FM_REGISTRY"] = str(root / "projects.json")
        home = root / "demo"
        home.mkdir()
        # `claude` is /usr/bin/false: no real sessions are listed or stopped
        (home / "fm.toml").write_text('[project]\nname = "demo"\nprefix = "DE"\nrepo = "repo"\n\n[agents]\nclaude = "/usr/bin/false"\n', encoding="utf-8")
        util.write_json(home / "backlog.json", backlog.empty())
        register(home)
        self.p = Project.load(home)
        self.triage = backlog.add(self.p, "A health finding", status="triage", criterion="it is gone")
        self.work = backlog.add(self.p, "Some work", status="ready", criterion="it works")
        backlog.set_status(self.p, self.work, "in progress", agent={"name": "de-2", "short": "abc123"})
        self.qid = backlog.ask(self.p, self.work, "de-2", "Which colour?")
        self.perm = backlog.ask(self.p, self.work, "de-2", "push the tag", kind="permission", action="publish")
        panel.forget()
        self.srv, self.key = panel.make_server(0, token="test-key")
        self.base = f"http://127.0.0.1:{self.srv.server_address[1]}"
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def tearDown(self) -> None:
        self.srv.shutdown()
        self.srv.server_close()
        for k, v in self.env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self.tmp.cleanup()

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

    def test_issue_view_carries_the_description(self) -> None:
        report = Path(self.tmp.name) / "r.md"
        report.write_text("Did X")
        done = {"summary": "s", "confused": "", "file": str(report)}
        backlog.set_status(self.p, self.work, "in review", report=done, check={"head": "0123456789abcdef", "at": "then"})
        _, data = self.call(f"/api/issue?p=demo&id={self.work}")
        self.assertIn("Did X", data["issue"]["description"])
        self.assertIn("012345678", data["issue"]["description"])
        _, other = self.call(f"/api/issue?p=demo&id={self.triage}")
        self.assertNotIn("description", other["issue"])

    def test_key_required(self) -> None:
        self.assertEqual(self.call("/api/state", key=False)[0], 403)
        self.assertEqual(self.call("/api/state?key=wrong", key=False)[0], 403)
        self.assertEqual(self.call("/api/go?id=" + self.triage, "", key=False)[0], 403)
        code, _ = self.call("/api/state", key=False, headers={"Cookie": "fm_key=test-key"})
        self.assertEqual(code, 200)

    def test_state(self) -> None:
        code, d = self.call("/api/projects")
        self.assertEqual(code, 200)
        self.assertEqual(d["projects"][0]["name"], "demo")
        self.assertEqual(d["projects"][0]["needs"], 3)  # two open questions and the triage issue
        code, d = self.call("/api/state?p=demo")
        self.assertEqual(code, 200)
        self.assertEqual({i["id"] for i in d["issues"]}, {self.triage, self.work})
        self.assertEqual([q["id"] for q in d["questions"]], [self.qid, self.perm])
        code, d = self.call(f"/api/issue?p=demo&id={self.work}")
        self.assertEqual(code, 200)
        self.assertEqual(d["issue"]["agent"]["short"], "abc123")
        self.assertTrue(any("asks the owner" in e["text"] for e in d["events"]))
        code, d = self.call("/api/system?p=demo")
        self.assertEqual(code, 200)
        self.assertFalse(d["loop"]["alive"])
        self.assertIn("loop-dead", [f["key"] for f in d["findings"]])
        self.assertEqual(len(d["metrics"]), 2)
        self.assertEqual(self.call("/api/state?p=nope")[0], 404)

    def test_answer_and_go(self) -> None:
        code, _ = self.call(f"/api/answer?p=demo&id={quote(self.qid)}", "Blue")
        self.assertEqual(code, 200)
        d = backlog.load(self.p)
        q = next(x for x in d["decisions_resolved"] if x["id"] == self.qid)
        self.assertEqual((q["answer"], q["delivered"]), ("Blue", False))  # the loop gives it back to de-2
        self.assertEqual(self.call(f"/api/answer?p=demo&id={quote(self.perm)}", "yes")[0], 409)  # a permission: allow/deny
        code, _ = self.call(f"/api/go?p=demo&id={self.triage}", "")
        self.assertEqual(code, 200)
        self.assertEqual(backlog.get(backlog.load(self.p), self.triage)["status"], "ready")  # it had a criterion
        self.assertEqual(self.call(f"/api/go?p=demo&id={self.triage}", "")[0], 409)  # no longer in triage

    def test_allow_add_pause(self) -> None:
        self.assertEqual(self.call(f"/api/allow?p=demo&id={quote(self.perm)}", "")[0], 200)
        grants = util.read_json(self.p.state / "grants.json", [])
        self.assertEqual([(g["issue"], g["action"]) for g in grants], [(self.work, "publish")])
        code, d = self.call("/api/add?p=demo", json.dumps({"title": "New", "criterion": "done"}))
        self.assertEqual(code, 200)
        self.assertEqual(backlog.get(backlog.load(self.p), d["id"])["status"], "ready")
        self.assertEqual(self.call("/api/pause?p=demo&on=1", "")[0], 200)
        self.assertTrue((self.p.state / "paused").exists())
        evil = {"Origin": "http://127.0.0.1:1"}
        self.assertEqual(self.call("/api/pause?p=demo&on=0", "", headers=evil)[0], 403)
        self.assertTrue((self.p.state / "paused").exists())


if __name__ == "__main__":
    unittest.main()
