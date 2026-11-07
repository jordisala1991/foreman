"""An agent's identity recorded at launch (foreman launch, launches.json) and tied to its agent id by the
agents hook (PostToolUse of the Agent tool, or the token in its transcript); `foreman agents --name` by hand."""

import json
import re

from helpers import ProjectCase, make_repo

from foreman import agents as agents_mod
from foreman import identity, pause, transcripts, util
from foreman.backlog import Backlog

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

[repos.app]
path = "{tmp}/app"
remote = "none"
"""

LINE = re.compile(r"^\[foreman launch ([a-z2-7]{10})\] (.*)$", re.M)


class LaunchTest(ProjectCase):
    toml = TOML

    def setUp(self):
        super().setUp()
        make_repo(self.tmp / "app")
        self.b = Backlog(self.project)
        self.b.init()
        with self.b.mutate() as d:
            self.b.add_project(d, "E1", "One", "intro")
            self.b.add_issue(d, "E1", "AB-1", "feature", status="ready")
            self.b.set_sub(d, "AB-1", "T1", title="the feature")

    def agents(self):
        return util.read_json(self.project.state / "agents.json", {})

    def launch(self, *args, rc=0):
        r = self.cli("launch", *args)
        self.assertEqual(r.returncode, rc, r.stdout + r.stderr)
        m = LINE.search(r.stdout)
        return (m.group(1), m.group(0)) if m else (None, r)

    def post_agent(self, prompt, agent_id="ag1", kind="foreman:dev", session="S1", **extra):
        ev = {
            "hook_event_name": "PostToolUse",
            "tool_name": "Agent",
            "session_id": session,
            "tool_input": {"description": "x", "prompt": prompt, "subagent_type": kind},
            "tool_response": {"status": "async_launched", "agentId": agent_id},
            **extra,
        }
        r = self.hook("agents", ev)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r

    def start(self, aid="ag1", kind="foreman:dev"):
        r = self.hook(
            "agents", {"hook_event_name": "SubagentStart", "agent_id": aid, "agent_type": kind, "session_id": "S1"}
        )
        self.assertEqual(r.returncode, 0, r.stderr)

    # criterion 1
    def test_launch_makes_the_worktree_records_and_prints_the_line(self):
        token, line = self.launch("zed7", "--issue", "AB-1", "--sub", "T1", "--repo", "app", "--branch", "ab1/x")
        wt = self.tmp / "wt" / "app-zed7"
        self.assertTrue((wt / ".git").exists())
        self.assertEqual(line, f"[foreman launch {token}] zed7 · AB-1 T1 · branch ab1/x · worktree {wt} · production")
        rec = self.launches()[token]
        self.assertEqual(
            {k: rec[k] for k in ("name", "issue", "sub", "repo", "branch", "worktree", "production", "agent")},
            {
                "name": "zed7",
                "issue": "AB-1",
                "sub": "T1",
                "repo": "app",
                "branch": "ab1/x",
                "worktree": str(wt),
                "production": True,
                "agent": None,
            },
        )
        # O2: the backlog, as `backlog sub … --status ▶ --agent --branch` and `backlog status … "in progress"` did
        i = self.b.issue(self.b.load(), "AB-1")
        self.assertEqual(i["status"], "in progress")
        self.assertEqual(
            {k: i["subs"][0].get(k) for k in ("status", "agent", "branch")},
            {"status": "in progress", "agent": "zed7", "branch": "ab1/x"},
        )  # ▶

    def test_launch_without_a_repo_and_no_backlog(self):
        token, line = self.launch("plan3", "--issue", "AB-1", "--type", "foreman:planner", "--no-backlog")
        self.assertEqual(line, f"[foreman launch {token}] plan3 · AB-1")
        self.assertIsNone(self.launches()[token]["production"])  # decided by the agent's type when tied
        self.assertEqual(self.b.issue(self.b.load(), "AB-1")["status"], "ready")
        self.assertFalse((self.tmp / "wt").exists())
        # --json
        r = self.cli("launch", "dev4", "--type", "foreman:dev", "--json")
        out = json.loads(r.stdout)
        self.assertTrue(out["production"])
        self.assertTrue(out["line"].startswith(f"[foreman launch {out['token']}] dev4"))

    def test_model_is_recorded_only_when_given_and_read_from_the_transcript(self):
        token0, _ = self.launch("plain1", "--type", "foreman:mechanic")
        self.assertNotIn("model", self.launches()[token0])  # nothing asked, nothing recorded
        token, line = self.launch(
            "mech1",
            "--issue",
            "AB-1",
            "--sub",
            "T1",
            "--repo",
            "app",
            "--branch",
            "ab1/m",
            "--model",
            "sonnet",
            "--type",
            "foreman:mechanic",
        )
        self.assertEqual(self.launches()[token]["model"], "sonnet")
        out = json.loads(self.cli("launch", "mech2", "--model", "haiku", "--json").stdout)
        self.assertEqual(out["model"], "haiku")
        tr = self.tmp / "agm.jsonl"
        rows = [
            {"type": "user", "message": {"content": line}},
            {"type": "assistant", "message": {"model": "claude-sonnet-5-5", "content": "a"}},
            {"type": "assistant", "message": {"model": "<synthetic>", "content": "b"}},
        ]
        tr.write_text("\n".join(json.dumps(r) for r in rows) + '\nnot json "model"\n')
        self.post_agent(line, agent_id="agm", kind="foreman:mechanic")
        self.assertEqual(self.agents()["agm"]["model_asked"], "sonnet")
        self.assertNotIn("model", self.agents()["agm"])  # only at the stop
        stop = {
            "hook_event_name": "SubagentStop",
            "agent_id": "agm",
            "agent_type": "foreman:mechanic",
            "session_id": "S1",
            "agent_transcript_path": str(tr),
            "last_assistant_message": "done",
        }
        self.assertEqual(self.hook("agents", stop).returncode, 0)
        self.assertEqual(self.agents()["agm"]["model"], "claude-sonnet-5-5")
        shown = json.loads(self.cli("agents", "--json").stdout)
        self.assertEqual((shown["agm"]["model"], shown["agm"]["model_asked"]), ("claude-sonnet-5-5", "sonnet"))

    def test_no_model_in_the_transcript_records_none(self):
        tr = self.tmp / "agn.jsonl"
        tr.write_text(json.dumps({"type": "assistant", "message": {"content": "x"}}) + "\n")
        self.start("agn", "foreman:mechanic")
        stop = {
            "hook_event_name": "SubagentStop",
            "agent_id": "agn",
            "agent_type": "foreman:mechanic",
            "session_id": "S1",
            "agent_transcript_path": str(tr),
        }
        self.assertEqual(self.hook("agents", stop).returncode, 0)
        self.assertNotIn("model", self.agents()["agn"])
        self.assertIsNone(identity.transcript_model({"transcript": str(self.tmp / "missing.jsonl")}))

    def test_final_chars_is_recorded_at_the_stop(self):
        """The length of the whole final message: from the event, else from the agent's own (sidechain) transcript."""
        self.start("agf", "foreman:mechanic")
        long = "x" * 3000
        stop = {
            "hook_event_name": "SubagentStop",
            "agent_id": "agf",
            "agent_type": "foreman:mechanic",
            "session_id": "S1",
            "last_assistant_message": long,
        }
        self.assertEqual(self.hook("agents", stop).returncode, 0)
        self.assertEqual(self.agents()["agf"]["final_chars"], 3000)
        self.assertEqual(len(self.agents()["agf"]["final"]), 600)
        tr = self.tmp / "agg.jsonl"
        rows = [
            {"type": "user", "isSidechain": True, "message": {"content": "brief"}},
            {
                "type": "assistant",
                "isSidechain": True,
                "message": {"model": "m", "content": [{"type": "tool_use", "id": "t", "name": "Bash", "input": {}}]},
            },
            {
                "type": "user",
                "isSidechain": True,
                "message": {"content": [{"type": "tool_result", "tool_use_id": "t", "content": "ok"}]},
            },
            {
                "type": "assistant",
                "isSidechain": True,
                "message": {"model": "m", "content": [{"type": "text", "text": "Entregado: " + "y" * 89}]},
            },
        ]
        tr.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
        self.start("agg", "foreman:mechanic")
        stop = {
            "hook_event_name": "SubagentStop",
            "agent_id": "agg",
            "agent_type": "foreman:mechanic",
            "session_id": "S1",
            "agent_transcript_path": str(tr),
        }
        self.assertEqual(self.hook("agents", stop).returncode, 0)
        self.assertEqual(self.agents()["agg"]["final_chars"], 100)
        self.assertIsNone(transcripts.final_chars(str(self.tmp / "missing.jsonl")))

    def test_bad_arguments_change_nothing(self):
        for args in (
            ["x1", "--repo", "app"],
            ["x1", "--sub", "T1"],
            ["x 1"],
            ["x1", "--issue", "AB-9"],
            ["x1", "--issue", "AB-1", "--sub", "T7", "--repo", "app", "--branch", "b"],
        ):
            self.launch(*args, rc=2)
        self.assertEqual(self.launches(), {})
        self.assertFalse((self.tmp / "wt").exists())
        self.launch("x1", "--repo", "nope", "--branch", "b", rc=1)

    def test_worktree_also_records_a_launch(self):
        r = self.cli("worktree", "app", "zed7", "ab1/x")
        self.assertEqual(r.returncode, 0, r.stderr)
        wt = self.tmp / "wt" / "app-zed7"
        self.assertTrue(r.stdout.startswith(f"{wt} (ab1/x from main "))
        token = LINE.search(r.stdout).group(1)
        self.assertEqual(LINE.search(r.stdout).group(2), f"zed7 · branch ab1/x · worktree {wt} · production")
        self.assertIsNone(self.launches()[token]["issue"])
        # a name with a pending launch: the worktree is still made, without a second launch
        self.launch("zed8")
        r = self.cli("worktree", "app", "zed8", "ab1/y")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue((self.tmp / "wt" / "app-zed8").exists())
        self.assertIn("no launch recorded", r.stderr)
        self.assertEqual(len(self.launches()), 2)

    def test_a_name_in_use_is_exit_2(self):
        self.launch("zed7")
        _, r = self.launch("zed7", rc=2)
        self.assertIn("still waiting for its agent", r.stderr)
        self.assertEqual(self.cli("launch", "zed7", "--cancel").returncode, 0)
        self.assertEqual(self.cli("launch", "zed7", "--cancel").returncode, 1)
        token, _ = self.launch("zed7")
        # tied to a live agent: still taken; once delivered, free again
        self.post_agent(f"[foreman launch {token}] zed7\nwork")
        _, r = self.launch("zed7", rc=2)
        self.assertIn("a live agent is called zed7", r.stderr)
        d = self.agents()
        d["ag1"]["delivered"] = "2026-10-31T12:00:00"
        util.write_json(self.project.state / "agents.json", d)
        self.launch("zed7")
        # a live agent with a name but no launch (an old entry) holds its name too
        util.write_json(
            self.project.state / "agents.json",
            {"ag9": {"start": "2026-10-31T12:00:00", "type": "foreman:dev", "name": "c6"}},
        )
        self.launch("c6", rc=2)

    # criterion 2
    def test_tied_by_the_post_tool_use_of_agent_in_any_language(self):
        for order in ("start-first", "start-after", "no-start"):
            with self.subTest(order=order):
                aid = f"ag-{order}"
                name = "n-" + order
                token, line = self.launch(
                    name, "--issue", "AB-1", "--sub", "T1", "--repo", "app", "--branch", f"b/{order}"
                )
                if order == "start-first":
                    self.start(aid)
                brief = f"{line}\nSigue docs/agent.md; trabajas en la rama indicada.\n"  # no phrase, no «Issue:»
                self.post_agent(brief, agent_id=aid)
                if order == "start-after":
                    self.start(aid)
                a = self.agents()[aid]
                wt = str(self.tmp / "wt" / f"app-{name}")
                self.assertEqual(
                    {
                        k: a.get(k)
                        for k in (
                            "name",
                            "issue",
                            "sub",
                            "repo",
                            "branch",
                            "worktree",
                            "production",
                            "launch",
                            "named_by",
                            "type",
                        )
                    },
                    {
                        "name": name,
                        "issue": "AB-1",
                        "sub": "T1",
                        "repo": "app",
                        "branch": f"b/{order}",
                        "worktree": wt,
                        "production": True,
                        "launch": token,
                        "named_by": "launch",
                        "type": "foreman:dev",
                    },
                )
                self.assertEqual(self.launches()[token]["agent"], aid)
                self.assertEqual(agents_mod.agent_state(a), "working")

    def test_production_decided_by_the_type_when_the_launch_did_not_say(self):
        _token, line = self.launch("plan3")
        self.post_agent(line, agent_id="agp", kind="foreman:planner")
        self.assertIs(self.agents()["agp"]["production"], False)
        _token, line = self.launch("dev5")
        self.post_agent(line, agent_id="agd", kind="dev")
        self.assertIs(self.agents()["agd"]["production"], True)

    # criterion 3
    def test_tied_by_the_token_in_the_transcript(self):
        token, line = self.launch("zed7", "--issue", "AB-1", "--repo", "app", "--branch", "ab1/x")
        tr = self.tmp / "ag2.jsonl"
        tr.write_text(json.dumps({"type": "user", "message": {"content": f"{line}\nTe toca la T1."}}) + "\n")
        r = self.hook(
            "agents",
            {
                "hook_event_name": "PostToolUse",
                "agent_id": "ag2",
                "agent_type": "foreman:dev",
                "tool_name": "Bash",
                "tool_input": {"command": "ls"},
                "agent_transcript_path": str(tr),
            },
        )
        self.assertEqual(r.returncode, 0, r.stderr)
        a = self.agents()["ag2"]
        self.assertEqual(
            (a["name"], a["issue"], a["branch"], a["launch"], a["named_by"]), ("zed7", "AB-1", "ab1/x", token, "launch")
        )
        # a brief that quotes a launch already tied to another agent does not take it (and names nobody)
        tr3 = self.tmp / "ag3.jsonl"
        tr3.write_text(f"read the branch of {line}; te llamas lector.\n")
        self.hook(
            "agents",
            {
                "hook_event_name": "PostToolUse",
                "agent_id": "ag3",
                "agent_type": "foreman:planner",
                "tool_name": "Bash",
                "agent_transcript_path": str(tr3),
            },
        )
        a3 = self.agents()["ag3"]
        self.assertEqual((a3.get("name"), a3.get("named_by"), a3.get("launch")), (None, None, None))
        self.assertEqual(self.launches()[token]["agent"], "ag2")

    def test_a_name_phrase_names_nobody(self):
        """«your name is c6» in a brief without the launch line no longer names the agent."""
        tr = self.tmp / "c6.jsonl"
        tr.write_text("Follow docs/agent.md; your name is c6. Issue: AB-1 T1\n")
        self.assertIsNone(identity.of(self.project, {"transcript": str(tr)})["name"])
        self.hook(
            "agents",
            {
                "hook_event_name": "SubagentStop",
                "agent_id": "agc",
                "agent_type": "foreman:dev",
                "agent_transcript_path": str(tr),
            },
        )
        a = self.agents()["agc"]
        self.assertEqual((a.get("name"), a.get("named_by")), (None, None))

    # O3: warn, do not block
    def test_a_production_agent_launched_without_the_line_is_warned(self):
        r = self.post_agent("Follow docs/agent.md; te llamas c6.", agent_id="ag7")
        ctx = json.loads(r.stdout)["hookSpecificOutput"]
        self.assertEqual(ctx["hookEventName"], "PostToolUse")
        self.assertIn("foreman agents --name ag7 NAME", ctx["additionalContext"])
        self.assertNotIn("ag7", self.agents())  # nothing tied (SubagentStart records it as before)
        for kind in ("foreman:planner", "Explore"):
            self.assertEqual(self.post_agent("no line", agent_id="ag8", kind=kind).stdout, "")

    def test_a_subagent_launching_another_is_tied_too(self):
        _token, line = self.launch("sub1")
        self.start("parent")
        r = self.hook(
            "agents",
            {
                "hook_event_name": "PostToolUse",
                "tool_name": "Agent",
                "agent_id": "parent",
                "agent_type": "foreman:dev",
                "tool_input": {"prompt": line, "subagent_type": "foreman:mechanic"},
                "tool_response": {"status": "async_launched", "agentId": "child"},
            },
        )
        self.assertEqual(r.returncode, 0)
        d = self.agents()
        self.assertEqual((d["child"]["name"], d["parent"]["tool"]), ("sub1", "Agent"))

    # criterion 7
    def test_agents_name_by_hand(self):
        util.write_json(
            self.project.state / "agents.json",
            {
                "a14a8c0001": {"start": "2026-10-31T09:37:00", "type": "foreman:dev"},
                "a14a8c0002": {"start": "2026-10-31T09:38:00", "type": "foreman:dev"},
                "b0000": {"start": "2026-10-31T09:39:00", "type": "foreman:dev", "name": "c7"},
            },
        )
        r = self.cli(
            "agents",
            "--name",
            "a14a8c0001",
            "c6",
            "--issue",
            "AB-1",
            "--sub",
            "T1",
            "--branch",
            "feat/es256-only",
            "--repo",
            "app",
        )
        self.assertEqual(r.returncode, 0, r.stderr)
        a = self.agents()["a14a8c0001"]
        self.assertEqual(
            {k: a.get(k) for k in ("name", "issue", "sub", "branch", "repo", "named_by")},
            {
                "name": "c6",
                "issue": "AB-1",
                "sub": "T1",
                "branch": "feat/es256-only",
                "repo": "app",
                "named_by": "hand",
            },
        )
        self.assertEqual(self.cli("agents", "--name", "a14a8c", "c8").returncode, 2)  # two match
        self.assertEqual(self.cli("agents", "--name", "zz", "c8").returncode, 2)  # none
        self.assertEqual(self.cli("agents", "--name", "a14a8c0002", "c7").returncode, 2)  # a live c7
        self.assertEqual(self.cli("agents", "--name", "a14a8c0002", "c9").returncode, 0)  # a unique prefix
        self.assertEqual(self.agents()["a14a8c0002"]["name"], "c9")

    def test_launch_with_a_repo_is_held_while_paused(self):
        self.project.state.mkdir(parents=True, exist_ok=True)
        (self.project.state / "pause").write_text("paused at 21:07\n")
        self.assertIsNotNone(pause.guard_check(self.project, "foreman launch zed7 --repo app --branch b"))
        self.assertIsNotNone(pause.guard_check(self.project, "foreman launch zed7 --repo=app --branch=b"))
        self.assertIsNone(pause.guard_check(self.project, "foreman launch plan3 --issue AB-1"))

    def test_prune(self):
        old = {"name": "x", "agent": None, "at": "2020-01-01T00:00:00"}
        fresh = {"name": "y", "agent": None, "at": identity.now_iso()}
        gone = {"name": "z", "agent": "ag-gone", "at": identity.now_iso()}
        kept = {"name": "w", "agent": "ag1", "at": "2020-01-01T00:00:00"}
        self.assertEqual(set(identity.prune({"a": old, "b": fresh, "c": gone, "d": kept}, {"ag1": {}})), {"b", "d"})
        self.assertEqual(identity.token_in("x [foreman launch abcdefgh23] y"), "abcdefgh23")
        self.assertIsNone(identity.token_in("[foreman launch ABCDEFGH23]"))


