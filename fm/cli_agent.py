"""The commands an agent runs from its own session: ask, done, verdict, ready, block."""

import argparse
import os
import sys
from pathlib import Path

from . import parents, util
from .cli_common import home, mine
from .core.oversight import diagnosis
from .core.review import reviews
from .core.tasks import backlog, questions


def cmd_ask(a: argparse.Namespace) -> None:
    p = home(a)
    kind = "permission" if a.action else "question"
    qid = questions.ask(p, mine(a), os.environ.get("FM_AGENT", "?"), a.text, kind=kind, action=a.action or "")
    print(f"{qid} is with the owner. End your turn now: his answer will come back to this conversation.")


def cmd_done(a: argparse.Namespace) -> None:
    p = home(a)
    tid = mine(a)
    agent: dict[str, str] = backlog.get(backlog.load(p), tid).get("agent") or {}
    if agent.get("kind") == "parent":
        print(f"{tid} is closed as {parents.finish(p, tid, a.summary)}. End your turn.")
        return
    report = {"summary": a.summary, "file": a.report or "", "confused": a.misled or "", "at": util.now()}
    backlog.set_status(p, tid, "in review", a.summary, report=report, blocked="")
    backlog.record_misled(p, tid, "responsible", a.misled)
    print(f"{tid} is in review: the hunter and the reviewer read it, then the owner. End your turn.")


def cmd_verdict(a: argparse.Namespace) -> None:
    p = home(a)
    tid = mine(a)
    with backlog.edit(p) as d:
        i = backlog.get(d, tid)
        latest: reviews.Reviewer = i.get("reviewer") or {}
        newest = latest.get("tip", "")
        launched = [x.get("tip", "") for x in (*i.get("reviewers", []), latest)]
        tip = next((t for t in launched if t and t.startswith(a.tip)), "") if a.tip else newest
        if a.tip and not tip:
            sys.exit(f"fm: no reviewer was launched for tip {a.tip}; use the Tip from your brief")
        v = {"round": reviews.review_round(i), "tip": tip, "open": a.open, "file": a.file, "at": util.now()}
        i["reviews"] = [*i.get("reviews", []), v]
        if tip == newest:  # a verdict for an older launch is history: it decides nothing
            i["review"] = v
            report: backlog.Report = i.get("report") or {}
            i["report"] = {**report, "review": "clean" if a.open == 0 else f"open: {a.open}"}
    backlog.record_misled(p, tid, "hunter" if os.environ.get("FM_AGENT", "").endswith("-hunt") else "reviewer", a.misled)
    backlog.event(p, tid, f"review round {v['round']} of {tip[:9]}: {a.open} open ({a.file})")
    print("Verdict recorded. End your turn.")


def cmd_ready(a: argparse.Namespace) -> None:
    p = home(a)
    tid = mine(a)
    if a.plan:
        f = Path(a.plan)
        if not f.is_file() or not f.read_text(encoding="utf-8").strip():
            sys.exit(f"fm: the plan file {a.plan} is missing or empty: write it, then run fm ready again")
    with backlog.edit(p) as d:
        i = backlog.get(d, tid)
        if a.plan:
            i["plan_file"] = str(Path(a.plan).resolve())
        else:
            i.pop("plan_file", None)  # a relaunched planning agent that now says «trivial» leaves no stale plan
        if a.detail:
            i["detail"] = (i.get("detail", "") + "\n\n" + a.detail).strip()
        for k in ("model", "effort"):
            if (v := getattr(a, k)) and not i.get(k):
                i[k] = v
        i["status"], i["blocked"] = "ready", ""
    backlog.event(p, tid, "→ ready" + (f" (no plan: {a.no_plan})" if a.no_plan else ""))
    backlog.record_misled(p, tid, "planning", a.misled)
    print(f"{tid} is ready; the loop will launch its responsible agent. End your turn.")


def cmd_block(a: argparse.Namespace) -> None:
    p = home(a)
    backlog.update(p, mine(a), blocked=a.text)
    backlog.event(p, mine(a), f"blocked by its agent: {a.text}")
    print("Blocked: the owner sees it in the panel. End your turn.")


def cmd_diagnose(a: argparse.Namespace) -> None:
    p = home(a)
    tid = mine(a)
    f = Path(a.file)
    text = f.read_text(encoding="utf-8") if f.is_file() else ""
    if not text.strip():
        sys.exit(f"fm: the diagnosis file {a.file} is missing or empty")
    if missing := diagnosis.missing_sections(text):
        sys.exit("fm: the diagnosis lacks: " + ", ".join(missing) + ". Add those sections and run fm diagnose again.")
    if not diagnosis.record(p, tid, "Diagnosis (mechanic)", text, file=str(f)):
        sys.exit(f"fm: {tid} already has a diagnosis")
    print(f"Diagnosis recorded in {tid}. Remove your scratch folder and end your turn.")
