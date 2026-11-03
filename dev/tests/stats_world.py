"""Fake Claude Code transcripts for `foreman-dev stats` and `fill` (dev/tests: test_dev_stats.py, test_dev_fill.py and
the golden corpus): two products, four
sessions and three agents on 2020-01-15 (UTC), with what foreman records about them. The figures are chosen so every
grouping has a distinct, hand-checkable answer (WORLD below)."""

import json
from pathlib import Path

DAY = "2020-01-15"
S_ORCH = "11111111-aaaa-4aaa-8aaa-000000000001"  # product A, orchestrator (named in a handover note), closed
S_OTHER = "22222222-bbbb-4bbb-8bbb-000000000002"  # product A, another session, opened in A's repo
S_B = "33333333-cccc-4ccc-8ccc-000000000003"  # product B, recorded in install/sessions.json, opened elsewhere
S_NONE = "44444444-dddd-4ddd-8ddd-000000000004"  # no product (opened in an unrelated folder)


def call(
    mid, hhmm, cr, cw=0, inp=1, out=10, model="claude-opus-5-5", sidechain=False, day=DAY, version="2.1.311", cwd=None
):
    d = {
        "type": "assistant",
        "isSidechain": sidechain,
        "timestamp": f"{day}T{hhmm}:00.000Z",
        "version": version,
        "message": {
            "id": mid,
            "model": model,
            "role": "assistant",
            "content": [{"type": "text", "text": "ok"}],
            "usage": {
                "input_tokens": inp,
                "cache_read_input_tokens": cr,
                "cache_creation_input_tokens": cw,
                "output_tokens": out,
            },
        },
    }
    if cwd:
        d["cwd"] = cwd
    return json.dumps(d)


def user(text, cwd, sidechain=False, version="2.1.311"):
    return json.dumps(
        {
            "type": "user",
            "isSidechain": sidechain,
            "cwd": cwd,
            "version": version,
            "timestamp": f"{DAY}T08:00:00.000Z",
            "message": {"role": "user", "content": text},
        }
    )


def _write(path, lines):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(ln + "\n" for ln in lines))


