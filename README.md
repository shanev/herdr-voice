# herdr-voice

[![test](https://github.com/shanev/herdr-voice/actions/workflows/test.yml/badge.svg)](https://github.com/shanev/herdr-voice/actions/workflows/test.yml)

Run coding agents in [herdr](https://herdr.dev) by talking to [Hermes](https://hermes-agent.nousresearch.com) or [Hark](https://heyhark.app)'s own agent. Built for Hark, the voice app for your personal agent, and works with any Hermes chat. The rest of this page says Hermes; Hark's agent does the same.

## Using it

> Create a new Claude session for hark.

> Start omp in modelrelay.

> What's asterism 2 doing?

> Tell the hark Claude to fix the failing test, and let me know when it's done.

> Stop the codex agent.

> What's waiting on me?

> Where did we do the swipe fix?

> Have Codex review it.

> Merge it once CI passes.

> Show me that one.

A new session is always a new agent. It opens as a tab in the repo's herdr workspace, or in a new workspace if the repo doesn't have one yet. It gets a permanent name taken from the task, like `apple-models`, which herdr's sidebar shows in place of the agent's kind, and that's how Hermes refers to it. Agents you start yourself at the desk stay unnamed, so Hermes calls them by their pane's label (one you set with `herdr pane rename`, or a plugin such as Auto Title did), or else by workspace and tab, like `hark · 2`; never by pane id. It never touches the agents already running there. When another agent is already working in the repo's checkout, or you name an issue or branch, the new session gets its own git worktree instead, branched from the latest default branch, so agents don't switch branches or reset files under each other. Hermes offers to remove a worktree once its PR is merged, and never removes one on its own.

It works with every agent herdr can start: Claude Code, Codex, omp, Grok, pi, OpenCode, Cursor, Gemini, Amp and the rest. Hermes asks once whether sessions should run with permission prompts off and remembers your answer. For agents the skill doesn't list, it finds the flag in the agent's `--help`.

Hermes doesn't sit waiting while an agent works. It hands the wait to a background subagent, so you can keep talking, and tells you when the agent is done or stuck. Ask it to wait instead and it will. When one request starts several agents, you get one report once they're all done, not one per agent. A done report mentions what the task cost and how full the agent's context is, when the agent's status line shows them.

"Have Codex review it" starts a second agent on the PR's branch to review it without changing anything, and tells you the verdict and anything blocking; "send that to the Claude" passes the findings back to the agent that wrote it. "Merge it once CI passes" waits for the checks and squash-merges, or tells you which check failed. The first time, Hermes asks whether to merge like that from then on without checking with you. "Show me that one" switches Herdr's window to the agent you were just talking about.

"What's waiting on me?" goes through every agent and tells you which are still working, which have a question for you (and what it is), and which finished with a result you haven't heard yet. "Where did we do the swipe fix?" searches the agents' transcripts and tells you which one worked on it, without sending it anything.

Short phrases work too: "status" for what's running, "waiting on me" or "anything for me" for the sweep above, "nudge" an agent to have it carry on with its task, and "stop" one to interrupt it.

Replies are written to be heard: a few sentences on what changed, whether tests passed and what's left, never terminal output read aloud. The skill also expects speech-to-text mishearings ("herder" or "burger" for herdr, "cloud code" for Claude Code).

## Install

On the machine where Hermes and herdr run:

```
hermes skills install shanev/herdr-voice/skills/herdr-voice --category autonomous-ai-agents
```

Hark's setup installs it for you when herdr is present, on Hermes or Hark's agent. `hermes skills check` picks up updates; with Hark's agent, update it from Skills in Hark's settings.

## In Hark

Hark shows a page per herdr workspace, after its general page: this machine's workspaces and those on herdr's saved machines (`herdr machine list`), each with the machine it's on. Swipe to one and you see its agents, each with its name, whether it's working, waiting on you or done, and what it's on. A blocked agent shows its question as a card, with a button per option; a desk agent that finished shows its result to hear. What you say on a workspace's page is about that workspace: "start Claude on the flaky test" opens a new tab there, and "what's the Claude doing?" means the Claude in it.

The skill tells Hark all this through `metadata.hark` in its frontmatter (`contexts` and `items`, [the voice extension's section 8](https://github.com/tensor-systems/hark/blob/main/hark-agent/docs/voice-extension.md#8-skill-state-contexts-and-items)): Hark's agent runs `scripts/hark_state.py` to list workspaces, create one, and list a workspace's agents. Nothing about herdr is in Hark itself.

## Desk alerts

Agents you start yourself at the desk get the same treatment, with a herdr plugin. When one finishes, or stops on a question, while you're away from the computer, the plugin tells Hark's agent (Hermes with Hark's `hark-auth` plugin 1.8 or later, else Hark's own agent) that its workspace changed. The agent reads the workspace's agents again and pushes the change to your phone: "apple-models in hark needs you", with the question's options as buttons. A tap opens that workspace's page in Hark, where the agent shows as waiting, with its question as a card.

```
herdr plugin install shanev/herdr-voice/plugins/desk-alerts
```

When the agent stopped on a question or a permission prompt, whatever agent it is, its card carries the screen, and Hark reads the question and its options from it, with a button per option and a field for a typed answer. A tap sends a turn that answers only if the question is still on screen; it may have been answered at the desk already. When the dialog has another question after that one, it comes as a new card. A question leaves your phone once it's over: answered at the desk or through Hark, or its agent quit, or its pane closed.

It runs on herdr's `pane.agent_status_changed`, `pane.closed`, `pane.exited` and `pane.moved` events (herdr 0.9.3 or later), so nothing new keeps running in the background. It alerts when an agent goes from working to done or blocked, after the status has held for 20 seconds (Claude can end a reply while a command it started still runs), and only if there's been no keyboard or mouse input for 5 minutes. Agents Hermes started through this skill are skipped, since Hermes reports those itself. Settings, all optional, go in the `.env` file in `herdr plugin config-dir herdr-voice.desk-alerts`: `AWAY_AFTER_MINUTES`, `SETTLE_SECONDS`, and `HERMES_API_URL` and `HERMES_API_KEY` if the agent's API isn't the one in `~/.hermes/.env` (or `~/.hark-agent`).

## Why Hermes runs outside herdr

Hermes' gateway usually runs as a background service, not in a herdr pane. herdr's own skill stops when `HERDR_ENV` isn't set, so it refuses to work from there. This skill controls herdr from outside through its socket. It addresses agents by pane, never by focus, and never attaches, focuses or closes anything it didn't start, except that it switches to an agent when you ask to see it. If both skills are installed, Hermes may load herdr's by name; Hark's instructions point it at this one.

## Writing the description

Hermes' skill index keeps only the first 60 characters of a description. That's all the model sees when choosing a skill, so the description has to win against Hermes' built-in `claude-code` and `codex` skills, which run agents in tmux or one-shot. The tests enforce the limit.

The `metadata.hark` block in the frontmatter is for [Hark](https://heyhark.app): a one-sentence summary, the command the skill needs, the agent capabilities it needs (`background_tasks` for its hand-offs, `memory` for the choices it saves), example phrases, a routing sentence Hark adds to its instructions so Hermes picks this skill for coding agents, and the workspace and agent commands above with the sentence Hark adds to a workspace page's runs. Hermes ignores it, and Hark marks the skill on an agent that lacks one of those capabilities. The tests check its limits: summary up to 120 characters, routing up to 300, at most six phrases, the scope sentence up to 300 with only the `{id}`, `{name}` and `{location}` placeholders, and pushed states from Hark's five.

## Running the tests

```
python3 -m unittest discover -s tests -v
```

Stdlib only.

## License

MIT
