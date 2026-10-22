"""scripts/vux_state.py end to end: the script runs as vux-agent runs it (a subprocess, in the skill's folder), against
a stand-in `herdr` on PATH that answers from recorded JSON and logs every call."""
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent / "skills" / "herdr-voice"
SCRIPT = "scripts/vux_state.py"

# The stand-in: `herdr [--machine M] <command...>` prints answers[<machine>|<command>] and logs the call. A missing
# answer for a machine fails like an unreachable one.
FAKE_HERDR = r'''#!/usr/bin/env python3
import json, os, sys
args = sys.argv[1:]
machine = None
if args[:1] == ["--machine"]:
    machine, args = args[1], args[2:]
with open(os.path.join(os.path.dirname(__file__), "calls.log"), "a") as log:
    log.write(json.dumps({"machine": machine, "args": args}) + "\n")
answers = json.load(open(os.path.join(os.path.dirname(__file__), "answers.json")))
key = (machine or "") + "|" + " ".join(args)
if key not in answers:
    sys.stderr.write("no answer for " + key)
    sys.exit(1)
answer = answers[key]
sys.stdout.write(answer if isinstance(answer, str) else json.dumps(answer))
'''

SCREEN = "Do you want to proceed?\n ❯ 1. Yes\n   2. No   \n\n"
SCREEN_TEXT = "Do you want to proceed?\n ❯ 1. Yes\n   2. No"


def result(value):
    return {"id": "cli", "result": value}


AGENTS = [
    {"agent": "claude", "agent_status": "working", "name": "apple-models", "pane_id": "w5M:p1", "tab_id": "w5M:t1",
     "workspace_id": "w5M", "terminal_title_stripped": "Fixing the swipe test", "completion_seq": 10},
    {"agent": "codex", "agent_status": "blocked", "pane_id": "w5M:p2", "tab_id": "w5M:t2", "workspace_id": "w5M",
     "terminal_title_stripped": "Bash prompt"},
    {"agent": "claude", "agent_status": "done", "pane_id": "w5M:p3", "tab_id": "w5M:t2", "workspace_id": "w5M",
     "terminal_title_stripped": "PR opened", "completion_seq": 42},
    {"agent": "omp", "agent_status": "idle", "pane_id": "w5M:p4", "tab_id": "w5M:t3", "workspace_id": "w5M",
     "terminal_title_stripped": "π > \U000f03e2 hark", "cwd": "/home/me/hark"},
    {"agent": "pi", "agent_status": "unknown", "pane_id": "w1P:p8", "tab_id": "w1P:t7", "workspace_id": "w1P",
     "terminal_title_stripped": ""},
    {"agent": "claude", "agent_status": "done", "name": "handed", "pane_id": "w5M:p5", "tab_id": "w5M:t3",
     "workspace_id": "w5M", "terminal_title_stripped": "Tests pass", "completion_seq": 7},
]
PANES = [
    {"pane_id": "w5M:p1", "label": "Fixing the swipe test", "terminal_title_stripped": "Fixing the swipe test"},
    {"pane_id": "w5M:p2", "label": "reviewer", "terminal_title_stripped": "Bash prompt"},
    {"pane_id": "w5M:p3", "label": "PR opened", "terminal_title_stripped": "PR opened"},
    {"pane_id": "w5M:p4"},
]
TABS = [{"tab_id": "w5M:t1", "workspace_id": "w5M", "label": "1 · Fixing"}, {"tab_id": "w1P:t7", "workspace_id": "w1P", "label": "1 · x"},
        {"tab_id": "w5M:t2", "workspace_id": "w5M", "label": "2 · PR"}, {"tab_id": "w5M:t3", "workspace_id": "w5M", "label": "mine"}]
WORKSPACES = [{"workspace_id": "w1P", "label": "asterism"}, {"workspace_id": "w5M", "label": "hark"}, {"workspace_id": "w9"},
              {"workspace_id": "wBE", "label": "~"}]


# The start of the done Claude's last reply, for Vux's row: 300 characters at most, cut at a word.
RESULT_TEXT = "Opened #12 for the swipe fix.\nAll checks green.\n\n" + "More detail. " * 18 + "More detail.…"


