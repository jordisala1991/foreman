"""A review runs only when its result goes somewhere (FN-133): one test per row of DESIGN.md's table "What the loop does
with a task in review" that this rule adds. Conflicts with main are resolved only when someone wants the merge."""

import os
import subprocess
import unittest
from pathlib import Path

from helpers import Env, fm, git

from fm import backlog, delivery, loop, util


def commit(wt: str, name: str, text: str, msg: str = "change") -> None:
    with open(os.path.join(wt, name), "a") as f:
        f.write(text)
    git(Path(wt), "add", "-A")
    git(Path(wt), "commit", "-qm", msg)


def deliver(e: Env, open_findings: int = 0) -> str:
    tid = backlog.add(e.p, "x", status="ready", criteria="y")
    loop.tick(e.p)
    commit(e.task(tid)["worktree"], "app.py", "# change\n")
    backlog.set_status(e.p, tid, "in review", report={"summary": "s", "confused": ""})
    e.review(tid, open_findings)
    return tid


def main_conflicts(e: Env) -> None:
    (e.repo / "app.py").write_text("changed on main\n")
    git(e.repo, "commit", "-qam", "main moves")


def main_moves(e: Env) -> None:
    (e.repo / "other.py").write_text("x = 1\n")
    git(e.repo, "add", "other.py")
    git(e.repo, "commit", "-qm", "main moves, no conflict")


def take_note(e: Env, tid: str) -> None:
    """The agent's turn ends and the next tick delivers the note the loop left for it."""
    e.set_state(tid.lower(), "done")
    loop.tick(e.p)


def auto_tree(repo: Path, tip: str) -> str:
    """git's automatic merge of tip and main (exit 1 when it conflicts): the tree on its first line. Commit ids, as the
    conflict markers in that tree carry the names given."""
    main = git(repo, "rev-parse", "main").strip()
    r = subprocess.run(["git", "-C", str(repo), "merge-tree", "--write-tree", "--no-messages", tip, main], capture_output=True, text=True, check=False)
    return r.stdout.splitlines()[0]


def head(e: Env, tid: str) -> str:
    return git(e.repo, "rev-parse", e.task(tid)["branch"]).strip()


def events(e: Env, tid: str) -> list[str]:
    return [x["text"] for x in util.read_jsonl(e.p.state / "events.jsonl") if x.get("task") == tid]


def launched(e: Env, tid: str) -> list[str]:
    return [t for t in events(e, tid) if t.startswith("launched ") and "rule: " in t]


class MergeOf(unittest.TestCase):
    def test_merge_of(self):
        e = Env()
        r = e.repo
        git(r, "checkout", "-qb", "b")
        commit(str(r), "app.py", "# on b\n")
        tip = git(r, "rev-parse", "HEAD").strip()
        git(r, "checkout", "-q", "main")
        main_moves(e)
        git(r, "checkout", "-q", "b")
        git(r, "merge", "-q", "--no-edit", "main")
        self.assertEqual(delivery.merge_of(e.p, tip, git(r, "rev-parse", "HEAD").strip()), ("clean", ""))
        tip = git(r, "rev-parse", "HEAD").strip()
        git(r, "checkout", "-q", "main")
        main_conflicts(e)
        git(r, "checkout", "-q", "b")
        git(r, "merge", "-q", "--no-edit", "-X", "ours", "main")
        auto = auto_tree(r, tip)
        self.assertEqual(delivery.merge_of(e.p, tip, git(r, "rev-parse", "HEAD").strip()), ("resolved", auto))
        tip = git(r, "rev-parse", "HEAD").strip()
        commit(str(r), "app.py", "# plain\n")
        self.assertEqual(delivery.merge_of(e.p, tip, git(r, "rev-parse", "HEAD").strip()), ("", ""))
        tip = git(r, "rev-parse", "HEAD").strip()
        git(r, "checkout", "-qb", "c", "main")
        commit(str(r), "c.py", "c = 1\n")
        git(r, "checkout", "-q", "b")
        git(r, "merge", "-q", "--no-edit", "c")
        self.assertEqual(delivery.merge_of(e.p, tip, git(r, "rev-parse", "HEAD").strip()), ("", ""))


