"""skills/herdr-core-voice/scripts/vux_state.py end to end, against a real private herdr-core: the script runs as
vux-agent runs it (a subprocess, in the skill's folder), the agents are fake ones that report their status over OSC
7501 as Claude Code and Codex do, and Herdr's document is written in the app's own format."""
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path

from private_core import SKILL_DIR, PrivateCore, space_doc

HERE = os.uname().nodename.split(".")[0]
ITEM_TYPES = {"id": str, "name": str, "kind": str, "task": str, "state": str, "card": (dict, type(None))}


def claude_session(home, pid, session, reply):
    """Claude Code's files for a session: its per-process file naming the session, and the transcript."""
    (home / ".claude" / "sessions").mkdir(parents=True, exist_ok=True)
    (home / ".claude" / "sessions" / f"{pid}.json").write_text(json.dumps({"pid": pid, "sessionId": session}))
    transcript = home / ".claude" / "projects" / "-repo" / f"{session}.jsonl"
    transcript.parent.mkdir(parents=True, exist_ok=True)
    transcript.write_text("\n".join(json.dumps(e) for e in [
        {"type": "user", "message": {"content": "Open the PR"}},
        {"type": "assistant", "message": {"id": "m1", "stop_reason": "end_turn", "content": [{"type": "text", "text": reply}]}},
    ]) + "\n")
    return transcript


def codex_rollout(home, cwd, reply, name, started=None):
    started = started or datetime.now(timezone.utc)
    path = home / ".codex" / "sessions" / datetime.now().strftime("%Y/%m/%d") / f"rollout-{name}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(e) for e in [
        {"type": "session_meta", "payload": {"id": name, "cwd": str(cwd), "source": "cli",
                                             "timestamp": started.isoformat().replace("+00:00", "Z")}},
        {"type": "event_msg", "payload": {"type": "task_started"}},
        {"type": "event_msg", "payload": {"type": "task_complete", "last_agent_message": reply}},
    ]) + "\n")
    return path


