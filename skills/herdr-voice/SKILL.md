---
name: herdr-voice
description: "Claude Code, Codex, omp, any coding agent: Herdr, not tmux"
metadata:
  hark:
    voice: true
    summary: "Start, check and steer Claude Code, Codex and other coding agents in Herdr."
    requires:
      command: herdr
    phrases:
      - "new Claude session for hark"
      - "what's waiting on me?"
      - "tell the hark Claude to fix the failing test"
      - "where did we do the swipe fix?"
      - "stop the codex agent"
    routing: "For Herdr, and for starting, checking, prompting or stopping any coding agent session (Claude Code, Codex, omp or any agent Herdr supports), load herdr-voice, not the herdr, claude-code or codex skills and not tmux. You run outside Herdr and that's supported: don't check HERDR_ENV."
---

# Herdr coding sessions, by voice

The user talks to you through a voice app and is usually away from the screen. You run as the Hermes gateway, a background service on the same machine as the user's Herdr server, not inside a Herdr pane. The `herdr` CLI reaches that server through its socket from any shell, so it works from here.

## You're outside Herdr, and that's expected

Don't check `HERDR_ENV` or ask to be moved into a Herdr pane: the user set Hermes up to control Herdr from outside. Stay out of their way instead:

- Address an agent by its `pane_id` from `herdr agent list` (`w1P:p8`), or by its `name` if it has one; never by focus. `<agent>` below means either. Pass `--no-focus` when creating workspaces or tabs.
- Never run `focus`, `attach`, bare `herdr`, `herdr server stop`, or close a workspace or agent you didn't start, unless the user asks. Asking to see an agent counts: see **Show me an agent**.
- Use the default session. Don't pass `--session`.

For interactive coding agent sessions on this machine (Claude Code, Codex, or any other agent Herdr supports), use Herdr, not tmux, unless the user asks for tmux. Herdr is the user's tool for these.

## Names speech-to-text gets wrong

Read requests generously: "herder", "hurdle", "burger" mean Herdr; "harp", "heart", "hearth" usually mean the repo `hark`; "quad code", "cloud code" mean Claude Code, and a "cloud agent" or "cloud session" is a Claude one. Match spoken names against `herdr agent list` and the user's repos before asking.

## Voice vocabulary

Short spoken phrases map to recipes below. Match them generously: speech-to-text drops and swaps words ("judge" or "notch" for nudge, "anything from me" for anything for me).

- "status", "what's going on": **What's running**.
- "waiting on me", "anything for me", "what needs me": **What's waiting on me**.
- "where did we do X", "which session did X": **Where did we do this**.
- "nudge X": send X `herdr agent prompt <agent> "Continue with your current task."` and hand off the waiting as in **Send a task**.
- "stop X": **Stop**.
- "show me X", "switch to X", "put X on screen": **Show me an agent**.
- "have Codex review it", "get a review": **Review a PR**.
- "merge it", "merge it once CI passes": **Merge a PR**.

X is an agent, matched as in **What's running**.

## Recipes

Every command prints JSON; read ids and states from it. For anything not covered here, print a command group's usage by running it without a subcommand (`herdr agent`, `herdr workspace`, `herdr tab`, `herdr pane`). Never run bare `herdr`: it opens the interactive UI.

**What's running:** `herdr agent list`. When the user says "sessions" they mean these coding agents, not Herdr's server sessions (`herdr session list` is almost never what they want). Speak of each agent by its `name` when it has one ("apple-models in hark"); otherwise the way Herdr's sidebar shows it: workspace label and tab label ("asterism 2", labels from `herdr workspace list` and `herdr tab list --workspace <id>`). Then give its kind and state: idle or done means waiting for input, working, or blocked. To match the user's words back to a `pane_id`, try the names first ("the apple models agent" is `apple-models`, spoken with spaces for dashes), and fall back to workspace and tab ("the asterism 2 agent", "the Claude in hark") only for agents started at the desk that never got a name.

**What's waiting on me:** sweep every agent in `herdr agent list` and sort it into one of three groups:

