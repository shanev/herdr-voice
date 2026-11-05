#!/usr/bin/env python3
"""A stand-in coding agent for tests: it reports each line typed into it as its status, over OSC 7501 as Claude Code
and Codex do (`working`, `done`, `idle`, `blocked question <message>`, `title <text>`, and `clear`, which they send
when they quit back to the shell they ran in, and `done-then-write <transcript> <json>`: done, then the reply in the
transcript half a second later, as Claude Code ends a turn), and prints the line."""
import base64
import sys
import time


def report(state, kind=None, msg=None):
    fields = [f"state={state}", "app=" + (sys.argv[1] if len(sys.argv) > 1 else "fake")]
    if kind:
        fields.append(f"kind={kind}")
    if msg:
        fields.append("msg=" + base64.b64encode(msg.encode()).decode())
    sys.stdout.write("\033]7501;" + ":".join(fields) + "\033\\")
    sys.stdout.flush()


report("idle")
for line in sys.stdin:
    words = line.split(maxsplit=2)
    if not words:
        continue
    if words[0] == "done-then-write":
        # As Claude Code ends a turn: `done` first, its reply in its transcript a moment later.
        report("done")
        time.sleep(0.5)
        with open(words[1], "a", encoding="utf-8") as transcript:
            transcript.write(words[2].strip() + "\n")
    elif words[0] == "clear":
        sys.stdout.write("\033]7501;state=clear\033\\")
        sys.stdout.flush()
    elif words[0] == "title":
        sys.stdout.write("\033]2;" + line.partition(" ")[2].strip() + "\007")
    elif words[0] == "blocked":
        report("blocked", words[1] if len(words) > 1 else None, words[2].strip() if len(words) > 2 else None)
    else:
        report(words[0])
    print("got " + line.strip(), flush=True)
