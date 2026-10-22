#!/usr/bin/env python3
"""Herdr event hook: tells Vux's agents when a coding agent at the desk finishes or stops on a question.

Vux doesn't poll the agent: every change it shows (an agent detected or gone, each status change, a pane closed,
exited or moved, a workspace made, closed, renamed or moved) is reported at once with `"push": false`, for Vux's open
event stream only. On `pane.agent_status_changed`, an agent that goes from working to done or blocked while the user
is away from the computer is also news for a push, once settled. The hook tells the agents (Hermes with Vux's vux-auth
plugin, or hark-auth 1.8 or later from before Hark became Vux, and Vux's own agent, vux-agent) that the agent's
workspace changed: `POST /v1/skills/herdr-voice/changed` with the workspace and pane. Every agent on the machine is
told at once (Hermes and vux-agent each serve Vux a stream); each pushes only to its own phones. The agent reads
the workspace's agents with the skill's `scripts/vux_state.py` and pushes the change to the user's phone, with the
question card of a blocked agent. Agents herdr-voice handed off are skipped: Hermes already waits for those.

A question stays on the user's phone until its pane moves on: the agent was answered (at the desk or through Vux) and
went back to work, quit, or its pane closed or moved. Then the hook reports the change again, and the agent withdraws
the question.

Every agent gets a herdr agent name, once: herdr-voice names those it starts by voice, and this hook names an agent
started at the desk from its first task (`herdr agent rename`, by the skill's rule in agent_names.py): at any pane
event, each agent without a name whose terminal title shows a task. One that starts working untitled is looked at again
for a while (`desk_alert.py name <pane>`, detached). A named agent is never renamed. Pane and tab labels are neither
read nor written: they're the user's and other plugins' (herdr.auto-title).

`desk_alert.py realert <pane>` alerts again for an agent still blocked after an answer, when its dialog moved on to
another question.

Settings, all optional, in this plugin's config dir `.env` (`herdr plugin config-dir herdr-voice.desk-alerts`):
AWAY_AFTER_MINUTES (default 5; 0 alerts even at the desk), SETTLE_SECONDS (default 20), HERMES_API_URL and
HERMES_API_KEY (an agent to tell first, besides Hermes (API_SERVER_PORT and API_SERVER_KEY in ~/.hermes/.env) and
vux-agent (PORT and key in ~/.vux-agent, or ~/.hark-agent from before Hark became Vux), each told when it's there).
Stdlib only.
"""
import fcntl
import json
import os
import re
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

# How the skill names agents (its scripts/agent_names.py), from the repo herdr installs this plugin from, where
# plugins/desk-alerts sits beside skills/herdr-voice: one rule for both. No bytecode is written there (herdr's checkout
# of the repo stays as installed).
sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "skills" / "herdr-voice" / "scripts"))
import agent_names  # noqa: E402

# The skill whose items these are (its SKILL.md declares them for Vux).
SKILL = "herdr-voice"
# herdr-voice writes a file here, named for the pane id or agent name, when it hands a task off (its
# `scripts/vux_state.py mark`), and clears the pane's when it has the plugin alert again (`realert`).
HANDED_OFF = Path.home() / ".cache" / "herdr-voice" / "handed-off"
HAND_OFF_TTL = 12 * 60 * 60
# A finish this soon after a handed-off one is the same task (Claude ended a reply, worked on, ended again).
SAME_TASK_SECONDS = 10 * 60
DEFAULT_AWAY_MINUTES = 5
DEFAULT_PORT = 8642
# Vux's own agent, and where it lived while Vux was called Hark: the first of them with a key is the one.
VUX_AGENT_HOMES = (Path.home() / ".vux-agent", Path.home() / ".hark-agent")
DEFAULT_VUX_AGENT_PORT = 8650
# Claude can end a reply while a command it started is still running: done, then working again.
# An alert waits this long and is dropped if the agent went back to work.
DEFAULT_SETTLE_SECONDS = 20


# An agent started at the desk is named once its first task's title shows. Claude titles its terminal a moment after the
# prompt, so an agent that starts working untitled is looked at again this often, for this long.
NAME_EVERY_SECONDS = 2
NAME_WAIT_SECONDS = 120


# Changes to which workspaces there are: Vux's list of contexts.
WORKSPACE_EVENTS = {"workspace_created", "workspace_closed", "workspace_renamed", "workspace_moved", "workspace_reordered"}


