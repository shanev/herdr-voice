"""LOCAL ONLY: herdr-core-voice with a real Claude Code, signed in, in a private herdr-core. CI has no Claude login, so
it runs only with HC_REAL_CLAUDE=1:

    HC_REAL_CLAUDE=1 python3 -m unittest discover -s tests -p test_core_real_claude.py -v

It costs a few cents of the account's Claude usage. A first-launch folder-trust question comes first (Claude reports
nothing to herdr-core there, so it's no item yet), then a question, its answer and the finish, through the
skill's items and watcher as Vux gets them."""
import json
import os
import queue
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path

from private_core import SKILL_DIR, PrivateCore

TASK = ("Use the AskUserQuestion tool to ask me whether I prefer red or blue, with the options Red and Blue. "
        "Then reply with just the color I chose, in one word.")


@unittest.skipUnless(os.getenv("HC_REAL_CLAUDE") == "1", "local only: a real, signed-in Claude Code (HC_REAL_CLAUDE=1)")
class RealClaude(unittest.TestCase):
    # The core's environment, and so its terminals'.
    core_env = {}

    def setUp(self):
        self.core = PrivateCore(**self.core_env).start()
        self.addCleanup(self.core.stop)
        self.repo = Path(tempfile.mkdtemp(prefix="hcv-repo-", dir="/tmp")).resolve()
        subprocess.run(["git", "init", "-q", str(self.repo)], check=True)
        self.addCleanup(shutil.rmtree, self.repo, True)
        self.env = {"PATH": os.getenv("PATH"), "HOME": str(Path.home()), "HC_SOCKET": str(self.core.socket)}
        self.watcher = subprocess.Popen([sys.executable, "scripts/watch.py"], cwd=SKILL_DIR, text=True,
                                        env={**self.env, "AWAY_AFTER_MINUTES": "0", "SETTLE_SECONDS": "2"},
                                        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.addCleanup(self.stop_watcher)
        self.lines = queue.Queue()
        threading.Thread(target=lambda: [self.lines.put(json.loads(line)) for line in self.watcher.stdout], daemon=True).start()

    def stop_watcher(self):
        self.watcher.kill()
        self.watcher.wait()
        for pipe in (self.watcher.stdin, self.watcher.stdout, self.watcher.stderr):
            pipe.close()

    def state(self, *args):
        done = subprocess.run([sys.executable, "scripts/vux_state.py", *args], cwd=SKILL_DIR, env=self.env,
                              capture_output=True, text=True, timeout=60, check=True)
        return json.loads(done.stdout)

    def pushed(self, item):
        """Waits for the watcher's push line for the item."""
        while True:
            line = self.lines.get(timeout=120)
            if line == {"context": "spReal", "item": item}:
                return

    def test_a_question_its_answer_and_the_finish(self):
        made = self.core.call("terminal.run", argv=["claude"], cwd=str(self.repo), name="real-claude",
                              labels={"herdr.space": "spReal"}, no_parent=True, keep=True)
        address = made["address"]
        # The folder-trust question: no status, so no item.
        waited = self.core.call("terminal.wait", terminal=address, timeout=30)
        self.assertIsNone(waited["status"], waited)
        self.assertIn("trust", self.core.call("terminal.read", terminal=address, lines=0)["text"])
        self.assertEqual(self.state("items", "spReal")["items"], [])
        self.core.call("terminal.keys", terminal=address, keys=["down", "enter"])
        self.core.until(address, lambda r: (r.get("status") or {}).get("state") == "idle", "idle after trust")
        [item] = self.state("items", "spReal")["items"]
        self.assertEqual((item["id"], item["name"], item["kind"], item["state"], item["card"]),
                         (address, "real-claude", "Claude", "idle", None))

        self.core.call("terminal.send", terminal=address, text=TASK)
        blocked = self.core.until(address, lambda r: (r.get("status") or {}).get("state") == "blocked", "its question")
        self.assertEqual(blocked["status"]["kind"], "question")
        self.assertIn("Red", blocked["status"]["msg"])
        [item] = self.state("items", "spReal")["items"]
        self.assertEqual((item["state"], item["card"]["kind"]), ("waiting", "question"))
        for option in ("Red", "Blue"):
            self.assertIn(option, item["card"]["screen"])
        self.pushed(address)

        screen = self.core.call("terminal.read", terminal=address, lines=0)["text"]
        key = "1" if "1. Red" in screen else "2"
        self.core.call("terminal.keys", terminal=address, keys=[key])
        # Answered: the question is over (the watcher says so, and may push to withdraw it), then the finish.
        self.pushed(address)
        self.core.until(address, lambda r: (r.get("status") or {}).get("state") == "done", "its finish")
        self.pushed(address)
        [item] = self.state("items", "spReal")["items"]
        self.assertEqual((item["state"], item["card"]["kind"]), ("done", "result"))
        # Its reply, from the session file Claude Code keeps for its process.
        self.assertIn("red", item["card"]["text"].lower())


class ChildClaude(RealClaude):
    """A core whose terminals all take themselves for another Claude's child sessions: one an agent's hc call started,
    from inside Claude Code. Claude Code (2.1.318) then keeps neither ~/.claude/sessions/<pid>.json nor a transcript.
    A session started the skill's way (SKILL.md's `env -u`) is no one's child: its reply is read from its files."""
    core_env = {"CLAUDE_CODE_CHILD_SESSION": "1", "CLAUDE_CODE_SESSION_ID": "00000000-0000-4000-8000-000000000000"}

    def finished(self, name, argv):
        address = self.core.call("terminal.run", argv=argv, cwd=str(self.repo), name=name,
                                 labels={"herdr.space": "spReal"}, no_parent=True)["address"]
        self.core.call("terminal.wait", terminal=address, timeout=30)
        if "trust" in self.core.call("terminal.read", terminal=address, lines=0)["text"]:
            self.core.call("terminal.keys", terminal=address, keys=["down", "enter"])
        self.core.until(address, lambda r: (r.get("status") or {}).get("state") == "idle", "idle")
        self.core.call("terminal.send", terminal=address, text="Reply with just the word pineapple.")
        self.core.until(address, lambda r: (r.get("status") or {}).get("state") == "done", "its finish")
        return address

    def test_a_question_its_answer_and_the_finish(self):
        child = self.finished("child", ["claude"])
        started = self.finished("started", ["env", "-u", "CLAUDE_CODE_CHILD_SESSION", "-u", "CLAUDE_CODE_SESSION_ID",
                                             "claude"])
        items = {i["id"]: i for i in self.state("items", "spReal")["items"]}
        # The child's reply is only on its screen: a result card without text, and `reply` says to read the screen.
        self.assertEqual(items[child]["card"]["kind"], "result")
        self.assertNotIn("text", items[child]["card"])
        reply = subprocess.run([sys.executable, "scripts/vux_state.py", "reply", child], cwd=SKILL_DIR, env=self.env,
                               capture_output=True, text=True, timeout=60)
        self.assertEqual(reply.returncode, 3, reply.stderr)
        # Started as SKILL.md says: its own session, read from its files.
        self.assertIn("pineapple", items[started]["card"]["text"].lower())
        self.assertEqual([s["id"] for s in self.state("sessions")["sessions"]], [started])
