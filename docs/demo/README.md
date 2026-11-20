# The demo

`kortex-demo.gif`, at the top of the README, is a replay of a real run, not a
screenshot: kortex answered over D-Bus exactly what the palette shows, the panel shows
the conversation as opencode kept it, and the palette, the notifications and the panel
are drawn by `page.html` in patina's aurora colours.

- `record.py` runs kortex the way `tests/e2e.py` does (a private session bus that
  starts it by D-Bus activation, stand-ins for KRunner, the notification server and
  KWin's scripting, a real `opencode serve`), with opencode set up as patina sets it
  up, and a scripted model, `demo_llm.py`, that answers slowly, thinks before each step
  as a reasoning model does, says in a sentence what each step does, as patina's
  `agent.md` asks, and uses tools like an agent. Meta+A is kortex's `Open`, as patina
  binds it: with nothing running it opens on `ai`, whose rows say how to ask. It asks
  how full the disk is (a read-only `df`, which runs without asking) and to install
  PhotoCraft: patina's artcraft bundle, then the ArtCraft Launcher opened. Each asks
  first, with what the step would do, the agent's words and the command (`patina` and
  the launcher are stand-ins that only print or do nothing). The first is answered
  with its row, Allow this once; then the palette is closed: a notification says the
  second step asks and takes the answer, its Allow once, and the next one brings the
  agent's answer, whose Show opens the conversation. At the end, Show every step
  under the answer starts the panel: the terminal is a stand-in that notes the
  command kortex gives it, KWin's stand-in notes the docking, and the panel's lines are
  read from opencode's own server. Every Match, Run, Teardown, notification and KWin
  call, and what kortex answered, goes into `timeline.json`.
- `capture.js` fills `timeline.json` into `page.html`, writes `replay.html` (open it in
  a browser to step through the run, with the D-Bus log beside it) and records its
  `#gif` mode into `kortex-demo.gif`. The answer's words play at the pace they came.

To make it again, for a new kortex or opencode:

```sh
KORTEX_OPENCODE=$(command -v opencode) python3 docs/demo/record.py   # python3-dbus, python3-gi
node docs/demo/capture.js                                             # Node's playwright, ffmpeg
```