def is_alert(previous, status):
    """Only the end of a stretch of work is news; startup, focus and idle changes aren't."""
    return previous == "working" and status in ("done", "blocked")


def is_resolved(previous, status):
    """The pane's question was answered, or its agent quit (an exit reports `done`)."""
    return previous == "blocked" and status != "blocked"


def gone_pane(event):
    """The pane id a closed, exited or moved pane had; None for any other event."""
    kind = event.get("type")
    if kind in ("pane_closed", "pane_exited"):
        return event.get("pane_id")
    if kind == "pane_moved":
        # Answers name the old id, which no longer reaches the agent.
        return event.get("previous_pane_id")
    return None


def forget_pane(path, pane):
    """Drops a pane that's gone and returns its last status and workspace."""
    with _statuses(path) as statuses:
        entry = statuses.pop(pane, None) or {}
        return entry.get("status"), entry.get("workspace")


def record_status(path, pane, status, workspace=None):
    """Saves the pane's status (and workspace, when known) and returns the one before it, and this event's number for
    the pane. Hooks can run at the same time."""
    with _statuses(path) as statuses:
        entry = statuses.get(pane) or {}
        statuses[pane] = {"status": status, "event": entry.get("event", 0) + 1,
                          "workspace": workspace or entry.get("workspace")}
        return entry.get("status"), statuses[pane]["event"]


def remember_workspace(path, pane, workspace):
    """Saves the pane's workspace, for when it's gone; not an event of its own (a settling alert checks those)."""
    with _statuses(path) as statuses:
        if pane in statuses:
            statuses[pane]["workspace"] = workspace


def recorded_workspace(path, pane):
    with _statuses(path) as statuses:
        return (statuses.get(pane) or {}).get("workspace")


def latest_event(path, pane):
    with _statuses(path) as statuses:
        return (statuses.get(pane) or {}).get("event")


class _statuses:
    """statuses.json, locked, as {pane: {"status", "event"}}; changes are saved on exit."""
    def __init__(self, path):
        self.path = path

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.handle = open(self.path, "a+", encoding="utf-8")
        fcntl.flock(self.handle, fcntl.LOCK_EX)
        self.handle.seek(0)
        try:
            self.statuses = json.loads(self.handle.read() or "{}")
        except json.JSONDecodeError:
            self.statuses = {}
        return self.statuses

    def __exit__(self, *_):
        self.handle.seek(0)
        self.handle.truncate()
        json.dump(self.statuses, self.handle)
        self.handle.close()


def take_hand_off(names, now, folder=HANDED_OFF):
    """True when herdr-voice handed this agent's task off (it reports that one itself). The mark is used up: it becomes
    `<name>.finished`, which tells the skill's vux_state.py this finish is Hermes' to tell, until the agent's next task
    from the desk finishes, which is alerted (and drops it)."""
    handed_off = False
    for name in filter(None, names):
        marker = folder / name.replace("/", "_")
        finished = marker.with_name(marker.name + ".finished")
        try:
            fresh = now - marker.stat().st_mtime < HAND_OFF_TTL
            handed_off |= fresh
            if fresh:
                marker.replace(finished)
            else:
                marker.unlink()
        except FileNotFoundError:
            pass
    if not handed_off:
        for name in filter(None, names):
            finished = folder.joinpath(name.replace("/", "_") + ".finished")
            try:
                if now - finished.stat().st_mtime < SAME_TASK_SECONDS:
                    # The same handed-off task finishing again: still Hermes' to tell.
                    os.utime(finished, (now, now))
                    handed_off = True
                else:
                    finished.unlink()
            except FileNotFoundError:
                pass
    return handed_off


def idle_seconds(ioreg_output):
    """Seconds since the last keyboard or mouse input, from `ioreg -c IOHIDSystem`."""
    match = re.search(r'"HIDIdleTime" = (\d+)', ioreg_output or "")
    return int(match.group(1)) / 1e9 if match else None


def is_away(idle, away_after_minutes):
    # Without an idle time (not macOS), every alert is sent.
    return idle is None or idle >= away_after_minutes * 60


