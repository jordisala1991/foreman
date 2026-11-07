"""Lib/foreman/transcripts.py, the one reader of Claude Code's transcripts, on fake transcripts that pin what
it assumes of the format (a response on several lines, `<synthetic>`, sidechain, broken lines, `Z` timestamps,
`cost-state`, subagent files) and the one definition of context tokens shared by the loop, the panel and identity."""

import json
import os
import subprocess
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from foreman import identity, panel, transcripts


def rec(mid, ts="2026-10-31T10:00:00.123Z", model="claude-opus-5-5", sidechain=False, text=None, **usage):
    u = {"input_tokens": 1, "cache_read_input_tokens": 100, "cache_creation_input_tokens": 10, "output_tokens": 5}
    u.update(usage)
    block = {"type": "text", "text": text} if text is not None else {"type": "tool_use", "name": "Bash"}
    return json.dumps(
        {
            "type": "assistant",
            "isSidechain": sidechain,
            "timestamp": ts,
            "uuid": f"u-{mid}-{ts}",
            "message": {"id": mid, "model": model, "role": "assistant", "content": [block], "usage": u},
        }
    )


class TranscriptsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(subprocess.run, ["rm", "-rf", str(self.tmp)])

    def write(self, lines, name="S1.jsonl"):
        f = self.tmp / name
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("".join(ln + "\n" for ln in lines))
        return f

    def test_a_response_on_several_lines_counts_once(self):
        f = self.write(
            [
                rec("m1", input_tokens=3, cache_read_input_tokens=1000, output_tokens=7),
                rec("m1", input_tokens=3, cache_read_input_tokens=1000, output_tokens=7),  # second block, same usage
                rec("m1", input_tokens=3, cache_read_input_tokens=1000, output_tokens=40),  # third: the final usage
                rec("m2", cache_read_input_tokens=2000),
            ]
        )
        cs = transcripts.calls(f)
        self.assertEqual([c["id"] for c in cs], ["m1", "m2"])
        self.assertEqual(sum(c["cache_read"] for c in cs), 3000)
        self.assertEqual((cs[0]["input"], cs[0]["output"]), (3, 40))

    def test_fields_and_timestamps(self):
        u = {
            "cache_creation": {"ephemeral_5m_input_tokens": 6, "ephemeral_1h_input_tokens": 4},
            "output_tokens_details": {"thinking_tokens": 2},
        }
        c = transcripts.calls(self.write([rec("m1", ts="2026-10-31T10:00:00Z", **u)]))[0]
        self.assertEqual(
            (
                c["input"],
                c["cache_read"],
                c["cache_write"],
                c["cache_write_1h"],
                c["output"],
                c["thinking"],
                c["sidechain"],
                c["model"],
            ),
            (1, 100, 10, 4, 5, 2, False, "claude-opus-5-5"),
        )
        self.assertEqual(c["ts"], datetime(2026, 10, 31, 10, 0, tzinfo=timezone.utc))
        self.assertEqual(transcripts.context(c), 111)
        for s, h, us in (
            ("2026-10-31T10:00:00.5Z", 10, 500000),
            ("2026-10-31T12:00:00.123456789+02:00", 10, 123456),
            ("2026-10-31T10:00:00+0000", 10, 0),
        ):
            t = transcripts.parse_ts(s)
            self.assertEqual((t.astimezone(timezone.utc).hour, t.microsecond), (h, us))
        for bad in ("", None, 5, "yesterday", "2026-13-45T00:00:00Z"):
            self.assertIsNone(transcripts.parse_ts(bad))

    def test_since_and_until(self):
        f = self.write(
            [
                rec("a", ts="2026-10-31T09:59:59Z"),
                rec("b", ts="2026-10-31T10:00:00Z"),
                rec("c", ts="2026-10-31T11:00:00Z"),
                rec("d", ts="bad"),
            ]
        )
        t = lambda h: datetime(2026, 10, 31, h, tzinfo=timezone.utc)  # noqa: E731
        ids = lambda **k: [c["id"] for c in transcripts.calls(f, **k)]  # noqa: E731
        self.assertEqual(ids(), ["a", "b", "c", "d"])
        self.assertEqual(ids(since=t(10)), ["b", "c"])
        self.assertEqual(ids(since=t(10), until=t(11)), ["b"])
        self.assertEqual(ids(until=t(10)), ["a"])
        local10 = datetime(2026, 10, 31, 10).astimezone(timezone.utc)  # a naive bound is local time
        self.assertEqual(ids(since=datetime(2026, 10, 31, 10)), ids(since=local10))

    def test_synthetic_sidechain_and_garbage_are_handled(self):
        f = self.write(
            [
                rec("s", model="<synthetic>", input_tokens=0, cache_read_input_tokens=0, cache_creation_input_tokens=0),
                rec("side", sidechain=True, cache_read_input_tokens=9),
                '{"type": "assistant", "message": {"usage": ',  # broken
                'not json at all "usage"',
                json.dumps({"type": "user", "message": {"content": '"assistant" "usage"'}}),
                json.dumps({"type": "assistant", "message": {"id": "x", "usage": [1]}}),  # unknown shape
                json.dumps(
                    {
                        "type": "assistant",
                        "message": {"id": "y", "model": 5, "usage": {"input_tokens": "7", "output_tokens": True}},
                    }
                ),
                json.dumps([1, 2]),
                "",
                "\xff\xfe",
                json.dumps({"type": "future-record", "whatever": {"usage": {}}}),
                rec("main", cache_read_input_tokens=50),
            ]
        )
        cs = transcripts.calls(f)
        self.assertEqual([c["id"] for c in cs], ["side", "y", "main"])
        self.assertEqual((cs[1]["input"], cs[1]["output"], cs[1]["model"], cs[1]["ts"]), (0, 0, None, None))
        self.assertEqual(transcripts.last_context(f), 61)  # the main chain: not the sidechain, not the synthetic
        self.assertEqual(transcripts.last_context(self.write([rec("side", sidechain=True)], "side.jsonl")), None)
        self.assertEqual(transcripts.last_context(self.tmp / "side.jsonl", sidechain=True), 111)

    def test_missing_unreadable_and_wrong_arguments_never_raise(self):
        for p in (self.tmp / "nope.jsonl", None, "", self.tmp, ()):
            self.assertEqual(transcripts.calls(p), [])
            self.assertIsNone(transcripts.last_context(p))
            self.assertIsNone(transcripts.model(p))
            self.assertIsNone(transcripts.cost_state_at(p)[0])
            self.assertIsNone(transcripts.last_message(p))
            self.assertIsNone(transcripts.last_text(p))
            self.assertEqual(transcripts.head(p), "")
        self.assertEqual(transcripts.subagents(None), [])
        self.assertEqual(transcripts.subagents(self.tmp / "nope.jsonl"), [])
        self.assertIsNone(transcripts.parse_call(None))
        self.assertIsNone(transcripts.parse_call(b'\xff"usage"'))

    def test_last_model_is_the_model_of_the_end(self):
        """What the agents screen asks every 10 s of a growing transcript: the model of the end, not a scan of it."""
        filler = [rec(f"x{i}", text="y" * 2000, model="<synthetic>") for i in range(400)]  # past the first window
        f = self.write([rec("a", model="claude-haiku-5-5"), *filler, rec("z", model="claude-opus-5-5"), *filler[:3]])
        self.assertEqual(transcripts.last_model(f), "claude-opus-5-5")
        self.assertIsNone(transcripts.last_model(self.write([rec("a", model="<synthetic>")], "y.jsonl")))

    def test_model_is_the_last_real_one(self):

        f = self.write(
            [
                rec("a", model="claude-haiku-5-5"),
                rec("b", model="<synthetic>"),
                'garbage "model"',
                rec("c", model="claude-opus-5-5"),
                rec("d", model="<synthetic>"),
            ]
        )
        self.assertEqual(transcripts.model(f), "claude-opus-5-5")
        self.assertIsNone(transcripts.model(self.write([rec("a", model="<synthetic>")], "x.jsonl")))
        self.assertEqual(transcripts.last_model(f), "claude-opus-5-5")
        self.assertIsNone(transcripts.last_model(self.tmp / "none.jsonl"))
        self.assertEqual(identity.transcript_model({"transcript": str(f)}), "claude-opus-5-5")
        self.assertEqual(identity.transcript_head({"transcript": str(f)}, 10), rec("a", model="claude-haiku-5-5")[:10])

    def test_one_definition_of_context_for_the_session_and_the_agent_cards(self):
        main = self.write(
            [
                rec("a", cache_read_input_tokens=900),
                rec(
                    "b", input_tokens=2, cache_read_input_tokens=600, cache_creation_input_tokens=8, output_tokens=10**6
                ),
            ]
        )
        self.assertEqual(transcripts.last_context(main), 610)
        agent = self.write(
            [
                rec(
                    "a",
                    sidechain=True,
                    input_tokens=2,
                    cache_read_input_tokens=600,
                    cache_creation_input_tokens=8,
                    output_tokens=10**6,
                )
            ],
            "agent-a1.jsonl",
        )
        self.assertEqual(panel.context_tokens(agent), 610)  # the output is not in it
        self.assertIsNone(panel.context_tokens(self.tmp / "nope.jsonl"))

    def test_cost_state_and_subagents_add_up(self):
        session = self.write(
            [
                rec("m1", cache_read_input_tokens=1000),
                rec("m1", cache_read_input_tokens=1000),
                json.dumps({"type": "cost-state", "modelUsage": {"claude-opus-5-5": {"cacheReadInputTokens": 1}}}),
                '{broken "cost-state"',
                json.dumps(
                    {
                        "type": "cost-state",
                        "totalCostUSD": 1.5,
                        "modelUsage": {"claude-opus-5-5": {"cacheReadInputTokens": 1700}},
                    }
                ),
            ]
        )
        sub = self.tmp / "S1" / "subagents"
        sub.mkdir(parents=True)
        (sub / "agent-b.jsonl").write_text(
            rec("x", sidechain=True, cache_read_input_tokens=500)
            + "\n"
            + rec("x", sidechain=True, cache_read_input_tokens=500)
            + "\n"
        )
        (sub / "agent-b.meta.json").write_text(json.dumps({"agentType": "foreman:dev", "description": "AB-1 T1"}))
        (sub / "agent-a.jsonl").write_text(rec("y", sidechain=True, cache_read_input_tokens=200) + "\n")
        (sub / "agent-a.meta.json").write_text("{broken")
        (sub / "other.jsonl").write_text("")
        subs = transcripts.subagents(session)
        self.assertEqual([(s["id"], s["meta"].get("agentType")) for s in subs], [("a", None), ("b", "foreman:dev")])
        total = sum(c["cache_read"] for p in [session] + [s["path"] for s in subs] for c in transcripts.calls(p))
        cs = transcripts.cost_state_at(session)[0]
        self.assertEqual(total, cs["modelUsage"]["claude-opus-5-5"]["cacheReadInputTokens"])
        self.assertEqual(cs["totalCostUSD"], 1.5)

    def test_the_tail_reader_and_the_last_message(self):
        big = json.dumps({"type": "user", "message": {"content": "y" * (3 * 1024 * 1024)}})
        f = self.write([rec("a", text="old", cache_read_input_tokens=1200), big])
        self.assertGreater(f.stat().st_size, transcripts.CONTEXT_FIRST_WINDOW * 4)
        self.assertEqual(transcripts.last_context(f), 1211)
        g = self.write(
            [
                json.dumps({"type": "user", "message": {"content": "hi"}}),
                rec("a", text="one"),
                json.dumps({"type": "user", "message": {"content": [{"type": "tool_result"}]}}),
                rec("b", sidechain=True, text="two"),
                json.dumps({"type": "user", "isSidechain": True, "message": {"content": "next"}}),
            ],
            "g.jsonl",
        )
        lm = transcripts.last_message(
            g
        )  # a subagent's own file: its records are sidechain ones; the prompt passed over
        self.assertEqual(lm["text"], "one\n\ntwo")
        self.assertEqual(transcripts.last_text(g), "two")  # the newest text block alone (the panel's card)
        self.assertEqual(os.path.basename(transcripts.transcript_of("S1") or "x"), "x")

    def test_session_info_sessions_and_when_the_cost_state_was_written(self):
        f = self.write(
            [
                json.dumps({"type": "queue-operation"}),
                '{broken "cwd"',
                json.dumps({"type": "user", "cwd": "/w/one", "version": "2.1.311", "message": {}}),
                json.dumps({"type": "user", "cwd": "/w/two", "version": "9", "message": {}}),
            ],
            "p1/S1.jsonl",
        )
        self.assertEqual(transcripts.session_info(f), {"cwd": "/w/one", "version": "2.1.311"})
        self.assertEqual(transcripts.session_info(self.tmp / "nope"), {"cwd": None, "version": None})
        g = self.write(
            [
                rec("a", ts="2026-10-31T10:00:00Z"),
                json.dumps({"type": "cost-state", "totalCostUSD": 2}),
                rec("b", ts="2026-10-31T11:00:00Z"),
            ],
            "p2/S2.jsonl",
        )
        os.utime(f, (1000, 1000))
        saved = os.environ.get("FOREMAN_CLAUDE_PROJECTS")
        self.addCleanup(
            lambda: (
                os.environ.pop("FOREMAN_CLAUDE_PROJECTS", None)
                if saved is None
                else os.environ.__setitem__("FOREMAN_CLAUDE_PROJECTS", saved)
            )
        )
        os.environ["FOREMAN_CLAUDE_PROJECTS"] = str(self.tmp)
        self.assertEqual([sid for _, sid in transcripts.sessions()], ["S1", "S2"])
        self.assertEqual([sid for _, sid in transcripts.sessions(newer_than=2000)], ["S2"])
        cs, at = transcripts.cost_state_at(g)
        self.assertEqual((cs["totalCostUSD"], at), (2, datetime(2026, 10, 31, 10, 0, tzinfo=timezone.utc)))
        self.assertEqual(transcripts.cost_state_at(f), (None, None))

    def test_entries_what_reaches_the_model(self):
        def u(content, **k):
            return json.dumps(
                {
                    "type": "user",
                    "timestamp": "2026-10-31T10:00:00Z",
                    "message": {"role": "user", "content": content},
                    **k,
                }
            )

        f = self.write(
            [
                json.dumps({"type": "queue-operation", "operation": "enqueue"}),  # not a piece
                u("hello"),
                rec("m1", text="Hi."),
                json.dumps(
                    {
                        "type": "assistant",
                        "isSidechain": False,
                        "message": {
                            "id": "m1",
                            "model": "m",
                            "content": [
                                {"type": "thinking", "thinking": "hmm"},
                                {"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": "ls"}},
                            ],
                        },
                    }
                ),
                u(
                    [
                        {"type": "tool_result", "tool_use_id": "t1", "content": "a\nb"},
                        {
                            "type": "tool_result",
                            "tool_use_id": "t2",
                            "content": [{"type": "image", "source": {}}, {"type": "text", "text": "pic"}],
                        },
                    ]
                ),
                u("<task-notification><result>done</result></task-notification>", origin={"kind": "task-notification"}),
                u("Another Claude session sent a message:\nx", isMeta=True),
                u("Base directory for this skill: /s", isMeta=True, sourceToolUseID="t3"),
                u("<system-reminder>r</system-reminder>", isMeta=True),
                u("Loop prompt", isMeta=True, turnOrigin="scheduled"),
                json.dumps(
                    {
                        "type": "attachment",
                        "attachment": {
                            "type": "hook_additional_context",
                            "hookEvent": "Stop",
                            "content": ["one", "two"],
                        },
                        "rendered": [{"content": "one\ntwo"}],
                    }
                ),
                json.dumps(
                    {"type": "attachment", "attachment": {"type": "hook_success", "content": ""}}
                ),  # not rendered
                json.dumps(
                    {
                        "type": "attachment",
                        "attachment": {"type": "date"},
                        "rendered": [{"content": [{"type": "text", "text": "today"}]}],
                    }
                ),
                "{broken",
                rec("m2", sidechain=True, text="side"),
            ]
        )
        es = list(transcripts.entries(f))
        self.assertEqual(
            [e["kind"] for e in es],
            [
                "prompt",
                "call",
                "call",
                "result",
                "result",
                "prompt",
                "prompt",
                "prompt",
                "prompt",
                "prompt",
                "attachment",
                "attachment",
                "call",
            ],
        )
        self.assertEqual(
            [e["origin"] for e in es if e["kind"] == "prompt"],
            ["owner", "task-notification", "peer", "skill", "meta", "scheduled"],
        )
        self.assertEqual(es[1]["id"], es[2]["id"])  # one response on two lines: two entries, one id
        self.assertEqual(
            [(b["type"], b["chars"]) for b in es[2]["blocks"]],
            [("thinking", 3), ("tool_use", len('{"command": "ls"}'))],
        )
        self.assertEqual(es[2]["blocks"][1]["name"], "Bash")
        self.assertIsNone(es[2]["usage"])  # a line without usage
        self.assertEqual(es[1]["usage"]["cache_read"], 100)
        self.assertEqual([(e["tool_id"], e["chars"], e["images"]) for e in es[3:5]], [("t1", 3, 0), ("t2", 3, 1)])
        self.assertEqual(
            (es[10]["type"], es[10]["hook"], es[10]["texts"], es[10]["chars"]),
            ("hook_additional_context", "Stop", ["one", "two"], 7),
        )
        self.assertEqual((es[11]["texts"], es[12]["sidechain"]), (["today"], True))
        self.assertEqual(list(transcripts.entries(self.tmp / "nope.jsonl")), [])
        self.assertEqual(list(transcripts.entries(None)), [])

    def test_find_sessions_and_agents_by_prefix(self):
        self.write([rec("a")], "p1/abc123.jsonl")
        self.write([rec("b")], "p1/abd456.jsonl")
        self.write([rec("c", sidechain=True)], "p1/abc123/subagents/agent-abe789.jsonl")
        saved = os.environ.get("FOREMAN_CLAUDE_PROJECTS")
        self.addCleanup(
            lambda: (
                os.environ.pop("FOREMAN_CLAUDE_PROJECTS", None)
                if saved is None
                else os.environ.__setitem__("FOREMAN_CLAUDE_PROJECTS", saved)
            )
        )
        os.environ["FOREMAN_CLAUDE_PROJECTS"] = str(self.tmp)
        self.assertEqual(
            [(i, a) for _, i, a in transcripts.find("ab")], [("abc123", False), ("abe789", True), ("abd456", False)]
        )
        self.assertEqual([i for _, i, _ in transcripts.find("agent-abe")], ["abe789"])
        self.assertEqual([i for _, i, _ in transcripts.find("abc")], ["abc123"])
        for bad in ("", "*", "../x", None, ".a"):
            self.assertEqual(transcripts.find(bad), [])


if __name__ == "__main__":
    unittest.main()
