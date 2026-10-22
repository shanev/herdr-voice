# herdr-voice

[![test](https://github.com/shanev/herdr-voice/actions/workflows/test.yml/badge.svg)](https://github.com/shanev/herdr-voice/actions/workflows/test.yml)

Run coding agents in [herdr](https://herdr.dev) by talking to [Hermes](https://hermes-agent.nousresearch.com) or [Vux](https://vux.dev)'s own agent. Built for Vux, voice control for your agents (it was called Hark), and works with any Hermes chat. The rest of this page says Hermes; Vux's agent, vux-agent, does the same.

## Using it

> Create a new Claude session for website.

> Start omp in modelrelay.

> What's asterism 2 doing?

> Tell the api Claude to fix the failing test, and let me know when it's done.

> Stop the codex agent.

> What's waiting on me?

> Where did we do the swipe fix?

> Have Codex review it.

> Merge it once CI passes.

> Show me that one.

A new session is always a new agent. It opens as a tab in the repo's herdr workspace, or in a new workspace if the repo doesn't have one yet. It gets a permanent name taken from the task, like `apple-models`, which herdr's sidebar shows in place of the agent's kind, and that's how Hermes refers to it. Agents you start yourself at the desk get one too, once, from their first task, when the desk-alerts plugin below is installed: Claude on "Tesla Roadster physics simulation" becomes `tesla-roadster-physics`. Until its first task an agent is `new-<pane>` (`new-w1p-p8`). A name never changes after that unless you rename the agent in herdr; Hermes never calls an agent by pane id. It never touches the agents already running there. When another agent is already working in the repo's checkout, or you name an issue or branch, the new session gets its own git worktree instead, branched from the latest default branch, so agents don't switch branches or reset files under each other. Hermes offers to remove a worktree once its PR is merged, and never removes one on its own.

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

Vux's setup installs it for you when herdr is present, on Hermes or vux-agent. `hermes skills check` picks up updates; with vux-agent, update it from Skills in Vux's settings.

## In Vux

Vux shows a page per herdr workspace on this machine, after its general page. Another of herdr's saved machines shows when you pair it in Vux as a computer of its own, with this skill and the plugin below installed there. Swipe to one and you see its agents, each with its name, its kind, what it's working on, and whether it's working, waiting on you or done (the workspace in your home folder is "Home"). A blocked agent shows its question as a card, with a button per option; a desk agent that finished shows the start of its result, with the whole of it to hear. What you say on a workspace's page is about that workspace: "start Claude on the flaky test" opens a new tab there, and "what's the Claude doing?" means the Claude in it.

The skill tells Vux all this through `metadata.hark` in its frontmatter (`contexts` and `items`, [the voice extension's section 8](https://github.com/tensor-systems/hark/blob/main/vux-agent/docs/voice-extension.md#8-skill-state-contexts-and-items)): vux-agent (or Hermes' vux-auth plugin) runs `scripts/vux_state.py` to list workspaces, create one, and list a workspace's agents. Nothing about herdr is in Vux itself. Each agent is an item in format version 2 (`items.version: 2`), with these keys on every item, each with one meaning:

| Key | Meaning |
| --- | --- |
| `id` | herdr's pane id |
| `name` | the agent's herdr agent name: unique among herdr's agents, kept for the agent's life; `new-<pane>` (pane id lowercased, `:` as `-`) until it has one |
| `kind` | the agent kind as people write it: Claude, Codex, omp, Grok |
| `task` | what it's doing now, from the title the agent gives its terminal, without the agent's own decoration; `New session` before its first task |
| `state` | `working`, `waiting`, `done` or `idle` |
| `card` | a question card for a blocked agent, a result card for a desk agent's finish you haven't heard, else `null` |

The naming rule is in `skills/herdr-voice/scripts/agent_names.py`, which the skill's script and the desk-alerts plugin share: a short kebab-case phrase from the task, at most four words without the small ones, lowercase, fitting herdr's rule for names (`[a-z][a-z0-9_-]{0,31}`), with `-2`, `-3`… when another agent has it.

### With Auto Title

herdr-voice works beside [herdr.auto-title](https://github.com/kryptamine/herdr-auto-title), which labels tabs and panes for the work in them ("4 · dashboard › Implement OAuth scopes") and rewrites the labels as the work changes. Labels are yours and Auto Title's: herdr-voice never reads them for a name or a task, and never writes them. Agent names are herdr-voice's and yours: Auto Title doesn't touch them, so an agent's name in Vux and in what Hermes says stays put while its labels move. Vux doesn't poll for these: it keeps the agent's event stream open, and the desk-alerts plugin below reports each change, so install it on the machine for Vux to show agents change as they do.

## Desk alerts

Agents you start yourself at the desk get the same treatment, with a herdr plugin. When one finishes, or stops on a question, while you're away from the computer, the plugin tells Vux's agents on the machine (Hermes with Vux's `vux-auth` plugin, or `hark-auth` 2.0 or later from before the rename, and vux-agent, each when it's there) that its workspace changed, all at once. Each pushes only to the phones paired with it. The agent reads the workspace's agents again and pushes the change to your phone: "apple-models in api needs you", with the question's options as buttons. A tap opens that workspace's page in Vux, where the agent shows as waiting, with its question as a card.

```
herdr plugin install shanev/herdr-voice/plugins/desk-alerts
```

When the agent stopped on a question or a permission prompt, whatever agent it is, its card carries the screen, and Vux reads the question and its options from it, with a button per option and a field for a typed answer. A tap sends a turn that answers only if the question is still on screen; it may have been answered at the desk already. When the dialog has another question after that one, it comes as a new card. A question leaves your phone once it's over: answered at the desk or through Vux, or its agent quit, or its pane closed.

It runs on herdr's events (herdr 0.9.3 or later), so nothing new keeps running in the background. Every change Vux shows goes to Vux's agents at once, for an open Vux only (`"push": false`, which needs vux-agent, or Hark's agent from tensor-systems/hark#323 or `hark-auth` 2.0 before the rename): an agent detected or gone, each status change, an agent's pane closed, exited or moved (`pane.agent_detected`, `pane.agent_status_changed`, `pane.closed`, `pane.exited`, `pane.moved`), and a workspace made, closed, renamed or moved (`workspace.created`, `.closed`, `.renamed`, `.moved`, `.reordered`). Title changes (an agent's task) aren't reported on their own: herdr sends `pane.updated` for every redraw of a title, and the title is fresh at the next status change. It pushes when an agent goes from working to done or blocked, after the status has held for 20 seconds (Claude can end a reply while a command it started still runs), and only if there's been no keyboard or mouse input for 5 minutes. Agents Hermes started through this skill are skipped, since Hermes reports those itself. The plugin also names agents started at the desk (`herdr agent rename`), once each: at any of the events above, every agent without a herdr name whose title shows a task gets its name from that task, and Vux is told. An agent that starts working before its title says what on (Claude titles its terminal a moment after the prompt) is looked at again every 2 seconds for up to 2 minutes. An agent with a name, yours or herdr-voice's, is never renamed, and a name stays when the agent moves on to other tasks. herdr clears a name when its agent exits, so the next agent in that pane is named for its own first task. The plugin finds the naming rule in the skill's folder of the repo herdr installs it from. Settings, all optional, go in the `.env` file in `herdr plugin config-dir herdr-voice.desk-alerts`: `AWAY_AFTER_MINUTES`, `SETTLE_SECONDS`, and `HERMES_API_URL` (with `HERMES_API_KEY`, else Hermes' key) for an agent to tell besides those in `~/.hermes/.env` and `~/.vux-agent` (or `~/.hark-agent`, from before the rename).

## Why Hermes runs outside herdr

Hermes' gateway usually runs as a background service, not in a herdr pane. herdr's own skill stops when `HERDR_ENV` isn't set, so it refuses to work from there. This skill controls herdr from outside through its socket. It addresses agents by pane, never by focus, and never attaches, focuses or closes anything it didn't start, except that it switches to an agent when you ask to see it. If both skills are installed, Hermes may load herdr's by name; Vux's instructions point it at this one.

## Writing the description

Hermes' skill index keeps only the first 60 characters of a description. That's all the model sees when choosing a skill, so the description has to win against Hermes' built-in `claude-code` and `codex` skills, which run agents in tmux or one-shot. The tests enforce the limit.

The `metadata.hark` block in the frontmatter is for [Vux](https://vux.dev): a display title, a one-sentence summary, the command the skill needs, the agent capabilities it needs (`background_tasks` for its hand-offs, `memory` for the choices it saves), example phrases, a routing sentence Vux adds to its instructions so Hermes picks this skill for coding agents, and the workspace and agent commands above with the sentence Vux adds to a workspace page's runs. Hermes ignores it, and Vux marks the skill on an agent that lacks one of those capabilities. The tests check its limits: summary up to 120 characters, routing up to 300, at most six phrases, the scope sentence up to 300 with only the `{id}`, `{name}` and `{location}` placeholders, and pushed states from Vux's five. It keeps the name `hark` from before Hark became Vux: Vux, vux-agent and vux-auth read `metadata.vux` and fall back to `metadata.hark`, but Hark-era apps, agents and `hark-auth` read only `metadata.hark`, so it moves to `vux` once those are gone.

## Running the tests

```
python3 -m unittest discover -s tests -v
```

Stdlib only.

## License

MIT
