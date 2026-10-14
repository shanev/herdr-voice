#!/usr/bin/env python3
"""herdr workspaces and their agents, for Hark: the skill state its SKILL.md declares (metadata.hark).

Usage: hark_state.py contexts
       hark_state.py create <repo-or-name> [machine]
       hark_state.py items [workspace]
       hark_state.py realert <pane>

contexts: this machine's herdr workspaces and those on herdr's enabled saved machines (`herdr machine list`), as
{"contexts": [{"id", "name", "location"}]}. A workspace on another machine has the id `<machine>/<workspace id>`, and
its location is the machine's label; this machine's location is its host name. A machine that doesn't answer is
left out.

create: a workspace for a repo (found as ~/<repo>, ~/github.com/*/<repo> or ~/*/<repo>, or a path), labelled with its
name, else one labelled with the name; on the given machine if it isn't this one. Prints {"context": {...}}.

items: the agents in a workspace, or in every workspace each with its "context", as {"items": [...]}: herdr's status
as working, waiting (blocked on a question), done, idle; the terminal title as the detail line; for a blocked agent,
a question card with its screen (Hark reads the question from it); for a done agent whose finish no one has told
the user yet, a result card. An agent herdr-voice handed off (its mark in ~/.cache/herdr-voice/handed-off) gets no
result card: Hermes reports that one in the conversation.

realert: has the desk-alerts plugin alert again for a pane whose dialog moved on to another question.

Stdlib only.
"""
import hashlib
import json
import socket
import subprocess
import sys
import time
from pathlib import Path

STATES = {"working": "working", "blocked": "waiting", "done": "done", "idle": "idle"}
HANDED_OFF = Path.home() / ".cache" / "herdr-voice" / "handed-off"
HAND_OFF_TTL = 12 * 60 * 60
MACHINE_TIMEOUT = 8


class HerdrError(Exception):
    pass


def herdr(*args, machine=None, timeout=20):
    command = ["herdr", *(["--machine", machine] if machine else []), *args]
    try:
        got = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise HerdrError(f"`{' '.join(command)}` took more than {timeout} seconds") from None
    if got.returncode != 0:
        raise HerdrError(f"`{' '.join(command)}` failed: {got.stderr.strip()[:300]}")
    return got.stdout


def herdr_json(*args, machine=None, timeout=20):
    value = json.loads(herdr(*args, machine=machine, timeout=timeout))
    return value["result"] if isinstance(value, dict) and "result" in value else value


def this_machine():
    return socket.gethostname().split(".")[0]


def machines():
    """herdr's enabled saved machines, by label."""
    try:
        listed = json.loads(herdr("machine", "list", "--json"))
    except (HerdrError, ValueError):
        return []
    return [m["label"] for m in listed if isinstance(m, dict) and m.get("enabled") and m.get("label")]


def split(context):
    """A context id as (machine or None, workspace id)."""
    machine, _, workspace = context.rpartition("/")
    return (machine or None), workspace


def joined(machine, workspace):
    return f"{machine}/{workspace}" if machine else workspace


def workspace_name(workspace):
    return workspace.get("label") or f"Workspace {workspace['workspace_id']}"


def contexts():
    found = []
    here = this_machine()
    for workspace in herdr_json("workspace", "list")["workspaces"]:
        found.append({"id": workspace["workspace_id"], "name": workspace_name(workspace), "location": here})
    for machine in machines():
        try:
            listed = herdr_json("workspace", "list", machine=machine, timeout=MACHINE_TIMEOUT)["workspaces"]
        except (HerdrError, ValueError, KeyError, TypeError):
            continue
        found += [{"id": joined(machine, w["workspace_id"]), "name": workspace_name(w), "location": machine} for w in listed]
    return {"contexts": found}


def find_repo(name):
    path = Path(name).expanduser()
    if ("/" in name or name.startswith("~")) and path.is_dir():
        return path
    home = Path.home()
    for candidate in [home / name, *sorted(home.glob(f"github.com/*/{name}")), *sorted(home.glob(f"*/{name}"))]:
        if candidate.is_dir():
            return candidate
    return None


def create(name, machine=None):
    if machine == this_machine():
        machine = None
    if machine and machine not in machines():
        raise HerdrError(f"herdr has no saved machine named {machine} (`herdr machine list`)")
    repo = find_repo(name) if machine is None else None
    if repo is None and machine and name.startswith(("/", "~")):
        args = ["--cwd", name, "--label", Path(name).name]
    elif repo is not None:
        args = ["--cwd", str(repo), "--label", repo.name]
    else:
        args = ["--label", name]
    made = herdr_json("workspace", "create", *args, "--no-focus", machine=machine)["workspace"]
    return {"context": {"id": joined(machine, made["workspace_id"]), "name": workspace_name(made),
                        "location": machine or this_machine()}}


