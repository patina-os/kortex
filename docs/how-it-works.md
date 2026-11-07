# How kortex works

```text
KRunner ──D-Bus (org.kde.krunner1)──> kortex ──HTTP on 127.0.0.1──> opencode serve
start menu                              │                            or hermes gateway
                                        │                                   │
notifications <─D-Bus───────────────────┘                 Ollama (local) or a cloud provider
```

One Python program, `kortex.py`, with a KRunner desktop file and a D-Bus service file.

## A KRunner D-Bus runner

KRunner calls `Config`, `Match`, `Actions`, `Run` and `Teardown` on kortex over the
session bus (`org.kde.krunner1`), the documented interface for runners that are not
compiled into KRunner. kortex is started on demand by D-Bus activation
(`io.github.patina_os.kortex`) and holds no state KRunner needs. Both KRunner and the
start menu send it every search; it tells the start menu (plasmashell) from KRunner by
who calls it over D-Bus.

KRunner never waits for the agent: every request to it goes to a thread of its own, and
a match shows what the agent said last. A request still on its way, or an agent that
starts, shows as "Waiting for the agent…".

## The palette that pulls

KRunner asks for matches; it is not told about new ones. While the conversation is on
screen and changes, kortex runs the query again through KRunner's own D-Bus method
(`org.kde.krunner /App querySingleRunner`) with the marker, `opencode ›`, so KRunner
asks again and gets the news; the first time it does for a request, kortex logs
"KRunner asked again". KRunner asks only for a query that differs from the one it
shows, so each refresh puts an invisible word joiner (U+2060) after the marker or takes
it away again. The marker and that character are all kortex adds to the query line,
and a follow-up typed after it is kept. While a request runs, the elapsed seconds are
news too, except while a row asks, which must hold still to be clicked.

While KRunner is closed (its `Teardown`), kortex follows the request with one
notification (`org.freedesktop.Notifications`): after three seconds that the agent
works on it, transient, then, in its place, how it ended. Show and a click on it are
its actions; kortex listens for `ActionInvoked` and opens KRunner on the conversation.

## opencode as the agent

kortex starts `opencode serve` on 127.0.0.1 with a random port and a password
(`OPENCODE_SERVER_PASSWORD`, generated once, kept in `~/.config/kortex/` with mode
0600), and stops it, and itself, after a quiet quarter of an hour. It uses the server's
API:

| Call | For |
|---|---|
| `POST /session` | a new conversation |
| `POST /session/:id/prompt_async` | the request |
| `GET /session/:id/message`, `GET /session/status` | the answer, and whether the agent is at work |
| `GET /permission`, `POST /permission/:id/reply` | the rows that ask: `once`, `always` or `reject` |
| `GET /event` | what the two above cannot tell |

It reads them when KRunner asks, and every 0.7 s while a conversation is on screen or
a request runs. The event stream fills two gaps of opencode 1.19:

- `GET /session/:id/message` keeps a text or reasoning part empty until the part is
  done, while the stream sends its words (`message.part.delta`); kortex keeps them and
  fills the part in, so the answer and the agent's thoughts grow on screen.
- `GET /permission` fails as a whole while a web fetch without a timeout waits; kortex
  takes the requests from the stream (`permission.asked`, `permission.replied`).

opencode reads its own configuration, so the model, the provider and the permissions
are the user's (patina writes them at install time).

## Hermes Agent as the agent

With `hermes` in `~/.config/kortex/agent`, kortex starts `hermes gateway run` with its
API server on 127.0.0.1 (`API_SERVER_KEY`, the same generated password,
`API_SERVER_PORT`, a free port) and stops it with itself.

| Call | For |
|---|---|
| `POST /api/sessions` | a new conversation |
| `POST /v1/runs` | the request |
| `GET /api/sessions/:id/messages`, `GET /v1/runs/:id` | the answer, and whether the run is done |
| `GET /v1/runs/:id/events` | the run's approval requests |
| `POST /v1/runs/:id/approval` | the answer to one: `once`, `always`, `deny` |

Hermes asks first only before a command it counts as dangerous (deleting a folder,
formatting a disk, piping a download into a shell, root through `sudo -A`), not before
every change as opencode does: it edits files, deletes a single file, stops a process
or installs an app from Flathub without asking. With `approvals.mode: manual` (patina
sets it) no second model decides which commands to let through. A request left
unanswered is denied after Hermes' timeout (five minutes). Hermes' own extras,
messaging and its scheduler, run in that gateway only when you set them up
(`hermes setup`).

## Services connected on the fly

opencode reads its MCP servers (`mcp` in `opencode.json`) when its server starts, not
again. When the file changes while kortex's server runs, kortex adds the servers that
are new and enabled through `POST /mcp`, their `{file:...}` and `{env:...}` filled in as
opencode fills them in its file (a token file), before the next request and while a
conversation is on screen: a service connected mid-conversation (`patina ai connect`)
is there for the agent's next step. A server changed or removed takes effect at the
server's next start.

Hermes reads its own (`mcp_servers` in `~/.hermes/config.yaml`) when its gateway
starts, and keeps a conversation's tools as they were when it began: when the file
changed, kortex stops the gateway it started once no run is busy, and the next request
starts it again, so the service is there from the next conversation on.

## Open in terminal

Runs the terminal Plasma names (`TerminalApplication` in `kdeglobals`, else konsole)
with `opencode attach <server> --session <id>` (Hermes: `hermes --resume <id>`), the
password in the environment, not on the command line.
