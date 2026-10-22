#!/usr/bin/env python3
"""herdr workspaces and their agents, for Vux: the skill state its SKILL.md declares (in metadata.hark, the name from
before Hark became Vux, which Hark-era clients still need).

Usage: vux_state.py contexts
       vux_state.py create <repo-or-name> [machine]
       vux_state.py items [workspace]
       vux_state.py realert <pane>
       vux_state.py mark <agent>

contexts: this machine's herdr workspaces, as {"contexts": [{"id", "name", "location"}]}, the location its host name;
the home folder's workspace (labelled "~") is "Home".
Another of herdr's saved machines shows in Vux when it's paired as a computer of its own: the desk alerts there
report its changes, which this machine's never hear of.

create: a workspace for a repo (found as ~/<repo>, ~/github.com/*/<repo> or ~/*/<repo>, or a path), labelled with its
name, else one labelled with the name; on the given machine if it isn't this one (its id is then
`<machine>/<workspace id>`). Prints {"context": {...}}.

items: the agents in a workspace, or in every workspace each with its "context", as {"items": [...]}, in the item
format version 2 that SKILL.md declares (metadata.hark.items.version). Every field is herdr's own, as herdr has it,
the same for every agent kind:
  id     the agent's herdr pane id.
  name   its herdr agent name, else its pane id (herdr's agent commands take either).
  kind   herdr's agent kind as herdr reports it (claude, codex, grok…), "agent" when herdr reports none.
  task   what herdr shows for the pane: its pane label when it has one (`herdr pane list`), else its terminal title,
         else "New session".
  state  herdr's status as working, waiting (blocked on a question), done or idle.
  card   for a blocked agent, a question card with its screen (Vux reads the question from it); for a done agent whose
         finish no one has told the user yet, a result card, with the start of its last reply (Claude Code and Codex,
         on this machine); else null. An agent herdr-voice handed off (its mark in ~/.cache/herdr-voice/handed-off)
         gets no result card: Hermes reports that one in the conversation.
Names and labels are whoever set them: the user (`herdr agent rename`, `herdr pane rename`), the voice recipe's
`herdr agent start <name>`, or a plugin such as herdr.auto-title, which labels panes for their work. This script
reports them as they are and writes neither.

realert: has the desk-alerts plugin alert again for a pane whose dialog moved on to another question, and clears the
pane's hand-off mark (below), so the plugin reports the pane from then on.

mark: marks the agent's task (by pane id or name) as handed off: the agent running this skill reports it, so the
desk-alerts plugin doesn't alert for it and items give it no result card. The mark is a file named for the agent in
~/.cache/herdr-voice/handed-off, which the plugin reads. These are the skill's only bookkeeping writes, here so the agent
never runs `touch` or `rm` on them itself (an agent's approval policy may stop a turn to ask about an `rm`).

Stdlib only.
"""
import hashlib
import json
import socket
import subprocess
import sys
import time
from pathlib import Path

# last_reply.py beside this script, however it's loaded, without writing bytecode beside it: Hermes calls a new file in
# a skill's folder a local edit and won't update the skill over it.
sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
from last_reply import last_reply  # noqa: E402

STATES = {"working": "working", "blocked": "waiting", "done": "done", "idle": "idle"}
HANDED_OFF = Path.home() / ".cache" / "herdr-voice" / "handed-off"
HAND_OFF_TTL = 12 * 60 * 60
# The start of a result Vux shows on the agent's row (its first line or two); Hear gets the whole of it.
RESULT_TEXT = 300
# The task of an agent whose pane herdr shows nothing for.
NO_TASK = "New session"


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


def joined(machine, workspace):
    return f"{machine}/{workspace}" if machine else workspace


def workspace_name(workspace):
    label = workspace.get("label")
    # A workspace opened in the home folder is labelled with it, "~": nothing to show or say.
    return "Home" if label == "~" else label or f"Workspace {workspace['workspace_id']}"


def contexts():
    here = this_machine()
    return {"contexts": [{"id": w["workspace_id"], "name": workspace_name(w), "location": here}
                         for w in herdr_json("workspace", "list")["workspaces"]]}


def find_repo(name):
    path = Path(name).expanduser()
    if ("/" in name or name.startswith("~")) and path.is_dir():
        return path
    home = Path.home()
    for candidate in [home / name, *sorted(home.glob(f"github.com/*/{name}")), *sorted(home.glob(f"*/{name}"))]:
        if candidate.is_dir():
            return candidate
    return None


def machine_named(said):
    """The saved machine a person means: its label, or the user or host part of one like vega@vega."""
    labels = machines()
    if said in labels:
        return said
    return next((label for label in labels if said in label.split("@")), None)


def create(name, machine=None):
    """The workspace for `name` on the machine: the one already labelled so (a repo's name, or any), else a new one,
    in the repo's folder when it's one here, else in the home folder."""
    if machine in (this_machine(), "this computer"):
        machine = None
    if machine:
        found = machine_named(machine)
        if found is None:
            raise HerdrError(f"herdr has no saved machine named {machine} (`herdr machine list`)")
        machine = found
    repo = find_repo(name) if machine is None else None
    label = repo.name if repo is not None else Path(name).name if name.startswith(("/", "~")) else name
    existing = herdr_json("workspace", "list", machine=machine)["workspaces"]
    # By its label or the name Vux shows ("Home" for the home folder's "~").
    same = next((w for w in existing if label.lower() in ((w.get("label") or "").lower(), workspace_name(w).lower())), None)
    if same:
        return {"context": {"id": joined(machine, same["workspace_id"]), "name": workspace_name(same),
                            "location": machine or this_machine()}}
    if repo is not None:
        args = ["--cwd", str(repo), "--label", repo.name]
    elif machine and name.startswith(("/", "~")):
        args = ["--cwd", name, "--label", label]
    elif machine:
        # Another machine's folders aren't known here: its home folder (that machine's herdr expands ~).
        args = ["--cwd", "~", "--label", name]
    else:
        args = ["--cwd", str(Path.home()), "--label", name]
    made = herdr_json("workspace", "create", *args, "--no-focus", machine=machine)["workspace"]
    return {"context": {"id": joined(machine, made["workspace_id"]), "name": workspace_name(made),
                        "location": machine or this_machine()}}


