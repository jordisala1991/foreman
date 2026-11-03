"""A fixed day of one product for the history (tests/test_timeline.py) and `foreman-dev timeline`
(dev/tests/test_dev_timeline.py and its golden corpus): an orchestrator session with two agents on 2020-01-15, one
delivered and integrated, one refused by the delivery check and let go incomplete with a failed CI run; notes,
progress, launches, loop passes, the inbox and a decision. State times are local and the transcripts' UTC: run it
with TZ=UTC."""

import json
from pathlib import Path

DAY = "2020-01-15"
S = "11111111-aaaa-4aaa-8aaa-000000000001"
BRIEF1 = "Do the first thing. Read the rules first."


def _call(mid, hhmm, cr, cw=0, out=10, sidechain=False, model="claude-opus-5-5"):
    return json.dumps(
        {
            "type": "assistant",
            "isSidechain": sidechain,
            "timestamp": f"{DAY}T{hhmm}:00.000Z",
            "message": {
                "id": mid,
                "model": model,
                "role": "assistant",
                "content": [{"type": "text", "text": "ok"}],
                "usage": {
                    "input_tokens": 1,
                    "cache_read_input_tokens": cr,
                    "cache_creation_input_tokens": cw,
                    "output_tokens": out,
                },
            },
        }
    )


def _user(text, sidechain=False):
    return json.dumps(
        {
            "type": "user",
            "isSidechain": sidechain,
            "timestamp": f"{DAY}T08:00:00.000Z",
            "message": {"role": "user", "content": text},
        }
    )


def _write(path, lines):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(ln + "\n" for ln in lines))


