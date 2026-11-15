"""fm finds its project from any of the project's folders: the home, every repo, the worktrees folder and the
umbrella folder set in `[project] umbrella`; or by --project NAME; the deepest folder wins."""

import contextlib
import io
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fm import backlog, cli, config, panel_views, util
from fm.config import Project


class FindProjectTest(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()
        env = mock.patch.dict(os.environ, {"FM_REGISTRY": str(self.root / "projects.json")})
        env.start()
        self.addCleanup(env.stop)
        for k in ("FM_HOME", "FM_PROJECT", "FM_TASK"):
            os.environ.pop(k, None)
        cwd = Path.cwd()
        self.addCleanup(os.chdir, cwd)
        self.elsewhere = self.dir("elsewhere")

    def dir(self, rel: str) -> Path:
        d = self.root / rel
        d.mkdir(parents=True, exist_ok=True)
        return d

    def project(self, name: str, project_lines: str = "", tail: str = "", *, home: Path | None = None, repo: bool = True) -> Path:
        h = home or self.dir(name)
        h.mkdir(parents=True, exist_ok=True)
        lines = f'name = "{name}"\nprefix = "{name[:2].upper()}"\n'
        if repo:
            lines += f'repo = "{self.dir(name + "-repo")}"\n'
        (h / "fm.toml").write_text(f'[project]\n{lines}{project_lines}\n[agents]\nclaude = "/usr/bin/false"\n{tail}', encoding="utf-8")
        util.write_json(h / "backlog.json", backlog.empty())
        backlog.add(Project.load(h), f"{name.capitalize()} work", criteria="x")
        config.register(h)
        return h

    def run_fm(self, cwd: Path, *argv: str) -> str:
        os.chdir(cwd)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            cli.main(list(argv))
        return out.getvalue()

    def refused(self, cwd: Path, *argv: str) -> str:
        with self.assertRaises(SystemExit) as e:
            self.run_fm(cwd, *argv)
        self.assertNotIn(e.exception.code, (0, None))
        return str(e.exception.code)

    def test_list_is_the_same_from_every_folder(self) -> None:
        umbrella = self.dir("umb")
        home = self.project("alpha", f'umbrella = "{umbrella}"\n')
        self.project("beta")
        repo = self.root / "alpha-repo"
        folders = [home, repo, self.dir("alpha-repo/sub"), self.dir("alpha/worktrees/al-1"), umbrella, self.dir("umb/x")]
        outs = [self.run_fm(f, "list") for f in folders]
        self.assertIn("Alpha work", outs[0])
        self.assertEqual(outs, [outs[0]] * len(outs))

    def test_project_flag_from_any_folder(self) -> None:
        self.project("alpha")
        self.project("beta")
        self.assertIn("Beta work", self.run_fm(self.elsewhere, "--project", "beta", "list"))
        msg = self.refused(self.elsewhere, "--project", "nope", "list")
        for word in ("nope", "alpha", "beta"):
            self.assertIn(word, msg)

    def test_no_project_here(self) -> None:
        self.project("alpha")
        self.project("beta")
        msg = self.refused(self.elsewhere, "list")
        for word in ("no project here", "alpha", "beta", "--project"):
            self.assertIn(word, msg)

    def test_one_repo_as_today(self) -> None:
        self.project("alpha")
        self.assertIn("Alpha work", self.run_fm(self.root / "alpha-repo", "list"))
        self.assertEqual(Project.load(self.root / "alpha").repos, [self.root / "alpha-repo"])

    def test_several_repos(self) -> None:
        api, web = self.dir("api"), self.dir("web")
        home = self.project("multi", tail=f'\n[repos.api]\npath = "{api}"\n\n[repos.web]\npath = "{web}"\nmain = "trunk"\n', repo=False)
        self.assertIn("Multi work", self.run_fm(api, "list"))
        self.assertIn("Multi work", self.run_fm(web, "list"))
        p = Project.load(home)
        self.assertEqual(p.repo, api)
        self.assertEqual(p.get("project", "main"), "main")
        self.assertEqual(p.repos, [api, web])

    def test_both_forms_refused(self) -> None:
        home = self.project("alpha")
        with (home / "fm.toml").open("a", encoding="utf-8") as f:
            f.write(f'\n[repos.x]\npath = "{self.dir("x")}"\n')
        msg = self.refused(self.elsewhere, "--home", str(home), "list")
        self.assertIn("[project] repo", msg)
        self.assertIn("[repos.<key>]", msg)

    def test_both_forms_refused_when_registered(self) -> None:
        home = self.project("alpha")
        self.project("beta")
        with (home / "fm.toml").open("a", encoding="utf-8") as f:
            f.write(f'\n[repos.x]\npath = "{self.dir("x")}"\n')
        for msg in (self.refused(self.elsewhere, "--project", "alpha", "list"), self.refused(self.root / "alpha-repo", "list")):
            self.assertIn("[project] repo", msg)
            self.assertIn("[repos.<key>]", msg)
        self.assertIn("Beta work", self.run_fm(self.elsewhere, "--project", "beta", "list"))

    def test_unreadable_project_still_claims_its_folders(self) -> None:
        other = self.project("other")
        inner = self.dir("other/sub")
        home = self.project("alpha", f'repo = "{inner}"\n', repo=False)
        with (home / "fm.toml").open("a", encoding="utf-8") as f:
            f.write(f'\n[repos.x]\npath = "{inner}"\n')
        for cwd in (inner, home):
            msg = self.refused(cwd, "list")
            self.assertIn("[repos.<key>]", msg)
            self.assertIn(str(home), msg)
            self.assertNotIn("no registered project", msg)
        self.assertIn("Other work", self.run_fm(other, "list"))
        self.assertNotIn("no registered project", self.refused(self.elsewhere, "list"))

    def test_unreadable_project_keeps_its_good_folders(self) -> None:
        self.project("other")
        inner = self.dir("other/sub")
        home = self.project("alpha", 'repo = "../other/sub"\numbrella = "~nosuchuser_zz/x"\n', repo=False)
        with (home / "fm.toml").open("a", encoding="utf-8") as f:
            f.write("\n[repos]\nx = 3\n")
        msg = self.refused(inner, "list")
        self.assertIn(str(home), msg)
        self.assertIn("Other work", self.run_fm(self.elsewhere, "--project", "other", "list"))

    def test_repos_entry_not_a_table_refused(self) -> None:
        for tail in ("\n[repos]\nx = 3\n", 'repos = "x"\n'):
            home = self.project("alpha", repo=False)
            text = (home / "fm.toml").read_text(encoding="utf-8")
            (home / "fm.toml").write_text(tail + text if tail.startswith("repos") else text + tail, encoding="utf-8")
            self.assertIn("[repos", self.refused(self.elsewhere, "--home", str(home), "list"))

    def test_health_says_first_repo(self) -> None:
        api, web = self.dir("api"), self.dir("web")
        self.project("multi", tail=f'\n[repos.api]\npath = "{api}"\n\n[repos.web]\npath = "{web}"\n', repo=False)
        out = self.run_fm(self.elsewhere, "--project", "multi", "health")
        lines = [ln for ln in out.splitlines() if "first one" in ln]
        self.assertEqual(len(lines), 1, out)
        self.assertIn(str(api), lines[0])

    def test_shared_folder(self) -> None:
        u = self.dir("shared")
        self.project("alpha", f'umbrella = "{u}"\n')
        self.project("beta", f'umbrella = "{u}"\n')
        out = self.run_fm(self.elsewhere, "--project", "alpha", "health")
        line = next(ln for ln in out.splitlines() if str(u) in ln)
        self.assertIn("alpha", line)
        self.assertIn("beta", line)
        msg = self.refused(u, "list")
        for word in ("alpha", "beta", str(u)):
            self.assertIn(word, msg)

    def test_deepest_wins(self) -> None:
        u = self.dir("outer-umb")
        self.project("outer", f'umbrella = "{u}"\n')
        self.project("inner", home=u / "inner")
        self.assertIn("Inner work", self.run_fm(self.dir("outer-umb/inner/x"), "list"))
        self.assertIn("Outer work", self.run_fm(self.dir("outer-umb/other"), "list"))

    def test_paths_are_compared_resolved(self) -> None:
        real = self.dir("real-repo")
        link = self.root / "link"
        link.symlink_to(real)
        home = self.project("alpha", f'repo = "{link}"\n', repo=False)
        self.assertIn("Alpha work", self.run_fm(self.dir("real-repo/sub"), "list"))
        p = Project.load(home)
        self.assertEqual([q.name for q, _ in config.owners([p], link / "sub")], ["alpha"])

    def test_order(self) -> None:
        alpha, beta = self.project("alpha"), self.project("beta")
        in_alpha, in_beta = self.root / "alpha-repo", self.root / "beta-repo"
        self.assertIn("Beta work", self.run_fm(in_alpha, "--project", "beta", "--home", str(alpha), "list"))
        with mock.patch.dict(os.environ, {"FM_HOME": str(alpha)}):
            self.assertIn("Alpha work", self.run_fm(in_beta, "list"))
            self.assertIn("Beta work", self.run_fm(in_alpha, "--home", str(beta), "list"))
        with mock.patch.dict(os.environ, {"FM_PROJECT": "beta"}):
            self.assertIn("Beta work", self.run_fm(in_alpha, "list"))
        with mock.patch.dict(os.environ, {"FM_HOME": str(alpha), "FM_PROJECT": "beta"}):
            self.assertIn("Alpha work", self.run_fm(in_beta, "list"))
        with mock.patch.dict(os.environ, {"FM_HOME": str(alpha), "FM_TASK": "AL-1"}):  # an agent session
            self.assertIn("Alpha work", self.run_fm(in_beta, "list"))

    def test_loop_and_panel_serve_all(self) -> None:
        self.project("alpha")
        self.project("beta")
        os.chdir(self.root / "alpha-repo")
        self.assertEqual([p.name for p in panel_views.projects()], ["alpha", "beta"])


if __name__ == "__main__":
    unittest.main()
