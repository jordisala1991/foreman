"""The review loop: the pass holds a delivered branch whose review does not let it in and says what to do
(`review_needed`, `review_findings`, the owner's question after round 2), `foreman launch NAME --review REPO:BRANCH`
(a reviewer with no worktree that holds the branch while it lives) and the plan B launch that reuses a branch's
worktree. Reviews are written by hand, as a reviewer agent would write them."""

import json
from datetime import datetime, timedelta

from helpers import git
from test_review import ReviewCase

from foreman import delivery, identity, loop, review, util


def at(minutes_ago=0):
    return (datetime.now() - timedelta(minutes=minutes_ago)).isoformat(timespec="seconds")


class LoopCase(ReviewCase):
    def agent(self, aid, live=False, **fields):
        """An agents.json entry: live (working) or delivered."""
        f = loop.agents_file(self.project)
        d = util.read_json(f, {})
        d[aid] = {"start": at(30), "activity": at(1), "type": "dev", "named_by": "launch", **fields}
        if not live:
            d[aid].setdefault("end", at(1))
            d[aid].setdefault("delivered", at(1))
        f.parent.mkdir(parents=True, exist_ok=True)
        util.write_json(f, d)

    def stop(self, aid):
        f = loop.agents_file(self.project)
        d = util.read_json(f, {})
        d[aid].update(end=at(0), delivered=at(0))
        util.write_json(f, d)

    def ns(self):
        """{kind: [notices]} of the pass's branch notices."""
        live = loop.live_agents(self.project)
        heads = {h["id"]: "" for h in live}
        ids = {h["id"]: identity.of(self.project, h, "") for h in live}
        out = {}
        for n in loop.branch_notices(self.project, live, heads, ids):
            out.setdefault(n["kind"], []).append(n)
        return out

    def ready(self):
        ns = self.ns()
        return ns["ready_to_integrate"][0]["text"] if "ready_to_integrate" in ns else ""

    def text(self, kind):
        ns = self.ns()
        self.assertIn(kind, ns, ns)
        return " | ".join(n["text"] for n in ns[kind])


