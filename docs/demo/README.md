# The demo

`kortex-demo.gif`, at the top of the README, is a replay of a real run, not a
screenshot: kortex answered over D-Bus exactly what it shows, and the KRunner panel is
drawn by `page.html` in patina's aurora colours.

- `record.py` runs kortex the way `tests/e2e.py` does (a private session bus that
  starts it by D-Bus activation, a stand-in for KRunner, a real `opencode serve`), with
  opencode set up as patina sets it up, and a scripted model, `demo_llm.py`, that
  answers slowly and uses tools like an agent. It asks how full the disk is (a
  read-only `df`, which runs without asking) and to switch to the light theme (which
  waits for Allow once; `patina` is a stand-in that only prints). Every Match and Run
  and what kortex answered goes into `timeline.json`.
- `capture.js` fills `timeline.json` into `page.html`, writes `replay.html` (open it in
  a browser to step through the run, with the D-Bus log beside it) and records its
  `#gif` mode into `kortex-demo.gif`.

To make it again, for a new kortex or opencode:

```sh
KORTEX_OPENCODE=$(command -v opencode) python3 docs/demo/record.py   # python3-dbus, python3-gi
node docs/demo/capture.js                                             # Node's playwright, ffmpeg
```
