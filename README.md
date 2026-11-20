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
  Linux to work for you, and for working with an agent the modern way. Talk to it as
  in a chat, allow each change it explains in its own words, and dock its view at the
  edge of the screen when you want to see every step.
</p>

![kortex in KRunner: Meta+A and a question answered in a sentence; then PhotoCraft installed in two steps, each asking first: what the step would do, the agent's own words and the command, answered with the row Allow this once, and the second, while the palette was closed, with Allow once on the notification; then Show every step docks the agent's view of the conversation at the right edge](docs/demo/kortex-demo.gif)

<sub>A replay of a real run of kortex 0.9.0 with opencode 1.19.6 and a scripted model
that thinks before it answers and says what each step does: kortex answered over D-Bus
what the palette shows, and the panel shows the conversation as opencode kept it; both
are drawn for the replay. How it is made: [`docs/demo/`](docs/demo/).</sub>

kortex is a small part of [patina](https://github.com/patina-os/patina), the polish
layer for Plasma on Debian: patina installs it with its AI agent and puts it on Meta+A.
You do not need patina for it: kortex runs on any Plasma 6 desktop, whatever the
distribution, that has [opencode](https://opencode.ai) or
[Hermes Agent](https://github.com/NousResearch/hermes-agent). They are the agent, and
kortex is where you talk to it. [Will it run on my system?](#will-it-run-on-my-system)

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
  answer back; Show every step docks the agent's own view of the conversation
  at the screen's right edge, for longer work.

An agent can be wrong, a small local model more often than a large one: read what it
proposes before you allow it.

## What it does

- **Ask from anywhere.** Meta+A opens the palette on the agent alone, and on the
  conversation while the agent waits for your answer. Any search in Meta+Space or the
  start menu ends with "Ask kortex: …", first when it reads as a question.
- **A conversation, not a console.** The palette reads like a chat: the seconds,
  what the agent wrote, thought or does last ("running a command"), then its answer.
  Its commands are in the panel, for when you want to see them.
- **Nothing changes without a yes.** Every command, file change, web request or
  service's tool the agent's permissions hold back is a row of its own: what the step
  would do, worked out by kortex ("Deletes", "Runs as root", "Changes your network"),
  then the agent's words ("I'll add the alias to your .bashrc, after a copy of it"),
  then the step itself, since words can be wrong: the command, the file and its
  changed lines, or what a service's tool is given. Its answers are rows to read and
  pick: Allow this once, or Don't. If you would rather say yes once per task, turn on
  Allow the rest of this task ([what it covers, and what it gives up](docs/usage.md#allow-the-rest-of-this-task)); it is off until you do. What the permissions hold back is the agent's configuration: patina's asks
  before every change, on the machine and in a connected service.
- **It asks you, too.** A question of the agent's is a row with its answers; pick
  one or type your own.
- **Walk away.** Click elsewhere and a notification follows the request to its answer.
  One that asks for a step takes your answer where it is: Allow once or Deny. Show
  brings the conversation back.
- **See what it does.** Show every step (or Meta+Shift+A with patina) docks
  the agent's own interface on the conversation at the screen's right edge, full
  height and a fifth of its width, above your windows: every step and command, while
  you work on beside it.
- **Local first.** kortex sends nothing anywhere itself. Where a request goes is the
  agent's configuration: a model on the machine through Ollama, or the provider you chose.

Status: alpha. Its CI runs it against a real `opencode serve` and a real Hermes gateway,
and patina's against a real KRunner in a Plasma 6.3 session on Debian 13; the
[roadmap](docs/roadmap.md) says what is still to prove on a real desktop.

## Will it run on my system?

kortex lives in KRunner, Plasma's command palette, so what matters is the desktop and
not the distribution.

- **Runs on** any distribution with **KDE Plasma 6**, under Wayland or X11, on x86_64
  and arm64 (kortex is plain Python, with nothing to compile): Debian 13, Fedora KDE,
  openSUSE Tumbleweed, Arch, Manjaro and CachyOS, KDE neon, Kubuntu 25.04 and later.
- **Tested** only on Debian 13 with Plasma 6.3, so far. On the other distributions it
  should work just the same. If you try one,
  [tell us how it went](https://github.com/patina-os/kortex/issues).
- **Does not run on** Plasma 5 (Debian 12, Kubuntu 24.04 LTS, openSUSE Leap 15), and
  not on GNOME, Xfce, Cinnamon, Hyprland or any other desktop, because those have no
  KRunner.
- **Needs** your distribution's Python 3 with its D-Bus bindings, plus an agent:
  opencode or Hermes Agent, with a model it can use (a local one through Ollama, or a
  provider's). kortex installs per user and needs no root.

To see which Plasma you have, run `plasmashell --version` or open System Settings,
About this System.

## Install

**With patina,** the AI step of `patina install` (or `patina ai on`) installs kortex.
Skip the rest of this section.

**On any other Plasma 6 desktop,** kortex needs Python's D-Bus bindings, an agent
(opencode or Hermes Agent) with a model, and rules that make the agent ask before every
change, which patina would set up. [Setting up kortex without patina](docs/setup.md)
walks you through them, with a cloud provider's login or your own OpenAI-compatible
API. Then kortex itself installs per user, under `~/.local/share`, with no root:

```sh
git clone https://github.com/patina-os/kortex && cd kortex
./install.sh
```

Then open KRunner (Meta+Space or Alt+Space), type a request and pick "Ask kortex".
`git pull` and `./install.sh` again update it, `./uninstall.sh` removes it.

## Use

| Where | What |
|---|---|
| Meta+A (patina) | the palette on the agent alone: type a request, Enter; the conversation while the agent waits for you |
| Meta+Shift+A (patina) | the panel: the agent's own view of the conversation, docked at the right edge |
| any search, Meta+Space or the start menu | ends with "Ask kortex: …"; first for a question |
| `ai` and a request | a row that asks only the agent; Enter sends it |
| `ai` | the recent conversations |
| `kortex ›` | the current conversation; type after it to go on |
| a step that asks | its rows: Allow this once · Don't, stop here · Always allow, where offered |
| what the agent asks you | Enter on an answer, or type your own |
| while the agent works | its last words · its steps in a word each · Show every step · Stop |
| the answer | Enter copies · Show every step |

## Documentation

- [Setting up kortex without patina](docs/setup.md): the D-Bus bindings, opencode, a
  model (a cloud provider or your own OpenAI-compatible API) and the rules that make
  it ask first
- [Using kortex](docs/usage.md): the searches, the conversation, the rows that ask, the
  panel, the notifications, a key of your own, Hermes, and what to look at when it
  seems stuck
- [How it works](docs/how-it-works.md): the KRunner D-Bus runner, the agents' HTTP
  APIs, the palette that pulls, services connected on the fly
- [Principles](docs/principles.md): privacy, no action without a yes, root through a
  dialog, small and boring
- [Development](docs/development.md): the tests, the demo, releases
- [Design studies](docs/design/): the palette's redesign of November 2026 as prototypes
  to click through, the options not taken, and what would reopen the question
- [Roadmap](docs/roadmap.md): choices still open, how it reaches users
- [Changelog](CHANGELOG.md)

## Licence

GPL-3.0-or-later, like patina; the logo and the icon too. The icon is patina's survey
point, the ▲, inside two contour lines, in the colours of patina's aurora theme.
