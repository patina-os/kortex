#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""kortex: the AI agent in KDE Plasma's command palette.

A KRunner D-Bus runner (org.kde.krunner1). Any search in KRunner or Plasma's start menu
ends with "Ask kortex: …" (first when it reads as a question), `ai <request>` asks at
once, `ai` alone lists the recent conversations, and `kortex ›` (`ai ›` too) shows the
current one: while the agent works, a row that says how long it has been at it and what
it wrote or thought last, and a row for each step it took; then its answer. Every action
the agent wants to take is a match of its own with Allow once, Always and Deny. Each of
those rows is one line, except an answer and a row that asks, so KRunner's window holds
still while the agent works. The start menu shows neither the answer nor those actions,
so the conversation opens in KRunner. While KRunner is closed, a notification follows
the request until the agent answers. The agent is opencode or Hermes Agent
(~/.config/kortex/agent, KORTEX_AGENT): kortex starts its server (`opencode serve`,
`hermes gateway run`) on 127.0.0.1 when it is first needed and talks to its HTTP API
from a thread of its own, so KRunner never waits for the agent.

D-Bus activation starts kortex (see install.sh); it stops itself and the server after
a quiet quarter of an hour.
"""

import base64
import html
import http.client
import json
import os
import queue
import re
import secrets
import shlex
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

VERSION = "0.7.0"
SERVICE = "io.github.patina_os.kortex"
OBJECT_PATH = "/runner"
IFACE = "org.kde.krunner1"
RUNNER_ID = "kortex"  # X-KDE-PluginInfo-Name in kortex.desktop
TRIGGER = "ai"
NAME = "kortex"  # what the palette, its rows and its notifications call it, whatever the agent
VIEW_MARK = "›"  # after kortex's name (`kortex ›`), or `ai`: the current conversation
# KRunner asks again only for a query that differs from the one it shows, so a refresh
# puts an invisible word joiner after the marker, or takes it away again. A refresh
# rewrites KRunner's field, so it comes only while the field holds the bare `kortex ›`
# (or `ai`), never while a follow-up is typed after it.
JOINER = "\u2060"
ICON = "kortex"
SEP = "\x1f"  # between the kind of a match and its argument in a match id
MAX_ANSWER = 1500  # characters of an answer KRunner shows; the terminal has the rest
MAX_LATEST = 40  # characters of what the agent wrote or thought last, after the seconds
PANEL_SHARE, PANEL_MIN, PANEL_MAX = 0.2, 480, 720  # the panel's width: a fifth of the screen, in px
MAX_NOTE = 300  # characters a notification shows of what the agent said last; Show has the rest
MIN_SEARCH = 3  # letters a search needs before it is offered to the agent
TELL_AFTER = 3  # seconds a request runs before a closed KRunner gets a notification
REFRESH_MS = 700
IDLE_EXIT = 15 * 60  # seconds without a request before kortex and its server stop
CATEGORY_RELEVANCE = 100  # KRunner::QueryMatch::CategoryRelevance::Highest
CATEGORY_LOWEST = 0  # ::Lowest, for "Ask kortex" below what a search finds
# A search that starts with one of these and has three words or more reads as a request
# to the agent, and so does one that ends with a question mark: "Ask kortex" comes
# first then. English and German, the words a question or a request starts with.
ASKING = frozenset({
    "how", "why", "what", "what's", "whats", "when", "where", "who", "which", "can",
    "could", "would", "should", "is", "are", "does", "did", "explain", "help", "fix",
    "write", "install", "tell", "please", "update", "wie", "warum", "wieso",
    "weshalb", "was", "wann", "wo", "wer", "welche", "welcher", "welches", "welchen",
    "welchem", "wozu", "woher", "wieviel", "kann", "kannst", "können", "könnt",
    "könntest", "könnte", "koennte", "koennen", "soll", "sollte", "ist", "sind", "gibt",
    "erkläre", "erklaere", "erklär", "hilf", "bitte", "mach", "mache", "schreib",
    "schreibe", "installiere", "repariere", "aktualisiere", "zeig", "zeige", "ändere",
    "aendere", "stell",
})
# Not among them: do, set, show, make and change, which start the names of settings
# ("do not disturb", "show hidden files") as often as a request.
# With one of these, two words are a request: "install photocraft" would else leave
# KRunner's own "Run install photocraft" first, which runs coreutils' install.
ASKING_TWO = frozenset({"install", "installiere"})
# A rule for Always that starts with one of these lets through far more than the
# command that asks: any command as root, any script, any deletion. Always is not
# offered for it, nor for a change to any file. git, apt and patina are here for the
# width of their rules: "git -C *", "patina *" hold every subcommand.
RISKY = frozenset({
    "sudo", "su", "doas", "pkexec", "rm", "sh", "bash", "zsh", "fish", "dash", "python",
    "python3", "perl", "ruby", "node", "curl", "wget", "env", "xargs", "eval", "dd",
    "chmod", "chown", "mv", "tee", "nohup", "kill", "pkill", "killall", "git", "apt",
    "apt-get", "dpkg", "patina",
})

# The KWin script that docks the panel (Desktop.dock): the window of one process, at
# the right edge of the screen it opens on, as high as the work area, kept above.
PANEL_SCRIPT = """
const pid = %d, share = %s, least = %d, most = %d;
function dock(window) {
    if (!window || window.pid !== pid || !window.normalWindow) {
        return false;
    }
    const area = workspace.clientArea(KWin.MaximizeArea, window);
    const width = Math.round(Math.min(most, Math.max(least, area.width * share)));
    window.frameGeometry = {x: area.x + area.width - width, y: area.y, width: width, height: area.height};
    window.keepAbove = true;
    return true;
}
if (!workspace.windowList().some(dock)) {
    workspace.windowAdded.connect(dock);
}
"""

# The actions a match can offer: id, text, icon. KRunner asks for them once.
ACTIONS = [
    ("allow-once", "Allow once", "dialog-ok-apply"),
    ("allow-always", "Always allow", "media-playlist-repeat"),  # not a second check mark
    ("deny", "Deny", "dialog-cancel"),
    ("copy", "Copy the answer", "edit-copy"),
    ("terminal", "Show what the agent does", "view-right-new"),  # the panel, docked on the right
    ("stop", "Stop", "process-stop"),
]
REPLIES = {"allow-once": "once", "allow-always": "always", "deny": "reject"}
HERMES_CHOICES = {"once": "once", "always": "always", "reject": "deny"}  # Hermes' words for them
# opencode 1.19 answers GET /permission with a 400 for the whole list while one request in
# it lacks an optional field (a webfetch without a timeout). kortex then takes the requests
# from opencode's event stream; one it did not see there is shown as waiting, unanswerable.
UNREADABLE = {"id": "unreadable", "permission": "a request kortex cannot show"}


def log(*words):
    print("kortex:", *words, file=sys.stderr, flush=True)


class AgentError(Exception):
    """The agent is missing, did not start, or did not answer."""


def http_status(error):
    """The HTTP status the agent answered a failed request with, or None."""
    cause = error.__cause__
    return cause.code if isinstance(cause, urllib.error.HTTPError) else None


def config_dir():
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "kortex"


def state_dir():
    return Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local/state") / "kortex"


def opencode_config():
    """opencode's own configuration file, where its MCP servers (`mcp`) are."""
    given = os.environ.get("OPENCODE_CONFIG")
    if given:
        return Path(given)
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "opencode" / "opencode.json"


def hermes_config():
    """Hermes' own configuration file, where its MCP servers (`mcp_servers`) are."""
    return Path(os.environ.get("HERMES_HOME") or Path.home() / ".hermes") / "config.yaml"


def agent_name():
    """The agent kortex talks to: KORTEX_AGENT, else ~/.config/kortex/agent, else opencode."""
    given = os.environ.get("KORTEX_AGENT")
    if not given:
        try:
            given = (config_dir() / "agent").read_text().strip()
        except OSError:
            given = ""
    return "hermes" if given == "hermes" else "opencode"


def fill_placeholders(text, base):
    """opencode's placeholders in a piece of its configuration, filled as opencode fills
    them when it reads its file: {env:NAME} and {file:path} (~/ or relative to `base`),
    the file's content trimmed; `text` is JSON and stays JSON."""
    def file(m):
        return json.dumps((base / Path(m.group(1)).expanduser()).read_text().strip())[1:-1]
    text = re.sub(r"\{env:([^}]+)\}", lambda m: json.dumps(os.environ.get(m.group(1), ""))[1:-1], text)
    return re.sub(r"\{file:([^}]+)\}", file, text)


def mtime(path):
    try:
        return path.stat().st_mtime_ns
    except OSError:
        return None


def server_password():
    """The password of the server kortex starts: made once, readable by the user only."""
    path = config_dir() / "password"
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(secrets.token_urlsafe(32) + "\n")
    return path.read_text().strip()


def find_opencode():
    """opencode on PATH, or where its installers put it; KORTEX_OPENCODE overrides."""
    given = os.environ.get("KORTEX_OPENCODE")
    if given:
        return given
    home = Path.home()
    path = os.pathsep.join([os.environ.get("PATH", ""), str(home / ".local/bin"), str(home / ".opencode/bin")])
    found = shutil.which("opencode", path=path)
    if not found:
        raise AgentError("opencode is not installed (https://opencode.ai)")
    return found


def find_hermes():
    """hermes on PATH, or where its installer links it; KORTEX_HERMES overrides."""
    given = os.environ.get("KORTEX_HERMES")
    if given:
        return given
    home = Path.home()
    path = os.pathsep.join([os.environ.get("PATH", ""), str(home / ".local/bin"),
                            str(home / ".hermes/hermes-agent/venv/bin")])
    found = shutil.which("hermes", path=path)
    if not found:
        raise AgentError("Hermes Agent is not installed (https://hermes-agent.nousresearch.com)")
    return found


