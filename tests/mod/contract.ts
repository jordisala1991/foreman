// The cases foreman's hooks module (hooks/mod/foreman.ts) and its Python side (lib/foreman/hooks_mod.py) agree on.
// Kept as `export default` followed by plain JSON (no other code): `claude plugin test` cannot import a .json file,
// and the Python side reads this file by dropping these comment lines and `export default`. The selftest's static
// checks fail when that does not parse. Each case:
//   op      the `foreman hook mod OP` the module calls
//   engine  what the engine answers the module (the event's cwd, $.session.id(), $.session.version()); for
//           `measure`, `tick`, `answer` and `end`, also `start` (what Python answered the session's `hook mod start`),
//           for `start` optionally `previous` (the id the session ran under before a /clear or a resume: the module
//           starts under it, and its first tick under `session` starts again), for `tick` optionally `agents`
//           ($.agent.list(), [] when absent), for `renewed` what the module's $.command.run of `compact` settled
//           with (`text` it resolved with; `reject`: the error it rejected with), for `measure` the session.measure
//           event (`measure`), and for `answer` the turn.complete event's `turnId` (`turn`) and `answer`; for
//           `grant`, the main-loop call: a Bash one (`command`), a file tool's (`tool`, `file_path`) or any other
//           tool's (`tool` and its arguments, `input`), with `tool_use_id` and `agentId` for a subagent's;
//           $.session.surfaces() (`surfaces`), and for a case of the answer phase what the check answered before
//           (`check`, a stdout) and how the question ended (`ask`: {answer} the label chosen,
//           {reject} the error $.ui.ask rejected with, {timeout: true} no answer within the check's `ask_ms`), and
//           what $.ui.notify of the check's `notice` resolved with (`notify`: {isSent, channel} or {isSent, reason};
//           {reject} the error it rejected with; absent: {isSent: false, reason: "no-surface"}, as with no terminal)
//   stdin   the JSON the module must send to Python (<root> is the plugin root, <project> an active project's home
//           and <elsewhere> a folder outside any project: the Python test replaces them), or null: no call at all
//   run     what $.process.run answers the module: {exitCode, stdout, stderr}, {reject} for a call that fails, or
//           null when there is no call. For `tick` the module applies it: a `submit` is one $.prompt.submit of that
//           text, a `log` one $.ui.log line; `notices` and `version` go to the model as `context` with the next own
//           tool result or prompt, once, and `version_owner` to $.ui.log then; a `renew` is one $.command.run of
//           `compact` with the `instructions` as its args after the `log` (tests/mod/renew.test.ts). For `renewed`
//           and `measure`, a `log` is one $.ui.log line.
//           For `answer`, a `reminder` goes as `context` with the next prompt a person types. For `grant`, a
//           check's `question` is one $.ui.ask (unless `live` with a grant the module heard given), with its
//           `notice` one $.ui.notify (titled `header`) whose outcome the answer carries as `notified`, and its `deny`
//           refuses the call; an answer's `granted` lets the call on, its `deny` refuses it, its `log` is one $.ui.log
//           line; a call that fails lets the call on (the classic guard decides)
//   status  the status line the module must show (null: none)
//   python  whether the Python side must answer `run.stdout` to `stdin` (tests/test_golden.py, GoldenModContract):
//           on the world its MOD_WORLDS builds for the case's name (an orchestrator marked, a pass just run, an
//           unread message, an open renewal, an installed newer foreman…), byte for byte once both are
//           normalised as the golden files are (a time, a duration, a renewal's id: the case keeps a concrete one).
//           false only where Python has no answer to give (no call, a failure, an answer of the wrong shape) and
//           for the two band cases (GoldenModBand checks the band in both languages). The `grant` cases need a
//           simulated `ps` (the mark's own process): tests/test_grants.py ContractTest checks them, on a fresh state
//           with the session marked and the world its CONTRACT_WORLDS builds, times aside
export default [
  {
    "name": "an active project",
    "op": "start",
    "python": true,
    "engine": {
      "cwd": "<project>",
      "session": "S-active",
      "version": "2.1.314"
    },
    "stdin": {
      "cwd": "<project>",
      "session": "S-active",
      "claude_code": "2.1.314",
      "root": "<root>",
      "interactive": true
    },
    "run": {
      "exitCode": 0,
      "stdout": "{\"active\": true, \"below\": false, \"min\": \"2.1.299\"}\n",
      "stderr": ""
    },
    "status": null
  },
  {
    "name": "a new conversation in the same process (/clear, resume): the old id goes as previous",
    "op": "start",
    "python": true,
    "engine": {
      "cwd": "<project>",
      "session": "S-cleared",
      "version": "2.1.314",
      "previous": "S-active"
    },
    "stdin": {
      "cwd": "<project>",
      "session": "S-cleared",
      "claude_code": "2.1.314",
      "root": "<root>",
      "interactive": true,
      "previous": "S-active"
    },
    "run": {
      "exitCode": 0,
      "stdout": "{\"active\": true, \"below\": false, \"min\": \"2.1.299\"}\n",
      "stderr": ""
    },
    "status": null
  },
  {
    "name": "outside any project",
    "op": "start",
    "python": true,
    "engine": {
      "cwd": "<elsewhere>",
      "session": "S-elsewhere",
      "version": "2.1.314"
    },
    "stdin": {
      "cwd": "<elsewhere>",
      "session": "S-elsewhere",
      "claude_code": "2.1.314",
      "root": "<root>",
      "interactive": true
    },
    "run": {
      "exitCode": 0,
      "stdout": "{\"active\": false}\n",
      "stderr": ""
    },
    "status": null
  },
  {
    "name": "below the minimum Claude Code",
    "op": "start",
    "python": true,
    "engine": {
      "cwd": "<project>",
      "session": "S-old",
      "version": "2.1.200"
    },
    "stdin": {
      "cwd": "<project>",
      "session": "S-old",
      "claude_code": "2.1.200",
      "root": "<root>",
      "interactive": true
    },
    "run": {
      "exitCode": 0,
      "stdout": "{\"active\": true, \"below\": true, \"min\": \"2.1.299\", \"status\": \"foreman needs Claude Code ≥ 2.1.299 but this session runs 2.1.200: run `claude update`.\"}\n",
      "stderr": ""
    },
    "status": "foreman needs Claude Code ≥ 2.1.299 but this session runs 2.1.200: run `claude update`."
  },
  {
    "name": "Python exits with an error",
    "op": "start",
    "python": false,
    "engine": {
      "cwd": "<project>",
      "session": "S-broken",
      "version": "2.1.314"
    },
    "stdin": {
      "cwd": "<project>",
      "session": "S-broken",
      "claude_code": "2.1.314",
      "root": "<root>",
      "interactive": true
    },
    "run": {
      "exitCode": 1,
      "stdout": "",
      "stderr": "hook mod start: OSError: disk full\n"
    },
    "status": "foreman: hook mod start failed (exit 1: hook mod start: OSError: disk full)"
  },
  {
    "name": "Python cannot run",
    "op": "start",
    "python": false,
    "engine": {
      "cwd": "<project>",
      "session": "S-timeout",
      "version": "2.1.314"
    },
    "stdin": {
      "cwd": "<project>",
      "session": "S-timeout",
      "claude_code": "2.1.314",
      "root": "<root>",
      "interactive": true
    },
    "run": {
      "reject": "timed out after 30000 ms"
    },
    "status": "foreman: hook mod start failed ($.process.run: timed out after 30000 ms)"
  },
  {
    "name": "Python answers without active",
    "op": "start",
    "python": false,
    "engine": {
      "cwd": "<project>",
      "session": "S-odd",
      "version": "2.1.314"
    },
    "stdin": {
      "cwd": "<project>",
      "session": "S-odd",
      "claude_code": "2.1.314",
      "root": "<root>",
      "interactive": true
    },
    "run": {
      "exitCode": 0,
      "stdout": "{}\n",
      "stderr": ""
    },
    "status": "foreman: hook mod start failed (an answer without \"active\")"
  },
  {
    "name": "measure: the context moved",
    "op": "measure",
    "python": true,
    "engine": {
      "cwd": "<project>",
      "session": "S-measure",
      "version": "2.1.314",
      "start": "{\"active\": true, \"below\": false, \"min\": \"2.1.299\"}\n",
      "measure": {
        "context": {
          "tokens": 61000,
          "window": 200000,
          "percent": 31
        },
        "rateLimits": [
          {
            "kind": "five_hour",
            "percentUsed": 23.5,
            "resetsAt": "2020-01-15T15:00:00Z"
          },
          {
            "kind": "seven_day",
            "percentUsed": 7
          }
        ],
        "changed": [
          "context",
          "rateLimits",
          "cost"
        ],
        "cost": {
          "usd": 1.25
        }
      }
    },
    "stdin": {
      "cwd": "<project>",
      "session": "S-measure",
      "context": {
        "tokens": 61000,
        "window": 200000,
        "percent": 31
      },
      "rate_limits": [
        {
          "kind": "five_hour",
          "percentUsed": 23.5,
          "resetsAt": "2020-01-15T15:00:00Z"
        },
        {
          "kind": "seven_day",
          "percentUsed": 7
        }
      ],
      "cost": {
        "usd": 1.25
      }
    },
    "run": {
      "exitCode": 0,
      "stdout": "{\"stored\": true}\n",
      "stderr": ""
    },
    "status": null
  },
  {
    "name": "measure: a fresh window, no tokens yet and no cost ledger",
    "op": "measure",
    "python": true,
    "engine": {
      "cwd": "<project>",
      "session": "S-fresh",
      "version": "2.1.314",
      "start": "{\"active\": true, \"below\": false, \"min\": \"2.1.299\"}\n",
      "measure": {
        "context": {
          "window": 200000
        },
        "rateLimits": [],
        "changed": [
          "context"
        ]
      }
    },
    "stdin": {
      "cwd": "<project>",
      "session": "S-fresh",
      "context": {
        "window": 200000
      },
      "rate_limits": [],
      "cost": null
    },
    "run": {
      "exitCode": 0,
      "stdout": "{\"stored\": true}\n",
      "stderr": ""
    },
    "status": null
  },
  {
    "name": "measure: only the rate limits or the cost moved",
    "op": "measure",
    "python": false,
    "engine": {
      "cwd": "<project>",
      "session": "S-limits",
      "version": "2.1.314",
      "start": "{\"active\": true, \"below\": false, \"min\": \"2.1.299\"}\n",
      "measure": {
        "context": {
          "tokens": 61000,
          "window": 200000,
          "percent": 31
        },
        "rateLimits": [
          {
            "kind": "five_hour",
            "percentUsed": 23.5,
            "resetsAt": "2020-01-15T15:00:00Z"
          },
          {
            "kind": "seven_day",
            "percentUsed": 7
          }
        ],
        "changed": [
          "rateLimits",
          "cost"
        ],
        "cost": {
          "usd": 1.25
        }
      }
    },
    "stdin": null,
    "run": null,
    "status": null
  },
  {
    "name": "measure: outside any project (inert)",
    "op": "measure",
    "python": false,
    "engine": {
      "cwd": "<elsewhere>",
      "session": "S-elsewhere",
      "version": "2.1.314",
      "start": "{\"active\": false}\n",
      "measure": {
        "context": {
          "tokens": 61000,
          "window": 200000,
          "percent": 31
        },
        "rateLimits": [
          {
            "kind": "five_hour",
            "percentUsed": 23.5,
            "resetsAt": "2020-01-15T15:00:00Z"
          },
          {
            "kind": "seven_day",
            "percentUsed": 7
          }
        ],
        "changed": [
          "context"
        ],
        "cost": {
          "usd": 1.25
        }
      }
    },
    "stdin": null,
    "run": null,
    "status": null
  },
  {
    "name": "measure: below the minimum Claude Code (inert)",
    "op": "measure",
    "python": false,
    "engine": {
      "cwd": "<project>",
      "session": "S-old",
      "version": "2.1.314",
      "start": "{\"active\": true, \"below\": true, \"min\": \"2.1.299\", \"status\": \"old\"}\n",
      "measure": {
        "context": {
          "tokens": 61000,
          "window": 200000,
          "percent": 31
        },
        "rateLimits": [
          {
            "kind": "five_hour",
            "percentUsed": 23.5,
            "resetsAt": "2020-01-15T15:00:00Z"
          },
          {
            "kind": "seven_day",
            "percentUsed": 7
          }
        ],
        "changed": [
          "context"
        ],
        "cost": {
          "usd": 1.25
        }
      }
    },
    "stdin": null,
    "run": null,
    "status": null
  },
  {
    "name": "measure: Python exits with an error",
    "op": "measure",
    "python": false,
    "engine": {
      "cwd": "<project>",
      "session": "S-broken",
      "version": "2.1.314",
      "start": "{\"active\": true, \"below\": false, \"min\": \"2.1.299\"}\n",
      "measure": {
        "context": {
          "tokens": 61000,
          "window": 200000,
          "percent": 31
        },
        "rateLimits": [
          {
            "kind": "five_hour",
            "percentUsed": 23.5,
            "resetsAt": "2020-01-15T15:00:00Z"
          },
          {
            "kind": "seven_day",
            "percentUsed": 7
          }
        ],
        "changed": [
          "context"
        ],
        "cost": {
          "usd": 1.25
        }
      }
    },
    "stdin": {
      "cwd": "<project>",
      "session": "S-broken",
      "context": {
        "tokens": 61000,
        "window": 200000,
        "percent": 31
      },
      "rate_limits": [
        {
          "kind": "five_hour",
          "percentUsed": 23.5,
          "resetsAt": "2020-01-15T15:00:00Z"
        },
        {
          "kind": "seven_day",
          "percentUsed": 7
        }
      ],
      "cost": {
        "usd": 1.25
      }
    },
    "run": {
      "exitCode": 1,
      "stdout": "",
      "stderr": "hook mod measure: OSError: disk full\n"
    },
    "status": "foreman: hook mod measure failed (exit 1: hook mod measure: OSError: disk full)"
  },
  {
    "name": "measure: Python answers without stored",
    "op": "measure",
    "python": false,
    "engine": {
      "cwd": "<project>",
      "session": "S-odd",
      "version": "2.1.314",
      "start": "{\"active\": true, \"below\": false, \"min\": \"2.1.299\"}\n",
      "measure": {
        "context": {
          "tokens": 61000,
          "window": 200000,
          "percent": 31
        },
        "rateLimits": [
          {
            "kind": "five_hour",
            "percentUsed": 23.5,
            "resetsAt": "2020-01-15T15:00:00Z"
          },
          {
            "kind": "seven_day",
            "percentUsed": 7
          }
        ],
        "changed": [
          "context"
        ],
        "cost": {
          "usd": 1.25
        }
      }
    },
    "stdin": {
      "cwd": "<project>",
      "session": "S-odd",
      "context": {
        "tokens": 61000,
        "window": 200000,
        "percent": 31
      },
      "rate_limits": [
        {
          "kind": "five_hour",
          "percentUsed": 23.5,
          "resetsAt": "2020-01-15T15:00:00Z"
        },
        {
          "kind": "seven_day",
          "percentUsed": 7
        }
      ],
      "cost": {
        "usd": 1.25
      }
    },
    "run": {
      "exitCode": 0,
      "stdout": "{}\n",
      "stderr": ""
    },
    "status": "foreman: hook mod measure failed (an answer without \"stored\")"
  },
  {
    "name": "a pass due with something to act on",
    "op": "tick",
    "python": true,
    "engine": {
      "cwd": "<project>",
      "session": "S-orch",
      "version": "2.1.314",
      "start": "{\"active\": true, \"below\": false, \"min\": \"2.1.299\"}\n"
    },
    "stdin": {
      "cwd": "<project>",
      "session": "S-orch",
      "busy": false,
      "claude_code": "2.1.314",
      "agents": []
    },
    "run": {
      "exitCode": 0,
      "stdout": "{\"band\": {\"level\": \"ok\", \"rows\": [[{\"prio\": 0, \"role\": \"dot\", \"text\": \"●\"}, {\"prio\": 2, \"role\": \"plain\", \"text\": \"memory —\"}, {\"prio\": 4, \"role\": \"plain\", \"text\": \"next pass 11:00\"}, {\"prio\": 7, \"role\": \"dim\", \"text\": \"foreman\"}], [{\"prio\": 1, \"role\": \"dim\", \"text\": \"nothing running\"}]]}, \"due\": true, \"grants\": false, \"marked\": true, \"submit\": \"harbor orchestrator loop\\n\\nLoop pass 10:00 (heartbeat written, 0.4 s). Act on each line (those marked (info) only inform):\\n- (info) WIP (pipeline not full): production 0/2: no unblocked `ready` issue to launch.\\n- (info) WIP (pipeline not full): unblocked ready 0/1 (refining 0): nothing to plan (only triage, blocked or ready issues).\\n- Inbox: 1 unread from Sam (ids 1): `foreman inbox new`, answer and mark them read.\"}\n",
      "stderr": ""
    },
    "status": null
  },
  {
    "name": "an idle pass",
    "op": "tick",
    "python": true,
    "engine": {
      "cwd": "<project>",
      "session": "S-orch",
      "version": "2.1.314",
      "start": "{\"active\": true, \"below\": false, \"min\": \"2.1.299\"}\n"
    },
    "stdin": {
      "cwd": "<project>",
      "session": "S-orch",
      "busy": false,
      "claude_code": "2.1.314",
      "agents": []
    },
    "run": {
      "exitCode": 0,
      "stdout": "{\"band\": {\"level\": \"ok\", \"rows\": [[{\"prio\": 0, \"role\": \"dot\", \"text\": \"●\"}, {\"prio\": 2, \"role\": \"plain\", \"text\": \"memory —\"}, {\"prio\": 4, \"role\": \"plain\", \"text\": \"next pass 11:00\"}, {\"prio\": 7, \"role\": \"dim\", \"text\": \"foreman\"}], [{\"prio\": 1, \"role\": \"dim\", \"text\": \"nothing running\"}]]}, \"due\": true, \"grants\": false, \"log\": \"Loop pass 10:00: nothing to do (skipped without calling the model). For information: WIP (pipeline not full): production 0/2: no unblocked `ready` issue to launch.; WIP (pipeline not full): unblocked ready 0/1 (refining 0): nothing to plan (only triage, blocked or ready issues).\", \"marked\": true}\n",
      "stderr": ""
    },
    "status": null
  },
  {
    "name": "no pass due",
    "op": "tick",
    "python": true,
    "engine": {
      "cwd": "<project>",
      "session": "S-orch",
      "version": "2.1.314",
      "start": "{\"active\": true, \"below\": false, \"min\": \"2.1.299\"}\n"
    },
    "stdin": {
      "cwd": "<project>",
      "session": "S-orch",
      "busy": false,
      "claude_code": "2.1.314",
      "agents": []
    },
    "run": {
      "exitCode": 0,
      "stdout": "{\"band\": {\"level\": \"ok\", \"rows\": [[{\"prio\": 0, \"role\": \"dot\", \"text\": \"●\"}, {\"prio\": 2, \"role\": \"plain\", \"text\": \"memory —\"}, {\"prio\": 4, \"role\": \"plain\", \"text\": \"next pass 11:00\"}, {\"prio\": 7, \"role\": \"dim\", \"text\": \"foreman\"}], [{\"prio\": 1, \"role\": \"dim\", \"text\": \"nothing running\"}]]}, \"due\": false, \"grants\": false, \"marked\": true}\n",
      "stderr": ""
    },
    "status": null
  },
  {
    "name": "a session that is not the marked one",
    "op": "tick",
    "python": true,
    "engine": {
      "cwd": "<project>",
      "session": "S-other",
      "version": "2.1.314",
      "start": "{\"active\": true, \"below\": false, \"min\": \"2.1.299\"}\n"
    },
    "stdin": {
      "cwd": "<project>",
      "session": "S-other",
      "busy": false,
      "claude_code": "2.1.314",
      "agents": []
    },
    "run": {
      "exitCode": 0,
      "stdout": "{\"grants\": false, \"marked\": false}\n",
      "stderr": ""
    },
    "status": null
  },
  {
    "name": "outside any project",
    "op": "tick",
    "python": true,
    "engine": {
      "cwd": "<elsewhere>",
      "session": "S-elsewhere",
      "version": "2.1.314",
      "start": "{\"active\": true, \"below\": false, \"min\": \"2.1.299\"}\n"
    },
    "stdin": {
      "cwd": "<elsewhere>",
      "session": "S-elsewhere",
      "busy": false,
      "claude_code": "2.1.314",
      "agents": []
    },
    "run": {
      "exitCode": 0,
      "stdout": "{\"marked\": false}\n",
      "stderr": ""
    },
    "status": null
  },
  {
    "name": "the timer of a session outside any project never starts",
    "op": "tick",
    "python": false,
    "engine": {
      "cwd": "<elsewhere>",
      "session": "S-elsewhere",
      "version": "2.1.314",
      "start": "{\"active\": false}\n"
    },
    "stdin": null,
    "run": null,
    "status": null
  },
  {
    "name": "the timer below the minimum Claude Code never starts",
    "op": "tick",
    "python": false,
    "engine": {
      "cwd": "<project>",
      "session": "S-old",
      "version": "2.1.314",
      "start": "{\"active\": true, \"below\": true, \"min\": \"2.1.299\", \"status\": \"old\"}\n"
    },
    "stdin": null,
    "run": null,
    "status": null
  },
  {
    "name": "Python fails",
    "op": "tick",
    "python": false,
    "engine": {
      "cwd": "<project>",
      "session": "S-orch",
      "version": "2.1.314",
      "start": "{\"active\": true, \"below\": false, \"min\": \"2.1.299\"}\n"
    },
    "stdin": {
      "cwd": "<project>",
      "session": "S-orch",
      "busy": false,
      "claude_code": "2.1.314",
      "agents": []
    },
    "run": {
      "exitCode": 1,
      "stdout": "",
      "stderr": "hook mod tick: OSError: disk full\n"
    },
    "status": "foreman: hook mod tick failed (exit 1: hook mod tick: OSError: disk full)"
  },
  {
    "name": "an answer without \"marked\"",
    "op": "tick",
    "python": false,
    "engine": {
      "cwd": "<project>",
      "session": "S-orch",
      "version": "2.1.314",
      "start": "{\"active\": true, \"below\": false, \"min\": \"2.1.299\"}\n"
    },
    "stdin": {
      "cwd": "<project>",
      "session": "S-orch",
      "busy": false,
      "claude_code": "2.1.314",
      "agents": []
    },
    "run": {
      "exitCode": 0,
      "stdout": "{}\n",
      "stderr": ""
    },
    "status": "foreman: hook mod tick failed (an answer without \"marked\")"
  },
  {
    "name": "in-between notices with no pass due",
    "op": "tick",
    "python": true,
    "engine": {
      "cwd": "<project>",
      "session": "S-orch",
      "version": "2.1.314",
      "start": "{\"active\": true, \"below\": false, \"min\": \"2.1.299\"}\n"
    },
    "stdin": {
      "cwd": "<project>",
      "session": "S-orch",
      "busy": false,
      "claude_code": "2.1.314",
      "agents": []
    },
    "run": {
      "exitCode": 0,
      "stdout": "{\"band\": {\"level\": \"ok\", \"rows\": [[{\"prio\": 0, \"role\": \"dot\", \"text\": \"●\"}, {\"prio\": 2, \"role\": \"plain\", \"text\": \"memory —\"}, {\"prio\": 4, \"role\": \"plain\", \"text\": \"next pass 11:00\"}, {\"prio\": 7, \"role\": \"dim\", \"text\": \"foreman\"}], [{\"prio\": 1, \"role\": \"dim\", \"text\": \"nothing running\"}]]}, \"due\": false, \"grants\": false, \"marked\": true, \"notices\": \"Inbox: 1 unread from Sam: `foreman inbox new`, attend to them and mark them read.\"}\n",
      "stderr": ""
    },
    "status": null
  },
  {
    "name": "in-between notices and a version notice",
    "op": "tick",
    "python": true,
    "engine": {
      "cwd": "<project>",
      "session": "S-orch",
      "version": "2.1.314",
      "start": "{\"active\": true, \"below\": false, \"min\": \"2.1.299\"}\n"
    },
    "stdin": {
      "cwd": "<project>",
      "session": "S-orch",
      "busy": false,
      "claude_code": "2.1.314",
      "agents": []
    },
    "run": {
      "exitCode": 0,
      "stdout": "{\"band\": {\"level\": \"ok\", \"rows\": [[{\"prio\": 0, \"role\": \"dot\", \"text\": \"●\"}, {\"prio\": 2, \"role\": \"plain\", \"text\": \"memory —\"}, {\"prio\": 4, \"role\": \"plain\", \"text\": \"next pass 11:00\"}, {\"prio\": 7, \"role\": \"dim\", \"text\": \"foreman\"}], [{\"prio\": 1, \"role\": \"dim\", \"text\": \"nothing running\"}]]}, \"due\": false, \"grants\": false, \"marked\": true, \"notices\": \"Inbox: 1 unread from Sam: `foreman inbox new`, attend to them and mark them read.\", \"version\": \"This session runs foreman 0.1.0 (/cache/foreman/0.1.0); 0.2.0 is installed (/cache/foreman/0.2.0). Claude Code keeps a session on the version it started with: its hooks and the `foreman` on its PATH stay 0.1.0 until Sam types `/reload-plugins` in this session (only they can) or resumes it. Tell Sam once. Until then, what changed is in /cache/foreman/0.2.0/CHANGELOG.md (0.1.0 → 0.2.0): a config key, a lock or a check added since may behave here as in 0.1.0.\", \"version_owner\": \"foreman 0.1.0 in this session, 0.2.0 installed: type /reload-plugins\"}\n",
      "stderr": ""
    },
    "status": null
  },
  {
    "name": "a version notice in a session that is not the marked one",
    "op": "tick",
    "python": true,
    "engine": {
      "cwd": "<project>",
      "session": "S-other",
      "version": "2.1.314",
      "start": "{\"active\": true, \"below\": false, \"min\": \"2.1.299\"}\n"
    },
    "stdin": {
      "cwd": "<project>",
      "session": "S-other",
      "busy": false,
      "claude_code": "2.1.314",
      "agents": []
    },
    "run": {
      "exitCode": 0,
      "stdout": "{\"grants\": false, \"marked\": false, \"version\": \"This session runs foreman 0.1.0 (/cache/foreman/0.1.0); 0.2.0 is installed (/cache/foreman/0.2.0). Claude Code keeps a session on the version it started with: its hooks and the `foreman` on its PATH stay 0.1.0 until Sam types `/reload-plugins` in this session (only they can) or resumes it. Tell Sam once. Until then, what changed is in /cache/foreman/0.2.0/CHANGELOG.md (0.1.0 → 0.2.0): a config key, a lock or a check added since may behave here as in 0.1.0.\", \"version_owner\": \"foreman 0.1.0 in this session, 0.2.0 installed: type /reload-plugins\"}\n",
      "stderr": ""
    },
    "status": null
  },
  {
    "name": "the session's subagents, as $.agent.list() gives them",
    "op": "tick",
    "python": true,
    "engine": {
      "cwd": "<project>",
      "session": "S-orch",
      "version": "2.1.314",
      "start": "{\"active\": true, \"below\": false, \"min\": \"2.1.299\"}\n",
      "agents": [
        {
          "id": "a1",
          "description": "fix the parser",
          "type": "foreman:dev",
          "status": "running",
          "name": "hb7t1"
        },
        {
          "id": "a2",
          "description": "read the docs",
          "type": "Explore",
          "status": "killed"
        }
      ]
    },
    "stdin": {
      "cwd": "<project>",
      "session": "S-orch",
      "busy": false,
      "claude_code": "2.1.314",
      "agents": [
        {
          "id": "a1",
          "status": "running"
        },
        {
          "id": "a2",
          "status": "killed"
        }
      ]
    },
    "run": {
      "exitCode": 0,
      "stdout": "{\"band\": {\"level\": \"ok\", \"rows\": [[{\"prio\": 0, \"role\": \"dot\", \"text\": \"●\"}, {\"prio\": 2, \"role\": \"plain\", \"text\": \"memory —\"}, {\"prio\": 4, \"role\": \"plain\", \"text\": \"next pass 11:00\"}, {\"prio\": 7, \"role\": \"dim\", \"text\": \"foreman\"}], [{\"prio\": 1, \"role\": \"dim\", \"text\": \"nothing running\"}]]}, \"due\": false, \"grants\": false, \"marked\": true}\n",
      "stderr": ""
    },
    "status": null
  },
  {
    "name": "a held tick: the heartbeat only, even with a pass due",
    "op": "tick",
    "python": true,
    "engine": {
      "cwd": "<project>",
      "session": "S-orch",
      "version": "2.1.314",
      "start": "{\"active\": true, \"below\": false, \"min\": \"2.1.299\"}\n"
    },
    "stdin": {
      "cwd": "<project>",
      "session": "S-orch",
      "busy": false,
      "claude_code": "2.1.314",
      "hold": true,
      "agents": []
    },
    "run": {
      "exitCode": 0,
      "stdout": "{\"grants\": false, \"marked\": true}\n",
      "stderr": ""
    },
    "status": null
  },
  {
    "name": "the band of the marked session",
    "op": "tick",
    "python": false,
    "engine": {
      "cwd": "<project>",
      "session": "S-orch",
      "version": "2.1.314",
      "start": "{\"active\": true, \"below\": false, \"min\": \"2.1.299\"}\n"
    },
    "stdin": {
      "cwd": "<project>",
      "session": "S-orch",
      "busy": false,
      "claude_code": "2.1.314",
      "agents": []
    },
    "run": {
      "exitCode": 0,
      "stdout": "{\"band\": {\"level\": \"ask\", \"rows\": [[{\"prio\": 0, \"role\": \"dot\", \"text\": \"●\"}, {\"prio\": 0, \"role\": \"ask\", \"text\": \"2 questions for you\"}, {\"prio\": 2, \"role\": \"plain\", \"text\": \"memory 84K of 200K\"}, {\"prio\": 3, \"role\": \"plain\", \"text\": \"to integrate 1\"}, {\"prio\": 4, \"role\": \"plain\", \"text\": \"next pass 11:05\"}, {\"prio\": 7, \"role\": \"dim\", \"text\": \"foreman\"}], [{\"prio\": 1, \"role\": \"dim\", \"text\": \"Light the harbor (HB-7 T1) 23 min\"}, {\"prio\": 2, \"role\": \"dim\", \"text\": \"Rebuild the jetty (HB-9 T2) 4 min\"}, {\"prio\": 3, \"role\": \"dim\", \"text\": \"Paint the pier (HB-11) 2 min\"}, {\"n\": 1, \"prio\": 0, \"role\": \"more\", \"text\": \"+{n} more\"}]]}, \"due\": false, \"grants\": false, \"marked\": true}\n",
      "stderr": ""
    },
    "status": null
  },
  {
    "name": "an idle pass and the band, in Spanish",
    "op": "tick",
    "python": false,
    "engine": {
      "cwd": "<project>",
      "session": "S-orch",
      "version": "2.1.314",
      "start": "{\"active\": true, \"below\": false, \"min\": \"2.1.299\"}\n"
    },
    "stdin": {
      "cwd": "<project>",
      "session": "S-orch",
      "busy": false,
      "claude_code": "2.1.314",
      "agents": []
    },
    "run": {
      "exitCode": 0,
      "stdout": "{\"band\": {\"level\": \"ok\", \"rows\": [[{\"prio\": 0, \"role\": \"dot\", \"text\": \"●\"}, {\"prio\": 2, \"role\": \"plain\", \"text\": \"memoria —\"}, {\"prio\": 4, \"role\": \"plain\", \"text\": \"próxima pasada ya\"}, {\"prio\": 7, \"role\": \"dim\", \"text\": \"foreman\"}], [{\"prio\": 1, \"role\": \"dim\", \"text\": \"nada en marcha\"}]]}, \"due\": true, \"grants\": false, \"log\": \"Pasada del bucle 10:05: nada que hacer (omitida sin llamar al modelo).\", \"marked\": true}\n",
      "stderr": ""
    },
    "status": null
  },
  {
    "name": "a flagged answer of the marked session",
    "op": "answer",
    "python": true,
    "engine": {
      "cwd": "<project>",
      "session": "S-orch",
      "version": "2.1.314",
      "start": "{\"active\": true, \"below\": false, \"min\": \"2.1.299\"}\n",
      "turn": "turn-1",
      "answer": "Antes de seguir necesito tus respuestas a J1 y J2."
    },
    "stdin": {
      "cwd": "<project>",
      "session": "S-orch",
      "turn": "turn-1",
      "answer": "Antes de seguir necesito tus respuestas a J1 y J2."
    },
    "run": {
      "exitCode": 0,
      "stdout": "{\"reminder\": \"Your last message to the owner named without explaining: J1, J2. If you write about any of them again, say what it is (docs/owner.md); no need to resend that message.\", \"reviewed\": true}\n",
      "stderr": ""
    },
    "status": null
  },
  {
    "name": "a clean answer of the marked session",
    "op": "answer",
    "python": true,
    "engine": {
      "cwd": "<project>",
      "session": "S-orch",
      "version": "2.1.314",
      "start": "{\"active\": true, \"below\": false, \"min\": \"2.1.299\"}\n",
      "turn": "turn-1",
      "answer": "Dos preguntas te esperan en el panel."
    },
    "stdin": {
      "cwd": "<project>",
      "session": "S-orch",
      "turn": "turn-1",
      "answer": "Dos preguntas te esperan en el panel."
    },
    "run": {
      "exitCode": 0,
      "stdout": "{\"reviewed\": true}\n",
      "stderr": ""
    },
    "status": null
  },
  {
    "name": "an answer in a session that is not the marked one",
    "op": "answer",
    "python": true,
    "engine": {
      "cwd": "<project>",
      "session": "S-other",
      "version": "2.1.314",
      "start": "{\"active\": true, \"below\": false, \"min\": \"2.1.299\"}\n",
      "turn": "turn-1",
      "answer": "Antes de seguir necesito tus respuestas a J1 y J2."
    },
    "stdin": {
      "cwd": "<project>",
      "session": "S-other",
      "turn": "turn-1",
      "answer": "Antes de seguir necesito tus respuestas a J1 y J2."
    },
    "run": {
      "exitCode": 0,
      "stdout": "{\"reviewed\": false}\n",
      "stderr": ""
    },
    "status": null
  },
  {
    "name": "an answer outside any project",
    "op": "answer",
    "python": true,
    "engine": {
      "cwd": "<elsewhere>",
      "session": "S-elsewhere",
      "version": "2.1.314",
      "start": "{\"active\": true, \"below\": false, \"min\": \"2.1.299\"}\n",
      "turn": "turn-1",
      "answer": "Antes de seguir necesito tus respuestas a J1 y J2."
    },
    "stdin": {
      "cwd": "<elsewhere>",
      "session": "S-elsewhere",
      "turn": "turn-1",
      "answer": "Antes de seguir necesito tus respuestas a J1 y J2."
    },
    "run": {
      "exitCode": 0,
      "stdout": "{\"reviewed\": false}\n",
      "stderr": ""
    },
    "status": null
  },
  {
    "name": "an empty answer asks nothing",
    "op": "answer",
    "python": false,
    "engine": {
      "cwd": "<project>",
      "session": "S-orch",
      "version": "2.1.314",
      "start": "{\"active\": true, \"below\": false, \"min\": \"2.1.299\"}\n",
      "turn": "turn-1",
      "answer": "  "
    },
    "stdin": null,
    "run": null,
    "status": null
  },
  {
    "name": "a session outside any project asks nothing",
    "op": "answer",
    "python": false,
    "engine": {
      "cwd": "<elsewhere>",
      "session": "S-elsewhere",
      "version": "2.1.314",
      "start": "{\"active\": false}\n",
      "turn": "turn-1",
      "answer": "Antes de seguir necesito tus respuestas a J1 y J2."
    },
    "stdin": null,
    "run": null,
    "status": null
  },
  {
    "name": "the review fails",
    "op": "answer",
    "python": false,
    "engine": {
      "cwd": "<project>",
      "session": "S-orch",
      "version": "2.1.314",
      "start": "{\"active\": true, \"below\": false, \"min\": \"2.1.299\"}\n",
      "turn": "turn-1",
      "answer": "Antes de seguir necesito tus respuestas a J1 y J2."
    },
    "stdin": {
      "cwd": "<project>",
      "session": "S-orch",
      "turn": "turn-1",
      "answer": "Antes de seguir necesito tus respuestas a J1 y J2."
    },
    "run": {
      "exitCode": 1,
      "stdout": "",
      "stderr": "hook mod answer: OSError: disk full\n"
    },
    "status": "foreman: hook mod answer failed (exit 1: hook mod answer: OSError: disk full)"
  },
  {
    "name": "an answer without \"reviewed\"",
    "op": "answer",
    "python": false,
    "engine": {
      "cwd": "<project>",
      "session": "S-orch",
      "version": "2.1.314",
      "start": "{\"active\": true, \"below\": false, \"min\": \"2.1.299\"}\n",
      "turn": "turn-1",
      "answer": "Antes de seguir necesito tus respuestas a J1 y J2."
    },
    "stdin": {
      "cwd": "<project>",
      "session": "S-orch",
      "turn": "turn-1",
      "answer": "Antes de seguir necesito tus respuestas a J1 y J2."
    },
    "run": {
      "exitCode": 0,
      "stdout": "{}\n",
      "stderr": ""
    },
    "status": "foreman: hook mod answer failed (an answer without \"reviewed\")"
  },
  {
    "name": "the session ends in an active project",
    "op": "end",
    "python": true,
    "engine": {
      "cwd": "<project>",
      "session": "S-orch",
      "version": "2.1.314",
      "start": "{\"active\": true, \"below\": false, \"min\": \"2.1.299\"}\n"
    },
    "stdin": {
      "cwd": "<project>",
      "session": "S-orch"
    },
    "run": {
      "exitCode": 0,
      "stdout": "{\"ended\": true}\n",
      "stderr": ""
    },
    "status": null
  },
  {
    "name": "a session with no heartbeat ends",
    "op": "end",
    "python": true,
    "engine": {
      "cwd": "<project>",
      "session": "S-never",
      "version": "2.1.314",
      "start": "{\"active\": true, \"below\": false, \"min\": \"2.1.299\"}\n"
    },
    "stdin": {
      "cwd": "<project>",
      "session": "S-never"
    },
    "run": {
      "exitCode": 0,
      "stdout": "{\"ended\": false}\n",
      "stderr": ""
    },
    "status": null
  },
  {
    "name": "a session outside any project ends",
    "op": "end",
    "python": true,
    "engine": {
      "cwd": "<elsewhere>",
      "session": "S-elsewhere",
      "version": "2.1.314",
      "start": "{\"active\": true, \"below\": false, \"min\": \"2.1.299\"}\n"
    },
    "stdin": {
      "cwd": "<elsewhere>",
      "session": "S-elsewhere"
    },
    "run": {
      "exitCode": 0,
      "stdout": "{\"ended\": false}\n",
      "stderr": ""
    },
    "status": null
  },
  {
    "name": "an inert session ends: no call",
    "op": "end",
    "python": false,
    "engine": {
      "cwd": "<elsewhere>",
      "session": "S-elsewhere",
      "version": "2.1.314",
      "start": "{\"active\": false}\n"
    },
    "stdin": null,
    "run": null,
    "status": null
  },
  {
    "name": "Python fails at the end: nothing shown",
    "op": "end",
    "python": false,
    "engine": {
      "cwd": "<project>",
      "session": "S-orch",
      "version": "2.1.314",
      "start": "{\"active\": true, \"below\": false, \"min\": \"2.1.299\"}\n"
    },
    "stdin": {
      "cwd": "<project>",
      "session": "S-orch"
    },
    "run": {
      "exitCode": 1,
      "stdout": "",
      "stderr": "hook mod end: OSError: disk full\n"
    },
    "status": null
  },
  {
    "name": "status in an active project with no orchestrator marked",
    "op": "status",
    "python": true,
    "engine": {
      "cwd": "<project>",
      "session": "S-other",
      "version": "2.1.314",
      "start": "{\"active\": true, \"below\": false, \"min\": \"2.1.299\"}\n"
    },
    "stdin": {
      "cwd": "<project>",
      "session": "S-other"
    },
    "run": {
      "exitCode": 0,
      "stdout": "{\"text\": \"needs attention · no orchestrator session marked · memory — · no pass yet · foreman\\nnothing running\"}\n",
      "stderr": ""
    },
    "status": null
  },
  {
    "name": "status outside any project",
    "op": "status",
    "python": true,
    "engine": {
      "cwd": "<elsewhere>",
      "session": "S-elsewhere",
      "version": "2.1.314",
      "start": "{\"active\": false}\n"
    },
    "stdin": {
      "cwd": "<elsewhere>",
      "session": "S-elsewhere"
    },
    "run": {
      "exitCode": 0,
      "stdout": "{\"text\": \"foreman: this folder is not in an active foreman project.\"}\n",
      "stderr": ""
    },
    "status": null
  },
  {
    "name": "status: Python fails",
    "op": "status",
    "python": false,
    "engine": {
      "cwd": "<project>",
      "session": "S-orch",
      "version": "2.1.314",
      "start": "{\"active\": true, \"below\": false, \"min\": \"2.1.299\"}\n"
    },
    "stdin": {
      "cwd": "<project>",
      "session": "S-orch"
    },
    "run": {
      "exitCode": 1,
      "stdout": "",
      "stderr": "hook mod status: OSError: disk gone\n"
    },
    "status": null
  },
  {
    "name": "status: an answer without \"text\"",
    "op": "status",
    "python": false,
    "engine": {
      "cwd": "<project>",
      "session": "S-orch",
      "version": "2.1.314",
      "start": "{\"active\": true, \"below\": false, \"min\": \"2.1.299\"}\n"
    },
    "stdin": {
      "cwd": "<project>",
      "session": "S-orch"
    },
    "run": {
      "exitCode": 0,
      "stdout": "{}\n",
      "stderr": ""
    },
    "status": null
  },
  {
    "name": "a version notice in the marked session, a terminal one",
    "op": "tick",
    "python": true,
    "engine": {
      "cwd": "<project>",
      "session": "S-orch",
      "version": "2.1.314",
      "start": "{\"active\": true, \"below\": false, \"min\": \"2.1.299\"}\n"
    },
    "stdin": {
      "cwd": "<project>",
      "session": "S-orch",
      "busy": false,
      "claude_code": "2.1.314",
      "agents": []
    },
    "run": {
      "exitCode": 0,
      "stdout": "{\"band\": {\"level\": \"ok\", \"rows\": [[{\"prio\": 0, \"role\": \"dot\", \"text\": \"●\"}, {\"prio\": 2, \"role\": \"plain\", \"text\": \"memory —\"}, {\"prio\": 4, \"role\": \"plain\", \"text\": \"next pass 11:00\"}, {\"prio\": 7, \"role\": \"dim\", \"text\": \"foreman\"}], [{\"prio\": 1, \"role\": \"dim\", \"text\": \"nothing running\"}]]}, \"due\": false, \"grants\": false, \"marked\": true, \"version\": \"This session runs foreman 0.35.0 (/cache/foreman/0.35.0); 9.9.0 is installed (/cache/foreman/9.9.0). Claude Code keeps a session on the version it started with: its hooks and the `foreman` on its PATH stay 0.35.0 until Sam types `/reload-plugins` in this session (only they can) or resumes it. Tell Sam once. Until then, what changed is in /cache/foreman/9.9.0/CHANGELOG.md (0.35.0 → 9.9.0): a config key, a lock or a check added since may behave here as in 0.35.0.\", \"version_owner\": \"foreman 0.35.0 in this session, 9.9.0 installed: type /reload-plugins\"}\n",
      "stderr": ""
    },
    "status": null
  },
  {
    "name": "a renewal due",
    "op": "tick",
    "python": true,
    "engine": {
      "cwd": "<project>",
      "session": "S-orch",
      "version": "2.1.314",
      "start": "{\"active\": true, \"below\": false, \"min\": \"2.1.299\"}\n"
    },
    "stdin": {
      "cwd": "<project>",
      "session": "S-orch",
      "busy": false,
      "claude_code": "2.1.314",
      "agents": []
    },
    "run": {
      "exitCode": 0,
      "stdout": "{\"band\": {\"level\": \"ok\", \"rows\": [[{\"prio\": 0, \"role\": \"dot\", \"text\": \"●\"}, {\"prio\": 2, \"role\": \"warn\", \"text\": \"memory 208K of 200K\"}, {\"prio\": 4, \"role\": \"plain\", \"text\": \"next pass 11:00\"}, {\"prio\": 7, \"role\": \"dim\", \"text\": \"foreman\"}], [{\"prio\": 1, \"role\": \"dim\", \"text\": \"nothing running\"}]]}, \"due\": false, \"grants\": false, \"instructions\": \"foreman renews the memory of harbor's orchestrator session in place. Right after this compaction foreman gives the session back its rules, board, active backlog, live agents, the owner's open decisions and the owner's last messages verbatim, so leave all that out. Keep this summary short (at most about 1,500 words) and keep only what none of those holds: what the owner asked in this conversation that is still open and not on the board, what was agreed with them, what you were in the middle of and your next step. No history of finished work, no tool output, no file contents.\", \"log\": \"foreman: renewing this session's memory in place (208K of 1000K used); the work goes on in a minute\", \"marked\": true, \"renew\": \"r1700000000\"}\n",
      "stderr": ""
    },
    "status": null
  },
  {
    "name": "a renewal call that settled",
    "op": "renewed",
    "python": true,
    "engine": {
      "cwd": "<project>",
      "session": "S-orch",
      "version": "2.1.314",
      "start": "{\"active\": true, \"below\": false, \"min\": \"2.1.299\"}\n",
      "text": ""
    },
    "stdin": {
      "cwd": "<project>",
      "session": "S-orch",
      "id": "r1700000000",
      "text": ""
    },
    "run": {
      "exitCode": 0,
      "stdout": "{\"recorded\": true}\n",
      "stderr": ""
    },
    "status": null
  },
  {
    "name": "a renewal call rejected",
    "op": "renewed",
    "python": true,
    "engine": {
      "cwd": "<project>",
      "session": "S-orch",
      "version": "2.1.314",
      "start": "{\"active\": true, \"below\": false, \"min\": \"2.1.299\"}\n",
      "reject": "no implementation for command.run"
    },
    "stdin": {
      "cwd": "<project>",
      "session": "S-orch",
      "id": "r1700000000",
      "text": "",
      "error": "no implementation for command.run"
    },
    "run": {
      "exitCode": 0,
      "stdout": "{\"log\": \"foreman: renewing this session's memory did not work (no implementation for command.run); foreman tries again in 30 min\", \"recorded\": true}\n",
      "stderr": ""
    },
    "status": null
  },
  {
    "name": "a renewal call that settled with no attempt open",
    "op": "renewed",
    "python": true,
    "engine": {
      "cwd": "<project>",
      "session": "S-orch",
      "version": "2.1.314",
      "start": "{\"active\": true, \"below\": false, \"min\": \"2.1.299\"}\n",
      "text": "Compacted."
    },
    "stdin": {
      "cwd": "<project>",
      "session": "S-orch",
      "id": "r1700000000",
      "text": "Compacted."
    },
    "run": {
      "exitCode": 0,
      "stdout": "{\"recorded\": false}\n",
      "stderr": ""
    },
    "status": null
  },
  {
    "name": "Python fails on a renewal call that settled",
    "op": "renewed",
    "python": false,
    "engine": {
      "cwd": "<project>",
      "session": "S-orch",
      "version": "2.1.314",
      "start": "{\"active\": true, \"below\": false, \"min\": \"2.1.299\"}\n",
      "text": ""
    },
    "stdin": {
      "cwd": "<project>",
      "session": "S-orch",
      "id": "r1700000000",
      "text": ""
    },
    "run": {
      "exitCode": 1,
      "stdout": "",
      "stderr": "hook mod renewed: OSError: disk full\n"
    },
    "status": "foreman: hook mod renewed failed (exit 1: hook mod renewed: OSError: disk full)"
  },
  {
    "name": "measure: the first after a renewal",
    "op": "measure",
    "python": true,
    "engine": {
      "cwd": "<project>",
      "session": "S-orch",
      "version": "2.1.314",
      "start": "{\"active\": true, \"below\": false, \"min\": \"2.1.299\"}\n",
      "measure": {
        "context": {
          "tokens": 14000,
          "window": 1000000,
          "percent": 1
        },
        "rateLimits": [],
        "changed": [
          "context"
        ]
      }
    },
    "stdin": {
      "cwd": "<project>",
      "session": "S-orch",
      "context": {
        "tokens": 14000,
        "window": 1000000,
        "percent": 1
      },
      "rate_limits": [],
      "cost": null
    },
    "run": {
      "exitCode": 0,
      "stdout": "{\"log\": \"foreman: memory renewed in this session, 208K → 14K; the work goes on, nothing to do on your side\", \"stored\": true}\n",
      "stderr": ""
    },
    "status": null
  },
  {
    "name": "a group command with no grant: the owner is asked",
    "op": "grant",
    "python": true,
    "engine": {
      "cwd": "<project>",
      "session": "S-orch",
      "version": "2.1.314",
      "start": "{\"active\": true, \"below\": false, \"grants\": true, \"min\": \"2.1.299\"}\n",
      "command": "make deploy",
      "tool_use_id": "toolu_1",
      "surfaces": [
        "desktop"
      ]
    },
    "stdin": {
      "phase": "check",
      "cwd": "<project>",
      "session": "S-orch",
      "command": "make deploy",
      "tool_use_id": "toolu_1"
    },
    "run": {
      "exitCode": 0,
      "stdout": "{\"ask_ms\": 300000, \"group\": \"deploy\", \"header\": \"foreman\", \"live\": false, \"no\": \"No\", \"notice\": \"orq, the orchestrator of fake, asks for your yes to «deploy»: answer in the chat; with no answer in 5 min the request moves to foreman's panel.\", \"question\": \"orq, the orchestrator of fake, wants to run a command of the group «deploy»: `make deploy`. Do you let it run the commands of «deploy» for 30 minutes?\", \"waiting\": \"Blocked: a question about the group «deploy» is already waiting for Owner's answer. Do not try again until Owner says so in the chat.\", \"yes\": \"Yes, for 30 min\"}\n",
      "stderr": ""
    },
    "status": null
  },
  {
    "name": "a command the guard decides alone: no question",
    "op": "grant",
    "python": true,
    "engine": {
      "cwd": "<project>",
      "session": "S-orch",
      "version": "2.1.314",
      "start": "{\"active\": true, \"below\": false, \"grants\": true, \"min\": \"2.1.299\"}\n",
      "command": "ls",
      "tool_use_id": "toolu_1",
      "surfaces": [
        "desktop"
      ]
    },
    "stdin": {
      "phase": "check",
      "cwd": "<project>",
      "session": "S-orch",
      "command": "ls",
      "tool_use_id": "toolu_1"
    },
    "run": {
      "exitCode": 0,
      "stdout": "{}\n",
      "stderr": ""
    },
    "status": null
  },
  {
    "name": "any tool: a main-loop Read of a place a group covers: the owner is asked",
    "op": "grant",
    "python": true,
    "engine": {
      "cwd": "<project>",
      "session": "S-orch",
      "version": "2.1.314",
      "start": "{\"active\": true, \"below\": false, \"grants\": true, \"min\": \"2.1.299\"}\n",
      "tool": "Read",
      "input": {
        "file_path": "/srv/prod-api/config.json"
      },
      "tool_use_id": "toolu_1",
      "surfaces": [
        "desktop"
      ]
    },
    "stdin": {
      "phase": "check",
      "cwd": "<project>",
      "session": "S-orch",
      "tool": "Read",
      "input": {
        "file_path": "/srv/prod-api/config.json"
      },
      "tool_use_id": "toolu_1"
    },
    "run": {
      "exitCode": 0,
      "stdout": "{\"ask_ms\": 300000, \"group\": \"deploy\", \"header\": \"foreman\", \"live\": false, \"no\": \"No\", \"notice\": \"orq, the orchestrator of fake, asks for your yes to «deploy»: answer in the chat; with no answer in 5 min the request moves to foreman's panel.\", \"question\": \"orq, the orchestrator of fake, wants to use Read on `/srv/prod-api/config.json`, which the group «deploy» covers. Do you let it run the commands of «deploy» and reach what they name for 30 minutes?\", \"waiting\": \"Blocked: a question about the group «deploy» is already waiting for Owner's answer. Do not try again until Owner says so in the chat.\", \"yes\": \"Yes, for 30 min\"}\n",
      "stderr": ""
    },
    "status": null
  },
  {
    "name": "any tool: a main-loop Monitor running a group's command: the owner is asked",
    "op": "grant",
    "python": true,
    "engine": {
      "cwd": "<project>",
      "session": "S-orch",
      "version": "2.1.314",
      "start": "{\"active\": true, \"below\": false, \"grants\": true, \"min\": \"2.1.299\"}\n",
      "tool": "Monitor",
      "input": {
        "command": "make deploy",
        "description": "deploy"
      },
      "tool_use_id": "toolu_1",
      "surfaces": [
        "desktop"
      ]
    },
    "stdin": {
      "phase": "check",
      "cwd": "<project>",
      "session": "S-orch",
      "tool": "Monitor",
      "input": {
        "command": "make deploy",
        "description": "deploy"
      },
      "tool_use_id": "toolu_1"
    },
    "run": {
      "exitCode": 0,
      "stdout": "{\"ask_ms\": 300000, \"group\": \"deploy\", \"header\": \"foreman\", \"live\": false, \"no\": \"No\", \"notice\": \"orq, the orchestrator of fake, asks for your yes to «deploy»: answer in the chat; with no answer in 5 min the request moves to foreman's panel.\", \"question\": \"orq, the orchestrator of fake, wants to use Monitor on `make deploy`, which the group «deploy» covers. Do you let it run the commands of «deploy» and reach what they name for 30 minutes?\", \"waiting\": \"Blocked: a question about the group «deploy» is already waiting for Owner's answer. Do not try again until Owner says so in the chat.\", \"yes\": \"Yes, for 30 min\"}\n",
      "stderr": ""
    },
    "status": null
  },
  {
    "name": "any tool: a call whose free text only mentions a group's command: no question",
    "op": "grant",
    "python": true,
    "engine": {
      "cwd": "<project>",
      "session": "S-orch",
      "version": "2.1.314",
      "start": "{\"active\": true, \"below\": false, \"grants\": true, \"min\": \"2.1.299\"}\n",
      "tool": "WebFetch",
      "input": {
        "url": "https://example.com/docs",
        "prompt": "how to run make deploy"
      },
      "tool_use_id": "toolu_1",
      "surfaces": [
        "desktop"
      ]
    },
    "stdin": {
      "phase": "check",
      "cwd": "<project>",
      "session": "S-orch",
      "tool": "WebFetch",
      "input": {
        "url": "https://example.com/docs",
        "prompt": "how to run make deploy"
      },
      "tool_use_id": "toolu_1"
    },
    "run": {
      "exitCode": 0,
      "stdout": "{}\n",
      "stderr": ""
    },
    "status": null
  },
  {
    "name": "any tool: a project without groups: a Read is not checked",
    "op": "grant",
    "python": false,
    "engine": {
      "cwd": "<project>",
      "session": "S-orch",
      "version": "2.1.314",
      "start": "{\"active\": true, \"below\": false, \"min\": \"2.1.299\"}\n",
      "tool": "Read",
      "input": {
        "file_path": "/srv/prod-api/config.json"
      },
      "tool_use_id": "toolu_1",
      "surfaces": [
        "desktop"
      ]
    },
    "stdin": null,
    "run": null,
    "status": null
  },
  {
    "name": "any tool: the owner says yes to a Read",
    "op": "grant",
    "python": true,
    "engine": {
      "cwd": "<project>",
      "session": "S-orch",
      "version": "2.1.314",
      "start": "{\"active\": true, \"below\": false, \"grants\": true, \"min\": \"2.1.299\"}\n",
      "tool": "Read",
      "input": {
        "file_path": "/srv/prod-api/config.json"
      },
      "check": "{\"ask_ms\": 300000, \"group\": \"deploy\", \"header\": \"foreman\", \"live\": false, \"no\": \"No\", \"notice\": \"orq, the orchestrator of fake, asks for your yes to «deploy»: answer in the chat; with no answer in 5 min the request moves to foreman's panel.\", \"question\": \"orq, the orchestrator of fake, wants to use Read on `/srv/prod-api/config.json`, which the group «deploy» covers. Do you let it run the commands of «deploy» and reach what they name for 30 minutes?\", \"waiting\": \"Blocked: a question about the group «deploy» is already waiting for Owner's answer. Do not try again until Owner says so in the chat.\", \"yes\": \"Yes, for 30 min\"}\n",
      "ask": {
        "answer": "Yes, for 30 min"
      },
      "tool_use_id": "toolu_1",
      "surfaces": [
        "desktop"
      ]
    },
    "stdin": {
      "phase": "answer",
      "cwd": "<project>",
      "session": "S-orch",
      "tool": "Read",
      "input": {
        "file_path": "/srv/prod-api/config.json"
      },
      "tool_use_id": "toolu_1",
      "group": "deploy",
      "surfaces": [
        "desktop"
      ],
      "answer": "yes",
      "notified": "no:no-surface"
    },
    "run": {
      "exitCode": 0,
      "stdout": "{\"granted\": true, \"log\": \"«deploy» allowed for orq until 10:30\", \"until\": \"2026-11-01T10:10:30\"}\n",
      "stderr": ""
    },
    "status": null
  },
  {
    "name": "a live grant in grants.json the module has not heard given: asked again",
    "op": "grant",
    "python": true,
    "engine": {
      "cwd": "<project>",
      "session": "S-orch",
      "version": "2.1.314",
      "start": "{\"active\": true, \"below\": false, \"grants\": true, \"min\": \"2.1.299\"}\n",
      "command": "make deploy",
      "tool_use_id": "toolu_1",
      "surfaces": [
        "desktop"
      ],
      "ask": {
        "answer": "No"
      }
    },
    "stdin": {
      "phase": "check",
      "cwd": "<project>",
      "session": "S-orch",
      "command": "make deploy",
      "tool_use_id": "toolu_1"
    },
    "run": {
      "exitCode": 0,
      "stdout": "{\"ask_ms\": 300000, \"group\": \"deploy\", \"header\": \"foreman\", \"live\": true, \"no\": \"No\", \"notice\": \"orq, the orchestrator of fake, asks for your yes to «deploy»: answer in the chat; with no answer in 5 min the request moves to foreman's panel.\", \"question\": \"orq, the orchestrator of fake, wants to run a command of the group «deploy»: `make deploy`. Do you let it run the commands of «deploy» for 30 minutes?\", \"until\": \"2026-11-01T10:10:30\", \"waiting\": \"Blocked: a question about the group «deploy» is already waiting for Owner's answer. Do not try again until Owner says so in the chat.\", \"yes\": \"Yes, for 30 min\"}\n",
      "stderr": ""
    },
    "status": null
  },
  {
    "name": "a live grant the owner approved from the panel: no question",
    "op": "grant",
    "python": true,
    "engine": {
      "cwd": "<project>",
      "session": "S-orch",
      "version": "2.1.314",
      "start": "{\"active\": true, \"below\": false, \"grants\": true, \"min\": \"2.1.299\"}\n",
      "command": "make deploy",
      "tool_use_id": "toolu_1",
      "surfaces": [
        "desktop"
      ]
    },
    "stdin": {
      "phase": "check",
      "cwd": "<project>",
      "session": "S-orch",
      "command": "make deploy",
      "tool_use_id": "toolu_1"
    },
    "run": {
      "exitCode": 0,
      "stdout": "{}\n",
      "stderr": ""
    },
    "status": null
  },
  {
    "name": "the group cools down after the owner's no: refused, no question",
    "op": "grant",
    "python": true,
    "engine": {
      "cwd": "<project>",
      "session": "S-orch",
      "version": "2.1.314",
      "start": "{\"active\": true, \"below\": false, \"grants\": true, \"min\": \"2.1.299\"}\n",
      "command": "make deploy",
      "tool_use_id": "toolu_1",
      "surfaces": [
        "desktop"
      ]
    },
    "stdin": {
      "phase": "check",
      "cwd": "<project>",
      "session": "S-orch",
      "command": "make deploy",
      "tool_use_id": "toolu_1"
    },
    "run": {
      "exitCode": 0,
      "stdout": "{\"deny\": \"Blocked: Owner said no to the group «deploy» a moment ago; foreman will not ask again before 10:30. Do not try again until Owner says so in the chat.\", \"group\": \"deploy\"}\n",
      "stderr": ""
    },
    "status": null
  },
  {
    "name": "the owner says yes",
    "op": "grant",
    "python": true,
    "engine": {
      "cwd": "<project>",
      "session": "S-orch",
      "version": "2.1.314",
      "start": "{\"active\": true, \"below\": false, \"grants\": true, \"min\": \"2.1.299\"}\n",
      "command": "make deploy",
      "tool_use_id": "toolu_1",
      "surfaces": [
        "desktop"
      ],
      "check": "{\"ask_ms\": 300000, \"group\": \"deploy\", \"header\": \"foreman\", \"live\": false, \"no\": \"No\", \"notice\": \"orq, the orchestrator of fake, asks for your yes to «deploy»: answer in the chat; with no answer in 5 min the request moves to foreman's panel.\", \"question\": \"orq, the orchestrator of fake, wants to run a command of the group «deploy»: `make deploy`. Do you let it run the commands of «deploy» for 30 minutes?\", \"waiting\": \"Blocked: a question about the group «deploy» is already waiting for Owner's answer. Do not try again until Owner says so in the chat.\", \"yes\": \"Yes, for 30 min\"}\n",
      "ask": {
        "answer": "Yes, for 30 min"
      },
      "notify": {
        "isSent": true,
        "channel": "iterm2"
      }
    },
    "stdin": {
      "phase": "answer",
      "cwd": "<project>",
      "session": "S-orch",
      "command": "make deploy",
      "tool_use_id": "toolu_1",
      "group": "deploy",
      "surfaces": [
        "desktop"
      ],
      "answer": "yes",
      "notified": "sent:iterm2"
    },
    "run": {
      "exitCode": 0,
      "stdout": "{\"granted\": true, \"log\": \"«deploy» allowed for orq until 10:30\", \"until\": \"2026-11-01T10:10:30\"}\n",
      "stderr": ""
    },
    "status": null
  },
  {
    "name": "the owner says no",
    "op": "grant",
    "python": true,
    "engine": {
      "cwd": "<project>",
      "session": "S-orch",
      "version": "2.1.314",
      "start": "{\"active\": true, \"below\": false, \"grants\": true, \"min\": \"2.1.299\"}\n",
      "command": "make deploy",
      "tool_use_id": "toolu_1",
      "surfaces": [
        "desktop"
      ],
      "check": "{\"ask_ms\": 300000, \"group\": \"deploy\", \"header\": \"foreman\", \"live\": false, \"no\": \"No\", \"notice\": \"orq, the orchestrator of fake, asks for your yes to «deploy»: answer in the chat; with no answer in 5 min the request moves to foreman's panel.\", \"question\": \"orq, the orchestrator of fake, wants to run a command of the group «deploy»: `make deploy`. Do you let it run the commands of «deploy» for 30 minutes?\", \"waiting\": \"Blocked: a question about the group «deploy» is already waiting for Owner's answer. Do not try again until Owner says so in the chat.\", \"yes\": \"Yes, for 30 min\"}\n",
      "ask": {
        "answer": "No"
      },
      "notify": {
        "reject": "a hook refused it"
      }
    },
    "stdin": {
      "phase": "answer",
      "cwd": "<project>",
      "session": "S-orch",
      "command": "make deploy",
      "tool_use_id": "toolu_1",
      "group": "deploy",
      "surfaces": [
        "desktop"
      ],
      "answer": "no",
      "notified": "error"
    },
    "run": {
      "exitCode": 0,
      "stdout": "{\"deny\": \"Blocked: Owner said no to the group «deploy». Do not try again until Owner says so in the chat; for 10 minutes foreman will not ask again.\", \"granted\": false}\n",
      "stderr": ""
    },
    "status": null
  },
  {
    "name": "the owner closes the question (Esc, «Chat about this», a host that refuses)",
    "op": "grant",
    "python": true,
    "engine": {
      "cwd": "<project>",
      "session": "S-orch",
      "version": "2.1.314",
      "start": "{\"active\": true, \"below\": false, \"grants\": true, \"min\": \"2.1.299\"}\n",
      "command": "make deploy",
      "tool_use_id": "toolu_1",
      "surfaces": [
        "desktop"
      ],
      "check": "{\"ask_ms\": 300000, \"group\": \"deploy\", \"header\": \"foreman\", \"live\": false, \"no\": \"No\", \"notice\": \"orq, the orchestrator of fake, asks for your yes to «deploy»: answer in the chat; with no answer in 5 min the request moves to foreman's panel.\", \"question\": \"orq, the orchestrator of fake, wants to run a command of the group «deploy»: `make deploy`. Do you let it run the commands of «deploy» for 30 minutes?\", \"waiting\": \"Blocked: a question about the group «deploy» is already waiting for Owner's answer. Do not try again until Owner says so in the chat.\", \"yes\": \"Yes, for 30 min\"}\n",
      "ask": {
        "reject": "foreman: $.ui.ask: no answer (The user doesn't want to proceed with this tool use.)"
      }
    },
    "stdin": {
      "phase": "answer",
      "cwd": "<project>",
      "session": "S-orch",
      "command": "make deploy",
      "tool_use_id": "toolu_1",
      "group": "deploy",
      "surfaces": [
        "desktop"
      ],
      "answer": "dismissed",
      "notified": "no:no-surface"
    },
    "run": {
      "exitCode": 0,
      "stdout": "{\"deny\": \"Blocked: Owner closed the question about the group «deploy» without saying yes. Do not try again until Owner says so in the chat.\", \"granted\": false}\n",
      "stderr": ""
    },
    "status": null
  },
  {
    "name": "nobody to ask (a -p run)",
    "op": "grant",
    "python": true,
    "engine": {
      "cwd": "<project>",
      "session": "S-orch",
      "version": "2.1.314",
      "start": "{\"active\": true, \"below\": false, \"grants\": true, \"min\": \"2.1.299\"}\n",
      "command": "make deploy",
      "tool_use_id": "toolu_1",
      "surfaces": [
        "desktop"
      ],
      "check": "{\"ask_ms\": 300000, \"group\": \"deploy\", \"header\": \"foreman\", \"live\": false, \"no\": \"No\", \"notice\": \"orq, the orchestrator of fake, asks for your yes to «deploy»: answer in the chat; with no answer in 5 min the request moves to foreman's panel.\", \"question\": \"orq, the orchestrator of fake, wants to run a command of the group «deploy»: `make deploy`. Do you let it run the commands of «deploy» for 30 minutes?\", \"waiting\": \"Blocked: a question about the group «deploy» is already waiting for Owner's answer. Do not try again until Owner says so in the chat.\", \"yes\": \"Yes, for 30 min\"}\n",
      "ask": {
        "reject": "foreman: $.tool.call: no tool named \"AskUserQuestion\" in this session"
      }
    },
    "stdin": {
      "phase": "answer",
      "cwd": "<project>",
      "session": "S-orch",
      "command": "make deploy",
      "tool_use_id": "toolu_1",
      "group": "deploy",
      "surfaces": [
        "desktop"
      ],
      "answer": "unavailable",
      "notified": "no:no-surface"
    },
    "run": {
      "exitCode": 0,
      "stdout": "{\"deny\": \"Blocked: nobody could be asked about the group «deploy» in this session (no one at the prompt). Tell Owner in the chat.\", \"granted\": false}\n",
      "stderr": ""
    },
    "status": null
  },
  {
    "name": "no answer within ask_ms: a no for this call, the request waits in the panel",
    "op": "grant",
    "python": true,
    "engine": {
      "cwd": "<project>",
      "session": "S-orch",
      "version": "2.1.314",
      "start": "{\"active\": true, \"below\": false, \"grants\": true, \"min\": \"2.1.299\"}\n",
      "command": "make deploy",
      "tool_use_id": "toolu_1",
      "surfaces": [
        "desktop"
      ],
      "check": "{\"ask_ms\": 300000, \"group\": \"deploy\", \"header\": \"foreman\", \"live\": false, \"no\": \"No\", \"notice\": \"orq, the orchestrator of fake, asks for your yes to «deploy»: answer in the chat; with no answer in 5 min the request moves to foreman's panel.\", \"question\": \"orq, the orchestrator of fake, wants to run a command of the group «deploy»: `make deploy`. Do you let it run the commands of «deploy» for 30 minutes?\", \"waiting\": \"Blocked: a question about the group «deploy» is already waiting for Owner's answer. Do not try again until Owner says so in the chat.\", \"yes\": \"Yes, for 30 min\"}\n",
      "ask": {
        "timeout": true
      }
    },
    "stdin": {
      "phase": "answer",
      "cwd": "<project>",
      "session": "S-orch",
      "command": "make deploy",
      "tool_use_id": "toolu_1",
      "group": "deploy",
      "surfaces": [
        "desktop"
      ],
      "answer": "timeout",
      "notified": "no:no-surface"
    },
    "run": {
      "exitCode": 0,
      "stdout": "{\"deny\": \"Blocked: Owner did not answer the question about the group «deploy» in time. The request waits for Owner in foreman's panel for up to 24 h, and a late answer in the chat counts too: when Owner answers, a message from Owner comes to your inbox. Do not try again until then; if you no longer need it, withdraw it with `foreman permissions grants off deploy --pending`.\", \"granted\": false}\n",
      "stderr": ""
    },
    "status": null
  },
  {
    "name": "a yes to a session that is no longer the marked one grants nothing",
    "op": "grant",
    "python": true,
    "engine": {
      "cwd": "<project>",
      "session": "S-other",
      "version": "2.1.314",
      "start": "{\"active\": true, \"below\": false, \"grants\": true, \"min\": \"2.1.299\"}\n",
      "command": "make deploy",
      "tool_use_id": "toolu_1",
      "surfaces": [
        "desktop"
      ],
      "check": "{\"ask_ms\": 300000, \"group\": \"deploy\", \"header\": \"foreman\", \"live\": false, \"no\": \"No\", \"notice\": \"orq, the orchestrator of fake, asks for your yes to «deploy»: answer in the chat; with no answer in 5 min the request moves to foreman's panel.\", \"question\": \"orq, the orchestrator of fake, wants to run a command of the group «deploy»: `make deploy`. Do you let it run the commands of «deploy» for 30 minutes?\", \"waiting\": \"Blocked: a question about the group «deploy» is already waiting for Owner's answer. Do not try again until Owner says so in the chat.\", \"yes\": \"Yes, for 30 min\"}\n",
      "ask": {
        "answer": "Yes, for 30 min"
      }
    },
    "stdin": {
      "phase": "answer",
      "cwd": "<project>",
      "session": "S-other",
      "command": "make deploy",
      "tool_use_id": "toolu_1",
      "group": "deploy",
      "surfaces": [
        "desktop"
      ],
      "answer": "yes",
      "notified": "no:no-surface"
    },
    "run": {
      "exitCode": 0,
      "stdout": "{\"deny\": \"Blocked: the yes for the group «deploy» came to a session that is no longer the marked orchestrator, so nothing was granted.\", \"granted\": false}\n",
      "stderr": ""
    },
    "status": null
  },
  {
    "name": "a subagent's call: no check, no question",
    "op": "grant",
    "python": false,
    "engine": {
      "cwd": "<project>",
      "session": "S-orch",
      "version": "2.1.314",
      "start": "{\"active\": true, \"below\": false, \"grants\": true, \"min\": \"2.1.299\"}\n",
      "command": "make deploy",
      "tool_use_id": "toolu_1",
      "surfaces": [
        "desktop"
      ],
      "agentId": "a1"
    },
    "stdin": null,
    "run": null,
    "status": null
  },
  {
    "name": "a project without groups: no check",
    "op": "grant",
    "python": false,
    "engine": {
      "cwd": "<project>",
      "session": "S-orch",
      "version": "2.1.314",
      "start": "{\"active\": true, \"below\": false, \"min\": \"2.1.299\"}\n",
      "command": "make deploy",
      "tool_use_id": "toolu_1",
      "surfaces": [
        "desktop"
      ]
    },
    "stdin": null,
    "run": null,
    "status": null
  },
  {
    "name": "a project without groups: a change of permissions that opens is still checked and asked",
    "op": "grant",
    "python": true,
    "engine": {
      "cwd": "<project>",
      "session": "S-orch",
      "version": "2.1.314",
      "start": "{\"active\": true, \"below\": false, \"min\": \"2.1.299\"}\n",
      "command": "foreman permissions apply cand.toml --opens",
      "tool_use_id": "toolu_1",
      "surfaces": [
        "desktop"
      ]
    },
    "stdin": {
      "phase": "check",
      "cwd": "<project>",
      "session": "S-orch",
      "command": "foreman permissions apply cand.toml --opens",
      "tool_use_id": "toolu_1"
    },
    "run": {
      "exitCode": 0,
      "stdout": "{\"ask_ms\": 300000, \"group\": \"permissions\", \"header\": \"foreman\", \"live\": false, \"no\": \"No\", \"notice\": \"orq, the orchestrator of fake, asks for your yes to «permissions»: answer in the chat; with no answer in 5 min the request moves to foreman's panel.\", \"question\": \"orq, the orchestrator of fake, wants to run a command of the group «permissions»: `foreman permissions apply cand.toml --opens`. Do you let it run the commands of «permissions» for 30 minutes?\", \"waiting\": \"Blocked: a question about the group «permissions» is already waiting for Owner's answer. Do not try again until Owner says so in the chat.\", \"yes\": \"Yes, for 30 min\"}\n",
      "stderr": ""
    },
    "status": null
  },
  {
    "name": "a project without groups: an integration that opens the permissions is still checked and asked",
    "op": "grant",
    "python": true,
    "engine": {
      "cwd": "<project>",
      "session": "S-orch",
      "version": "2.1.314",
      "start": "{\"active\": true, \"below\": false, \"min\": \"2.1.299\"}\n",
      "command": "foreman integrate app ab1 AB-1 --opens",
      "tool_use_id": "toolu_1",
      "surfaces": [
        "desktop"
      ]
    },
    "stdin": {
      "phase": "check",
      "cwd": "<project>",
      "session": "S-orch",
      "command": "foreman integrate app ab1 AB-1 --opens",
      "tool_use_id": "toolu_1"
    },
    "run": {
      "exitCode": 0,
      "stdout": "{\"ask_ms\": 300000, \"group\": \"permissions\", \"header\": \"foreman\", \"live\": false, \"no\": \"No\", \"notice\": \"orq, the orchestrator of fake, asks for your yes to «permissions»: answer in the chat; with no answer in 5 min the request moves to foreman's panel.\", \"question\": \"orq, the orchestrator of fake, wants to run a command of the group «permissions»: `foreman integrate app ab1 AB-1 --opens`. Do you let it run the commands of «permissions» for 30 minutes?\", \"waiting\": \"Blocked: a question about the group «permissions» is already waiting for Owner's answer. Do not try again until Owner says so in the chat.\", \"yes\": \"Yes, for 30 min\"}\n",
      "stderr": ""
    },
    "status": null
  },
  {
    "name": "the check fails: the classic guard decides alone",
    "op": "grant",
    "python": false,
    "engine": {
      "cwd": "<project>",
      "session": "S-orch",
      "version": "2.1.314",
      "start": "{\"active\": true, \"below\": false, \"grants\": true, \"min\": \"2.1.299\"}\n",
      "command": "make deploy",
      "tool_use_id": "toolu_1",
      "surfaces": [
        "desktop"
      ]
    },
    "stdin": {
      "phase": "check",
      "cwd": "<project>",
      "session": "S-orch",
      "command": "make deploy",
      "tool_use_id": "toolu_1"
    },
    "run": {
      "exitCode": 1,
      "stdout": "",
      "stderr": "hook mod grant: ValueError: boom\n"
    },
    "status": "foreman: hook mod grant failed (exit 1: hook mod grant: ValueError: boom)"
  },
  {
    "name": "file: a main-loop Write of Claude Code's settings: the owner is asked",
    "op": "grant",
    "python": true,
    "engine": {
      "cwd": "<project>",
      "session": "S-orch",
      "version": "2.1.314",
      "start": "{\"active\": true, \"below\": false, \"grants\": true, \"min\": \"2.1.299\"}\n",
      "tool": "Write",
      "file_path": "<project>/.claude/settings.local.json",
      "tool_use_id": "toolu_1",
      "surfaces": [
        "desktop"
      ]
    },
    "stdin": {
      "phase": "check",
      "cwd": "<project>",
      "session": "S-orch",
      "tool": "Write",
      "path": "<project>/.claude/settings.local.json",
      "tool_use_id": "toolu_1"
    },
    "run": {
      "exitCode": 0,
      "stdout": "{\"ask_ms\": 300000, \"group\": \"file:claude\", \"header\": \"foreman\", \"live\": false, \"no\": \"No\", \"question\": \"Do you let this session of fake write `<project>/.claude/settings.local.json`, Claude Code's own settings? It is for this one change only.\", \"waiting\": \"Blocked: a question about Claude Code's own settings is already waiting for Owner's answer. Do not try again until Owner says so in the chat.\", \"yes\": \"Yes, this change only\"}\n",
      "stderr": ""
    },
    "status": null
  },
  {
    "name": "file: the owner's yes lets that one Write on",
    "op": "grant",
    "python": true,
    "engine": {
      "cwd": "<project>",
      "session": "S-orch",
      "version": "2.1.314",
      "start": "{\"active\": true, \"below\": false, \"grants\": true, \"min\": \"2.1.299\"}\n",
      "tool": "Write",
      "file_path": "<project>/.claude/settings.local.json",
      "check": "{\"ask_ms\": 300000, \"group\": \"file:claude\", \"header\": \"foreman\", \"live\": false, \"no\": \"No\", \"question\": \"Do you let this session of fake write `<project>/.claude/settings.local.json`, Claude Code's own settings? It is for this one change only.\", \"waiting\": \"Blocked: a question about Claude Code's own settings is already waiting for Owner's answer. Do not try again until Owner says so in the chat.\", \"yes\": \"Yes, this change only\"}\n",
      "ask": {
        "answer": "Yes, this change only"
      },
      "tool_use_id": "toolu_1",
      "surfaces": [
        "desktop"
      ]
    },
    "stdin": {
      "phase": "answer",
      "cwd": "<project>",
      "session": "S-orch",
      "tool": "Write",
      "path": "<project>/.claude/settings.local.json",
      "tool_use_id": "toolu_1",
      "group": "file:claude",
      "surfaces": [
        "desktop"
      ],
      "answer": "yes"
    },
    "run": {
      "exitCode": 0,
      "stdout": "{\"granted\": true, \"log\": \"`<project>/.claude/settings.local.json` may be written once, this call\"}\n",
      "stderr": ""
    },
    "status": null
  },
  {
    "name": "file: the owner's no refuses the Write",
    "op": "grant",
    "python": true,
    "engine": {
      "cwd": "<project>",
      "session": "S-orch",
      "version": "2.1.314",
      "start": "{\"active\": true, \"below\": false, \"grants\": true, \"min\": \"2.1.299\"}\n",
      "tool": "Write",
      "file_path": "<project>/.claude/settings.local.json",
      "check": "{\"ask_ms\": 300000, \"group\": \"file:claude\", \"header\": \"foreman\", \"live\": false, \"no\": \"No\", \"question\": \"Do you let this session of fake write `<project>/.claude/settings.local.json`, Claude Code's own settings? It is for this one change only.\", \"waiting\": \"Blocked: a question about Claude Code's own settings is already waiting for Owner's answer. Do not try again until Owner says so in the chat.\", \"yes\": \"Yes, this change only\"}\n",
      "ask": {
        "answer": "No"
      },
      "tool_use_id": "toolu_1",
      "surfaces": [
        "desktop"
      ]
    },
    "stdin": {
      "phase": "answer",
      "cwd": "<project>",
      "session": "S-orch",
      "tool": "Write",
      "path": "<project>/.claude/settings.local.json",
      "tool_use_id": "toolu_1",
      "group": "file:claude",
      "surfaces": [
        "desktop"
      ],
      "answer": "no"
    },
    "run": {
      "exitCode": 0,
      "stdout": "{\"deny\": \"Blocked: Owner said no to writing `<project>/.claude/settings.local.json` (Claude Code's own settings). Do not try again until Owner says so in the chat.\", \"granted\": false}\n",
      "stderr": ""
    },
    "status": null
  },
  {
    "name": "file: a session with nobody to ask (claude -p) cannot answer: refused",
    "op": "grant",
    "python": true,
    "engine": {
      "cwd": "<project>",
      "session": "S-orch",
      "version": "2.1.314",
      "start": "{\"active\": true, \"below\": false, \"grants\": true, \"min\": \"2.1.299\"}\n",
      "tool": "Write",
      "file_path": "<project>/.claude/settings.local.json",
      "check": "{\"ask_ms\": 300000, \"group\": \"file:claude\", \"header\": \"foreman\", \"live\": false, \"no\": \"No\", \"question\": \"Do you let this session of fake write `<project>/.claude/settings.local.json`, Claude Code's own settings? It is for this one change only.\", \"waiting\": \"Blocked: a question about Claude Code's own settings is already waiting for Owner's answer. Do not try again until Owner says so in the chat.\", \"yes\": \"Yes, this change only\"}\n",
      "ask": {
        "reject": "foreman: $.tool.call: no tool named \"AskUserQuestion\" in this session"
      },
      "tool_use_id": "toolu_1",
      "surfaces": [
        "desktop"
      ]
    },
    "stdin": {
      "phase": "answer",
      "cwd": "<project>",
      "session": "S-orch",
      "tool": "Write",
      "path": "<project>/.claude/settings.local.json",
      "tool_use_id": "toolu_1",
      "group": "file:claude",
      "surfaces": [
        "desktop"
      ],
      "answer": "unavailable"
    },
    "run": {
      "exitCode": 0,
      "stdout": "{\"deny\": \"Blocked: nobody could be asked in this session about writing `<project>/.claude/settings.local.json` (Claude Code's own settings); nobody is in front of it. Tell Owner what to change and where.\", \"granted\": false}\n",
      "stderr": ""
    },
    "status": null
  },
  {
    "name": "file: a subagent's Write of Claude Code's settings is never asked about: the classic guard refuses it",
    "op": "grant",
    "python": false,
    "engine": {
      "cwd": "<project>",
      "session": "S-orch",
      "version": "2.1.314",
      "start": "{\"active\": true, \"below\": false, \"grants\": true, \"min\": \"2.1.299\"}\n",
      "tool": "Write",
      "file_path": "<project>/.claude/settings.local.json",
      "agentId": "a1",
      "tool_use_id": "toolu_1",
      "surfaces": [
        "desktop"
      ]
    },
    "stdin": null,
    "run": null,
    "status": null
  },
  {
    "name": "file: without groups, a Write of a file whose path names none of them is not checked",
    "op": "grant",
    "python": false,
    "engine": {
      "cwd": "<project>",
      "session": "S-orch",
      "version": "2.1.314",
      "start": "{\"active\": true, \"below\": false, \"min\": \"2.1.299\"}\n",
      "tool": "Write",
      "file_path": "<project>/notes.md",
      "tool_use_id": "toolu_1",
      "surfaces": [
        "desktop"
      ]
    },
    "stdin": null,
    "run": null,
    "status": null
  },
  {
    "name": "file: a Bash command that writes Claude Code's settings: the owner is asked, with the command",
    "op": "grant",
    "python": true,
    "engine": {
      "cwd": "<project>",
      "session": "S-orch",
      "version": "2.1.314",
      "start": "{\"active\": true, \"below\": false, \"grants\": true, \"min\": \"2.1.299\"}\n",
      "command": "echo '{}' > .claude/settings.local.json",
      "tool_use_id": "toolu_1",
      "surfaces": [
        "desktop"
      ]
    },
    "stdin": {
      "phase": "check",
      "cwd": "<project>",
      "session": "S-orch",
      "command": "echo '{}' > .claude/settings.local.json",
      "tool_use_id": "toolu_1"
    },
    "run": {
      "exitCode": 0,
      "stdout": "{\"ask_ms\": 300000, \"group\": \"file:claude\", \"header\": \"foreman\", \"live\": false, \"no\": \"No\", \"question\": \"Do you let this session of fake run `echo '{}' > .claude/settings.local.json`? It writes `.claude/settings.local.json`, Claude Code's own settings. It is for this one command only.\", \"waiting\": \"Blocked: a question about Claude Code's own settings is already waiting for Owner's answer. Do not try again until Owner says so in the chat.\", \"yes\": \"Yes, this change only\"}\n",
      "stderr": ""
    },
    "status": null
  }
]
