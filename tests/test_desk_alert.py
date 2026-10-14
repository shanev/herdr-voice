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


CHANGED = "http://hermes/v1/skills/herdr-voice/changed"


class Resolved(unittest.TestCase):
    """A question is over once its pane leaves `blocked` or goes away: the agent is told its workspace changed, once,
    and withdraws the question."""

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
        return {"type": "pane_agent_status_changed", "pane_id": pane, "agent_status": status, "workspace_id": "w1"}

    def test_answered_or_quit_resolves_the_question(self):
        for status in ("working", "done", "idle"):
            sent = self.run_hook([self.status("w1:p1", status)], [("w1:p1", "working"), ("w1:p1", "blocked")])
            self.assertEqual(sent, [(CHANGED, {"context": "w1", "item": "w1:p1"})], status)

    def test_only_a_blocked_pane_has_a_question(self):
        self.assertEqual(self.run_hook([self.status("w1:p1", "done")], [("w1:p1", "working")]), [])
        self.assertEqual(self.run_hook([self.status("w1:p1", "blocked")], [("w1:p1", "working")]), [])

    def test_a_closed_exited_or_moved_pane_resolves_its_question(self):
        for data in ({"type": "pane_closed", "pane_id": "w1:p1", "workspace_id": "w1"},
                     {"type": "pane_exited", "pane_id": "w1:p1", "workspace_id": "w1"},
                     {"type": "pane_moved", "previous_pane_id": "w1:p1", "previous_workspace_id": "w1",
                      "previous_tab_id": "w1:t1", "pane": {"pane_id": "w2:p4"}}):
            sent = self.run_hook([data, data], [("w1:p1", "blocked")])
            self.assertEqual(sent, [(CHANGED, {"context": "w1", "item": "w1:p1"})], data["type"])
        closed = {"type": "pane_closed", "pane_id": "w1:p1", "workspace_id": "w1"}
        self.assertEqual(self.run_hook([closed], [("w1:p1", "done")]), [])

    def test_a_gateway_without_skill_state_is_skipped(self):
        def refuse(url, key, body, timeout):
            raise desk_alert.urllib.error.HTTPError(url, 404, "Not Found", None, None)
        original = desk_alert.post
        desk_alert.post = refuse
        try:
            desk_alert.changed({"key": "k", "url": "http://hermes"}, "w1", "w1:p1")
        finally:
            desk_alert.post = original


class Alerting(unittest.TestCase):
    """The hook, for an agent that stops: what it hands the detached settle-and-deliver step must still be the pane's
    latest event once it settles, or the alert drops itself (found live: saving the workspace counted as an event)."""

    def test_an_agent_that_stops_is_delivered_once_settled(self):
        spawned, sent = [], []
        names = ("deliver_detached", "state_dir", "config_for", "running_agent", "post", "take_hand_off")
        original = {n: getattr(desk_alert, n) for n in names}
        try:
            with tempfile.TemporaryDirectory() as folder:
                state = Path(folder)
                desk_alert.state_dir = lambda: state
                desk_alert.config_for = lambda state: {"key": "k", "url": "http://hermes", "away_after_minutes": 0,
                                                       "settle_seconds": 0}
                agent = {"agent": "claude", "pane_id": "w1:p1", "workspace_id": "w1", "agent_status": "blocked"}
                desk_alert.running_agent = lambda pane: agent
                desk_alert.take_hand_off = lambda names, now: False
                desk_alert.post = lambda url, key, body, timeout: sent.append((url, body))
                desk_alert.deliver_detached = lambda state, config, pane, status, event: spawned.append((config, pane, status, event))
                for status in ("working", "blocked"):
                    os.environ["HERDR_PLUGIN_EVENT_JSON"] = json.dumps({"data": {"type": "pane_agent_status_changed",
                                                                                 "pane_id": "w1:p1", "agent_status": status}})
                    desk_alert.main()
                [(config, pane, status, event)] = spawned
                desk_alert.alert_when_settled(config, pane, status, state / "statuses.json", event)
        finally:
            for n, f in original.items():
                setattr(desk_alert, n, f)
            os.environ.pop("HERDR_PLUGIN_EVENT_JSON", None)
        self.assertEqual(sent, [(CHANGED, {"context": "w1", "item": "w1:p1"})])