class ShadowedTypesTest(ProjectCase):
    toml = TOML

    def test_check_lists_the_types_a_project_defines_again(self):
        """An agent file of the home, of a repo or of the user's folder whose name is one of foreman's types shadows
        it: shadowed_types finds it by its frontmatter name (else its file name) and `foreman check` warns, without
        failing; a type with its own name is not listed."""
        user = self.tmp / "claude-config"
        for d, f, text in (
            (self.home, "dev.md", "---\nname: dev\ndescription: a copy\n---\nbody\n"),
            (self.tmp / "app", "my-planner.md", "---\nname: 'planner'\ndescription: renamed file\n---\n"),
            (self.tmp / "app", "quayside-dev.md", "---\nname: quayside-dev\ndescription: its own\n---\n"),
            (user, "mechanic.md", "no frontmatter: the file name is its name\n"),
            (user, "reviewer.md", "---\nname: reviewer\ndescription: a copy\n---\n"),
        ):
            (d / ".claude/agents").mkdir(parents=True, exist_ok=True)
            (d / ".claude/agents" / f).write_text(text)
        found = identity.shadowed_types(self.project, user / ".claude/agents")
        self.assertEqual(
            [(n, f.relative_to(self.tmp).as_posix()) for n, f in found],
            [
                ("dev", "home/.claude/agents/dev.md"),
                ("planner", "app/.claude/agents/my-planner.md"),
                ("mechanic", "claude-config/.claude/agents/mechanic.md"),
                ("reviewer", "claude-config/.claude/agents/reviewer.md"),
            ],
        )
        r = self.cli("check", env={"CLAUDE_CONFIG_DIR": str(user / ".claude")})
        warned = re.findall(r"warning: (\S+) shadows foreman's agent type (\w+)", r.stdout)
        self.assertEqual(sorted(t for _, t in warned), ["dev", "mechanic", "planner", "reviewer"], r.stdout)
        self.assertNotIn("quayside-dev", r.stdout)
        (user / ".claude/agents/mechanic.md").unlink()
        (user / ".claude/agents/reviewer.md").unlink()
        (self.home / ".claude/agents/dev.md").unlink()
        (self.tmp / "app/.claude/agents/my-planner.md").unlink()
        r = self.cli("check", env={"CLAUDE_CONFIG_DIR": str(user / ".claude")})
        self.assertNotIn("shadows", r.stdout)
