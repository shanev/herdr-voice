#!/usr/bin/env python3
"""The skill's watcher (metadata.vux.watch): tells Vux's agent when the Herdr app's spaces and agents change, from
herdr-core's event stream, one line per change on stdout, the body `POST /v1/skills/herdr-core-voice/changed` takes:
{"context"?: <space id>, "item"?: <terminal address>, "push"?: false}.

Vux doesn't poll: every change it shows is reported at once with `"push": false`, for its open stream only. A terminal
in a space made, gone, finished, moved to another space or changing status is that space and item; a change to which
spaces there are (Herdr's document, a terminal labelled into a space no terminal had, a space's last terminal gone,
or a fresh subscription) is a line without a context. An agent that goes from working to done or blocked while the
user is away from the computer is also news for a push, once its status has held for the settle time (Claude can end a
reply while a command it started still runs): then the line has no `push`, and the agent reads the space's items and
pushes the change. A question that's over (answered, or its agent quit) may push too, so the agent withdraws it.

Agents the skill handed off (vux_state.py mark: the terminal's `herdr-core-voice.handed-off` label) aren't pushed:
the agent running the skill reports those itself. Their first finish turns the mark into `finished:<ms>`, which keeps
later finishes of the same task (within SAME_TASK_SECONDS of the last) its to report, and keeps the result card off
the item; the next finish after that is the desk's, and drops the mark. `vux_state.py realert` sets the terminal's
`herdr-core-voice.realert` label: the next question is alerted once settled, at the desk or away.

This only reads herdr-core, apart from those two labels of its own: it never names a terminal or touches Herdr's.
It ends when stdin closes (vux-agent, which runs it, is gone) or the core's stream does (vux-agent starts it again).

Settings, all optional, in the environment: AWAY_AFTER_MINUTES (default 5; 0 alerts even at the desk),
SETTLE_SECONDS (default 20). Stdlib only.
"""
import json
import os
import re
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
import core  # noqa: E402

DEFAULT_AWAY_MINUTES = 5
DEFAULT_SETTLE_SECONDS = 20
SAME_TASK_SECONDS = 10 * 60
# Heartbeats come every 15 seconds: this long without a line, the core is gone.
SILENT_SECONDS = 45


def is_alert(previous, status):
    """Only the end of a stretch of work is news; startup and idle changes aren't."""
    return previous == "working" and status in ("done", "blocked")


def is_resolved(previous, status):
    """The question was answered, or its agent quit."""
    return previous == "blocked" and status != "blocked"


def idle_seconds(ioreg_output):
    """Seconds since the last keyboard or mouse input, from `ioreg -c IOHIDSystem`."""
    match = re.search(r'"HIDIdleTime" = (\d+)', ioreg_output or "")
    return int(match.group(1)) / 1e9 if match else None


def is_away(idle, away_after_minutes):
    # Without an idle time (not macOS), every alert is sent.
    return idle is None or idle >= away_after_minutes * 60


def number(name, default):
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return float(value)
    except ValueError:
        sys.exit(f"watch: {name} must be a number, not {value!r}")


def hand_off(value, now):
    """Whether the mark (a terminal's handed-off label) makes this finish the skill's to report, and the label's value
    after it ("" removes it)."""
    if not value:
        return False, None
    used, _, when = value.rpartition(":")
    try:
        when = int(when) / 1000
    except ValueError:
        return False, ""
    fresh = now - when < (SAME_TASK_SECONDS if used == "finished" else core.HAND_OFF_TTL)
    return (True, f"finished:{int(now * 1000)}") if fresh else (False, "")


