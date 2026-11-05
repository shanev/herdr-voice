---
name: herdr-core-voice
description: "Claude Code, Codex, any coding agent: Herdr app, not tmux"
metadata:
  vux:
    voice: true
    title: Herdr
    summary: "Start, check and steer Claude Code, Codex and other coding agents in the Herdr app."
    requires:
      command: hc
      capabilities:
        - background_tasks
        - memory
    phrases:
      - "new Claude session for website"
      - "what's waiting on me?"
      - "tell the api Claude to fix the failing test"
      - "where did we do the swipe fix?"
      - "stop the codex agent"
    contexts:
      list:
        - python3
        - scripts/vux_state.py
        - contexts
      create:
        - python3
        - scripts/vux_state.py
        - create
      scope: "This conversation is about the Herdr space '{name}' on {location} (Vux context {id}). Unless the user names another, new sessions go in it and the agents they mention are its agents. Load herdr-core-voice: its Vux section says how to reach it."
    items:
      version: 2
      list:
        - python3
        - scripts/vux_state.py
        - items
      push:
        - waiting
        - done
    watch:
      - python3
      - scripts/watch.py
    routing: "The Herdr app (herdr-core, hc, spaces) and its coding agents (Claude Code, Codex, any): load herdr-core-voice, not claude-code, codex or tmux; classic herdr's workspaces are herdr-voice's. Both installed, neither named: the skill the page's scope names, else the one with the repo, else ask once."
  hark:
    voice: true
    title: Herdr
    summary: "Start, check and steer Claude Code, Codex and other coding agents in the Herdr app."
    requires:
      command: hc
      capabilities:
        - background_tasks
        - memory
    phrases:
      - "new Claude session for website"
      - "what's waiting on me?"
      - "tell the api Claude to fix the failing test"
      - "where did we do the swipe fix?"
      - "stop the codex agent"
    contexts:
      list:
        - python3
        - scripts/vux_state.py
        - contexts
      create:
        - python3
        - scripts/vux_state.py
        - create
      scope: "This conversation is about the Herdr space '{name}' on {location} (Vux context {id}). Unless the user names another, new sessions go in it and the agents they mention are its agents. Load herdr-core-voice: its Vux section says how to reach it."
    items:
      version: 2
      list:
        - python3
        - scripts/vux_state.py
        - items
      push:
        - waiting
        - done
    watch:
      - python3
      - scripts/watch.py
    routing: "The Herdr app (herdr-core, hc, spaces) and its coding agents (Claude Code, Codex, any): load herdr-core-voice, not claude-code, codex or tmux; classic herdr's workspaces are herdr-voice's. Both installed, neither named: the skill the page's scope names, else the one with the repo, else ask once."
---

# Herdr app coding sessions, by voice

The user talks to you through a voice app and is usually away from the screen. You run as a background service (Hermes' gateway, or Vux's own agent, vux-agent) on the same machine as herdr-core, the runtime behind the Herdr app. `hc` reaches it through its socket from any shell, and its terminals keep running while Herdr's window is closed. The window shows them in spaces (Vux's pages) and tabs.

## You're outside Herdr, and that's expected

Don't check `HERDR_ENV` or `HC_TERMINAL_ID`, or ask to be moved into a terminal. Stay out of the user's way:

