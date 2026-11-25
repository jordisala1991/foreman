"""`foreman backlog setup` (providers/github/board.py) on a fake Board: the 9 Status options, the 10 other fields, the label,
no workflows; a second run changes nothing."""

import pytest
from fakegh import FakeGh, board_env
from helpers import Env, fm

from foreman import proc
from foreman.core.tasks import board


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> FakeGh:
    f = FakeGh()
    monkeypatch.setattr(proc, "run", f)
    return f


def test_setup_on_a_new_board(fake: FakeGh):
    e = board_env()
    out = fm(e.home, "backlog", "setup")
    assert (
        [o["name"] for o in fake.fields["Status"]["options"]]
        == list(board.STATUS)
        == ["Triage", "Backlog", "Refining", "Ready", "In progress", "In review", "Done", "Canceled", "Duplicate"]
    )
    for f in board.FIELDS[1:]:
        have = fake.fields[f.name]
        assert (have["dataType"], tuple(o["name"] for o in have.get("options", []))) == (f.kind, f.options)
    assert len(board.FIELDS[1:]) == 10
    assert fake.labels == {"needs-owner"}
    assert fake.workflows == []
    assert out.splitlines() == [
        "Status: options replaced",
        *(f"field {f.name} created" for f in board.FIELDS[1:]),
        "label needs-owner created",
        "workflow Item closed deleted",
        "workflow Pull request merged deleted",
        "workflow Auto-close issue deleted",
    ]


def test_second_setup_changes_nothing(fake: FakeGh):
    e = board_env()
    fm(e.home, "backlog", "setup")
    mutations, calls = fake.mutations, len(fake.calls)
    assert fm(e.home, "backlog", "setup") == "the Board and the label are already set up\n"
    assert fake.mutations == mutations
    assert len(fake.calls) == calls + 1  # the one read, no gh label


def test_wrong_field_type_is_refused(fake: FakeGh):
    e = board_env()
    fake.fields["Model"] = {"id": "F-m", "name": "Model", "dataType": "TEXT"}
    with pytest.raises(SystemExit, match="foreman: field Model is TEXT, fm needs SINGLE_SELECT"):
        fm(e.home, "backlog", "setup")
    assert fake.mutations == 0 and fake.labels == set()


def test_without_a_board_setup_says_so(fake: FakeGh):
    with pytest.raises(SystemExit, match=r"foreman: no \[backlog\] table in foreman.toml"):
        fm(Env().home, "backlog", "setup")
    assert fake.calls == []


def test_a_board_without_status_is_refused(fake: FakeGh):
    e = board_env()
    del fake.fields["Status"]
    with pytest.raises(SystemExit, match="foreman: Board 1 of o has no Status field"):
        fm(e.home, "backlog", "setup")


def test_more_fields_than_one_page_is_refused(fake: FakeGh):
    e = board_env()
    fake.field_total = 51
    with pytest.raises(SystemExit, match="foreman: Board 1 of o has 51 fields: fm reads at most 50"):
        fm(e.home, "backlog", "setup")
    fake.field_total, fake.workflow_total = None, 51
    with pytest.raises(SystemExit, match="foreman: Board 1 of o has 51 workflows: fm reads at most 50"):
        fm(e.home, "backlog", "setup")
    assert fake.mutations == 0
