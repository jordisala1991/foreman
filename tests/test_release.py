"""Version and release checks (lib/foreman/release.py): each rule broken on a copy of the plugin's manifests in
$TMPDIR."""

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from helpers import ROOT

import foreman
from foreman import release, util

GIT_ENV = {
    **os.environ,
    "GIT_AUTHOR_NAME": "t",
    "GIT_AUTHOR_EMAIL": "t@example.invalid",
    "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@example.invalid",
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_NOSYSTEM": "1",
}


class ReleaseChecks(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="foreman-release-")).resolve()
        (self.tmp / ".claude-plugin").mkdir()
        for f in (".claude-plugin/plugin.json", ".claude-plugin/marketplace.json", "CHANGELOG.md"):
            shutil.copy(ROOT / f, self.tmp / f)
        self.version = json.loads((ROOT / ".claude-plugin/plugin.json").read_text())["version"]

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def check(self, code_version=None):
        return release.check(self.tmp, code_version or self.version)

    def edit_json(self, rel, fn):
        p = self.tmp / rel
        data = json.loads(p.read_text())
        fn(data)
        p.write_text(json.dumps(data, indent=2))

    def git(self, *args):
        subprocess.run(["git", "-C", str(self.tmp), *args], check=True, capture_output=True, env=GIT_ENV)

    def bump(self, version):
        self.edit_json(".claude-plugin/plugin.json", lambda d: d.update(version=version))
        text = (self.tmp / "CHANGELOG.md").read_text()
        (self.tmp / "CHANGELOG.md").write_text(text.replace("## [Unreleased]", f"## [Unreleased]\n\n## [{version}]", 1))
        self.version = version

    def test_the_repo_itself_passes(self):
        self.assertEqual(release.check(ROOT), [])

    def test_its_git_runs_without_hooks(self):
        self.git("init", "-q", "-b", "main")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "c")
        with mock.patch("foreman.util.subprocess.run", wraps=subprocess.run) as run:
            self.assertEqual(self.check(), [])
        argvs = [c.args[0] for c in run.call_args_list]
        self.assertTrue(argvs)
        for argv in argvs:
            self.assertEqual(Path(argv[0]).name, "git")
            self.assertEqual(argv[1 : 1 + len(util.GIT_QUIET)], list(util.GIT_QUIET))

    def test_copy_passes(self):
        self.assertEqual(self.check(), [])

    def test_package_reads_plugin_json(self):
        self.assertEqual(foreman.__version__, self.version)
        saved = foreman.PLUGIN_MANIFEST
        try:
            foreman.PLUGIN_MANIFEST = self.tmp / "missing.json"
            self.assertEqual(foreman._read_version(), "0.0.0+unknown")
            self.edit_json(".claude-plugin/plugin.json", lambda d: d.update(version="9.8.7"))
            foreman.PLUGIN_MANIFEST = self.tmp / ".claude-plugin/plugin.json"
            self.assertEqual(foreman._read_version(), "9.8.7")
        finally:
            foreman.PLUGIN_MANIFEST = saved

    def test_code_and_manifest_differ(self):
        self.assertIn("the package reports", " ".join(self.check(code_version="0.0.0+unknown")))

    def test_not_semver(self):
        self.edit_json(".claude-plugin/plugin.json", lambda d: d.update(version="1.2"))
        self.assertIn("not a semantic version", " ".join(self.check()))

    def test_marketplace_declares_version(self):
        self.edit_json(".claude-plugin/marketplace.json", lambda d: d["plugins"][0].update(version="0.1.0"))
        self.assertIn("declares a version", " ".join(self.check()))

    def test_marketplace_absolute_source(self):
        self.edit_json(".claude-plugin/marketplace.json", lambda d: d["plugins"][0].update(source="/abs/path"))
        self.assertIn("is not relative", " ".join(self.check()))

    def test_changelog_missing_version_section(self):
        self.edit_json(".claude-plugin/plugin.json", lambda d: d.update(version="99.0.0"))
        self.assertIn("no '## [99.0.0]' section", " ".join(self.check(code_version="99.0.0")))

    def test_changelog_missing_unreleased(self):
        p = self.tmp / "CHANGELOG.md"
        p.write_text(p.read_text().replace("## [Unreleased]", "## Unreleased"))
        self.assertIn("[Unreleased]", " ".join(self.check()))

    def test_no_changelog(self):
        (self.tmp / "CHANGELOG.md").unlink()
        self.assertIn("no CHANGELOG.md", " ".join(self.check()))

    @unittest.skipUnless(shutil.which("git"), "needs git")
    def test_tags_and_release_branch(self):
        self.git("init", "-q", "-b", "main")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "v1")
        v1 = self.version
        self.git("tag", f"foreman--v{v1}")
        self.git("branch", "release")
        self.assertEqual(self.check(), [])

        # a commit without a version bump: release stays; moving release there is caught
        (self.tmp / "x").write_text("x")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "work")
        self.assertEqual(self.check(), [])
        self.git("branch", "-f", "release", "HEAD")
        self.assertIn(f"not on tag foreman--v{v1}", " ".join(self.check()))
        self.git("branch", "-f", "release", f"foreman--v{v1}")

        # a version tag on HEAD that names another version
        self.git("tag", "foreman--v77.0.0")
        self.assertIn("HEAD is tagged foreman--v77.0.0", " ".join(self.check()))
        self.git("tag", "-d", "foreman--v77.0.0")

        # the version commit, tagged, and release moved to it
        self.bump("88.0.0")
        self.git("commit", "-q", "-am", "bump")
        self.git("tag", "foreman--v88.0.0")
        self.git("branch", "-f", "release", "foreman--v88.0.0")
        self.assertEqual(self.check(), [])

    def test_not_a_git_root_skips_git_checks(self):
        # an installed copy has no .git: only the file checks apply (the tmp folder is not a repo here)
        self.assertEqual(self.check(), [])


if __name__ == "__main__":
    unittest.main()
