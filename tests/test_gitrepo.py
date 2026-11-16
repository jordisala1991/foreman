"""The repo adapter (fm/providers/gitrepo.py) on a real git repo: every expected value is what git means by it."""

import tempfile
from pathlib import Path

import pytest
from helpers import git

from fm.providers.gitrepo import GitRepo
from fm.providers.repo import FileDiff, MergeOutcome, RepoError


def commit(repo: Path, msg: str, **files: str | None) -> str:
    for name, text in files.items():
        f = repo / name.replace("_", ".")
        if text is None:
            f.unlink()
        else:
            f.write_text(text)
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", msg)
    return git(repo, "rev-parse", "HEAD").strip()


@pytest.fixture
def repo() -> Path:
    """main: a.txt = 1 2 3, b.txt = x. Branch b appends 4 to a.txt and deletes b.txt; branch c rewrites line 1 as c,
    and main then rewrites it as m (c conflicts with main, b does not)."""
    r = Path(tempfile.mkdtemp(prefix="fmgit-")).resolve() / "repo"
    r.mkdir()
    git(r, "init", "-q", "-b", "main")
    git(r, "config", "user.email", "t@t")
    git(r, "config", "user.name", "t")
    commit(r, "init", a_txt="1\n2\n3\n", b_txt="x\n")
    git(r, "checkout", "-qb", "b")
    commit(r, "b", a_txt="1\n2\n3\n4\n", b_txt=None)
    git(r, "checkout", "-q", "main")
    git(r, "checkout", "-qb", "c")
    commit(r, "c", a_txt="c\n2\n3\n")
    git(r, "checkout", "-q", "main")
    commit(r, "m", a_txt="m\n2\n3\n")
    return r


def test_heads(repo: Path):
    r = GitRepo(repo)
    assert r.head("main") == git(repo, "rev-parse", "main").strip()
    assert r.find("main") == r.head("main")
    assert r.find("nope") == ""
    with pytest.raises(RepoError) as e:
        r.head("nope")
    assert e.value.reason


def test_what_a_branch_changes(repo: Path):
    r = GitRepo(repo)
    assert r.commits_ahead("main", "b") == 1
    assert sorted(r.diff_stat("main", "b"), key=lambda f: f.path) == [FileDiff("a.txt", 1, 0), FileDiff("b.txt", 0, 1)]
    assert r.deleted_files("main", "b") == ["b.txt"]


def test_conflicts(repo: Path):
    r = GitRepo(repo)
    assert r.conflicts("main", "c") is True
    assert r.conflicts("main", "b") is False


@pytest.mark.parametrize("squash", [False, True])
def test_merge_of_a_clean_branch(repo: Path, squash: bool):
    r = GitRepo(repo)
    out = r.merge("main", "b", "Merge FN-1: b", squash)
    assert out.commit == r.head("main") and not out.conflict
    assert git(repo, "log", "-1", "--format=%s").strip() == "Merge FN-1: b"
    assert r.is_ancestor(r.head("b"), "main") is (not squash)


@pytest.mark.parametrize("squash", [False, True])
def test_merge_of_a_conflicting_branch_leaves_main_as_it_was(repo: Path, squash: bool):
    r = GitRepo(repo)
    before = r.head("main")
    out = r.merge("main", "c", "Merge FN-2: c", squash)
    assert out.commit == "" and out.conflict and out.why
    assert out == MergeOutcome("", True, out.why)
    assert r.dirty() is False
    assert r.head("main") == before


def test_worktrees(repo: Path):
    r = GitRepo(repo)
    wt = repo.parent / "t"
    r.add_worktree(wt, "t", "main")
    assert r.find("t") == r.head("main")
    assert wt.resolve() in [w.resolve() for w in r.worktrees()]
    r.remove_worktree(wt)
    assert wt.resolve() not in [w.resolve() for w in r.worktrees()]
    r.delete_branch("t")
    assert r.find("t") == ""
    r.add_worktree(wt, "b", "main")  # an existing branch is checked out as it is
    assert git(wt, "branch", "--show-current").strip() == "b"


def test_detached_worktree_and_merge_in_it(repo: Path):
    r = GitRepo(repo)
    with pytest.raises(RepoError) as e:
        r.add_detached(repo.parent / "d0", "nope")
    assert e.value.reason
    wt = repo.parent / "d"
    r.add_detached(wt, r.head("main"))
    assert git(wt, "rev-parse", "HEAD").strip() == r.head("main")
    with pytest.raises(RepoError):
        r.merge_in_worktree(wt, r.head("c"))


def test_merges_of(repo: Path):
    r = GitRepo(repo)
    before = r.head("main")
    git(repo, "merge", "--no-ff", "-qm", "Merge FN-1: x", "b")
    assert r.merges_of("FN-1", before, "main") == [r.head("main")]
    assert r.merges_of("FN-2", before, "main") == []


def test_the_main_checkout(repo: Path):
    r = GitRepo(repo)
    assert r.is_ancestor("b~1", "main") is True
    assert r.is_ancestor("b", "main") is False
    assert r.current_branch() == "main"
    assert r.dirty() is False
    (repo / "a.txt").write_text("edited\n")
    assert r.dirty() is True
    assert r.origin_url() == ""
    git(repo, "remote", "add", "origin", "https://github.com/o/r")
    assert r.origin_url() == "https://github.com/o/r"