class Watcher:
    def __init__(self, out=sys.stdout):
        self.out = out
        # The terminals, shared with settling timers; and stdout, one line at a time.
        self.lock = threading.Lock()
        self.out_lock = threading.Lock()
        self.away_after = number("AWAY_AFTER_MINUTES", DEFAULT_AWAY_MINUTES)
        self.settle = number("SETTLE_SECONDS", DEFAULT_SETTLE_SECONDS)
        # By address: {"status", "space", "labels", "event"} (event: the count of its status changes, for settling).
        self.terminals = {}

    def say(self, context=None, item=None, push=False):
        body = {**({"context": context} if context else {}), **({"item": item} if item else {}),
                **({} if push else {"push": False})}
        with self.out_lock:
            self.out.write(json.dumps(body) + "\n")
            self.out.flush()

    def spaces(self):
        return {t["space"] for t in self.terminals.values() if t["space"]}

    def track(self, record):
        labels = record.get("labels") or {}
        before = self.terminals.get(record["address"]) or {}
        self.terminals[record["address"]] = {
            "status": (record.get("status") or {}).get("state") if record.get("state") == "live" else None,
            "space": labels.get(core.SPACE) or None, "labels": labels, "event": before.get("event", 0)}

    def snapshot(self, terminals):
        self.terminals = {}
        for record in terminals:
            if core.is_local(record["address"]):
                self.track(record)
        self.say()

    def event(self, kind, address, data):
        if kind == "doc":
            if data.get("name") == core.APP_DOCUMENT and not data.get("machine"):
                self.say()
            return
        if not address or not core.is_local(address):
            return
        spaces = self.spaces()
        if kind in ("created", "restarted"):
            self.track(data)
            space = self.terminals[address]["space"]
            if space:
                self.say(space, address)
                if space not in spaces:
                    self.say()
        elif kind in ("finished", "removed"):
            gone = self.terminals.pop(address, None)
            if gone and gone["space"]:
                self.say(gone["space"], address, push=gone["status"] == "blocked")
                if gone["space"] not in self.spaces():
                    self.say()
        elif kind == "labels":
            self.labels(address, data, spaces)
        elif kind == "status":
            self.status(address, (data.get("status") or {}).get("state"))

    def labels(self, address, data, spaces):
        if data.get("record"):
            self.track(data["record"])
        terminal = self.terminals.get(address)
        if terminal is None:
            return
        labels = data.get("labels") or {}
        before, terminal["labels"] = terminal["labels"], labels
        old, terminal["space"] = terminal["space"], labels.get(core.SPACE) or None
        if old != terminal["space"]:
            for space in filter(None, (old, terminal["space"])):
                self.say(space, address)
            if self.spaces() != spaces:
                self.say()
        if labels.get(core.REALERT) and labels.get(core.REALERT) != before.get(core.REALERT):
            # The dialog moved on to another question: herdr-core reports no status change between the two.
            self.settle_then_alert(address, "blocked")

    def status(self, address, status):
        terminal = self.terminals.get(address)
        if terminal is None:
            return
        previous, terminal["status"] = terminal["status"], status
        terminal["event"] += 1
        if previous == status or not terminal["space"]:
            return
        self.say(terminal["space"], address, push=is_resolved(previous, status))
        if not is_alert(previous, status) or self.handed_off(address, terminal):
            return
        ioreg = subprocess.run(["ioreg", "-c", "IOHIDSystem"], capture_output=True, text=True).stdout \
            if sys.platform == "darwin" else ""
        if is_away(idle_seconds(ioreg), self.away_after):
            self.settle_then_alert(address, status)

    def handed_off(self, address, terminal):
        handed, label = hand_off(terminal["labels"].get(core.HANDED_OFF), time.time())
        if label is not None:
            try:
                core.call("terminal.label", terminal=address, labels={core.HANDED_OFF: label})
            except core.CoreError as error:
                print(f"watch: {address}'s hand-off mark not updated: {error}", file=sys.stderr)
        return handed

    def settle_then_alert(self, address, status):
        """Alerts once the status has held for the settle time: no newer status for the terminal, and still there."""
        event = self.terminals[address]["event"]

        def deliver():
            with self.lock:
                terminal = self.terminals.get(address)
                held = terminal is not None and terminal["event"] == event and terminal["status"] == status
            if held and terminal["space"]:
                self.say(terminal["space"], address, push=True)

        timer = threading.Timer(self.settle, deliver)
        timer.daemon = True
        timer.start()

    def run(self, connection):
        snapshot = connection.subscribe()
        with self.lock:
            self.snapshot(snapshot["terminals"])
        seq = snapshot["seq"]
        for method, params in connection.notifications():
            if method == "missed":
                seq = params["seq"]
                with self.lock:
                    self.snapshot(params["terminals"])
            elif method == "event" and params.get("seq", 0) > seq:
                seq = params["seq"]
                with self.lock:
                    self.event(params.get("type"), params.get("terminal"), params.get("data") or {})


def exit_with_stdin():
    """vux-agent gives the watcher a stdin it never writes: its end means vux-agent is gone, however it went. Read
    from the file descriptor, not sys.stdin: a thread inside sys.stdin's buffer holds its lock, and Python aborts
    when the watcher exits for another reason meanwhile."""
    while os.read(0, 4096):
        pass
    os._exit(0)


def main():
    threading.Thread(target=exit_with_stdin, daemon=True).start()
    try:
        with core.Core(timeout=SILENT_SECONDS) as connection:
            Watcher().run(connection)
    except core.CoreError as error:
        sys.exit(f"watch: {error}")
    except socket.timeout:
        sys.exit(f"watch: herdr-core sent nothing for {SILENT_SECONDS} seconds")
    sys.exit("watch: herdr-core ended the event stream")


if __name__ == "__main__":
    main()
