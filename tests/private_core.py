"""A private herdr-core (`hc`) for the herdr-core-voice tests: its own session, socket and state, started with a clean
environment as a desktop start would be, so nothing touches the user's own core. The tests talk to it as the skill's
scripts do, over its socket."""
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKILL_DIR = ROOT / "skills" / "herdr-core-voice"
FAKE_AGENT = Path(__file__).resolve().parent / "fake_agent.py"


def hc_binary():
    found = shutil.which("hc")
    if found is None:
        if os.getenv("CI"):
            raise RuntimeError("hc isn't installed: CI installs it (.github/workflows/test.yml)")
        raise unittest.SkipTest("hc (herdr-core) isn't installed")
    return found


class PrivateCore:
    def __init__(self):
        self.hc = hc_binary()
        # macOS caps socket paths at 103 bytes: a short folder under /tmp.
        self.folder = Path(tempfile.mkdtemp(prefix="hcv", dir="/tmp")).resolve()
        self.session = self.folder.name
        self.socket = self.folder / "c.sock"
        self.env = {"HOME": os.getenv("HOME") or str(Path.home()), "USER": os.getenv("USER") or "",
                    "PATH": os.getenv("PATH") or "/usr/bin:/bin", "SHELL": os.getenv("SHELL") or "/bin/sh",
                    "LANG": os.getenv("LANG") or "en_US.UTF-8", "TERM": "xterm-256color",
                    "HC_SESSION": self.session, "HC_SOCKET": str(self.socket),
                    "XDG_STATE_HOME": str(self.folder / "state"), "XDG_CONFIG_HOME": str(self.folder / "config")}

    def start(self):
        subprocess.run([self.hc, "server", "start"], env=self.env, check=True, capture_output=True, timeout=60)
        return self

    def stop(self):
        subprocess.run([self.hc, "server", "stop"], env=self.env, capture_output=True, timeout=60)
        shutil.rmtree(self.folder, ignore_errors=True)

    def connect(self):
        connection = socket.socket(socket.AF_UNIX)
        connection.settimeout(60)
        connection.connect(str(self.socket))
        return connection, connection.makefile("r", encoding="utf-8")

    def call(self, method, **params):
        connection, lines = self.connect()
        try:
            connection.sendall((json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}) + "\n").encode())
            for line in lines:
                message = json.loads(line)
                if message.get("id") == 1:
                    if "error" in message:
                        raise RuntimeError(f"{method}: {message['error']}")
                    return message["result"]
            raise RuntimeError(f"{method}: the core closed the connection")
        finally:
            lines.close()
            connection.close()

    def until(self, terminal, test, what):
        """Waits, on the terminal's own event stream, until `test(record)` holds for its record (kept current from
        the stream's status, title and labels events)."""
        connection, lines = self.connect()
        try:
            connection.sendall((json.dumps({"jsonrpc": "2.0", "id": 1, "method": "events.subscribe",
                                            "params": {"terminal": terminal}}) + "\n").encode())
            record = next(t for t in json.loads(lines.readline())["result"]["terminals"] if t["address"] == terminal)
            # Heartbeats keep the stream busy every 15 seconds: the deadline is for the whole wait.
            deadline = time.monotonic() + 60
            while not test(record):
                if time.monotonic() > deadline:
                    raise socket.timeout
                line = lines.readline()
                if not line:
                    raise AssertionError(f"{terminal}: the stream ended before {what}")
                params = json.loads(line).get("params") or {}
                data = params.get("data") or {}
                kind = params.get("type")
                if kind == "status":
                    record["status"], record["reported"] = data.get("status"), data.get("reported")
                elif kind == "turn":
                    record["turn"] = data["turn"]
                elif kind == "title":
                    record["title"] = data["title"]
                elif kind == "labels":
                    record["labels"] = data["labels"]
                elif kind in ("finished", "removed"):
                    record["state"] = "finished"
            return record
        except socket.timeout:
            raise AssertionError(f"{terminal}: no {what} within 60 seconds") from None
        finally:
            lines.close()
            connection.close()

    def agent(self, space=None, app="claude-code", name=None, cwd=None, labels=None):
        """A fake agent (tests/fake_agent.py) in a space; returns its record once it reports idle."""
        labels = {**({"herdr.space": space} if space else {}), **(labels or {})}
        made = self.call("terminal.run", argv=[sys.executable, str(FAKE_AGENT), app], labels=labels, no_parent=True,
                         cwd=str(cwd or self.folder), **({"name": name} if name else {}))
        address = made["address"] if "address" in made else made["terminal"]["address"]
        return self.until(address, lambda r: (r.get("status") or {}).get("state") == "idle", "first idle report")

    def say(self, terminal, line, state):
        """Types a line into a fake agent and waits until it reports `state` (and, leaving `working`, until the turn
        that ended is counted: the core sends that as an event of its own, after the status)."""
        before = self.call("terminal.get", terminal=terminal)
        ends_turn = (before.get("status") or {}).get("state") == "working" and state != "working"
        self.call("terminal.send", terminal=terminal, text=line, force=True)
        return self.until(terminal, lambda r: (r.get("status") or {}).get("state") == state
                          and (not ends_turn or r.get("turn", 0) > before.get("turn", 0)), f"status {state}")

    def document(self, value):
        """Writes Herdr's `herdr-app` document, as the app does."""
        version = self.call("doc.get", name="herdr-app")["version"]
        return self.call("doc.set", name="herdr-app", value=value, version=version)

    def clear(self):
        for terminal in self.call("terminal.list", all=True)["terminals"]:
            if terminal["state"] == "live":
                self.call("terminal.kill", terminal=terminal["address"])
                self.until(terminal["address"], lambda r: r["state"] == "finished", "its end")
            self.call("terminal.rm", terminal=terminal["address"])
        if self.call("doc.get", name="herdr-app")["value"] is not None:
            self.document(None)


def space_doc(*spaces):
    """Herdr's document (herdr-app's src/mirror.rs): spaces with tabs, each a layout tree of terminal blocks."""
    return {"spaces": [{"id": space_id, "name": name, "folder": folder, "tabs": [
        {"id": f"tb-{space_id}-{n}", "name": tab_name, "root": {"id": f"tb-{space_id}-{n}", "type": "row", "children": [
            {"id": f"p-{terminal}", "type": "block", "content": {"terminal": terminal}} for terminal in terminals]}}
        for n, (tab_name, terminals) in enumerate(tabs)]} for space_id, name, folder, tabs in spaces]}