- Address an agent by its terminal address (`t7`) or its name, from `python3 ${HERMES_SKILL_DIR}/scripts/vux_state.py items` (`scripts/vux_state.py` in this skill's folder). `<t>` below means either.
- Never run bare `hc` or `hc attach` (they take over a terminal), `hc server stop` or `restart`, or kill a terminal you didn't start, unless the user asks. Asking to see an agent is **Show me an agent**.
- Never `sleep` and poll: `hc wait` waits.

For interactive coding agent sessions on this machine, use Herdr, not tmux, unless the user asks for tmux.

## Names speech-to-text gets wrong

"herder", "hurdle", "burger" mean Herdr; "quad code", "cloud code" mean Claude Code, and a "cloud agent" is a Claude one. Match spoken names against the items and the user's repos before asking.

## Voice vocabulary

- "status", "what's going on": **What's running**.
- "waiting on me", "anything for me": **What's waiting on me**.
- "where did we do X": **Where did we do this**.
- "nudge X": **Send a task** with "Continue with your current task."
- "stop X": **Stop**.
- "show me X", "switch to X": **Show me an agent**.
- "have Codex review it": **Review a PR**. "merge it once CI passes": **Merge a PR**.

## Vux

Vux shows a page per Herdr space on this machine, with its agents, from `scripts/vux_state.py` (Vux's agent runs it, you don't), kept live by `scripts/watch.py` (both reach herdr-core through `scripts/core.py`), which also pushes an agent's finish or question while the user is away. A run from a space's page names that space in its instructions, with its id.

- **The space from the instructions** is the one the user means: new sessions go in it (**Start a session** with that space id, in the folder `python3 ${HERMES_SKILL_DIR}/scripts/vux_state.py folder <space>` prints), and "the Claude" or "it" is an agent in it, unless the user names another repo or space.
- **Live state:** read the items every time, never from memory.
- **[Card answer]:** the user answering an agent's question from its card; it names the terminal as its item. Read the screen (`hc read <t>`). If that question isn't showing anymore, it was answered at the desk: say so in a few words, nothing else. Otherwise mark the agent (the `mark` line in **Send a task**), answer as **Answer a question** says, and read the screen again. If it asks another question now, run `python3 ${HERMES_SKILL_DIR}/scripts/vux_state.py realert <t>` and say only that you answered it. Otherwise hand off the wait as in **Send a task** and say it's running.
- **[Card result]:** read the result (**Read the result**) and report it.

## Recipes

hc prints JSON when piped; read addresses and statuses from it. `hc <command> --help` has the rest.

**What's running:** `python3 ${HERMES_SKILL_DIR}/scripts/vux_state.py items`: each agent's `name`, `kind`, `task` (its tab's name, else its terminal title), `state` and the space (`context`; `vux_state.py contexts` names them, the home folder's is "Home"). Speak of an agent by its name ("apple-models in api", dashes as spaces); one without a name has its address there: never say an address, call it by its task or as "a new Claude in api". An agent is a terminal in a space with a status: plain shells aren't, nor an agent that quit back to its shell, nor a Claude or Codex still on its first-launch folder-trust question (see **Start a session**). `hc ls` lists every terminal. Match the user's words to names first, then tasks, then kind and space.

**What's waiting on me:** sort every agent in the items into **Still working** (`working`), **Needs you** (done or idle, its last reply ends on a question or decision for the user: say it in a sentence) and **Finished** (done or idle, with a result you haven't told them: one sentence). Read each done or idle agent's reply as in **Read the result**. Leave `waiting` agents to **Blocked**. Report group by group, skipping empty ones. "One by one": report only the first that needs them, wait for their answer, then the next; say "that's the last one" at the end.

**Start a session** ("new Claude session for website"). A new session is always a new agent. Without asking:

1. **Repo:** find its path (`~/<repo>`, `~/github.com/*/<repo>`). Ask only if several match or none.
2. **Space:** `python3 ${HERMES_SKILL_DIR}/scripts/vux_state.py space <repo_path>` prints `{"space", "new", "busy"}`: the repo's space, or a new space id. If `busy` lists agents, or the user names an issue or branch, give it a worktree (**Worktrees**) and run `space` on the worktree's folder instead.
3. **Name:** a short kebab-case phrase from the task (`apple-models`), or the user's; 1 to 32 of a-z, 0-9, `-`. It's permanent: hc can't rename a terminal. If `hc run` says the name is taken, add `-2`.
4. **Start**, with its prompts-off flag if the user wants prompts off (claude `--dangerously-skip-permissions`, codex `--yolo`, omp `--auto-approve`, grok `--always-approve`, opencode `--auto`, cursor `--force`; others: read `--help` once). Codex always needs `--no-daemon`.

   ```bash
   hc run --bg --no-parent --keep --name <name> --cwd <folder> --label herdr.space=<space> -- <agent> <flags>
   hc wait <name> --timeout 30
   ```

   The label puts it in that space as a new tab, at once if Herdr's window is open, else when it opens. A new space id makes a new space, named after the repo.
5. **Ready:** when the wait reports `idle`, send the task as in **Send a task**. With no status (`reason: timeout`), read the screen (`hc read <name>`): a first-launch folder-trust question, for the repo or the Vux space's folder, gets yes (Claude: `hc keys <name> down enter`; Codex: `hc keys <name> enter`), then wait again. Tell the user anything else it asks.

Tell the user where it is ("apple-models is up in api", in a new space or not). **Permission prompts:** the first time, check memory for the user's choice; if none, ask once "Should coding sessions run with permission prompts off, so they never stop to ask?" and save the answer.

**Worktrees:** `git -C <repo_path> fetch -q origin`, base `git -C <repo_path> symbolic-ref --short refs/remotes/origin/HEAD`, then `git -C <repo_path> worktree add -b <branch> <repo_path>.worktrees/<branch> <base>` (an existing branch: `git -C <repo_path> worktree add <repo_path>.worktrees/<branch> <branch>`). The branch is the user's, else `<number>-<short-title>` for an issue, else the session's name. Say it's in its own worktree, and that uncommitted changes in the main checkout aren't there. Never remove one on your own: once its PR is merged, offer once; on a yes, `hc kill` its agents and `git -C <repo_path> worktree remove <path>`, adding `--force` only if they say to throw away its changes.

**Send a task**, handing off the waiting:

1. ```bash
   python3 ${HERMES_SKILL_DIR}/scripts/vux_state.py mark <t>
   hc send <t> "<task>"
   ```

   The mark makes its finish yours to report, so the watcher doesn't push it. If `started` is false, read the screen before anything else: send again only if your text isn't there.
