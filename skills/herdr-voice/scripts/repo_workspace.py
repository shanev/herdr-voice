#!/usr/bin/env python3
"""Print the herdr workspace a repo's sessions belong in, or nothing if it has none yet.

Usage: repo_workspace.py <repo_path>

The workspace of an agent working in the repo (its cwd is the repo or inside it), else the
workspace labelled with the repo's name, ignoring case. An agent in a folder that contains the
repo doesn't count. Exit 0 with the workspace id on stdout, or exit 1 with nothing when the repo
has no workspace yet. Stdlib only.
"""
import json
import subprocess
import sys
from pathlib import Path


def herdr(*args):
    got = subprocess.run(["herdr", *args], capture_output=True, text=True, check=True)
    return json.loads(got.stdout)["result"]


def workspace_for(repo, agents, workspaces):
    repo = str(Path(repo).expanduser()).rstrip("/")
    for agent in agents:
        cwd = (agent.get("cwd") or "").rstrip("/")
        if cwd == repo or cwd.startswith(repo + "/"):
            return agent["workspace_id"]
    name = Path(repo).name.lower()
    for workspace in workspaces:
        if (workspace.get("label") or "").lower() == name:
            return workspace["workspace_id"]
    return None


def main(argv):
    if len(argv) != 2:
        print(__doc__.strip(), file=sys.stderr)
        return 2
    found = workspace_for(argv[1], herdr("agent", "list")["agents"], herdr("workspace", "list")["workspaces"])
    if found is None:
        return 1
    print(found)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
