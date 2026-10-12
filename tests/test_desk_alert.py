import importlib.util
import json
import os
import tempfile
import time
import tomllib
import unittest
from pathlib import Path

PLUGIN = Path(__file__).resolve().parent.parent / "plugins" / "desk-alerts"
SKILL = Path(__file__).resolve().parent.parent / "skills" / "herdr-voice" / "SKILL.md"
spec = importlib.util.spec_from_file_location("desk_alert", PLUGIN / "desk_alert.py")
desk_alert = importlib.util.module_from_spec(spec)
spec.loader.exec_module(desk_alert)


class Manifest(unittest.TestCase):
    def test_hooks_agent_status_changes(self):
        manifest = tomllib.loads((PLUGIN / "herdr-plugin.toml").read_text(encoding="utf-8"))
        hook = ["python3", "desk_alert.py"]
        self.assertEqual(manifest["events"], [{"on": on, "command": hook} for on in
                                              ("pane.agent_status_changed", "pane.closed", "pane.exited", "pane.moved")])


class Transitions(unittest.TestCase):
    def test_only_the_end_of_work_alerts(self):
        self.assertTrue(desk_alert.is_alert("working", "done"))
        self.assertTrue(desk_alert.is_alert("working", "blocked"))
        # Startup (folder-trust prompt), focus and idle changes aren't news.
        for previous, status in [(None, "blocked"), ("unknown", "blocked"), ("blocked", "idle"),
                                 ("idle", "working"), ("done", "idle"), (None, "done"), ("blocked", "done")]:
            self.assertFalse(desk_alert.is_alert(previous, status), (previous, status))

    def test_statuses_are_remembered_per_pane(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "state" / "statuses.json"
            self.assertEqual(desk_alert.record_status(path, "w1:p1", "working"), (None, 1))
            self.assertEqual(desk_alert.record_status(path, "w1:p2", "idle"), (None, 1))
            self.assertEqual(desk_alert.record_status(path, "w1:p1", "done"), ("working", 2))
            self.assertEqual(desk_alert.record_status(path, "w1:p2", "working"), ("idle", 2))
            self.assertEqual(desk_alert.latest_event(path, "w1:p1"), 2)


class Resolved(unittest.TestCase):
    """A question is over once its pane leaves `blocked` or goes away: Hermes is told, once."""

    def run_hook(self, events, statuses=()):
        sent = []
        original = desk_alert.post, desk_alert.state_dir, desk_alert.config_for, desk_alert.running_agent
        try:
            with tempfile.TemporaryDirectory() as folder:
                state = Path(folder)
                for pane, status in statuses:
                    desk_alert.record_status(state / "statuses.json", pane, status)
                desk_alert.state_dir = lambda: state
                desk_alert.config_for = lambda state: {"key": "k", "url": "http://hermes"}
                desk_alert.post = lambda url, key, body, timeout: sent.append((url, body))
                # An agent back at work or done at the desk isn't away news here.
                desk_alert.running_agent = lambda pane: None
                for data in events:
                    os.environ["HERDR_PLUGIN_EVENT_JSON"] = json.dumps({"data": data})
                    desk_alert.main()
        finally:
            desk_alert.post, desk_alert.state_dir, desk_alert.config_for, desk_alert.running_agent = original
            os.environ.pop("HERDR_PLUGIN_EVENT_JSON", None)
        return sent

    def status(self, pane, status):
        return {"type": "pane_agent_status_changed", "pane_id": pane, "agent_status": status}

    def test_answered_or_quit_resolves_the_question(self):
        for status in ("working", "done", "idle"):
            sent = self.run_hook([self.status("w1:p1", status)], [("w1:p1", "working"), ("w1:p1", "blocked")])
            self.assertEqual(sent, [("http://hermes/v1/hark/desk/resolved", {"pane": "w1:p1"})], status)

    def test_only_a_blocked_pane_has_a_question(self):
        self.assertEqual(self.run_hook([self.status("w1:p1", "done")], [("w1:p1", "working")]), [])
        self.assertEqual(self.run_hook([self.status("w1:p1", "blocked")], [("w1:p1", "working")]), [])

    def test_a_closed_exited_or_moved_pane_resolves_its_question(self):
        for data in ({"type": "pane_closed", "pane_id": "w1:p1", "workspace_id": "w1"},
                     {"type": "pane_exited", "pane_id": "w1:p1", "workspace_id": "w1"},
                     {"type": "pane_moved", "previous_pane_id": "w1:p1", "previous_workspace_id": "w1",
                      "previous_tab_id": "w1:t1", "pane": {"pane_id": "w2:p4"}}):
            sent = self.run_hook([data, data], [("w1:p1", "blocked")])
            self.assertEqual(sent, [("http://hermes/v1/hark/desk/resolved", {"pane": "w1:p1"})], data["type"])
        closed = {"type": "pane_closed", "pane_id": "w1:p1", "workspace_id": "w1"}
        self.assertEqual(self.run_hook([closed], [("w1:p1", "done")]), [])

    def test_a_gateway_without_hark_auth_is_skipped(self):
        def refuse(url, key, body, timeout):
            raise desk_alert.urllib.error.HTTPError(url, 404, "Not Found", None, None)
        original = desk_alert.post
        desk_alert.post = refuse
        try:
            desk_alert.resolve({"key": "k", "url": "http://hermes"}, "w1:p1")
        finally:
            desk_alert.post = original


class HandOffs(unittest.TestCase):
    def test_a_hand_off_is_skipped_once(self):
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            (folder / "w1:p1").touch()
            now = time.time()
            self.assertTrue(desk_alert.take_hand_off(["w1:p1", None], now, folder))
            # The agent's next task, typed at the desk, is alerted.
            self.assertFalse(desk_alert.take_hand_off(["w1:p1", None], now, folder))

    def test_marked_by_name(self):
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            (folder / "hark-claude").touch()
            self.assertTrue(desk_alert.take_hand_off(["w1:p1", "hark-claude"], time.time(), folder))

    def test_an_old_mark_doesnt_count(self):
        with tempfile.TemporaryDirectory() as folder:
            marker = Path(folder) / "w1:p1"
            marker.touch()
            old = time.time() - desk_alert.HAND_OFF_TTL - 60
            os.utime(marker, (old, old))
            self.assertFalse(desk_alert.take_hand_off(["w1:p1"], time.time(), Path(folder)))
            self.assertFalse(marker.exists())

    def test_the_skill_marks_the_same_folder(self):
        self.assertIn("~/.cache/herdr-voice/handed-off/", SKILL.read_text(encoding="utf-8"))
        self.assertEqual(desk_alert.HANDED_OFF, Path.home() / ".cache" / "herdr-voice" / "handed-off")


class Away(unittest.TestCase):
    def test_idle_time_from_ioreg(self):
        output = '    |   "HIDIdleTime" = 312500000000\n    |   "HIDIdleTimeNotify" = 1\n'
        self.assertEqual(desk_alert.idle_seconds(output), 312.5)
        self.assertIsNone(desk_alert.idle_seconds(""))

    def test_away_after_the_threshold(self):
        self.assertFalse(desk_alert.is_away(60, 5))
        self.assertTrue(desk_alert.is_away(300, 5))
        self.assertTrue(desk_alert.is_away(0, 0))
        self.assertTrue(desk_alert.is_away(None, 5))


class Settings(unittest.TestCase):
    def test_hermes_defaults_and_overrides(self):
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            hermes = folder / "hermes.env"
            hermes.write_text('API_SERVER_KEY="secret"\nAPI_SERVER_PORT=9000\n', encoding="utf-8")
            self.assertEqual(desk_alert.settings(folder, hermes),
                             {"url": "http://127.0.0.1:9000", "key": "secret", "away_after_minutes": 5, "settle_seconds": 20})
            (folder / ".env").write_text("# mine\nexport AWAY_AFTER_MINUTES=0\nSETTLE_SECONDS=2\nHERMES_API_URL=https://mac.example/\n",
                                         encoding="utf-8")
            self.assertEqual(desk_alert.settings(folder, hermes),
                             {"url": "https://mac.example", "key": "secret", "away_after_minutes": 0, "settle_seconds": 2})


class Settling(unittest.TestCase):
    def setUp(self):
        self.delivered = []
        self.original = desk_alert.running_agent, desk_alert.labels, desk_alert.last_words, desk_alert.deliver
        desk_alert.labels = lambda agent: ("asterism", None, None)
        desk_alert.last_words = lambda pane, status: "Done."
        desk_alert.deliver = lambda config, message: self.delivered.append(message)

    def tearDown(self):
        desk_alert.running_agent, desk_alert.labels, desk_alert.last_words, desk_alert.deliver = self.original

    def settle(self, now, newer_event=False):
        desk_alert.running_agent = lambda pane: now
        with tempfile.TemporaryDirectory() as folder:
            statuses = Path(folder) / "statuses.json"
            desk_alert.record_status(statuses, "w1:p1", "working")
            _, event = desk_alert.record_status(statuses, "w1:p1", "done")
            if newer_event:
                desk_alert.record_status(statuses, "w1:p1", "working")
                desk_alert.record_status(statuses, "w1:p1", "done")
            desk_alert.alert_when_settled({"settle_seconds": 0}, "w1:p1", "done", statuses, event)

    def test_still_done_alerts(self):
        self.settle({"agent": "claude", "pane_id": "w1:p1", "agent_status": "done"})
        self.assertEqual(len(self.delivered), 1)

    def test_back_at_work_is_dropped(self):
        # Claude ended a reply while its own background command ran, then carried on.
        self.settle({"agent": "claude", "pane_id": "w1:p1", "agent_status": "working"})
        self.settle(None)
        self.assertEqual(self.delivered, [])

    def test_only_the_last_of_several_dones_alerts(self):
        # done, working, done again inside the settle time: the first done's alert is dropped.
        self.settle({"agent": "claude", "pane_id": "w1:p1", "agent_status": "done"}, newer_event=True)
        self.assertEqual(self.delivered, [])


FIXTURES = Path(__file__).resolve().parent / "fixtures"


class Questions(unittest.TestCase):
    agent = {"agent": "codex", "pane_id": "w4:p1", "cwd": "/src/x"}

    def data(self, message):
        line = next(line for line in message.splitlines() if line.startswith(desk_alert.QUESTION_PREFIX))
        return json.loads(line.removeprefix(desk_alert.QUESTION_PREFIX))

    def test_a_blocked_alert_carries_the_screen_as_data(self):
        # Any agent's dialog: the app reads the question from the screen, so nothing here parses it.
        screen = (FIXTURES / "claude_bash_prompt.txt").read_text(encoding="utf-8")
        data = self.data(desk_alert.alert_message(self.agent, ("asterism", "2", None), "blocked", screen))
        self.assertEqual((data["pane"], data["agent"], data["workspace"], data["tab"]), ("w4:p1", "codex", "asterism", "2"))
        self.assertEqual((data["name"], data["label"]), (None, None))
        self.assertIn("Do you want to proceed?\n ❯ 1. Yes", data["screen"])
        self.assertEqual(Path(data["script"]), PLUGIN / "desk_alert.py")
        # Data only. With the answer's wording in it, Hermes approved a prompt by itself, live.
        self.assertEqual(set(data), {"pane", "agent", "name", "label", "workspace", "tab", "screen", "script"})

    def test_the_screen_is_sent_once(self):
        message = desk_alert.alert_message(self.agent, ("asterism", None, None), "blocked", "Allow npm publish?\n 1. Yes\n 2. No")
        self.assertEqual(message.count("Allow npm publish?"), 1)
        self.assertIn("`screen` of the data line", message)

    def test_blank_lines_and_trailing_spaces_are_dropped(self):
        self.assertEqual(desk_alert.screen_text("a   \n\n  \n b\n"), "a\n b")
        self.assertEqual(len(desk_alert.screen_text("x" * 9000)), 6000)

    def test_a_finished_agent_has_no_question(self):
        message = desk_alert.alert_message(self.agent, ("asterism", "2", None), "done", "Tests pass.\n 1. Yes")
        self.assertNotIn(desk_alert.QUESTION_PREFIX, message)
        self.assertIn("Its last reply:\n\nTests pass.", message)


class Realert(unittest.TestCase):
    def test_delivers_for_the_agent_still_blocked(self):
        spawned = []
        original = desk_alert.deliver_detached, desk_alert.state_dir, desk_alert.config_for
        try:
            with tempfile.TemporaryDirectory() as folder:
                state = Path(folder)
                desk_alert.record_status(state / "statuses.json", "w1:p1", "working")
                desk_alert.record_status(state / "statuses.json", "w1:p1", "blocked")
                desk_alert.state_dir = lambda: state
                desk_alert.config_for = lambda state: {"key": "k"}
                desk_alert.deliver_detached = lambda *args: spawned.append(args[2:])
                desk_alert.realert("w1:p1")
        finally:
            desk_alert.deliver_detached, desk_alert.state_dir, desk_alert.config_for = original
        # The pane's own latest event: an answer that sets it working again drops this alert.
        self.assertEqual(spawned, [("w1:p1", "blocked", 2)])


class Message(unittest.TestCase):
    agent = {"agent": "claude", "pane_id": "w1:p8", "cwd": "/src/asterism"}

    def test_done(self):
        message = desk_alert.alert_message(self.agent, ("asterism", "2", None), "done", "Tests pass. PR #12 is open.\n")
        self.assertTrue(message.startswith(desk_alert.ALERT_PREFIX))
        self.assertIn('workspace "asterism", tab "2"', message)
        self.assertIn("finished its task", message)
        self.assertIn("Tests pass. PR #12 is open.", message)
        # The skill has no desk-alert section: the message says how to report it.
        self.assertIn("Tell the user in a sentence or two", message)

    def test_blocked(self):
        message = desk_alert.alert_message(self.agent, ("asterism", None, None), "blocked", "Allow npm publish? 1. Yes 2. No")
        self.assertIn('workspace "asterism" (pane', message)
        self.assertIn("waiting for an answer", message)
        self.assertIn("Don't answer the agent", message)
        # Answering through Hermes makes the agent's next finish Hermes' to report.
        self.assertIn("touch ~/.cache/herdr-voice/handed-off/w1:p8", message)

    def test_names_the_agent_by_its_name_and_pane_label(self):
        agent = {**self.agent, "name": "apple-models"}
        message = desk_alert.alert_message(agent, ("asterism", "2", "reviewer"), "blocked", "Allow npm publish? 1. Yes")
        # The start stays as hark-auth 1.6 and earlier match it: `The (\S+) agent in herdr workspace "…"`.
        self.assertIn('The claude agent in herdr workspace "asterism", tab "2" '
                      '(pane w1:p8, named "apple-models", pane label "reviewer", folder /src/asterism)', message)
        self.assertIn("never its pane id", message)
        data = json.loads(message.splitlines()[-1].removeprefix(desk_alert.QUESTION_PREFIX))
        self.assertEqual((data["name"], data["label"]), ("apple-models", "reviewer"))

    def test_a_pane_label_counts_only_once_renamed(self):
        panes = {"w1:p8": {"label": "Phase 8 design pass", "terminal_title_stripped": "Phase 8 design pass"},
                 "w1:p9": {"label": "reviewer", "terminal_title_stripped": "Phase 8 design pass"}}
        def herdr(kind, _, key):
            found = {"pane": panes.get(key), "workspace": {"label": "asterism"}, "tab": {"label": "1 · Phase 8"}}[kind]
            return 0, json.dumps({"result": {kind: found}})
        original = desk_alert.herdr
        try:
            desk_alert.herdr = herdr
            title_only = desk_alert.labels({**self.agent, "workspace_id": "w1", "tab_id": "w1:t1"})
            renamed = desk_alert.labels({**self.agent, "pane_id": "w1:p9", "workspace_id": "w1", "tab_id": "w1:t1"})
        finally:
            desk_alert.herdr = original
        self.assertEqual(title_only, ("asterism", "1 · Phase 8", None))
        self.assertEqual(renamed, ("asterism", "1 · Phase 8", "reviewer"))

    def test_marks_like_the_skill(self):
        skill = SKILL.read_text(encoding="utf-8")
        self.assertIn(desk_alert.hand_off_command("<agent>"), skill)


if __name__ == "__main__":
    unittest.main()
