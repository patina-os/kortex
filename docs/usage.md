# Using kortex

What kortex shows, where, and what each row does. The agent behind it is
[opencode](https://opencode.ai) or [Hermes Agent](https://github.com/NousResearch/hermes-agent);
kortex adds no prompt of its own beyond a conversation's title, so the agent's
instructions (patina's `defaults/agent.md`, or your own `AGENTS.md`) decide how it
answers. The palette calls itself kortex whichever agent answers; the hint row of `ai`
and the row that says how long name the agent.

```text
Meta+A       why is my wifi slow?
             ◉ Ask kortex: why is my wifi slow?                 Enter
             ─────────────────────────────────────────────────────────
kortex ›     ◉ Working · 12 s · running a command                Copy · Show what the agent does · Stop
             ⚠ Allow: I'll switch you to the 5 GHz network.     Allow once · Always · Deny
```

## Asking

- **Meta+A** (patina's `ask` key) opens KRunner on kortex alone: whatever you type goes
  to the agent. Enter on "Ask kortex: …" sends it. While a request runs or a row waits
  for your answer, it opens on the conversation instead.
- **Any search** of three letters or more, in KRunner (Meta+Space) or in Plasma's start
  menu, ends with "Ask kortex: …", below what Plasma finds. A search that reads as a
  question or a request comes first instead: it ends with `?`, or it has three words
  or more and starts with a question word or a request (how, why, what, can, please,
  fix, install, wie, warum, was, kann, bitte, hilf, installiere and the like). With
  "install" two words are enough ("install photocraft"), since KRunner's own first
  row for them runs the `install` command. Words that start the names of settings
  as often as a request (do, set, show, make, change) are not among them. With a
  conversation going, a second row continues it.
- **`ai` and a request** asks at once, as in the versions before 0.4.0; `ai` alone
  lists the recent conversations to continue.
- Nothing reaches the agent before Enter. KRunner keeps its last queries, requests to
  the agent included, in `~/.local/share/krunnerstaterc`.

## The conversation

Enter sends the request and opens KRunner on the conversation, `kortex ›` (`kortex >` and
`ai ›` work too). It reads like a chat with the agent: its words, not its commands, which
are in the panel (below). While the agent works, the row that says so is one line, so
KRunner's window holds still instead of growing and shrinking with each word. It shows:

- **How long the agent has been at it and what it wrote or does last**, on one row:
  "Working · 12 s · The 5 GHz network is in range.", or "Thinking · 12 s · …" while a
  reasoning model thinks (the start of the last sentence it finished, which holds still
  until it finishes the next) before it answers, or what it does during a step
  ("running a command", "reading a file", "using linear"), so a model that thinks for a
  minute does not look stuck. While a row asks, it says "Waiting for your answer · Shift+Enter
  allows once" and holds still, so that the row can be clicked.
- **The answer**, once the agent is done, in place of the row that said how long: what
  it said after its last step (what it said between the steps is how it got there),
  with how many steps it took beside it ("Enter copies · 3 steps"). The Markdown a
  model writes is read as far as a row can show it: bold, lists, a table's rows. An
  answer longer than 1,500 characters shows its start and says that the panel has the
  rest. Enter or Copy puts the answer on the clipboard.
- **A row for each action the agent asks to take** (a command, a file change, a web
  request, a service's tool its permissions put on `ask`), in what the agent said right
  before that step ("Allow: I'll add the alias to your .bashrc, after a copy of it as
  .bashrc.bak."). A command that deletes, runs as root or the like (`sudo`, `rm`, a
  shell, `kill`, `apt` …) shows, whole, under the words: words can be wrong, and in a
  test an agent called a folder that held a thesis "one stale cache file". A step the
  agent did not explain shows as before: the whole command as it will run, the path
  and the changed lines of a file, or what a service's tool is given. And with
  what Always would let through (`Always allows, in every conversation: sh *` lets any
  script through, in this conversation and the next ones, until the agent's server
  stops). Allow once, Always or Deny answer it, with the mouse; from the keyboard,
  Shift+Enter is Allow once (KRunner runs a row's first action with it). Enter alone
  answers nothing, and nothing runs before you choose. Always is not offered where
  its rule would let far more through than what asks: a change to any file, or
  commands that start with `sudo`, `rm`, a shell, `python3`, `curl` and the like;
  one Always on such a row let every later `sudo` run unasked. With Hermes, which
  names no rule, the command that asks decides, and a question it gives up on after
  five minutes (`approvals.timeout`) leaves the palette. opencode asks before
  every change (patina's permissions); Hermes only before what it counts as
  dangerous.
- **What the agent asks you** (opencode's `question` tool: "Which team should the
  ticket go to?"), with a row for each answer it offers. Enter on one answers; so
  does typing your own after `kortex ›`. What a conversation the agent started for a
  part of the work asks is shown here too.
- **Stop**, on the row that says how long, ends what the agent does (opencode).
- **Why it stopped**, when the agent or its provider failed, with Show what the agent
  does.

## The panel

Show what the agent does, on the row that says how long or on the answer, opens the
agent's own interface on the conversation (`opencode attach`, `hermes --resume`) in the
terminal Plasma names, docked at the screen's right edge: as high as the screen's work
area, a fifth of its width but at least 480 and at most 720 pixels, above your other
windows. Every step is there, whole, and the conversation goes on there too, for
longer work such as code; close it when you have seen enough. patina puts it on
Meta+Shift+A, which opens the agent's interface alone when there is no conversation
yet. The docking is a small KWin script kortex loads over D-Bus for the moment the
window takes to appear (Plasma lets no program place its own window on Wayland);
without KWin the terminal opens where it would.

Typing after the marker continues the conversation (`kortex › and in dark mode?`).
Another search typed over it leaves the conversation alone.

## When KRunner is closed

A click elsewhere closes KRunner, and the agent keeps working. A few seconds later a
notification says that it works on your request; the same notification then says what
it answered (its last words, which after a task say how it went) or why it stopped. A
row that asks, and a question of the agent's, come as notifications of their own, and
so does a provider that puts the request off for more than a minute (a free limit
used up), with the time of its next try. Show, or a click on the notification, opens
the conversation in KRunner.

The start menu shows neither a long answer nor a row's actions, so a request sent from
there opens the conversation in KRunner.

## A key for the agent

patina puts the palette on Meta+A and the panel on Meta+Shift+A. On another desktop,
bind a key (System Settings, Shortcuts, Add New, Command) to:

```sh
dbus-send --session --print-reply --dest=io.github.patina_os.kortex /runner \
  io.github.patina_os.kortex.Open
```

kortex's `Open` opens the palette on kortex alone, or on the conversation while a
request runs or waits for an answer; D-Bus starts kortex for it. Earlier releases' key
called KRunner itself (`org.kde.krunner /App querySingleRunner string:kortex string:`),
which shows an empty field whatever runs.

The panel, the same way: `io.github.patina_os.kortex.Panel` in place of `Open`.

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
- `kortex answered after 12 s`, or `kortex stopped after …` with the reason.

The agent's own output is in `~/.local/state/kortex/opencode.log` (or `hermes.log`).