class VuxState(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.core = PrivateCore().start()
        cls.addClassCleanup(cls.core.stop)

    def setUp(self):
        self.core.clear()
        self.home = Path(tempfile.mkdtemp()).resolve()
        self.repo = self.home / "github.com" / "me" / "api"
        (self.repo / ".git").mkdir(parents=True)
        (self.repo / "src").mkdir()
        self.addCleanup(shutil.rmtree, self.home, True)

    def run_state(self, *args, check=True):
        env = {"PATH": os.getenv("PATH"), "HOME": str(self.home), "HC_SOCKET": str(self.core.socket)}
        done = subprocess.run([sys.executable, "scripts/vux_state.py", *args], cwd=SKILL_DIR, env=env,
                              capture_output=True, text=True, timeout=60)
        if not check:
            return done
        self.assertEqual(done.returncode, 0, done.stderr)
        return json.loads(done.stdout) if done.stdout.startswith("{") else done.stdout

    def test_contexts_are_herdrs_spaces_with_herdrs_names(self):
        named = self.core.agent("spN")
        in_repo = self.core.agent("spR", cwd=self.repo / "src")
        at_home = self.core.agent("spH", cwd=self.home)
        # Started while Herdr's window was closed: no document has its space yet, and Herdr makes it when it opens.
        later = self.core.agent("spL", cwd=self.repo)
        self.core.document(space_doc(
            ("spN", "notes", str(self.home), [("", [named["address"]])]),
            # Unnamed: Herdr calls it after its first terminal's repo, wherever in it the terminal is now.
            ("spR", "", "/somewhere/else", [("", [in_repo["address"]])]),
            ("spH", "", str(self.home), [("", [at_home["address"]])]),
            # No terminal: the folder it was made in.
            ("spE", "", str(self.repo), [])))
        self.assertEqual(self.run_state("contexts")["contexts"], [
            {"id": "spN", "name": "notes", "location": HERE},
            {"id": "spR", "name": "api", "location": HERE},
            {"id": "spH", "name": "Home", "location": HERE},
            {"id": "spE", "name": "api", "location": HERE},
            {"id": "spL", "name": "api", "location": HERE},
        ])

    def test_items_are_the_spaces_agents_as_herdr_core_reports_them(self):
        working = self.core.agent("spA", name="apple-models")
        self.core.say(working["address"], "title ✳ Fixing the swipe test", "idle")
        self.core.say(working["address"], "working", "working")
        blocked = self.core.agent("spA", app="codex")
        self.core.say(blocked["address"], "working", "working")
        self.core.say(blocked["address"], "blocked question Red or blue?", "blocked")
        done = self.core.agent("spA")
        self.core.say(done["address"], "working", "working")
        done = self.core.say(done["address"], "done", "done")
        errored = self.core.agent("spA", app="pi-agent")
        self.core.say(errored["address"], "error", "error")
        # Not agents: a shell that never reports, and an agent outside Herdr's spaces.
        self.core.call("terminal.run", argv=["sh", "-c", "exec cat"], labels={"herdr.space": "spA"}, no_parent=True)
        self.core.agent(None)
        self.core.document(space_doc(("spA", "api", str(self.repo), [("swipe fix", [working["address"]]),
                                                                       ("", [blocked["address"], done["address"]])])))
        items = self.run_state("items", "spA")["items"]
        screen = self.core.call("terminal.read", terminal=blocked["address"], lines=0)["text"]
        screen = "\n".join(line.rstrip() for line in screen.splitlines() if line.strip())
        self.assertEqual(items, [
            # Its hc name; its tab's name, which Herdr shows, is its task.
            {"id": working["address"], "name": "apple-models", "kind": "Claude", "task": "swipe fix", "state": "working",
             "card": None},
            # No name: its address. Its tab has no name: its terminal title.
            {"id": blocked["address"], "name": blocked["address"], "kind": "Codex", "task": "New session",
             "state": "waiting", "card": {"kind": "question", "id": hashlib.sha256(screen.encode()).hexdigest()[:16],
                                          "screen": screen}},
            # Done, with a result no one told the user: a result card for this turn, without text (no session file).
            {"id": done["address"], "name": done["address"], "kind": "Claude", "task": "New session", "state": "done",
             "card": {"kind": "result", "id": f"{done['address']}:{done['turn']}"}},
            {"id": errored["address"], "name": errored["address"], "kind": "Pi-agent", "task": "New session",
             "state": "failed", "card": None},
        ])
        self.assertIn("Red or blue?", items[1]["card"]["screen"])
        for item in self.run_state("items")["items"]:
            self.assertEqual(set(item), {*ITEM_TYPES, "context"})
            for key, kind in ITEM_TYPES.items():
                self.assertIsInstance(item[key], kind)
            self.assertEqual(item["context"], "spA")

    def test_an_agent_that_quit_to_its_shell_is_no_agent(self):
        # Claude Code and Codex clear their status when they exit to the shell Herdr ran them in: the terminal lives
        # on, a plain shell now, which reported once.
        agent = self.core.agent("spQ")
        self.core.say(agent["address"], "working", "working")
        self.core.say(agent["address"], "done", "done")
        self.assertEqual(len(self.run_state("items", "spQ")["items"]), 1)
        self.core.call("terminal.send", terminal=agent["address"], text="clear")
        record = self.core.until(agent["address"], lambda r: r.get("status") is None, "its status cleared")
        self.assertTrue(record["reported"])
        self.assertEqual(self.run_state("items", "spQ")["items"], [])
        self.assertEqual(self.run_state("space", str(self.core.folder))["busy"], [])

    def test_a_sub_agent_is_in_its_nearest_labelled_ancestors_space(self):
        # An agent's own helpers (`hc run` from it): no labels, and Herdr shows them in their parent's tab.
        lead = self.core.agent("spS", name="lead")
        helper = self.core.agent(parent=lead["address"], name="helper")
        nested = self.core.agent(parent=helper["address"], name="nested")
        # Started by a terminal in no space: in none.
        loose = self.core.agent()
        self.core.agent(parent=loose["address"], name="stray")
        self.core.document(space_doc(("spS", "api", str(self.repo), [("", [lead["address"]])])))
        self.assertEqual([i["name"] for i in self.run_state("items", "spS")["items"]], ["lead", "helper", "nested"])
        self.assertEqual({(i["name"], i["context"]) for i in self.run_state("items")["items"]},
                         {("lead", "spS"), ("helper", "spS"), ("nested", "spS")})
        # No space of their own.
        self.assertEqual([c["id"] for c in self.run_state("contexts")["contexts"]], ["spS"])

    def test_a_title_is_the_task_until_the_tab_is_named(self):
        agent = self.core.agent("spT")
        self.core.say(agent["address"], "title \U000f03e2 Tesla Roadster  physics", "idle")
        self.core.until(agent["address"], lambda r: "Roadster" in (r.get("title") or ""), "its title")
        [item] = self.run_state("items", "spT")["items"]
        self.assertEqual(item["task"], "Tesla Roadster physics")
        self.core.document(space_doc(("spT", "", str(self.repo), [("dynamics", [agent["address"]])])))
        [item] = self.run_state("items", "spT")["items"]
        self.assertEqual(item["task"], "dynamics")

    def test_a_done_claudes_result_comes_from_its_session(self):
        agent = self.core.agent("spC")
        self.core.say(agent["address"], "working", "working")
        done = self.core.say(agent["address"], "done", "done")
        pid = self.core.call("terminal.get", terminal=agent["address"])["pid"]
        claude_session(self.home, pid, "s-42", "Opened #12 for the swipe fix.\n\n" + "More detail. " * 40)
        [item] = self.run_state("items", "spC")["items"]
        self.assertEqual(item["card"]["id"], f"{agent['address']}:{done['turn']}")
        self.assertTrue(item["card"]["text"].startswith("Opened #12 for the swipe fix."))
        # The start of it, cut at a word: Vux shows it on the row, and Hear reads the whole.
        self.assertTrue(item["card"]["text"].endswith("…"))
        self.assertLessEqual(len(item["card"]["text"]), 301)
        self.assertIn(item["card"]["text"][:-1], "Opened #12 for the swipe fix.\n\n" + "More detail. " * 40)
        self.assertEqual(item["card"]["text"][-2:-1].strip(), item["card"]["text"][-2:-1])
        self.assertEqual(self.run_state("reply", agent["address"]).strip(), ("Opened #12 for the swipe fix.\n\n" + "More detail. " * 40).strip())
        [session] = self.run_state("sessions")["sessions"]
        self.assertEqual(session, {"id": agent["address"], "name": agent["address"],
                                   "path": str(self.home / ".claude/projects/-repo/s-42.jsonl")})

    def test_a_reply_written_just_after_done_is_read(self):
        # Claude Code reports done about 0.1 s before its reply is in its transcript: read at once (the watcher's line,
        # a hand-off's wait returning), the reply is waited for, not missed.
        agent = self.core.agent("spF")
        self.core.say(agent["address"], "working", "working")
        pid = self.core.call("terminal.get", terminal=agent["address"])["pid"]
        transcript = claude_session(self.home, pid, "s-7", "First.")
        with transcript.open("a") as lines:
            lines.write(json.dumps({"type": "user", "message": {"content": "Again"}}) + "\n")
        entry = {"type": "assistant", "message": {"id": "m2", "stop_reason": "end_turn",
                                                  "content": [{"type": "text", "text": "Second."}]}}
        self.core.call("terminal.send", terminal=agent["address"], force=True,
                       text=f"done-then-write {transcript} {json.dumps(entry)}")
        self.core.until(agent["address"], lambda r: (r.get("status") or {}).get("state") == "done", "done")
        self.assertEqual(self.run_state("reply", agent["address"]).strip(), "Second.")

    def test_a_codex_result_needs_its_one_session_in_the_folder(self):
        agent = self.core.agent("spX", app="codex", cwd=self.repo)
        self.core.say(agent["address"], "working", "working")
        self.core.say(agent["address"], "done", "done")
        # An older session in the same folder, from before the terminal started, isn't its.
        old = codex_rollout(self.home, self.repo, "Old.", "old", datetime(2020, 1, 1, tzinfo=timezone.utc))
        codex_rollout(self.home, self.repo, "Merged.", "new")
        self.assertEqual(self.run_state("reply", agent["address"]).strip(), "Merged.")
        old.unlink()
        # Two sessions there since it started: which is its is unknown, so the screen is read instead.
        codex_rollout(self.home, self.repo, "Other.", "other")
        done = self.run_state("reply", agent["address"], check=False)
        self.assertEqual(done.returncode, 3, done.stderr)

    def test_an_agent_without_a_session_file_has_no_reply_to_read(self):
        agent = self.core.agent("spP", app="omp")
        done = self.run_state("reply", agent["address"], check=False)
        self.assertEqual(done.returncode, 3)
        self.assertIn("read the screen", done.stderr)

    def test_a_marked_agent_gets_no_result_card_and_realert_clears_it(self):
        agent = self.core.agent("spM", name="handed")
        self.core.say(agent["address"], "working", "working")
        self.core.say(agent["address"], "done", "done")
        self.run_state("mark", "handed")
        labels = self.core.call("terminal.get", terminal=agent["address"])["labels"]
        self.assertLess(abs(int(labels["herdr-core-voice.handed-off"]) / 1000 - time.time()), 60)
        [item] = self.run_state("items", "spM")["items"]
        self.assertIsNone(item["card"])
        # The watcher's word once that task finished: still the skill's to report.
        self.core.call("terminal.label", terminal=agent["address"],
                       labels={"herdr-core-voice.handed-off": f"finished:{int(time.time() * 1000)}"})
        self.assertIsNone(self.run_state("items", "spM")["items"][0]["card"])
        self.run_state("realert", agent["address"])
        labels = self.core.call("terminal.get", terminal=agent["address"])["labels"]
        self.assertNotIn("herdr-core-voice.handed-off", labels)
        self.assertIn("herdr-core-voice.realert", labels)
        self.assertEqual(self.run_state("items", "spM")["items"][0]["card"]["kind"], "result")
        # A mark older than a hand-off lasts is no mark.
        self.core.call("terminal.label", terminal=agent["address"],
                       labels={"herdr-core-voice.handed-off": str(int((time.time() - 13 * 3600) * 1000))})
        self.assertEqual(self.run_state("items", "spM")["items"][0]["card"]["kind"], "result")

    def test_space_is_the_repos_else_a_new_one(self):
        fresh = self.run_state("space", str(self.repo))
        self.assertTrue(fresh["new"])
        self.assertRegex(fresh["space"], r"^sp[0-9a-f]{32}$")
        self.assertNotEqual(self.run_state("space", str(self.repo))["space"], fresh["space"])
        # An agent working in the repo (here in a folder inside it): its space, and it's busy.
        inside = self.core.agent("spW", cwd=self.repo / "src")
        # A folder that only contains the repo doesn't count.
        self.core.agent("spOuter", cwd=self.home)
        self.assertEqual(self.run_state("space", str(self.repo)), {"space": "spW", "new": False, "busy": [inside["address"]]})
        # A space named for the repo, with no agent in the repo.
        self.core.clear()
        self.core.document(space_doc(("spNamed", "API", str(self.home), [])))
        self.assertEqual(self.run_state("space", str(self.repo)), {"space": "spNamed", "new": False, "busy": []})
        self.assertEqual(self.run_state("folder", "spNamed").strip(), str(self.home))

    def test_create_opens_a_shell_in_a_new_space_for_the_repo_once(self):
        made = self.run_state("create", "api")["context"]
        self.assertEqual((made["name"], made["location"]), ("api", HERE))
        [shell] = [t for t in self.core.call("terminal.list")["terminals"]]
        self.assertEqual((shell["labels"], shell["cwd"], shell["keep"]), ({"herdr.space": made["id"]}, str(self.repo), True))
        # It's Herdr's space now (Herdr makes it when it sees the terminal): asking again finds it.
        self.assertEqual(self.run_state("create", "api")["context"], made)
        self.assertIn(made, self.run_state("contexts")["contexts"])
        self.assertEqual(len(self.core.call("terminal.list")["terminals"]), 1)
        self.assertEqual(self.run_state("folder", made["id"]).strip(), str(self.repo))
        unknown = self.run_state("create", "nowhere", check=False)
        self.assertEqual(unknown.returncode, 1)
        self.assertIn("no repo named nowhere", unknown.stderr)

    def test_no_core_is_an_error_not_an_empty_list(self):
        env = {"PATH": os.getenv("PATH"), "HOME": str(self.home), "HC_SOCKET": str(self.home / "none.sock")}
        done = subprocess.run([sys.executable, "scripts/vux_state.py", "items"], cwd=SKILL_DIR, env=env,
                              capture_output=True, text=True, timeout=60)
        self.assertEqual(done.returncode, 1)
        self.assertIn("herdr-core isn't running", done.stderr)
        self.assertEqual(done.stdout, "")

    def test_running_leaves_the_skills_folder_as_installed(self):
        cache = SKILL_DIR / "scripts" / "__pycache__"
        shutil.rmtree(cache, ignore_errors=True)
        self.run_state("items")
        self.assertFalse(cache.exists(), "running the script wrote bytecode into the skill's folder")


if __name__ == "__main__":
    unittest.main()
