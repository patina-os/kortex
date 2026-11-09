# Changelog

What changed in each release of kortex. The format follows
[Keep a Changelog 1.1.0](https://keepachangelog.com/en/1.1.0/), and the project uses
[Semantic Versioning](https://semver.org/spec/v2.0.0.html). patina pins a release in its
`manifest/upstream.yaml`; the next `patina update` installs it where an AI agent is on.

## [Unreleased]

### Fixed

- A request the provider puts off says why and until when. With a free limit used up
  (OpenCode Zen's, for Big Pickle), the row read "Retrying: Free usage exceeded,
  subscribe to Go" for hours, with no end in sight; it now takes opencode's own title
  for the reason and the time of the next try: "Retrying: Free limit reached · next
  try 00:39".

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