class ReviewLoop(LoopCase):
    """§2.1 of the plan, the loop's part, and §2.2 as the pass says it."""

    def test_each_trigger_is_held_with_the_launch_command(self):
        cases = {
            "paths": ({"lib/guard.py": "x = 2\n"}, None, "delicate paths: lib/guard.py"),
            "size": ({"lib/new.py": "n\n" * 30}, None, "30 lines of code, over 20"),
            "partial": ({"lib/core.py": "a = 2\n"}, "partial", "delivered partial"),
        }
        for kind, (files, outcome, why) in cases.items():
            with self.subTest(kind=kind):
                self.drop_branch()
                self.commit(files)
                if outcome:
                    r = self.cli("progress", "--done", "half", "--outcome", outcome, env={"FOREMAN_WHO": "w1"})
                    self.assertEqual(r.returncode, 0, r.stderr)
                ns = self.ns()
                self.assertNotIn("ready_to_integrate", ns)
                n = ns["review_needed"][0]
                self.assertTrue(n["actionable"])
                self.assertIn(f"app:ab1 (AB-1 T1; {why}; round 1)", n["text"])
                self.assertIn("`foreman launch w1-rv1 --review app:ab1 --issue AB-1 --sub T1`", n["text"])
                self.assertEqual(n["key"], "review_needed:app:ab1@needed")
                self.assertNotIn("review_findings", ns)

    def test_small_branch_is_ready_as_today(self):
        self.commit({"lib/core.py": "a = 2\n"})
        ns = self.ns()
        self.assertIn("app:ab1", ns["ready_to_integrate"][0]["text"])
        self.assertFalse({"review_needed", "review_findings"} & set(ns))
        self.write_toml(TOML_OFF)  # off: nothing is held, whatever the branch touches
        self.commit({"lib/guard.py": "x = 2\n"})
        self.assertIn("app:ab1", self.ready())

    def test_findings_go_back_then_round_two(self):
        tip = self.commit({"lib/guard.py": "x = 2\n"})
        self.agent("dev1", name="w1", repo="app", branch="ab1", worktree=str(self.wt), production=True)
        self.agent("rev1", name="w1-rv1", repo="app", branch="ab1", review=True, type="reviewer", start=at(5))
        self.write_review(tip, high=1)
        t = self.text("review_findings")
        self.assertIn("app:ab1: H 1, M 0 open (reviews/2026-11-05-review-AB-1-T1.md, round 1 of 2)", t)
        self.assertIn("send it back to w1 (id dev1) with SendMessage", t)
        self.assertIn("«Review, round 1» section", t)
        self.assertIn("`foreman launch w1-fix --repo app --branch ab1 --issue AB-1 --sub T1`", t)
        self.assertEqual(self.ready(), "")
        # resumed, the agent is live: the branch is its own again, no notice
        self.agent("dev1", live=True, name="w1", repo="app", branch="ab1", production=True)
        self.assertFalse({"review_needed", "review_findings", "ready_to_integrate"} & set(self.ns()))
        new = self.commit({"lib/guard.py": "x = 3\n"}, "fix")
        self.stop("dev1")
        t = self.text("review_needed")
        self.assertIn("app:ab1 (AB-1 T1; round 2: the fixes are in, reviews/2026-11-05-review-AB-1-T1.md left H 1", t)
        self.assertIn("resume the reviewer w1-rv1 (id rev1) with SendMessage, or launch one: ", t)
        self.assertIn("`foreman launch w1-rv2 --review app:ab1 --issue AB-1 --sub T1`", t)
        self.write_review(new, rnd=2)
        self.assertIn("app:ab1", self.ready())
        self.assertFalse({"review_needed", "review_findings"} & set(self.ns()))

    def test_round_two_open_asks_the_owner_and_waits(self):
        tip = self.commit({"lib/guard.py": "x = 2\n"})
        self.write_review(tip, high=1)
        new = self.commit({"lib/guard.py": "x = 3\n"}, "fix")
        rv = self.write_review(new, rnd=2, high=1)
        page = str(rv.relative_to(self.home))
        n = self.ns()["review_findings"][0]
        self.assertTrue(n["actionable"])
        self.assertIn("app:ab1: round 2 of 2 left H 1, M 0 open", n["text"])
        self.assertIn("no third round: ask Owner with a question written for them (docs/owner.md)", n["text"])
        self.assertIn(f"`foreman backlog decision add --issue AB-1 --blocks T1 --page {page} --title …`", n["text"])
        r = self.cli(
            "backlog", "decision", "add", "--issue", "AB-1", "--blocks", "T1", "--page", page, "--title",
            "A serious problem is still open in the feature", "--text", "Two reviews could not close it.",
            "--option", "Let it in with the problem noted", "--effect", "it is in the product today",
            "--option", "Drop it", "--effect", "nothing changes", "--as-is",
        )  # fmt: skip
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        did = Backlog_ids(self)[0]
        ns = self.ns()
        self.assertEqual([x["actionable"] for x in ns["review_findings"]], [False])
        self.assertIn(f"app:ab1 waits for Owner's answer to {did}", ns["review_findings"][0]["text"])
        self.assertNotIn("ready_to_integrate", ns)
        r = self.cli("backlog", "decision", "resolve", did, "--option", "1", "--by", "Owner, 10:00 (chat)")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        n = self.ns()["review_findings"][0]
        self.assertTrue(n["actionable"])
        self.assertIn(f"app:ab1: Owner answered {did}: «Let it in with the problem noted»", n["text"])
        self.assertIn('`foreman integrate app ab1 "AB-1 T1" --approved "Owner, ', n["text"])
        self.assertIn("then a review with «Round: 3»", n["text"])
        # another agent fixed it and a round 3 found nothing open: it goes in
        last = self.commit({"lib/guard.py": "x = 4\n"}, "fix again")
        self.write_review(last, rnd=3, name="2026-11-05-review-AB-1-T1-r3.md")
        self.assertIn("app:ab1", self.ready())

    def test_partial_then_ok_keeps_the_open_review(self):
        tip = self.commit({"lib/core.py": "a = 2\n"})
        r = self.cli("progress", "--done", "half", "--outcome", "partial", env={"FOREMAN_WHO": "w1"})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.write_review(tip, medium=1)
        self.assertIn("review_findings", self.ns())
        self.commit({"lib/core.py": "a = 3\n"}, "the rest")
        r = self.cli("progress", "--done", "all of it", "--outcome", "ok", env={"FOREMAN_WHO": "w1"})
        self.assertEqual(r.returncode, 0, r.stderr)
        st = self.state("ok")
        self.assertEqual((st.status, st.reasons[0].kind), (review.FIXED, "review"))
        self.assertIn("round 2: the fixes are in", self.text("review_needed"))

    def test_paused_only_informs(self):
        self.commit({"lib/guard.py": "x = 2\n"})
        self.project.state.mkdir(parents=True, exist_ok=True)
        (self.project.state / "pause").write_text("10:00\n")  # as the panel writes it
        self.assertEqual([n["actionable"] for n in self.ns()["review_needed"]], [False])