def build(claude_projects, state_root, a, b, elsewhere):
    """Writes the world. `a` and `b`: {"name", "home", "repo", "prefix"} of the two products (A has a backlog sub-issue
    whose agent is `plan2`, written by the caller or not); `elsewhere` a folder of no product."""
    cp = Path(claude_projects)
    da, db, dn = cp / "-a", cp / "-b", cp / "-none"
    # A's orchestrator: 3 calls (the first on two lines), one before the window's day; a cost-state at its close.
    _write(
        da / f"{S_ORCH}.jsonl",
        [
            user("start", str(a["home"])),
            call("o0", "07:00", 50000, day="2020-01-14"),
            call("o1", "08:00", 20000, 3000, inp=2, out=100),
            call("o1", "08:00", 20000, 3000, inp=2, out=150),  # the same response, second block: counts once
            call("o2", "09:00", 30000, 1000, out=200),
            call("o3", "10:30", 40000, 500, out=300),
            json.dumps(
                {
                    "type": "cost-state",
                    "totalCostUSD": 12.345,
                    "startTime": 1578985200000,
                    "modelUsage": {
                        "claude-opus-5-5": {
                            "inputTokens": 8,
                            "outputTokens": 1960,
                            "cacheReadInputTokens": 160000,
                            "cacheCreationInputTokens": 5100,
                        },
                        "claude-sonnet-5-5": {"inputTokens": 1, "outputTokens": 50, "cacheReadInputTokens": 4000},
                        "claude-haiku-5-5": {"inputTokens": 900, "outputTokens": 40},
                    },
                }
            ),
        ],
    )
    subs = da / S_ORCH / "subagents"
    # a1: recorded by foreman (agents.json): A-1 T1, dev, opus.
    _write(
        subs / "agent-a1.jsonl",
        [
            user(f"[foreman launch abcdefghij] dev1 · {a['prefix']}-1 T1 · production", str(a["home"]), sidechain=True),
            call("x1", "08:10", 5000, 200, out=500, sidechain=True),
            call("x2", "08:20", 6000, 100, out=500, sidechain=True),
        ],
    )
    (subs / "agent-a1.meta.json").write_text(json.dumps({"agentType": "foreman:dev", "description": "dev1"}))
    # a2: no launch record; named by hand (agents.json: name only), a sub-issue's agent in A's backlog when the caller
    # wrote it; planner, opus.
    _write(
        subs / "agent-a2.jsonl",
        [
            user("Plan it.", str(a["home"]), sidechain=True),
            call("y1", "09:10", 9000, 300, out=300, sidechain=True),
        ],
    )
    (subs / "agent-a2.meta.json").write_text(json.dumps({"agentType": "foreman:planner", "description": "a plan"}))
    # a3: no record, no issue anywhere; general-purpose on sonnet.
    _write(
        subs / "agent-a3.jsonl",
        [
            user("Look around.", str(a["home"]), sidechain=True),
            call("z1", "10:00", 4000, 0, out=50, model="claude-sonnet-5-5", sidechain=True),
        ],
    )
    (subs / "agent-a3.meta.json").write_text(json.dumps({"agentType": "general-purpose", "description": "study"}))
    # A's other session, opened in its repo, on sonnet, still open (no cost-state).
    _write(
        da / f"{S_OTHER}.jsonl",
        [
            user("hello", str(a["repo"])),
            call("p1", "11:00", 8000, 800, out=40, model="claude-sonnet-5-5"),
            call("p2", "11:30", 9000, 100, out=60, model="claude-sonnet-5-5"),
        ],
    )
    # B's session: opened in an unrelated folder, but its hooks recorded it as B's.
    _write(
        db / f"{S_B}.jsonl",
        [
            user("hi", str(elsewhere)),
            call("q1", "12:00", 70000, 2000, inp=3, out=700),
        ],
    )
    install = Path(state_root) / "install"
    install.mkdir(parents=True, exist_ok=True)
    (install / "sessions.json").write_text(json.dumps({S_B: {"project": b["name"], "cwd": str(elsewhere)}}))
    # A session of no product, on a Claude Code version the reader was not tested with.
    _write(
        dn / f"{S_NONE}.jsonl",
        [
            user("x", str(elsewhere), version="9.9.9"),
            call("r1", "13:00", 1000, 0, out=5, version="9.9.9"),
        ],
    )
    # What foreman recorded: A's agents.json (a1 by its launch, a2 by hand) and the handover note naming A's
    # orchestrator.
    st = Path(state_root) / a["name"]
    st.mkdir(parents=True, exist_ok=True)
    (st / "agents.json").write_text(
        json.dumps(
            {
                "a1": {
                    "name": "dev1",
                    "issue": f"{a['prefix']}-1",
                    "sub": "T1",
                    "type": "foreman:dev",
                    "named_by": "launch",
                },
                "a2": {"name": "plan2", "type": "foreman:planner", "named_by": "hand"},
            }
        )
    )
    notes = Path(a["home"]) / "notes"
    notes.mkdir(parents=True, exist_ok=True)
    with open(notes / f"{DAY}.md", "a") as f:
        f.write(
            f"\n## 10:45 Handover\n\nOutgoing session: orch-a (session {S_ORCH}, since {DAY}T07:55:00); "
            "context 40000 tokens.\n"
        )


# What the window 2020-01-15 (UTC) holds, by hand:
# A main orchestrator: o1 (cr 20000, cw 3000, in 2, out 150), o2 (30000, 1000, 1, 200), o3 (40000, 500, 1, 300)
#   → 3 calls, cr 90000, cw 4500, out 650, in 4; peak 40501; o0 is the day before.
# a1: 2 calls, cr 11000, cw 300, out 1000; a2: 1, cr 9000, cw 300, out 300; a3: 1, cr 4000 (sonnet), out 50.
# A other session: 2 calls, cr 17000, cw 900, out 100 (sonnet).
# B: 1 call, cr 70000, cw 2000, out 700. None: 1 call, cr 1000, out 5.
WORLD = {
    "A": 90000 + 11000 + 9000 + 4000 + 17000,
    "B": 70000,
    "none": 1000,
    "orch_session": 90000 + 11000 + 9000 + 4000,
    "orch_whole": 50000 + 90000 + 11000 + 9000 + 4000,
}


# ---------- `foreman stats --fill`: one orchestrator session with every kind of piece, on another day ----------

