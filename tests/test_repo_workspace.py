import importlib.util
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "skills" / "herdr-voice" / "scripts" / "repo_workspace.py"
spec = importlib.util.spec_from_file_location("repo_workspace", SCRIPT)
repo_workspace = importlib.util.module_from_spec(spec)
spec.loader.exec_module(repo_workspace)
find = repo_workspace.workspace_for

REPO = "/Users/me/github.com/shanev/hv-scratch"
WORKSPACES = [{"workspace_id": "w3E", "label": "shanev"}, {"workspace_id": "w1Y", "label": "hark"}]


class WorkspaceFor(unittest.TestCase):
    def test_agent_in_the_repo(self):
        agents = [{"cwd": REPO, "workspace_id": "w9"}]
        self.assertEqual(find(REPO, agents, WORKSPACES), "w9")

    def test_agent_in_a_subfolder(self):
        agents = [{"cwd": REPO + "/web", "workspace_id": "w9"}]
        self.assertEqual(find(REPO, agents, WORKSPACES), "w9")

    def test_agent_in_the_parent_folder_does_not_count(self):
        agents = [{"cwd": "/Users/me/github.com/shanev", "workspace_id": "w3E"}]
        self.assertIsNone(find(REPO, agents, WORKSPACES))

    def test_sibling_with_a_shared_prefix_does_not_count(self):
        agents = [{"cwd": REPO + "-old", "workspace_id": "w5"}]
        self.assertIsNone(find(REPO, agents, WORKSPACES))

    def test_label_matches_the_repo_name_ignoring_case(self):
        self.assertEqual(find("/Users/me/github.com/tensor-systems/Hark", [], WORKSPACES), "w1Y")

    def test_trailing_slash(self):
        agents = [{"cwd": REPO, "workspace_id": "w9"}]
        self.assertEqual(find(REPO + "/", agents, WORKSPACES), "w9")


if __name__ == "__main__":
    unittest.main()
