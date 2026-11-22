"""The Vocabulary paragraph of the briefs names GLOSSARY.md only when the repo has one."""

import pytest
from helpers import Env

from foreman.providers import registry
from foreman.services.agents import brief

KINDS = ("responsible", "planning", "reviewer", "hunter", "parent", "chain")
SENTENCE = "Use the terms of `GLOSSARY.md` at the repo root"


class TestBriefVocabulary:
    @pytest.fixture(autouse=True)
    def set_up(self, env: Env) -> None:
        self.e = env
        self.tid = registry.backlog(env.p).add("t", criteria="a")

    def render(self, kind: str) -> str:
        return brief.brief(self.e.p, self.e.task(self.tid), kind)

    @pytest.mark.parametrize("kind", KINDS)
    def test_no_brief_names_glossary_without_one(self, kind: str) -> None:
        text = self.render(kind)
        assert "GLOSSARY" not in text
        assert "$vocabulary" not in text

    @pytest.mark.parametrize("kind", KINDS)
    def test_every_brief_names_glossary_with_one(self, kind: str) -> None:
        (self.e.repo / "GLOSSARY.md").write_text("# Glossary\n")
        assert SENTENCE in self.render(kind)