S_FILL = "55555555-eeee-4eee-8eee-000000000005"
A_FILL = "f1agent0000000001"
FILL_TOKEN = "fillagentz"
FILL_DAY = "2020-01-10"
WIP = "WIP (pipeline not full): production 0/1: launch the next `ready` issue."
FINAL = "r" * 2000  # each agent's final message (the rule: median over 1,500)
BIG = "big line\n" * 3000  # the largest tool output: 27,000 characters


def _ts(hhmm, s=0):
    return f"{FILL_DAY}T{hhmm}:{s:02d}.000Z"


def fcall(mid, hhmm, ctx, out, blocks, thinking=0, sidechain=False):
    usage = {
        "input_tokens": 1,
        "cache_read_input_tokens": ctx - 1001,
        "cache_creation_input_tokens": 1000,
        "output_tokens": out,
    }
    if thinking:
        usage["output_tokens_details"] = {"thinking_tokens": thinking}
    return json.dumps(
        {
            "type": "assistant",
            "isSidechain": sidechain,
            "timestamp": _ts(hhmm),
            "version": "2.1.311",
            "message": {"id": mid, "model": "claude-opus-5-5", "role": "assistant", "content": blocks, "usage": usage},
        }
    )


def tool(tid, name, **inp):
    return {"type": "tool_use", "id": tid, "name": name, "input": inp}


def result(tid, hhmm, content, sidechain=False):
    return json.dumps(
        {
            "type": "user",
            "isSidechain": sidechain,
            "timestamp": _ts(hhmm, 30),
            "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": tid, "content": content}]},
        }
    )


def prompt(hhmm, text, **extra):
    return json.dumps(
        {
            "type": "user",
            "isSidechain": False,
            "timestamp": _ts(hhmm, 40),
            "message": {"role": "user", "content": text},
            **extra,
        }
    )


def attachment(hhmm, kind, rendered, **extra):
    return json.dumps(
        {
            "type": "attachment",
            "isSidechain": False,
            "timestamp": _ts(hhmm, 50),
            "attachment": {"type": kind, **extra},
            "rendered": [{"content": rendered}],
        }
    )


def hook(hhmm, *texts, event="PostToolUse"):
    return attachment(hhmm, "hook_additional_context", "\n".join(texts), content=list(texts), hookEvent=event)


def notification(n):
    return (
        f"<task-notification>\n<task-id>a{n}</task-id>\n<status>completed</status>\n<summary>done</summary>\n"
        f"<result>{FINAL}</result>\n</task-notification>"
    )


