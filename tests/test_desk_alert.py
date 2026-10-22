import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import time
import tomllib
import unittest
from unittest import mock
from pathlib import Path

PLUGIN = Path(__file__).resolve().parent.parent / "plugins" / "desk-alerts"
SKILL = Path(__file__).resolve().parent.parent / "skills" / "herdr-voice" / "SKILL.md"

# A stand-in herdr, which the plugin runs from HERDR_BIN_PATH as herdr has it do: never the machine's own, whose agents a
# test must not rename. It answers from state.json beside it (a test's agents and workspaces; without one, every command
# fails), renames by herdr's rules (names match [a-z][a-z0-9_-]{0,31}, unique among live agents, `taken` naming any
# given meanwhile) and logs each call. `list_delay` slows `agent list`, so two hooks at once both list before either
# renames.
FAKE_HERDR = r'''#!/usr/bin/env python3
import fcntl, json, os, re, sys, time
here = os.path.dirname(os.path.abspath(__file__))
args = sys.argv[1:]
with open(os.path.join(here, "calls.log"), "a") as log:
    log.write(json.dumps(args) + "\n")
def fail(code):
    sys.stderr.write(json.dumps({"error": {"code": code, "message": " ".join(args)}, "id": "cli"}))
    sys.exit(1)
def reply(value):
    sys.stdout.write(json.dumps({"id": "cli", "result": value}))
path = os.path.join(here, "state.json")
if not os.path.exists(path):
    fail("no_stand_in_state")
state = json.load(open(path))
find = lambda agents, target: next((a for a in agents if target in (a["pane_id"], a.get("name"))), None)
if args == ["agent", "list"]:
    time.sleep(state.get("list_delay", 0))
    reply({"agents": state["agents"]})
elif args == ["workspace", "list"]:
    reply({"workspaces": state.get("workspaces", [])})
elif args[:2] == ["agent", "get"] and len(args) == 3:
    agent = find(state["agents"], args[2])
    reply({"agent": agent}) if agent else fail("agent_not_found")
elif args[:2] == ["agent", "rename"] and len(args) == 4:
    with open(path + ".lock", "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        state = json.load(open(path))
        agent, name = find(state["agents"], args[2]), args[3]
        if agent is None:
            fail("agent_not_found")
        if not re.fullmatch(r"[a-z][a-z0-9_-]{0,31}", name):
            fail("invalid_agent_name")
        if name in state.get("taken", []) or any(a is not agent and a.get("name") == name for a in state["agents"]):
            fail("agent_name_taken")
        agent["name"] = name
        json.dump(state, open(path, "w"))
        reply({"agent": agent})
else:
    fail("unknown_command")
'''
FAKE_DIR = Path(tempfile.mkdtemp(prefix="desk-alerts-herdr-"))
(FAKE_DIR / "herdr").write_text(FAKE_HERDR, encoding="utf-8")
(FAKE_DIR / "herdr").chmod(0o755)
os.environ["HERDR_BIN_PATH"] = str(FAKE_DIR / "herdr")


def fake_herdr(agents=(), workspaces=(), **more):
    """Gives the stand-in herdr these agents and workspaces, and clears its call log."""
    (FAKE_DIR / "state.json").write_text(json.dumps({"agents": list(agents), "workspaces": list(workspaces), **more}))
    (FAKE_DIR / "calls.log").unlink(missing_ok=True)


def herdr_calls():
    log = FAKE_DIR / "calls.log"
    return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []


def herdr_names():
    return {a["pane_id"]: a.get("name") for a in json.loads((FAKE_DIR / "state.json").read_text())["agents"]}


fake_herdr()
spec = importlib.util.spec_from_file_location("desk_alert", PLUGIN / "desk_alert.py")
desk_alert = importlib.util.module_from_spec(spec)
spec.loader.exec_module(desk_alert)


class Manifest(unittest.TestCase):
    def test_hooks_agent_status_changes(self):
        manifest = tomllib.loads((PLUGIN / "herdr-plugin.toml").read_text(encoding="utf-8"))
        hook = ["python3", "desk_alert.py"]
        self.assertEqual(manifest["events"], [{"on": on, "command": hook} for on in
                                              ("pane.agent_status_changed", "pane.agent_detected", "pane.closed", "pane.exited",
                                               "pane.moved", "workspace.created", "workspace.closed", "workspace.renamed",
                                               "workspace.moved", "workspace.reordered")])


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


