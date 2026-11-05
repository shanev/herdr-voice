#!/usr/bin/env python3
"""The Herdr app's spaces and their agents, for Vux: the skill state its SKILL.md declares (in metadata.vux, and the
same in metadata.hark for Hark-era clients). It reads herdr-core (`hc`) through its socket.

Usage: vux_state.py contexts
       vux_state.py create <repo-or-name>
       vux_state.py items [space]
       vux_state.py space <repo_path>
       vux_state.py folder <space>
       vux_state.py mark <terminal>
       vux_state.py realert <terminal>
       vux_state.py reply <terminal>
       vux_state.py sessions

contexts: this machine's Herdr spaces, as {"contexts": [{"id", "name", "location"}]}, the location its host name. The
Herdr app keeps them in the core's `herdr-app` document; a terminal labelled with a space the document doesn't have yet
(started while the window was closed) is in a space too: Herdr makes it when it opens. A space's name is the one given
it, else Herdr's: its first terminal's repo, else its folder; the home folder's ("~") is "Home". Another machine's
spaces are in its own core: that machine shows in Vux as a computer of its own.

create: the space for a repo (found as ~/<repo>, ~/github.com/*/<repo> or ~/*/<repo>, or a path): its existing one,
else a new one with a shell in the repo's folder, made through hc so it works with Herdr's window closed (Herdr shows
it when it opens). Herdr names a space after its folder, so a name that's no repo is only found, never made. Prints
{"context": {...}}.

items: the agents in a space, or in every space each with its "context", as {"items": [...]}, in item format version 2
(metadata.vux.items.version). An agent is a terminal in a Herdr space (its `herdr.space` label) whose program has a
status (OSC 7501) now, as Herdr counts agents: a plain shell isn't one, nor is an agent that never reports (pi,
cursor-agent), one still on its first-launch folder-trust dialog (Claude Code and Codex report nothing there), or one
that quit back to its shell (it clears its status). A terminal is in the space its `herdr.space` label names; a
sub-agent another agent started with `hc run`, unlabelled, is in its nearest labelled ancestor's, as Herdr shows it in
its parent's tab.
  id     the terminal's address (t7).
  name   its hc name, else its address. hc names a terminal only when it starts (`hc run --name`) and can't rename
         one: a terminal started from Herdr's window has none, and this script never makes one up.
  kind   the agent the terminal reports (its 7501 `app`) as people write it: Claude, Codex, else the word
         capitalized; "Agent" when it says none.
  task   the name of its Herdr tab when someone gave it one, else its terminal title, else "New session".
  state  its status as working, waiting (blocked on a question or a permission), done, failed (error) or idle.
  card   for a blocked agent, a question card with its screen (Vux reads the question from it); for a done agent whose
         finish no one has told the user yet, a result card with the start of its last reply (Claude Code and Codex);
         else null. An agent the skill handed off (`mark`) gets no result card: the agent running the skill reports it.

space: the space a repo's new sessions go in, as {"space", "new", "busy"}: the space of an agent working in the repo
(its folder is the repo or inside it), else the space named for the repo, else a new space id (`new` true) for the
first terminal to bring. `busy` is the agents in the repo's own checkout (not its worktrees).

folder: the folder a new terminal in the space starts in, as Herdr starts one: where the space's first terminal is
now, else the folder the space was made in.

mark: marks the terminal's task as handed off: the agent running this skill reports its finish, so the watcher
(watch.py) doesn't push it and items give it no result card. The mark is the terminal's `herdr-core-voice.handed-off`
label. realert: clears the mark and has the watcher alert again, for an agent whose dialog moved on to another question
after an answer (its status doesn't change between two questions).

reply: the terminal's last reply from its agent's session file (Claude Code, Codex), via last_reply.py; exit 3 when
there's none to read that way (read the screen instead). sessions: every agent's transcript file, as
{"sessions": [{"id", "name", "path"}]}, for searching what each worked on.

Stdlib only.
"""
import hashlib
import json
import secrets
import socket
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

# The scripts beside this one, however it's loaded, without writing bytecode beside them: Hermes calls a new file in a
# skill's folder a local edit and won't update the skill over it.
sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
import core  # noqa: E402
from core import CoreError  # noqa: E402
from last_reply import last_reply, READERS  # noqa: E402

STATES = {"working": "working", "blocked": "waiting", "done": "done", "idle": "idle", "error": "failed"}
# The agents as people write them, by the app word they report.
KINDS = {"claude-code": "Claude", "codex": "Codex"}
# last_reply.py's readers, by the same word.
READER_KINDS = {"claude-code": "claude", "codex": "codex"}
RESULT_TEXT = 300
NO_TASK = "New session"
NO_REPLY = 3
# Claude Code reports `done` a moment (about 0.1 s) before its reply is in its transcript: for this long after a done
# report, a reply not in the file yet is waited for.
FLUSH_SECONDS = 2


