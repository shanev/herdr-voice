"""skills/herdr-core-voice/scripts/watch.py end to end: the watcher runs as vux-agent runs it (in the skill's folder,
stdin a pipe that's never written, one JSON line per change on stdout) against a real private herdr-core, while fake
agents report their status over OSC 7501. It's away from the computer from the start (AWAY_AFTER_MINUTES=0) and
statuses settle in three seconds, longer than a test takes to report a few in a row."""
import json
import os
import queue
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

from private_core import SKILL_DIR, PrivateCore, space_doc

SETTLE = 3
CHANGED = {"push": False}


class Watch(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.core = PrivateCore().start()
        cls.addClassCleanup(cls.core.stop)

    def setUp(self):
        self.core.clear()
        self.home = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.home, True)
        env = {"PATH": os.getenv("PATH"), "HOME": str(self.home), "HC_SOCKET": str(self.core.socket),
               "VUX_SKILL_DIR": str(SKILL_DIR), "AWAY_AFTER_MINUTES": "0", "SETTLE_SECONDS": str(SETTLE)}
        self.watcher = subprocess.Popen([sys.executable, "scripts/watch.py"], cwd=SKILL_DIR, env=env, text=True,
                                        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.addCleanup(self.stop_watcher)
        self.lines = queue.Queue()
        threading.Thread(target=self.read, daemon=True).start()
        # Subscribed: which spaces there are is news (it may have missed changes before).
        self.assertEqual(self.next_line(), CHANGED)

    def read(self):
        for line in self.watcher.stdout:
            self.lines.put(json.loads(line))
        self.lines.put(None)

    def stop_watcher(self):
        if self.watcher.poll() is None:
            self.watcher.kill()
        self.watcher.wait()
        for pipe in (self.watcher.stdin, self.watcher.stdout, self.watcher.stderr):
            pipe.close()

    def next_line(self, timeout=30):
        try:
            line = self.lines.get(timeout=timeout)
        except queue.Empty:
            self.fail(f"the watcher said nothing for {timeout} seconds")
        if line is None:
            self.fail(f"the watcher ended: {self.watcher.stderr.read()}")
        return line

    def lines_until(self, wanted):
        """Every line up to and including `wanted`."""
        seen = []
        while not seen or seen[-1] != wanted:
            seen.append(self.next_line())
        return seen

    def pushes(self, seen, item):
        return [line for line in seen if line.get("item") == item and "push" not in line]

    def test_a_finish_while_away_is_pushed_once_settled(self):
        agent = self.core.agent("spA")
        # A new terminal in a new space: the space's agents, and which spaces there are.
        self.assertEqual(self.lines_until(CHANGED), [{"context": "spA", "item": agent["address"], "push": False}, CHANGED])
        # Its first report (idle, at startup).
        self.assertEqual(self.next_line(), {"context": "spA", "item": agent["address"], "push": False})
        self.core.say(agent["address"], "working", "working")
        self.assertEqual(self.next_line(), {"context": "spA", "item": agent["address"], "push": False})
        self.core.say(agent["address"], "done", "done")
        # At once for Vux's open stream, then, held for the settle time, as news to push.
        self.assertEqual(self.next_line(), {"context": "spA", "item": agent["address"], "push": False})
        self.assertEqual(self.next_line(), {"context": "spA", "item": agent["address"]})

    def test_a_finish_that_goes_back_to_work_is_not_pushed(self):
        # Claude can end a reply while a command it started runs on: done, then working again within the settle time,
        # then done again. Only the last finish has held still, so there's one push, not two.
        agent, later = self.core.agent("spB"), self.core.agent("spB")
        for word in ("working", "done", "working", "done"):
            self.core.say(agent["address"], word, word)
        # Another agent's finish, after those: its push comes after any of the first agent's (timers fire in order).
        self.core.say(later["address"], "working", "working")
        self.core.say(later["address"], "done", "done")
        seen = self.lines_until({"context": "spB", "item": later["address"]})
        self.assertEqual(len(self.pushes(seen, agent["address"])), 1)
        # A finish followed by a question: the finish's timer finds newer news, and the question is pushed.
        self.core.say(agent["address"], "working", "working")
        self.core.say(agent["address"], "done", "done")
        self.core.say(agent["address"], "working", "working")
        self.core.say(agent["address"], "blocked permission approve Bash", "blocked")
        seen = self.lines_until({"context": "spB", "item": agent["address"]})
        self.assertEqual(seen[-2], {"context": "spB", "item": agent["address"], "push": False})

    def test_an_answered_question_may_push_so_it_is_withdrawn(self):
        agent = self.core.agent("spQ")
        self.core.say(agent["address"], "working", "working")
        self.core.say(agent["address"], "blocked question Red or blue?", "blocked")
        self.lines_until({"context": "spQ", "item": agent["address"]})
        self.core.say(agent["address"], "working", "working")
        self.assertEqual(self.next_line(), {"context": "spQ", "item": agent["address"]})

    def test_a_handed_off_task_is_the_skills_to_report(self):
        handed, other = self.core.agent("spH"), self.core.agent("spH")
        subprocess.run([sys.executable, "scripts/vux_state.py", "mark", handed["address"]], cwd=SKILL_DIR, check=True,
                       env={"PATH": os.getenv("PATH"), "HOME": str(self.home), "HC_SOCKET": str(self.core.socket)})
        self.core.say(handed["address"], "working", "working")
        self.core.say(handed["address"], "done", "done")
        # The mark is used: this task's finishes stay the skill's for a while, and the next desk task's are pushed.
        labels = self.core.until(handed["address"], lambda r: (r.get("labels") or {}).get("herdr-core-voice.handed-off", "")
                                 .startswith("finished:"), "the mark used")["labels"]
        self.assertLess(abs(int(labels["herdr-core-voice.handed-off"].partition(":")[2]) / 1000 - time.time()), 60)
        # Claude ended a reply and worked on: the same task.
        self.core.say(handed["address"], "working", "working")
        self.core.say(handed["address"], "done", "done")
        self.core.say(other["address"], "working", "working")
        self.core.say(other["address"], "done", "done")
        seen = self.lines_until({"context": "spH", "item": other["address"]})
        self.assertEqual(self.pushes(seen, handed["address"]), [])
        self.assertIn({"context": "spH", "item": handed["address"], "push": False}, seen)
        # An old used mark is the desk's again: pushed, and the mark goes.
        self.core.call("terminal.label", terminal=handed["address"],
                       labels={"herdr-core-voice.handed-off": f"finished:{int((time.time() - 11 * 60) * 1000)}"})
        self.core.say(handed["address"], "working", "working")
        self.core.say(handed["address"], "done", "done")
        self.lines_until({"context": "spH", "item": handed["address"]})
        self.core.until(handed["address"], lambda r: "herdr-core-voice.handed-off" not in (r.get("labels") or {}), "the mark gone")

    def test_realert_alerts_the_next_question(self):
        agent = self.core.agent("spR")
        self.core.say(agent["address"], "working", "working")
        self.core.say(agent["address"], "blocked question First?", "blocked")
        self.lines_until({"context": "spR", "item": agent["address"]})
        # Answered, and the dialog shows a second question: no status change, so only realert brings it.
        subprocess.run([sys.executable, "scripts/vux_state.py", "realert", agent["address"]], cwd=SKILL_DIR, check=True,
                       env={"PATH": os.getenv("PATH"), "HOME": str(self.home), "HC_SOCKET": str(self.core.socket)},
                       capture_output=True)
        self.assertEqual(self.next_line(), {"context": "spR", "item": agent["address"]})

    def test_terminals_gone_and_herdrs_document_change_which_spaces_there_are(self):
        agent = self.core.agent("spG")
        self.core.say(agent["address"], "working", "working")
        self.core.say(agent["address"], "blocked question Still there?", "blocked")
        self.lines_until({"context": "spG", "item": agent["address"]})
        self.core.call("terminal.kill", terminal=agent["address"])
        # Its status is cleared, so its question is over (it may push, to withdraw it); then it's gone, and its
        # space had no other terminal.
        self.assertEqual(self.lines_until(CHANGED)[-3:], [{"context": "spG", "item": agent["address"]},
                                                          {"context": "spG", "item": agent["address"], "push": False}, CHANGED])
        self.core.document(space_doc(("spD", "docs", str(self.home), [])))
        self.assertEqual(self.next_line(), CHANGED)
        # Terminals outside Herdr's spaces aren't Vux's.
        outside = self.core.agent(None)
        self.core.say(outside["address"], "working", "working")
        self.core.call("terminal.label", terminal=outside["address"], labels={"herdr.space": "spM"})
        self.assertEqual(self.lines_until(CHANGED), [{"context": "spM", "item": outside["address"], "push": False}, CHANGED])

    def test_an_agent_that_quits_to_its_shell_leaves_vux(self):
        agent = self.core.agent("spE")
        self.core.say(agent["address"], "working", "working")
        self.core.say(agent["address"], "blocked question Still there?", "blocked")
        self.lines_until({"context": "spE", "item": agent["address"]})
        # Its status cleared: it's no agent now, and its question is over (it may push, to withdraw it).
        self.core.call("terminal.send", terminal=agent["address"], text="clear", force=True)
        self.assertEqual(self.next_line(), {"context": "spE", "item": agent["address"]})

    def test_a_sub_agent_is_its_ancestors_spaces(self):
        lead = self.core.agent("spS")
        self.lines_until(CHANGED)
        helper = self.core.agent(parent=lead["address"])
        # In its parent's space, which isn't new.
        self.assertEqual(self.next_line(), {"context": "spS", "item": lead["address"], "push": False})
        self.assertEqual(self.next_line(), {"context": "spS", "item": helper["address"], "push": False})
        self.assertEqual(self.next_line(), {"context": "spS", "item": helper["address"], "push": False})
        self.core.say(helper["address"], "working", "working")
        self.core.say(helper["address"], "done", "done")
        self.assertEqual(self.lines_until({"context": "spS", "item": helper["address"]})[-2:],
                         [{"context": "spS", "item": helper["address"], "push": False},
                          {"context": "spS", "item": helper["address"]}])

    def test_it_ends_with_its_stdin(self):
        # vux-agent's end, however it went (a SIGKILL included), closes the pipe.
        self.watcher.stdin.close()
        self.assertEqual(self.watcher.wait(timeout=30), 0)

    def test_it_ends_when_the_core_is_gone(self):
        env = {"PATH": os.getenv("PATH"), "HOME": str(self.home), "HC_SOCKET": str(self.home / "none.sock")}
        # Its stdin open, as vux-agent keeps it.
        done = subprocess.Popen([sys.executable, "scripts/watch.py"], cwd=SKILL_DIR, env=env, stdin=subprocess.PIPE,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.assertEqual(done.wait(timeout=30), 1)
        self.assertIn("herdr-core isn't running", done.stderr.read())
        for pipe in (done.stdin, done.stdout, done.stderr):
            pipe.close()


if __name__ == "__main__":
    unittest.main()
