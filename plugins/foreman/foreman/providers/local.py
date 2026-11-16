"""The local provider: the branch is merged into the main checkout of the repo with git. Nothing is pushed."""

from . import Change, ChangeState, Conflict, Provider, ProviderError
from .repo import RepoError


class Local(Provider):
    name = "local"
    merges = ("squash", "merge")
    has_ci = False  # no CI: merged here, in the repo

    def merge(self, change: Change, strategy: str) -> str:
        main, branch = self.p.get("project", "main"), change.branch
        try:
            if self.repo.current_branch() != main:
                raise ProviderError(f"the main checkout {self.p.repo} is not on {main}")
            if self.repo.dirty():
                raise ProviderError(f"the main checkout {self.p.repo} has uncommitted changes")
            out = self.repo.merge(main, branch, f"Merge {change.id}: {change.title}", strategy == "squash")
        except RepoError as e:
            raise ProviderError(str(e)) from e
        if not out.commit:
            why = f"merge failed: {out.why}"
            raise Conflict(why) if out.conflict else ProviderError(why)
        return out.commit

    def state(self, change: Change) -> ChangeState:
        ref = change.merge
        try:
            merged = bool(ref) and self.repo.is_ancestor(ref, self.p.get("project", "main"))
        except RepoError as e:
            raise ProviderError(str(e)) from e
        return ChangeState(merged, merged)
