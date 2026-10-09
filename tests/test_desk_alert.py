import importlib.util
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
        self.assertEqual(manifest["events"], [{"on": "pane.agent_status_changed", "command": ["python3", "desk_alert.py"]}])


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
        desk_alert.labels = lambda agent: ("asterism", None)
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


class Message(unittest.TestCase):
    agent = {"agent": "claude", "pane_id": "w1:p8", "cwd": "/src/asterism"}

    def test_done(self):
        message = desk_alert.alert_message(self.agent, ("asterism", "2"), "done", "Tests pass. PR #12 is open.\n")
        self.assertTrue(message.startswith(desk_alert.ALERT_PREFIX))
        self.assertIn('workspace "asterism", tab "2"', message)
        self.assertIn("finished its task", message)
        self.assertIn("Tests pass. PR #12 is open.", message)

    def test_blocked(self):
        message = desk_alert.alert_message(self.agent, ("asterism", None), "blocked", "Allow npm publish? 1. Yes 2. No")
        self.assertIn('workspace "asterism" (pane', message)
        self.assertIn("waiting for an answer", message)
        self.assertIn("Don't answer it", message)


if __name__ == "__main__":
    unittest.main()
