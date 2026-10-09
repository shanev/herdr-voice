#!/usr/bin/env python3
"""Herdr event hook: tells Hermes when a coding agent at the desk finishes or stops on a question.

On `pane.agent_status_changed`, an agent that goes from working to done or blocked while the user
is away from the computer becomes a turn in the Hermes session `herdr-desk-agents` ("Desk agents").
Hermes says what happened in a sentence or two, and a client watching that session (Hark) tells
the user. Agents herdr-voice handed off are skipped: Hermes already waits for those.

Settings, all optional, in this plugin's config dir `.env` (`herdr plugin config-dir
herdr-voice.desk-alerts`): AWAY_AFTER_MINUTES (default 5; 0 alerts even at the desk),
HERMES_API_URL and HERMES_API_KEY (default: Hermes' own API_SERVER_PORT and API_SERVER_KEY in
~/.hermes/.env). Stdlib only.
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

SESSION_ID = "herdr-desk-agents"
SESSION_TITLE = "Desk agents"
# How every alert turn starts; Hark hides these turns' input and reports the replies.
ALERT_PREFIX = "[Desk agent alert]"
# herdr-voice writes a file here, named for the pane id or agent name, when it hands a task off.
HANDED_OFF = Path.home() / ".cache" / "herdr-voice" / "handed-off"
HAND_OFF_TTL = 12 * 60 * 60
DEFAULT_AWAY_MINUTES = 5
DEFAULT_PORT = 8642
# Claude can end a reply while a command it started is still running: done, then working again.
# An alert waits this long and is dropped if the agent went back to work.
DEFAULT_SETTLE_SECONDS = 20


def is_alert(previous, status):
    """Only the end of a stretch of work is news; startup, focus and idle changes aren't."""
    return previous == "working" and status in ("done", "blocked")


def record_status(path, pane, status):
    """Saves the pane's status and returns the one before it, and this event's number for the pane.
    Hooks can run at the same time."""
    with _statuses(path) as statuses:
        entry = statuses.get(pane) or {}
        statuses[pane] = {"status": status, "event": entry.get("event", 0) + 1}
        return entry.get("status"), statuses[pane]["event"]


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
    """True when herdr-voice handed this agent's task off (it reports that one itself). The mark is
    used up, so the agent's next task from the desk is alerted."""
    handed_off = False
    for name in filter(None, names):
        marker = folder / name.replace("/", "_")
        try:
            handed_off |= now - marker.stat().st_mtime < HAND_OFF_TTL
            marker.unlink()
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


def settings(config_dir, hermes_env):
    own, hermes = read_env_file(Path(config_dir) / ".env"), read_env_file(hermes_env)
    port = hermes.get("API_SERVER_PORT") or DEFAULT_PORT
    def number(key, default):
        try:
            return float(own.get(key, default))
        except ValueError:
            return default
    return {
        "url": (own.get("HERMES_API_URL") or f"http://127.0.0.1:{port}").rstrip("/"),
        "key": own.get("HERMES_API_KEY") or hermes.get("API_SERVER_KEY") or "",
        "away_after_minutes": number("AWAY_AFTER_MINUTES", DEFAULT_AWAY_MINUTES),
        "settle_seconds": number("SETTLE_SECONDS", DEFAULT_SETTLE_SECONDS),
    }


def where(labels):
    """Where the agent is, the way herdr's sidebar shows it: workspace, then tab."""
    workspace, tab = labels
    return f'workspace "{workspace}", tab "{tab}"' if tab else f'workspace "{workspace}"'


def alert_message(agent, labels, status, words):
    kind = agent.get("agent") or "coding"
    what = "finished its task" if status == "done" else "stopped and is waiting for an answer"
    source = "Its last reply" if status == "done" else "Its screen"
    return (
        f"{ALERT_PREFIX} The {kind} agent in herdr {where(labels)} (pane {agent.get('pane_id')}, "
        f"folder {agent.get('cwd')}) {what} while the user was away. {source}:\n\n{words.strip()}\n\n"
        "Tell the user in a sentence or two, the way you'd say it out loud: name the agent the way "
        "herdr's sidebar shows it, then what it did, or the question it's asking. Don't answer it or "
        "send it anything; the user decides."
    )


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