def note_event(asked, line, streamed=None):
    """Keep `asked`, the pending permission requests by id, current with one line of
    opencode's event stream (`data: {"type": ..., "properties": ...}`), and `streamed`, the
    text of each part still being written, by part id: opencode 1.19 keeps a text or
    reasoning part empty in GET /session/:id/message until the part is done, and sends
    the words on its event stream meanwhile."""
    if not line.startswith("data:"):
        return
    try:
        event = json.loads(line[5:])
        kind, props = event["type"], event["properties"]
        if kind == "permission.asked":
            asked[props["id"]] = props
        elif kind == "permission.replied":
            asked.pop(props["requestID"], None)
        elif streamed is not None and kind == "message.part.delta" and props.get("field") == "text":
            streamed[props["partID"]] = streamed.get(props["partID"], "") + props["delta"]
        elif streamed is not None and kind == "message.part.updated" and props["part"].get("text"):
            streamed.pop(props["part"]["id"], None)  # done: GET /message has the whole of it now
    except (ValueError, LookupError, TypeError, AttributeError):
        pass  # not an event kortex follows


def retry_message(status, now=time.time):
    """Why opencode waits before it tries a request again, and when it will: its own
    title for the reason ("Free limit reached") where it gives one, since the provider's
    words may be an offer ("subscribe to …"), and the time of the next try once that is
    a minute or more away, so a limit that lasts hours does not read as a moment."""
    words = (status.get("action") or {}).get("title") or status.get("message", "")
    try:
        at = float(status.get("next") or 0) / 1000
    except (TypeError, ValueError):
        at = 0
    if at - now() >= 60:
        words += (" · " if words else "") + "next try " + time.strftime("%H:%M", time.localtime(at))
    return words


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Server:
    """An agent's HTTP server on this machine, started by kortex on first use and stopped
    with it; or one the environment names that runs already, which kortex never stops."""

    def __init__(self, url=None, password=None):
        self.url = url
        self.password = password
        self.process = None
        # Never through a proxy: the server is on this machine.
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        self.asked = {}  # permission requests from an event stream, by id; under self.lock
        self.streamed = {}  # the text of parts still being written, by part id; under self.lock
        self.lock = threading.Lock()
        self.listened = None  # the server the event stream is followed for
        self.config_seen = None  # the agent's configuration's mtime as the server in use read it

    def owned(self):
        return self.process is not None

    def stopped(self):
        """True when the server kortex started has exited since."""
        return self.process is not None and self.process.poll() is not None

    def launch(self, command, env, log_name, health, wait=30):
        """Start the server and wait until `health` answers."""
        state_dir().mkdir(parents=True, exist_ok=True)
        log(f"starting {' '.join(command)}")
        with open(state_dir() / log_name, "ab") as output:
            self.process = subprocess.Popen(
                command, cwd=Path.home(), env=dict(os.environ, **env), stdin=subprocess.DEVNULL,
                stdout=output, stderr=output, start_new_session=True)
        deadline = time.monotonic() + wait
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                raise AgentError(f"{command[0]} exited; see {state_dir() / log_name}")
            try:
                # opencode leaves the first connection it accepts while it starts
                # unanswered; a short wait per try moves on to the next one.
                self.request("GET", health, start=False, timeout=1, quiet=True)
                return
            except AgentError:
                time.sleep(0.2)
        raise AgentError(f"{command[0]} did not answer within {wait} seconds")

    def stop(self):
        if self.process is None:
            return
        self.process.terminate()
        try:
            self.process.wait(5)
        except subprocess.TimeoutExpired:
            self.process.kill()
        self.process = None
        self.url = None
        self.listened = None  # a new server may get the same port
        with self.lock:
            self.asked.clear()
            self.streamed.clear()

    def request(self, method, path, body=None, start=True, timeout=10, quiet=False):
        if start:
            self.start()
        data = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request(self.url + path, data=data, method=method)
        req.add_header("Content-Type", "application/json")
        self.authorize(req)
        try:
            with self.opener.open(req, timeout=timeout) as response:
                raw = response.read()
            return json.loads(raw) if raw else None
        except (urllib.error.URLError, OSError, http.client.HTTPException, ValueError) as e:
            if not quiet:  # the details, for the journal; not each try while a server starts
                log(f"{method} {path}: {e}")
            raise AgentError(f"{self.title} {failure(e, timeout)}") from e


def failure(error, timeout):
    """Why a request to the agent's server failed, in words for the palette: what
    urllib says ("<urlopen error [Errno 111] Connection refused>") explains nothing to
    a user, and the log has it."""
    if isinstance(error, urllib.error.HTTPError):
        if error.code in (401, 403):
            return f"refused kortex's password (HTTP {error.code})"
        if error.code >= 500:
            return f"failed on the request (HTTP {error.code})"
        return f"did not take the request (HTTP {error.code})"
    reason = getattr(error, "reason", error)
    if isinstance(reason, ConnectionRefusedError):
        return "is not running"
    if isinstance(reason, TimeoutError):
        return f"did not answer within {timeout} s"
    if isinstance(error, ValueError):
        return "sent an answer kortex cannot read"
    return "could not be reached"


class Opencode(Server):
    """opencode's HTTP server (`opencode serve`). KORTEX_OPENCODE_URL names one that runs
    already."""

    title = "opencode"  # the agent's name, which the palette says once, as a detail

    @classmethod
    def from_environment(cls):
        url = os.environ.get("KORTEX_OPENCODE_URL")
        if url:
            return cls(url.rstrip("/"), os.environ.get("KORTEX_OPENCODE_PASSWORD"))
        return cls(None, server_password())

    def start(self):
        if self.url and (self.process is None or self.process.poll() is None):
            self.listen()
            return
        command = find_opencode()
        port = free_port()
        self.config_seen = mtime(opencode_config())  # the new server reads it as it is now
        self.url = f"http://127.0.0.1:{port}"
        self.launch([command, "serve", "--hostname", "127.0.0.1", "--port", str(port)],
                    {"OPENCODE_SERVER_PASSWORD": self.password}, "opencode.log", "/global/health")
        self.listen()

    def add_services(self):
        """MCP servers added to opencode's configuration since its server started, added
        to the running server (POST /mcp), which does not read its configuration again: a
        service connected mid-conversation (`patina ai connect`) is there at once. One
        changed or removed waits for the next start. opencode fills {file:...} and {env:...}
        only in what it reads from its file, so kortex fills them in what it sends."""
        path = opencode_config()
        now = mtime(path)
        if self.url is None or now == self.config_seen:
            return
        self.config_seen = now
        try:
            wanted = json.loads(path.read_text()).get("mcp") or {}
            running = self.request("GET", "/mcp", start=False) or {}
            for name, config in wanted.items():
                if name not in running and isinstance(config, dict) and config.get("enabled", True):
                    log(f"adding the MCP server {name} to the running agent")
                    config = json.loads(fill_placeholders(json.dumps(config), path.parent))
                    self.request("POST", "/mcp", {"name": name, "config": config}, start=False)
        except (OSError, ValueError, AttributeError, AgentError) as e:
            log(f"MCP servers of {path} not added: {e}")  # opencode reports its own file's problems

    def listen(self):
        """Follow the event stream of the server in use, in a thread of its own."""
        if self.listened != self.url:
            self.listened = self.url
            threading.Thread(target=self.follow, args=(self.url,), daemon=True).start()

    def follow(self, url):
        """The thread: read the stream while `url` is the server in use; no GLib or D-Bus
        here, only the requests under the lock."""
        req = urllib.request.Request(url + "/event")
        self.authorize(req)
        while self.url == url:
            try:
                with self.opener.open(req, timeout=60) as response:
                    for raw in response:
                        if self.url != url:
                            break
                        with self.lock:
                            note_event(self.asked, raw.decode("utf-8", "replace"), self.streamed)
            except (OSError, http.client.HTTPException):
                pass  # the server stopped or restarts; try again while it is the one in use
            time.sleep(1)

    def authorize(self, req):
        if self.password:
            token = base64.b64encode(f"opencode:{self.password}".encode()).decode()
            req.add_header("Authorization", "Basic " + token)

    def attach(self, session):
        """The command, and its environment, that continues a session in opencode's own
        interface, or a new one without `session`; the password goes in the environment,
        not on the command line."""
        self.start()
        return ([find_opencode(), "attach", self.url] + (["--session", session] if session else []),
                {"OPENCODE_SERVER_PASSWORD": self.password or ""})

    def new_session(self, title):
        return self.request("POST", "/session", {"title": title})["id"]

    def ask(self, session, text):
        self.request("POST", f"/session/{session}/prompt_async", {"parts": [{"type": "text", "text": text}]})

    def messages(self, session):
        """The conversation, with the parts still being written filled in from the event
        stream, so the answer, and what the agent thinks first, grow on screen."""
        found = self.request("GET", f"/session/{session}/message")
        with self.lock:
            for message in found:
                for part in message.get("parts", []):
                    if not part.get("text") and part.get("id") in self.streamed:
                        part["text"] = self.streamed[part["id"]]
        return found

    def status(self, session):
        """idle, busy or retry, with the retry's message."""
        status = self.request("GET", "/session/status").get(session) or {"type": "idle"}
        return status.get("type", "idle"), retry_message(status)

    def family(self, session):
        """The conversation and the ones its agent started for a part of the work (the
        `task` tool): what one of those asks is asked of this conversation's user."""
        try:
            children = self.request("GET", f"/session/{session}/children") or []
        except AgentError:
            children = []
        return {session} | {c["id"] for c in children if isinstance(c, dict) and c.get("id")}

    def permissions(self, session):
        mine = self.family(session)
        try:
            found = self.request("GET", "/permission")
        except AgentError as e:
            if http_status(e) != 400:
                raise
            with self.lock:
                found = list(self.asked.values())
            # A request of another conversation the stream did show explains the 400.
            return [p for p in found if p.get("sessionID") in mine] or ([] if found else [UNREADABLE])
        return [p for p in found if p.get("sessionID") in mine]

    def questions(self, session):
        """What the agent asks the user (its `question` tool) and waits for: each with
        its id, the question and the answers it offers."""
        mine = self.family(session)
        try:
            found = self.request("GET", "/question") or []
        except AgentError as e:
            if http_status(e) in (400, 404):  # an opencode without it
                return []
            raise
        asked = []
        for q in found:
            if q.get("sessionID") not in mine:
                continue
            for i, one in enumerate(q.get("questions") or []):
                asked.append({"id": q["id"], "index": i, "of": len(q["questions"]),
                              "question": one.get("question") or one.get("header") or "A question",
                              "options": [o.get("label", "") for o in one.get("options") or [] if o.get("label")]})
        return asked

    def answer(self, request_id, answers):
        """Answer a question: one list of labels (or the user's own words) per question."""
        try:
            self.request("POST", f"/question/{request_id}/reply", {"answers": answers})
        except AgentError as e:
            if http_status(e) != 404:  # answered already, in the terminal say
                raise

    def abort(self, session):
        """Stop what the agent does in the conversation."""
        self.request("POST", f"/session/{session}/abort")

    def reply(self, request_id, reply):
        try:
            self.request("POST", f"/permission/{request_id}/reply", {"reply": reply})
        except AgentError as e:
            if http_status(e) != 404:  # 404: answered already, in the terminal say
                raise

    def sessions(self, limit):
        found = self.request("GET", f"/session?roots=true&limit={limit}")
        found.sort(key=lambda s: s.get("time", {}).get("updated", 0), reverse=True)
        return found[:limit]