def screen_text(screen):
    """The screen without trailing spaces and blank lines, newest 6000 characters (as desk-alerts sends it)."""
    lines = [line.rstrip() for line in screen.splitlines()]
    return "\n".join(line for line in lines if line)[-6000:]


def handed_off(agent, now):
    """herdr-voice handed this agent's task off, so Hermes tells its result itself."""
    for name in filter(None, (agent.get("pane_id"), agent.get("name"))):
        for marker in (HANDED_OFF / name.replace("/", "_"), HANDED_OFF / (name.replace("/", "_") + ".finished")):
            try:
                if now - marker.stat().st_mtime < HAND_OFF_TTL:
                    return True
            except OSError:
                pass
    return False


def agent_items(machine, workspace=None):
    """The agents on a machine, in one workspace or all, with the context each belongs to."""
    agents = herdr_json("agent", "list", machine=machine)["agents"]
    if workspace is not None:
        agents = [a for a in agents if a.get("workspace_id") == workspace]
    if not agents:
        return []
    panes = {p["pane_id"]: p for p in herdr_json("pane", "list", machine=machine)["panes"]}
    tabs = herdr_json("tab", "list", machine=machine)["tabs"]
    now = time.time()
    items = []
    for agent in agents:
        pane = panes.get(agent["pane_id"]) or {}
        # A pane's label is its terminal title until it's renamed (by the user, or a plugin such as Auto Title).
        label = pane.get("label") if pane.get("label") and pane.get("label") != pane.get("terminal_title_stripped") else None
        siblings = [t for t in tabs if t.get("workspace_id") == agent.get("workspace_id")]
        tab = next((t for t in siblings if t["tab_id"] == agent.get("tab_id")), None)
        where = (tab.get("label") if tab and tab.get("label") and not tab["label"][:1].isdigit() else
                 str(siblings.index(tab) + 1) if tab else "")
        kind = (agent.get("agent") or "agent").capitalize()
        name = agent.get("name") or label or (f"{kind} · {where}" if where else kind)
        state = STATES.get(agent.get("agent_status"), "idle")
        item = {"id": agent["pane_id"], "name": name, "state": state, "detail": agent.get("terminal_title_stripped") or ""}
        if state == "waiting":
            try:
                screen = screen_text(herdr("agent", "read", agent["pane_id"], "--source", "visible", machine=machine))
            except HerdrError:
                screen = ""
            if screen:
                item["card"] = {"kind": "question", "id": hashlib.sha256(screen.encode()).hexdigest()[:16], "screen": screen}
        elif state == "done" and agent.get("completion_seq") is not None and not handed_off(agent, now):
            item["card"] = {"kind": "result", "id": f"{agent['pane_id']}:{agent['completion_seq']}"}
        items.append((joined(machine, agent.get("workspace_id", "")), item))
    return items


def items(context=None):
    if context is not None:
        machine, workspace = split(context)
        return {"items": [item for _, item in agent_items(machine, workspace)]}
    found = agent_items(None)
    for machine in machines():
        try:
            found += agent_items(machine)
        except (HerdrError, ValueError, KeyError, TypeError):
            continue
    return {"items": [{**item, "context": context} for context, item in found]}


def realert(pane):
    plugins = herdr_json("plugin", "list", "--json")["plugins"]
    root = next((p.get("plugin_root") for p in plugins if p.get("plugin_id") == "herdr-voice.desk-alerts"), None)
    if not root:
        raise HerdrError("the desk-alerts plugin isn't installed: the next question shows when Hark next looks")
    subprocess.run([sys.executable, str(Path(root) / "desk_alert.py"), "realert", pane], check=True, timeout=30)


def main(argv):
    try:
        if argv[1:] == ["contexts"]:
            print(json.dumps(contexts()))
        elif len(argv) in (3, 4) and argv[1] == "create":
            print(json.dumps(create(*argv[2:])))
        elif len(argv) in (2, 3) and argv[1] == "items":
            print(json.dumps(items(*argv[2:])))
        elif len(argv) == 3 and argv[1] == "realert":
            realert(argv[2])
        else:
            print(__doc__.strip(), file=sys.stderr)
            return 2
    except (HerdrError, ValueError, KeyError, TypeError) as error:
        print(f"hark_state: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
