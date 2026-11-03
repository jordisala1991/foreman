"""The delivery check at a production agent's stop (delivery.py, the agents hook) and the outcome of
`foreman progress --done --outcome`. Every event goes through `foreman hook agents`, as Claude Code sends it."""

import json
import re

from helpers import ProjectCase, git, make_repo

from foreman import delivery, loop

TOML = """
[project]
name = "fake"
owner = "Owner"
language = "en"

[paths]
worktrees = "{tmp}/wt"

[agents]
production_slots = 1
ready_target = 1

[loop]
prompt = "Fake orchestrator loop"
disk_low_gb = 0

[repos.app]
path = "{tmp}/app"
remote = "none"
{extra}
"""

LINE = re.compile(r"^\[foreman launch [a-z2-7]{10}\] .*$", re.M)


class DeliveryCase(ProjectCase):
    """A product whose home lives outside its repo (`app`): reports are written in the home."""

    toml = TOML.replace("{extra}", "")

    def setUp(self):
        super().setUp()
        make_repo(self.tmp / "app")
        self.n = 0

    # ---------- helpers ----------

    def launch(self, name="zed7", *args, aid="ag1", kind="dev", branch="ab1/x"):
        """`foreman launch` with a worktree, then the launcher's PostToolUse that ties it to agent `aid`."""
        extra = list(args) or ["--repo", "app", "--branch", branch]
        r = self.cli("launch", name, "--issue", "AB-1", "--sub", "T1", "--no-backlog", *extra)
        self.assertEqual(r.returncode, 0, r.stderr)
        line = LINE.search(r.stdout).group(0)
        ev = {
            "hook_event_name": "PostToolUse",
            "tool_name": "Agent",
            "session_id": "S1",
            "tool_input": {"prompt": line + "\nWork.", "subagent_type": kind},
            "tool_response": {"status": "async_launched", "agentId": aid},
        }
        self.assertEqual(self.hook("agents", ev).returncode, 0)
        return self.tmp / "wt" / f"app-{name}"

    def bash(self, cmd, aid="ag1", kind="dev"):
        r = self.hook(
            "agents",
            {
                "hook_event_name": "PostToolUse",
                "agent_id": aid,
                "agent_type": kind,
                "tool_name": "Bash",
                "tool_input": {"command": cmd},
            },
        )
        self.assertEqual((r.returncode, r.stdout), (0, ""), r.stderr)

    def done(self, outcome=None, aid="ag1", name="zed7", report=None, cwd=None, kind=None):
        """The agent runs `foreman progress --done` (the CLI, then the hook sees its command line)."""
        args = ["progress", "--done", "the result"] + (["--outcome", outcome] if outcome else [])
        args += ["--report", report] if report else []
        args += ["--kind", kind] if kind else []
        r = self.cli("--project", "fake", *args, cwd=cwd, env={"FOREMAN_WHO": name})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.bash("foreman --project fake " + " ".join(f"'{a}'" if " " in a else a for a in args), aid)

    def stop(self, aid="ag1", kind="dev", **kw):
        """The agent's SubagentStop: the block's reason, or None when it may stop."""
        r = self.hook(
            "agents",
            {
                "hook_event_name": "SubagentStop",
                "agent_id": aid,
                "agent_type": kind,
                "last_assistant_message": "Done.",
                **kw,
            },
        )
        self.assertEqual(r.returncode, 0, r.stderr)
        if not r.stdout.strip():
            return None
        out = json.loads(r.stdout)
        self.assertEqual(out["decision"], "block")
        return out["reason"]

    def commit(self, wt, name="work.txt"):
        (wt / name).write_text("work\n")
        git(wt, "add", "-A")
        git(wt, "commit", "-q", "-m", "work")

    def report(self, name="zed7"):
        d = self.home / "reviews"
        d.mkdir(exist_ok=True)
        (d / f"2026-10-31-{name}.md").write_text("# Report\n")

    def entry(self, aid="ag1"):
        return loop.agents(self.project)[aid]

    def kinds(self):
        return {n["kind"]: n for n in loop.notices_for_pass(self.project, None, mark=False)}