- **Still working:** `agent_status` is `working`. No read needed.
- **Needs you:** idle or done, and its last reply ends on a decision or question for the user. Give the actual question in one sentence.
- **Finished:** idle or done, with a result you haven't already told the user in this conversation. Give the result in a sentence.

Read each idle or done agent's last reply with `python3 ${HERMES_SKILL_DIR}/scripts/last_reply.py <agent>`; if it exits with status 3, use `herdr agent read <agent> --source recent-unwrapped --lines 200` and take only its last message. Leave out agents that are `blocked` on a live prompt: handle each with **Blocked** below. Report group by group, naming each agent as the sidebar shows it, and skip empty groups and idle agents with nothing new.

**One at a time** (the user says "let's go one by one", or after any triage report where their reply is ambiguous about which agent it answers): do the same sweep silently, but report only ONE agent — the first **Needs you** one, else the first **Finished** one — and end the turn. Their reply closes that agent (answer it to the agent, dismiss it, or "next"), then report the next one the same way. Never batch: a list makes "yes" ambiguous about which agent it answers. Keep the silent sweep's counts in mind and say "that's the last one" on the final agent.

**Start a session** ("new Claude session for hark", "start omp in hark"). It works the same for every agent Herdr supports; `herdr agent start --help` lists the `--kind` values. Match the spoken agent to a kind (Cursor's is `cursor`, Antigravity's is `agy`). A request for a new session always means a new agent, even if one is already running in that repo, idle or not. Reuse an existing agent only when the user asks for it. Do it all without asking, in this order:

1. **Repo:** find its path, e.g. `~/<repo>` or `~/github.com/*/<repo>`. Ask only if more than one matches, or none does. Never create the repo's folder.
2. **Workspace:** first decide whether the session gets its own worktree (see **Worktrees** below); if it does, skip to step 3. Otherwise a session for a repo goes in that repo's workspace. Run `python3 ${HERMES_SKILL_DIR}/scripts/repo_workspace.py <repo_path>` (`scripts/repo_workspace.py` in this skill's folder): it prints the workspace id to use. It matches an agent working in the repo, or a workspace labelled with the repo's name, and never a folder that only contains the repo. If it prints nothing (exit status 1), the repo has no workspace yet: create one in step 3, even when another workspace looks close. Never put a session in another repo's workspace.
3. **Pane:** always a fresh one, never a pane that already has an agent or shell in it. If the workspace exists, add a tab; if not, create the workspace. For a worktree, create the worktree: it opens as its own workspace, labelled with the branch. Either way, read the new pane's id from `root_pane.pane_id`:

   ```bash
   herdr tab create --workspace <workspace_id> --cwd <repo_path> --no-focus
   herdr workspace create --cwd <repo_path> --label <repo> --no-focus
   herdr worktree create --cwd <repo_path> --branch <branch> --base <base> --no-focus
   ```

4. **Name:** derive a permanent name from the task, a short phrase the user would naturally say to refer to it, taken from their request: kebab-case, lowercase, no spaces, under about five words. Starting Claude on the Apple Foundation Models agent loops in hark gives `hark-apple-models` or just `apple-models`. If the user asks for a particular name, use theirs. Only when the request gives no usable phrase, or you're starting the session with an empty prompt, use `new-<pane>`: the `pane_id` in lowercase with `:` as `-` (`w1P:p8` → `new-w1p-p8`); pane ids are never reused, so it's always free.
5. **Start:**

   ```bash
   herdr agent start <name> --kind <kind> --pane <pane_id> --timeout 60000 -- <prompts-off flag>
   # With permission prompts on, or for an agent without such a flag, leave out everything from `--` on.
   ```

   Prompts-off flags: claude `--dangerously-skip-permissions`, codex `--yolo`, omp `--auto-approve`, grok `--always-approve`, hermes `--yolo`, opencode `--auto`, cursor `--force`; pi has no approval prompts, so it needs none. For any other kind, read its executable's `--help` once and use the flag that auto-approves all tool calls (names like `--yolo`, `--dangerously-…`, `--always-approve`, `--auto-approve`). If there's no such flag, start it without one and tell the user it may stop to ask.

6. **Keep the name:** never clear it. Herdr's sidebar shows it under "asterism · 2" instead of the agent's kind, and from then on it's how you and the user refer to the agent.

