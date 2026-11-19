"""The repo adapter (foreman/providers/gitrepo.py) on a real git repo: every expected value is what git means by it."""

import tempfile
from pathlib import Path
from typing import Any

import pytest
from helpers import git

from foreman import proc
from foreman.providers.gitrepo import GitRepo
from foreman.providers.repo import FileDiff, MergeOutcome, RepoError


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


def test_a_git_failure_is_an_error_not_a_conflict(repo: Path):
    r = GitRepo(repo)
    for base, branch in (("main", "nope"), ("nope", "c")):
        with pytest.raises(RepoError) as e:
            r.conflicts(base, branch)
        assert e.value.reason


def test_merge_of_an_unknown_branch_is_an_error(repo: Path):
    with pytest.raises(RepoError):
        GitRepo(repo).merge("main", "nope", "m", False)


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


def test_a_git_that_times_out_mid_merge_leaves_main_as_it_was(repo: Path, monkeypatch: pytest.MonkeyPatch):
    real = proc.run

    def run(*a: Any, **kw: Any) -> proc.Result:
        if "commit" in a[0]:
            raise proc.ProcError("git commit: timed out after 60s", timed_out=True)
        return real(*a, **kw)

    monkeypatch.setattr(proc, "run", run)
    r = GitRepo(repo)
    before = r.head("main")
    with pytest.raises(RepoError):
        r.merge("main", "b", "Merge FN-1: b", True)
    assert r.dirty() is False
    assert r.head("main") == before


def test_a_checkout_git_cannot_restore_after_a_failed_merge_is_an_error_that_says_so(repo: Path, monkeypatch: pytest.MonkeyPatch):
    real = proc.run

    def run(*a: Any, **kw: Any) -> proc.Result:
        if "commit" in a[0]:
            (repo / ".git" / "index.lock").write_text("")  # what a git killed mid-commit leaves
            raise proc.ProcError("git commit: timed out after 60s", timed_out=True)
        return real(*a, **kw)

    monkeypatch.setattr(proc, "run", run)
    with pytest.raises(RepoError, match="git reset"):
        GitRepo(repo).merge("main", "b", "Merge FN-1: b", True)


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
    assert r.remote_url("origin") == ""
    git(repo, "remote", "add", "up", "https://github.com/o/r")
    assert r.remote_url("up") == "https://github.com/o/r"
    assert r.remote_url("origin") == ""


def test_commit_files(repo: Path, tmp_path: Path):
    r = GitRepo(repo)
    x, y = tmp_path / "x.png", tmp_path / "y.png"
    x.write_bytes(b"x")
    y.write_bytes(b"y")

    def names(commit: str) -> list[str]:
        return git(repo, "ls-tree", "-r", "--name-only", commit).split()

    first = r.commit_files("", "FN-1", {"x.png": x}, "m")
    assert names(first) == ["FN-1/x.png"]
    assert git(repo, "rev-list", "--parents", "-n1", first).split() == [first]  # an orphan root
    other = r.commit_files(first, "FN-2", {"x.png": x}, "m")
    second = r.commit_files(other, "FN-1", {"y.png": y}, "m")
    assert names(second) == ["FN-1/x.png", "FN-1/y.png", "FN-2/x.png"]
    assert r.commit_files(second, "FN-1", {"x.png": x, "y.png": y}, "m") == ""


def test_fast_forward(repo: Path):
    r = GitRepo(repo)
    git(repo, "checkout", "-q", "b")
    git(repo, "checkout", "-q", "-b", "ahead", "main")
    commit(repo, "more", d_txt="d\n")
    git(repo, "checkout", "-q", "main")
    r.fast_forward("ahead")
    assert r.head("main") == r.head("ahead")
    before = r.head("main")
    with pytest.raises(RepoError):
        r.fast_forward("c")
    assert r.head("main") == before


def test_is_checkout(repo: Path):
    assert GitRepo(repo).is_checkout()
    plain, sub = repo.parent / "plain", repo / "sub"
    plain.mkdir()
    sub.mkdir()
    assert not GitRepo(plain).is_checkout()
    assert not GitRepo(repo.parent / "missing").is_checkout()
    assert not GitRepo(sub).is_checkout()


def test_untracked_lists_untracked_and_ignored_paths(repo: Path):
    (repo / "d").mkdir()
    (repo / "ig").mkdir()
    for name in ("u.txt", "d/x.txt", "ig/y.txt", "ign.txt"):
        (repo / name).write_text("x")
    (repo / ".git/info/exclude").write_text("ig/\nign.txt\n")
    assert sorted(GitRepo(repo).untracked()) == ["d/x.txt", "ig/", "ign.txt", "u.txt"]
