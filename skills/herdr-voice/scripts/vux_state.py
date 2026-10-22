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

items: the agents in a workspace, or in every workspace each with its "context", as {"items": [...]}: herdr's status
as working, waiting (blocked on a question), done, idle; named as `item_name` says, often for what it's working on, with
its kind as the detail line; for a blocked agent, a question card with its screen (Vux reads the question from it); for
a done agent whose finish no one has told the user yet, a result card, with the start of its last reply (Claude Code
and Codex, on this machine). An agent herdr-voice handed off (its mark in ~/.cache/herdr-voice/handed-off) gets no
result card: Hermes reports that one in the conversation.

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
import re
import socket
import subprocess
import sys
import time
import unicodedata
from pathlib import Path

# last_reply.py beside this script, however it's loaded, without writing bytecode beside it: Hermes calls a new file in a
# skill's folder a local edit and won't update the skill over it.
sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
from last_reply import last_reply  # noqa: E402

STATES = {"working": "working", "blocked": "waiting", "done": "done", "idle": "idle"}
HANDED_OFF = Path.home() / ".cache" / "herdr-voice" / "handed-off"
HAND_OFF_TTL = 12 * 60 * 60
# The start of a result Vux shows on the agent's row (its first line or two); Hear gets the whole of it.
RESULT_TEXT = 300
# An item's name fits one line on a phone.
NAME_LENGTH = 40
# How each agent kind is written, where that isn't its kind capitalized (herdr agent start --help lists the kinds).
KIND_NAMES = {"omp": "omp", "pi": "pi", "agy": "Antigravity", "opencode": "OpenCode", "qodercli": "Qoder",
              "mastracode": "Mastra Code"}


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


def kind_name(kind):
    """An agent kind as people write it: Claude, Codex, omp."""
    return KIND_NAMES.get(kind) or (kind or "agent").capitalize()


def task_title(agent, workspace_label=None):
    """What the agent is working on: the title it gives its terminal, without the agent's own chrome around it (status
    glyphs, omp's "π > ", Grok's " - grok", Codex's " | <folder>"); "" when the title only names the agent or its folder
    (an agent that hasn't been given a task yet). Agents set it once per task, so it holds while the agent works."""
    kind = agent.get("agent") or ""
    folder = (agent.get("cwd") or "").rstrip("/").rsplit("/", 1)[-1]
    title = plain(agent.get("terminal_title_stripped") or agent.get("terminal_title"))
    # Status glyphs ahead of the title: spinners, ✳, bullets.
    title = title.lstrip("•· ").lstrip()
    while title and unicodedata.category(title[0]) == "So":
        title = title[1:].lstrip()
    title = re.sub(r"^π\s*[>›»:-]\s*", "", title)
    for tail in filter(None, {kind, kind_name(kind), folder}):
        title = re.sub(rf"\s+[-–—|·]\s+{re.escape(tail)}$", "", title, flags=re.I)
    generic = {kind, kind_name(kind), f"{kind_name(kind)} code", f"{kind} cli", folder, workspace_label or "", "~"}
    return "" if title.lower() in {g.lower() for g in generic} else title


# Words a cut name shouldn't end on: "Cybersecurity advisories and…" says less than "Cybersecurity advisories…".
DANGLING = {"a", "an", "and", "as", "at", "by", "for", "from", "in", "into", "of", "on", "or", "the", "to", "with"}


def short(text, limit=NAME_LENGTH):
    """Text cut to fit a line, at a word where there's one, with an ellipsis."""
    if len(text) <= limit:
        return text
    words = text[:limit - 1].split(" ")
    if len(words) > 1:
        # Drop the last word if it's cut through, and any small word left dangling.
        if text[limit - 1] != " ":
            words.pop()
        while len(words) > 1 and words[-1].lower() in DANGLING:
            words.pop()
    return " ".join(words).rstrip(" ,;:-–—|·") + "…"


def item_name(agent, label, title, where):
    """What Vux calls an agent, the way the skill speaks of it: the name it was started with (herdr-voice's, taken from
    the task), else its pane's label if someone renamed it, else what it's working on, else its kind and tab as herdr's
    sidebar shows them. The first two never change on their own; a title changes only with a new task."""
    kind = kind_name(agent.get("agent"))
    return (plain(agent.get("name") or label or "") or short(title)
            or (f"{kind} · {where}" if where else kind))


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
    panes = {p["pane_id"]: p for p in herdr_json("pane", "list", machine=machine)["panes"]}
    tabs = herdr_json("tab", "list", machine=machine)["tabs"]
    labels = {w["workspace_id"]: w.get("label") for w in herdr_json("workspace", "list", machine=machine)["workspaces"]}
    now = time.time()
    items = []
    for agent in agents:
        pane = panes.get(agent["pane_id"]) or {}
        # A pane's label is its terminal title (cut short) until it's renamed, by the user or a plugin such as Auto Title.
        title = pane.get("terminal_title_stripped") or ""
        label = pane.get("label") if pane.get("label") and not title.startswith(pane["label"]) else None
        siblings = [t for t in tabs if t.get("workspace_id") == agent.get("workspace_id")]
        tab = next((t for t in siblings if t["tab_id"] == agent.get("tab_id")), None)
        where = (tab.get("label") if tab and tab.get("label") and not tab["label"][:1].isdigit() else
                 str(siblings.index(tab) + 1) if tab else "")
        doing = task_title(agent, labels.get(agent.get("workspace_id")))
        name = item_name(agent, label, doing, where)
        kind = kind_name(agent.get("agent"))
        # The kind, and what it's on when the name doesn't already say so.
        detail = kind if not doing or name == short(doing) else f"{kind} · {doing}"
        state = STATES.get(agent.get("agent_status"), "idle")
        item = {"id": agent["pane_id"], "name": name, "state": state, "detail": detail}
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
    # Two agents a person couldn't tell apart (two untitled omps in one tab, two on the same task): the second is
    # "omp · 1 · 2", and so on. Names are how the user and the agent refer to agents by voice.
    seen = {}
    for context, item in items:
        key = (context, item["name"])
        seen[key] = seen.get(key, 0) + 1
        if seen[key] > 1:
            item["name"] = f"{item['name']} · {seen[key]}"
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
