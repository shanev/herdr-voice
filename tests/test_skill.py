import re
import unittest
from pathlib import Path

SKILL = Path(__file__).resolve().parent.parent / "skills" / "herdr-voice" / "SKILL.md"
TEXT = SKILL.read_text(encoding="utf-8")


def frontmatter():
    # The block-style YAML subset SKILL.md uses: nested mappings, "- item" lists, quoted scalars.
    match = re.match(r"^---\n(.*?)\n---\n", TEXT, re.S)
    assert match, "SKILL.md must start with YAML frontmatter"
    lines = [line for line in match.group(1).splitlines() if line.strip()]
    value, rest = _block(lines, 0)
    assert not rest, rest
    return value


def _block(lines, indent):
    if lines[0].lstrip().startswith("- "):
        items = []
        while lines and lines[0].startswith(" " * indent + "- "):
            items.append(_scalar(lines.pop(0).strip()[2:]))
        return items, lines
    fields = {}
    while lines and len(lines[0]) - len(lines[0].lstrip()) == indent:
        key, _, value = lines.pop(0).strip().partition(":")
        if value.strip():
            fields[key] = _scalar(value.strip())
        else:
            child = len(lines[0]) - len(lines[0].lstrip())
            fields[key], lines = _block(lines, child)
    return fields, lines


def _scalar(value):
    if value in ("true", "false"):
        return value == "true"
    return value[1:-1] if value[:1] == value[-1:] == '"' else value


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

    def test_hermes_reads_the_whole_frontmatter(self):
        # Hermes' skill scan parses only the first 4000 characters (tools/skills_tool.py).
        self.assertLess(TEXT.index("\n---\n", 3), 4000)

    def test_describes_itself_to_hark(self):
        # Hark's voice skill catalog and app read metadata.hark (heyhark.app/voice-skills.json).
        hark = frontmatter()["metadata"]["hark"]
        self.assertIs(hark["voice"], True)
        self.assertLessEqual(len(hark["summary"]), 120)
        self.assertEqual(hark["requires"], {"command": "herdr"})
        self.assertLessEqual(len(hark["phrases"]), 6)
        self.assertTrue(all(hark["phrases"]))
        self.assertLessEqual(len(hark["routing"]), 300)
        self.assertIn("herdr-voice", hark["routing"])


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
    def test_new_agents_keep_a_task_name(self):
        # Voice users address agents by what they're doing ("apple-models"), not by tab number.
        self.assertIn("herdr agent start <name>", TEXT)
        self.assertNotIn("--clear", TEXT)
        # new-<pane> is only the fallback when the request gives no usable phrase.
        self.assertIn("new-<pane>", TEXT)

    def test_workspace_comes_from_the_script(self):
        # Matching by hand, Hermes twice put a hv-scratch session in its parent folder's workspace.
        self.assertIn("scripts/repo_workspace.py <repo_path>", TEXT)

    def test_never_steals_focus(self):
        for line in TEXT.splitlines():
            if re.search(r"herdr (workspace|tab|worktree) (create|open)", line):
                self.assertIn("--no-focus", line)

    def test_hands_off_the_wait(self):
        # Waiting in the turn holds the user's voice conversation for up to 9 minutes.
        self.assertIn("hand the waiting to a background subagent with `delegate_task`", TEXT)
        self.assertIn("--until working", TEXT)

    def test_marks_hand_offs_for_desk_alerts(self):
        # plugins/desk-alerts skips agents with this mark: Hermes reports those itself.
        self.assertIn("touch ~/.cache/herdr-voice/handed-off/<agent>", TEXT)
        # Answering a stuck agent makes its finish Hermes' to report too.
        blocked = next(line for line in TEXT.splitlines() if line.startswith("**Blocked:**"))
        self.assertIn("the `touch` line above", blocked)

    def test_triage_sweeps_every_agent(self):
        # "What's waiting on me" reads each idle agent's reply, and leaves live prompts to Blocked.
        self.assertIn("**What's waiting on me:**", TEXT)
        section = TEXT.split("**What's waiting on me:**")[1].split("\n**")[0]
        self.assertIn("scripts/last_reply.py <agent>", section)
        self.assertIn("--source recent-unwrapped --lines 200", section)
        self.assertIn("**Blocked**", section)

    def test_batch_reports_once(self):
        # Per-agent waiters race a batch waiter; the parent speaks once, when the set is complete.
        self.assertIn("**Several agents at once:**", TEXT)
        section = TEXT.split("**Several agents at once:**")[1].split("\n**")[0]
        self.assertIn("`delegate_task` once per agent", section)
        self.assertIn("for the whole batch", section)
        self.assertIn("duplicates", section)

    def test_reports_cost_and_context_when_visible(self):
        self.assertIn("context percentage", TEXT)
        self.assertIn("context at six percent", TEXT)

    def test_recall_search_is_read_only(self):
        self.assertIn("**Where did we do this**", TEXT)
        section = TEXT.split("\n**Where did we do this**")[1].split("\n**")[0]
        for needle in ("agent_session", "~/.claude/projects/", "grep -l", "pane_id", "read-only"):
            self.assertIn(needle, section)

    def test_voice_vocabulary_maps_to_recipes(self):
        self.assertIn("## Voice vocabulary", TEXT)
        section = TEXT.split("## Voice vocabulary")[1].split("\n## ")[0]
        for phrase, recipe in [
            ('"status"', "**What's running**"),
            ('"waiting on me"', "**What's waiting on me**"),
            ('"where did we do X"', "**Where did we do this**"),
            ('"nudge X"', "herdr agent prompt <agent>"),
            ('"stop X"', "**Stop**"),
        ]:
            line = next(line for line in section.splitlines() if phrase in line)
            self.assertIn(recipe, line)
            self.assertIn(recipe.strip("*"), TEXT.replace(section, ""))

    def test_worktree_when_the_checkout_is_busy(self):
        self.assertIn("scripts/repo_workspace.py --busy <repo_path>", TEXT)
        self.assertIn("herdr worktree create --cwd <repo_path>", TEXT)

    def test_never_removes_a_worktree_unasked(self):
        self.assertIn("never remove a worktree on your own", TEXT)
        self.assertIn("pass `--force` only if they say to throw them away", TEXT)

    def test_merges_only_on_green(self):
        self.assertIn("gh pr checks <number> --watch --fail-fast", TEXT)
        self.assertIn("gh pr merge <number> --squash", TEXT)
        self.assertIn("check your memory for the user's merge choice", TEXT)

    def test_focus_only_when_asked(self):
        show = next(line for line in TEXT.splitlines() if line.startswith("**Show me an agent**"))
        self.assertIn("herdr agent focus <agent>", show)
        self.assertIn("only when the user asks", show)
        self.assertEqual(TEXT.count("herdr agent focus"), 1)

    def test_waits_fit_hermes_terminal_limit(self):
        # Hermes' foreground terminal limit is 600 s.
        for ms in re.findall(r"--timeout (\d+)", TEXT):
            self.assertLess(int(ms), 600_000)


if __name__ == "__main__":
    unittest.main()
