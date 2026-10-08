import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "skills" / "herdr-voice" / "scripts" / "last_reply.py"
spec = importlib.util.spec_from_file_location("last_reply", SCRIPT)
last_reply = importlib.util.module_from_spec(spec)
spec.loader.exec_module(last_reply)


def jsonl(*entries):
    return [json.dumps(entry) + "\n" for entry in entries]


def prompt(text):
    return {"type": "user", "message": {"role": "user", "content": text}}


def tool_result():
    return {"type": "user", "message": {"role": "user", "content": [{"type": "tool_result", "content": "ok"}]}}


def assistant(message_id, stop, *blocks, sidechain=False):
    return {"type": "assistant", "isSidechain": sidechain,
            "message": {"id": message_id, "role": "assistant", "stop_reason": stop, "content": list(blocks)}}


def text(value):
    return {"type": "text", "text": value}


THINKING = {"type": "thinking", "thinking": "..."}
TOOL_USE = {"type": "tool_use", "name": "Bash", "input": {}}


class ClaudeReply(unittest.TestCase):
    def test_final_message_of_a_finished_turn(self):
        lines = jsonl(
            prompt("fix the test"),
            assistant("m1", "tool_use", text("Let me look at the test."), TOOL_USE),
            tool_result(),
            assistant("m2", "end_turn", THINKING),
            assistant("m2", "end_turn", text("Fixed it. All 12 tests pass.")),
        )
        self.assertEqual(last_reply.claude_reply(lines), "Fixed it. All 12 tests pass.")

    def test_still_working_has_no_reply(self):
        lines = jsonl(
            prompt("fix the test"),
            assistant("m1", "tool_use", text("Let me look at the test."), TOOL_USE),
            tool_result(),
        )
        self.assertIsNone(last_reply.claude_reply(lines))

    def test_reply_belongs_to_the_latest_prompt(self):
        lines = jsonl(
            prompt("first task"),
            assistant("m1", "end_turn", text("First done.")),
            prompt("second task"),
        )
        self.assertIsNone(last_reply.claude_reply(lines))

    def test_subagent_messages_are_ignored(self):
        lines = jsonl(
            prompt("review it"),
            assistant("m1", "end_turn", text("Review looks clean.")),
            assistant("s1", "end_turn", text("subagent chatter"), sidechain=True),
        )
        self.assertEqual(last_reply.claude_reply(lines), "Review looks clean.")

    def test_meta_user_entries_are_not_prompts(self):
        lines = jsonl(
            prompt("ship it"),
            assistant("m1", "end_turn", text("Shipped.")),
            {"type": "user", "isMeta": True, "message": {"role": "user", "content": "caveat"}},
        )
        self.assertEqual(last_reply.claude_reply(lines), "Shipped.")

    def test_text_split_across_entries_is_joined(self):
        lines = jsonl(
            prompt("summarize"),
            assistant("m1", "end_turn", text("Part one.")),
            assistant("m1", "end_turn", text("Part two.")),
        )
        self.assertEqual(last_reply.claude_reply(lines), "Part one.\n\nPart two.")


def task(kind, **fields):
    return {"type": "event_msg", "payload": {"type": kind, **fields}}


class CodexReply(unittest.TestCase):
    def test_last_agent_message_of_a_finished_task(self):
        lines = jsonl(
            task("task_started"),
            {"type": "response_item", "payload": {"type": "message", "role": "assistant"}},
            task("task_complete", last_agent_message="Done. CI is green."),
        )
        self.assertEqual(last_reply.codex_reply(lines), "Done. CI is green.")

    def test_newer_task_still_running(self):
        lines = jsonl(
            task("task_started"),
            task("task_complete", last_agent_message="First done."),
            task("task_started"),
        )
        self.assertIsNone(last_reply.codex_reply(lines))


class FindingTheSession(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp())

    def write(self, relative, lines):
        path = self.home / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("".join(lines), encoding="utf-8")
        return path

    def agent(self, kind, value, ref="id"):
        return {"agent": kind, "agent_session": {"agent": kind, "kind": ref, "source": f"herdr:{kind}", "value": value}}

    def test_claude_by_session_id(self):
        self.write(".claude/projects/-Users-me-hark/abc.jsonl",
                   jsonl(prompt("go"), assistant("m1", "end_turn", text("Gone."))))
        self.assertEqual(last_reply.last_reply(self.agent("claude", "abc"), self.home), "Gone.")

    def test_codex_by_session_id(self):
        self.write(".codex/sessions/2026/10/08/rollout-2026-10-08T09-00-00-01a1.jsonl",
                   jsonl(task("task_started"), task("task_complete", last_agent_message="Merged.")))
        self.assertEqual(last_reply.last_reply(self.agent("codex", "01a1"), self.home), "Merged.")

    def test_session_given_as_a_path(self):
        path = self.write("elsewhere/session.jsonl", jsonl(prompt("go"), assistant("m1", "end_turn", text("Gone."))))
        self.assertEqual(last_reply.last_reply(self.agent("claude", str(path), ref="path"), self.home), "Gone.")

    def test_unknown_session_file(self):
        self.assertIsNone(last_reply.last_reply(self.agent("claude", "missing"), self.home))

    def test_agent_kind_without_a_reader(self):
        self.assertIsNone(last_reply.last_reply(self.agent("omp", "abc"), self.home))

    def test_no_session_recorded(self):
        self.assertIsNone(last_reply.last_reply({"agent": "claude"}, self.home))


if __name__ == "__main__":
    unittest.main()