def build_fill(claude_projects, cwd, state=None):
    """One main session (09:00 → 10:00, context 20,000 → 150,000) and one of its agents. Δ by interval:
    c0→c1 25,000: own 1,000 (thinking 400) + the sed output 24,000; c1 creates the loop at 45,000;
    c1→c2 8,400: own 200 + the loop tool's result, 11 WIP notices, a loop pass, another hook's text (by chars);
    c2→c3 12,000: own 500 (an Agent brief, a foreman note, a foreman agents call, a Read) + their results (the Read's
    an image); c3→c4 40,000: own 300 + three final messages, the owner, a queued message, a peer, a skill, a reminder
    and a skill listing; c4→c5 3,000: own 100 + a ToolSearch result with no text; c5→c6 a compaction (−60,000);
    c6→c7 101,600: own 200 + `foreman integrate`'s output."""
    d = Path(claude_projects) / "-fill"
    lines = [
        prompt("08:59", "Start the session.", cwd=str(cwd)),
        fcall(
            "f0",
            "09:00",
            20000,
            1000,
            [
                {"type": "thinking", "thinking": "t" * 400},
                {"type": "text", "text": "x" * 300},
                tool("t0", "Bash", command="cd /w && sed -n 1,3000p big.py"),
            ],
            thinking=400,
        ),
        result("t0", "09:00", BIG),
        fcall("f1", "09:10", 45000, 200, [tool("t1", "CronCreate", cron="*/30 * * * *", prompt="pass")]),
        result("t1", "09:10", "scheduled"),
    ]
    lines += [hook("09:10", WIP) for _ in range(11)]
    lines += [
        hook(
            "09:10",
            "Loop pass 09:10 (heartbeat written, 0.0 s). Act on each line (those marked (info) only "
            "inform):\n- WIP (pipeline not full): unblocked ready 0/2 (refining 0): nothing to plan.",
            event="UserPromptSubmit",
        ),
        hook("09:10", "lint: nothing to fix"),
        fcall(
            "f2",
            "09:20",
            53400,
            500,
            [
                tool("t2", "Agent", prompt="b" * 2000, description="dev1"),
                tool("t3", "Bash", command='FOREMAN_WHO=o foreman --project faro note "' + "n" * 600 + '"'),
                tool("t4", "Bash", command="foreman --project faro agents"),
                tool("t5", "Read", file_path="/w/shot.png"),
            ],
        ),
        result("t2", "09:20", [{"type": "text", "text": "Async agent launched successfully."}]),
        result("t3", "09:20", "noted"),
        result("t4", "09:20", '{"agents": []}' + " " * 400),
        result("t5", "09:20", [{"type": "image", "source": {"type": "base64", "data": "AAAA"}}]),
        fcall("f3", "09:30", 65400, 300, [{"type": "text", "text": "Waiting for the agents."}]),
    ]
    lines += [prompt("09:30", notification(n), origin={"kind": "task-notification"}) for n in range(3)]
    lines += [
        prompt("09:30", "how is it going?"),
        attachment("09:30", "queued_command", "<system-reminder>The user sent: and the panel?</system-reminder>"),
        prompt("09:30", "Another Claude session sent a message:\nhello", isMeta=True, origin={"kind": "peer"}),
        prompt("09:30", "Base directory for this skill: /s\n# Pass\n" + "s" * 500, isMeta=True, sourceToolUseID="t9"),
        prompt("09:30", "<system-reminder>tools used</system-reminder>", isMeta=True),
        attachment("09:30", "skill_listing", "- a skill: " + "k" * 300),
        json.dumps(
            {
                "type": "attachment",
                "isSidechain": False,
                "timestamp": _ts("09:30"),
                "attachment": {"type": "hook_success", "content": "", "stdout": "{}"},
            }
        ),  # not rendered
        fcall("f4", "09:40", 105400, 100, [tool("t6", "ToolSearch", query="select:Monitor")]),
        result("t6", "09:40", [{"type": "tool_reference", "tool_name": "Monitor"}]),
        fcall("f5", "09:45", 108400, 100, [tool("t7", "Bash", command="echo compacting")]),
        "{broken line",
        fcall(
            "f6", "09:50", 48400, 200, [tool("t8", "Bash", command="cd /w; foreman --project faro integrate faro b")]
        ),
        result("t8", "09:50", "integrated " + "i" * 1000),
        fcall("f7", "10:00", 150000, 50, [{"type": "text", "text": "Done."}]),
    ]
    _write(d / f"{S_FILL}.jsonl", lines)
    # the agent: its own transcript, every record a sidechain one
    _write(
        d / S_FILL / "subagents" / f"agent-{A_FILL}.jsonl",
        [
            json.dumps(
                {
                    "type": "user",
                    "isSidechain": True,
                    "timestamp": _ts("09:21"),
                    "cwd": str(cwd),
                    "message": {"role": "user", "content": f"[foreman launch {FILL_TOKEN}] dev1 · production\nDo it."},
                }
            ),
            fcall("g0", "09:21", 15000, 400, [tool("u0", "Bash", command="cat lib/a.py")], sidechain=True),
            result("u0", "09:21", "code\n" * 1000, sidechain=True),
            fcall("g1", "09:25", 20400, 300, [{"type": "text", "text": "Delivered."}], sidechain=True),
        ],
    )
    (d / S_FILL / "subagents" / f"agent-{A_FILL}.meta.json").write_text(
        json.dumps({"agentType": "foreman:dev", "description": "dev1"})
    )
    if state is not None:  # the product's state: the launch its brief's line names (the agent's name)
        f = Path(state) / "launches.json"
        f.parent.mkdir(parents=True, exist_ok=True)
        launches = json.loads(f.read_text()) if f.exists() else {}
        launches[FILL_TOKEN] = {"name": "dev1", "type": "dev", "production": True}
        f.write_text(json.dumps(launches))