class HandOffs(unittest.TestCase):
    def test_a_hand_off_is_skipped_once(self):
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            (folder / "w1:p1").touch()
            now = time.time()
            self.assertTrue(desk_alert.take_hand_off(["w1:p1", None], now, folder))
            # The finish stays Hermes' to tell: hark_state.py gives it no result card.
            self.assertTrue((folder / "w1:p1.finished").exists())
            # The agent's next task, typed at the desk, is alerted, and its result is the user's to hear.
            self.assertFalse(desk_alert.take_hand_off(["w1:p1", None], now, folder))
            self.assertEqual(list(folder.iterdir()), [])

    def test_the_skills_state_script_reads_the_same_marks(self):
        spec = importlib.util.spec_from_file_location("hark_state", SKILL.parent / "scripts" / "hark_state.py")
        hark_state = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(hark_state)
        self.assertEqual(hark_state.HANDED_OFF, desk_alert.HANDED_OFF)
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            (folder / "apple-models").touch()
            desk_alert.take_hand_off(["w1:p1", "apple-models"], time.time(), folder)
            hark_state.HANDED_OFF = folder
            self.assertTrue(hark_state.handed_off({"pane_id": "w1:p1", "name": "apple-models"}, time.time()))
            self.assertFalse(hark_state.handed_off({"pane_id": "w1:p2"}, time.time()))

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

    def test_hark_agent_without_hermes(self):
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            agent = folder / ".hark-agent"
            agent.mkdir()
            (agent / "agent-key").write_text("agent-secret\n", encoding="utf-8")
            (agent / "env").write_text("PORT=8650\nWORKSPACE='/Users/me'\n", encoding="utf-8")
            self.assertEqual(desk_alert.settings(folder, folder / "no-hermes.env", agent),
                             {"url": "http://127.0.0.1:8650", "key": "agent-secret", "away_after_minutes": 5, "settle_seconds": 20})
            # With Hermes too, Hermes is told: one push per change.
            hermes = folder / "hermes.env"
            hermes.write_text("API_SERVER_KEY=secret\n", encoding="utf-8")
            self.assertEqual(desk_alert.settings(folder, hermes, agent)["key"], "secret")


class Settling(unittest.TestCase):
    def setUp(self):
        self.delivered = []
        self.original = desk_alert.running_agent, desk_alert.post
        desk_alert.post = lambda url, key, body, timeout: self.delivered.append((url, body))

    def tearDown(self):
        desk_alert.running_agent, desk_alert.post = self.original

    def settle(self, now, newer_event=False):
        desk_alert.running_agent = lambda pane: now
        with tempfile.TemporaryDirectory() as folder:
            statuses = Path(folder) / "statuses.json"
            desk_alert.record_status(statuses, "w1:p1", "working")
            _, event = desk_alert.record_status(statuses, "w1:p1", "done")
            if newer_event:
                desk_alert.record_status(statuses, "w1:p1", "working")
                desk_alert.record_status(statuses, "w1:p1", "done")
            desk_alert.alert_when_settled({"settle_seconds": 0, "key": "k", "url": "http://hermes"}, "w1:p1", "done",
                                          statuses, event)

    def test_still_done_tells_the_agent_its_workspace_changed(self):
        self.settle({"agent": "claude", "pane_id": "w1:p1", "agent_status": "done", "workspace_id": "w1"})
        self.assertEqual(self.delivered, [(CHANGED, {"context": "w1", "item": "w1:p1"})])

    def test_back_at_work_is_dropped(self):
        # Claude ended a reply while its own background command ran, then carried on.
        self.settle({"agent": "claude", "pane_id": "w1:p1", "agent_status": "working"})
        self.settle(None)
        self.assertEqual(self.delivered, [])

    def test_only_the_last_of_several_dones_alerts(self):
        # done, working, done again inside the settle time: the first done's alert is dropped.
        self.settle({"agent": "claude", "pane_id": "w1:p1", "agent_status": "done"}, newer_event=True)
        self.assertEqual(self.delivered, [])


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


class Marks(unittest.TestCase):
    def test_marks_like_the_skill(self):
        skill = SKILL.read_text(encoding="utf-8")
        self.assertIn(desk_alert.hand_off_command("<agent>"), skill)


if __name__ == "__main__":
    unittest.main()
