---
name: herdr-voice
description: "Claude Code, Codex, omp, any coding agent: Herdr, not tmux"
---

# Herdr coding sessions, by voice

The user talks to you through a voice app and is usually away from the screen. You run as the Hermes gateway, a background service on the same machine as the user's Herdr server, not inside a Herdr pane. The `herdr` CLI reaches that server through its socket from any shell, so it works from here.

## You're outside Herdr, and that's expected

Don't check `HERDR_ENV` or ask to be moved into a Herdr pane: the user set Hermes up to control Herdr from outside. Stay out of their way instead:

- Address an agent by its `pane_id` from `herdr agent list` (`w1P:p8`), or by its `name` if it has one; never by focus. `<agent>` below means either. Pass `--no-focus` when creating workspaces or tabs.
- Never run `focus`, `attach`, bare `herdr`, `herdr server stop`, or close a workspace or agent you didn't start, unless the user asks.
- Use the default session. Don't pass `--session`.

For interactive coding agent sessions on this machine (Claude Code, Codex, or any other agent Herdr supports), use Herdr, not tmux, unless the user asks for tmux. Herdr is the user's tool for these.

## Names speech-to-text gets wrong

Read requests generously: "herder", "hurdle", "burger" mean Herdr; "harp", "heart", "hearth" usually mean the repo `hark`; "quad code", "cloud code" mean Claude Code, and a "cloud agent" or "cloud session" is a Claude one. Match spoken names against `herdr agent list` and the user's repos before asking.

## Recipes

Every command prints JSON; read ids and states from it. For anything not covered here, print a command group's usage by running it without a subcommand (`herdr agent`, `herdr workspace`, `herdr tab`, `herdr pane`). Never run bare `herdr`: it opens the interactive UI.

**What's running:** `herdr agent list`. When the user says "sessions" they mean these coding agents, not Herdr's server sessions (`herdr session list` is almost never what they want). Speak of each agent the way Herdr's sidebar shows it: workspace label and tab label ("asterism 2", labels from `herdr workspace list` and `herdr tab list --workspace <id>`), then its kind and state: idle or done means waiting for input, working, or blocked. Match the user's "the asterism 2 agent" or "the Claude in hark" back to a `pane_id` the same way.

**Start a session** ("new Claude session for hark", "start omp in hark"). It works the same for every agent Herdr supports; `herdr agent start --help` lists the `--kind` values. Match the spoken agent to a kind (Cursor's is `cursor`, Antigravity's is `agy`). A request for a new session always means a new agent, even if one is already running in that repo, idle or not. Reuse an existing agent only when the user asks for it. Do it all without asking, in this order:

1. **Repo:** find its path, e.g. `~/<repo>` or `~/github.com/*/<repo>`. Ask only if more than one matches, or none does. Never create the repo's folder.
2. **Workspace:** a session for a repo goes in that repo's workspace. Run `python3 ${HERMES_SKILL_DIR}/scripts/repo_workspace.py <repo_path>` (`scripts/repo_workspace.py` in this skill's folder): it prints the workspace id to use. It matches an agent working in the repo, or a workspace labelled with the repo's name, and never a folder that only contains the repo. If it prints nothing (exit status 1), the repo has no workspace yet: create one in step 3, even when another workspace looks close. Never put a session in another repo's workspace.
3. **Pane:** always a fresh one, never a pane that already has an agent or shell in it. If the workspace exists, add a tab; if not, create the workspace. Either way, read the new pane's id from `root_pane.pane_id`:

   ```bash
   herdr tab create --workspace <workspace_id> --cwd <repo_path> --no-focus
   herdr workspace create --cwd <repo_path> --label <repo> --no-focus
   ```

4. **Temporary name:** `agent start` requires a name, so use `new-<pane>`: the `pane_id` in lowercase with `:` as `-` (`w1P:p8` → `new-w1p-p8`); pane ids are never reused, so it's always free. Herdr's sidebar shows a named agent's name under "asterism · 2" instead of its kind, so step 6 clears it.
5. **Start:**

   ```bash
   herdr agent start new-<pane> --kind <kind> --pane <pane_id> --timeout 60000 -- <prompts-off flag>
   # With permission prompts on, or for an agent without such a flag, leave out everything from `--` on.
   ```

   Prompts-off flags: claude `--dangerously-skip-permissions`, codex `--yolo`, omp `--auto-approve`, grok `--always-approve`, hermes `--yolo`, opencode `--auto`, cursor `--force`; pi has no approval prompts, so it needs none. For any other kind, read its executable's `--help` once and use the flag that auto-approves all tool calls (names like `--yolo`, `--dangerously-…`, `--always-approve`, `--auto-approve`). If there's no such flag, start it without one and tell the user it may stop to ask.

6. **Clear the name** once it's ready (after any folder-trust prompt below): `herdr agent rename <pane_id> --clear`. From then on use the `pane_id`. Keep a name only when the user asks for one; then use theirs instead of `new-…` and don't clear it.