class Hook(unittest.TestCase):
    """Runs the hook for herdr events, recording what it posts."""

    def run_hook(self, events, statuses=(), agents=(), workspaces=(), keep_herdr=False):
        """The hook for each event in turn, with the stand-in herdr holding `agents` (or as it is, `keep_herdr`);
        returns what it told Vux's agents. Naming watchers it would start are recorded in self.watching, not started."""
        sent, self.watching = [], []
        if not keep_herdr:
            fake_herdr(agents, workspaces)
        original = (desk_alert.post, desk_alert.state_dir, desk_alert.config_for, desk_alert.running_agent,
                    desk_alert.name_detached)
        try:
            with tempfile.TemporaryDirectory() as folder:
                state = Path(folder)
                for pane, status in statuses:
                    desk_alert.record_status(state / "statuses.json", pane, status)
                desk_alert.state_dir = lambda: state
                desk_alert.config_for = lambda state: {"agents": [{"key": "k", "url": "http://hermes"}]}
                desk_alert.post = lambda url, key, body, timeout: sent.append((url, body))
                # An agent back at work or done at the desk isn't away news here.
                desk_alert.running_agent = lambda pane: None
                desk_alert.name_detached = lambda state, pane: self.watching.append(pane)
                for data in events:
                    os.environ["HERDR_PLUGIN_EVENT_JSON"] = json.dumps({"data": data})
                    desk_alert.main()
        finally:
            (desk_alert.post, desk_alert.state_dir, desk_alert.config_for, desk_alert.running_agent,
             desk_alert.name_detached) = original
            os.environ.pop("HERDR_PLUGIN_EVENT_JSON", None)
        return sent

    def status(self, pane, status):
        return {"type": "pane_agent_status_changed", "pane_id": pane, "agent_status": status, "workspace_id": "w1"}


class Resolved(Hook):
    """A question is over once its pane leaves `blocked` or goes away: the agent is told its workspace changed, once,
    and withdraws the question."""

    def test_answered_or_quit_resolves_the_question(self):
        for status in ("working", "done", "idle"):
            sent = self.run_hook([self.status("w1:p1", status)], [("w1:p1", "working"), ("w1:p1", "blocked")])
            self.assertEqual(sent, [(CHANGED, {"context": "w1", "item": "w1:p1"})], status)

    def test_only_a_blocked_pane_has_a_question(self):
        # Not a resolved question: the change is only for Vux's open stream (an alert, if any, comes once settled).
        for status in ("done", "blocked"):
            self.assertEqual(self.run_hook([self.status("w1:p1", status)], [("w1:p1", "working")]),
                             [(CHANGED, {"context": "w1", "item": "w1:p1", "push": False})], status)

    def test_a_closed_exited_or_moved_pane_resolves_its_question(self):
        for data in ({"type": "pane_closed", "pane_id": "w1:p1", "workspace_id": "w1"},
                     {"type": "pane_exited", "pane_id": "w1:p1", "workspace_id": "w1"},
                     {"type": "pane_moved", "previous_pane_id": "w1:p1", "previous_workspace_id": "w1",
                      "previous_tab_id": "w1:t1", "pane": {"pane_id": "w2:p4"}}):
            sent = self.run_hook([data, data], [("w1:p1", "blocked")])
            self.assertEqual(sent, [(CHANGED, {"context": "w1", "item": "w1:p1"})], data["type"])
        closed = {"type": "pane_closed", "pane_id": "w1:p1", "workspace_id": "w1"}
        # An agent's pane that wasn't asking: gone from Vux's list, nothing to withdraw or push.
        self.assertEqual(self.run_hook([closed, closed], [("w1:p1", "done")]),
                         [(CHANGED, {"context": "w1", "item": "w1:p1", "push": False})])
        # A pane that never had an agent isn't on Vux's list.
        self.assertEqual(self.run_hook([closed]), [])

    def test_a_gateway_without_skill_state_is_skipped(self):
        def refuse(url, key, body, timeout):
            raise desk_alert.urllib.error.HTTPError(url, 404, "Not Found", None, None)
        original = desk_alert.post
        desk_alert.post = refuse
        try:
            desk_alert.changed({"agents": [{"key": "k", "url": "http://hermes"}]}, "w1", "w1:p1")
        finally:
            desk_alert.post = original


