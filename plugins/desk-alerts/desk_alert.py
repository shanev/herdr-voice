#!/usr/bin/env python3
"""Herdr event hook: tells Hark's agent when a coding agent at the desk finishes or stops on a question.

On `pane.agent_status_changed`, an agent that goes from working to done or blocked while the user is away from the
computer is news. The hook tells the agent (Hermes with Hark's hark-auth plugin 1.8 or later, else Hark's own agent)
that the agent's workspace changed: `POST /v1/skills/herdr-voice/changed` with the workspace and pane. The agent reads
the workspace's agents with the skill's `scripts/hark_state.py` and pushes the change to the user's phone, with the
question card of a blocked agent. Agents herdr-voice handed off are skipped: Hermes already waits for those.

A question stays on the user's phone until its pane moves on: the agent was answered (at the desk or through Hark) and
went back to work, quit, or its pane closed or moved. Then the hook reports the change again, and the agent withdraws
the question.

`desk_alert.py realert <pane>` alerts again for an agent still blocked after an answer, when its dialog moved on to
another question.

Settings, all optional, in this plugin's config dir `.env` (`herdr plugin config-dir herdr-voice.desk-alerts`):
AWAY_AFTER_MINUTES (default 5; 0 alerts even at the desk), SETTLE_SECONDS (default 20), HERMES_API_URL and
HERMES_API_KEY (default: Hermes' own API_SERVER_PORT and API_SERVER_KEY in ~/.hermes/.env, else Hark's agent's PORT and
key in ~/.hark-agent). Stdlib only.
"""
import fcntl
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

# The skill whose items these are (its SKILL.md declares them for Hark).
SKILL = "herdr-voice"
# herdr-voice writes a file here, named for the pane id or agent name, when it hands a task off.
HANDED_OFF = Path.home() / ".cache" / "herdr-voice" / "handed-off"
HAND_OFF_TTL = 12 * 60 * 60
DEFAULT_AWAY_MINUTES = 5
DEFAULT_PORT = 8642
HARK_AGENT = Path.home() / ".hark-agent"
DEFAULT_HARK_AGENT_PORT = 8650
# Claude can end a reply while a command it started is still running: done, then working again.
# An alert waits this long and is dropped if the agent went back to work.
DEFAULT_SETTLE_SECONDS = 20


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
    `<name>.finished`, which tells the skill's hark_state.py this finish is Hermes' to tell, until the agent's next task
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
            folder.joinpath(name.replace("/", "_") + ".finished").unlink(missing_ok=True)
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


def settings(config_dir, hermes_env, hark_agent=HARK_AGENT):
    own, hermes = read_env_file(Path(config_dir) / ".env"), read_env_file(hermes_env)
    port = hermes.get("API_SERVER_PORT") or DEFAULT_PORT
    key = own.get("HERMES_API_KEY") or hermes.get("API_SERVER_KEY") or ""
    url = own.get("HERMES_API_URL") or f"http://127.0.0.1:{port}"
    if not key and read_key(Path(hark_agent) / "agent-key"):
        # No Hermes here: Hark's own agent, which runs the skill too.
        key = read_key(Path(hark_agent) / "agent-key")
        url = own.get("HERMES_API_URL") or "http://127.0.0.1:{}".format(
            read_env_file(Path(hark_agent) / "env").get("PORT") or DEFAULT_HARK_AGENT_PORT)
    def number(key, default):
        try:
            return float(own.get(key, default))
        except ValueError:
            return default
    return {
        "url": url.rstrip("/"),
        "key": key,
        "away_after_minutes": number("AWAY_AFTER_MINUTES", DEFAULT_AWAY_MINUTES),
        "settle_seconds": number("SETTLE_SECONDS", DEFAULT_SETTLE_SECONDS),
    }


def hand_off_command(pane):
    """What marks the agent as Hermes' to report (herdr-voice's SKILL.md runs the same)."""
    return f"mkdir -p ~/.cache/herdr-voice/handed-off && touch ~/.cache/herdr-voice/handed-off/{pane}"


def herdr(*args):
    binary = os.environ.get("HERDR_BIN_PATH") or "herdr"
    result = subprocess.run([binary, *args], capture_output=True, text=True, timeout=20)
    return result.returncode, result.stdout


def running_agent(pane):
    """The agent in the pane, or None once it has quit (an exit also reports `done`)."""
    code, out = herdr("agent", "get", pane)
    try:
        return json.loads(out)["result"]["agent"] if code == 0 else None
    except (json.JSONDecodeError, KeyError, TypeError):
        return None


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


def changed(config, workspace, pane=None):
    """Tells Hark's agent the workspace changed (the pane's agent in it, if given): it reads the workspace's agents
    again and pushes or withdraws what's new. An agent without skill state (404), or one that's down, is skipped."""
    if not config["key"] or not workspace:
        return
    body = {"context": workspace, **({"item": pane} if pane else {})}
    try:
        post(f"{config['url']}/v1/skills/{SKILL}/changed", config["key"], body, 60)
    except (urllib.error.URLError, OSError) as error:
        print(f"desk-alerts: {workspace} {pane or ''} not reported to the agent: {error}", file=sys.stderr)


def main():
    if len(sys.argv) == 3 and sys.argv[1] == "realert":
        realert(sys.argv[2])
        return
    if sys.argv[1:] == ["deliver"]:
        payload = json.load(sys.stdin)
        alert_when_settled(payload["config"], payload["pane"], payload["status"], Path(payload["statuses"]),
                           payload["event"])
        return
    event = json.loads(os.environ.get("HERDR_PLUGIN_EVENT_JSON") or "{}").get("data") or {}
    state = state_dir()
    statuses = state / "statuses.json"
    gone = gone_pane(event)
    if gone:
        status, workspace = forget_pane(statuses, gone)
        if status == "blocked":
            changed(config_for(state), event.get("workspace_id") or event.get("previous_workspace_id") or workspace, gone)
        return
    pane, status = event.get("pane_id"), event.get("agent_status")
    if not pane or not status:
        return
    previous, event_number = record_status(statuses, pane, status, event.get("workspace_id"))
    resolved = is_resolved(previous, status)
    if not resolved and not is_alert(previous, status):
        return
    agent = running_agent(pane)
    if agent is not None and agent.get("workspace_id"):
        record_status(statuses, pane, status, agent["workspace_id"])
    if resolved:
        changed(config_for(state), (agent or {}).get("workspace_id") or recorded_workspace(statuses, pane), pane)
    if not is_alert(previous, status) or agent is None or take_hand_off([pane, agent.get("name")], time.time()):
        return
    config = config_for(state)
    if not config["key"]:
        print("desk-alerts: no agent API key (API_SERVER_KEY in ~/.hermes/.env, or ~/.hark-agent/agent-key)", file=sys.stderr)
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


def realert(pane):
    """Run by Hermes after answering a question when the agent's dialog shows another: that one comes
    as an alert of its own, with its card, once the agent has held still. herdr reports no status
    change between two questions of one dialog, so the hook alone never would."""
    state = state_dir()
    config = config_for(state)
    if not config["key"]:
        sys.exit("desk-alerts: no agent API key (API_SERVER_KEY in ~/.hermes/.env, or ~/.hark-agent/agent-key)")
    deliver_detached(state, config, pane, "blocked", latest_event(state / "statuses.json", pane))
    print("The next question will come as a new alert.")


if __name__ == "__main__":
    main()
