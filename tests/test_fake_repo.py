"""The fake repo (tests/fakerepo.py) gives git's answers: test_gitrepo.py's scenario and expected values, on the fake."""

import tempfile
from pathlib import Path

import pytest
from fakerepo import FakeRepo

from foreman.providers.repo import FileDiff, MergeOutcome, RepoError


@pytest.fixture
def repo() -> FakeRepo:
    """As test_gitrepo.py's: branch b appends to a.txt and deletes b.txt; branch c rewrites a line that main then rewrites
    too (c conflicts with main, b does not). The fake's conflict is per path, git's per hunk: that line is in c.txt here."""
    r = FakeRepo()
    r.commit("main", FileDiff("a.txt", 3, 0), FileDiff("b.txt", 1, 0), FileDiff("c.txt", 1, 0), message="init")
    r.commit("b", FileDiff("a.txt", 1, 0), FileDiff("b.txt", 0, 1), deleted=("b.txt",), message="b")
    r.commit("c", FileDiff("c.txt", 1, 1), message="c")
    r.commit("main", FileDiff("c.txt", 1, 1), message="m")
    return r


def test_heads(repo: FakeRepo):
    assert repo.find("main") == repo.head("main")
    assert repo.find("nope") == ""
    with pytest.raises(RepoError) as e:
        repo.head("nope")
    assert e.value.reason


def test_what_a_branch_changes(repo: FakeRepo):
    assert repo.commits_ahead("main", "b") == 1
    assert sorted(repo.diff_stat("main", "b"), key=lambda f: f.path) == [FileDiff("a.txt", 1, 0), FileDiff("b.txt", 0, 1)]
    assert repo.deleted_files("main", "b") == ["b.txt"]


def test_diff_stat_adds_up_each_commit(repo: FakeRepo):
    repo.commit("d", FileDiff("d.txt", 3, 0))
    repo.commit("d", FileDiff("d.txt", 0, 3))
    assert repo.diff_stat("main", "d") == [FileDiff("d.txt", 3, 3)]  # coarser than git, which gives nothing (docstring)


def test_conflicts(repo: FakeRepo):
    assert repo.conflicts("main", "c") is True
    assert repo.conflicts("main", "b") is False
    assert repo.conflicts(repo.head("main"), repo.head("c")) is True  # commit ids too, as mergecheck.py passes them


def test_a_git_failure_is_an_error_not_a_conflict(repo: FakeRepo):
    for base, branch in (("main", "nope"), ("nope", "c")):
        with pytest.raises(RepoError) as e:
            repo.conflicts(base, branch)
        assert e.value.reason


def test_merge_of_an_unknown_branch_is_an_error(repo: FakeRepo):
    with pytest.raises(RepoError):
        repo.merge("main", "nope", "m", False)


@pytest.mark.parametrize("squash", [False, True])
def test_merge_of_a_clean_branch(repo: FakeRepo, squash: bool):
    out = repo.merge("main", "b", "Merge FN-1: b", squash)
    assert out.commit == repo.head("main") and not out.conflict
    assert repo.messages()[-1] == "Merge FN-1: b"
    assert repo.is_ancestor(repo.head("b"), "main") is (not squash)


def test_a_branch_merged_already_merges_as_up_to_date(repo: FakeRepo):
    first = repo.merge("main", "b", "Merge FN-1: b", False)
    assert repo.merge("main", "b", "Merge FN-1: b", False) == first
    assert repo.head("main") == first.commit


@pytest.mark.parametrize("squash", [False, True])
def test_merge_of_a_conflicting_branch_leaves_main_as_it_was(repo: FakeRepo, squash: bool):
    before = repo.head("main")
    out = repo.merge("main", "c", "Merge FN-2: c", squash)
    assert out.commit == "" and out.conflict and out.why
    assert out == MergeOutcome("", True, out.why)
    assert repo.dirty() is False
    assert repo.head("main") == before


def test_worktrees(repo: FakeRepo):
    wt = Path(tempfile.mkdtemp(prefix="fmfake-")) / "t"
    repo.add_worktree(wt, "t", "main")
    assert repo.find("t") == repo.head("main")
    assert wt.is_dir() and wt in repo.worktrees()
    repo.remove_worktree(wt)
    assert not wt.exists() and wt not in repo.worktrees()
    repo.delete_branch("t")
    assert repo.find("t") == ""
    b = repo.head("b")
    repo.add_worktree(wt, "b", "main")  # an existing branch is checked out as it is
    assert repo.head("b") == b
    with pytest.raises(RepoError):
        repo.add_worktree(wt, "b", "main")  # git refuses a path that is already a worktree
    assert repo.worktrees() == [wt]  # only the worktrees added: not the main checkout (docstring)
    other = wt.parent / "not-a-worktree"
    other.mkdir()
    repo.remove_worktree(other)
    assert other.is_dir()  # git removes only a worktree it knows


def test_detached_worktree_and_merge_in_it(repo: FakeRepo):
    tmp = Path(tempfile.mkdtemp(prefix="fmfake-"))
    with pytest.raises(RepoError) as e:
        repo.add_detached(tmp / "d0", "nope")
    assert e.value.reason
    wt = tmp / "d"
    repo.add_detached(wt, repo.head("main"))
    assert repo.trees[wt] == repo.head("main")
    with pytest.raises(RepoError):
        repo.merge_in_worktree(wt, repo.head("c"))


def test_merges_of(repo: FakeRepo):
    before = repo.head("main")
    repo.merge("main", "b", "Merge FN-1: x", squash=False)
    assert repo.merges_of("FN-1", before, "main") == [repo.head("main")]
    assert repo.merges_of("FN-2", before, "main") == []


def test_the_main_checkout(repo: FakeRepo):
    assert repo.is_ancestor(repo.parents[repo.head("b")][0], "main") is True  # b~1
    assert repo.is_ancestor("b", "main") is False
    assert repo.is_ancestor("main", "nope") is False  # git exits 128: not an ancestor, no error
    assert repo.current_branch() == "main"
    assert repo.dirty() is False
    assert repo.origin_url() == ""


def test_commit_files(repo: FakeRepo, tmp_path: Path):
    x, y = tmp_path / "x.png", tmp_path / "y.png"
    x.write_bytes(b"x")
    y.write_bytes(b"y")
    first = repo.commit_files("", "FN-1", {"x.png": x}, "m")
    assert sorted(repo.files[first]) == ["FN-1/x.png"]
    assert repo.parents[first] == ()  # an orphan root
    other = repo.commit_files(first, "FN-2", {"x.png": x}, "m")
    second = repo.commit_files(other, "FN-1", {"y.png": y}, "m")
    assert sorted(repo.files[second]) == ["FN-1/x.png", "FN-1/y.png", "FN-2/x.png"]
    assert repo.commit_files(second, "FN-1", {"x.png": x, "y.png": y}, "m") == ""


def test_fast_forward(repo: FakeRepo):
    repo.commit("ahead", FileDiff("d.txt", 1, 0), message="more")
    repo.fast_forward("ahead")
    assert repo.head("main") == repo.head("ahead")
    before = repo.head("main")
    with pytest.raises(RepoError):
        repo.fast_forward("c")
    assert repo.head("main") == before