Adding a tab leaves the agents already in that workspace alone: don't read, prompt, interrupt, or close them while starting the new one. Tell the user where the new agent is by its name and workspace ("apple-models is up in hark") and whether that's an existing workspace or a new one.

**Worktrees:** agents sharing one checkout get in each other's way: one switches branches or resets files under another. Give a new session its own worktree when another agent is already working in the repo's checkout (`python3 ${HERMES_SKILL_DIR}/scripts/repo_workspace.py --busy <repo_path>` prints their pane ids and exits 0; it exits 1 when there are none), or when the user names an issue or a branch for it. Otherwise use the repo's own checkout as above.

- **Branch:** the one the user names; for an issue, `<number>-<short-title>` from `gh issue view <number>` (`24-swipe-crash`); otherwise the session's name from step 4, so derive that first.
- **Base:** the remote's default branch, so the session starts from fresh code rather than whatever the checkout has: `git -C <repo_path> fetch -q origin`, then `git -C <repo_path> symbolic-ref --short refs/remotes/origin/HEAD` gives it (`origin/main`). If that fails, leave out `--base`: it then branches from the checkout's current commit. An existing branch is checked out as it is, and `--base` is ignored.
- If `worktree create` fails because the branch is already checked out elsewhere, tell the user where instead of picking another branch.
- When you tell the user where it is, say it's in its own worktree ("apple-models is up in its own worktree of hark"). If `git -C <repo_path> status --porcelain` prints anything, add that the worktree doesn't have the uncommitted changes in the main checkout. If the checkout has installed dependencies (`node_modules`, `.venv`, `target`, `.build`), add that the first build there may be slow while it installs them.
- **Cleanup:** never remove a worktree on your own. Once its PR is merged, offer once to clean it up, and only on a yes run `herdr worktree remove --workspace <workspace_id>`: it closes that workspace with any agents in it and deletes the folder, and keeps the branch. If it fails with `dirty_worktree_requires_force`, tell the user it has uncommitted changes, and pass `--force` only if they say to throw them away.

**Permission prompts:** the first time you start a session for this user, check your memory for their choice. If there's none, ask once: "Should coding sessions run with permission prompts off, so they never stop to ask?" Save the answer to memory and follow it from then on. With prompts on, a session that stops to ask shows as `blocked` (see below).

If `agent start` fails with `agent_not_ready` (blocked during startup), read the screen (`herdr agent read <agent> --source visible`). Many agents ask whether to trust a folder the first time they open it. For a repo the user asked for, pick the option that trusts it with `send-keys`, then `herdr agent wait <agent> --until idle --timeout 30000`. Claude asks "Is this a project you created or one you trust?" and `herdr agent send-keys <agent> Down Enter` answers yes. For any other question, tell the user what it asks.

**Send a task** and hand off the waiting, so the user can keep talking while the agent works:

1. Send it and check it started. The first line marks the task as yours to report, so the desk-alerts plugin (if installed) doesn't announce it too:

   ```bash
   mkdir -p ~/.cache/herdr-voice/handed-off && touch ~/.cache/herdr-voice/handed-off/<agent>
   herdr agent prompt <agent> "<task>" --wait --until working --until blocked --until done --until idle --timeout 30000
   ```

   If it fails with `agent_prompt_stalled`, don't resend yet: read the screen. If your text, exactly as you sent it, is sitting in the input box, send `Enter`; if a menu is open, send `Escape` once and check again. Resend only when your text isn't there, so the task never runs twice.

2. If it's already `done` or `idle`, read the result now (below). If it's `blocked`, handle it as below. Otherwise hand the waiting to a background subagent with `delegate_task`; its result comes back to this conversation by itself when the agent finishes. Give it this goal, with `<agent>` filled in:

   > Wait for the Herdr coding agent `<agent>` to finish. Run `herdr agent wait <agent> --until done --until idle --until blocked --timeout 540000` with the terminal tool's `timeout=560`, and run it again while the agent is still `working`. Never `sleep` and poll. Then run `python3 ${HERMES_SKILL_DIR}/scripts/last_reply.py <agent>`; if it exits with status 3, run `herdr agent read <agent> --source recent-unwrapped --lines 200` and take only the agent's last message. If the agent is `blocked`, read the screen with `herdr agent read <agent> --source visible` and report the question it asks. Also look at the coding agent's status line at the bottom of `herdr agent read <agent> --source visible` and report the spend and context percentage if it shows them. If the reply says it's still waiting on a command it started in the background, the task isn't finished: run `herdr agent wait <agent> --until working --timeout 540000`, then wait for it to finish again as above. Don't send the agent anything. Report the agent's final state and its last reply or question, word for word.