def plain(text):
    """Text without icon-font glyphs (Nerd Fonts' private-use characters, which a phone can't show and speech would
    read), spaces folded."""
    kept = "".join(c for c in text or "" if not (0xE000 <= ord(c) <= 0xF8FF or ord(c) >= 0xF0000))
    return " ".join(kept.split())


def task(agent, label):
    """What herdr shows for the agent's pane: its label, else its terminal title, else "New session"."""
    return plain(label) or plain(agent.get("terminal_title_stripped") or agent.get("terminal_title")) or NO_TASK


def screen_text(screen):
    """The screen without trailing spaces and blank lines, newest 6000 characters (as desk-alerts sends it)."""
    lines = [line.rstrip() for line in screen.splitlines()]
    return "\n".join(line for line in lines if line)[-6000:]


def marks(name):
    """The hand-off mark for an agent's pane id or name, and the one the desk-alerts plugin leaves once that task
    finished (`<name>.finished`), as the plugin names them."""
    mark = HANDED_OFF / name.replace("/", "_")
    return mark, mark.with_name(mark.name + ".finished")


def mark(agent):
    """Marks the agent's task as handed off (a fresh mark: it counts for HAND_OFF_TTL from now)."""
    HANDED_OFF.mkdir(parents=True, exist_ok=True)
    marks(agent)[0].touch()


def handed_off(agent, now):
    """herdr-voice handed this agent's task off, so Hermes tells its result itself."""
    for name in filter(None, (agent.get("pane_id"), agent.get("name"))):
        for marker in marks(name):
            try:
                if now - marker.stat().st_mtime < HAND_OFF_TTL:
                    return True
            except OSError:
                pass
    return False


def result_start(agent):
    """The start of the agent's last reply, from its session file (Claude Code, Codex); None for other kinds."""
    try:
        reply = (last_reply(agent, Path.home()) or "").strip()
    except (OSError, ValueError, KeyError, TypeError):
        return None
    if len(reply) <= RESULT_TEXT:
        return reply or None
    cut = reply[:RESULT_TEXT]
    space = cut.rfind(" ")
    return (cut[:space] if space > 0 else cut).rstrip() + "…"


def agent_items(machine, workspace=None):
    """The agents on a machine, in one workspace or all, with the context each belongs to."""
    agents = herdr_json("agent", "list", machine=machine)["agents"]
    if workspace is not None:
        agents = [a for a in agents if a.get("workspace_id") == workspace]
    if not agents:
        return []
    # Pane labels are on panes only (an agent's entry has none).
    panes = herdr_json("pane", "list", *(["--workspace", workspace] if workspace is not None else []), machine=machine)
    labels = {p["pane_id"]: p.get("label") for p in panes["panes"]}
    now = time.time()
    items = []
    for agent in agents:
        state = STATES.get(agent.get("agent_status"), "idle")
        item = {"id": agent["pane_id"], "name": agent.get("name") or agent["pane_id"], "kind": agent.get("agent") or "agent",
                "task": task(agent, labels.get(agent["pane_id"])), "state": state, "card": None}
        if state == "waiting":
            try:
                screen = screen_text(herdr("agent", "read", agent["pane_id"], "--source", "visible", machine=machine))
            except HerdrError:
                screen = ""
            if screen:
                item["card"] = {"kind": "question", "id": hashlib.sha256(screen.encode()).hexdigest()[:16], "screen": screen}
        elif state == "done" and agent.get("completion_seq") is not None and not handed_off(agent, now):
            item["card"] = {"kind": "result", "id": f"{agent['pane_id']}:{agent['completion_seq']}"}
            # Session files are this machine's: another machine's agent's result is only heard.
            text = result_start(agent) if machine is None else None
            if text:
                item["card"]["text"] = text
        items.append((joined(machine, agent.get("workspace_id", "")), item))
    return items


def items(context=None):
    if context is not None:
        return {"items": [item for _, item in agent_items(None, context)]}
    return {"items": [{**item, "context": context} for context, item in agent_items(None)]}


def realert(pane):
    # The pane is the plugin's to report again: its next finish or question is an alert, not a hand-off. Its agent may
    # have been marked by name too.
    try:
        name = (herdr_json("agent", "get", pane).get("agent") or {}).get("name")
    except (HerdrError, ValueError, AttributeError):
        name = None
    for marker in [m for each in filter(None, (pane, name)) for m in marks(each)]:
        marker.unlink(missing_ok=True)
    plugins = herdr_json("plugin", "list", "--json")["plugins"]
    root = next((p.get("plugin_root") for p in plugins if p.get("plugin_id") == "herdr-voice.desk-alerts"), None)
    if not root:
        raise HerdrError("the desk-alerts plugin isn't installed: the next question shows when Vux next looks")
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
        elif len(argv) == 3 and argv[1] == "mark":
            mark(argv[2])
        else:
            print(__doc__.strip(), file=sys.stderr)
            return 2
    except (HerdrError, ValueError, KeyError, TypeError, OSError) as error:
        print(f"vux_state: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