def this_machine():
    return socket.gethostname().split(".")[0]


def plain(text):
    """Text without icon-font glyphs (private-use characters a phone can't show and speech would read), spaces
    folded."""
    kept = "".join(c for c in text or "" if not (0xE000 <= ord(c) <= 0xF8FF or ord(c) >= 0xF0000))
    return " ".join(kept.split())


def terminals_in(node):
    """The terminal addresses in a Herdr tab's layout tree, in order."""
    if isinstance(node, dict):
        content = node.get("content")
        if isinstance(content, dict) and isinstance(content.get("terminal"), str):
            yield content["terminal"]
        for key in ("children", "floating"):
            for child in node.get(key) or []:
                yield from terminals_in(child)


def checkout(folder):
    """The repo a folder is in (a worktree's own folder), else the folder: what Herdr calls an unnamed space after."""
    path = Path(folder)
    for candidate in (path, *path.parents):
        if (candidate / ".git").exists():
            return candidate
    return path


def folder_name(folder):
    if not folder:
        return None
    top = checkout(folder)
    return "Home" if top == Path.home() else top.name or str(top)


def live_folder(terminal):
    return (terminal.get("foreground") or {}).get("cwd") or terminal.get("cwd")


class World:
    """The Herdr document and the core's live terminals, read once."""

    def __init__(self, connection):
        self.document = connection.call("doc.get", name=core.APP_DOCUMENT).get("value") or {}
        listed = connection.call("terminal.list")["terminals"]
        self.terminals = {t["address"]: t for t in listed if core.is_local(t["address"])}
        # Where each terminal is in the document: its space and tab, in Herdr's order.
        self.places = {}
        for space in self.document.get("spaces") or []:
            for tab in space.get("tabs") or []:
                for address in terminals_in(tab.get("root")):
                    self.places.setdefault(address, (space, tab))

    def own_space(self, terminal):
        """The space the terminal's own label names: Herdr gives it a pane there."""
        return (terminal.get("labels") or {}).get(core.SPACE) or None

    def space_of(self, terminal):
        """Its own space, else its nearest labelled ancestor's (a sub-agent's: Herdr keeps it in its parent's tab)."""
        seen = set()
        while terminal is not None and terminal["address"] not in seen:
            seen.add(terminal["address"])
            if self.own_space(terminal):
                return self.own_space(terminal)
            terminal = self.terminals.get(terminal.get("parent") or "")
        return None

    def spaces(self):
        """(id, name) of every space: the document's, then those only terminals' labels name yet."""
        known = []
        for space in self.document.get("spaces") or []:
            known.append((space["id"], self.space_name(space["id"], space)))
        ids = {space for space, _ in known}
        for terminal in self.ordered():
            space = self.own_space(terminal)
            if space and space not in ids:
                ids.add(space)
                known.append((space, self.space_name(space, None)))
        return known

    def space_name(self, space, saved):
        if saved and saved.get("name"):
            return saved["name"]
        first = next((t for t in self.ordered() if self.own_space(t) == space), None)
        return folder_name(live_folder(first) if first else (saved or {}).get("folder")) or f"Space {space}"

    def ordered(self):
        """Live terminals in Herdr's order (space, tab, pane), then the rest as they were made."""
        placed = [a for a in self.places if a in self.terminals]
        rest = sorted((a for a in self.terminals if a not in self.places), key=lambda a: self.terminals[a].get("created_ms") or 0)
        return [self.terminals[a] for a in placed + rest]

    def agents(self, space=None):
        return [t for t in self.ordered() if t.get("status") and t.get("state") == "live" and self.space_of(t)
                and (space is None or self.space_of(t) == space)]


def contexts():
    with core.Core() as connection:
        world = World(connection)
    here = this_machine()
    return {"contexts": [{"id": space, "name": name, "location": here} for space, name in world.spaces()]}


def find_repo(name):
    path = Path(name).expanduser()
    if ("/" in name or name.startswith("~")) and path.is_dir():
        return path
    home = Path.home()
    for candidate in [home / name, *sorted(home.glob(f"github.com/*/{name}")), *sorted(home.glob(f"*/{name}"))]:
        if candidate.is_dir():
            return candidate
    return None


def inside(folder, repo):
    folder, repo = str(folder or "").rstrip("/"), str(repo).rstrip("/")
    return folder == repo or folder.startswith(repo + "/")


def space_for(world, repo):
    """The repo's space id and whether it's new (no terminal or document has it yet), and its busy agents."""
    repo = Path(repo).expanduser().resolve()
    busy = [t for t in world.agents() if inside(live_folder(t), repo)]
    if busy:
        return world.space_of(busy[0]), False, [t["address"] for t in busy]
    named = next((space for space, name in world.spaces() if name.lower() == repo.name.lower()), None)
    if named:
        return named, False, []
    return "sp" + secrets.token_hex(16), True, []


