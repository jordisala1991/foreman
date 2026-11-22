"""The planning, responsible, hunter and reviewer briefs carry the rule on cases nobody asked for, once each."""

import pytest
from helpers import Env

from foreman.providers import registry
from foreman.services.agents import brief

KINDS = ("planning", "responsible", "hunter", "reviewer")


class TestBriefUnasked:
    @pytest.fixture(autouse=True)
    def set_up(self, env: Env) -> None:
        self.e = env
        self.tid = registry.backlog(env.p).add("t", criteria="a")

    @pytest.mark.parametrize("kind", KINDS)
    def test_every_brief_of_the_four_carries_the_rule_once(self, kind: str) -> None:
        text = brief.brief(self.e.p, self.e.task(self.tid), kind)
        assert text.count(brief.UNASKED) == 1
