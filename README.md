# kortex

![kortex in KRunner: a question answered, then PhotoCraft installed in two steps, each waiting for Allow once](docs/demo/kortex-demo.gif)

<sub>A replay of a real run of kortex 0.1.0 with opencode 1.19.4 and a scripted model:
kortex answered over D-Bus what you see, the panel is drawn for the replay. How it is
made: [`docs/demo/`](docs/demo/).</sub>

An AI agent in KDE Plasma's command palette. Press Meta+Space, type `ai` and what you
want, and the agent answers right there in KRunner: it explains, changes settings once
you allow it, writes code and works on your servers. kortex is the palette end of the
agent; the agent itself is [opencode](https://opencode.ai), running on your machine.

Status: first version, 0.1.0. It passes its tests against a real `opencode serve`
(1.19) but has not yet run inside a Plasma session; that run is what "First release,
done when" below asks for. kortex comes from [patina](https://github.com/patina-os/patina),
which asks for the agent at install time, but it works on any Plasma 6 desktop that has
opencode.

## Install

Needs Plasma 6, opencode (`npm install -g opencode-ai`, or patina's AI step) with a
model it can use, and Python's D-Bus bindings:

```sh
sudo apt install python3-dbus python3-gi     # Debian and Ubuntu
git clone https://github.com/patina-os/kortex && cd kortex
./install.sh                                 # per user, under ~/.local/share; no root
```

Then open KRunner (Meta+Space or Alt+Space) and type `ai` and your request.
`./uninstall.sh` removes it again.

## What it does

```text
Meta+Space   ai why is my wifi slow
             ▲ Ask the agent                                   Enter
             ─────────────────────────────────────────────────────────
             ▲ Your wifi runs on 2.4 GHz at 72 Mbit/s; the 5 GHz
               network "home-5G" is in range ...                Copy · Open in terminal
             ▲ Allow: nmcli connection up home-5G              Allow once · Always · Deny
```

- `ai <request>` shows one match, "Ask the agent". Enter sends the request and reopens
  KRunner on the conversation, `ai ›`, which shows the answer as it grows, as a
  multi-line match. With a conversation going, a second match continues it instead of
  starting a new one; so does typing after the marker (`ai › and in dark mode?`).
- Every action the agent wants to take that its permissions put on `ask` (changing a
  file, running a command, reaching a server) shows as its own match with three
  actions: Allow once, Always, Deny. Nothing runs before you choose; Enter alone on
  such a match answers nothing. While KRunner is closed, a request for permission
  comes as a notification.
- Copy puts the answer on the clipboard; Open in terminal continues the same session in
  opencode's own interface, for longer work such as code.
- `ai` alone lists the recent conversations to continue.
- The words come from what you type, the rest from the agent: kortex adds no prompt of
  its own beyond the session title, so the agent's instructions (patina's
  `defaults/agent.md`, or the user's own `AGENTS.md`) decide how it behaves.

## How it works

```text
KRunner ──D-Bus (org.kde.krunner1)──> kortex ──HTTP on 127.0.0.1──> opencode serve
                                                                        │
                                                         Ollama (local) or a cloud provider
```

- **A KRunner D-Bus runner.** KRunner calls `Match`, `Actions` and `Run` on kortex over
  the session bus (`org.kde.krunner1`), the documented interface for runners that are
  not compiled into KRunner. kortex is a small program started on demand by D-Bus
  activation; it holds no state KRunner needs.
- **opencode as the agent.** kortex starts `opencode serve` on 127.0.0.1 with a random
  port and a password (`OPENCODE_SERVER_PASSWORD`, generated once, kept in
  `~/.config/kortex/` with mode 0600), and stops it, and itself, after a quiet quarter
  of an hour. It uses the server's API: `POST /session` for a new conversation, `POST
  /session/:id/prompt_async` to send the request, `GET /session/:id/message` and `GET
  /session/status` for the answer, `GET /permission` for what the agent asks to do and
  `POST /permission/:id/reply` with `once`, `always` or `reject` for the three actions.
  It reads those few calls when KRunner asks or every 0.7 s while the conversation is
  on screen; no event stream to keep open. opencode reads its own configuration, so
  the model, the provider and the permissions are the user's (patina writes them at
  install time). `KORTEX_OPENCODE_URL` (and `KORTEX_OPENCODE_PASSWORD`) point kortex
  at a server that runs already instead.
- **Answers in a palette that pulls.** KRunner asks for matches; it is not told about
  new ones. While the conversation is on screen and changes, kortex runs the query
  again through KRunner's own D-Bus method (`org.kde.krunner /App querySingleRunner`)
  with the marker `ai ›`, so KRunner asks again and gets the news. The marker is the
  only thing kortex adds to the query line, and a follow-up typed after it is kept.
- **Open in terminal** runs the terminal Plasma names (`TerminalApplication` in
  `kdeglobals`, else konsole) with `opencode attach <server> --session <id>`, the
  password in the environment, not on the command line, so nothing is lost when a
  request outgrows the palette.
- **Tests.** `python3 -m unittest discover -s tests` checks what kortex shows and does
  with opencode faked; `python3 tests/e2e.py` runs it as KRunner would: a private
  session bus that starts kortex by D-Bus activation, a real `opencode serve`, and a
  test model on localhost that asks to run a command. It checks that nothing runs
  before Allow once, and that it runs after. CI runs both.

## Rules

- Free software: GPL-3.0-or-later, like patina.
- Local first. kortex itself sends nothing anywhere; where the request goes is
  opencode's configuration. With Ollama, nothing leaves the machine.
- No action without a yes. kortex never answers a permission request on its own, and
  "Always" is opencode's own rule, visible and removable in its configuration.
- Python 3, with `python3-dbus` and `python3-gi` from Debian's archive: one readable
  program, no build step.
- Small and boring: one program, one desktop file for KRunner, one D-Bus service file.
  No daemon beyond what D-Bus activation starts, no own model runtime, no telemetry.
- Plasma 6 and its D-Bus runner interface only; no compiled KRunner plugin, so it does
  not break with a Plasma update's ABI.

## Choices still open

- **Streaming in KRunner.** Whether reopening KRunner on every change keeps the
  selection steady enough to read a growing answer, or whether kortex should reopen it
  only when the answer is complete, with a "thinking" match before it. To try on Plasma
  6.3 (Debian 13) and the current Plasma.
- **Other agents.** The opencode API is the only backend at first. Another agent (Goose,
  for example) would be a second small adapter behind the same three calls: send,
  follow, answer a permission.

## How it reaches users

1. A release here: a tarball with the program, the KRunner desktop file
   (`~/.local/share/krunner/dbusplugins/kortex.desktop`) and the D-Bus service file
   (`~/.local/share/dbus-1/services/`), installed per user, no root.
2. The KDE Store, where Plasma's "Get New Stuff" finds KRunner runners.
3. A Debian package, so that patina can install it from Debian's archive (patina takes
   software only from Debian, Flathub or an upstream installer, and binds the palette
   once kortex comes from one of them).

## First release, done when

- `ai <request>` in KRunner on Debian 13's Plasma answers from a local model through
  opencode and Ollama, and from a cloud model with the same configuration switched.
- A request that needs a change shows the Allow / Always / Deny matches, and nothing
  runs before one is chosen.
- Open in terminal continues the session.
- Installing and removing it per user leaves nothing behind but the password file.