def space(repo):
    with core.Core() as connection:
        found, new, busy = space_for(World(connection), repo)
    return {"space": found, "new": new, "busy": busy}


def folder(space):
    with core.Core() as connection:
        world = World(connection)
    first = next((t for t in world.ordered() if world.own_space(t) == space), None)
    saved = next((s for s in world.document.get("spaces") or [] if s.get("id") == space), None)
    found = live_folder(first) if first else (saved or {}).get("folder")
    if not found:
        raise CoreError(f"no space {space} here")
    return found


def create(name):
    repo = find_repo(name)
    here = this_machine()
    with core.Core() as connection:
        world = World(connection)
        if repo is None:
            same = next(((s, n) for s, n in world.spaces() if n.lower() == name.lower()), None)
            if same:
                return {"context": {"id": same[0], "name": same[1], "location": here}}
            raise CoreError(f"no repo named {name} here, and no space by that name")
        found, new, _ = space_for(world, repo)
        if not new:
            return {"context": {"id": found, "name": dict(world.spaces()).get(found) or repo.name, "location": here}}
        # A shell in the repo's folder, in the new space: Herdr shows it there, now or when its window opens.
        connection.call("terminal.run", cwd=str(repo), labels={core.SPACE: found}, keep=True, no_parent=True)
        return {"context": {"id": found, "name": folder_name(str(repo)), "location": here}}


def screen_text(screen):
    """The screen without trailing spaces and blank lines, newest 6000 characters."""
    lines = [line.rstrip() for line in screen.splitlines()]
    return "\n".join(line for line in lines if line)[-6000:]


def mark_time(value):
    """When a hand-off mark was made or last used, from its label (`<ms>` or `finished:<ms>`); None if unreadable."""
    try:
        return int(str(value).rpartition(":")[2]) / 1000
    except ValueError:
        return None


def handed_off(terminal, now):
    made = mark_time((terminal.get("labels") or {}).get(core.HANDED_OFF, ""))
    return made is not None and now - made < core.HAND_OFF_TTL


def kind_name(app):
    return KINDS.get(app) or (app[:1].upper() + app[1:] if app else "Agent")


