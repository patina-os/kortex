# Changelog

What changed in each release of kortex. The format follows
[Keep a Changelog 1.1.0](https://keepachangelog.com/en/1.1.0/), and the project uses
[Semantic Versioning](https://semver.org/spec/v2.0.0.html). patina pins a release in its
`manifest/upstream.yaml`; the next `patina update` installs it where an AI agent is on.

## [0.8.0] - 2026-11-14

### Added

- Edit the agent's instructions, an action on the hint row: with patina it runs
  `patina ai prompt` in a terminal, which keeps your own instructions beside patina's
  for opencode and Hermes alike; without patina it opens the agent's own file
  (opencode's `~/.config/opencode/AGENTS.md`, Hermes' `~/.hermes/SOUL.md`). The
  instructions could only be found by knowing where they were.

### Fixed

- Always is offered only for a command that looks and changes nothing whatever it is
  given (`du`, `ls`, `wc`, `apt-cache`, `systemctl status` and the like), with
  opencode and with Hermes. opencode's rule for Always is the command's start and `*`:
  one Always on a row that counted files (`find *, wc *`) let `find -delete` and
  `find -exec sudo …` run unasked later, in a new conversation, and others let
  `flatpak install`, `crontab -` and `cat` on any file through. kortex cannot narrow
  opencode's rule, so such a row offers Allow once and Deny.
- The palette no longer lags for seconds while the agent writes a long answer: finding
  the last sentence for the row that says how long took time that grew with the square
  of the answer's length (3 s for 14,000 characters of code), on the thread that
  answers KRunner.
- A reply without a word or a step (a model that thinks and then ends its turn) says
  "The agent stopped without an answer", with Enter to ask the same again; the palette
  stayed empty, and the notification said "kortex answered: Done.".
- A row that asks for a change to a file shows its path and the changed lines under
  the agent's words, and one for a service's tool what the tool is given: "I'll set
  both Platform tickets to Done." named no ticket, and asked for only one of them.
- An agent that ends its turn after a step without a word, or on words that announce
  more ("I'll clean what's safe now:"), no longer reads as done: the palette and the
  notification say that it stopped without saying it is done, and to say "go on" if it
  is not. They showed its plan ("I'll add a weekly cron job") as the answer, and there
  was no cron job.
- Every command a row asks for shows under the agent's words, not only one that
  deletes or runs as root: the words may describe a later step ("I'll install it from
  Debian's archive" over an `apt-cache show`). A command that reaches outside the home
  folder says so, and where ("access outside your home folder (/tmp/*)"), and such a
  row offers no Always, whose rule would hold for any step in that folder.
- A service's tool that asks offers no Always: its rule was any call of the tool, in
  every conversation ("any cloud_delete_note"), so one click let every later deletion
  through, one asked for by text inside a ticket too. A tool that only reads does not
  ask (patina's rules), so one that asks changes something. Neither does opencode's
  grep tool, which prints what it finds in a file: one Always would let it read token
  files and private keys in every later conversation.
- A request left for a new one while the agent works on it is followed on: a
  notification says when it asks first and what the agent answered, and its Show opens
  that conversation. Only the last request was followed, so a long essay asked before
  "what is 3+3?" ended without a word.
- A provider that cannot be reached, or refuses the key, is named: "The agent could not
  reach its provider (custom). Check its address and key: patina ai on patina, else the
  agent's own settings.", with the error under it; it ended in a raw "Cannot connect to
  API: ECONNREFUSED" after a minute of retries. The row that retries keeps counting the
  seconds and says which try it is, and a closed palette is told after ten seconds of
  retries, not only of a limit that lasts a minute or more.
- An agent that does not start says why, in a row that shows all of it: "opencode did
  not start: Config file at ~/.config/opencode/opencode.json is not valid JSON(C):
  ValueExpected at line 1, column 2". The row named the path of the program and of
  its log.
- Meta+Shift+A with the palette closed says in a notification when the panel does not
  open; it failed without a word, and its error showed later in the conversation as
  "Not sent". A failed answer to a row, Stop, Copy or panel says what did not happen
  ("Not answered", "Not stopped" …) and goes once the agent answers again; a row whose
  answer did not reach the agent comes back, to answer again.
- Meta+Shift+A, or Show what the agent does, on an open panel brings it to the front
  (docked again, if it was moved) in place of a second panel; on another conversation
  the panel shows the current one in its place. Each press opened one more, and a
  quick second press left the first undocked.
- An answer cut off because the agent's server stopped mid-answer says so ("Cut off:
  the agent stopped before it finished …") above what it wrote; after the server
  started again, the half essay read as the whole answer.
- A long answer fits a small screen: the palette shows at most twelve lines of it, as
  the row wraps them, and a row below it, "Show the whole answer", opens the panel on
  it. On a 768-px screen a long answer was a row taller than the screen, with a
  scrollbar, and the note that the panel had the rest pointed at an unlabelled icon.

## [0.7.2] - 2026-11-10

### Fixed

- The panel shows in the taskbar as kortex, with kortex's icon, apart from the
  terminal's other windows: in kitty it was one more kitty window under kitty's icon.
  kitty's window takes kortex's app id, which names a hidden application entry
  `install.sh` puts in place; another terminal keeps its own.

## [0.7.1] - 2026-11-10

### Fixed

- With the palette closed, the notification that a step asks first says what the agent
  said of it, as the palette's row does; it named the command.
- Allow once right before the palette was closed no longer brings a notification that
  the same step asks first: a fetch begun before the answer listed it once more.
- The row that says how long no longer cuts a sentence at a stop inside a number or a
  name ("8.0G", "df.py") or at a colon.
- A model that gives two tool calls one id no longer has the second step's row show
  the first step's words.

### Changed

- The README's demo is a replay of kortex 0.7.0: rows in the agent's words, a step
  asked while the palette was closed, Meta+A on the waiting row, and the panel.

## [0.7.0] - 2026-11-10

### Added

- The panel: Show what the agent does (in place of Open in terminal) docks the agent's
  own interface on the conversation at the screen's right edge, full height, a fifth
  of the width (480 to 720 px), above other windows, so the user works on beside it.
  A KWin script kortex loads over D-Bus places it. `Panel` on kortex's own interface
  does the same for a key (patina's Meta+Shift+A), the agent's interface alone when
  there is no conversation.
- `Open` on kortex's own D-Bus interface (`io.github.patina_os.kortex`), for Meta+A:
  KRunner on kortex alone, on the conversation while a request runs or a row waits for
  an answer. KRunner calls no runner for an empty field, so Meta+A showed an empty
  palette while the agent waited for a yes.

- `tests/agent/`: the driver and the stand-in services of the agent test of November
  2026, to test an agent by hand with a real model.

### Changed

- The conversation reads like a chat: no rows for the agent's steps while it works and
  none folded below the answer (the panel shows them; the answer says how many). The
  row that says how long says what the agent does during a step ("running a command",
  "using linear"). A row that asks shows what the agent said right before that step,
  in place of the command; a command that deletes, runs as root or the like (`sudo`,
  `rm`, a shell, `kill`, `apt` …) shows under the words, and a step the agent did not
  explain shows as before.
- The row that says how long shows the start of the last sentence the agent finished
  writing or thinking, which holds still until the next; it showed the end of what
  streamed in, half sentences that changed with every word.
- A request to the agent's server that fails says why in words ("opencode is not
  running", "did not answer within 10 s", "refused kortex's password (HTTP 401)"),
  not urllib's (`GET /session/status: <urlopen error [Errno 111] …>`); the journal
  keeps the details.

### Fixed

- No step row with the tool's bare name while the model is still writing the call.
- kortex no longer asks the agent for news every 0.7 s for a quarter of an hour after
  its last use: with KRunner on something else and the agent done, it asks nothing
  until the next look at the conversation.

## [0.6.0] - 2026-11-10

### Added

- A question of the agent's is answered in the palette. opencode's `question` tool
  ("Which team should the ticket go to?") left the palette at "Working" until the
  user gave up, and the conversation was lost. It is a row now, with a row for each
  answer it offers: Enter on one answers, and so does typing your own after
  `kortex ›`. With KRunner closed a notification asks.
- What a conversation the agent starts for a part of the work (its `task` tool) asks
  is shown in the conversation that started it; it was shown nowhere, and both waited.
- Stop, on the row that says how long, ends what the agent does (opencode).
- A provider that puts a request off for more than a minute is told of once in a
  notification that stays, with the time of its next try.

### Fixed

- With Hermes too, Always is not offered on a row of `sudo`, `rm`, a shell and the
  like. kortex decided from the rule opencode names, which Hermes does not, so its
  rows kept Always; Hermes keeps one for good and for the whole kind of command, and
  one Always on `sudo -A apt-get clean` let every later sudo run unasked, in a new
  conversation too. There the command itself decides now.
- A question Hermes gave up on leaves the palette. After five minutes without an
  answer Hermes goes on without the command; the row stayed, "Waiting for your
  answer", while the agent worked on, and Allow on it was "HTTP Error 409: Conflict".
  Hermes says nowhere that it gave up (its run still reads as waiting), so the row
  goes once the model writes again, and an answer that comes too late is no error.
- The same request asked of Hermes a second time is answered. Hermes takes a
  conversation's title once, and kortex names a conversation after the request: the
  second time the palette said "POST /api/sessions: HTTP Error 400: Bad Request ·
  Not sent", for good. The second conversation gets the time behind its title.
- A tool of a service that a hook of Hermes' sends to its approval (patina's gate)
  reads "Allow tracker: update_issue {…}", not Hermes' name for the rule.
- Always is not offered where its rule lets far more through than what asks: a
  change to any file, any bash command, or commands that start with `sudo`, `rm`, a
  shell, `python3`, `curl` and the like. One Always on a row that measured a folder
  (`du …; sudo -A du …`, "Always allows: du *, sudo *") let every later `sudo` run
  unasked, in every conversation.
- A row that asks shows what it asks for: the whole path and the changed lines of a
  file (it read "Allow edit: home/u/.bashrc"), what a service's tool is given (it
  read "Allow tracker_create_issue: *"), "access outside your home folder" for
  opencode's `external_directory`, and what the agent said it would do, which the
  palette hid while it asked.
- The answer is what the agent said after its last step, not every sentence of the
  turn ("let me check…" stood in front of it), and a long one keeps its start: it
  kept the last 1,500 characters, so a list began at its sixth entry.
- An agent whose server exits while it works ends the request ("kortex stopped");
  the palette said "working" until kortex stopped itself a quarter of an hour later.

- A request the provider puts off says why and until when. With a free limit used up
  (OpenCode Zen's, for Big Pickle), the row read "Retrying: Free usage exceeded,
  subscribe to Go" for hours, with no end in sight; it now takes opencode's own title
  for the reason and the time of the next try: "Retrying: Free limit reached · next
  try 00:39".

- A step you denied no longer ends in "Done.". opencode ends the turn on a Deny, and
  the palette showed "Done.", or what the agent had said before, as if it had gone
  on; it now says "Stopped: you denied the last step, so it did not run. Say what to
  do instead.", in the palette and in the notification, and the step reads
  "(denied)" in the terminal's place of "(failed)".
- A follow-up that repeats the request before it ("try again") is no longer taken as
  answered by the answer before it: what kortex knew of the conversation when the
  follow-up was sent is dropped, so no notification says "kortex answered" with the
  old answer.
- Opening another conversation from `ai`'s list while a request runs ends the
  following of that request: the conversation opened got a "kortex is working on
  it" for a request that was never made in it.

### Changed

- The Markdown of an answer is read as far as a row can show it: bold, a heading as
  a bold line, lists with bullets, a table's rows with · between the cells, code
  without its backticks. It showed raw.
- Subtexts are short enough to stay whole: KRunner cuts a long one in its middle
  ("A new conve…tte · Enter", "op…al"). "New conversation · Enter", "Search, ask,
  fix", "Enter copies", "Asks first".
- "install photocraft" reads as a request with two words (so does "installiere"):
  KRunner's own first row for it runs the `install` command. please, bitte, können,
  update and umlaut-less forms start a request too; do, set, show, make and change
  no longer do, since "do not disturb" and "show hidden files" are settings.
- A step of a service (an MCP server's tool) says what it was given: "tracker_update_issue
  id=DES-103, priority=high" where the row was the tool's bare name, so it shows
  which issue or note was touched.
- `kortex >` shows the conversation as `kortex ›` does: few keyboards have a key
  for ›, which the hint names.
- While a row asks, the row below it says how to answer from the keyboard: "Waiting
  for your answer · Shift+Enter allows once". KRunner runs a row's first action
  with Shift+Enter and says so only in a tooltip; Enter answers nothing, as before.
- A row that asks says how far Always reaches: "Always allows, in every
  conversation: du *". opencode keeps the rule for every conversation until its
  server stops, which the row did not say.

## [0.5.1] - 2026-11-09

### Fixed

- Typing in the palette while it shows the conversation keeps every key. A refresh,
  once a second while the agent works, put back the query line as KRunner had last
  asked for it, so the key typed since was lost (a space typed at the end always was,
  since KRunner does not ask again for it), and it emptied the rows for a moment, so
  Enter could find nothing to run. kortex now refreshes only while the line holds the
  bare `kortex ›` or `ai`, drops a refresh when a key comes first, and hears the first
  letter typed over the conversation. A follow-up's rows show the news with each key.
- The palette opens on `kortex › `, with the space after the marker, so a follow-up is
  typed straight away.

## [0.5.0] - 2026-11-08

### Changed

- While the agent works, the conversation is rows of one line each: one that says how
  long it has been at it and what it wrote or thought last ("Working · 12 s · …",
  "Thinking · 12 s · …"), and one per step it took, the last four, older ones folded
  into one row. Once it is done, its answer takes the first row, on as many lines as
  it needs, and its steps fold into one row ("3 steps: bash ×2, read"). KRunner gives
  a one-line row a fixed height, so its window holds still while the agent works; it
  grew and shrank with each refresh, as the answer gained a line and the thoughts
  below it came and went. Enter on a step opens the conversation in the terminal.
- The palette calls itself kortex, whichever agent answers: `kortex ›` shows the
  conversation, the rows ask "Ask kortex: …", and the notifications say "kortex is
  working on it" and "kortex answered". The agent is named once, as a detail: in the
  hint of `ai` and the row that says how long. `opencode ›`, as 0.4 called the
  conversation, and `ai ›` still show it.
- Copy puts what the agent said on the clipboard, without the steps it took.

### Fixed

- The seconds count where you see them. They were in the answer row's subtext, which
  KRunner hides when the text takes the row's whole width, so they never showed.

## [0.4.1] - 2026-11-07

### Changed

- The icon is patina's ▲ inside two contour lines, a drawn one and a dotted one, in
  aurora's violets, made to read at the 16 and 22 px of KRunner's rows; it was a plain
  ▲. The README has a logo, the icon beside kortex in the block letters of patina's
  wordmark.
- The notification of an answer shows the agent's last words, which after a task say
  how it went, in place of its first 300 characters and the tools it used.
- The README is short; what it held moved to `docs/` (usage, how it works, principles,
  development, roadmap) and here. Its animation is recorded again with this release.

### Fixed

- The answer, and what a reasoning model thinks, grow on screen again. opencode
  (1.19.5) keeps a text or reasoning part empty in its message list until the part is
  done and sends the words on its event stream meanwhile; kortex now fills them in
  from there. Before, the palette stood at "Thinking…" until each part was done, and
  a model's thoughts never showed.

## [0.4.0] - 2026-11-07

### Added

- Any search in KRunner ends with "Ask opencode: …", as the start menu's did, and a
  search that reads as a question (a `?` at its end, or three words or more that start
  with how, why, wie, warum and the like) puts it first: no `ai` to type.
- While the agent works, the palette counts the seconds and shows what a reasoning model
  thinks, in italics below the answer.
- With KRunner closed, a notification says that the agent works on the request and then
  what it answered or why it stopped; its Show opens the conversation. Permission
  requests get Show too.
- The journal says when a request leaves, what the agent asks first, how the request
  ended and whether KRunner followed the conversation.

### Changed

- The conversation is called by the agent's name, `opencode ›` (`Hermes ›`), in place of
  `ai ›`, which still works.

### Fixed

- Another search typed over the conversation is no longer pulled back to it by the next
  refresh.

## [0.3.4] - 2026-11-04

### Changed

- The rows that ask call the agent by its name: "Ask opencode: …" or "Ask Hermes: …",
  in place of "Ask the agent: …".

## [0.3.3] - 2026-11-02

### Fixed

- `AI ›`, and a tab after `ai`, work as `ai ›` and a space do.
- An answer to a request the agent no longer waits for (answered in the terminal, say)
  is no error.

## [0.3.2] - 2026-11-02

### Changed

- KRunner never waits for the agent, which can take a minute to start: kortex asks it
  from a thread of its own and shows "Waiting for the agent…" meanwhile.

## [0.3.1] - 2026-11-01

### Changed

- The Allow row shows the whole command as it will run, and what Always would let
  through.

### Fixed

- kortex stops when the user picks the other agent, and D-Bus starts one for it.
- The agent's server stops when the session bus goes away.
- KRunner closed right after a run from the start menu stays closed.
- Token files are filled in for services added to a running opencode.
- The runner's metadata carries the program's version; the D-Bus service file is written
  with its mode set.

## [0.3.0] - 2026-10-30

### Added

- Any search in Plasma's start menu ends with "Ask the agent: …", which opens the
  conversation in KRunner.

## [0.2.0] - 2026-10-30

### Added

- Hermes Agent is the second agent kortex talks to, next to opencode.

## [0.1.3] - 2026-10-30

### Added

- A service connected while kortex runs (an MCP server added to the agent's
  configuration, as `patina ai connect` does) reaches opencode's running agent at once,
  and Hermes' next conversation.

## [0.1.2] - 2026-10-29

### Changed

- The D-Bus name is `io.github.patina_os.kortex`.
- The first `ai` after a pause takes about 3 s instead of 12.

### Fixed

- The answer refreshes in a real KRunner, which asks again only for a query it has not
  shown.
- Requests opencode cannot list come from its event stream.

## [0.1.1] - 2026-10-28

### Changed

- The icon is a solid green summit; kortex's addresses moved to the patina-os account.

## [0.1.0] - 2026-10-27

### Added

- The first kortex: the AI agent as a KRunner runner, `ai` and a request, the answer in
  the palette, Allow once, Always and Deny for every action the agent asks to take.

[0.8.0]: https://github.com/patina-os/kortex/compare/v0.7.2...v0.8.0
[0.7.2]: https://github.com/patina-os/kortex/compare/v0.7.1...v0.7.2
[0.7.1]: https://github.com/patina-os/kortex/compare/v0.7.0...v0.7.1
[0.7.0]: https://github.com/patina-os/kortex/compare/v0.6.0...v0.7.0
[0.6.0]: https://github.com/patina-os/kortex/compare/v0.5.1...v0.6.0
[0.5.1]: https://github.com/patina-os/kortex/compare/v0.5.0...v0.5.1
[0.5.0]: https://github.com/patina-os/kortex/compare/v0.4.1...v0.5.0
[0.4.1]: https://github.com/patina-os/kortex/compare/v0.4.0...v0.4.1
[0.4.0]: https://github.com/patina-os/kortex/compare/v0.3.4...v0.4.0
[0.3.4]: https://github.com/patina-os/kortex/compare/v0.3.3...v0.3.4
[0.3.3]: https://github.com/patina-os/kortex/compare/v0.3.2...v0.3.3
[0.3.2]: https://github.com/patina-os/kortex/compare/v0.3.1...v0.3.2
[0.3.1]: https://github.com/patina-os/kortex/compare/v0.3.0...v0.3.1
[0.3.0]: https://github.com/patina-os/kortex/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/patina-os/kortex/compare/v0.1.3...v0.2.0
[0.1.3]: https://github.com/patina-os/kortex/compare/v0.1.2...v0.1.3
[0.1.2]: https://github.com/patina-os/kortex/compare/v0.1.1...v0.1.2
[0.1.1]: https://github.com/patina-os/kortex/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/patina-os/kortex/releases/tag/v0.1.0