TOML_OFF = ReviewCase.toml.replace('review = "auto"', 'review = "off"')


def Backlog_ids(case):
    from foreman.backlog import Backlog

    return [d["id"] for d in Backlog(case.project).load()["decisions"]]


class ReviewerLaunch(LoopCase):
    """§2.3 of the plan: `launch --review` and the plan B launch."""

    def launch(self, *args):
        return self.cli("launch", *args)

    def test_review_launch_makes_no_worktree_and_holds_the_branch(self):
        tip = self.commit({"lib/guard.py": "x = 2\n"})
        before = sorted(p.name for p in (self.tmp / "wt").iterdir())
        backlog_before = (self.home / "backlog.json").read_text() if (self.home / "backlog.json").exists() else None
        r = self.launch("w1-rv1", "--review", "app:ab1", "--issue", "AB-1", "--sub", "T1", "--json")
        self.assertEqual(r.returncode, 0, r.stderr)
        out = json.loads(r.stdout)
        self.assertEqual(
            (out["repo"], out["branch"], out["worktree"], out["type"], out["review"], out["production"]),
            ("app", "ab1", None, "reviewer", True, None),
        )
        self.assertTrue(out["line"].endswith("w1-rv1 · AB-1 T1 · branch ab1 · review"), out["line"])
        self.assertEqual(sorted(p.name for p in (self.tmp / "wt").iterdir()), before)
        if backlog_before is not None:
            self.assertEqual((self.home / "backlog.json").read_text(), backlog_before)
        # the agent takes the launch: its entry says it reviews the branch, not production
        agents = {}
        self.assertTrue(identity.link(self.project, agents, "rev1", out["token"], agent_type="foreman:reviewer"))
        self.assertEqual(
            (agents["rev1"]["review"], agents["rev1"]["branch"], agents["rev1"]["production"]), (True, "ab1", False)
        )
        f = loop.agents_file(self.project)
        f.parent.mkdir(parents=True, exist_ok=True)
        agents["rev1"]["activity"] = at(0)
        util.write_json(f, agents)
        ns = self.ns()
        self.assertFalse({"ready_to_integrate", "review_needed", "review_findings"} & set(ns))  # held
        r = self.integrate("--check")
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("== review: in review by w1-rv1", r.stdout)
        self.assertIn("wait for its review", r.stderr)
        # its delivery is not the branch's: the outcome stays the dev's
        self.cli("progress", "--done", "half", "--outcome", "partial", env={"FOREMAN_WHO": "w1"})
        d = util.read_json(f, {})
        d["rev1"].update(end=at(0), delivered=at(0), outcome="ok", report="reviews/x.md", kind="measure")
        util.write_json(f, d)
        self.assertEqual(delivery.outcome_of(self.project, branch="ab1", name="w1"), "partial")
        self.assertIsNone(delivery.report_of(self.project, branch="ab1"))
        self.assertIsNone(delivery.kind_of(self.project, branch="ab1"))
        # done without a review: the branch is released, and still needs one
        self.assertIn("app:ab1 (AB-1 T1; delicate paths: lib/guard.py; delivered partial; round 1)",
                      self.text("review_needed"))  # fmt: skip
        self.write_review(tip)
        self.assertIn("app:ab1", self.ready())

    def test_review_launch_refusals(self):
        self.commit({"lib/guard.py": "x = 2\n"})
        cases = {
            ("--review", "app:ab1", "--repo", "app", "--branch", "ab1"): "goes without --repo",
            ("--review", "app"): "REPO:BRANCH",
            ("--review", "nope:ab1"): "no repo 'nope'",
            ("--review", "app:nope"): "app has no local branch 'nope'",
            ("--review", "app:ab1", "--production"): "never a production agent",
        }
        for args, why in cases.items():
            with self.subTest(args=args):
                r = self.launch("rv9", *args)
                self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
                self.assertIn(why, r.stderr)
        launches = identity.launches_file(self.project)
        self.assertFalse(launches.exists() and "rv9" in launches.read_text())

    def test_plan_b_reuses_the_worktree_of_a_branch_with_no_live_agent(self):
        self.commit({"lib/guard.py": "x = 2\n"})
        self.agent("dev1", live=True, name="w1", repo="app", branch="ab1", worktree=str(self.wt), production=True)
        r = self.launch("w2", "--repo", "app", "--branch", "ab1", "--issue", "AB-1", "--sub", "T1")
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn(f"already checked out in {self.wt} and the live agent w1 has it; nothing done", r.stderr)
        self.stop("dev1")
        head = git(self.wt, "rev-parse", "HEAD")
        r = self.launch("w2", "--repo", "app", "--branch", "ab1", "--issue", "AB-1", "--sub", "T1", "--base", "x")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn(f"{self.wt} (ab1 reusing its worktree at {head[:7]}", r.stdout)
        self.assertIn("BASE x ignored", r.stderr)
        self.assertIn(f"branch ab1 · worktree {self.wt} · production", r.stdout)
        self.assertEqual(git(self.wt, "rev-parse", "HEAD"), head)
        from foreman.backlog import Backlog

        sub = Backlog(self.project).load()
        sub = next(s for _, i, _ in Backlog(self.project).iter_issues(sub) for s in i["subs"] if s["id"] == "T1")
        self.assertEqual((sub["agent"], sub["branch"]), ("w2", "ab1"))

    def test_plan_b_never_takes_a_worktree_foreman_did_not_make(self):
        app = self.tmp / "app"
        other = self.tmp / "elsewhere"
        git(app, "worktree", "add", "-q", "-b", "mine", str(other))
        r = self.launch("w3", "--repo", "app", "--branch", "mine")
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("is not under foreman's worktrees folder", r.stderr)
        r = self.cli("worktree", "app", "w4", "ab1")
        self.assertEqual(r.returncode, 0, r.stderr)
        r = self.cli("worktree", "app", "w5", "ab1")  # `foreman worktree` never reuses
        self.assertEqual(r.returncode, 1)
        self.assertIn("already checked out in", r.stderr)

    def test_reviewer_of(self):
        agents = [
            {"id": "a", "review": True, "branch": "ab1", "repo": "app", "start": "2026-11-05T10:00:00"},
            {"id": "b", "review": True, "branch": "ab1", "repo": "app", "start": "2026-11-05T11:00:00"},
            {"id": "c", "review": True, "branch": "ab1", "repo": "api", "start": "2026-11-05T12:00:00"},
            {"id": "d", "branch": "ab1", "repo": "app", "start": "2026-11-05T13:00:00"},
        ]
        self.assertEqual(review.reviewer_of(agents, "app", "ab1")["id"], "b")
        self.assertIsNone(review.reviewer_of(agents, "app", "other"))
