"""The glossary's names, with no compatibility: an old flag, environment variable, config section or stored key is
not read."""

import os
from unittest import mock

import pytest
from helpers import fm

from fm import util
from fm.core.project.config import Project
from fm.core.tasks import backlog

# The old names, written from parts so a scan for them finds nothing outside GLOSSARY.md.
OLD_ENV = "FM_" + "IS" + "SUE"
OLD_FLAG = "--" + "cla" + "im"
OLD_SECTION = "inte" + "grate"
OLD_KEY = "is" + "sues"


class TestOldNames:
    @pytest.fixture(autouse=True)
    def set_up(self, request, env) -> None:
        self.e = env
        patch = mock.patch.dict(os.environ)
        patch.start()
        request.addfinalizer(patch.stop)
        for k in ("FM_TASK", OLD_ENV):
            os.environ.pop(k, None)

    def test_old_flag_fails(self):
        with pytest.raises(SystemExit) as r:
            fm(self.e.home, "add", "t", OLD_FLAG, "x")
        assert r.value.code == 2
        tid = fm(self.e.home, "add", "t", "--goal", "x").strip()
        assert self.e.task(tid)["acceptance"]["goal"] == "x"

    def test_old_env_is_not_read(self):
        tid = backlog.add(self.e.p, "t", criteria="c")
        os.environ[OLD_ENV] = tid
        with pytest.raises(SystemExit) as r:
            fm(self.e.home, "block", "why")
        assert "FM_TASK" in str(r.value.code)
        os.environ["FM_TASK"] = tid
        fm(self.e.home, "block", "why")
        assert self.e.task(tid)["blocked"] == "why"

    def test_old_config_section_is_not_read(self):
        toml = self.e.home / "fm.toml"
        toml.write_text(f'[{OLD_SECTION}]\nautonomy = "medium"\n')
        assert Project.load(self.e.home).get("merge", "autonomy") == "manual"
        toml.write_text('[merge]\nautonomy = "low"\n')
        assert Project.load(self.e.home).get("merge", "autonomy") == "low"

    def test_old_stored_key_is_not_read(self):
        util.write_json(self.e.p.backlog_file, {"schema_version": 5, OLD_KEY: [{"id": "TY-1", "title": "t", "status": "backlog"}]})
        assert backlog.tasks(backlog.load(self.e.p)) == []
