"""herdr-core's API for this skill's scripts: JSON-RPC 2.0, one message per line, on the core's Unix socket (herdr-core's
schema/README.md). Stdlib only.
"""
import json
import os
import socket
from pathlib import Path

# Labels this skill keeps on terminals (keys are free-form; Herdr reads only its own `herdr.*` ones).
# The agent running the skill handed this terminal's task off and reports its finish itself: `<ms>` when marked,
# `finished:<ms>` once the watcher saw that task finish (vux_state.py and watch.py explain both).
HANDED_OFF = "herdr-core-voice.handed-off"
# Set by `vux_state.py realert`: the watcher alerts again for the terminal's next question.
REALERT = "herdr-core-voice.realert"
HAND_OFF_TTL = 12 * 60 * 60
# Herdr's own: the space (and tab) a terminal shows in.
SPACE = "herdr.space"
# The document the Herdr app keeps its spaces, tabs and splits in.
APP_DOCUMENT = "herdr-app"


class CoreError(Exception):
    pass


def socket_path():
    """The core's socket, by hc's rules: HC_SOCKET, else <session>.sock in $XDG_RUNTIME_DIR/herdr-core, else in
    /run/user/<uid>/herdr-core when that runtime folder exists, else in the session's state folder."""
    given = os.getenv("HC_SOCKET")
    if given:
        return Path(given)
    session = os.getenv("HC_SESSION") or "default"
    runtime = os.getenv("XDG_RUNTIME_DIR")
    if runtime:
        return Path(runtime) / "herdr-core" / f"{session}.sock"
    run_user = Path(f"/run/user/{os.getuid()}")
    if run_user.is_dir():
        return run_user / "herdr-core" / f"{session}.sock"
    state = Path(os.getenv("XDG_STATE_HOME") or Path.home() / ".local" / "state")
    return state / "herdr-core" / session / f"{session}.sock"


class Core:
    """One connection to the core. `call` sends a request and returns its result; after `subscribe` the connection
    carries only notifications, read with `notifications`."""

    def __init__(self, path=None, timeout=20):
        self.path = Path(path or socket_path())
        self.sock = socket.socket(socket.AF_UNIX)
        self.sock.settimeout(timeout)
        try:
            self.sock.connect(str(self.path))
        except OSError as error:
            self.sock.close()
            raise CoreError(f"herdr-core isn't running here ({self.path}: {error.strerror or error})") from None
        self.lines = self.sock.makefile("r", encoding="utf-8")
        self.next_id = 0

    def close(self):
        self.lines.close()
        self.sock.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def call(self, method, **params):
        self.next_id += 1
        self.sock.sendall((json.dumps({"jsonrpc": "2.0", "id": self.next_id, "method": method, "params": params}) + "\n").encode())
        while True:
            line = self.lines.readline()
            if not line:
                raise CoreError(f"herdr-core closed the connection during {method}")
            message = json.loads(line)
            if message.get("id") != self.next_id:
                continue
            if "error" in message:
                error = message["error"]
                kind = (error.get("data") or {}).get("kind")
                raise CoreError(f"{method}: {error.get('message')}" + (f" ({kind})" if kind else ""))
            return message["result"]

    def subscribe(self):
        """The snapshot (`seq`, `terminals`); then `notifications` yields the stream."""
        return self.call("events.subscribe")

    def notifications(self):
        """(method, params) for each notification; ends when the core closes the connection. A read timeout raises
        socket.timeout: heartbeats come every 15 seconds, so silence for longer means the core is gone."""
        for line in self.lines:
            message = json.loads(line)
            if "method" in message:
                yield message["method"], message.get("params") or {}


def call(method, **params):
    with Core() as core:
        return core.call(method, **params)


def is_local(address):
    """A terminal of this machine's core; `box/t7` is an added machine's, whose spaces are that machine's."""
    return "/" not in address