def claude_session(terminal, home):
    """Claude Code's session id, from the file it keeps per process (~/.claude/sessions/<pid>.json): the terminal's
    foreground process, else the one hc started. A Claude that's another Claude's child session (its environment has
    CLAUDE_CODE_CHILD_SESSION: a helper an agent started with `hc run`, or any terminal of a core an agent's hc call
    started) keeps neither that file nor a transcript, so it has no reply to read here: the screen has it."""
    for pid in filter(None, ((terminal.get("foreground") or {}).get("pid"), terminal.get("pid"))):
        try:
            found = json.loads((home / ".claude" / "sessions" / f"{pid}.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if found.get("sessionId"):
            return {"kind": "id", "value": found["sessionId"]}
    return None


def codex_session(terminal, home):
    """The Codex session started in the terminal's folder since the terminal started, when there's exactly one
    (Codex records no process): two Codex sessions there since then leave it unknown, never a guess."""
    started = (terminal.get("created_ms") or 0) / 1000
    folders = {terminal.get("cwd"), live_folder(terminal)} - {None}
    day, found = datetime.fromtimestamp(started).date() - timedelta(days=1), []
    while day <= datetime.now().date():
        for path in (home / ".codex" / "sessions").glob(day.strftime("%Y/%m/%d") + "/rollout-*.jsonl"):
            if path.stat().st_mtime < started:
                continue
            try:
                with path.open(encoding="utf-8") as lines:
                    meta = json.loads(lines.readline()).get("payload") or {}
            except (OSError, ValueError):
                continue
            begun = meta.get("timestamp")
            if meta.get("cwd") in folders and meta.get("source") == "cli" and begun and \
                    datetime.fromisoformat(begun.replace("Z", "+00:00")).timestamp() >= started:
                found.append(path)
        day += timedelta(days=1)
    return {"kind": "path", "value": str(found[0])} if len(found) == 1 else None


SESSIONS = {"claude": claude_session, "codex": codex_session}


def as_agent(terminal, home):
    """The terminal as last_reply.py takes an agent: its kind and session, when it's one with a reader."""
    reader = READER_KINDS.get((terminal.get("status") or {}).get("app"))
    session = SESSIONS[reader](terminal, home) if reader else None
    return {"agent": reader, "agent_session": session} if session else None


def transcript(terminal, home):
    agent = as_agent(terminal, home)
    if agent is None:
        return None
    find, _ = READERS[agent["agent"]]
    path = find(agent["agent_session"], home)
    return path if path is not None and path.is_file() else None


def finished_reply(terminal, home):
    """The agent's last reply from its session file, None if there's none to read there. Just after the agent reported
    `done` (FLUSH_SECONDS), a file that hasn't had its reply written yet is waited for: never longer, so a turn that
    ended without a reply (interrupted) costs nothing later."""
    agent = as_agent(terminal, home)
    if agent is None:
        return None
    text = last_reply(agent, home)
    reported = (terminal.get("status_ms") or 0) / 1000
    if text is not None or (terminal.get("status") or {}).get("state") != "done":
        return text
    path = transcript(terminal, home)
    while path is not None and time.time() < reported + FLUSH_SECONDS:
        time.sleep(0.05)
        if path.stat().st_mtime >= reported:
            text = last_reply(agent, home)
            if text is not None:
                return text
    return None


def result_start(terminal):
    try:
        reply = (finished_reply(terminal, Path.home()) or "").strip()
    except (OSError, ValueError, KeyError, TypeError):
        return None
    if len(reply) <= RESULT_TEXT:
        return reply or None
    cut = reply[:RESULT_TEXT]
    space = cut.rfind(" ")
    return (cut[:space] if space > 0 else cut).rstrip() + "…"


def items(context=None):
    now = time.time()
    with core.Core() as connection:
        world = World(connection)
        listed = []
        for terminal in world.agents(context):
            status = terminal.get("status") or {}
            state = STATES.get(status.get("state"), "idle")
            _, tab = world.places.get(terminal["address"], (None, {}))
            item = {"id": terminal["address"], "name": terminal.get("name") or terminal["address"],
                    "kind": kind_name(status.get("app")),
                    "task": plain(tab.get("name")) or plain(terminal.get("title")) or NO_TASK, "state": state, "card": None}
            if state == "waiting":
                screen = screen_text(connection.call("terminal.read", terminal=terminal["address"], lines=0)["text"])
                if screen:
                    item["card"] = {"kind": "question", "id": hashlib.sha256(screen.encode()).hexdigest()[:16], "screen": screen}
            elif state == "done" and not handed_off(terminal, now):
                item["card"] = {"kind": "result", "id": f"{terminal['address']}:{terminal.get('turn', 0)}"}
                text = result_start(terminal)
                if text:
                    item["card"]["text"] = text
            listed.append(item if context is not None else {**item, "context": world.space_of(terminal)})
    return {"items": listed}


def resolve(connection, terminal):
    """A terminal by address or name, as hc takes it."""
    return connection.call("terminal.get", terminal=terminal)


def mark(terminal):
    with core.Core() as connection:
        found = resolve(connection, terminal)
        connection.call("terminal.label", terminal=found["address"], labels={core.HANDED_OFF: str(int(time.time() * 1000))})


def realert(terminal):
    with core.Core() as connection:
        found = resolve(connection, terminal)
        # The watcher alerts for the next question, and the terminal's finish is its to report again.
        connection.call("terminal.label", terminal=found["address"],
                        labels={core.HANDED_OFF: "", core.REALERT: str(int(time.time() * 1000))})


def reply(terminal):
    with core.Core() as connection:
        found = resolve(connection, terminal)
    text = finished_reply(found, Path.home())
    if text is None:
        app = (found.get("status") or {}).get("app")
        print(f"No finished reply in a session file for this {kind_name(app) if app else 'terminal'}; read the screen.",
              file=sys.stderr)
        return NO_REPLY
    print(text)
    return 0


def sessions():
    with core.Core() as connection:
        world = World(connection)
    found = []
    for terminal in world.agents():
        path = transcript(terminal, Path.home())
        if path is not None:
            found.append({"id": terminal["address"], "name": terminal.get("name") or terminal["address"], "path": str(path)})
    return {"sessions": found}


def main(argv):
    try:
        if argv[1:] == ["contexts"]:
            print(json.dumps(contexts()))
        elif len(argv) == 3 and argv[1] == "create":
            print(json.dumps(create(argv[2])))
        elif len(argv) in (2, 3) and argv[1] == "items":
            print(json.dumps(items(*argv[2:])))
        elif len(argv) == 3 and argv[1] == "space":
            print(json.dumps(space(argv[2])))
        elif len(argv) == 3 and argv[1] == "folder":
            print(folder(argv[2]))
        elif len(argv) == 3 and argv[1] == "mark":
            mark(argv[2])
        elif len(argv) == 3 and argv[1] == "realert":
            realert(argv[2])
            print("The next question will come as a new alert.")
        elif len(argv) == 3 and argv[1] == "reply":
            return reply(argv[2])
        elif argv[1:] == ["sessions"]:
            print(json.dumps(sessions()))
        else:
            print(__doc__.strip(), file=sys.stderr)
            return 2
    except (CoreError, OSError, ValueError, KeyError, TypeError) as error:
        print(f"vux_state: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