class Hermes(Server):
    """Hermes Agent's API server (`hermes gateway run`, its API server switched on by a
    key in the environment). KORTEX_HERMES_URL and KORTEX_HERMES_KEY name one that runs
    already. Hermes asks before a command it counts as dangerous, not before every
    change; the request comes on the run's event stream."""

    title = "Hermes"
    DONE = ("completed", "failed", "cancelled", "interrupted", "stopped")

    def __init__(self, url=None, password=None):
        super().__init__(url, password)
        self.runs = {}  # the last run of each conversation, by session id

    @classmethod
    def from_environment(cls):
        url = os.environ.get("KORTEX_HERMES_URL")
        if url:
            return cls(url.rstrip("/"), os.environ.get("KORTEX_HERMES_KEY"))
        return cls(None, server_password())

    def start(self):
        if self.url and (self.process is None or self.process.poll() is None):
            return
        command = find_hermes()
        port = free_port()
        self.config_seen = mtime(hermes_config())
        self.url = f"http://127.0.0.1:{port}"
        self.launch([command, "gateway", "run"],
                    {"API_SERVER_KEY": self.password, "API_SERVER_PORT": str(port), "API_SERVER_HOST": "127.0.0.1"},
                    "hermes.log", "/health", wait=60)

    def authorize(self, req):
        if self.password:
            req.add_header("Authorization", "Bearer " + self.password)

    def attach(self, session):
        return [find_hermes()] + (["--resume", session] if session else []), {}

    def stop(self):
        super().stop()
        self.runs.clear()  # a new gateway knows none of them

    def add_services(self):
        """Hermes reads its MCP servers (`mcp_servers` in config.yaml) when its gateway
        starts. When the file changed since, the gateway kortex started stops once no run
        is busy, and the next request starts it again with them; the conversations stay."""
        now = mtime(hermes_config())
        if not self.owned() or self.url is None or now == self.config_seen:
            return
        if any(self.status(session)[0] == "busy" for session in list(self.runs)):
            return
        log("Hermes' configuration changed; restarting its gateway for the next request")
        self.stop()

    def follow(self, run, session):
        """The thread: keep the approval requests of one run, from its event stream."""
        req = urllib.request.Request(f"{self.url}/v1/runs/{run}/events")
        self.authorize(req)
        try:
            with self.opener.open(req, timeout=600) as response:
                for raw in response:
                    with self.lock:
                        if note_hermes_event(self.asked, raw.decode("utf-8", "replace"), session, run):
                            break
        except (OSError, http.client.HTTPException):
            pass  # the run ended or the server stopped; its status says which
        with self.lock:
            for key in [k for k, p in self.asked.items() if p["run"] == run]:
                del self.asked[key]

    def new_session(self, title):
        """A conversation named after the request. Hermes takes a title once: the same
        request asked again (the first 60 characters of it) was refused for good, with
        an HTTP error as the palette's answer. The second gets the time behind it."""
        try:
            return self.request("POST", "/api/sessions", {"title": title})["session"]["id"]
        except AgentError as e:
            if http_status(e) != 400:
                raise
        again = f"{title[:50]} · {time.strftime('%d.%m. %H:%M:%S')}"
        return self.request("POST", "/api/sessions", {"title": again})["session"]["id"]

    def ask(self, session, text):
        run = self.request("POST", "/v1/runs", {"input": text, "session_id": session})["run_id"]
        self.runs[session] = run
        threading.Thread(target=self.follow, args=(run, session), daemon=True).start()

    def run_state(self, session):
        """The last run of a conversation; {} for none, or one this gateway does not know."""
        run = self.runs.get(session)
        if not run:
            return {}
        try:
            return self.request("GET", f"/v1/runs/{run}")
        except AgentError as e:
            if http_status(e) == 404:
                return {}
            raise

    def messages(self, session):
        found = self.request("GET", f"/api/sessions/{session}/messages")
        return hermes_messages(found.get("data") or [], self.run_state(session))

    def status(self, session):
        state = self.run_state(session).get("status")
        return ("idle", "") if state is None or state in self.DONE else ("busy", "")

    def permissions(self, session):
        with self.lock:
            return [p for p in self.asked.values() if p["sessionID"] == session]

    def questions(self, session):
        return []  # Hermes asks in its answer

    def reply(self, request_id, reply):
        """The request is kept until Hermes has the answer, so one that did not reach it
        is shown again and can be answered again."""
        with self.lock:
            asked = self.asked.get(request_id)
        if not asked:
            return
        try:
            self.request("POST", f"/v1/runs/{asked['run']}/approval",
                         {"request_id": request_id, "choice": HERMES_CHOICES[reply]})
        except AgentError as e:
            # 404: the run is over; 409: Hermes gave up on the question. Nothing waits
            # for the answer either way.
            if http_status(e) not in (404, 409):
                raise
        with self.lock:
            self.asked.pop(request_id, None)

    def sessions(self, limit):
        found = self.request("GET", f"/api/sessions?limit={limit}").get("data") or []
        found = [{"id": s["id"], "title": s.get("title"),
                  "time": {"updated": int((s.get("last_active") or s.get("started_at") or 0) * 1000)}} for s in found]
        found.sort(key=lambda s: s["time"]["updated"], reverse=True)
        return found[:limit]


def note_hermes_event(asked, line, session, run):
    """Keep `asked` current with one line of a Hermes run's event stream; True once the
    run has ended."""
    if not line.startswith("data:"):
        return False
    try:
        event = json.loads(line[5:])
        kind = event.get("event")
        if kind == "approval.request":
            what, command = event.get("description") or "a command", event.get("command", "")
            if command.endswith("(plugin approval rule)") and ": " in what:
                # A hook sent the tool to the approval (patina's gate before a service's
                # tool): its message says what is asked, "tracker: update_issue {…}";
                # the command is Hermes' name for the rule.
                what, command = what.split(": ", 1)
            asked[event["request_id"]] = {
                "id": event["request_id"], "sessionID": session, "run": run,
                "permission": what, "patterns": [command]}
        elif kind == "approval.responded":
            asked.pop(event.get("request_id"), None)
        elif kind in ("message.delta", "message.interim", "reasoning.available", "reasoning.delta"):
            # The model writes or thinks again: no command of this run waits any more.
            # Hermes gives up on a question after a while (`approvals.timeout`, five
            # minutes) and goes on without the command, and says so nowhere: its run
            # still reads "waiting_for_approval". The row stood in the palette, "Waiting
            # for your answer", while the agent worked on.
            for key in [k for k, p in asked.items() if p["run"] == run]:
                del asked[key]
        return kind in ("run.completed", "run.failed", "run.cancelled", "run.interrupted")
    except (ValueError, LookupError, TypeError, AttributeError):
        return False  # not an event kortex follows


def hermes_messages(found, run):
    """Hermes' messages of a conversation in the shape the palette shows (opencode's):
    text parts, and one tool part per call with the result's status."""
    results = {m.get("tool_call_id"): m for m in found if m.get("role") == "tool"}
    messages = []
    for m in found:
        role = m.get("role")
        if role not in ("user", "assistant"):
            continue
        parts = [{"type": "text", "text": m["content"]}] if m.get("content") else []
        for call in m.get("tool_calls") or []:
            function = call.get("function") or {}
            try:
                given = json.loads(function.get("arguments") or "{}")
            except ValueError:
                given = {}
            result, status = results.get(call.get("id")), "running"
            if result is not None:
                try:
                    outcome = json.loads(result.get("content") or "{}")
                except ValueError:
                    outcome = {}
                failed = isinstance(outcome, dict) and (outcome.get("error") or outcome.get("exit_code") not in (None, 0))
                status = "error" if failed else "completed"
            parts.append({"type": "tool", "tool": function.get("name", "tool"), "state": {"status": status, "input": given}})
        messages.append({"info": {"role": role}, "parts": parts})
    if run.get("status") == "failed":
        error = run.get("error") or run.get("last_error") or "the run failed"
        error = error.get("message", str(error)) if isinstance(error, dict) else str(error)
        messages.append({"info": {"role": "assistant", "error": {"data": {"message": error}}}, "parts": []})
    return messages