class Rules(unittest.TestCase):
    def test_a_waiting_task_that_conflicts_stays_and_says_so(self):
        e = Env(test="true")
        tid = deliver(e)
        loop.tick(e.p)
        main_conflicts(e)
        loop.tick(e.p)
        i = e.task(tid)
        self.assertEqual(i["status"], "in review")
        self.assertNotIn("conflicts", i)
        self.assertTrue(i["check"]["conflict"])
        self.assertTrue(i["check"]["reasons"][0].startswith("conflicts with main"), i["check"]["reasons"])

    def accept_into_conflict(self, e: Env) -> tuple[str, str]:
        """Delivered and checked; main conflicts; the owner accepts; the merge sends it back. Returns (task, old tip)."""
        tid = deliver(e)
        loop.tick(e.p)
        main_conflicts(e)
        loop.tick(e.p)
        fm(e.home, "accept", tid)
        old = head(e, tid)
        loop.tick(e.p)
        i = e.task(tid)
        self.assertEqual((i["status"], i["conflicts"], i["resolving"]), ("in progress", 1, "owner"))
        take_note(e, tid)
        self.assertIn("conflicts", e.calls()[-1]["argv"][-1])
        return tid, old

    def resolve(self, e: Env, tid: str) -> None:
        git(e.repo.parent / e.task(tid)["worktree"], "merge", "-q", "--no-edit", "-X", "ours", "main")
        backlog.set_status(e.p, tid, "in review")

    def test_accept_then_conflict_resolves_and_merges_without_a_new_accept(self):
        e = Env(test="true")
        tid, old = self.accept_into_conflict(e)
        self.resolve(e, tid)
        before = len(e.calls())
        e.hunt_then_review(tid)
        self.assertEqual(len(e.calls()), before + 2)  # one hunter, one reviewer
        auto = auto_tree(e.repo, old)
        self.assertIn(f"git diff {auto} {head(e, tid)}", e.calls()[-1]["argv"][-1])
        self.assertTrue(launched(e, tid)[-1].endswith("rule: conflict resolution"), launched(e, tid))
        e.verdict(tid, 0)
        loop.tick(e.p)
        i = e.task(tid)
        self.assertEqual((i["status"], i["acceptance"]["verdict"]), ("done", "accepted by owner"))
        self.assertIn("Merge TY-1", git(e.repo, "log", "--oneline", "-1"))

    def test_a_resolution_left_open_waits_for_the_owner(self):
        e = Env(test="true")
        tid, _ = self.accept_into_conflict(e)
        self.resolve(e, tid)
        e.hunt_then_review(tid)
        e.verdict(tid, 1)
        loop.tick(e.p)
        self.assertEqual(e.task(tid)["status"], "in progress")  # round 1 of the resolution: back to its agent
        take_note(e, tid)
        commit(e.task(tid)["worktree"], "app.py", "# fixed\n")
        backlog.set_status(e.p, tid, "in review")
        e.review(tid, 1)
        loop.tick(e.p)
        i = e.task(tid)
        self.assertEqual(i["status"], "in review")
        self.assertFalse(i.get("accepted"))
        self.assertFalse(i.get("resolving"))
        self.assertIn("its accept is not carried over", " ".join(events(e, tid)))

    def test_a_clean_merge_of_main_launches_nothing(self):
        e = Env(test="true")
        tid = deliver(e)
        loop.tick(e.p)
        self.assertTrue(launched(e, tid)[0].endswith("rule: rounds 1-2"), launched(e, tid))
        before = len(e.calls())
        main_moves(e)
        git(e.repo.parent / e.task(tid)["worktree"], "merge", "-q", "--no-edit", "main")
        loop.tick(e.p)
        loop.tick(e.p)
        i = e.task(tid)
        self.assertEqual(len(e.calls()), before)
        self.assertEqual((i["review"]["holds"], i["check"]["head"]), (head(e, tid), head(e, tid)))
        self.assertIn("no review runs, its verdict holds", " ".join(events(e, tid)))

    def test_after_round_two_later_tips_launch_no_review(self):
        e = Env(test="true")
        tid = deliver(e, open_findings=1)
        loop.tick(e.p)
        take_note(e, tid)
        commit(e.task(tid)["worktree"], "app.py", "# fix\n")
        backlog.set_status(e.p, tid, "in review")
        e.review(tid, 1)
        loop.tick(e.p)
        self.assertEqual((e.task(tid)["status"], e.task(tid)["review_for"]), ("in review", "none"))
        before = len(e.calls())
        commit(e.task(tid)["worktree"], "app.py", "# again\n")
        loop.tick(e.p)
        loop.tick(e.p)
        i = e.task(tid)
        self.assertEqual(len(e.calls()), before)
        self.assertEqual(i["review"]["holds"], head(e, tid))
        self.assertTrue(i["check"]["review"].startswith("open after round 2"), i["check"])

    def test_a_new_tip_after_a_clean_verdict_gets_a_review(self):
        e = Env(test="true")
        tid = deliver(e)
        loop.tick(e.p)
        self.assertEqual(e.task(tid).get("review_for", ""), "")
        commit(e.task(tid)["worktree"], "app.py", "# more\n")
        loop.tick(e.p)
        self.assertEqual(e.task(tid)["hunter"]["tip"], head(e, tid))
        self.assertTrue(launched(e, tid)[-1].endswith("rule: rounds 1-2"), launched(e, tid))

    def test_after_two_clean_rounds_a_new_tip_launches_nothing(self):
        e = Env(test="true")
        tid = deliver(e)
        loop.tick(e.p)
        commit(e.task(tid)["worktree"], "app.py", "# more\n")
        e.review(tid, 0)
        self.assertIn("Round: 2", e.calls()[-1]["argv"][-1])
        loop.tick(e.p)
        self.assertEqual(e.task(tid)["review_for"], "none")
        before = len(e.calls())
        commit(e.task(tid)["worktree"], "app.py", "# again\n")
        loop.tick(e.p)
        loop.tick(e.p)
        self.assertEqual(len(e.calls()), before)
        self.assertEqual(e.task(tid)["review"]["holds"], head(e, tid))

    def test_reject_gets_a_review_of_the_fix_and_open_findings_go_back(self):
        e = Env(test="true")
        tid = deliver(e)
        loop.tick(e.p)
        old = head(e, tid)
        fm(e.home, "reject", tid, "name it better")
        take_note(e, tid)
        commit(e.task(tid)["worktree"], "app.py", "# better name\n")
        backlog.set_status(e.p, tid, "in review")
        e.review(tid, 1)
        brief = e.calls()[-1]["argv"][-1]
        self.assertIn(f"git diff {old} {head(e, tid)}", brief)
        self.assertIn("Round: 1", brief)
        self.assertTrue(launched(e, tid)[-1].endswith("rule: owner reject"), launched(e, tid))
        loop.tick(e.p)
        self.assertEqual(e.task(tid)["status"], "in progress")
        self.assertIn("leaves 1 open", " ".join(events(e, tid)))


if __name__ == "__main__":
    unittest.main()