def build(claude_projects, state, notes_dir, prefix, repo_key):
    """Writes the day: transcripts under `claude_projects`, the product's state folder `state`, its notes."""
    P = prefix
    d = Path(claude_projects) / "-home-product"
    _write(
        d / f"{S}.jsonl",
        [
            _user("Orchestrator loop"),
            _call("o1", "09:00", 20000, 5000, 100),
            _call("o2", "10:00", 30000, 2000, 200),
            _call("o3", "10:50", 41000, 1000, 50),
        ],
    )
    subs = d / S / "subagents"
    a1, a2 = subs / "agent-a1.jsonl", subs / "agent-a2.jsonl"
    _write(
        a1,
        [
            _user(
                f"[foreman launch aaaaaaaaaa] dev1 · {P}-1 T1 · branch {P.lower()}1/t1 · production\n\n" + BRIEF1, True
            ),
            _call("x1", "09:10", 5000, 300, 400, True),
            _call("x2", "09:40", 7000, 100, 600, True),
        ],
    )
    _write(
        a2,
        [
            _user(
                f"[foreman launch bbbbbbbbbb] dev2 · {P}-2 T1 · branch {P.lower()}2/t1 · production\n\n"
                "Do the second thing.",
                True,
            ),
            _call("y1", "10:12", 9000, 200, 300, True, "claude-sonnet-5-5"),
        ],
    )
    st = Path(state)
    (st / "integrate").mkdir(parents=True, exist_ok=True)
    (st / "session").write_text(S + "\n")
    (st / "orchestrator.json").write_text(json.dumps({"session": S, "name": "orch-a", "since": f"{DAY}T08:50:00"}))
    (st / "launches.json").write_text(
        json.dumps(
            {
                "aaaaaaaaaa": {
                    "name": "dev1",
                    "issue": f"{P}-1",
                    "sub": "T1",
                    "repo": repo_key,
                    "branch": f"{P.lower()}1/t1",
                    "type": "dev",
                    "production": True,
                    "at": f"{DAY}T09:04:00",
                    "session": S,
                    "agent": "a1",
                },
                "bbbbbbbbbb": {
                    "name": "dev2",
                    "issue": f"{P}-2",
                    "sub": "T1",
                    "repo": repo_key,
                    "branch": f"{P.lower()}2/t1",
                    "type": "dev",
                    "model": "sonnet",
                    "production": True,
                    "at": f"{DAY}T10:05:00",
                    "session": S,
                    "agent": "a2",
                },
                "cccccccccc": {"name": "dev3", "issue": f"{P}-3", "type": "dev", "at": f"{DAY}T10:20:00", "session": S},
            }
        )
    )
    (st / "agents.json").write_text(
        json.dumps(
            {
                "a1": {
                    "start": f"{DAY}T09:05:00",
                    "type": "foreman:dev",
                    "transcript": str(a1),
                    "name": "dev1",
                    "issue": f"{P}-1",
                    "sub": "T1",
                    "branch": f"{P.lower()}1/t1",
                    "launch": "aaaaaaaaaa",
                    "end": f"{DAY}T09:45:00",
                    "delivered": f"{DAY}T09:45:00",
                    "outcome": "ok",
                    "model": "claude-opus-5-5",
                    "final_chars": 900,
                },
                "a2": {
                    "start": f"{DAY}T10:06:00",
                    "type": "foreman:dev",
                    "transcript": str(a2),
                    "name": "dev2",
                    "issue": f"{P}-2",
                    "sub": "T1",
                    "branch": f"{P.lower()}2/t1",
                    "launch": "bbbbbbbbbb",
                    "model_asked": "sonnet",
                    "end": f"{DAY}T10:35:00",
                    "delivery_blocks": 2,
                    "blocked_at": f"{DAY}T10:30:00",
                    "delivery_incomplete": {"at": f"{DAY}T10:35:00", "missing": ["report"]},
                    "model": "claude-sonnet-5-5",
                    "final_chars": 4200,
                },
            }
        )
    )
    (st / "progress.jsonl").write_text(
        "".join(
            json.dumps(x) + "\n"
            for x in [
                {"time": f"{DAY} 09:20", "agent": "dev1", "text": "halfway", "agent_id": "a1"},
                {
                    "time": f"{DAY} 09:44",
                    "agent": "dev1",
                    "text": "the first thing works",
                    "agent_id": "a1",
                    "done": True,
                    "outcome": "ok",
                },
                {"time": f"{DAY} 10:25", "agent": "dev2", "text": "tests fail on the old Python"},
                {"time": "2020-01-14 23:59", "agent": "dev0", "text": "the day before"},
            ]
        )
    )
    br1, br2 = f"{P.lower()}1-t1", f"{P.lower()}2-t1"
    (st / "integrate" / f"{br1}-ci.log").write_text(
        f"== {DAY} 09:47:00 in /wt/{br1}: make check\nok\n== rc 0 in 30 s\n"
        f"== {DAY} 09:49:00 in {Path(notes_dir).parent}: make lint\n== rc 0 in 2 s\n"
    )
    (st / "integrate" / f"{br2}-ci.log").write_text(
        f"== {DAY} 10:40:00 in /wt/{br2}: make check\nFAIL: test_x\n== rc 1 in 41 s\n"
    )
    passes = [
        ("09:00", False, ["wip", "disk_low"], ["wip"]),
        ("09:15", True, [], []),
        ("10:45", False, ["delivery_incomplete"], ["delivery_incomplete"]),
        ("10:50", True, ["disk_low"], []),
        ("10:55", True, [], []),
        ("11:00", True, [], []),
    ]
    (st / "loop.jsonl").write_text(
        "".join(
            json.dumps(
                {
                    "time": f"{DAY}T{t}:00",
                    "session": S,
                    "skipped": sk,
                    "notices": ns,
                    "actionable": act,
                    "duration_s": 0.4,
                }
            )
            + "\n"
            for t, sk, ns, act in passes
        )
    )
    (st / "inbox.json").write_text(
        json.dumps(
            [
                {"id": 1, "time": f"{DAY} 09:30", "from": "owner", "text": f"go on with {P}-2", "read": True},
                {
                    "id": 2,
                    "time": f"{DAY} 10:10",
                    "from": "owner",
                    "text": "the short one",
                    "read": True,
                    "decision": f"{P}-1 J1, which name?",
                },
                {"id": 3, "time": f"{DAY} 10:11", "from": "orchestrator", "text": "noted", "read": True},
            ]
        )
    )
    nd = Path(notes_dir)
    nd.mkdir(parents=True, exist_ok=True)
    (nd / f"{DAY}.md").write_text(
        f"# Notes {DAY}\n\n## 08:55 Plan {P}-1 approved\nbody\n\n## 09:50 Integrated {repo_key}:{P.lower()}1/t1 "
        f"({P}-1 T1)\nMerged.\n- 10:40 the second agent is stuck on the old Python\n\n## 10:46 Handover\n"
        f"Outgoing session: orch-a (session {S}, since {DAY}T08:50:00).\n"
    )
    (nd / "2020-01-14.md").write_text("# Notes\n\n## 23:00 The day before\n")
