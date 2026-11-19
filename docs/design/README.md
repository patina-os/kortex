# Design studies

Explorations kept for the day a question comes back. Each is a self-contained HTML
page: open it in a browser.

## Where should the agent talk to you? (November 2026)

[`palette-redesign.html`](palette-redesign.html): today's palette and four other homes
for the conversation, each as a prototype to click through on one request ("why is my
wifi so slow?", two changes to allow, one answer), with what each solves, costs and
risks, a comparison and the decision. The prototypes are drawings in patina's aurora
colours, not kortex: nothing in them talks to an agent.

**The question.** kortex holds the whole conversation in KRunner's result list. KRunner
pulls and is never told, sizes its window to its rows, shows a row's actions as icons,
and closes on a click elsewhere. The test rounds of November 2026 (patina's
`docs/agent-test.md` and `docs/test-round.md`) added what users meet: a diagnosis that
asks ten to twenty times, words that were wrong over a command that was right, and an
icon that read as "share".

**The options.**

| | What it is | Why not now |
|---|---|---|
| A · Steady palette | Stay in KRunner: answers as rows, what a step would do in kortex's words, notifications that answer | chosen |
| B · Hand-off card | KRunner starts the request, a window of kortex's own holds the chat, a pill while away | a second toolkit (Qt Quick, layer-shell-qt) and a window kortex must place itself |
| C · In the bar | A panel widget beside the start button, a popup Plasma places, one approval per plan | a plasmoid and a local API to own; whether a plasmoid takes Meta+A's focus on Wayland is untested |
| D · Session | One conversation in three sizes (card, corner, docked panel), earlier turns shortened, an Ask me switch | needs B's or C's surface first |

**The decision.** A, built as kortex 0.9.0. It needs `kortex.py` only and no new
dependency, and it keeps what works: every search can end with "Ask kortex".

**Handing over.** The study drew three levels of asking (every step, per plan, only if
risky) and, for A, "Allow the rest of this task". The maintainer's doubt decided it:
one yes that covers steps not seen yet makes opencode more like Hermes, whose guard
matches a command's shape and not its effect. So Allow the rest of this task is in
0.9.0 but off unless the user turns it on, covers one request, never what deletes, runs
as root, installs or changes a service, and the answer lists what ran on it. One
approval per plan, where the yes covers only commands the user has seen, was not built
and is the better candidate if handing over proves wanted.

**Where the build differs from the page's drawing of A.** The row that asks comes
first and the status last, so that Shift+Enter still allows once. The agent's words are
one line, since only one-line rows keep KRunner's height. The answer keeps its twelve
lines. The window still changes height when a step asks and when the answer comes, and
rows that only filled space to keep five rows were left out.

**What would reopen it.**

- Users hold sessions of many turns, and the palette's one answer is not enough: D,
  on C's or B's surface.
- A KRunner release breaks the refresh kortex depends on (`querySingleRunner` with a
  word joiner), or its row layout: B or C, which need it only for the first row.
- Handing over is used by most: per-plan approval, then D's Ask me switch.