2. Hand the waiting to a background subagent with `delegate_task`, with this goal:

   > Wait for the Herdr coding agent `<t>` to finish. Run `hc wait <t> --timeout 540` with the shell tool's `timeout=560`, again while its status is `working`. Never `sleep` and poll. Then run `python3 ${HERMES_SKILL_DIR}/scripts/vux_state.py reply <t>`; if it exits with status 3, run `hc read <t> --lines 200` and take only the agent's last message. If it's `blocked`, read the screen (`hc read <t>`) and report its question. Report the spend and context percentage if its status line at the bottom of the screen shows them. If the reply says a command it started is still running, run `hc wait <t> --timeout 540` again. Don't send the agent anything. Report its final status and its last reply or question, word for word.

3. Say it's started and you'll tell them when it's done ("Claude's on it in api. I'll tell you when it's done."), once, after the `delegate_task` call, and end your turn. Don't wait or read the screen yourself.
4. When the result arrives, report it (**Reporting back by voice**).

Wait in your own turn only when asked ("do it and wait"): `hc send --wait <t> "<task>" --timeout 540` with the shell tool's `timeout=560`. **Tell me when it's done:** mark it and start the same subagent. **Several agents at once:** step 1 for each, one `delegate_task` per agent plus one for the whole batch; say the hand-off once, report once every result is in, and drop duplicates.

**Read the result:** `python3 ${HERMES_SKILL_DIR}/scripts/vux_state.py reply <t>` prints the last reply from the agent's session file (Claude Code, Codex; `scripts/last_reply.py` reads them). Exit 3: `hc read <t> --lines 200`, only the agent's last message. Claude can end a reply while a background command runs: say so and wait again.

**Blocked** (state `waiting`): `hc get <t>` has its `status`: `kind` is `question` or `permission`, and Claude Code's `msg` says it ("answer: Red or blue? (Red · Blue)"). Without a `msg`, read the screen. Tell the user in a sentence and answer only once they reply, after marking it (the `mark` line); then wait and report as for a task. For a permission, the plain yes or no.

**Answer a question:** read the screen (`hc read <t>`) and find the user's option. If options show keys (`2.`), press it: `hc keys <t> 2`. Otherwise move the highlight (`❯`, `›`) with `hc keys <t> down` (or `up`), read the screen, then `hc keys <t> enter`. Read the screen after every key; never `enter` without checking the highlight. Checkboxes: toggle each (`space`), then Submit. A typed answer: pick the option for writing one ("Type something"), then `hc send --force <t> "<text>"`.

**Message an agent that's working:** `hc send <t> "<text>"` queues it.

**Stop:** `hc interrupt <t>` stops its current turn and says what happened. `hc kill` only what you started, when the user is done with it.

**Show me an agent:** `herdr-app do agent.focus --terminal <address>`, only when the user asks to see it. Say "It's on screen in Herdr". If it says there's no Herdr window, Herdr isn't open on the computer: it shows the agent when they open it.

**Where did we do this:** `python3 ${HERMES_SKILL_DIR}/scripts/vux_state.py sessions` lists each agent's transcript; `grep -l -i -F "<phrase>"` over those paths, with a shorter phrase if nothing matches. Name the agent as in **What's running**. Read-only: never prompt or focus an agent for a match.

**Review a PR:** the PR the user says, else the one last talked about, else `gh pr view --json number,url,headRefName` in the author's folder. Start a reviewer (**Start a session**, Claude unless they name another) named `pr-<number>-review` in the space of the checkout on that branch (`--cwd` that checkout; none: a worktree of the branch). Send it: "Review PR #<number> (`gh pr view <number>`, `gh pr diff <number>`). Don't edit files, commit, push, or comment on the PR. List the problems you find by severity (blocking, should fix, nit), each with its file and line and one sentence on why. End with a one-line verdict." Report the verdict and blockers in a few sentences. "Send that to the Claude": pass its reply to the author as a task: "A reviewer went over PR #<number>. Fix what you agree with and push, then say what you left alone and why:" and the reply word for word.

**Merge a PR:** find it as above. The first time, check your memory for the user's merge choice; if none, ask once "I'll squash-merge it once the checks pass. Should I merge PRs like that from now on without checking with you first?" and save it. Hand off with `delegate_task`: "Merge PR #<number> once its checks pass. In `<repo_path>`, run `gh pr checks <number> --watch --fail-fast --interval 30` with the shell tool's `timeout=560`, again while pending. Never `sleep` and poll. If all passed, `gh pr merge <number> --squash` and report whether it merged; otherwise don't merge and report which checks failed, or that there were none. Report any `gh` error word for word."

**Suggested prompts:** Claude Code's dimmed next prompt after `❯` isn't queued or a question: never report it or press `enter` for it.

## Reporting back by voice

Speak like a colleague: what changed, whether tests passed, what's committed or open, in two to four sentences. If the spend and context were on screen, one more: "That took about eleven cents, context at six percent." Never read out terminal output, diffs or files; offer to show them instead.
