# Using kortex

What kortex shows, where, and what each row does. The agent behind it is
[opencode](https://opencode.ai) or [Hermes Agent](https://github.com/NousResearch/hermes-agent);
kortex adds no prompt of its own beyond a conversation's title, so the agent's
instructions (patina's `defaults/agent.md`, or your own `AGENTS.md`) decide how it
answers.

```text
Meta+A       why is my wifi slow?
             ◉ Ask opencode: why is my wifi slow?               Enter
             ─────────────────────────────────────────────────────────
opencode ›   ◉ Your wifi runs on 2.4 GHz at 72 Mbit/s; the 5 GHz
               network "home-5G" is in range ...                 Copy · Open in terminal
             ⚠ Allow bash: nmcli connection up home-5G          Allow once · Always · Deny
```

## Asking

- **Meta+A** (patina's `ask` key) opens KRunner on kortex alone: whatever you type goes
  to the agent. Enter on "Ask opencode: …" sends it.
- **Any search** of three letters or more, in KRunner (Meta+Space) or in Plasma's start
  menu, ends with "Ask opencode: …" (or "Ask Hermes: …", by the agent kortex talks
  to), below what Plasma finds. A search that reads as a question or a request comes
  first instead: it ends with `?`, or it has three words or more and starts with a
  question word or a request (how, why, what, can, fix, install, wie, warum, was,
  kann, hilf, installiere and the like). With a conversation going, a second row
  continues it.
- **`ai` and a request** asks at once, as in the versions before 0.4.0; `ai` alone
  lists the recent conversations to continue.
- Nothing reaches the agent before Enter. KRunner keeps its last queries, requests to
  the agent included, in `~/.local/share/krunnerstaterc`.

## The conversation

Enter sends the request and opens KRunner on the conversation, `opencode ›` (or
`Hermes ›`; `ai ›` works too). It shows:

- **The answer as it grows**, one multi-line row, with a line for each tool the agent
  used (`› bash df -h /`). Copy puts the answer on the clipboard; Open in terminal
  goes on with the same conversation in the agent's own interface (`opencode attach`,
  `hermes --resume`), for longer work such as code.
- **What the agent thinks**, in italics below the answer, while a reasoning model
  thinks before it answers, and **how long it has been at it** ("Working… 12 s"), so
  a model that thinks for a minute does not look stuck. The seconds stop while a row
  asks, so that it holds still to be clicked.
- **A row for each action the agent asks to take** (a command, a file change, a web
  request its permissions put on `ask`), with the whole command as it will run and
  what Always would let through (`Always allows: sh *` lets any script through).
  Allow once, Always or Deny answer it; Enter alone answers nothing, and nothing runs
  before you choose. opencode asks before every change (patina's permissions); Hermes
  only before what it counts as dangerous.
- **Why it stopped**, when the agent or its provider failed, with Open in terminal.

Typing after the marker continues the conversation (`opencode › and in dark mode?`).
Another search typed over it leaves the conversation alone.

## When KRunner is closed

A click elsewhere closes KRunner, and the agent keeps working. A few seconds later a
notification says that it works on your request; the same notification then says what
it answered (its last words, which after a task say how it went) or why it stopped. A
row that asks comes as a notification of its own. Show, or a click on the notification,
opens the conversation in KRunner.

The start menu shows neither a long answer nor a row's actions, so a request sent from
there opens the conversation in KRunner.

## A key for the agent

patina puts the palette on Meta+A and the agent's own terminal on Meta+Shift+A. On
another desktop, bind a key (System Settings, Shortcuts, Add New, Command) to:

```sh
dbus-send --session --type=method_call --dest=org.kde.krunner /App \
  org.kde.krunner.App.querySingleRunner string:kortex string:
```

## opencode or Hermes

kortex talks to opencode. For Hermes, write `hermes` into `~/.config/kortex/agent`
(patina's `patina ai on hermes` does), or set `KORTEX_AGENT=hermes`. A kortex that runs
stops when the file names the other agent, and D-Bus starts one for it.

`KORTEX_OPENCODE_URL` (with `KORTEX_OPENCODE_PASSWORD`), or `KORTEX_HERMES_URL` (with
`KORTEX_HERMES_KEY`), points kortex at a server that runs already, which it then never
stops. `KORTEX_OPENCODE` and `KORTEX_HERMES` name the programs when they are not on
`PATH` or where their installers put them.

## When it seems stuck

kortex writes each request's way to the journal:

```sh
journalctl --user -b | grep kortex
```

- `asking opencode`: the request left kortex.
- `starting … opencode serve …`: the first request after a quiet quarter of an hour
  starts the agent's server; Hermes' gateway can take a minute.
- `opencode asks first: bash`: a row waits for your answer.
- `KRunner asked again: the palette follows the conversation`: KRunner shows what
  kortex sends it. Without this line the palette stands still while the agent works;
  the notification still comes.
- `opencode answered after 12 s`, or `opencode stopped after …` with the reason.

The agent's own output is in `~/.local/state/kortex/opencode.log` (or `hermes.log`).
