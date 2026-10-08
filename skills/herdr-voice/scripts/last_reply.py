#!/usr/bin/env python3
"""Print a herdr agent's last reply, read from the agent's own session file instead of its screen.

Usage: last_reply.py <agent>    (a pane id or name, as for `herdr agent get`)

Exit 0: the reply is on stdout. Exit 3: there's no finished reply to read this way (an agent kind
without a reader here, no session recorded by herdr, or the turn is still going); read the screen
instead. Stdlib only.
"""
import json
import subprocess
import sys
from pathlib import Path

NO_REPLY = 3


def claude_transcript(session, home):
    if session["kind"] == "path":
        return Path(session["value"])
    return newest((home / ".claude" / "projects").glob(f"*/{session['value']}.jsonl"))


def codex_transcript(session, home):
    if session["kind"] == "path":
        return Path(session["value"])
    return newest((home / ".codex" / "sessions").glob(f"*/*/*/rollout-*-{session['value']}.jsonl"))


def newest(paths):
    return max(paths, key=lambda p: p.stat().st_mtime, default=None)


def is_prompt(entry):
    """A message the user (or herdr, for them) typed, not a tool result Claude Code records as a user turn."""
    if entry.get("type") != "user" or entry.get("isMeta") or entry.get("isSidechain"):
        return False
    content = entry.get("message", {}).get("content")
    if isinstance(content, str):
        return True
    return any(isinstance(block, dict) and block.get("type") == "text" for block in content or [])


def claude_reply(lines):
    """The text of the turn's final assistant message, once that message ended the turn."""
    texts, last_id, last_stop = {}, None, None
    for line in lines:
        if not line.strip():
            continue
        entry = json.loads(line)
        if is_prompt(entry):
            texts, last_id, last_stop = {}, None, None
            continue
        if entry.get("type") != "assistant" or entry.get("isSidechain"):
            continue
        message = entry.get("message", {})
        # One API message is written as several entries (thinking, text, tool use) sharing its id.
        last_id, last_stop = message.get("id"), message.get("stop_reason")
        for block in message.get("content") or []:
            if isinstance(block, dict) and block.get("type") == "text" and block.get("text", "").strip():
                texts.setdefault(last_id, []).append(block["text"].strip())
    if last_stop not in ("end_turn", "stop_sequence") or last_id not in texts:
        return None
    return "\n\n".join(texts[last_id])


def codex_reply(lines):
    """`last_agent_message` of the latest finished task, unless a newer task has started since."""
    reply = None
    for line in lines:
        if not line.strip():
            continue
        entry = json.loads(line)
        if entry.get("type") != "event_msg":
            continue
        payload = entry.get("payload", {})
        if payload.get("type") == "task_started":
            reply = None
        elif payload.get("type") == "task_complete":
            reply = payload.get("last_agent_message") or None
    return reply


READERS = {"claude": (claude_transcript, claude_reply), "codex": (codex_transcript, codex_reply)}


def last_reply(agent, home):
    """`agent` is the object `herdr agent get` returns."""
    session = agent.get("agent_session")
    if agent.get("agent") not in READERS or not session:
        return None
    find, read = READERS[agent["agent"]]
    path = find(session, home)
    if path is None or not path.is_file():
        return None
    with path.open(encoding="utf-8") as lines:
        return read(lines)


def main(argv):
    if len(argv) != 2:
        print(__doc__.strip(), file=sys.stderr)
        return 2
    got = subprocess.run(["herdr", "agent", "get", argv[1]], capture_output=True, text=True)
    if got.returncode != 0:
        sys.stderr.write(got.stderr or got.stdout)
        return 1
    agent = json.loads(got.stdout)["result"]["agent"]
    reply = last_reply(agent, Path.home())
    if reply is None:
        print(f"No finished reply in a session file for this {agent.get('agent') or 'agent'}; read the screen.",
              file=sys.stderr)
        return NO_REPLY
    print(reply)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