class BlockTest(DeliveryCase):
    def test_blocked_without_commits_report_or_done(self):
        wt = self.launch()
        reason = self.stop()
        self.assertIn("try 1 of 2", reason)
        self.assertIn("no commits on ab1/x beyond main", reason)
        self.assertIn(f"git -C {wt} add -A", reason)
        self.assertIn(f"no report of yours under {self.home / 'reviews'}", reason)
        self.assertIn("no delivery recorded", reason)
        self.assertIn("foreman --project fake progress --done", reason)
        e = self.entry()
        self.assertEqual((e["delivery_blocks"], "end" in e, loop.agent_state(e)), (1, False, "working"))

    def test_each_missing_piece_blocks_alone(self):
        wt = self.launch()
        self.commit(wt)
        self.report()
        reason = self.stop()  # commits and report, no done
        self.assertIn("no delivery recorded", reason)
        self.assertNotIn("no commits on", reason)
        self.assertNotIn("- no report", reason)

    def test_no_commits_blocks(self):
        self.launch()
        self.report()
        self.done()
        reason = self.stop()
        self.assertIn("no commits", reason)
        self.assertNotIn("no delivery recorded", reason)
        # the refused stop takes the delivery back: it has to run progress --done again
        e = self.entry()
        self.assertNotIn("delivered", e)
        self.assertEqual(loop.agent_state(e), "working")

    def test_no_report_blocks(self):
        wt = self.launch()
        self.commit(wt)
        self.done()
        reason = self.stop()
        self.assertIn("no report", reason)
        self.assertNotIn("no commits on", reason)
        self.assertNotIn("no delivery recorded", reason)

    def test_the_sub_issue_report_counts(self):
        """The report the sub-issue names counts, whatever its name or folder (the rule the pass uses too)."""
        from foreman.backlog import Backlog

        wt = self.launch()
        self.commit(wt)
        self.done()
        (self.home / "lab").mkdir()
        (self.home / "lab/measures.md").write_text("# Measures\n")
        self.assertIn("no report", self.stop())
        b = Backlog(self.project)
        b.init()
        with b.mutate() as d:
            b.add_project(d, "E1", "One", "intro")
            b.add_issue(d, "E1", "AB-1", "an issue", status="in progress")
            b.set_sub(d, "AB-1", "T1", status="in progress", branch="ab1/x", report="lab/measures.md")
        self.assertEqual(delivery.branch_report(self.project, "ab1/x").path, "lab/measures.md")
        with b.mutate() as d:
            b.set_sub(d, "AB-1", "T1", report="lab/missing.md")
        r = delivery.branch_report(self.project, "ab1/x")
        self.assertEqual((r.path, r.stale), (None, "lab/missing.md"))
        with b.mutate() as d:
            b.set_sub(d, "AB-1", "T1", report="lab/measures.md")
        self.done()
        self.assertIsNone(self.stop())

    def test_complete_delivery_stops(self):
        wt = self.launch()
        self.commit(wt)
        self.report()
        self.done()
        self.assertIsNone(self.stop())
        e = self.entry()
        self.assertEqual((loop.agent_state(e), e["outcome"], e.get("delivery_blocks")), ("delivered", "ok", None))

    def test_fixed_after_a_block(self):
        wt = self.launch()
        self.assertIsNotNone(self.stop())
        self.commit(wt)
        self.report()
        self.done("partial")
        self.assertIsNone(self.stop())
        e = self.entry()
        self.assertEqual((loop.agent_state(e), e["outcome"], e["delivery_blocks"]), ("delivered", "partial", 1))
        self.assertNotIn("delivery_incomplete", self.kinds())

    def test_failed_outcome_needs_no_commits_nor_report(self):
        self.launch()
        self.done("failed")
        self.assertIsNone(self.stop())
        self.assertEqual(self.entry()["outcome"], "failed")
        line = [json.loads(x) for x in (self.project.state / "progress.jsonl").read_text().splitlines()][-1]
        self.assertEqual((line["done"], line["outcome"]), (True, "failed"))

    def test_two_blocks_then_let_go_with_a_notice(self):
        self.launch()
        self.assertIn("try 1 of 2", self.stop())
        self.assertIn("try 2 of 2", self.stop())
        self.assertIsNone(self.stop())
        e = self.entry()
        self.assertEqual(e["delivery_blocks"], 2)
        self.assertEqual(e["delivery_incomplete"]["missing"], ["commits", "report", "progress --done"])
        n = self.kinds()["delivery_incomplete"]
        self.assertTrue(n["actionable"])
        self.assertIn("zed7 (ab1/x): missing commits, report, progress --done", n["text"])
        self.assertEqual(n["key"], "delivery_incomplete:zed7")
        self.assertIsNone(self.stop())  # never trapped: further stops pass too
        # resumed: the notice goes until it stops again
        self.bash("ls")
        self.assertNotIn("delivery_incomplete", self.entry())
        self.assertNotIn("delivery_incomplete", self.kinds())

    def test_background_tasks_are_not_a_delivery(self):
        """An agent that stops to wait for a task of its own is not checked (it is `waiting`)."""
        self.launch()
        self.assertIsNone(self.stop(background_tasks=[{"id": "b1", "command": "selftest"}]))
        self.assertEqual(loop.agent_state(self.entry()), "waiting")

    def test_never_blocked_outside_production(self):
        # a planner with a launch record and no worktree
        self.launch("pl1", "--type", "planner", aid="ag2", kind="foreman:planner")
        self.assertIsNone(self.stop("ag2", kind="foreman:planner"))
        # a worktree launched as not production
        self.launch("mx1", "--repo", "app", "--branch", "mx/x", "--no-production", aid="ag3", kind="foreman:mechanic")
        self.assertIsNone(self.stop("ag3", kind="foreman:mechanic"))
        # a dev agent with no launch record
        self.assertIsNone(self.stop("ag4"))
        self.assertNotIn("delivery_blocks", self.entry("ag4"))

    def test_an_error_does_not_block(self):
        """The worktree is gone: commits and report cannot be told; only the missing --done is said."""
        wt = self.launch()
        git(self.tmp / "app", "worktree", "remove", "--force", str(wt))
        reason = self.stop()
        self.assertNotIn("no commits on", reason)
        self.assertNotIn("- no report", reason)
        self.assertIn("no delivery recorded", reason)

    def test_outcome_in_cli_loop_and_integrate(self):
        r = self.cli("progress", "--outcome", "partial", "text")
        self.assertEqual(r.returncode, 2)
        self.assertIn("--outcome goes with --done", r.stderr)
        r = self.cli("progress", "--done", "x", "--outcome", "maybe")
        self.assertEqual(r.returncode, 2)
        r = self.cli("progress", "--done", "old form", env={"FOREMAN_WHO": "zed7"})
        self.assertEqual(r.returncode, 0)
        line = json.loads((self.project.state / "progress.jsonl").read_text().splitlines()[-1])
        self.assertEqual(line["outcome"], "ok")
        self.assertEqual(delivery.outcome_in(["--done", "x"]), "ok")
        self.assertEqual(delivery.outcome_in(["--done", "--outcome=failed", "x"]), "failed")
        self.assertIsNone(delivery.outcome_in(["--done", "--outcome", "maybe"]))
        self.assertEqual(delivery.report_in(["--done", "x", "--report", "reviews/a.md"]), "reviews/a.md")
        self.assertEqual(delivery.report_in(["--done", "--report=reviews/b.md", "x"]), "reviews/b.md")
        self.assertIsNone(delivery.report_in(["--done", "x"]))
        self.assertIsNone(delivery.report_in(["--done", "x", "--report"]))
        wt = self.launch()
        self.commit(wt)
        self.report()
        self.done("partial")
        self.assertIsNone(self.stop())
        self.assertEqual(delivery.outcome_of(self.project, branch="ab1/x"), "partial")
        self.assertEqual(delivery.outcome_of(self.project, name="zed7"), "partial")
        self.assertIsNone(delivery.outcome_of(self.project, branch="other"))
        self.assertIsNone(delivery.report_of(self.project, branch="ab1/x"))

    def test_kind_measure_in_cli_and_entry(self):
        """`progress --done --kind measure`: in the progress line and, by the hook, in the agent's entry; it goes
        when the agent is resumed; `--kind` without --done is refused."""
        r = self.cli("progress", "--kind", "measure", "text")
        self.assertEqual(r.returncode, 2)
        self.assertIn("--kind goes with --done", r.stderr)
        self.assertEqual(self.cli("progress", "--done", "x", "--kind", "study").returncode, 2)
        self.assertEqual(delivery.kind_in(["--done", "x", "--kind", "measure"]), "measure")
        self.assertEqual(delivery.kind_in(["--done", "--kind=measure", "x"]), "measure")
        self.assertIsNone(delivery.kind_in(["--done", "x", "--kind", "code"]))
        self.assertIsNone(delivery.kind_in(["--done", "x", "--kind"]))
        self.assertIsNone(delivery.kind_in(["--done", "x"]))
        self.launch()
        self.report()
        self.done(kind="measure")
        line = json.loads((self.project.state / "progress.jsonl").read_text().splitlines()[-1])
        self.assertEqual((line["done"], line["kind"]), (True, "measure"))
        self.assertEqual(self.entry()["kind"], "measure")
        self.stop()
        self.bash("ls")  # resumed after its stop: working again, the delivery's kind goes with it
        self.assertNotIn("kind", self.entry())
        self.done(kind="code")
        line = json.loads((self.project.state / "progress.jsonl").read_text().splitlines()[-1])
        self.assertNotIn("kind", line)
        self.assertNotIn("kind", self.entry())

    def test_a_measurement_needs_no_commits_only_its_report(self):
        """`--kind measure` at delivery: no commits needed; the report still is. The refusal of a plain delivery
        without commits says how to deliver a measurement."""
        self.launch()
        self.done()
        reason = self.stop()
        self.assertIn("no commits", reason)
        self.assertIn("add `--kind measure`: it needs no commits", reason)
        self.done(kind="measure")
        reason = self.stop()  # no report yet
        self.assertIn("no report", reason)
        self.assertNotIn("no commits on", reason)
        self.assertNotIn("--kind measure", reason)
        self.report()
        self.done(kind="measure")
        self.assertIsNone(self.stop())
        e = self.entry()
        self.assertEqual((loop.agent_state(e), e["outcome"], e["kind"]), ("delivered", "ok", "measure"))
        self.assertNotIn("delivery_incomplete", e)  # let go complete, not after its two tries
        self.assertEqual(delivery.kind_of(self.project, branch="ab1/x"), "measure")
        self.assertEqual(delivery.kind_of(self.project, name="zed7"), "measure")
        self.assertIsNone(delivery.kind_of(self.project, branch="other"))

    def test_a_measure_sub_issue_needs_no_commits(self):
        """The sub-issue planned as a measurement (`kind = "measure"`): the agent delivers without `--kind` and
        without commits; a code sub-issue still needs them."""
        from foreman.backlog import Backlog

        b = Backlog(self.project)
        b.init()
        with b.mutate() as d:
            b.add_project(d, "E1", "One", "intro")
            b.add_issue(d, "E1", "AB-1", "an issue", status="in progress")
            b.set_sub(d, "AB-1", "T1", status="▶", agent="zed7", branch="ab1/x")
        self.launch()
        self.report()
        self.done()
        self.assertIn("no commits", self.stop())
        with b.mutate() as d:
            b.set_sub(d, "AB-1", "T1", kind="measure")
        self.done()
        self.assertIsNone(self.stop())

    def test_branch_kind(self):
        """The one rule of what a branch brings (plan cases b-e, and a branch no sub-issue names)."""
        from foreman.backlog import Backlog

        b = Backlog(self.project)
        b.init()
        with b.mutate() as d:
            b.add_project(d, "E1", "One", "intro")
            b.add_issue(d, "E1", "AB-1", "an issue", status="in progress")
            b.set_sub(d, "AB-1", "T1", status="▶", branch="code/x")  # a: code
            b.set_sub(d, "AB-1", "T2", status="▶", branch="meas/x", kind="measure")  # b: planned measure
            b.set_sub(d, "AB-1", "T3", status="▶", branch="said/x", agent="zed7")  # c: said at delivery
            b.set_sub(d, "AB-1", "T4", status="done", branch="shut/x")  # d: closed without a commit
            b.set_sub(d, "AB-1", "T5", status="canceled", branch="shut/x")
            b.set_sub(d, "AB-1", "T6", status="▶", branch="mix/x")  # e: code and measure open
            b.set_sub(d, "AB-1", "T7", status="▶", branch="mix/x", kind="measure")
            b.set_sub(d, "AB-1", "T8", status="done", branch="done/x", commit="abc123")  # integrated
            b.set_sub(d, "AB-1", "T9", status="done", branch="half/x", kind="measure")  # one closed, one open
            b.set_sub(d, "AB-1", "T10", status="▶", branch="half/x", kind="measure")
        kind = {br: delivery.branch_kind(self.project, br) for br in ("code/x", "meas/x", "said/x", "shut/x")}
        self.assertEqual(kind, {"code/x": "code", "meas/x": "measure", "said/x": "code", "shut/x": "closed"})
        self.assertEqual(delivery.branch_kind(self.project, "mix/x"), "code")
        self.assertEqual(delivery.branch_kind(self.project, "done/x"), "code")
        self.assertEqual(delivery.branch_kind(self.project, "half/x"), "measure")
        self.assertEqual(delivery.branch_kind(self.project, "nobody/x"), "code")
        # c: the agent of T3 delivers saying it only measured
        self.launch(branch="said/x")
        self.report()
        self.done(kind="measure")
        self.assertIsNone(self.stop())
        self.assertEqual(delivery.branch_kind(self.project, "said/x"), "measure")
        # a branch no sub-issue names: its last delivery decides
        self.launch("mx2", aid="ag2", branch="loose/x")
        self.done(kind="measure", aid="ag2", name="mx2")
        self.assertEqual(delivery.branch_kind(self.project, "loose/x"), "measure")
        # loaded data is used as given
        d = b.load()
        b.issue(d, "AB-1")["subs"][0]["kind"] = "measure"
        self.assertEqual(delivery.branch_kind(self.project, "code/x", d), "measure")

    def test_a_named_report_in_the_home(self):
        """The home outside the repo: the named report must exist in the home; a missing one blocks by name."""
        wt = self.launch()
        self.commit(wt)
        self.report()
        self.bash("foreman --project fake progress --done x --report reviews/2026-11-01-gone.md")
        reason = self.stop()
        self.assertIn(f"the report you named at delivery, reviews/2026-11-01-gone.md, is not in {self.home}", reason)
        self.done(report="reviews/2026-10-31-zed7.md")
        self.assertIsNone(self.stop())
        self.assertEqual(delivery.report_of(self.project, branch="ab1/x"), "reviews/2026-10-31-zed7.md")
        self.assertEqual(delivery.report_of(self.project, name="zed7"), "reviews/2026-10-31-zed7.md")


