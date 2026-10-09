import importlib.util
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "skills" / "herdr-voice" / "scripts" / "repo_workspace.py"
spec = importlib.util.spec_from_file_location("repo_workspace", SCRIPT)
repo_workspace = importlib.util.module_from_spec(spec)
spec.loader.exec_module(repo_workspace)
find = repo_workspace.workspace_for
busy = lambda repo, agents: [agent["pane_id"] for agent in repo_workspace.agents_in(repo, agents)]

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


class AgentsIn(unittest.TestCase):
    def test_agents_in_the_checkout(self):
        agents = [{"cwd": REPO, "pane_id": "w9:p1"}, {"cwd": REPO + "/web", "pane_id": "w9:p2"}]
        self.assertEqual(busy(REPO, agents), ["w9:p1", "w9:p2"])

    def test_agents_in_a_worktree_do_not_count(self):
        # herdr puts worktrees under ~/.herdr/worktrees/<repo>/, outside the checkout.
        agents = [{"cwd": "/Users/me/.herdr/worktrees/hv-scratch/24-fix", "pane_id": "w9:p1"}]
        self.assertEqual(busy(REPO, agents), [])

    def test_parent_and_sibling_folders_do_not_count(self):
        agents = [{"cwd": "/Users/me/github.com/shanev", "pane_id": "w3:p1"}, {"cwd": REPO + "-old", "pane_id": "w5:p1"}]
        self.assertEqual(busy(REPO, agents), [])


if __name__ == "__main__":
    unittest.main()