def labels(agent):
    """The agent's workspace and tab labels. A hook's own context describes the focused pane, which
    may be another one."""
    def label(kind, key):
        code, out = herdr(kind, "get", agent.get(key) or "")
        try:
            return json.loads(out)["result"][kind]["label"] if code == 0 else None
        except (json.JSONDecodeError, KeyError, TypeError):
            return None
    return label("workspace", "workspace_id") or agent.get("workspace_id") or "?", label("tab", "tab_id")


def last_words(pane, status):
    if status == "done":
        for script in sorted(Path.home().glob(".hermes/skills/*/herdr-voice/scripts/last_reply.py")):
            result = subprocess.run([sys.executable, str(script), pane], capture_output=True, text=True, timeout=20)
            if result.returncode == 0 and result.stdout.strip():
                return result.stdout[-6000:]
        _, screen = herdr("agent", "read", pane, "--source", "recent-unwrapped", "--lines", "80")
    else:
        _, screen = herdr("agent", "read", pane, "--source", "visible")
    return screen[-6000:]


def post(url, key, body, timeout):
    request = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                     headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def alert_when_settled(config, pane, status, statuses, event):
    """Once the status has held for the settle time (no newer event for the pane), reads what the
    agent said and runs the alert turn. Runs detached from the hook: settling and the turn take a while."""
    time.sleep(config["settle_seconds"])
    if latest_event(statuses, pane) != event:
        return
    agent = running_agent(pane)
    if agent is None or agent.get("agent_status") != status:
        return
    deliver(config, alert_message(agent, labels(agent), status, last_words(pane, status)))


def deliver(config, message):
    """Runs the alert turn in the Desk agents session, creating the session the first time."""
    try:
        post(f"{config['url']}/api/sessions", config["key"], {"id": SESSION_ID, "title": SESSION_TITLE}, 30)
    except urllib.error.HTTPError as error:
        if error.code != 409:
            raise
    post(f"{config['url']}/api/sessions/{SESSION_ID}/chat", config["key"], {"message": message}, 600)


def main():
    if sys.argv[1:] == ["deliver"]:
        payload = json.load(sys.stdin)
        alert_when_settled(payload["config"], payload["pane"], payload["status"], Path(payload["statuses"]),
                           payload["event"])
        return
    event = json.loads(os.environ.get("HERDR_PLUGIN_EVENT_JSON") or "{}").get("data") or {}
    pane, status = event.get("pane_id"), event.get("agent_status")
    if not pane or not status:
        return
    state = Path(os.environ.get("HERDR_PLUGIN_STATE_DIR") or Path.home() / ".local/state/herdr-voice-desk-alerts")
    statuses = state / "statuses.json"
    previous, event_number = record_status(statuses, pane, status)
    if not is_alert(previous, status):
        return
    agent = running_agent(pane)
    if agent is None or take_hand_off([pane, agent.get("name")], time.time()):
        return
    config = settings(os.environ.get("HERDR_PLUGIN_CONFIG_DIR") or state, Path.home() / ".hermes" / ".env")
    if not config["key"]:
        print("desk-alerts: no Hermes API key (API_SERVER_KEY in ~/.hermes/.env)", file=sys.stderr)
        return
    ioreg = subprocess.run(["ioreg", "-c", "IOHIDSystem"], capture_output=True, text=True).stdout \
        if sys.platform == "darwin" else ""
    if not is_away(idle_seconds(ioreg), config["away_after_minutes"]):
        return
    with open(state / "deliver.log", "a", encoding="utf-8") as log:
        child = subprocess.Popen([sys.executable, __file__, "deliver"], stdin=subprocess.PIPE, stdout=log,
                                 stderr=log, start_new_session=True, text=True)
        child.stdin.write(json.dumps({"config": config, "pane": pane, "status": status,
                                     "statuses": str(statuses), "event": event_number}))
        child.stdin.close()


if __name__ == "__main__":
    main()