# ---- what the palette shows: plain functions of opencode's data ------------------------


def parse(query, agent=None):
    """(kind, text): ask with the request, list for `ai` alone, view with an optional
    follow-up after `kortex ›` or `ai ›` (`>` is read as `›`), or (None, "") for any other search. The
    agent's name with the marker (`opencode ›`), as 0.4 called the conversation, shows
    it too. Any case, and a tab, are read as typed."""
    q = query.replace(JOINER, "").strip()
    if q.lower() == TRIGGER:
        return "list", ""
    for word in dict.fromkeys(w for w in (NAME, TRIGGER, agent) if w):
        head, rest = q[:len(word)], q[len(word):]
        # `kortex ›`, `kortex›`, not `kortexx ›`
        # `>` too: few keyboards have a key for ›, which the hint names
        if head.lower() == word.lower() and rest.lstrip().startswith((VIEW_MARK, ">")) and not rest[:1].isalnum():
            return "view", rest.lstrip()[1:].strip()
    if q[:len(TRIGGER)].lower() != TRIGGER or not q[len(TRIGGER):][:1].isspace():
        return None, ""
    return "ask", q[len(TRIGGER):].strip()


def asking(text):
    """True when a search reads as a request to the agent rather than a search: it ends
    with a question mark, or it has three words or more and starts with a question or a
    request (ASKING), or two and starts with install (ASKING_TWO)."""
    words = text.lower().split()
    first = words[0].strip(",.:!") if words else ""
    return (text.rstrip().endswith("?") or (len(words) >= 3 and first in ASKING)
            or (len(words) >= 2 and first in ASKING_TWO))


def tool_line(part):
    """A step the agent took, on one line: the tool and what it was given."""
    state = part.get("state") or {}
    given = state.get("input") or {}
    detail = given.get("command") or given.get("filePath") or given.get("path") or given.get("url") or state.get("title") or ""
    if not detail and isinstance(given, dict):
        # A service's tool (MCP): what it was given says which issue or note it touched.
        detail = shorten_end(", ".join(f"{k}={v}" for k, v in given.items() if isinstance(v, (str, int, float, bool))), 100)
    line = " ".join(f"{part.get('tool', 'tool')} {detail}".splitlines()).rstrip()  # a heredoc's too
    if state.get("status") == "error":
        line += " (denied)" if denied(part) else " (failed)"
    return line


def composing(part):
    """True for a tool call the model is still writing: it has no input yet, and its
    row would be the tool's bare name."""
    state = part.get("state") or {}
    return state.get("status") == "pending" and not state.get("input")


def denied(part):
    """True for a step the user denied: opencode marks it failed, with these words."""
    state = part.get("state") or {}
    return state.get("status") == "error" and "rejected permission" in str(state.get("error", ""))


DENIED = "Stopped: you denied the last step, so it did not run. Say what to do instead."


def answer_of(messages):
    """The agent's reply to the last request as the palette gives it: what it said
    after its last step, since what it said between the steps ("let me check…") is how
    it got there, not the answer; all it said when nothing follows the last step;
    "Done." when it took steps and said nothing. When the last thing in the reply is a
    step the user denied, what it said before that step and that it stopped there:
    opencode ends the turn on a Deny, and the words before read as if it went on."""
    said, _steps, _error = reply_of(messages)
    parts = [p for p in reply_parts(messages) if p.get("type") in ("text", "tool")
             and not p.get("synthetic") and not p.get("ignored") and (p.get("type") == "tool" or p.get("text", "").strip())]
    if parts and parts[-1]["type"] == "tool" and denied(parts[-1]):
        return said[-1:] + [DENIED]
    last = max((i for i, p in enumerate(parts) if p["type"] == "tool"), default=-1)
    after = [p["text"].strip() for p in parts[last + 1:]]
    return after or said or ["Done."]


def last_user(messages):
    return max((i for i, m in enumerate(messages) if m["info"].get("role") == "user"), default=-1)


def reply_parts(messages):
    """The parts of the agent's reply to the last request, in order."""
    return [p for m in messages[last_user(messages) + 1:] if m["info"].get("role") == "assistant"
            for p in m.get("parts", [])]


def reply_of(messages):
    """The agent's reply to the last request: what it said (its text parts), the steps
    it took (a line per tool it used) and the error that stopped it, if one did."""
    said, steps, error = [], [], ""
    for message in messages[last_user(messages) + 1:]:
        info = message["info"]
        if info.get("role") != "assistant":
            continue
        for part in message.get("parts", []):
            if part.get("type") == "text" and not part.get("synthetic") and not part.get("ignored"):
                if part.get("text", "").strip():
                    said.append(part["text"].strip())
            elif part.get("type") == "tool" and not composing(part):
                steps.append(tool_line(part))
        if info.get("error"):
            data = info["error"].get("data") or {}
            error = data.get("message") or info["error"].get("name", "error")
    return said, steps, error


def latest_of(messages):
    """What the agent wrote last while it works, on one line: (True, its thoughts) when a
    reasoning model thinks, (False, its words) when it writes, (False, "") when it took a
    step last or has written nothing yet."""
    parts = [p for p in reply_parts(messages) if p.get("type") in ("text", "reasoning", "tool")
             and not p.get("synthetic") and not p.get("ignored")]
    if not parts or parts[-1]["type"] == "tool":
        return False, ""
    return parts[-1]["type"] == "reasoning", last_sentence(parts[-1].get("text", ""))


def last_sentence(text):
    """The last sentence of `text` that is complete, on one line, or "" before the
    first is: what streams in is cut off mid-sentence, and its end changes with every
    word."""
    done = re.findall(r"[^.!?:]*[.!?:](?=\s|$)", " ".join(text.split()))
    return done[-1].strip() if done else ""


DOING = {"bash": "running a command", "read": "reading a file", "edit": "changing a file",
         "write": "writing a file", "glob": "looking for files", "grep": "searching files",
         "list": "looking at a folder", "webfetch": "reading a web page", "task": "handing part of it on",
         "todowrite": "planning", "question": "asking you"}


def doing(messages):
    """What the agent does right now, in words, while its last part is a step: the
    palette shows no commands, and "Working" alone looks stuck during a long one."""
    parts = [p for p in reply_parts(messages) if p.get("type") in ("text", "reasoning", "tool")
             and not p.get("synthetic") and not p.get("ignored")]
    if not parts or parts[-1]["type"] != "tool" or composing(parts[-1]):
        return ""
    tool = str(parts[-1].get("tool", ""))
    if tool in DOING:
        return DOING[tool]
    service = tool.split("_", 1)[0] if "_" in tool else ""
    return f"using {service}" if service else "taking a step"


def explained(p, messages):
    """What the agent said, in its own words, right before the step that asks: the
    text after the step before it. "" when it said nothing in between."""
    parts = [x for x in reply_parts(messages) if not x.get("synthetic") and not x.get("ignored")
             and (x.get("type") == "tool" or x.get("type") == "text" and x.get("text", "").strip())]
    call = (p.get("tool") or {}).get("callID")
    at = next((i for i, x in enumerate(parts) if call and x.get("type") == "tool" and x.get("callID") == call), None)
    if at is None:  # Hermes names no call: the step that asks is the last, or not stored yet
        last = parts[-1] if parts else {}
        running = last.get("type") == "tool" and (last.get("state") or {}).get("status") in ("pending", "running")
        at = len(parts) - 1 if running else len(parts)
    for x in reversed(parts[:at]):
        if x["type"] == "tool":
            return ""
        return " ".join(x["text"].split())
    return ""


def risky(p):
    """True for a command that deletes, runs as root or the like (RISKY): its row shows
    the command under the agent's words, which can be wrong (the test's thesis was
    "one stale cache file")."""
    if p.get("permission") not in ("bash", None) and not (p.get("metadata") or {}).get("command"):
        return False
    words = (p.get("metadata") or {}).get("command") or " ".join(p.get("patterns") or [])
    return any((part.split() or [""])[0] in RISKY for part in re.split(r"&&|\|\||[;|\n(){}]", words))


def replied(messages, request):
    """True when the agent has replied to `request`: it is the last request the
    conversation has (in what the agent stored of it, which may hold more), and a reply of
    the agent follows it."""
    i = last_user(messages)
    if i < 0 or not any(m["info"].get("role") == "assistant" for m in messages[i + 1:]):
        return False
    said = " ".join(p.get("text", "") for p in messages[i].get("parts", [])
                    if p.get("type") == "text" and not p.get("synthetic"))
    return request.strip() in said


MORE = "… Show what the agent does has the rest."