def read_env_file(path):
    values = {}
    try:
        lines = Path(path).read_text(encoding="utf-8").splitlines()
    except OSError:
        return values
    for line in lines:
        key, sep, value = line.strip().removeprefix("export ").partition("=")
        if sep and key and not key.startswith("#"):
            values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def read_key(path):
    try:
        return Path(path).read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def settings(config_dir, hermes_env, vux_agent=None):
    """Every agent on the machine, each serving Vux its own stream: one named in this plugin's `.env` first, then
    Hermes, then vux-agent, each once."""
    own, hermes = read_env_file(Path(config_dir) / ".env"), read_env_file(hermes_env)
    agents = []
    hermes_key = hermes.get("API_SERVER_KEY") or ""
    if own.get("HERMES_API_URL") and (own.get("HERMES_API_KEY") or hermes_key):
        agents.append({"url": own["HERMES_API_URL"], "key": own.get("HERMES_API_KEY") or hermes_key})
    if hermes_key:
        agents.append({"url": "http://127.0.0.1:{}".format(hermes.get("API_SERVER_PORT") or DEFAULT_PORT), "key": hermes_key})
    homes = [Path(vux_agent)] if vux_agent is not None else list(VUX_AGENT_HOMES)
    vux_agent = next((home for home in homes if read_key(home / "agent-key")), homes[0])
    agent_key = read_key(vux_agent / "agent-key")
    if agent_key:
        agents.append({"url": "http://127.0.0.1:{}".format(
            read_env_file(Path(vux_agent) / "env").get("PORT") or DEFAULT_VUX_AGENT_PORT), "key": agent_key})
    seen = set()
    agents = [a for a in ({"url": a["url"].rstrip("/"), "key": a["key"]} for a in agents)
              if not (a["url"] in seen or seen.add(a["url"]))]
    def number(key, default):
        try:
            return float(own.get(key, default))
        except ValueError:
            return default
    return {
        "agents": agents,
        "away_after_minutes": number("AWAY_AFTER_MINUTES", DEFAULT_AWAY_MINUTES),
        "settle_seconds": number("SETTLE_SECONDS", DEFAULT_SETTLE_SECONDS),
    }


def herdr(*args):
    binary = os.environ.get("HERDR_BIN_PATH") or "herdr"
    result = subprocess.run([binary, *args], capture_output=True, text=True, timeout=20)
    return result.returncode, result.stdout, result.stderr


def herdr_error(err):
    """The error code herdr prints on failure (`{"error": {"code": ...}}`), or None."""
    try:
        return json.loads(err)["error"]["code"]
    except (json.JSONDecodeError, KeyError, TypeError):
        return None


def running_agent(pane):
    """The agent in the pane, or None once it has quit (an exit also reports `done`)."""
    code, out, _ = herdr("agent", "get", pane)
    try:
        return json.loads(out)["result"]["agent"] if code == 0 else None
    except (json.JSONDecodeError, KeyError, TypeError):
        return None


class HerdrFailed(Exception):
    pass


def herdr_json(*args):
    code, out, err = herdr(*args)
    if code != 0:
        raise HerdrFailed(f"`herdr {' '.join(args)}` failed: {err.strip()[:300]}")
    try:
        return json.loads(out)["result"]
    except (json.JSONDecodeError, KeyError, TypeError):
        raise HerdrFailed(f"`herdr {' '.join(args)}` printed no result") from None


def name_agents(state, skip=()):
    """Gives every agent without a herdr agent name one, once it has a task (its terminal title, cleaned as the skill's
    agent_names.task_title cleans it, says what it's on): the name the skill's rule makes of the task, unique among
    herdr's live agents. An agent with a name, the user's, the voice recipe's or this one's, is never renamed: a name
    stays for the agent's life. Pane labels aren't read or written.

    Hooks run at once for herdr's events, so the look and the rename happen under a lock: a second hook sees the name
    the first gave. Panes in `skip` (one whose agent was only just detected, its title maybe still the shell's) wait for
    a later event. Returns the agents it named, as (workspace, pane, name), and the panes of unnamed agents that have
    no task yet. herdr failing is reported and names nothing: alerts go on without it."""
    try:
        with _lock(state / "naming.lock"):
            agents = herdr_json("agent", "list")["agents"]
            unnamed = [a for a in agents if not a.get("name") and a.get("agent") and a.get("pane_id") not in skip
                       and a.get("agent_status") != "unknown"]
            if not any(agent_names.task_title(a) for a in unnamed):
                return [], [a["pane_id"] for a in unnamed]
            labels = {w["workspace_id"]: w.get("label") for w in herdr_json("workspace", "list")["workspaces"]}
            taken = {a["name"] for a in agents if a.get("name")}
            named, untitled = [], []
            for agent in unnamed:
                base = agent_names.base_name(agent, labels.get(agent.get("workspace_id")))
                if base is None:
                    untitled.append(agent["pane_id"])
                    continue
                given = rename(agent["pane_id"], base, taken)
                if given:
                    named.append((agent.get("workspace_id"), agent["pane_id"], given))
            return named, untitled
    except (HerdrFailed, OSError, subprocess.SubprocessError) as error:
        print(f"desk-alerts: agents not named: {error}", file=sys.stderr)
        return [], []


