"""A home's verify.md reaches the responsible, hunter and reviewer briefs; the three always name the session (FN-426, FN-427)."""

import pytest
from helpers import Env

from foreman.providers import registry
from foreman.services.agents import brief

KINDS = ("responsible", "hunter", "reviewer")


class TestBriefVerify:
    @pytest.fixture(autouse=True)
    def set_up(self, env: Env) -> None:
        self.e = env
        self.tid = registry.backlog(env.p).add("t", criteria="a")
        self.recipe = str(env.home / "verify.md")
        self.session = str(env.home / "reports" / self.tid / "session.md")

    def render(self, kind: str) -> str:
        return brief.brief(self.e.p, self.e.task(self.tid), kind)

    def write_recipe(self) -> None:
        (self.e.home / "verify.md").write_text("Build it, start it, drive it.\n", encoding="utf-8")

    @pytest.mark.parametrize("kind", KINDS)
    def test_the_recipe_path_reaches_the_three_briefs(self, kind: str) -> None:
        self.write_recipe()
        text = self.render(kind)
        assert self.recipe in text
        assert self.session in text

    def test_no_recipe_no_path_in_hunter_and_reviewer(self) -> None:
        for kind in ("hunter", "reviewer"):
            assert self.recipe not in self.render(kind)

    def test_the_responsible_brief_carries_the_session_path_without_a_recipe(self) -> None:
        text = self.render("responsible")
        assert self.session in text
        assert self.recipe not in text

    @pytest.mark.parametrize("kind", ("hunter", "reviewer"))
    def test_the_hunter_and_reviewer_carry_the_session_path_without_a_recipe(self, kind: str) -> None:
        text = self.render(kind)
        assert self.session in text
        assert "$session" not in text

    @pytest.mark.parametrize("kind", KINDS)
    def test_no_variable_is_left_unreplaced(self, kind: str) -> None:
        self.write_recipe()
        text = self.render(kind)
        assert "$verify" not in text
        assert "$session" not in text