class Reports(Hook):
    """Vux doesn't poll: every change it shows is reported at once, for its open stream only (`push: false`)."""

    def test_every_status_change_is_reported_at_once(self):
        sent = self.run_hook([self.status("w1:p1", "idle"), self.status("w1:p1", "working"), self.status("w1:p1", "working"),
                              self.status("w1:p1", "done"), self.status("w1:p1", "idle")])
        stream = (CHANGED, {"context": "w1", "item": "w1:p1", "push": False})
        # The repeated status isn't a change.
        self.assertEqual(sent, [stream] * 4)

    def test_an_agent_detected_or_released_is_reported(self):
        for agent in ("claude", None):
            sent = self.run_hook([{"type": "pane_agent_detected", "pane_id": "w1:p3", "workspace_id": "w1", "agent": agent}])
            self.assertEqual(sent, [(CHANGED, {"context": "w1", "item": "w1:p3", "push": False})], agent)

    def test_workspaces_made_closed_renamed_or_moved_report_the_contexts(self):
        events = [{"type": "workspace_created", "workspace": {"workspace_id": "w7"}},
                  {"type": "workspace_closed", "workspace_id": "w7", "workspace": None},
                  {"type": "workspace_renamed", "workspace_id": "w1", "label": "hark"},
                  {"type": "workspace_moved", "workspace_id": "w1", "insert_index": 0, "workspaces": []},
                  {"type": "workspace_reordered", "workspace_ids": ["w1"], "workspaces": []}]
        for event in events:
            self.assertEqual(self.run_hook([event]), [(CHANGED, {"push": False})], event["type"])

    def test_a_focused_workspace_is_no_change(self):
        self.assertEqual(self.run_hook([{"type": "workspace_focused", "workspace_id": "w1"}]), [])