def rename(pane, base, taken):
    """Names the pane's agent `base`, or `base-2`, `base-3`… : the first no live agent has, by `taken` and then by herdr
    (names are unique among the server's live agents, and one may have been named since the list). Returns the name
    given, or None when herdr refuses otherwise (the agent quit)."""
    for _ in range(20):
        name = agent_names.unique(base, taken)
        code, _, err = herdr("agent", "rename", pane, name)
        if code == 0:
            taken.add(name)
            return name
        if herdr_error(err) != "agent_name_taken":
            print(f"desk-alerts: {pane} not named {name}: {err.strip()[:300]}", file=sys.stderr)
            return None
        taken.add(name)
    print(f"desk-alerts: {pane} not named: every name from {base} to {name} is taken", file=sys.stderr)
    return None


class _lock:
    """An exclusive lock on a file, for the length of a with block."""
    def __init__(self, path):
        self.path = path

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.handle = open(self.path, "a", encoding="utf-8")
        fcntl.flock(self.handle, fcntl.LOCK_EX)

    def __exit__(self, *_):
        self.handle.close()


def name_when_titled(state, config, pane, sleep=time.sleep):
    """For an unnamed agent that started working before its title said what on: looks again every
    NAME_EVERY_SECONDS, for up to NAME_WAIT_SECONDS, and names it once the title shows (or stops when it's gone or
    named). Runs detached from the hook. Vux hears of each agent named."""
    deadline = time.monotonic() + NAME_WAIT_SECONDS
    while time.monotonic() < deadline:
        sleep(NAME_EVERY_SECONDS)
        named, untitled = name_agents(state)
        report_named(config, named)
        if pane not in untitled:
            return


def report_named(config, named, already=None):
    """Tells Vux's agents about agents just named (each a changed item, for the open stream), but the one `already`
    reported."""
    for workspace, pane, _ in named:
        if pane != already:
            changed(config, workspace, pane, push=False)


def post(url, key, body, timeout):
    request = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                     headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def alert_when_settled(config, pane, status, statuses, event):
    """Once the status has held for the settle time (no newer event for the pane), tells the agent. Runs detached
    from the hook: settling takes a while."""
    time.sleep(config["settle_seconds"])
    if latest_event(statuses, pane) != event:
        return
    agent = running_agent(pane)
    if agent is None or agent.get("agent_status") != status:
        return
    changed(config, agent.get("workspace_id"), pane)


def changed(config, workspace, pane=None, push=True):
    """Tells Vux's agents the workspace changed (the pane's agent in it, if given), or, without a workspace, which
    workspaces there are: each reads them again and sends them to Vux's open stream, or, while Vux is closed, pushes
    or withdraws what's new. `push=False` is for the stream only (the agent still withdraws a question that's over).
    Every agent on the machine is told, at once, since each serves Vux its own stream: a slow one holds up none of the
    others. Each may push: an agent pushes only to the phones paired with it. An agent without skill state (404), or one
    that's down, is skipped, and the rest are still told."""
    if pane and not workspace:
        return
    body = {**({"context": workspace} if workspace else {}), **({"item": pane} if pane else {}),
            **({} if push else {"push": False})}

    def tell(agent):
        try:
            post(f"{agent['url']}/v1/skills/{SKILL}/changed", agent["key"], body, 60)
        except (urllib.error.URLError, OSError) as error:
            print(f"desk-alerts: {workspace or 'workspaces'} {pane or ''} not reported to {agent['url']}: {error}", file=sys.stderr)

    threads = [threading.Thread(target=tell, args=(agent,)) for agent in config["agents"]]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()


