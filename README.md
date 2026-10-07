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

Replies are written to be heard: a few sentences on what changed, whether tests passed and what's left, never terminal output read aloud. The skill also expects speech-to-text mishearings ("herder" or "burger" for herdr, "cloud code" for Claude Code).

## Install

On the machine where Hermes and herdr run:

```
hermes skills install shanev/herdr-voice/skills/herdr-voice --category autonomous-ai-agents
```

Hark's setup installs it for you when herdr is present. `hermes skills check` picks up updates.

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
