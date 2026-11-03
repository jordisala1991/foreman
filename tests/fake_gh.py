#!/usr/bin/env python3
"""A stand-in for `gh` in the integrate tests: PRs live in $FAKE_GH_DIR/state.json and merges really happen on the
bare `origin` remote of the current repository (squash, merge commit or rebase), so foreman integrate can pull
them. Every call is appended to $FAKE_GH_DIR/calls.log. FAKE_GH_CHECKS=fail makes `pr checks` fail;
FAKE_GH_DELETE_HEAD=1 deletes the head branch on the remote when merging, as GitHub may."""

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

D = Path(os.environ["FAKE_GH_DIR"])
STATE = D / "state.json"


def git(cwd, *args):
    r = subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True, check=False)
    if r.returncode:
        sys.exit(f"fake gh: git {' '.join(args)}: {r.stderr}")
    return r.stdout.strip()


def opt(args, name, default=None):
    return args[args.index(name) + 1] if name in args else default


def main(args):
    D.mkdir(parents=True, exist_ok=True)
    with open(D / "calls.log", "a") as f:
        f.write(json.dumps(args) + "\n")
    st = json.loads(STATE.read_text()) if STATE.exists() else {"prs": {}}
    prs = st["prs"]
    if args[:2] == ["pr", "create"]:
        n = str(len(prs) + 1)
        prs[n] = {
            "head": opt(args, "--head"),
            "base": opt(args, "--base"),
            "title": opt(args, "--title"),
            "body": opt(args, "--body"),
            "state": "OPEN",
            "comments": [],
        }
        print(f"https://github.com/acme/app/pull/{n}")
    elif args[:2] == ["pr", "view"]:
        key = args[2]
        n = key if key in prs else next((k for k, p in prs.items() if p["head"] == key and p["state"] == "OPEN"), None)
        if not n:
            sys.exit("no pull requests found")
        if "--jq" in args:
            print(prs[n].get("merge_commit", ""))
        else:
            print(
                json.dumps({"number": int(n), "url": f"https://github.com/acme/app/pull/{n}", "state": prs[n]["state"]})
            )
    elif args[:2] == ["pr", "checks"]:
        if os.environ.get("FAKE_GH_CHECKS") == "fail":
            print("lint\tfail\t1m\thttps://ci")
            sys.exit(1)
        print("lint\tpass\t1m\thttps://ci")
    elif args[:2] == ["pr", "comment"]:
        prs[args[2]]["comments"].append(opt(args, "--body"))
    elif args[:2] == ["pr", "merge"]:
        n, p = args[2], prs[args[2]]
        origin = git(os.getcwd(), "remote", "get-url", "origin")
        with tempfile.TemporaryDirectory() as tmp:
            git(tmp, "clone", "-q", origin, "c")
            c = Path(tmp) / "c"
            git(c, "checkout", "-q", p["base"])
            head = git(c, "rev-parse", f"origin/{p['head']}")
            if opt(args, "--match-head-commit") != head:
                sys.exit("fake gh: head commit does not match")
            if "--squash" in args:
                git(c, "merge", "-q", "--squash", f"origin/{p['head']}")
                git(c, "commit", "-q", "-m", opt(args, "--subject"), "-m", opt(args, "--body", ""))
            elif "--merge" in args:
                git(c, "merge", "-q", "--no-ff", f"origin/{p['head']}", "-m", opt(args, "--subject"))
            else:
                git(c, "rebase", "-q", p["base"], f"origin/{p['head']}")
                git(c, "checkout", "-q", "-B", p["base"])
            git(c, "push", "-q", "origin", f"HEAD:{p['base']}")
            if os.environ.get("FAKE_GH_DELETE_HEAD"):  # like GitHub's "delete head branches" on merge
                git(c, "push", "-q", "origin", "--delete", p["head"])
            p["merge_commit"] = git(c, "rev-parse", "HEAD")
            p["state"] = "MERGED"
            p["method"] = next(m for m in ("--squash", "--merge", "--rebase") if m in args)
    elif args[:2] == ["issue", "list"]:
        print(os.environ.get("FAKE_GH_ISSUES", "[]"))
    else:
        sys.exit(f"fake gh: unsupported {args}")
    STATE.write_text(json.dumps(st, indent=1))


if __name__ == "__main__":
    main(sys.argv[1:])
