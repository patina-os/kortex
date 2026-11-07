# Development

## Tests

```sh
python3 -m unittest discover -s tests                        # what kortex shows and does, agents faked
pipx run ruff check kortex.py tests/                         # lint
KORTEX_OPENCODE=$(command -v opencode) python3 tests/e2e.py  # end to end, a real opencode serve
python3 tests/e2e.py hermes                                  # the same, a real Hermes gateway
```

The end-to-end test runs kortex as KRunner would: a private session bus that starts it
by D-Bus activation, a stand-in for KRunner's `/App` and for the notification server
(`tests/fake_krunner.py`), a real agent server and a test model on localhost
(`tests/mock_llm.py`) that asks to run a command. It checks that nothing runs before
Allow once and that it runs after; then it connects an MCP server (`tests/mock_mcp.py`)
in the agent's configuration while kortex runs and checks that the model gets its tool
on the next request, with KRunner closed: the notification of the answer comes, and
its Show reopens the conversation. Hermes gets a folder to remove, since it asks first
only before what it counts as dangerous. It needs `python3-dbus`, `python3-gi` and the
D-Bus daemon; opencode fetches its provider package from npm on first use.

CI (`.github/workflows/ci.yml`) runs all of them. patina's desktop workflow runs kortex
in a real Plasma 6.3 session on Debian 13 and fails when KRunner does not follow the
conversation.

## The demo

The animation at the top of the README is a replay of a real run, recorded and drawn by
[`docs/demo/`](demo/); its README says how to make it again for a new kortex or opencode.

## The logo

`kortex.svg` is the icon KRunner, the notifications and patina's start menu show:
patina's ▲ inside two contour lines, drawn to read at 16 and 22 px, the ▲ in the colour
scheme's positive colour. `docs/logo.svg` (for a dark ground) and `docs/logo-dark.svg`
(for a light one) set it beside the word in the block letters of patina's wordmark,
for the README. As in patina, they are committed as the SVG, not with the script that
drew them.

## A release

1. Set the version in `kortex.py` (`VERSION`) and `kortex.desktop`
   (`X-KDE-PluginInfo-Version`); a test keeps them equal.
2. Add its section to `CHANGELOG.md`.
3. Merge, then tag `main` as `v<version>`.
4. In patina, pin the tag (`kortex.ref` in `manifest/upstream.yaml`); the next
   `patina update` installs it where an AI agent is on.
