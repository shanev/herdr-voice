import re
import unittest
from pathlib import Path

SKILL = Path(__file__).resolve().parent.parent / "skills" / "herdr-voice" / "SKILL.md"
TEXT = SKILL.read_text(encoding="utf-8")


def frontmatter():
    match = re.match(r"^---\n(.*?)\n---\n", TEXT, re.S)
    assert match, "SKILL.md must start with YAML frontmatter"
    fields = {}
    for line in match.group(1).splitlines():
        key, _, value = line.partition(":")
        fields[key.strip()] = value.strip().strip('"')
    return fields


class Frontmatter(unittest.TestCase):
    def test_name_matches_folder(self):
        self.assertEqual(frontmatter()["name"], SKILL.parent.name)

    def test_description_fits_hermes_index(self):
        # Hermes keeps 60 characters (agent/skill_utils.py SKILL_PROMPT_DESC_LIMIT);
        # anything longer is cut to 57 plus "...".
        description = frontmatter()["description"]
        self.assertLessEqual(len(description), 60, description)
        for word in ("Herdr", "Claude", "Codex", "any coding agent"):
            self.assertIn(word, description)


class HermesScannerFriendly(unittest.TestCase):
    # Hermes' skills guard blocks an install on these tokens even in prose that forbids them.
    def test_no_blocked_tokens(self):
        for path in [SKILL, *SKILL.parent.glob("scripts/*.py")]:
            content = path.read_text(encoding="utf-8")
            for token in ("authorized_keys", "~/.ssh", "printenv", "os.environ"):
                self.assertNotIn(token, content, path.name)

    def test_scripts_are_referenced(self):
        # Hermes installs only the scripts/ files SKILL.md names, and only a path that starts a word or
        # code span counts (tools/skills_hub_models.py _LOCAL_LINK_RE), not ${HERMES_SKILL_DIR}/scripts/...
        for path in SKILL.parent.glob("scripts/*.py"):
            self.assertRegex(TEXT, rf"(?:^|[\s`\"'(]){re.escape(f'scripts/{path.name}')}")


class Recipes(unittest.TestCase):
    def test_new_agents_end_up_unnamed(self):
        self.assertIn("herdr agent rename <pane_id> --clear", TEXT)

    def test_workspace_comes_from_the_script(self):
        # Matching by hand, Hermes twice put a hv-scratch session in its parent folder's workspace.
        self.assertIn("scripts/repo_workspace.py <repo_path>", TEXT)

    def test_never_steals_focus(self):
        for line in TEXT.splitlines():
            if re.search(r"herdr (workspace|tab) create", line):
                self.assertIn("--no-focus", line)

    def test_hands_off_the_wait(self):
        # Waiting in the turn holds the user's voice conversation for up to 9 minutes.
        self.assertIn("hand the waiting to a background subagent with `delegate_task`", TEXT)
        self.assertIn("--until working", TEXT)

    def test_marks_hand_offs_for_desk_alerts(self):
        # plugins/desk-alerts skips agents with this mark: Hermes reports those itself.
        self.assertIn("touch ~/.cache/herdr-voice/handed-off/<agent>", TEXT)
        self.assertIn("[Desk agent alert]", TEXT)

    def test_waits_fit_hermes_terminal_limit(self):
        # Hermes' foreground terminal limit is 600 s.
        for ms in re.findall(r"--timeout (\d+)", TEXT):
            self.assertLess(int(ms), 600_000)


if __name__ == "__main__":
    unittest.main()