class Alerting(unittest.TestCase):
    """The hook, for an agent that stops: what it hands the detached settle-and-deliver step must still be the pane's
    latest event once it settles, or the alert drops itself (found live: saving the workspace counted as an event)."""

    def test_an_agent_that_stops_is_delivered_once_settled(self):
        spawned, sent = [], []
        names = ("deliver_detached", "state_dir", "config_for", "running_agent", "post", "take_hand_off")
        original = {n: getattr(desk_alert, n) for n in names}
        fake_herdr()
        try:
            with tempfile.TemporaryDirectory() as folder:
                state = Path(folder)
                desk_alert.state_dir = lambda: state
                desk_alert.config_for = lambda state: {"agents": [{"key": "k", "url": "http://hermes"}], "away_after_minutes": 0,
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
        # Each status for Vux's open stream at once, then the settled alert, which may push.
        self.assertEqual(sent, [(CHANGED, {"context": "w1", "item": "w1:p1", "push": False})] * 2
                         + [(CHANGED, {"context": "w1", "item": "w1:p1"})])


class HandOffs(unittest.TestCase):
    def test_a_hand_off_is_skipped_once(self):
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            (folder / "w1:p1").touch()
            now = time.time()
            self.assertTrue(desk_alert.take_hand_off(["w1:p1", None], now, folder))
            # The finish stays Hermes' to tell: vux_state.py gives it no result card.
            self.assertTrue((folder / "w1:p1.finished").exists())
            # The agent's next task, typed at the desk later, is alerted, and its result is the user's to hear.
            self.assertFalse(desk_alert.take_hand_off(["w1:p1", None], now + desk_alert.SAME_TASK_SECONDS + 1, folder))
            self.assertEqual(list(folder.iterdir()), [])

    def test_a_handed_off_task_finishing_twice_stays_handed_off(self):
        # Claude can end a reply, go back to work on what it started, and end again: one task, Hermes' to tell.
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            (folder / "w1:p1").touch()
            now = time.time()
            self.assertTrue(desk_alert.take_hand_off(["w1:p1", None], now, folder))
            self.assertTrue(desk_alert.take_hand_off(["w1:p1", None], now + 60, folder))
            self.assertTrue((folder / "w1:p1.finished").exists())
            # Long after, a finish is a new task from the desk.
            later = now + desk_alert.SAME_TASK_SECONDS + 120
            os.utime(folder / "w1:p1.finished", (now + 60, now + 60))
            self.assertFalse(desk_alert.take_hand_off(["w1:p1", None], later, folder))
            self.assertFalse((folder / "w1:p1.finished").exists())

    def test_the_skills_state_script_reads_the_same_marks(self):
        spec = importlib.util.spec_from_file_location("vux_state", SKILL.parent / "scripts" / "vux_state.py")
        vux_state = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(vux_state)
        self.assertEqual(vux_state.HANDED_OFF, desk_alert.HANDED_OFF)
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            (folder / "apple-models").touch()
            desk_alert.take_hand_off(["w1:p1", "apple-models"], time.time(), folder)
            vux_state.HANDED_OFF = folder
            self.assertTrue(vux_state.handed_off({"pane_id": "w1:p1", "name": "apple-models"}, time.time()))
            self.assertFalse(vux_state.handed_off({"pane_id": "w1:p2"}, time.time()))

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
            self.assertEqual(desk_alert.settings(folder, hermes, folder / "no-vux-agent"),
                             {"agents": [{"url": "http://127.0.0.1:9000", "key": "secret"}], "away_after_minutes": 5,
                              "settle_seconds": 20})
            (folder / ".env").write_text("# mine\nexport AWAY_AFTER_MINUTES=0\nSETTLE_SECONDS=2\nHERMES_API_URL=https://mac.example/\n",
                                         encoding="utf-8")
            # An agent of your own is told too, first, with Hermes' key unless you give one.
            self.assertEqual(desk_alert.settings(folder, hermes, folder / "no-vux-agent"),
                             {"agents": [{"url": "https://mac.example", "key": "secret"}, {"url": "http://127.0.0.1:9000", "key": "secret"}],
                              "away_after_minutes": 0, "settle_seconds": 2})
            (folder / ".env").write_text("HERMES_API_URL=http://127.0.0.1:9000\nHERMES_API_KEY=other\n", encoding="utf-8")
            self.assertEqual(desk_alert.settings(folder, hermes, folder / "no-vux-agent")["agents"],
                             [{"url": "http://127.0.0.1:9000", "key": "other"}])

    def test_every_agent_on_the_machine(self):
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            agent = folder / ".vux-agent"
            agent.mkdir()
            (agent / "agent-key").write_text("agent-secret\n", encoding="utf-8")
            (agent / "env").write_text("PORT=8650\nWORKSPACE='/Users/me'\n", encoding="utf-8")
            self.assertEqual(desk_alert.settings(folder, folder / "no-hermes.env", agent)["agents"],
                             [{"url": "http://127.0.0.1:8650", "key": "agent-secret"}])
            # Hermes and vux-agent on one Mac: both are told, Hermes first.
            hermes = folder / "hermes.env"
            hermes.write_text("API_SERVER_KEY=secret\n", encoding="utf-8")
            self.assertEqual(desk_alert.settings(folder, hermes, agent)["agents"],
                             [{"url": "http://127.0.0.1:8642", "key": "secret"}, {"url": "http://127.0.0.1:8650", "key": "agent-secret"}])
            self.assertEqual(desk_alert.settings(folder, folder / "no-hermes.env", folder / "none")["agents"], [])

    def test_vux_agent_then_the_one_from_before_the_rename(self):
        """Vux's agent lives in ~/.vux-agent; one installed while Vux was called Hark stays in ~/.hark-agent until
        setup runs again and moves it."""
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            vux, hark = folder / ".vux-agent", folder / ".hark-agent"
            hark.mkdir()
            (hark / "agent-key").write_text("hark-secret\n", encoding="utf-8")
            with mock.patch.object(desk_alert, "VUX_AGENT_HOMES", (vux, hark)):
                self.assertEqual(desk_alert.settings(folder, folder / "no-hermes.env")["agents"],
                                 [{"url": "http://127.0.0.1:8650", "key": "hark-secret"}])
                vux.mkdir()
                (vux / "agent-key").write_text("vux-secret\n", encoding="utf-8")
                (vux / "env").write_text("PORT=8651\n", encoding="utf-8")
                self.assertEqual(desk_alert.settings(folder, folder / "no-hermes.env")["agents"],
                                 [{"url": "http://127.0.0.1:8651", "key": "vux-secret"}])


class EveryAgent(unittest.TestCase):
    """Each agent on the machine serves Vux its own stream, so each hears of every change, at once: one that's slow or
    down holds up none of the others. Each may push it: an agent pushes only to the phones paired with it, and which
    agent that is (the computer the phone pairs) isn't this plugin's to know."""

    AGENTS = {"agents": [{"url": "http://hermes", "key": "h"}, {"url": "http://vux-agent", "key": "a"}]}

    def run_changed(self, *args, fail=(), slow=(), started=None):
        sent = []
        def post(url, key, body, timeout):
            if started is not None:
                started[url] = time.monotonic()
            if url.startswith(slow):
                time.sleep(1)
            sent.append((url, key, body))
            if url.startswith(fail):
                raise desk_alert.urllib.error.URLError("down")
        original = desk_alert.post
        desk_alert.post = post
        try:
            desk_alert.changed(self.AGENTS, *args)
        finally:
            desk_alert.post = original
        return sent

    def test_a_stream_change_goes_to_every_agent(self):
        self.assertEqual(self.run_changed("w1", "w1:p1", False), [
            ("http://hermes/v1/skills/herdr-voice/changed", "h", {"context": "w1", "item": "w1:p1", "push": False}),
            ("http://vux-agent/v1/skills/herdr-voice/changed", "a", {"context": "w1", "item": "w1:p1", "push": False})])
        self.assertEqual([body for _, _, body in self.run_changed(None, None, False)], [{"push": False}, {"push": False}])

    def test_every_agent_may_push(self):
        # Hermes left installed after the phone moved to vux-agent must not be the only one allowed to push.
        self.assertEqual(sorted((url, body) for url, _, body in self.run_changed("w1", "w1:p1")), [
            ("http://hermes/v1/skills/herdr-voice/changed", {"context": "w1", "item": "w1:p1"}),
            ("http://vux-agent/v1/skills/herdr-voice/changed", {"context": "w1", "item": "w1:p1"})])

    def test_one_agent_down_doesnt_stop_the_others(self):
        sent = self.run_changed("w1", "w1:p1", False, fail=("http://hermes",))
        self.assertEqual(sorted(url for url, _, _ in sent), ["http://hermes/v1/skills/herdr-voice/changed",
                                                             "http://vux-agent/v1/skills/herdr-voice/changed"])

    def test_a_slow_agent_doesnt_hold_up_the_others(self):
        started, begun = {}, time.monotonic()
        sent = self.run_changed("w1", "w1:p1", False, slow=("http://hermes",), started=started)
        self.assertLess(started["http://vux-agent/v1/skills/herdr-voice/changed"] - begun, 0.3)
        # vux-agent was told while Hermes was still answering; the hook still waits for both before it exits.
        self.assertEqual([url for url, _, _ in sent], ["http://vux-agent/v1/skills/herdr-voice/changed",
                                                       "http://hermes/v1/skills/herdr-voice/changed"])


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
            desk_alert.alert_when_settled({"settle_seconds": 0, "agents": [{"key": "k", "url": "http://hermes"}]}, "w1:p1", "done",
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
                desk_alert.config_for = lambda state: {"agents": [{"key": "k", "url": "http://hermes"}]}
                desk_alert.deliver_detached = lambda *args: spawned.append(args[2:])
                desk_alert.realert("w1:p1")
        finally:
            desk_alert.deliver_detached, desk_alert.state_dir, desk_alert.config_for = original
        # The pane's own latest event: an answer that sets it working again drops this alert.
        self.assertEqual(spawned, [("w1:p1", "blocked", 2)])


class Marks(unittest.TestCase):
    def mark(self, home, agent):
        subprocess.run([sys.executable, "scripts/vux_state.py", "mark", agent], cwd=SKILL.parent, check=True,
                       env={"HOME": str(home), "PATH": os.environ["PATH"]}, capture_output=True, timeout=30)

    def test_marks_like_the_skill(self):
        # The skill marks an agent with `vux_state.py mark`, and this plugin finds the mark where it looks.
        with tempfile.TemporaryDirectory() as home:
            folder = Path(home) / ".cache" / "herdr-voice" / "handed-off"
            self.mark(home, "w1:p1")
            self.mark(home, "apple-models")
            self.assertTrue(desk_alert.take_hand_off(["w1:p1", None], time.time(), folder))
            self.assertTrue(desk_alert.take_hand_off(["w2:p1", "apple-models"], time.time(), folder))
            self.assertFalse(desk_alert.take_hand_off(["w3:p1", "other"], time.time(), folder))

    def test_marking_again_is_a_fresh_mark(self):
        # A mark counts for 12 hours from when the task was handed off: the next task's mark starts the clock again.
        with tempfile.TemporaryDirectory() as home:
            folder = Path(home) / ".cache" / "herdr-voice" / "handed-off"
            self.mark(home, "w1:p1")
            old = time.time() - desk_alert.HAND_OFF_TTL - 60
            os.utime(folder / "w1:p1", (old, old))
            self.mark(home, "w1:p1")
            self.assertTrue(desk_alert.take_hand_off(["w1:p1"], time.time(), folder))

def desk_agent(pane, title, status="idle", kind="claude", **more):
    """An agent as `herdr agent list` shows one started at the desk: no name."""
    return {"agent": kind, "agent_status": status, "pane_id": pane, "workspace_id": pane.split(":")[0],
            "terminal_title_stripped": title, "cwd": "/u/me/hark", **more}


class Naming(Hook):
    """Every agent gets a herdr agent name, once: herdr-voice names one it starts by voice; this plugin names one started
    at the desk from its first task, with the skill's rule, and never renames an agent with a name."""

    def renames(self):
        return [call[2:] for call in herdr_calls() if call[:2] == ["agent", "rename"]]

    def test_a_desk_agent_is_named_from_its_first_task_once(self):
        agents = [desk_agent("w1:p1", "✳ Tesla Roadster physics simulation for the demo", "working")]
        sent = self.run_hook([self.status("w1:p1", "working")], agents=agents)
        self.assertEqual(self.renames(), [["w1:p1", "tesla-roadster-physics"]])
        self.assertEqual(sent, [(CHANGED, {"context": "w1", "item": "w1:p1", "push": False})])
        # Its next events, and a next task with a new title, leave the name as it is: it holds for the agent's life.
        state = json.loads((FAKE_DIR / "state.json").read_text())
        state["agents"][0]["terminal_title_stripped"] = "✳ Fix the flaky swipe test"
        (FAKE_DIR / "state.json").write_text(json.dumps(state))
        (FAKE_DIR / "calls.log").unlink()
        self.run_hook(keep_herdr=True, events=[self.status("w1:p1", "done"), self.status("w1:p1", "working")])
        self.assertEqual(self.renames(), [])
        self.assertEqual(herdr_names(), {"w1:p1": "tesla-roadster-physics"})

    def test_an_agent_with_a_name_is_never_renamed(self):
        # The voice recipe's names (new-<pane> for an empty prompt too) and the user's own.
        agents = [desk_agent("w1:p1", "Fixing the swipe test", "working", name="apple-models"),
                  desk_agent("w1:p2", "PR opened", "done", name="new-w1-p2"),
                  desk_agent("w1:p3", "Something", "idle", name="mine")]
        self.run_hook([self.status(p, s) for p, s in (("w1:p1", "done"), ("w1:p2", "idle"), ("w1:p3", "working"))],
                      agents=agents)
        self.assertEqual(self.renames(), [])
        self.assertEqual(herdr_names(), {"w1:p1": "apple-models", "w1:p2": "new-w1-p2", "w1:p3": "mine"})

    def test_no_task_no_name(self):
        # Before its first prompt the title names only the agent or its folder: it stays unnamed (new-<pane> to Vux),
        # and a watcher names it once the title shows.
        agents = [desk_agent("w1:p1", "✳ Claude Code", "working"), desk_agent("w1:p2", "π - hark", "idle", kind="omp"),
                  desk_agent("w1:p3", "grok", "blocked", kind="grok")]
        self.run_hook([self.status("w1:p1", "working"), self.status("w1:p2", "idle")], agents=agents)
        self.assertEqual(self.renames(), [])
        self.assertEqual(self.watching, ["w1:p1"])

    def test_names_are_unique_among_herdrs_agents(self):
        # herdr's names are unique among its live agents, in every workspace; one named since the list counts too.
        agents = [desk_agent("w1:p1", "Fix the flaky test"), desk_agent("w2:p1", "Fix the flaky test"),
                  desk_agent("w3:p1", "Anything", name="fix-flaky-test")]
        fake_herdr(agents, taken=["fix-flaky-test-2"])
        self.run_hook(keep_herdr=True, events=[self.status("w1:p1", "working")])
        self.assertEqual(herdr_names(), {"w1:p1": "fix-flaky-test-3", "w2:p1": "fix-flaky-test-4", "w3:p1": "fix-flaky-test"})

    def test_every_unnamed_agent_with_a_task_is_named_at_any_event(self):
        # Agents already running when the plugin is installed, or titled after their last event, are named at the next
        # event on the server, and Vux hears of each; one just detected waits (its title may still be its shell's).
        agents = [desk_agent("w1:p1", "Carry out prop colliders | hark", "working", kind="codex"),
                  desk_agent("w2:p4", "Recover Swiftfin Apple TV Jellyfin password - grok", kind="grok"),
                  desk_agent("w2:p5", "blockshane@mac: ~/github.com/shanev/asterism")]
        sent = self.run_hook([{"type": "pane_agent_detected", "pane_id": "w2:p5", "workspace_id": "w2", "agent": "claude"}],
                             agents=agents)
        self.assertEqual(herdr_names(), {"w1:p1": "carry-out-prop-colliders", "w2:p4": "recover-swiftfin-apple-tv",
                                         "w2:p5": None})
        self.assertEqual(sent, [(CHANGED, {"context": "w1", "item": "w1:p1", "push": False}),
                                (CHANGED, {"context": "w2", "item": "w2:p4", "push": False}),
                                (CHANGED, {"context": "w2", "item": "w2:p5", "push": False})])

    def test_pane_labels_are_neither_read_nor_written(self):
        # They're the user's and herdr.auto-title's ("4 · hark › Implement OAuth scopes"), rewritten as work changes.
        agents = [desk_agent("w1:p1", "Implement OAuth scopes", "working", label="4 · hark › Implement OAuth scopes")]
        self.run_hook([self.status("w1:p1", "working")], agents=agents)
        self.assertEqual(herdr_names(), {"w1:p1": "implement-oauth-scopes"})
        self.assertEqual([c for c in herdr_calls() if c[:1] in (["pane"], ["tab"])], [])

    def test_two_hooks_at_once_name_an_agent_once(self):
        # herdr runs a hook per event, several at the same moment: each lists the agents before either renames
        # (the stand-in's list is slow), and only the first may name it.
        fake_herdr([desk_agent("w1:p1", "Tesla Roadster physics simulation", "done")], list_delay=0.5)
        with tempfile.TemporaryDirectory() as folder:
            hooks = []
            for status in ("done", "idle", "working"):
                event = {"data": {"type": "pane_agent_status_changed", "pane_id": "w1:p1", "agent_status": status,
                                  "workspace_id": "w1"}}
                env = {"PATH": os.environ["PATH"], "HOME": folder, "HERDR_BIN_PATH": str(FAKE_DIR / "herdr"),
                       "HERDR_PLUGIN_STATE_DIR": folder, "HERDR_PLUGIN_CONFIG_DIR": folder,
                       "HERDR_PLUGIN_EVENT_JSON": json.dumps(event)}
                hooks.append(subprocess.Popen([sys.executable, str(PLUGIN / "desk_alert.py")], env=env,
                                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
            for hook in hooks:
                hook.wait(timeout=30)
        self.assertEqual(self.renames(), [["w1:p1", "tesla-roadster-physics"]])

    def test_the_watcher_names_the_agent_once_its_title_shows(self):
        fake_herdr([desk_agent("w1:p1", "✳ Claude Code", "working")])
        sent, naps = [], []

        def nap(seconds):
            # Claude titles its terminal a moment after the prompt.
            naps.append(seconds)
            if len(naps) == 2:
                state = json.loads((FAKE_DIR / "state.json").read_text())
                state["agents"][0]["terminal_title_stripped"] = "✳ Newly added models tweet"
                (FAKE_DIR / "state.json").write_text(json.dumps(state))

        original = desk_alert.post
        try:
            desk_alert.post = lambda url, key, body, timeout: sent.append((url, body))
            with tempfile.TemporaryDirectory() as folder:
                desk_alert.name_when_titled(Path(folder), {"agents": [{"key": "k", "url": "http://hermes"}]}, "w1:p1",
                                            sleep=nap)
        finally:
            desk_alert.post = original
        # Looked twice: untitled, then titled and named; then it's done.
        self.assertEqual(naps, [desk_alert.NAME_EVERY_SECONDS] * 2)
        self.assertEqual(herdr_names(), {"w1:p1": "newly-added-models-tweet"})
        self.assertEqual(sent, [(CHANGED, {"context": "w1", "item": "w1:p1", "push": False})])

    def test_herdr_failing_names_nothing_and_alerts_go_on(self):
        (FAKE_DIR / "state.json").unlink(missing_ok=True)
        with tempfile.TemporaryDirectory() as folder:
            self.assertEqual(desk_alert.name_agents(Path(folder)), ([], []))

    def test_the_rule_is_the_skills(self):
        # One rule for the agents the skill names by voice and the ones this plugin names: the skill's own file.
        self.assertEqual(Path(desk_alert.agent_names.__file__).resolve(),
                         (SKILL.parent / "scripts" / "agent_names.py").resolve())


class Slugs(unittest.TestCase):
    """The names the rule makes of real tasks (titles from a desk), by herdr's rules for names."""

    def test_short_kebab_case_from_the_task(self):
        names = desk_alert.agent_names
        for title, kind, name in [
            ("Tesla Roadster physics simulation", "claude", "tesla-roadster-physics"),
            ("Starship 15 simulation on simkit", "claude", "starship-15-simulation"),
            ("CLIP: Improve MOLLE clip design", "omp", "clip-improve-molle-clip"),
            ("Check today's cybersecurity issues", "codex", "check-todays-cybersecurity"),
            ("Café menu redesign", "claude", "cafe-menu-redesign"),
            # A name starts with a letter: a task that starts with a digit gets the kind first.
            ("1Password service account for modelrelay secrets", "claude", "claude-1password-service"),
            ("2026 roadmap", "omp", "omp-2026-roadmap"),
            ("Supercalifragilisticexpialidocious-and-then-some words", "claude", "supercalifragilisticexpialid"),
        ]:
            got = names.slug(title, kind)
            self.assertEqual(got, name, title)
            self.assertRegex(got, r"^[a-z][a-z0-9_-]{0,31}$")
            self.assertLessEqual(len(names.unique(got, {got, *(f"{got}-{n}" for n in range(2, 100))})), 32)
        self.assertIsNone(names.slug("修复登录", "claude"))

    def test_an_untitled_agent_has_no_name_yet(self):
        names = desk_alert.agent_names
        self.assertIsNone(names.name_for(desk_agent("w1P:p8", "Claude Code"), set()))
        self.assertEqual(names.name(desk_agent("w1P:p8", "Claude Code")), "new-w1p-p8")
        # A task with no letters to name it by: the placeholder, given for good.
        self.assertEqual(names.name_for(desk_agent("w1P:p8", "修复登录"), set()), "new-w1p-p8")


if __name__ == "__main__":
    unittest.main()
