# The desktop run

The manual run of kortex on a real Plasma desktop. The unit tests drive kortex with the
agent and the desktop as fakes, the end-to-end tests with a real agent and a stand-in
for KRunner, and patina's desktop workflow takes one picture of a real KRunner. This
run sees what none of them can: what a key does on a row, how the window behaves while
the agent works, the notifications' buttons, the password dialog and the panel's place
on the screen.

Written for kortex 0.10.0. Tick the boxes in a copy of this file or in an issue
([kortex's issues](https://github.com/patina-os/kortex/issues)); with each box that
fails, say what you saw instead. What a model does is not what is tested here: when
the agent takes another step than the one a check expects, ask again in other words
and note it.

Every request below is harmless: the files it makes are empty and are named
`kortex-test…` in your home folder.

## 1. The machine

- [ ] a real machine, or a VM with 3D acceleration, with KDE Plasma 6 and a Wayland
  session (sections 2 to 8 once more in an X11 session if you can)
- [ ] patina with an AI agent on (`patina ai` says which), or kortex alone, set up as
  [setup.md](setup.md) says
- [ ] opencode as the agent, with a cloud model, for sections 2 to 8
- [ ] note for the report:

  ```sh
  plasmashell --version
  echo "$XDG_SESSION_TYPE"
  python3 ~/.local/share/kortex/kortex.py --version
  opencode --version
  patina ai
  ```

- [ ] a terminal that shows kortex's log for the whole run; paste its lines with
  anything that fails:

  ```sh
  journalctl --user -f | grep kortex
  ```

- [ ] `~/.config/kortex/handover` is missing or says `off` (section 7 turns it on)

## 2. Asking

- [ ] Meta+A with nothing running opens the palette with `ai` in the field and the row
  "Ask kortex: type your request"; before your first conversation, also "Try: why is
  my wifi slow?" and "Try: what keeps the fan busy?"
- [ ] Enter on a Try row puts its words in the field and sends nothing
- [ ] typing after `ai` shows one row with your words and "Ask kortex · Enter"
- [ ] Enter sends it: the palette closes and opens again on `kortex ›` within a second
- [ ] Meta+Space, then `how full is my disk?`: "Ask kortex: how full is my disk?" is
  the first row
- [ ] Meta+Space, then `firefox`: Firefox is first and "Ask kortex: firefox" is last
- [ ] the start menu's search, `how full is my disk?`: the same row; Enter opens the
  conversation in the command palette
- [ ] Meta+A after a conversation shows the hint and that conversation to continue,
  and no Try rows, not even for a moment
- [ ] Meta+A while the agent works or asks opens on the conversation, not on `ai`

## 3. While the agent works

Ask `how full is my disk, and which folders in my home are the largest?`

- [ ] a first row at once: "Working · 0 s", counting on without a jump
  or "Thinking · 2 s · …"
- [ ] the seconds count up by themselves, without a key
- [ ] under it, one line each: the agent's last sentence, "Done so far: df · du" (the
  steps in a word each), "Show every step"
- [ ] the window keeps its height while the agent works: it does not grow and shrink
  with the words
- [ ] the selection stays on the row you put it on when the rows refresh
- [ ] Enter on the first row (the one that says how long) copies nothing: paste
  somewhere to see that the clipboard is as it was
- [ ] Enter on the row with the agent's words, and on "Done so far", does nothing but
  leave the conversation open. Say whether the palette closes and opens again, and
  how that looks
- [ ] the square icon on the first row is Stop: the agent stops and the row says
  "Stopped. Say what to do instead."
- [ ] typing `and in /var?` after `kortex ›` shows "Continue · Enter"; the rows above
  do not replace what you type while you type

## 4. A step that asks

Ask `make an empty file called kortex-test in my home folder`

- [ ] the palette shows, from the top: the row that asks, "Allow this once", "Don't,
  stop here", "Waiting for your answer", in that order
- [ ] the row that asks starts with a bold line of what the step would do ("Runs a
  command" or "Creates or changes files"), then "Allow: …" in the agent's words, then
  the whole command
- [ ] it has no icons of its own: its answers are the rows below
- [ ] the rows hold still while they wait: no refresh moves them under the pointer
- [ ] Enter on the row that asks answers nothing: `ls ~/kortex-test` finds no file
- [ ] ↓ to "Allow this once", Enter: the file is there, and the palette goes on to the
  answer
- [ ] ask once more, and Enter on "Don't, stop here": no file, and the answer says
  "Stopped: you denied the last step, so it did not run."
- [ ] `delete the file kortex-test in my home folder`: the bold line says "Deletes"
- [ ] `show me the last ten lines of the kernel log, with sudo`: the bold line says
  "Runs as root". Allow it: a notification asks for your password, its dialog is in
  the theme's colours, and the answer follows
- [ ] `install the flatpak org.kde.kcalc` and then Don't: the bold line says "Installs
  or removes software" (with "· as root" if the agent chose apt); a `flatpak search`
  before it says "Only looks"
- [ ] `add a comment line "# kortex test" to the end of my .bashrc`: the bold line
  says "Changes a file", with the file's path and the added line under the words.
  Don't
- [ ] a long command (ask for three files in one step) shows whole, on as many lines
  as it needs, never cut with "…"
- [ ] kortex without patina's rules only: a command that only looks (`ls`, `df`) asks,
  and has a fourth row, "Always allow: ls …", with "In every conversation, until the
  agent restarts" on its second line; `du` and `wc` have none

## 5. The answer

- [ ] the answer replaces the working rows; its small line says "Enter copies" and how
  many steps
- [ ] Enter on it copies it: paste somewhere
- [ ] "Show every step" under an answer with steps opens the panel (section 8)
- [ ] ask `write me a 600 word essay about contour maps`: the answer stops after about
  twelve lines with "… (the rest: Show the whole answer, below)", the window fits a
  1366x768 screen, and "Show the whole answer" opens the panel
- [ ] bold words and lists in an answer show as bold and as bullets, not as `**` and
  `-`

## 6. With the palette closed

Ask `make an empty file called kortex-test-2 in my home folder, then tell me a fact
about maps`, and click on the desktop at once.

- [ ] after about three seconds a notification says "kortex is working on it"
- [ ] when the step asks, a notification of its own: "kortex asks first · Runs a
  command", the agent's words and the command, and three buttons: Allow this once,
  Don't, stop here, Show
- [ ] Allow this once on it: the file is there, the palette stays closed, and that
  notification goes away by itself (look in the notification history too)
- [ ] a notification brings the answer, "kortex answered"; its Show, and a click on
  it, open the conversation
- [ ] ask again with another name, close the palette, and Don't, stop here on the notification:
  no file, and the next notification says "Stopped: you denied the last step, so it
  did not run."
- [ ] ask again, close the palette, wait for the notification that asks, then press
  Meta+A and answer in the palette: the notification that asked goes away
- [ ] ask something long, then ask something else before it is answered: the first
  one's answer still comes as a notification, and its Show opens that conversation

## 7. Allow the rest of this task

Off unless you turn it on. [usage.md](usage.md#allow-the-rest-of-this-task) says what
it gives up.

- [ ] while it is off: a step that asks has two answer rows, typing `go` after
  `kortex ›` shows no "Allow the rest of this task", and the notification that asks
  has no "Allow the rest of this task"
- [ ] Meta+A with nothing running has the row "Allow the rest of this task: off"; Enter
  turns it on, the row says "on", and `cat ~/.config/kortex/handover` says `on`
- [ ] with patina: `patina ai handover` says it is on; `patina ai handover off`, then
  `on`, change the same file, and the welcome's AI menu shows it on

Ask `make an empty file kortex-test-a in my home folder, then in a second step
kortex-test-b, then delete kortex-test-a`

- [ ] the first step has a third answer row, "Allow the rest of this task", between
  "Allow this once" and "Don't, stop here", with what still asks on its second line
- [ ] Enter on it: the first file is there, and the second step runs without a row
- [ ] while it runs, a row says "Allow the rest of this task: on · 1 step ran on it"
- [ ] the delete still asks: its bold line says "Deletes", and where the third answer
  would be a row says "Deletes: asks every time, also with Allow the rest of this task"
- [ ] after the answer, a row lists what ran without a question of its own, with the
  command
- [ ] the next request in the same conversation asks again from its first change
- [ ] ask the three-step request again, and type `go` after `kortex ›` while the first
  step asks: the first row is "Allow the rest of this task"; Enter does the same
- [ ] ask again, allow the rest, then Enter on "Allow the rest of this task: on" (or type
  `ask`): the next change asks again
- [ ] ask again, allow the rest, close the palette: the later steps run without a
  notification that asks; the delete's notification has no "Allow the rest of this task"
- [ ] with the palette closed from the start, the first step's notification has four
  buttons: Allow this once, Allow the rest of this task, Don't, stop here, Show. Say
  whether Plasma shows all four, and whether their words fit
- [ ] a step as root, one that installs, `systemctl --user enable …` and `kill …` ask
  every time after "Allow the rest of this task"
- [ ] turn it off again (the row, or `patina ai handover off`): the third answer is
  gone

## 8. The panel

- [ ] Meta+Shift+A (patina), or "Show every step": a terminal with the agent's own
  view of the conversation opens at the screen's right edge, as high as the work
  area, about a fifth of the screen wide (480 to 720 px), above your other windows
- [ ] on Wayland, and on X11
- [ ] it shows every step and command of the conversation, and the conversation goes
  on there when you type in it
- [ ] a second Meta+Shift+A brings the same panel to the front, and opens no second
- [ ] with kitty as the terminal, the taskbar shows it as kortex, with kortex's icon
- [ ] Meta+Shift+A with no conversation yet opens the agent's interface alone
- [ ] with two screens: it docks on the screen it opens on

## 9. The models

- [ ] a cloud model answers `what is 3+3?` in the palette
- [ ] a local model through Ollama (`patina ai on local`) answers the same; note the
  model and how long the first row and the answer took
- [ ] a model that thinks first shows "Thinking · 12 s · …" with the start of the last
  sentence it finished, and does not look stuck
- [ ] a provider that cannot be reached (turn the network off, ask something): the
  row says which provider and where to check it, not a raw error

## 10. Hermes

`patina ai on hermes`, or `hermes` in `~/.config/kortex/agent`. Hermes asks only before
what it counts as dangerous.

- [ ] Meta+A: the hint's small line says "Hermes answers", and there is no "Allow the
  rest of this task" row
- [ ] `what is 3+3?` is answered; note how long Hermes' first start took
- [ ] `make a folder kortex-test-dir in my home folder with an empty file in it` runs
  without a row
- [ ] `delete the folder kortex-test-dir`: a row asks, its bold line says "Deletes",
  with "Allow this once" and "Don't, stop here" under it, and no "Allow the rest of
  this task" even while `~/.config/kortex/handover` says `on`
- [ ] the notification that asks has Allow this once, Don't, stop here and Show
- [ ] a question left unanswered for five minutes leaves the palette
- [ ] back to opencode (`patina ai on opencode`): the next request is answered by
  opencode, and the hint says so

## 11. Installing and removing it

On a desktop without patina, or after the run on a copy of the machine.

- [ ] `./install.sh` as a user, no root: Meta+Space, a search, and "Ask kortex" is
  there without logging out
- [ ] `./uninstall.sh`: the row is gone from a new search
- [ ] what is left:

  ```sh
  find ~/.config ~/.local -iname '*kortex*'
  ```

  Expected: `~/.config/kortex` with `password`, and `agent` and `handover` if they
  were set. Anything else goes into the report
- [ ] `./install.sh` again: it works as before, with the same password file

## 12. After the run

- [ ] remove the test files: `rm -f ~/kortex-test*; rm -rf ~/kortex-test-dir`
- [ ] `~/.config/kortex/handover` is as you want it
- [ ] the report has the versions of section 1, every box that failed with what you
  saw, and the log lines around it
- [ ] the [roadmap](roadmap.md)'s open items this run covers are ticked: the answers
  from a local and a cloud model (section 9), the panel on Wayland (section 8),
  installing and removing (section 11), and the 0.9.0 palette on a real desktop
  (sections 3 to 7)