class VuxState(unittest.TestCase):
    def setUp(self):
        self.folder = Path(tempfile.mkdtemp())
        self.bin = self.folder / "bin"
        self.bin.mkdir()
        self.home = self.folder / "home"
        (self.home / "github.com" / "tensor-systems" / "hark").mkdir(parents=True)
        (self.bin / "herdr").write_text(FAKE_HERDR)
        (self.bin / "herdr").chmod(0o755)
        self.answers = {
            "|workspace list": result({"workspaces": WORKSPACES}),
            "|machine list --json": json.dumps([
                {"id": "616e", "label": "vega@vega", "enabled": True}, {"id": "aaaa", "label": "old", "enabled": False},
                {"id": "bbbb", "label": "gone", "enabled": True}]),
            "vega@vega|workspace list": result({"workspaces": [{"workspace_id": "w2", "label": "hark"}]}),
            "|agent list": result({"agents": AGENTS}),
            "|pane list": result({"panes": PANES}),
            "|tab list": result({"tabs": TABS}),
            "|agent read w5M:p2 --source visible": SCREEN,
            "vega@vega|agent list": result({"agents": [{"agent": "claude", "agent_status": "working", "pane_id": "w2:p1",
                                                        "tab_id": "w2:t1", "workspace_id": "w2", "terminal_title_stripped": "Port"}]}),
            "vega@vega|pane list": result({"panes": []}),
            "vega@vega|tab list": result({"tabs": [{"tab_id": "w2:t1", "workspace_id": "w2", "label": "1 · Port"}]}),
        }
        # herdr-voice marked this one handed off: Hermes reports its finish itself, so it has no result card.
        marks = self.home / ".cache" / "herdr-voice" / "handed-off"
        marks.mkdir(parents=True)
        (marks / "handed.finished").touch()
        # The done Claude's session file, as herdr records it: its last reply is the result card's text.
        session = self.home / ".claude" / "projects" / "-hark" / "s-42.jsonl"
        session.parent.mkdir(parents=True)
        session.write_text("\n".join(json.dumps(entry) for entry in [
            {"type": "user", "message": {"content": "Open the PR"}},
            {"type": "assistant", "message": {"id": "m1", "stop_reason": "end_turn", "content": [
                {"type": "text", "text": "Opened #12 for the swipe fix.\nAll checks green.\n\n" + "More detail. " * 40}]}},
        ]) + "\n")
        self.agents = json.loads(json.dumps(AGENTS))
        self.agents[2]["agent_session"] = {"agent": "claude", "kind": "id", "value": "s-42"}
        self.answers["|agent list"] = result({"agents": self.agents})

    def run_state(self, *args, check=True):
        (self.bin / "answers.json").write_text(json.dumps(self.answers))
        env = {"PATH": f"{self.bin}:{os.path.dirname(sys.executable)}:/usr/bin:/bin", "HOME": str(self.home)}
        done = subprocess.run([sys.executable, SCRIPT, *args], cwd=SKILL_DIR, env=env, capture_output=True, text=True, timeout=30)
        if check:
            self.assertEqual(done.returncode, 0, done.stderr)
            return json.loads(done.stdout)
        return done

    def calls(self):
        log = self.bin / "calls.log"
        return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []

    def test_contexts_are_this_machines_workspaces_only(self):
        here = self.run_state("contexts")["contexts"]
        local = os.uname().nodename.split(".")[0]
        self.assertEqual(here, [
            {"id": "w1P", "name": "asterism", "location": local},
            {"id": "w5M", "name": "hark", "location": local},
            {"id": "w9", "name": "Workspace w9", "location": local},
            # The home folder's workspace, labelled "~" by herdr.
            {"id": "wBE", "name": "Home", "location": local},
        ])
        # Another machine's workspaces are that machine's computer's in Vux: its own desk alerts report them live.
        self.assertEqual([c for c in self.calls() if c["machine"] or c["args"][:1] == ["machine"]], [])

    def test_items_are_the_workspaces_agents(self):
        items = self.run_state("items", "w5M")["items"]
        question = hashlib.sha256(SCREEN_TEXT.encode()).hexdigest()[:16]
        self.assertEqual(items, [
            # The name it was started with; its kind and what it's on below.
            {"id": "w5M:p1", "name": "apple-models", "state": "working", "detail": "Claude · Fixing the swipe test"},
            # No name: its pane's label, once renamed (not the terminal title), with the question read from its screen.
            {"id": "w5M:p2", "name": "reviewer", "state": "waiting", "detail": "Codex · Bash prompt",
             "card": {"kind": "question", "id": question, "screen": SCREEN_TEXT}},
            # Neither: what it's working on; done with a result the user hasn't heard.
            {"id": "w5M:p3", "name": "PR opened", "state": "done", "detail": "Claude",
             "card": {"kind": "result", "id": "w5M:p3:42", "text": RESULT_TEXT}},
            # No task yet (its title only names its folder): its kind and tab, as herdr's sidebar shows the tab.
            {"id": "w5M:p4", "name": "omp · mine", "state": "idle", "detail": "omp"},
            # Handed off by herdr-voice: Hermes tells its result in the conversation, so no card.
            {"id": "w5M:p5", "name": "handed", "state": "done", "detail": "Claude · Tests pass"},
        ])

    def test_running_leaves_the_skills_folder_as_installed(self):
        # Hermes calls a changed file in a skill's folder a local edit and won't update the skill over it.
        cache = SKILL_DIR / "scripts" / "__pycache__"
        shutil.rmtree(cache, ignore_errors=True)
        self.run_state("items")
        self.assertFalse(cache.exists(), "running the script wrote bytecode into the skill's folder")

    def test_items_of_every_workspace_say_theirs(self):
        items = self.run_state("items")["items"]
        self.assertEqual([(i["context"], i["id"]) for i in items], [
            ("w5M", "w5M:p1"), ("w5M", "w5M:p2"), ("w5M", "w5M:p3"), ("w5M", "w5M:p4"), ("w1P", "w1P:p8"), ("w5M", "w5M:p5")])
        self.assertEqual(next(i for i in items if i["id"] == "w1P:p8")["state"], "idle")
        self.assertEqual([c for c in self.calls() if c["machine"] or c["args"][:1] == ["machine"]], [])

    def test_create_finds_the_repo_and_opens_its_workspace_without_focus(self):
        self.answers["|workspace list"] = result({"workspaces": [w for w in WORKSPACES if w.get("label") != "hark"]})
        self.answers["|workspace create --cwd {} --label hark --no-focus".format(self.home / "github.com/tensor-systems/hark")] = \
            result({"workspace": {"workspace_id": "wN", "label": "hark"}, "root_pane": {"pane_id": "wN:p1"}})
        made = self.run_state("create", "hark")
        self.assertEqual(made, {"context": {"id": "wN", "name": "hark", "location": os.uname().nodename.split(".")[0]}})

    def test_create_with_a_name_thats_no_repo_or_on_another_machine(self):
        # Not a repo: it opens in the home folder, never wherever herdr would (a checkout other agents work in).
        self.answers[f"|workspace create --cwd {self.home} --label notes --no-focus"] = \
            result({"workspace": {"workspace_id": "wP", "label": "notes"}})
        self.answers["vega@vega|workspace create --cwd ~ --label docs --no-focus"] = result({"workspace": {"workspace_id": "w7", "label": "docs"}})
        self.assertEqual(self.run_state("create", "notes")["context"]["id"], "wP")
        self.assertEqual(self.run_state("create", "docs", "vega@vega"),
                         {"context": {"id": "vega@vega/w7", "name": "docs", "location": "vega@vega"}})
        # Its home folder there (herdr expands ~ on that machine), never wherever its herdr would open.
        # The machine as a person says it: its label's user or host part.
        self.assertEqual(self.run_state("create", "docs", "vega")["context"]["id"], "vega@vega/w7")
        unknown = self.run_state("create", "hark", "nowhere", check=False)
        self.assertNotEqual(unknown.returncode, 0)
        self.assertIn("nowhere", unknown.stderr)

    def test_create_opens_the_workspace_that_has_the_name_already(self):
        # "Open hark on vega" when vega has a hark workspace: that one, not a second.
        self.assertEqual(self.run_state("create", "hark", "vega"),
                         {"context": {"id": "vega@vega/w2", "name": "hark", "location": "vega@vega"}})
        self.assertEqual(self.run_state("create", "Hark")["context"]["id"], "w5M")
        # "Home", as Vux shows the home folder's workspace, is that one ("~").
        self.assertEqual(self.run_state("create", "home")["context"], {"id": "wBE", "name": "Home",
                                                                       "location": os.uname().nodename.split(".")[0]})
        self.assertFalse([c for c in self.calls() if c["args"][:2] == ["workspace", "create"]], "a second workspace")

    def test_realert_runs_the_desk_alerts_plugin(self):
        plugin = self.folder / "plugin"
        plugin.mkdir()
        (plugin / "desk_alert.py").write_text("import sys, pathlib\npathlib.Path(sys.argv[0]).with_name('ran').write_text(' '.join(sys.argv[1:]))\n")
        self.answers["|plugin list --json"] = result({"plugins": [{"plugin_id": "herdr.auto-title", "plugin_root": "/x"},
                                                                  {"plugin_id": "herdr-voice.desk-alerts", "plugin_root": str(plugin)}]})
        self.run_state("realert", "w5M:p2", check=False)
        self.assertEqual((plugin / "ran").read_text(), "realert w5M:p2")

    def test_realert_hands_the_pane_back_to_the_plugin(self):
        # Answered from its card, the agent was marked as the agent's to report; it asked another question, so the
        # plugin alerts for it again, and its finish is the plugin's to report: realert clears its marks, by pane id
        # and by its agent's name, and leaves other agents' alone. The skill never runs `rm` for this.
        marks = self.home / ".cache" / "herdr-voice" / "handed-off"
        for name in ("w5M:p2", "w5M:p2.finished", "reviewer-bot", "w5M:p1", "apple-models"):
            (marks / name).touch()
        self.answers["|agent get w5M:p2"] = result({"agent": {**AGENTS[1], "name": "reviewer-bot"}})
        self.answers["|plugin list --json"] = result({"plugins": [{"plugin_id": "herdr-voice.desk-alerts",
                                                                   "plugin_root": str(self.folder / "none")}]})
        self.run_state("realert", "w5M:p2", check=False)
        self.assertEqual(sorted(p.name for p in marks.iterdir()), ["apple-models", "handed.finished", "w5M:p1"])
        # Without the plugin, the marks are cleared all the same, and it says why no alert comes.
        (marks / "w5M:p2").touch()
        self.answers["|plugin list --json"] = result({"plugins": []})
        done = self.run_state("realert", "w5M:p2", check=False)
        self.assertNotEqual(done.returncode, 0)
        self.assertIn("desk-alerts plugin isn't installed", done.stderr)
        self.assertFalse((marks / "w5M:p2").exists())

    def test_a_marked_agent_gets_no_result_card(self):
        # `mark` is how the skill hands a task off: the agent reports that result in its conversation, so the row
        # doesn't offer it too.
        done = self.run_state("mark", "w5M:p3", check=False)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertTrue((self.home / ".cache" / "herdr-voice" / "handed-off" / "w5M:p3").exists())
        item = next(i for i in self.run_state("items", "w5M")["items"] if i["id"] == "w5M:p3")
        self.assertNotIn("card", item)

    def test_names_tell_agents_apart_and_a_cut_title_is_no_label(self):
        # Two omps in one tab with no task yet, two Claudes on the same task, and a pane whose label is only its
        # terminal title cut short (herdr does that at 50).
        title = "Build SAFT investor reconciliation table for the auditors"
        self.answers["|agent list"] = result({"agents": [
            {"agent": "omp", "agent_status": "idle", "pane_id": "w7F:p1", "tab_id": "w7F:t1", "workspace_id": "w7F",
             "terminal_title_stripped": "π - cybertruck", "cwd": "/home/me/cybertruck"},
            {"agent": "omp", "agent_status": "idle", "pane_id": "w7F:p3", "tab_id": "w7F:t1", "workspace_id": "w7F",
             "terminal_title_stripped": ""},
            {"agent": "omp", "agent_status": "idle", "pane_id": "w7F:p4", "tab_id": "w7F:t1", "workspace_id": "w7F",
             "terminal_title_stripped": title},
            {"agent": "claude", "agent_status": "idle", "pane_id": "w7F:p5", "tab_id": "w7F:t2", "workspace_id": "w7F",
             "terminal_title_stripped": "Fix the flaky test"},
            {"agent": "claude", "agent_status": "working", "pane_id": "w7F:p6", "tab_id": "w7F:t2", "workspace_id": "w7F",
             "terminal_title_stripped": "Fix the flaky test"}]})
        self.answers["|pane list"] = result({"panes": [{"pane_id": "w7F:p4", "label": title[:50],
                                                         "terminal_title_stripped": title}]})
        self.answers["|tab list"] = result({"tabs": [{"tab_id": "w7F:t1", "workspace_id": "w7F", "label": "1 · x"},
                                                     {"tab_id": "w7F:t2", "workspace_id": "w7F", "label": "2 · y"}]})
        names = [i["name"] for i in self.run_state("items", "w7F")["items"]]
        self.assertEqual(names, ["omp · 1", "omp · 1 · 2", "Build SAFT investor reconciliation…",
                                 "Fix the flaky test", "Fix the flaky test · 2"])
        self.assertNotIn(title[:50], names, "A cut terminal title isn't the user's label")

    def test_unnamed_agents_are_named_for_their_task(self):
        # Titles as Claude Code, omp, Grok and Codex set them (seen on a desk), and what Vux shows for each: the task
        # as the name, the kind below it.
        agents = [
            ("claude", "Tesla Roadster physics simulation", "/u/shanev/asterism"),
            ("claude", "1Password service account for modelrelay secrets", "/u/me"),
            ("omp", "π > \U000f03e2 CLIP: Improve MOLLE clip design", "/u/shanev/cybertruck"),
            ("grok", "Southern Loop Trip: Tampa Over Ohio and Myrtle Beach - grok", "/u/shanev/obsidian"),
            ("codex", "Check today's cybersecurity issues | cozy", "/u/tensor-systems/cozy"),
            ("codex", "⠹ Run curl header command | desk-alert-zZma", "/tmp/desk-alert-zZma"),
            # Before its first prompt: the title names only the agent.
            ("claude", "Claude Code", "/u/shanev/asterism"),
        ]
        self.answers["|agent list"] = result({"agents": [
            {"agent": kind, "agent_status": "idle", "pane_id": f"w1P:p{n}", "tab_id": f"w1P:t{n}", "workspace_id": "w1P",
             "terminal_title_stripped": title, "cwd": cwd} for n, (kind, title, cwd) in enumerate(agents)]})
        self.answers["|pane list"] = result({"panes": []})
        self.answers["|tab list"] = result({"tabs": [{"tab_id": f"w1P:t{n}", "workspace_id": "w1P", "label": f"{n + 1} · x"}
                                                     for n in range(len(agents))]})
        items = self.run_state("items", "w1P")["items"]
        self.assertEqual([(i["name"], i["detail"]) for i in items], [
            ("Tesla Roadster physics simulation", "Claude"),
            # Cut at a word to fit a phone's line, not on a word that leaves it hanging.
            ("1Password service account…", "Claude"),
            ("CLIP: Improve MOLLE clip design", "omp"),
            ("Southern Loop Trip: Tampa Over Ohio…", "Grok"),
            ("Check today's cybersecurity issues", "Codex"),
            ("Run curl header command", "Codex"),
            ("Claude · 7", "Claude"),
        ])
        self.assertTrue(all(len(i["name"]) <= 40 for i in items))

    def test_a_working_agents_name_holds(self):
        # Claude's spinner and omp's status icon redraw the title many times a second; the name the user hears and says
        # mustn't move with them.
        names = []
        for claude, omp in [("✳ Newly added models tweet", "π > \U000f03e2 SKY: Explain why the sky is blue"),
                            ("◑ Newly added models tweet", "π > \U000f0547 SKY: Explain why the sky is blue"),
                            ("⠋ Newly added models tweet", "π > SKY: Explain why the sky is blue")]:
            self.answers["|agent list"] = result({"agents": [
                {"agent": "claude", "agent_status": "working", "pane_id": "wC3:p2", "tab_id": "wC3:t2", "workspace_id": "wC3",
                 "terminal_title_stripped": claude},
                {"agent": "omp", "agent_status": "working", "pane_id": "wC3:p3", "tab_id": "wC3:t2", "workspace_id": "wC3",
                 "terminal_title_stripped": omp}]})
            self.answers["|pane list"] = result({"panes": []})
            self.answers["|tab list"] = result({"tabs": [{"tab_id": "wC3:t2", "workspace_id": "wC3", "label": "2 · x"}]})
            names.append([i["name"] for i in self.run_state("items", "wC3")["items"]])
        self.assertEqual(names, [["Newly added models tweet", "SKY: Explain why the sky is blue"]] * 3)

    def test_icon_font_glyphs_are_dropped(self):
        # Nerd Font icons (private use) have no glyph on the phone, and speech would read them.
        self.answers["|agent list"] = result({"agents": [
            {"agent": "omp", "agent_status": "working", "pane_id": "w7F:p1", "tab_id": "w7F:t1", "workspace_id": "w7F",
             "name": "clip\U000f03e2", "terminal_title_stripped": "π > \U000f03e2 CLIP: Improve MOLLE clip design \ue0b0"}]})
        self.answers["|pane list"] = result({"panes": []})
        self.answers["|tab list"] = result({"tabs": [{"tab_id": "w7F:t1", "workspace_id": "w7F", "label": "1 · x"}]})
        [item] = self.run_state("items", "w7F")["items"]
        self.assertEqual(item["detail"], "omp · CLIP: Improve MOLLE clip design")
        self.assertEqual(item["name"], "clip")

    def test_herdr_failing_is_an_error_not_an_empty_list(self):
        del self.answers["|agent list"]
        failed = self.run_state("items", "w5M", check=False)
        self.assertNotEqual(failed.returncode, 0)
        self.assertIn("agent list", failed.stderr)


if __name__ == "__main__":
    unittest.main()