def shorten(text, most=MAX_ANSWER):
    """The start of an answer KRunner can show, cut at a line's or a word's end: an
    answer starts with what matters, and a list cut at its head is no list."""
    if len(text) <= most:
        return text
    start = text[:most]
    cut = max(start.rfind("\n"), start.rfind(" "))
    return (start[:cut] if cut > most // 2 else start).rstrip() + "\n" + MORE


def shorten_end(text, most):
    """The start of `text`, at most `most` characters."""
    return text if len(text) <= most else text[:most - 1] + "…"


def head(text, most=MAX_LATEST):
    """The start of `text`, at most `most` characters, cut at a word's end."""
    if len(text) <= most:
        return text
    start = text[:most - 1]
    return (start.rsplit(" ", 1)[0] if " " in start else start).rstrip(",;: ") + "…"


def seconds(elapsed):
    """How long the agent has been at it, as the palette says it."""
    s = int(elapsed)
    return f"{s} s" if s < 60 else f"{s // 60} min {s % 60} s"


def markup(text):
    """Text as KRunner's styled text (HTML), where `<x ...>` would vanish and a line break
    would be a space."""
    return html.escape(text, quote=False).replace("\n", "<br>")


def rich(text):
    """An answer as KRunner's styled text: escaped, and the Markdown a model writes
    whatever it is told, read as far as a row can show it: **bold**, a heading as a bold
    line, a list's dash as a bullet, a table's row with · between its cells, and code
    without its backticks and fences."""
    lines = []
    for line in text.split("\n"):
        bare = line.strip()
        if bare.startswith("```") or re.fullmatch(r"\|?[\s:|-]*-[\s:|-]*\|?", bare) and "|" in bare:
            continue  # a fence, or the rule under a table's head
        if bare.startswith("|") and bare.endswith("|") and len(bare) > 1:
            line = " · ".join(cell.strip() for cell in bare.strip("|").split("|"))
        line = html.escape(line, quote=False).replace("`", "")
        heading = re.match(r"\s*#{1,6}\s+(.*)", line)
        if heading:
            line = "**" + heading.group(1).replace("**", "") + "**"
        line = re.sub(r"^(\s*)[-*]\s+", "\\1• ", line)
        lines.append(re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", line))
    return "<br>".join(lines)


def match(match_id, text, icon=ICON, relevance=1.0, subtext="", actions=(), multiline=False,
          category=CATEGORY_RELEVANCE, styled=False):
    """One match. KRunner shows a multiline one as styled text, so its text goes as
    escaped HTML, unless it is that already (`styled`)."""
    if multiline and not styled:
        text = markup(text)
    return (match_id, text, icon, relevance,
            {"subtext": subtext, "actions": list(actions), "multiline": multiline, "category": category})


def tool_input(p, messages):
    """What the tool call behind a permission request was given, from the conversation:
    opencode names the call (`tool.callID`) but sends a service's tool as `*`."""
    call = (p.get("tool") or {}).get("callID")
    if not call:
        return {}
    for message in messages:
        for part in message.get("parts", []):
            if part.get("type") == "tool" and part.get("callID") == call:
                given = (part.get("state") or {}).get("input")
                return given if isinstance(given, dict) else {}
    return {}


def changed_lines(diff, most=6):
    """The lines a unified diff adds and takes away, the first `most` of them."""
    lines = [line for line in (diff or "").splitlines()
             if line[:1] in "+-" and not line.startswith(("+++", "---"))]
    shown = [shorten_end(line, 110) for line in lines[:most]]
    if len(lines) > most:
        shown.append(f"… {len(lines) - most} more changed lines")
    return shown


def asked_for(p, messages=()):
    """What a permission request would do: opencode's whole command for bash (its
    patterns are the command cut at pipes, `&&` and `;`); for a change to a file, its
    whole path and the lines that change; for a service's tool, what it is given;
    else the patterns."""
    meta = p.get("metadata") or {}
    command = meta.get("command")
    if isinstance(command, str) and command.strip():
        return command
    if p.get("permission") == "edit" and meta.get("filepath"):
        return "\n".join([str(meta["filepath"])] + changed_lines(meta.get("diff")))
    patterns = p.get("patterns") or []
    if patterns in ([], ["*"]):
        given = tool_input(p, messages)
        told = ", ".join(f"{k}={v}" for k, v in given.items() if isinstance(v, (str, int, float, bool)))
        if told:
            return shorten_end(told, 300)
    return ", ".join(patterns) or p.get("permission", "")


def asks(p):
    """How a row names what is asked for: opencode's word for it, or plain words."""
    kind = p.get("permission", "this")
    return {"external_directory": "access outside your home folder", "edit": "a change to"}.get(kind, kind)


def always_offered(p):
    """False when Always would let through far more than what asks: a change to any
    file, any bash command, or commands that start with sudo, rm, a shell and the like
    (RISKY). Such a row offers Allow once and Deny. opencode says what its rule would
    be (`always`); Hermes does not, so there the command itself decides."""
    rules = p.get("always")
    if rules is None:
        # Hermes names no rule. Its Always is kept for good, for every command of the
        # kind that asked ("sudo with privilege flag": every sudo), so what counts is
        # the command: each of its parts, cut where a shell would cut it.
        commands = re.split(r"&&|\|\||[;|\n]", " ".join(p.get("patterns") or []))
        return not any((part.split() or [""])[0] in RISKY for part in commands)
    if p.get("permission") == "edit" or (p.get("permission") == "bash" and "*" in rules):
        return False
    return not any((rule.split() or [""])[0] in RISKY for rule in rules)


def view(state, elapsed=None, agent="", stop=False):
    """The matches of the conversation (`kortex ›`), as a chat shows it: what the agent
    asks to do, in its own words, and what it asks the user; while it works, a status
    row (how long it has been at it, `elapsed` seconds or None, and what it wrote or
    thought or does last); then its answer. The steps and their commands are the
    panel's (Show what the agent does), but for a risky command a row asks for. The
    status row is one line, which KRunner gives a fixed height, so its window holds
    still. `stop`: the agent's work can be stopped, so the status row offers it."""
    said, steps, error = reply_of(state["messages"])
    matches = []
    for p in state["permissions"]:
        if p["id"] == UNREADABLE["id"]:
            matches.append(match("stuck", "The agent waits for an answer kortex cannot show (an opencode bug)",
                                 "dialog-warning", 1.0, "Clears when opencode restarts"))
            continue
        kind = p.get("permission", "this")
        what = asked_for(p, state["messages"])
        words = explained(p, state["messages"])
        if words:
            # The agent's own words, as in a chat; the command only where a wrong
            # explanation would cost the most. The panel shows every step.
            text = "Allow: " + shorten_end(words, 240)
            if risky(p):
                text += "\n" + what
        else:  # it said nothing first: the command, not a blind Allow
            text = f"Allow {asks(p)}" + ("" if what == "*" else f": {what}")  # a tool that is given nothing
        always = always_offered(p)
        if p.get("always") and always:  # opencode's rule for Always: `du *` lets any du through
            # opencode keeps the rule for every conversation, until its server stops
            text += "\nAlways allows, in every conversation: " + ("any " + kind if p["always"] == ["*"] else ", ".join(p["always"]))
        matches.append(match(
            "permission" + SEP + p["id"], text, "dialog-warning", 1.0, "Asks first",
            ["allow-once"] + (["allow-always"] if always else []) + ["deny"], True))  # all of it, never elided
    questions = state.get("questions") or []
    if questions:
        q = questions[0]
        text = f"{NAME} asks: {q['question']}"
        if not q["options"]:
            text += "\nType your answer after " + f"{NAME} {VIEW_MARK}" + " and press Enter"
        matches.append(match("question" + SEP + q["id"], text, "dialog-question", 0.99,
                             "Pick an answer below, or type one", (), True))
        for i, label in enumerate(q["options"]):
            matches.append(match(SEP.join(("option", q["id"], str(q["index"]), label)), label, "dialog-ok-apply",
                                 0.98 - i * 0.001, "Enter answers with this"))
    status, retry = state["status"]
    working = status != "idle"
    if error:
        if error == "Aborted":
            error = "Stopped. Say what to do instead."
        matches.append(match("answer", error, "dialog-error", 0.95, "The agent stopped", ["terminal"], True))
    elif working:
        if state["permissions"]:
            # holds still while a row asks, to be clicked; KRunner runs a row's first
            # action, Allow once, on Shift+Enter, and says so only in a tooltip
            line = "Waiting for your answer · Shift+Enter allows once"
        elif questions:
            line = "Waiting for your answer"
        elif status == "retry":
            line = f"Retrying: {retry}"
        else:
            thinking, words = latest_of(state["messages"])
            line = ("Thinking" if thinking else "Working") + (f" · {seconds(elapsed)}" if elapsed is not None else "")
            words = words or doing(state["messages"])
            if words:
                line += " · " + head(words)
        matches.append(match("answer", line, ICON, 0.9, agent, ["copy", "terminal"] + (["stop"] if stop else [])))
    elif said or steps:
        took = f" · {len(steps)} step{'s' if len(steps) > 1 else ''}" if steps else ""
        matches.append(match("answer", rich(shorten("\n".join(answer_of(state["messages"])))), ICON, 0.9,
                             (agent + " · " if agent else "") + "Enter copies" + took, ["copy", "terminal"], True,
                             styled=True))
    return matches


def ago(ms):
    minutes = int((time.time() - ms / 1000) // 60)
    if minutes < 1:
        return "just now"
    if minutes < 60:
        return f"{minutes} min ago"
    if minutes < 48 * 60:
        return f"{minutes // 60} h ago"
    return f"{minutes // (24 * 60)} days ago"


def terminal_command(attach, terminal):
    """The terminal command that runs `attach`, the agent's own interface on a session."""
    words = shlex.split(terminal) or ["konsole"]
    if Path(words[0]).name.startswith("kitty"):
        return words + attach  # kitty takes the program as it is
    return words + ["-e"] + attach


# ---- the runner ------------------------------------------------------------------------


def at_once(work, then):
    """Run `work` and hand `then` its result and error: how the tests run kortex's
    requests to the agent; main() runs them in a thread of their own."""
    try:
        result = work()
    except AgentError as e:
        then(None, e)
    else:
        then(result, None)


class Kortex:
    """What KRunner's calls do, without D-Bus: the D-Bus service in main() forwards to
    it, and the tests drive it directly. `agent` is the agent's server (Opencode or
    Hermes); `desktop` reopens KRunner with a query, copies, starts programs and
    notifies; `now` is a clock; `background(work, then)` runs requests to the agent.

    KRunner waits for nothing here: every request to the agent goes to `background`,
    and a match shows what the agent said last. A request still on its way, or an agent
    that starts (Hermes can take a minute), shows as a waiting row, and the tick shows
    KRunner again when there is news. Only `background`'s jobs call the agent, and only
    they set `session` while a request is sent, in the order they were made.

    The request the user made last is followed until the agent has replied (`asked_at`,
    `request`): the palette says how long it has been at it, and while KRunner is closed
    one notification says that the agent works on it, then what it answered."""

    def __init__(self, agent, desktop, now=time.monotonic, background=at_once):
        self.agent = agent
        self.desktop = desktop
        self.now = now
        self.background = background
        self.session = None  # the conversation `kortex ›` shows
        self.state = None  # (session, what the agent said of it last)
        self.sessions = None  # the recent conversations, as the agent listed them last
        self.down = False  # the last request found no server, or none that answered
        self.unreachable = ""  # why the agent did not answer the last request
        self.waiting = ""  # a request on its way to the agent
        self.jobs = 0  # requests in the background
        self.fetching = set()  # what is being fetched, once at a time
        self.shown = None  # the query of kortex's matches KRunner shows, or None
        self.heard = 0  # KRunner's calls so far; a refresh decided before the latest is dropped
        self.view_query = self.opening
        self.closing_at = None  # when a run closed KRunner that `reopen` reopens
        self.signature = None  # what KRunner was shown, to compare with what is known now
        self.notified = set()  # the permission requests and questions told of in a notification
        self.answers = {}  # the answers picked so far to a request of several questions, by its id
        self.put_off = False  # told once that the provider puts the request off
        self.logged = set()  # and in the journal
        self.problem = ""
        self.last_activity = now()
        self.request = ""  # the request followed until the agent has replied to it
        self.asked_at = None  # when it was made; None once the agent has replied
        self.told = 0  # the notification that follows it, once there is one
        self.followed = False  # KRunner showed the news of it once (logged once)
        self.shows = set()  # the notifications whose Show opens the conversation

    @property
    def marker(self):
        """The query that shows the current conversation: kortex's name and ›."""
        return f"{NAME} {VIEW_MARK}"

    @property
    def opening(self):
        """What kortex puts in KRunner's field to show the conversation: the marker and a
        space, so what is typed next is a follow-up as it stands. KRunner does not ask
        again for a space typed at the end, so a space of the user's there would be lost
        to the next refresh."""
        return self.marker + " "

    def parse(self, query):
        return parse(query, self.agent.title)

    @property
    def viewing(self):
        """KRunner shows the conversation right now."""
        return self.shown is not None and self.parse(self.shown)[0] == "view"

    def elapsed(self):
        """Seconds since the request followed was made, while the agent is at it and asks
        for nothing (a waiting Allow row must hold still to be clicked), else None."""
        state = self.current() or {}
        if self.asked_at is None or state.get("permissions") or state.get("questions"):
            return None
        return int(self.now() - self.asked_at)

    def work(self, job, then, key=None):
        """Run `job`, requests to the agent, in the background; `then(result, error)` gets
        what it brought. A job with a key runs once at a time."""
        if key in self.fetching:
            return
        if key:
            self.fetching.add(key)
        self.jobs += 1

        def done(result, error):
            self.jobs -= 1
            self.fetching.discard(key)
            if error:
                log(error)
            then(result, error)

        self.background(job, done)

    def failed(self, _result, error):
        if error:
            self.problem = str(error)

    def config(self, menu=False):
        # Each program that searches asks for this once. Both send every query, so any
        # search can go to the agent; KRunner from one letter, so kortex hears the first
        # one typed over the conversation and no refresh puts the conversation back.
        return {"MinLetterCount": MIN_SEARCH if menu else 1}

    def match(self, query, menu=False):
        kind, text = self.parse(query)
        if not menu:
            self.heard += 1
        if kind is None:
            if not menu:
                self.shown = None  # another search: nothing of kortex's to keep current
            return self.anything(query)
        self.last_activity = self.now()
        if menu:
            if kind == "ask":
                return self.ask_matches(text)
            return self.list_matches(menu) if kind == "list" else self.menu_view_matches(text)
        if JOINER in query and not self.followed and self.asked_at is not None:
            self.followed = True
            log("KRunner asked again: the palette follows the conversation")
        self.shown = query.strip() if kind != "ask" else None
        if kind == "ask":
            matches = self.ask_matches(text)
        elif kind == "list":
            matches = self.list_matches()
        else:
            self.view_query = self.shown if text else self.opening
            matches = self.view_matches(text)
        self.signature = self.seen()
        return matches

    def ask_matches(self, text):
        if not text:
            return [self.hint()]
        matches = [match("ask" + SEP + text, text, ICON, 1.0, f"Ask {NAME} · Enter")]
        if self.session:
            matches.append(match("continue" + SEP + text, text, ICON, 0.9, "This conversation"))
        return matches

    def list_matches(self, menu=False):
        def listed(found, error):
            self.unreachable = str(error) if error else ""
            if found is not None:
                self.sessions = found

        self.work(lambda: self.agent.sessions(5), listed, "sessions")
        if self.unreachable:
            return [self.unreachable_match()]
        matches = [self.hint()]
        if self.sessions is None and not menu:  # the start menu does not show it again
            matches.append(self.waiting_match())
        for i, s in enumerate(self.sessions or []):
            matches.append(match(
                "session" + SEP + s["id"], s.get("title") or "Conversation", "view-history", 0.8 - i * 0.1,
                "Continue · " + ago(s.get("time", {}).get("updated", 0))))
        return matches

    def hint(self):
        # The one place outside the conversation that names the agent behind kortex.
        return match("hint", f"Ask {NAME}: type your request", ICON, 1.0, f"{self.agent.title} answers")

    def waiting_match(self):
        elapsed = self.elapsed() if self.waiting else None
        return match("waiting", "Waiting for the agent…" + (f" {seconds(elapsed)}" if elapsed else ""), ICON, 0.9,
                     "Starts with the first request")

    def unreachable_match(self):
        return match("problem", "The agent is not reachable", "dialog-error", 1.0, self.unreachable)

    def anything(self, query):
        """Any search, offered to the agent ("Ask kortex: …" reads as something to
        press): below what Plasma finds, or first when it reads as a question or a
        request, with a conversation to continue then too."""
        text = query.replace(JOINER, "").strip()
        if len(text) < MIN_SEARCH:
            return []
        if not asking(text):
            return [match("ask" + SEP + text, f"Ask {NAME}: {text}", ICON, 0.1,
                          "Search, ask, fix", category=CATEGORY_LOWEST)]
        matches = [match("ask" + SEP + text, f"Ask {NAME}: {text}", ICON, 1.0,
                         "New conversation · Enter")]
        if self.session:
            matches.append(match("continue" + SEP + text, f"Continue with {NAME}: {text}", ICON, 0.9,
                                 "This conversation"))
        return matches

    def menu_view_matches(self, followup):
        """The conversation (`kortex ›`) in the start menu, which shows neither the
        answer nor its actions: the conversation opens in KRunner instead."""
        matches = []
        if followup:
            matches.append(match("continue" + SEP + followup, followup, ICON, 1.0, "Continue the conversation · Enter"))
        if self.session:
            matches.append(match("show", "The current conversation", ICON, 0.9, "Opens in the command palette · Enter"))
        return matches or [self.hint()]

    def view_matches(self, followup):
        matches = []
        asked = (self.current() or {}).get("questions") or []
        if followup and asked:
            # What is typed while the agent asks is the answer to it, not a new request.
            q = asked[0]
            matches.append(match(SEP.join(("option", q["id"], str(q["index"]), followup)), followup, ICON, 1.0,
                                 "Your answer · Enter"))
        elif followup:
            matches.append(match("continue" + SEP + followup, followup, ICON, 1.0, "Continue · Enter"))
        if self.problem:
            matches.append(match("problem", self.problem, "dialog-error", 0.95, "Not sent"))
        if self.waiting:
            return matches + [self.waiting_match()]
        if not self.session:
            return matches or [self.hint()]
        self.refresh()
        if self.unreachable:
            return matches + [self.unreachable_match()]
        state = self.current()
        if not state:
            return matches + [self.waiting_match()]
        return matches + view(state, self.elapsed(), self.agent.title, hasattr(self.agent, "abort"))

    def current(self):
        """What the agent said last of the conversation `kortex ›` shows, or None."""
        if self.state and self.state[0] == self.session:
            return self.state[1]
        return None

    def seen(self):
        """What KRunner's matches are made of, to compare with what it was shown."""
        if self.shown is None:
            return None
        if self.parse(self.shown)[0] == "list":
            return repr((self.sessions, self.unreachable))
        return repr((self.session, self.current(), self.problem, self.waiting, self.unreachable, self.elapsed()))

    def refresh(self, start=True):
        """Ask the agent for the conversation in the background; the tick shows what
        changed. A stopped server starts again for KRunner's request (`start`), not for
        the tick's."""
        session = self.session
        if not session:
            return

        def fetch():
            if not start and (not self.agent.url or self.agent.stopped()):
                return None
            self.agent.add_services()
            return {
                "messages": self.agent.messages(session),
                "status": self.agent.status(session),
                "permissions": self.agent.permissions(session),
                "questions": self.unanswered(self.agent.questions(session)),
            }

        def fetched(state, error):
            self.down = error is not None or state is None
            self.unreachable = str(error) if error else ""
            if state is not None:
                self.state = (session, state)
            elif self.asked_at is not None and not self.waiting and self.agent.stopped():
                # The agent's server exited while it worked on the request: nothing more
                # comes, and "working" would stand until kortex stops.
                self.state = None
                self.unreachable = f"{self.agent.title} stopped while it worked; ask again"
                self.finish(f"{NAME} stopped", self.unreachable)

        self.work(fetch, fetched, "state")

    def unanswered(self, questions):
        """The questions still to answer: without those of a request of several whose
        answer was picked already and waits for the others."""
        return [q for q in questions if q["index"] not in self.answers.get(q["id"], {})]

    def run(self, match_id, action, menu=False):
        kind, _, arg = match_id.partition(SEP)
        self.last_activity = self.now()
        if kind in ("ask", "continue"):
            self.problem = ""
            self.waiting = arg
            self.request, self.asked_at, self.told, self.followed = arg, self.now(), 0, False
            self.put_off = False
            log(f"asking {self.agent.title}" + (" in the conversation" if kind == "continue" else ""))

            def send():
                if kind == "ask" or not self.session:
                    self.session = self.agent.new_session(arg[:60])
                self.agent.add_services()
                self.agent.ask(self.session, arg)

            def sent(_result, error):
                self.waiting = ""
                if not error:
                    # What is known of the conversation is from before this request: a
                    # follow-up that repeats the last request would read as replied to.
                    self.state = None
                self.failed(None, error)
                if error:
                    self.finish(f"{NAME} did not get the request", str(error))

            self.reopen(self.opening, menu)
            self.work(send, sent)
        elif kind == "session":
            if arg != self.session:
                self.asked_at = None  # the request followed was made in another conversation
            self.session = arg
            self.reopen(self.opening, menu)
        elif kind == "show":
            self.reopen(self.opening, menu)
        elif kind == "permission":
            state = self.current()
            asked = next((p for p in (state or {}).get("permissions", []) if p["id"] == arg), None)
            if action == "allow-always" and asked and not always_offered(asked):
                action = ""  # not offered for this row; nothing is answered
            if action in REPLIES:
                if state:  # answered: KRunner shows it again without it
                    state["permissions"] = [p for p in state["permissions"] if p["id"] != arg]
                self.work(lambda: self.agent.reply(arg, REPLIES[action]), self.failed)
            self.reopen(self.view_query, menu)
        elif kind == "option":
            request, index, label = arg.split(SEP, 2)
            state = self.current()
            asked = [q for q in (state or {}).get("questions", []) if q["id"] == request]
            if asked:
                picked = self.answers.setdefault(request, {})
                picked[int(index)] = [label]
                state["questions"] = self.unanswered(state["questions"])
                if len(picked) >= asked[0]["of"]:  # every question of the request has its answer
                    answers = [picked[i] for i in sorted(picked)]
                    del self.answers[request]
                    self.work(lambda: self.agent.answer(request, answers), self.failed)
            self.reopen(self.opening, menu)
        elif kind == "question":
            self.reopen(self.view_query, menu)  # the question itself: its answers are the rows below
        elif kind == "answer" and action == "stop" and self.session:
            session = self.session
            self.work(lambda: self.agent.abort(session), self.failed)
            self.reopen(self.view_query, menu)
        elif kind == "answer" and self.session:
            if action == "terminal":
                self.panel()
            else:
                session = self.session
                def read(messages, error):
                    if messages is not None:
                        said, steps, stopped = reply_of(messages)
                        self.desktop.copy("\n".join(said) or stopped or "\n".join(steps))
                    self.failed(None, error)

                self.work(lambda: self.agent.messages(session), read)

    def panel(self):
        """Show what the agent does: its own interface on the conversation, in a terminal
        docked to the screen's right edge, so the user works on beside it; the agent's
        interface alone when there is no conversation (Meta+Shift+A)."""
        self.last_activity = self.now()
        session = self.session

        def opened(found, error):
            if found:
                attach, env = found
                self.desktop.dock(self.desktop.launch(terminal_command(attach, self.desktop.terminal()), env))
            self.failed(None, error)

        self.work(lambda: self.agent.attach(session), opened)

    def reopen(self, query, menu=False):
        """After a run, which closes KRunner, or the start menu (`menu`): open KRunner
        on the conversation. Only a run in KRunner is followed by KRunner's teardown."""
        self.closing_at = None if menu else self.now()
        self.desktop.query(query)

    def teardown(self, menu=False):
        # The start menu's teardown ends its search, never the conversation, which it
        # does not show. KRunner's one right after a run in it is the closing that run
        # caused, which `reopen` undoes; any other means the user closed KRunner.
        if menu:
            return
        self.heard += 1  # KRunner closed, or its field was emptied
        if self.closing_at is not None and self.now() - self.closing_at < 1.5:
            self.closing_at = None
            return
        self.closing_at = None
        self.shown = None

    def open(self):
        """Meta+A (patina's `ask` key): KRunner on kortex alone, on the conversation while
        a request runs or waits for an answer, else empty for a new request. KRunner
        calls no runner for an empty field, so it showed nothing then."""
        self.last_activity = self.now()
        state = self.current() or {}
        busy = self.asked_at is not None or self.waiting or state.get("permissions") or state.get("questions")
        self.desktop.query(self.opening if busy and self.session else "")

    def show(self, note):
        """A notification's Show: KRunner opens on the conversation."""
        if note in self.shows:
            self.desktop.query(self.opening)

    def notify(self, summary, body, replaces=0, transient=False, follows=False):
        """A notification with Show, which opens the conversation. Its body is markup to
        the notification server, as a multiline match is to KRunner. One that `follows`
        the request is the one the next replaces."""
        def sent(note):
            self.shows.add(note)
            if follows:
                self.told = note

        self.desktop.notify(summary, markup(body), replaces, transient, sent)

    def finish(self, summary, body):
        """The request followed has ended: the agent replied, or it never got it. While
        KRunner is closed a notification says how, in place of the one that followed it."""
        if self.asked_at is None:
            return
        log(f"{summary} after {seconds(self.now() - self.asked_at)}")
        if self.shown is None:
            self.notify(summary, body or "Done.", max(self.told, 0))
        self.asked_at = None

    def follow(self):
        """The request followed, while it runs: done once the agent has replied to it;
        while KRunner is closed, told of once after TELL_AFTER seconds, and of its end."""
        state = self.current()
        if self.asked_at is None or self.waiting or not state:
            return
        said, steps, error = reply_of(state["messages"])
        if replied(state["messages"], self.request) and (error or state["status"][0] == "idle"):
            if error:
                self.finish(f"{NAME} stopped", error)
            else:
                # Its last words, not the steps it took: after a task they say how it went,
                # and a plain answer is its last words anyway.
                last = (said or steps or [""])[-1]
                if answer_of(state["messages"])[-1] == DENIED:
                    last = DENIED
                self.finish(f"{NAME} answered", last if len(last) <= MAX_NOTE else last[:MAX_NOTE] + "…")
            return
        status, why = state["status"]
        if status == "retry" and "next try" in why and self.shown is None and not self.put_off:
            # A limit that lasts: said once, in a notification that stays in the history.
            self.put_off = True
            self.notify(f"{NAME} waits for the provider", f"{why}\n{self.request}", max(self.told, 0), follows=True)
            return
        if self.shown is None and not self.told and self.now() - self.asked_at >= TELL_AFTER:
            log("KRunner closed while the agent works; a notification follows the request")
            self.told = -1  # asked for; its id comes with the notification server's answer
            self.notify(f"{NAME} is working on it", f"{self.request}\nA notification follows when it answers.",
                        transient=True, follows=True)

    def tick(self):
        """Every REFRESH_MS: ask the agent for news; while KRunner shows kortex's matches
        and what they are made of changed, show them again; while it is closed, notify
        of a new permission request, and follow the request with a notification. With
        KRunner on something else and the agent done, there is nothing to ask it: the
        next look at the conversation fetches it."""
        state = self.current() or {}
        if (self.shown is not None or self.asked_at is not None or self.waiting or not state
                or state["status"][0] != "idle" or state.get("permissions") or state.get("questions")):
            self.refresh(start=False)
        for p in (self.current() or {}).get("permissions", []):
            if p["id"] not in self.logged:
                self.logged.add(p["id"])
                log(f"{self.agent.title} asks first: {p.get('permission', 'a request')}")
        if self.shown is not None:
            kind, followup = self.parse(self.shown)
            # A follow-up being typed is the user's: a refresh would put back what KRunner
            # asked for last, without the keys typed since. Its rows refresh with each
            # key, and once it runs the field holds the bare marker again.
            if not followup and self.seen() != self.signature:
                self.signature = self.seen()
                # KRunner asks again only for a query that differs from the one it shows:
                # an invisible word joiner goes after `kortex ›` or `ai`, or away again,
                # with the space after them (`ai ›` and `AI ›` become `kortex ›`).
                head = self.marker if kind == "view" else TRIGGER
                q = head + ("" if JOINER in self.shown else JOINER) + " "
                heard = self.heard
                # Sent a moment later, and only if KRunner has called kortex since: the
                # user typed or emptied the field meanwhile.
                self.desktop.query(q, lambda: self.heard == heard)
            self.follow()
            return
        self.follow()
        state = self.current() or {}
        for p in state.get("permissions", []):
            if p["id"] not in self.notified:
                self.notified.add(p["id"])
                self.notify(f"{NAME} asks first", f"{asks(p)}: {asked_for(p, state.get('messages', ()))}")
        for q in state.get("questions", []):
            if (q["id"], q["index"]) not in self.notified:
                self.notified.add((q["id"], q["index"]))
                self.notify(f"{NAME} asks you", q["question"])

    def idle(self):
        """True when kortex may stop: nothing asked for a while, nothing on its way to the
        agent and no request of the conversation running there."""
        if self.now() - self.last_activity < IDLE_EXIT or self.jobs:
            return False
        state = self.current()
        return self.down or not state or state["status"][0] == "idle"


# ---- D-Bus -----------------------------------------------------------------------------


def main():
    import dbus
    import dbus.service
    from dbus.mainloop.glib import DBusGMainLoop
    from gi.repository import GLib

    DBusGMainLoop(set_as_default=True)
    bus = dbus.SessionBus()
    loop = GLib.MainLoop()
    # When the session bus goes away (a session that ends without a signal for kortex),
    # libdbus would end kortex on the spot and leave the agent's server running.
    bus.set_exit_on_disconnect(False)
    bus.call_on_disconnection(lambda _bus: loop.quit())

    def ignore(*_):
        pass

    def logged(e):
        log(e)

    class Desktop:
        """The few things kortex asks of the desktop, over D-Bus where Plasma offers it."""

        def query(self, text, still=None):
            """Open KRunner on kortex with `text`, a moment later; a refresh passes
            `still`, which says whether it is still wanted then."""
            def call():
                if still is None or still():
                    bus.get_object("org.kde.krunner", "/App").querySingleRunner(
                        RUNNER_ID, text, dbus_interface="org.kde.krunner.App",
                        reply_handler=ignore, error_handler=logged)
                return False

            GLib.timeout_add(150, call)

        def copy(self, text):
            try:
                bus.get_object("org.kde.klipper", "/klipper").setClipboardContents(
                    text, dbus_interface="org.kde.klipper.klipper")
            except dbus.DBusException:
                if shutil.which("wl-copy"):
                    subprocess.run(["wl-copy"], input=text.encode(), check=False)

        def terminal(self):
            try:
                out = subprocess.run(
                    ["kreadconfig6", "--file", "kdeglobals", "--group", "General", "--key", "TerminalApplication"],
                    capture_output=True, text=True, check=False).stdout.strip()
            except OSError:
                out = ""
            if out:
                return out
            return next((t for t in ("konsole", "kitty", "x-terminal-emulator") if shutil.which(t)), "konsole")

        def launch(self, command, env):
            return subprocess.Popen(command, env=dict(os.environ, **env), start_new_session=True,
                                    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                    stderr=subprocess.DEVNULL).pid

        def dock(self, pid):
            """Place the window of process `pid` at the screen's right edge, full height, a
            fifth of the width (PANEL_*), above other windows: a KWin script, loaded over
            D-Bus for the moment the window takes to appear. Plasma lets no program place
            its own window on Wayland."""
            script = state_dir() / "panel.js"
            state_dir().mkdir(parents=True, exist_ok=True)
            script.write_text(PANEL_SCRIPT % (pid, PANEL_SHARE, PANEL_MIN, PANEL_MAX))
            try:
                kwin = bus.get_object("org.kde.KWin", "/Scripting")
                kwin.unloadScript("kortex-panel", dbus_interface="org.kde.kwin.Scripting")
                # loadScript is overloaded (path; path and name): the signature picks one
                number = kwin.loadScript(str(script), "kortex-panel", signature="ss",
                                         dbus_interface="org.kde.kwin.Scripting")
                bus.get_object("org.kde.KWin", f"/Scripting/Script{int(number)}").run(
                    dbus_interface="org.kde.kwin.Script")
            except (dbus.DBusException, TypeError) as e:  # no KWin, or another one
                log(f"the panel stays where the terminal put it: {e}")
                return

            def unload():
                try:
                    kwin.unloadScript("kortex-panel", dbus_interface="org.kde.kwin.Scripting")
                except dbus.DBusException:
                    pass
                return False

            GLib.timeout_add_seconds(20, unload)

        def notify(self, summary, body, replaces=0, transient=False, then=None):
            # "default" is a click on the notification, "show" its button; both open the
            # conversation (ActionInvoked below). A transient one leaves no trace in the history.
            hints = {"transient": dbus.Boolean(True)} if transient else {}
            bus.get_object("org.freedesktop.Notifications", "/org/freedesktop/Notifications").Notify(
                "kortex", dbus.UInt32(replaces), ICON, summary, body,
                dbus.Array(["default", "Show", "show", "Show"], signature="s"),
                dbus.Dictionary(hints, signature="sv"), dbus.Int32(-1),
                dbus_interface="org.freedesktop.Notifications",
                reply_handler=lambda note: then and then(int(note)), error_handler=logged)

    class Worker:
        """The thread that makes every request to the agent, one after another, so the
        D-Bus loop answers KRunner at once however long the agent takes (Hermes' gateway
        can take a minute to start). What a request brings goes back to the loop."""

        def __init__(self):
            self.jobs = queue.SimpleQueue()
            threading.Thread(target=self.serve, daemon=True).start()

        def __call__(self, work, then):
            self.jobs.put((work, then))

        def serve(self):
            while True:
                work, then = self.jobs.get()
                try:
                    result, error = work(), None
                except AgentError as e:
                    result, error = None, e
                except Exception as e:  # noqa: BLE001 - the thread must outlive any job
                    log(f"a request failed: {e!r}")
                    result, error = None, AgentError(f"kortex failed on it ({type(e).__name__}); its log says more")
                GLib.idle_add(hand_back, then, result, error)

    def hand_back(then, result, error):
        try:
            then(result, error)
        except Exception as e:  # noqa: BLE001 - a callback that raises would be called again
            log(f"after a request: {e!r}")
        return False

    chosen = agent_name()
    agent = Hermes.from_environment() if chosen == "hermes" else Opencode.from_environment()
    kortex = Kortex(agent, Desktop(), background=Worker())

    def to_dbus(m):
        match_id, text, icon, relevance, props = m
        properties = dbus.Dictionary({
            "subtext": dbus.String(props["subtext"]),
            "actions": dbus.Array(props["actions"], signature="s"),
            "multiline": dbus.Boolean(props["multiline"]),
        }, signature="sv")
        return dbus.Struct((match_id, text, icon, dbus.Int32(props["category"]), dbus.Double(relevance), properties),
                           signature="sssida{sv}")

    def from_menu(sender):
        """True when plasmashell calls, which runs the start menu (Kickoff, and Plasma's other
        launchers); KRunner and everything else get the command palette's matches."""
        try:
            return sender == bus.get_name_owner("org.kde.plasmashell")
        except dbus.DBusException:
            return False

    class Runner(dbus.service.Object):
        @dbus.service.method(IFACE, out_signature="a{sv}", sender_keyword="sender")
        def Config(self, sender=None):
            return dbus.Dictionary(kortex.config(from_menu(sender)), signature="sv")

        @dbus.service.method(IFACE, out_signature="a(sss)")
        def Actions(self):
            return ACTIONS

        @dbus.service.method(IFACE, in_signature="s", out_signature="a(sssida{sv})", sender_keyword="sender")
        def Match(self, query, sender=None):
            return dbus.Array([to_dbus(m) for m in kortex.match(str(query), from_menu(sender))],
                              signature="(sssida{sv})")

        @dbus.service.method(IFACE, in_signature="ss", sender_keyword="sender")
        def Run(self, match_id, action_id, sender=None):
            kortex.run(str(match_id), str(action_id), from_menu(sender))

        @dbus.service.method(IFACE, sender_keyword="sender")
        def Teardown(self, sender=None):
            kortex.teardown(from_menu(sender))

        @dbus.service.method(IFACE, in_signature="s")
        def SetActivationToken(self, token):
            os.environ["XDG_ACTIVATION_TOKEN"] = str(token)

        @dbus.service.method(SERVICE)
        def Open(self):
            # Not KRunner's: what Meta+A calls (patina's ask.desktop)
            kortex.open()

        @dbus.service.method(SERVICE)
        def Panel(self):
            # Not KRunner's: what Meta+Shift+A calls (patina's agent.desktop)
            kortex.panel()

    try:
        name = dbus.service.BusName(SERVICE, bus, do_not_queue=True)
    except dbus.exceptions.NameExistsException:
        log(f"{SERVICE} runs already")
        return 0
    Runner(name, OBJECT_PATH)

    def tick():
        try:
            kortex.tick()
        except Exception as e:  # noqa: BLE001 - a timer that raises is never called again
            log(f"tick: {e!r}")
        if kortex.idle():
            log("quiet for a while; stopping")
            loop.quit()
        elif agent_name() != chosen:  # `patina ai on hermes`, say: D-Bus starts a kortex for it
            log(f"the agent is {agent_name()} now; stopping")
            loop.quit()
        return True

    # A notification's Show (or a click on it): the server tells every program; kortex
    # takes those of its own notifications.
    bus.add_signal_receiver(
        lambda note, _action: kortex.show(int(note)), signal_name="ActionInvoked",
        dbus_interface="org.freedesktop.Notifications", path="/org/freedesktop/Notifications")
    GLib.timeout_add(REFRESH_MS, tick)
    for sig in (signal.SIGTERM, signal.SIGINT):
        GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, sig, lambda: loop.quit() or False)
    log(f"{VERSION} ready on {SERVICE}")
    try:
        loop.run()
    finally:
        agent.stop()
    return 0


if __name__ == "__main__":
    if sys.argv[1:] in (["--version"], ["-V"]):
        print(f"kortex {VERSION}")
        sys.exit(0)
    sys.exit(main())