def main():
    if len(sys.argv) == 3 and sys.argv[1] == "realert":
        realert(sys.argv[2])
        return
    if sys.argv[1:] == ["deliver"]:
        payload = json.load(sys.stdin)
        alert_when_settled(payload["config"], payload["pane"], payload["status"], Path(payload["statuses"]),
                           payload["event"])
        return
    if len(sys.argv) == 3 and sys.argv[1] == "name":
        state = state_dir()
        name_when_titled(state, config_for(state), sys.argv[2])
        return
    event = json.loads(os.environ.get("HERDR_PLUGIN_EVENT_JSON") or "{}").get("data") or {}
    state = state_dir()
    statuses = state / "statuses.json"
    # Vux doesn't poll: every change it shows is reported at once, for its open stream (`push=False`). Only a question
    # that's over (to withdraw it) and a settled alert (below) may push.
    kind = event.get("type")
    if kind in WORKSPACE_EVENTS:
        changed(config_for(state), None, push=False)
        return
    if kind == "pane_agent_detected":
        # Desk agents get their names at any event once they have a task; this one's title may still be its shell's.
        config = config_for(state)
        named, _ = name_agents(state, skip={event.get("pane_id")})
        report_named(config, named)
        changed(config, event.get("workspace_id"), event.get("pane_id"), push=False)
        return
    gone = gone_pane(event)
    if gone:
        status, workspace = forget_pane(statuses, gone)
        if status is not None:
            changed(config_for(state), event.get("workspace_id") or event.get("previous_workspace_id") or workspace, gone,
                    push=status == "blocked")
        return
    pane, status = event.get("pane_id"), event.get("agent_status")
    if not pane or not status:
        return
    previous, event_number = record_status(statuses, pane, status, event.get("workspace_id"))
    if previous == status:
        return
    config = config_for(state)
    named, untitled = name_agents(state)
    report_named(config, named, already=pane)
    if status == "working" and pane in untitled:
        name_detached(state, pane)
    resolved, alert = is_resolved(previous, status), is_alert(previous, status)
    agent = running_agent(pane) if resolved or alert else None
    workspace = event.get("workspace_id") or (agent or {}).get("workspace_id") or recorded_workspace(statuses, pane)
    if not workspace and agent is None:
        workspace = (running_agent(pane) or {}).get("workspace_id")
    if workspace:
        remember_workspace(statuses, pane, workspace)
    changed(config, workspace, pane, push=resolved)
    if not alert or agent is None or take_hand_off([pane, agent.get("name")], time.time()):
        return
    if not config["agents"]:
        print("desk-alerts: no agent API key (API_SERVER_KEY in ~/.hermes/.env, or ~/.vux-agent/agent-key)", file=sys.stderr)
        return
    ioreg = subprocess.run(["ioreg", "-c", "IOHIDSystem"], capture_output=True, text=True).stdout \
        if sys.platform == "darwin" else ""
    if not is_away(idle_seconds(ioreg), config["away_after_minutes"]):
        return
    deliver_detached(state, config, pane, status, event_number)


def state_dir():
    return Path(os.environ.get("HERDR_PLUGIN_STATE_DIR") or Path.home() / ".local/state/herdr-voice-desk-alerts")


def config_for(state):
    return settings(os.environ.get("HERDR_PLUGIN_CONFIG_DIR") or state, Path.home() / ".hermes" / ".env")


def deliver_detached(state, config, pane, status, event_number):
    """Settles and delivers in a child of its own: the hook, or Hermes' command, returns now."""
    with open(state / "deliver.log", "a", encoding="utf-8") as log:
        child = subprocess.Popen([sys.executable, __file__, "deliver"], stdin=subprocess.PIPE, stdout=log,
                                 stderr=log, start_new_session=True, text=True)
        child.stdin.write(json.dumps({"config": config, "pane": pane, "status": status,
                                     "statuses": str(state / "statuses.json"), "event": event_number}))
        child.stdin.close()


def name_detached(state, pane):
    """Names the pane's agent once its title shows (`name_when_titled`), in a child of its own: the hook returns now."""
    with open(state / "deliver.log", "a", encoding="utf-8") as log:
        subprocess.Popen([sys.executable, __file__, "name", pane], stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                         start_new_session=True)


def realert(pane):
    """Run by Hermes after answering a question when the agent's dialog shows another: that one comes
    as an alert of its own, with its card, once the agent has held still. herdr reports no status
    change between two questions of one dialog, so the hook alone never would."""
    state = state_dir()
    config = config_for(state)
    if not config["agents"]:
        sys.exit("desk-alerts: no agent API key (API_SERVER_KEY in ~/.hermes/.env, or ~/.vux-agent/agent-key)")
    deliver_detached(state, config, pane, "blocked", latest_event(state / "statuses.json", pane))
    print("The next question will come as a new alert.")


if __name__ == "__main__":
    main()