class HomeInRepoTest(DeliveryCase):
    """The home inside the repo: the report must be in the branch."""

    toml = TOML.replace("{extra}", "").replace('path = "{tmp}/app"', 'path = "{tmp}/home"')

    def setUp(self):
        ProjectCase.setUp(self)
        make_repo(self.home, {"foreman.toml": (self.home / "foreman.toml").read_text()})

    def test_report_in_the_branch(self):
        wt = self.launch()
        self.commit(wt)
        self.done()
        self.report()  # written in the main checkout, not in the branch: missing
        reason = self.stop()
        self.assertIn("no report under reviews/ in your branch: write reviews/YYYY-MM-DD-<task>.md", reason)
        (wt / "reviews").mkdir()
        (wt / "reviews/2026-10-31-zed7.md").write_text("# Report\n")
        git(wt, "add", "-A")
        git(wt, "commit", "-q", "-m", "report")
        self.done()
        self.assertIsNone(self.stop())

    def test_the_report_named_at_delivery_must_be_in_the_branch(self):
        """`progress --done --report`: the CLI takes a report that exists only in the agent's worktree; the delivery
        check then wants that very file in the branch, and no other report found replaces it."""
        wt = self.launch()
        self.commit(wt)
        (wt / "reviews").mkdir()
        (wt / "reviews/2026-10-31-zed7.md").write_text("# Another report\n")
        git(wt, "add", "-A")
        git(wt, "commit", "-q", "-m", "a report")
        (wt / "reviews/2026-11-01-result.md").write_text("# Report\n")  # written, not committed
        r = self.cli("--project", "fake", "progress", "--done", "x", "--report", "reviews/2026-11-01-result.md")
        self.assertEqual(r.returncode, 2)  # from the main checkout, with no agent named: not found
        self.assertIn("reviews/2026-11-01-result.md does not exist", r.stderr)
        self.done(report=str(wt / "reviews/2026-11-01-result.md"), cwd=wt)  # absolute, inside the worktree
        self.assertEqual(self.entry()["report"], "reviews/2026-11-01-result.md")
        reason = self.stop()
        self.assertIn(
            f"the report you named at delivery, reviews/2026-11-01-result.md, is not in ab1/x: commit it in {wt}",
            reason,
        )
        self.assertNotIn("no commits on", reason)
        self.assertNotIn("report", self.entry())
        git(wt, "add", "-A")
        git(wt, "commit", "-q", "-m", "report")
        self.done(report="reviews/2026-11-01-result.md")  # from the main checkout: the agent's worktree is recorded
        self.assertIsNone(self.stop())
        self.assertEqual(self.entry()["report"], "reviews/2026-11-01-result.md")
