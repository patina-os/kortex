# kortex

![kortex in KRunner: a question answered, then PhotoCraft installed in two steps, each waiting for Allow once](docs/demo/kortex-demo.gif)

<sub>A replay of a real run of kortex 0.1.0 with opencode 1.19.4 and a scripted model:
kortex answered over D-Bus what you see, the panel is drawn for the replay. How it is
made: [`docs/demo/`](docs/demo/).</sub>

An AI agent in KDE Plasma's command palette. Press Meta+Space, type what you want, and
pick "Ask opencode" (it comes first when you type a question); the agent answers right
there in KRunner: it explains, changes settings once you allow it, writes code and works
on your servers. kortex is the palette end of the
agent; the agent itself is [opencode](https://opencode.ai) or
[Hermes Agent](https://github.com/NousResearch/hermes-agent), running on your machine.

Status: 0.4.0. Any search in KRunner ends with "Ask opencode: …", as the start menu's
did, and a question (one that ends with `?`, or starts with how, why, wie, warum and the
like) puts it first: no `ai` to type, though `ai` still asks at once. The conversation
is called by the agent's name, `opencode ›` (`Hermes ›`; `ai ›` still works). While the
agent works, the palette counts the seconds and shows what a reasoning model thinks,
in italics below the answer; with KRunner closed, a notification says that the agent
works on the request and then what it answered, and its Show opens the conversation.
kortex logs each request, what the agent asks first, how the request ended and whether
KRunner followed the conversation to the journal. Since 0.3.4 the rows that ask call the agent by its name: "Ask opencode: …" or
"Ask Hermes: …". Since 0.3.3 `AI ›` and a tab after `ai` work as `ai ›` and a space
do, and an answer to a request the agent no longer waits for is no error. Since 0.3.2
KRunner never waits for the agent, which can take a minute to start: kortex asks it
from a thread of its own and shows "Waiting for the agent…" meanwhile.
Since 0.3.1 the Allow row shows the whole command as it will run, and what Always
would let through. Since 0.3.0 any search in Plasma's start menu ends with "Ask the
agent: …", which opens the conversation in KRunner; since 0.2.0 Hermes Agent is the
second agent kortex talks to, next to opencode. A service connected while kortex runs (an MCP server added to the agent's configuration,
as `patina ai connect` does) reaches opencode's running agent at once, and Hermes' next
conversation. Since 0.1.2 the answer grows on screen in a real KRunner, requests
opencode cannot list are answered from its event stream, and the first `ai` after a
pause takes about 3 s instead of 12; the D-Bus name is `io.github.patina_os.kortex`. It
passes its tests against a real `opencode serve` (1.19), a real Hermes gateway
(v2026.9.24) and a real KRunner on Debian 13, but has not yet run inside a full Plasma
session; that run is what "First release, done when" below asks for. kortex comes from
[patina](https://github.com/patina-os/patina), which asks for the agent at install
time, but it works on any Plasma 6 desktop that has opencode or Hermes.

## Install

Needs Plasma 6, an agent with a model it can use (opencode: `npm install -g
opencode-ai`; Hermes Agent: its installer; or patina's AI step), and Python's D-Bus
bindings:

```sh
sudo apt install python3-dbus python3-gi     # Debian and Ubuntu
git clone https://github.com/patina-os/kortex && cd kortex
./install.sh                                 # per user, under ~/.local/share; no root
```

Then open KRunner (Meta+Space or Alt+Space), type your request and pick "Ask opencode".
To open KRunner on kortex alone, bind a key to `dbus-send --session --type=method_call
--dest=org.kde.krunner /App org.kde.krunner.App.querySingleRunner string:kortex string:`
(patina binds Meta+A).
`./uninstall.sh` removes it again. kortex talks to opencode; for Hermes, write `hermes`
into `~/.config/kortex/agent` (patina does), or set `KORTEX_AGENT=hermes`. A kortex that
runs stops when the file names the other agent, and D-Bus starts one for it.

## What it does

```text
Meta+Space   why is my wifi slow
             ▲ Ask opencode: why is my wifi slow               Enter
             ─────────────────────────────────────────────────────────
             ▲ Your wifi runs on 2.4 GHz at 72 Mbit/s; the 5 GHz
               network "home-5G" is in range ...                Copy · Open in terminal
             ▲ Allow: nmcli connection up home-5G              Allow once · Always · Deny
```

- Any search of three letters or more ends with "Ask opencode: …" (or "Ask Hermes: …",
  by the agent kortex talks to), below what Plasma finds. A search that reads as a
  question or a request comes first instead: it ends with `?`, or it has three words or
  more and starts with a question word or a request (how, why, what, can, fix, install,
  wie, warum, was, kann, hilf, installiere and the like), and with a conversation going
  a second match continues it. `ai <request>` asks at once, as before. Enter sends the
  request and reopens KRunner on the conversation, `opencode ›` (`Hermes ›`), which
  shows the answer as it grows, as a multi-line match. Typing after the marker continues
  the conversation (`opencode › and in dark mode?`); `ai ›` works as the marker too.
- While the agent works, the answer's row says for how long ("Working… 12 s"), and what
  a reasoning model thinks before it answers shows in italics below it, so a model that
  thinks for a minute does not look stuck. The seconds stop while an Allow row waits,
  so that it holds still to be clicked.
- With KRunner closed (a click elsewhere closes it), a notification says, after a few
  seconds, that the agent works on the request, and the same notification then says what
  it answered or why it stopped. Its Show, or a click on it, opens the conversation.
- Every action the agent wants to take that its permissions put on `ask` (changing a
  file, running a command, reaching a server) shows as its own match, with the whole
  command as it will run, and three actions: Allow once, Always, Deny. Nothing runs before you choose; Enter alone on
  such a match answers nothing. While KRunner is closed, a request for permission
  comes as a notification, whose Show opens the conversation.
- Copy puts the answer on the clipboard; Open in terminal continues the same session in
  opencode's own interface, for longer work such as code.
- `ai` alone lists the recent conversations to continue.
- Plasma's start menu searches with KRunner's runners too, but shows neither a long
  answer nor a match's actions. There a search ends with "Ask opencode: …" as in
  KRunner; Enter sends it and opens the conversation in KRunner, and `opencode ›` opens
  it there too. kortex tells the start menu (plasmashell) from KRunner by who calls it
  over D-Bus. Nothing reaches the agent before Enter.
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
  on screen. It also follows `GET /event` for the requests alone, since opencode 1.19
  cannot list a web fetch that has no timeout. opencode reads its own configuration, so
  the model, the provider and the permissions are the user's (patina writes them at
  install time). `KORTEX_OPENCODE_URL` (and `KORTEX_OPENCODE_PASSWORD`) point kortex
  at a server that runs already instead.
- **Answers in a palette that pulls.** KRunner asks for matches; it is not told about
  new ones. While the conversation is on screen and changes, kortex runs the query
  again through KRunner's own D-Bus method (`org.kde.krunner /App querySingleRunner`)
  with the marker `opencode ›`, so KRunner asks again and gets the news; the first time
  it does for a request, kortex logs "KRunner asked again" (`journalctl --user -b |
  grep kortex`). KRunner asks only
  for a query that differs from the one it shows, so each refresh puts an invisible
  word joiner (U+2060) after the marker or takes it away again. The marker and that
  character are all kortex adds to the query line, and a follow-up typed after it is
  kept.
- **Hermes Agent as the agent.** With `hermes` in `~/.config/kortex/agent`, kortex
  starts `hermes gateway run` with its API server on 127.0.0.1 (`API_SERVER_KEY`, the
  same generated password, `API_SERVER_PORT`, a free port) and stops it with itself.
  `POST /api/sessions` makes a conversation, `POST /v1/runs` sends the request, `GET
  /api/sessions/:id/messages` and `GET /v1/runs/:id` give the answer and whether it is
  done, and each run's event stream (`GET /v1/runs/:id/events`) brings its approval
  requests, answered with `POST /v1/runs/:id/approval` (`once`, `always`, `deny`).
  Hermes asks first only before a command it counts as dangerous (deleting a folder,
  formatting a disk, piping a download into a shell, root through `sudo -A`), not
  before every change as opencode does: it edits files, deletes a single file, stops a
  process or installs an app from Flathub without asking. With `approvals.mode: manual`
  (patina sets it) no second model decides which commands to let through. A request
  left unanswered is denied after Hermes' timeout (five minutes). Hermes' own extras, messaging and its scheduler, run in that gateway only
  when you set them up (`hermes setup`). Open in terminal runs `hermes --resume`.
- **Services.** opencode reads its MCP servers (`mcp` in `opencode.json`) when its
  server starts, not again. When the file changes while kortex's server runs, kortex
  adds the servers that are new and enabled through `POST /mcp`, their `{file:...}` and
  `{env:...}` filled in as opencode fills them in its file (a token file), before the next
  request and while a conversation is on screen, so a service connected
  mid-conversation is there for the agent's next step. A server changed or removed
  takes effect at the server's next start, after a quiet quarter of an hour. Hermes
  reads its own (`mcp_servers` in `~/.hermes/config.yaml`) when its gateway starts, and
  keeps a conversation's tools as they were when it began: when the file changed,
  kortex stops the gateway it started once no run is busy, and the next request starts
  it again, so the service is there from the next conversation on.
- **Open in terminal** runs the terminal Plasma names (`TerminalApplication` in
  `kdeglobals`, else konsole) with `opencode attach <server> --session <id>`, the
  password in the environment, not on the command line, so nothing is lost when a
  request outgrows the palette.
- **Tests.** `python3 -m unittest discover -s tests` checks what kortex shows and does
  with opencode faked; `python3 tests/e2e.py` runs it as KRunner would: a private
  session bus that starts kortex by D-Bus activation, a real `opencode serve`, and a
  test model on localhost that asks to run a command. It checks that nothing runs
  before Allow once, and that it runs after; then it connects an MCP server
  (`tests/mock_mcp.py`) in the agent's configuration while kortex runs and checks that
  the model gets its tool on the next request, with KRunner closed: a stand-in for the
  notification server gets the notification of the answer, and its Show reopens the
  conversation. `python3 tests/e2e.py hermes` does the
  same against a real Hermes gateway, with a folder to remove, since Hermes asks first
  only before what it counts as dangerous. CI runs all three.

## Rules

- Free software: GPL-3.0-or-later, like patina.
- Local first. kortex itself sends nothing anywhere; where the request goes is
  opencode's configuration. With Ollama, requests and answers stay on the machine, but
  two things around them do not: opencode fetches its list of models from
  `models.opencode.ai` whenever its server starts (`OPENCODE_DISABLE_MODELS_FETCH=1`
  stops that), and on its first prompt it fetches the provider's package from npm.
  KRunner also keeps its last queries, requests to the agent included, in
  `~/.local/share/krunnerstaterc`.
- No action without a yes. kortex never answers a permission request on its own, and
  "Always" is opencode's own rule, which the Allow row spells out (`Always allows: sh *`
  lets any script through): it holds for every conversation until opencode's server
  stops, after a quiet quarter of an hour, and is not written into its configuration.
  A request opencode cannot list (1.19 fails the whole list while a web fetch without
  a timeout waits) comes from its event stream and is answered like any other.
- Root through a dialog. The agent has no terminal, so `sudo` cannot ask for a
  password there. patina sets the session's `SUDO_ASKPASS` to a password dialog in the
  theme's colours and tells the agent to run `sudo -A`; on another desktop, point
  `SUDO_ASKPASS` at a dialog of your own (`ksshaskpass`, for one) for the same.
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
  6.3 (Debian 13) and the current Plasma; the journal's "KRunner asked again" says that
  KRunner followed.
- **A name of its own.** The conversation is called by the agent's name (`opencode ›`),
  from one place (the agent's `title`), so that an assistant named by the user can
  take it later.
- **Other agents.** opencode and Hermes are the two backends, each a small adapter
  behind the same calls: send, follow, answer a permission. A third (Goose, for
  example) would be another.

## How it reaches users

1. A release here: a tarball with the program, the KRunner desktop file
   (`~/.local/share/krunner/dbusplugins/kortex.desktop`) and the D-Bus service file
   (`~/.local/share/dbus-1/services/`), installed per user, no root.
2. The KDE Store, where Plasma's "Get New Stuff" finds KRunner runners.
3. A Debian package, so that patina can install it from Debian's archive (patina takes
   software only from Debian, Flathub or an upstream installer, and binds the palette
   once kortex comes from one of them).

## First release, done when

- A request in KRunner on Debian 13's Plasma answers from a local model through
  opencode and Ollama, and from a cloud model with the same configuration switched.
- A request that needs a change shows the Allow / Always / Deny matches, and nothing
  runs before one is chosen.
- Open in terminal continues the session.
- Installing and removing it per user leaves nothing behind but the password file.