3. After the `delegate_task` call, tell the user it's started and that you'll tell them when it's done ("Claude's on it in asterism 2. I'll tell you when it's done."), then end your turn. Everything you write is spoken, so that sentence is said once, there: between sending the task and the `delegate_task` call, write nothing, and don't say before the call that the agent is on it. Don't run `herdr agent wait`, read the screen or check files yourself after sending a task, even for a task that looks quick: the user is talking to you and hears nothing while you wait.

4. When the subagent's result arrives, report it as in "Reporting back by voice" below, naming the agent the way Herdr's sidebar shows it. If the agent is blocked, tell the user its question in one sentence.

Wait in your own turn instead only when the user asks you to ("do it and wait", "stay on it"): `herdr agent prompt <agent> "<task>" --wait --until done --until idle --until blocked --timeout 540000`, with the terminal tool's `timeout=560` (its foreground limit is 600 seconds). If that wait times out while the agent is still `working`, say it's still going and what it's doing, in one sentence from the latest output, then wait again with `herdr agent wait`.

**Tell me when it's done** ("let me know when asterism 2 finishes"): mark it as yours (the `touch` line above), start the same background subagent for that agent, even one you didn't start, and tell the user you will.

**Several agents at once:** when one request starts or prompts several agents, do step 1 of **Send a task** for each, then call `delegate_task` once per agent with the goal above, plus once more for the whole batch with this goal: wait for each of `<agent>`, `<agent>`, … in turn as above, and return all their final states and replies together. Say the hand-off sentence once, for the whole batch. Results come back as separate messages, one per subagent, and the per-agent ones race the batch one. On each arrival, check whether you now have a result for every agent in the batch: if not, end your turn without saying anything; if so, report them all once, in one reply. Results that arrive after you've reported the set are duplicates: end your turn without speaking.

**Review a PR** ("have Codex review it"):

