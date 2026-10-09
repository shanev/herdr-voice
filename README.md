# herdr-voice

[![test](https://github.com/shanev/herdr-voice/actions/workflows/test.yml/badge.svg)](https://github.com/shanev/herdr-voice/actions/workflows/test.yml)

Run coding agents in [herdr](https://herdr.dev) by talking to [Hermes](https://hermes-agent.nousresearch.com). Built for [Hark](https://heyhark.app), the voice app for Hermes, and works with any Hermes chat.

## Using it

> Create a new Claude session for hark.

> Start omp in modelrelay.

> What's asterism 2 doing?

> Tell the hark Claude to fix the failing test, and let me know when it's done.

> Stop the codex agent.

A new session is always a new agent. It opens as a tab in the repo's herdr workspace, or in a new workspace if the repo doesn't have one yet. It's unnamed, like an agent you start yourself, so herdr's sidebar shows `hark · 2` with `claude` under it, and that's how Hermes refers to it. It never touches the agents already running there.

It works with every agent herdr can start: Claude Code, Codex, omp, Grok, pi, OpenCode, Cursor, Gemini, Amp and the rest. Hermes asks once whether sessions should run with permission prompts off and remembers your answer. For agents the skill doesn't list, it finds the flag in the agent's `--help`.

Hermes doesn't sit waiting while an agent works. It hands the wait to a background subagent, so you can keep talking, and tells you when the agent is done or stuck. Ask it to wait instead and it will.

Replies are written to be heard: a few sentences on what changed, whether tests passed and what's left, never terminal output read aloud. The skill also expects speech-to-text mishearings ("herder" or "burger" for herdr, "cloud code" for Claude Code).

## Install

On the machine where Hermes and herdr run:

```
hermes skills install shanev/herdr-voice/skills/herdr-voice --category autonomous-ai-agents
```

Hark's setup installs it for you when herdr is present. `hermes skills check` picks up updates.

## Desk alerts

Agents you start yourself at the desk get the same treatment, with a herdr plugin. When one finishes, or stops on a question, while you're away from the computer, the plugin has Hermes write a sentence about it in a session called "Desk agents". If you're in a conversation in Hark, it tells you at the next pause, in whichever session you're in, with a button to "Desk agents" so you can answer.

```
herdr plugin install shanev/herdr-voice/plugins/desk-alerts
```

When the agent stopped on a Claude Code permission prompt, the alert also carries the prompt as data, so Hark shows it as a card with the exact command and Approve once / Deny. A tap sends Hermes a turn that answers the prompt only if it's still on screen; it may have been answered at the desk already.

It runs on herdr's `pane.agent_status_changed` event, so nothing new keeps running in the background. It alerts when an agent goes from working to done or blocked, after the status has held for 20 seconds (Claude can end a reply while a command it started still runs), and only if there's been no keyboard or mouse input for 5 minutes. Agents Hermes started through this skill are skipped, since Hermes reports those itself. Settings, all optional, go in the `.env` file in `herdr plugin config-dir herdr-voice.desk-alerts`: `AWAY_AFTER_MINUTES`, `SETTLE_SECONDS`, and `HERMES_API_URL` and `HERMES_API_KEY` if Hermes' API isn't the one in `~/.hermes/.env`.

## Why Hermes runs outside herdr

Hermes' gateway usually runs as a background service, not in a herdr pane. herdr's own skill stops when `HERDR_ENV` isn't set, so it refuses to work from there. This skill controls herdr from outside through its socket. It addresses agents by pane, never by focus, and never attaches, focuses or closes anything it didn't start. If both skills are installed, Hermes may load herdr's by name; Hark's instructions point it at this one.

## Writing the description

Hermes' skill index keeps only the first 60 characters of a description. That's all the model sees when choosing a skill, so the description has to win against Hermes' built-in `claude-code` and `codex` skills, which run agents in tmux or one-shot. The tests enforce the limit.

## Running the tests

```
python3 -m unittest discover -s tests -v
```

Stdlib only.

## License

MIT
