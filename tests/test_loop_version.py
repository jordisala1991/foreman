import os
from unittest import mock

import pytest
from fakerepo import FakeRepo
from helpers import Env, install_fm, version_folder

from foreman import launcher, util
from foreman.loop import tick


class TestVersion:
    """A loop started by the launcher (FOREMAN_PINNED) exits as soon as another foreman@foreman version is installed."""

    @pytest.fixture(autouse=True)
    def set_up(self, request: pytest.FixtureRequest, fake_git: FakeRepo):
        self.e = Env(repo=fake_git)
        self.p = self.e.p
        cache = self.e.tmp / "cache/foreman/foreman"
        self.a, self.b = version_folder(cache, "aaa"), version_folder(cache, "bbb")
        env = mock.patch.dict(os.environ, {"FOREMAN_PINNED": self.a["installPath"]})
        env.start()
        request.addfinalizer(env.stop)

    def test_a_new_install_stops_the_loop_before_its_tick(self, request: pytest.FixtureRequest):
        install_fm(request, self.b)
        with pytest.raises(SystemExit) as e:
            tick.forever([str(self.p.home)], once=True)
        assert str(e.value) == "foreman: foreman@foreman bbb is installed now; this runs aaa"
        assert not (self.p.state / "loop.json").exists()

    def test_the_same_install_ticks_and_the_heartbeat_names_the_version(self, request: pytest.FixtureRequest):
        install_fm(request, self.a)
        tick.forever([str(self.p.home)], once=True)
        hb = util.read_json(self.p.state / "loop.json", {})
        assert hb["version"] == launcher.ROOT.name
