<h1 align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/logo.svg">
    <img src="docs/logo-dark.svg" alt="kortex" width="300">
  </picture>
</h1>

<p align="center">
  <a href="https://github.com/patina-os/kortex/actions/workflows/ci.yml"><img src="https://github.com/patina-os/kortex/actions/workflows/ci.yml/badge.svg?branch=main" alt="ci"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/licence-GPL--3.0--or--later-3cff9d?labelColor=0b0a10" alt="licence: GPL-3.0-or-later"></a>
  <a href="https://github.com/patina-os/patina"><img src="https://img.shields.io/badge/part%20of-patina-9b6bff?labelColor=0b0a10" alt="part of patina"></a>
</p>

<p align="center">
  The AI agent in KDE Plasma's command palette, made for troubleshooting, for getting
  Linux to work for you, and for working with an agent the modern way. Ask in your own
  words, watch it work where you asked, and allow each change before it happens.
</p>

![kortex in KRunner: Meta+A and a question, the agent thinking, then PhotoCraft installed in two steps, each waiting for Allow once, and a notification with the answer after KRunner was closed](docs/demo/kortex-demo.gif)

<sub>A replay of a real run of kortex 0.4.1 with opencode 1.19.5 and a scripted model
that thinks before it answers: kortex answered over D-Bus what you see, the panel is
drawn for the replay. How it is made: [`docs/demo/`](docs/demo/).</sub>

kortex is a small part of [patina](https://github.com/patina-os/patina), the polish
layer for Plasma on Debian: patina installs it with its AI agent and puts it on Meta+A.
It works on any Plasma 6 desktop that has [opencode](https://opencode.ai) or
[Hermes Agent](https://github.com/NousResearch/hermes-agent): they are the agent, kortex
is where you talk to it.

## Made for

- **Troubleshooting.** "Why is my wifi so slow?", "the printer stopped printing",
  "what keeps the fan busy?" The agent looks where a forum thread would send you
  (`journalctl`, `nmcli`, `lpstat`, `top`), says what it found, and proposes the fix,
  which waits for your yes.
- **Getting Linux to work for you.** "Install PhotoCraft", "switch to the light theme",
  "make the touchpad scroll the other way": say what you want, not the commands for
  it. On patina the agent knows patina's own (`patina install`, `patina theme`),
  elsewhere it uses the system's tools, and every change is a row you allow.
- **Working with an agent.** Have it write a script, find its way through a project,
  work on your server, or file a ticket through a service you connected (Linear,
  Notion, GitHub, Nextcloud). Walk away while it works and a notification brings the
  answer back; Open in terminal takes the conversation into the agent's own interface
  for longer work.

An agent can be wrong, a small local model more often than a large one: read what it
proposes before you allow it.

## What it does

- **Ask from anywhere.** Meta+A opens the palette on the agent alone. Any search in
  Meta+Space or the start menu ends with "Ask kortex: …", first when it reads as a
  question. `ai` and a request asks at once, as before.
- **Watch it work.** The palette counts the seconds, shows what the agent wrote or
  thought last and a row for each step it takes, then its answer. Every row but the
  answer is one line, so the palette holds still while the agent works.
- **Nothing changes without a yes.** Every command, file change, web request or
  service's tool the agent's permissions hold back is a row of its own, with the
  whole command, the changed lines or what the tool is given: Allow once, Always or
  Deny. What the permissions hold back is the agent's configuration: patina's asks
  before every change, on the machine and in a connected service.
- **It asks you, too.** A question of the agent's is a row with its answers; pick
  one or type your own.
- **Walk away.** Click elsewhere and a notification follows the request to its answer.
  Its Show brings the conversation back.
- **Go on in a terminal.** Open in terminal hands the conversation to the agent's own
  interface, for longer work such as code.
- **Local first.** kortex sends nothing anywhere itself. Where a request goes is the
  agent's configuration: a model on the machine through Ollama, or the provider you chose.

Status: alpha. Its CI runs it against a real `opencode serve` and a real Hermes gateway,
and patina's against a real KRunner in a Plasma 6.3 session on Debian 13; the
[roadmap](docs/roadmap.md) says what is still to prove on a real desktop.

## Install

With patina, the AI step of `patina install` (or `patina ai on`) installs it. Elsewhere it
needs Plasma 6, an agent with a model it can use (opencode: `npm install -g opencode-ai`;
Hermes Agent: its installer) and Python's D-Bus bindings:

```sh
sudo apt install python3-dbus python3-gi     # Debian and Ubuntu
git clone https://github.com/patina-os/kortex && cd kortex
./install.sh                                 # per user, under ~/.local/share; no root
```

`./uninstall.sh` removes it again. For Hermes instead of opencode, write `hermes` into
`~/.config/kortex/agent`; to open the palette on kortex with a key of your own, see
[Using kortex](docs/usage.md#a-key-for-the-agent).

## Use

| Where | What |
|---|---|
| Meta+A (patina) | the palette on the agent alone: type a request, Enter |
| any search, Meta+Space or the start menu | ends with "Ask kortex: …"; first for a question |
| `ai` and a request | a row that asks only the agent; Enter sends it |
| `ai` | the recent conversations |
| `kortex ›` | the current conversation; type after it to go on |
| a row that asks | Allow once · Always · Deny |
| what the agent asks you | Enter on an answer, or type your own |
| the answer, or the row that says how long | Copy · Open in terminal · Stop |
| a step | Open in terminal |

## Documentation

- [Using kortex](docs/usage.md): the searches, the conversation, the rows that ask, the
  notifications, a key of your own, Hermes, and what to look at when it seems stuck
- [How it works](docs/how-it-works.md): the KRunner D-Bus runner, the agents' HTTP
  APIs, the palette that pulls, services connected on the fly
- [Principles](docs/principles.md): privacy, no action without a yes, root through a
  dialog, small and boring
- [Development](docs/development.md): the tests, the demo, releases
- [Roadmap](docs/roadmap.md): choices still open, how it reaches users
- [Changelog](CHANGELOG.md)

## Licence

GPL-3.0-or-later, like patina; the logo and the icon too. The icon is patina's survey
point, the ▲, inside two contour lines, in the colours of patina's aurora theme.
