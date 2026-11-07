# Principles

- **Free software**: GPL-3.0-or-later, like patina.
- **Local first.** kortex itself sends nothing anywhere; where a request goes is the
  agent's configuration. With Ollama, requests and answers stay on the machine, but two
  things around them do not: opencode fetches its list of models from
  `models.opencode.ai` whenever its server starts (`OPENCODE_DISABLE_MODELS_FETCH=1`
  stops that), and on its first prompt it fetches the provider's package from npm.
  KRunner keeps its last queries, requests to the agent included, in
  `~/.local/share/krunnerstaterc`.
- **No action without a yes.** kortex never answers a permission request on its own.
  "Always" is the agent's own rule, which the row spells out (`Always allows: sh *`
  lets any script through): with opencode it holds for every conversation until its
  server stops, after a quiet quarter of an hour, and is not written into its
  configuration. A request opencode cannot list comes from its event stream and is
  answered like any other.
- **Root through a dialog.** The agent has no terminal, so `sudo` cannot ask for a
  password there. patina sets the session's `SUDO_ASKPASS` to a password dialog in the
  theme's colours and tells the agent to run `sudo -A`; on another desktop, point
  `SUDO_ASKPASS` at a dialog of your own (`ksshaskpass`, for one) for the same.
- **Python 3**, with `python3-dbus` and `python3-gi` from Debian's archive: one readable
  program, no build step. kortex may be Python because it lives in a repository of its
  own; patina's own tooling is bash.
- **Small and boring**: one program, one desktop file for KRunner, one D-Bus service
  file, one icon. No daemon beyond what D-Bus activation starts, no model runtime of
  its own, no telemetry.
- **Plasma 6 and its D-Bus runner interface only**; no compiled KRunner plugin, so it
  does not break with a Plasma update's ABI.
- **The words are yours, the rest the agent's**: kortex adds no prompt of its own
  beyond a conversation's title.