Adding a tab leaves the agents already in that workspace alone: don't read, prompt, interrupt, or close them while starting the new one. Tell the user where the new agent is the way the sidebar shows it ("Claude is up in asterism 2") and whether that's an existing workspace or a new one.

**Permission prompts:** the first time you start a session for this user, check your memory for their choice. If there's none, ask once: "Should coding sessions run with permission prompts off, so they never stop to ask?" Save the answer to memory and follow it from then on. With prompts on, a session that stops to ask shows as `blocked` (see below).

If `agent start` fails with `agent_not_ready` (blocked during startup), read the screen (`herdr agent read <agent> --source visible`). Many agents ask whether to trust a folder the first time they open it. For a repo the user asked for, pick the option that trusts it with `send-keys`, then `herdr agent wait <agent> --until idle --timeout 30000`. Claude asks "Is this a project you created or one you trust?" and `herdr agent send-keys <agent> Down Enter` answers yes. For any other question, tell the user what it asks.

**Send a task** and hand off the waiting, so the user can keep talking while the agent works:

1. Send it and check it started:

   ```bash
   herdr agent prompt <agent> "<task>" --wait --until working --until blocked --until done --until idle --timeout 30000
   ```

   If it fails with `agent_prompt_stalled`, don't resend yet: read the screen. If your text is sitting in the input box, send `Enter`; if a menu is open, send `Escape` once and check again. Resend only when your text isn't there, so the task never runs twice.

2. If it's already `done` or `idle`, read the result now (below). If it's `blocked`, handle it as below. Otherwise hand the waiting to a background subagent with `delegate_task`; its result comes back to this conversation by itself when the agent finishes. Give it this goal, with `<agent>` filled in:

   > Wait for the Herdr coding agent `<agent>` to finish. Run `herdr agent wait <agent> --until done --until idle --until blocked --timeout 540000` with the terminal tool's `timeout=560`, and run it again while the agent is still `working`. Never `sleep` and poll. Then run `python3 ${HERMES_SKILL_DIR}/scripts/last_reply.py <agent>`; if it exits with status 3, run `herdr agent read <agent> --source recent-unwrapped --lines 200` and take only the agent's last message. If the agent is `blocked`, read the screen with `herdr agent read <agent> --source visible` and report the question it asks. If the reply says it's still waiting on a command it started in the background, the task isn't finished: run `herdr agent wait <agent> --until working --timeout 540000`, then wait for it to finish again as above. Don't send the agent anything. Report the agent's final state and its last reply or question, word for word.

3. Tell the user it's started and that you'll tell them when it's done ("Claude's on it in asterism 2. I'll tell you when it's done."), then end your turn. Don't run `herdr agent wait`, read the screen or check files yourself after sending a task, even for a task that looks quick: the user is talking to you and hears nothing while you wait.

4. When the subagent's result arrives, report it as in "Reporting back by voice" below, naming the agent the way Herdr's sidebar shows it. If the agent is blocked, tell the user its question in one sentence.

Wait in your own turn instead only when the user asks you to ("do it and wait", "stay on it"): `herdr agent prompt <agent> "<task>" --wait --until done --until idle --until blocked --timeout 540000`, with the terminal tool's `timeout=560` (its foreground limit is 600 seconds). If that wait times out while the agent is still `working`, say it's still going and what it's doing, in one sentence from the latest output, then wait again with `herdr agent wait`.

**Tell me when it's done** ("let me know when asterism 2 finishes"): start the same background subagent for that agent, even one you didn't start, and tell the user you will.

**Read the result:** `python3 ${HERMES_SKILL_DIR}/scripts/last_reply.py <agent>` (`scripts/last_reply.py` in this skill's folder) prints the agent's last reply from its own session file (Claude Code and Codex), complete however long it is. If it exits with status 3 (another agent kind, or no finished reply), read the screen instead: `herdr agent read <agent> --source recent-unwrapped --lines 200`, and take only the agent's last message after your prompt, not the whole screen. Claude can end its reply while a background command it started is still running; then its `✻ … done` line on screen says a shell is still running: say so, and wait again before reporting the task finished.

**Blocked:** read the screen and tell the user the question in one sentence. Answer it with `send-keys` only once they reply.

**Message an agent that's working:** `herdr agent prompt <agent> "<text>"` queues it as its next input.

**Stop:** `herdr agent send-keys <agent> Escape` interrupts the current task. Send it once: in Claude, a second Escape opens the rewind menu. Close a workspace you started only when the user says they're done with it.

## Reporting back by voice

The user hears your reply, so speak like a colleague: what changed, whether tests passed, what's committed or still open, in two to four sentences. Never read out terminal output, diffs, or file contents; offer to put details on screen instead. When you start a long task, say so and that you'll report when it's done.
