"""skills/herdr-core-voice/SKILL.md: what Vux, vux-agent and Hermes read from it, and the recipes' rules."""
import re
import unittest
from pathlib import Path

from test_skill import _block

ROOT = Path(__file__).resolve().parent.parent
SKILL = ROOT / "skills" / "herdr-core-voice" / "SKILL.md"
TEXT = SKILL.read_text(encoding="utf-8")


def frontmatter():
    match = re.match(r"^---\n(.*?)\n---\n", TEXT, re.S)
    assert match, "SKILL.md must start with YAML frontmatter"
    value, rest = _block([line for line in match.group(1).splitlines() if line.strip()], 0)
    assert not rest, rest
    return value


class Frontmatter(unittest.TestCase):
    def test_name_and_description(self):
        self.assertEqual(frontmatter()["name"], SKILL.parent.name)
        # Hermes keeps 60 characters of a description.
        description = frontmatter()["description"]
        self.assertLessEqual(len(description), 60, description)
        for word in ("Herdr", "Claude", "Codex", "any coding agent"):
            self.assertIn(word, description)
        # Hermes' skill scan parses only the first 4000 characters.
        self.assertLess(TEXT.index("\n---\n", 3), 4000)

    def test_vux_and_hark_say_the_same(self):
        # Vux, vux-agent and vux-auth read metadata.vux; Hark-era clients only metadata.hark.
        metadata = frontmatter()["metadata"]
        self.assertEqual(list(metadata), ["vux", "hark"])
        self.assertEqual(metadata["vux"], metadata["hark"])

    def test_describes_itself_to_vux(self):
        vux = frontmatter()["metadata"]["vux"]
        self.assertIs(vux["voice"], True)
        self.assertEqual(vux["title"], "Herdr")
        self.assertLessEqual(len(vux["summary"]), 120)
        # Offered only where herdr-core is: vux-agent starts the watcher and runs the commands only then.
        self.assertEqual(vux["requires"]["command"], "hc")
        self.assertEqual(vux["requires"]["capabilities"], ["background_tasks", "memory"])
        self.assertLessEqual(len(vux["phrases"]), 6)
        self.assertLessEqual(len(vux["routing"]), 300)
        # Both skills may be installed on a computer with both Herdrs: each names its own and the other's.
        self.assertIn("herdr-core-voice", vux["routing"])
        self.assertIn("herdr-voice's", vux["routing"])

    def test_declares_its_spaces_agents_and_watcher(self):
        vux = frontmatter()["metadata"]["vux"]
        for command, word in [(vux["contexts"]["list"], "contexts"), (vux["contexts"]["create"], "create"),
                              (vux["items"]["list"], "items")]:
            self.assertEqual(command, ["python3", "scripts/vux_state.py", word])
        self.assertEqual(vux["watch"], ["python3", "scripts/watch.py"])
        for command in (vux["items"]["list"], vux["watch"]):
            self.assertTrue((SKILL.parent / command[1]).exists())
        scope = vux["contexts"]["scope"]
        self.assertLessEqual(len(scope), 300)
        self.assertEqual(sorted(re.findall(r"\{[^}]*\}", scope)), ["{id}", "{location}", "{name}"])
        self.assertIn("herdr-core-voice", scope)
        self.assertEqual(vux["items"]["version"], 2)
        self.assertEqual(vux["items"]["push"], ["waiting", "done"])


class Installs(unittest.TestCase):
    def test_no_blocked_tokens(self):
        # Hermes' skills guard blocks an install on these, even in prose.
        for path in [SKILL, *SKILL.parent.glob("scripts/*.py")]:
            content = path.read_text(encoding="utf-8")
            for token in ("authorized_keys", "~/.ssh", "printenv", "os.environ"):
                self.assertNotIn(token, content, path.name)

    def test_scripts_are_referenced(self):
        # Hermes installs only the scripts/ files SKILL.md names, at the start of a word or code span.
        for path in SKILL.parent.glob("scripts/*.py"):
            self.assertRegex(TEXT, rf"(?:^|[\s`\"'(]){re.escape(f'scripts/{path.name}')}")

    def test_last_reply_is_herdr_voices(self):
        # Skills install on their own, so each has the session-file reader; they must stay one reader.
        ours = (SKILL.parent / "scripts" / "last_reply.py").read_bytes()
        theirs = (ROOT / "skills" / "herdr-voice" / "scripts" / "last_reply.py").read_bytes()
        self.assertEqual(ours, theirs, "skills/herdr-core-voice/scripts/last_reply.py differs from herdr-voice's")


class Recipes(unittest.TestCase):
    def test_new_sessions_show_in_herdr_with_the_window_closed(self):
        # herdr-app's own commands need its window; a terminal labelled with a space shows there whenever it opens.
        self.assertIn("hc run --bg --no-parent --keep --name <name> --cwd <folder> --label herdr.space=<space>", TEXT)
        self.assertNotIn("tab.new", TEXT)
        self.assertNotIn("space.new", TEXT)
        self.assertIn("scripts/vux_state.py space <repo_path>", TEXT)
        self.assertIn("--no-daemon", TEXT)

    def test_hands_off_the_wait_and_marks_it(self):
        section = TEXT.split("\n**Send a task**")[1].split("\n**Read the result")[0]
        self.assertIn("scripts/vux_state.py mark <t>", section)
        self.assertIn("`delegate_task`", section)
        self.assertIn("hc wait <t> --timeout 540", section)
        blocked = next(line for line in TEXT.splitlines() if line.startswith("**Blocked**"))
        self.assertIn("the `mark` line", blocked)
        card = next(line for line in TEXT.splitlines() if line.startswith("- **[Card answer]:**"))
        self.assertIn("scripts/vux_state.py realert <t>", card)

    def test_blocked_uses_the_status_herdr_core_has(self):
        blocked = next(line for line in TEXT.splitlines() if line.startswith("**Blocked**"))
        for needle in ("`kind`", "`question`", "`permission`", "`msg`"):
            self.assertIn(needle, blocked)

    def test_waits_are_hc_waits_that_fit_the_shell_limits(self):
        # Hermes and vux-agent cap a shell command at 600 s, and vux-agent stops one at 60 s unless given longer.
        self.assertNotIn("--until", TEXT)
        for line in TEXT.splitlines():
            for seconds in re.findall(r"hc (?:wait|send --wait)[^`]*--timeout (\d+)", line):
                self.assertLess(int(seconds), 600)
                if int(seconds) >= 60:
                    self.assertRegex(line, r"shell tool's `timeout=\d+`")

    def test_never_takes_over_a_terminal_or_the_core(self):
        # The only mentions of these are the rule against them.
        for command in ("hc attach", "hc server stop"):
            for line in [line for line in TEXT.splitlines() if command in line]:
                self.assertIn("Never", line)
        self.assertEqual(TEXT.count("agent.focus"), 1)
        show = next(line for line in TEXT.splitlines() if line.startswith("**Show me an agent:**"))
        self.assertIn("only when the user asks", show)

    def test_vocabulary_maps_to_recipes(self):
        section = TEXT.split("## Voice vocabulary")[1].split("\n## ")[0]
        for recipe in re.findall(r"\*\*([^*]+)\*\*", section):
            self.assertIn(f"**{recipe}", TEXT.replace(section, ""), recipe)

    def test_not_tied_to_hermes_or_classic_herdr(self):
        self.assertNotIn("terminal tool", TEXT)
        self.assertIsNone(re.search(r"`herdr (agent|pane|workspace|tab)", TEXT))


if __name__ == "__main__":
    unittest.main()
