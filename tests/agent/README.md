# Testing an agent by hand

The tools of the agent test of November 2026 (patina's `docs/agent-test.md` has what it
found): a person or an AI agent uses kortex as a user would, with a real agent and a
real model, and checks every outcome on the machine or in a service's state. They are
no automated test: `tests/e2e.py` is, with a scripted model.

- `kx.py` drives the installed kortex over D-Bus, as KRunner does, and prints the rows
  the palette would show as they change. `kx.py help` lists its commands.
- `mock_service.py` is a stand-in for an external service, an MCP server on stdio:
  `tracker` (teams, issues and comments, like Linear) or `cloud` (notes, a calendar and
  tasks, like Nextcloud). It keeps its state in a JSON file and logs every call beside
  it (`<state>.calls`), so a test can seed a service and compare it after.

## The machine

Debian 13 with patina installed by Path A and an AI bundle, in a container or a VM,
with `python3-dbus`, `python3-gi` and `dbus-daemon`. kx starts a private session bus,
so no Plasma session is needed; a real KRunner shows what kx cannot (how rows look at
the palette's width), so look at one there too. A user with `sudo` lets the agent do
what needs root.

## A run

```sh
kx=tests/agent/kx.py
$kx up                                   # a private bus; it starts the installed kortex
$kx ask "why is my disk full?"           # prints the rows; exits 3 when a row asks
$kx answer allow-once                    # or deny; then it watches again
$kx pick "Platform"                      # the agent asked a question (exit 5)
$kx say "and delete the biggest cache"   # a follow-up in the same conversation
$kx ask "update everything" --closed     # as if KRunner was closed: notifications follow
$kx notes                                # the notifications and KRunner queries kortex sent
$kx open                                 # Meta+A: what KRunner is opened with
$kx down
```

`--on-ask allow` or `--on-ask deny` answers every row, for a run that should not stop;
`--timeout` sets how long to watch (300 s).

A service: add the stand-in to the agent's configuration as a local MCP server, then
run `patina install` again, which writes the rules that make its tools ask first (a
server that is no preset asks for every tool):

```jsonc
// ~/.config/opencode/opencode.json
"mcp": {"tracker": {"type": "local", "command": ["python3", "/path/to/mock_service.py", "tracker", "/path/to/tracker.json"]}}
```

```yaml
# ~/.hermes/config.yaml
mcp_servers:
  tracker: {command: python3, args: [/path/to/mock_service.py, tracker, /path/to/tracker.json]}
```

## What counts

Stage a fault (break a file, fill a folder, stop a service) or seed the service, type
the request as a user without expert knowledge would, answer the rows as such a user
would, then compare the machine or the state file with before. What the agent says it
did is not evidence: in the test of November 2026 one answer said a file had been left
alone after it was destroyed. Note per request: right, partly right or wrong; how many
rows asked and whether a user could judge them; what changed without a row; and how
long it took.