1. **PR:** the number the user says; else the one the conversation was last about (an agent that opened one usually gives its URL); else the PR for the branch that agent is on, from `gh pr view --json number,url,headRefName` run in its `cwd`. Ask only if none of those finds one. The agent that wrote it is the author.
2. **Reviewer:** start a new agent as in **Start a session**, of the kind the user names (Claude if they don't), named `pr-<number>-review`. It only reads, so it goes in a new tab of the workspace whose checkout is on the PR's branch, usually the author's, even when the author is still in it: add the tab as in step 3, with `--cwd` set to that checkout. If no checkout has the branch, create a worktree for it (`--branch <headRefName>`).
3. **Task:** send it this, and hand off the waiting, as in **Send a task**: "Review PR #<number> (`gh pr view <number>`, `gh pr diff <number>`). Don't edit files, commit, push, or comment on the PR. List the problems you find by severity (blocking, should fix, nit), each with its file and line and one sentence on why. End with a one-line verdict."
4. **Report** the verdict and the blocking problems in two to four sentences, and offer to put the full list on screen (**Show me an agent**, on the reviewer).
5. **"Send that to the Claude":** read the reviewer's reply with `scripts/last_reply.py` and pass it to the author as a task, as in **Send a task**: "A reviewer went over PR #<number>. Fix what you agree with and push, then say what you left alone and why:" followed by the reply, word for word.

**Merge a PR** ("merge it once CI passes"): find the PR as in **Review a PR**. The first time, check your memory for the user's merge choice. If there's none, ask once: "I'll squash-merge it once the checks pass. Should I merge PRs like that from now on without checking with you first?" Save the answer to memory. If they want to be asked, confirm every merge before handing it off. Then hand off the wait with `delegate_task`, giving it this goal with `<number>` and `<repo_path>` filled in:

> Merge PR #<number> once its checks pass. In `<repo_path>`, run `gh pr checks <number> --watch --fail-fast --interval 30` with the terminal tool's `timeout=560`, and run it again while checks are still pending. Never `sleep` and poll. If every check passed, run `gh pr merge <number> --squash` and report whether it merged. If any check failed, or no checks are reported, don't merge: report which checks failed, from `gh pr checks <number>`, or that there were none. Report any error from `gh` word for word.

Tell the user you'll merge it when the checks pass and let them know. When the result arrives, say whether it merged, or which check failed. If it merged from a worktree, offer the cleanup in **Worktrees**.

**Show me an agent** ("show me that one", "switch to the asterism 2 Claude"): `herdr agent focus <agent>`. Run it only when the user asks to see or switch to an agent, or says yes to an offer to put something on screen, never as a side effect of anything else. "That one" or "it" is the agent the conversation was last about. Focus belongs to Herdr's session, so every open Herdr window switches to it, and Herdr can't tell you whether one is open. Say "It's on screen in Herdr" with the agent's name. If the user says nothing changed, no Herdr window is open on the Mac: it'll show that agent when they open one.

**Desk alerts:** a turn that starts with `[Desk agent alert]` comes from the desk-alerts plugin: an agent the user started at the desk finished or stopped on a question while they were away. Say what happened in a sentence or two, as it asks. Don't send the agent anything until the user says so.

**Read the result:** `python3 ${HERMES_SKILL_DIR}/scripts/last_reply.py <agent>` (`scripts/last_reply.py` in this skill's folder) prints the agent's last reply from its own session file (Claude Code and Codex), complete however long it is. If it exits with status 3 (another agent kind, or no finished reply), read the screen instead: `herdr agent read <agent> --source recent-unwrapped --lines 200`, and take only the agent's last message after your prompt, not the whole screen. Claude can end its reply while a background command it started is still running; then its `✻ … done` line on screen says a shell is still running: say so, and wait again before reporting the task finished.

**Where did we do this** ("where did we do the swipe fix"): find which agent's session dealt with a topic by searching transcripts, not by asking agents. For each agent in `herdr agent list`, its `agent_session` points at its transcript: with `kind` `path`, `value` is the file (omp); with `kind` `id`, it's `~/.claude/projects/*/<value>.jsonl` for Claude and `~/.codex/sessions/*/*/*/rollout-*-<value>.jsonl` for Codex. Run `grep -l -i -F "<phrase>"` over those files, trying a shorter key phrase if nothing matches. Tell the user which agent worked on it, named as the sidebar shows it, and its `pane_id`; if they want to know what it concluded, read its reply with `scripts/last_reply.py`. This is read-only: never prompt, interrupt, or focus an agent because of a match.

**Suggested prompts:** Claude Code shows a suggested next prompt, dimmed, in its empty input box after a reply. A screen read shows it as ordinary text after `❯`. It isn't queued and isn't the agent's question: never report it, and never send `Enter` for it.

**Blocked:** read the screen and tell the user the question in one sentence. Answer it with `send-keys` only once they reply. For a permission prompt, pick the plain yes or no; "always allow" or a mode switch only when the user asks for it. Before answering, mark the agent as yours (the `touch` line above), so its finish isn't announced again as a desk alert, then wait for it and report as for a task.

**Message an agent that's working:** `herdr agent prompt <agent> "<text>"` queues it as its next input.

**Stop:** `herdr agent send-keys <agent> Escape` interrupts the current task. Send it once: in Claude, a second Escape opens the rewind menu. Close a workspace you started only when the user says they're done with it.

## Reporting back by voice

The user hears your reply, so speak like a colleague: what changed, whether tests passed, what's committed or still open, in two to four sentences. When a task finished and its spend or context usage was on screen (Claude and others show both in the status line at the bottom), add one short sentence: "That took about eleven cents, context at six percent." If they weren't visible, leave it out without comment. Never read out terminal output, diffs, or file contents; offer to put details on screen instead. When you start a long task, say so and that you'll report when it's done.
