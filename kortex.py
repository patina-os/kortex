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

VERSION = "0.5.1"
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
MAX_LATEST = 36  # characters of what the agent wrote or thought last, after the seconds
MAX_STEPS = 4  # rows of steps while the agent works; the older ones fold into one row
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
    "could", "would", "should", "is", "are", "do", "does", "did", "explain", "help", "fix",
    "make", "write", "install", "set", "change", "show", "tell", "wie", "warum", "wieso",
    "weshalb", "was", "wann", "wo", "wer", "welche", "welcher", "welches", "kann",
    "kannst", "könnte", "sollte", "ist", "sind", "gibt", "erkläre", "erklär", "hilf",
    "mach", "mache", "schreib", "schreibe", "installiere", "zeig", "zeige", "ändere",
    "stell",
})

# The actions a match can offer: id, text, icon. KRunner asks for them once.
ACTIONS = [
    ("allow-once", "Allow once", "dialog-ok-apply"),
    ("allow-always", "Always allow", "media-playlist-repeat"),  # not a second check mark
    ("deny", "Deny", "dialog-cancel"),
    ("copy", "Copy the answer", "edit-copy"),
    ("terminal", "Open in terminal", "utilities-terminal"),
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
                self.request("GET", health, start=False, timeout=1)
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

    def request(self, method, path, body=None, start=True, timeout=10):
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
            raise AgentError(f"{method} {path}: {e}") from e


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
        interface; the password goes in the environment, not on the command line."""
        return [find_opencode(), "attach", self.url, "--session", session], {"OPENCODE_SERVER_PASSWORD": self.password or ""}

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

    def permissions(self, session):
        try:
            found = self.request("GET", "/permission")
        except AgentError as e:
            if http_status(e) != 400:
                raise
            with self.lock:
                found = list(self.asked.values())
            # A request of another conversation the stream did show explains the 400.
            return [p for p in found if p.get("sessionID") == session] or ([] if found else [UNREADABLE])
        return [p for p in found if p.get("sessionID") == session]

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
        return [find_hermes(), "--resume", session], {}

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
        return self.request("POST", "/api/sessions", {"title": title})["session"]["id"]

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
            if http_status(e) != 404:  # 404: the run is over, nothing waits for the answer
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
            asked[event["request_id"]] = {
                "id": event["request_id"], "sessionID": session, "run": run,
                "permission": event.get("description") or "a command", "patterns": [event.get("command", "")]}
        elif kind == "approval.responded":
            asked.pop(event.get("request_id"), None)
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
    request (ASKING)."""
    words = text.lower().split()
    return text.rstrip().endswith("?") or (len(words) >= 3 and words[0].strip(",.:!") in ASKING)


def tool_line(part):
    """A step the agent took, on one line: the tool and what it was given."""
    state = part.get("state") or {}
    given = state.get("input") or {}
    detail = given.get("command") or given.get("filePath") or given.get("path") or given.get("url") or state.get("title") or ""
    line = " ".join(f"{part.get('tool', 'tool')} {detail}".splitlines()).rstrip()  # a heredoc's too
    if state.get("status") == "error":
        line += " (failed)"
    return line


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
            elif part.get("type") == "tool":
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
    return parts[-1]["type"] == "reasoning", " ".join(parts[-1].get("text", "").split())


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


def shorten(text, most=MAX_ANSWER):
    return text if len(text) <= most else "…" + text[-most:]


def tail(text, most=MAX_LATEST):
    """The end of `text`, at most `most` characters from a word's start on."""
    if len(text) <= most:
        return text
    end = text[-most:]
    return "…" + (end.split(" ", 1)[1] if " " in end[1:] else end)


def steps_summary(steps):
    """The steps the agent took, on one line: `3 steps: bash ×2, read`."""
    used = {}
    for step in steps:
        tool = step.split(" ", 1)[0]
        used[tool] = used.get(tool, 0) + 1
    tools = ", ".join(f"{t} ×{n}" if n > 1 else t for t, n in used.items())
    return f"{len(steps)} step{'s' if len(steps) > 1 else ''}: {tools}"


def seconds(elapsed):
    """How long the agent has been at it, as the palette says it."""
    s = int(elapsed)
    return f"{s} s" if s < 60 else f"{s // 60} min {s % 60} s"


def markup(text):
    """Text as KRunner's styled text (HTML), where `<x ...>` would vanish and a line break
    would be a space."""
    return html.escape(text, quote=False).replace("\n", "<br>")


def match(match_id, text, icon=ICON, relevance=1.0, subtext="", actions=(), multiline=False,
          category=CATEGORY_RELEVANCE, styled=False):
    """One match. KRunner shows a multiline one as styled text, so its text goes as
    escaped HTML, unless it is that already (`styled`)."""
    if multiline and not styled:
        text = markup(text)
    return (match_id, text, icon, relevance,
            {"subtext": subtext, "actions": list(actions), "multiline": multiline, "category": category})


def asked_for(p):
    """What a permission request would do: opencode's whole command for bash (its
    patterns are the command cut at pipes, `&&` and `;`), else the patterns."""
    command = (p.get("metadata") or {}).get("command")
    if isinstance(command, str) and command.strip():
        return command
    return ", ".join(p.get("patterns") or []) or p.get("permission", "")


def view(state, elapsed=None, agent=""):
    """The matches of the conversation (`kortex ›`): what the agent asks to do first;
    while it works, a status row (how long it has been at it, `elapsed` seconds or None,
    and what it wrote or thought last) and a row for each of its last MAX_STEPS steps;
    then its answer and its steps on one row. All of them but an answer and a row that
    asks are one line, which KRunner gives a fixed height: its window grows by a row
    for a step, never by a line for a word."""
    said, steps, error = reply_of(state["messages"])
    matches = []
    for p in state["permissions"]:
        if p["id"] == UNREADABLE["id"]:
            matches.append(match("stuck", "The agent waits for an answer kortex cannot show (an opencode bug)",
                                 "dialog-warning", 1.0, "Asked before kortex was listening; it clears when opencode restarts"))
            continue
        kind = p.get("permission", "this")
        text = f"Allow {kind}: {asked_for(p)}"
        if p.get("always"):  # opencode's rule for Always: `sh *` lets any script through
            # opencode keeps the rule for every conversation, until its server stops
            text += "\nAlways allows, in every conversation: " + ("any " + kind if p["always"] == ["*"] else ", ".join(p["always"]))
        matches.append(match(
            "permission" + SEP + p["id"], text, "dialog-warning", 1.0,
            "The agent asks before it does this", ["allow-once", "allow-always", "deny"], True))  # all of it, never elided
    status, retry = state["status"]
    working = status != "idle"
    hint = (agent + " · " if agent else "") + "Copy · Open in terminal"
    if error:
        matches.append(match("answer", error, "dialog-error", 0.95, "The agent stopped", ["terminal"], True))
    elif working:
        if state["permissions"]:
            # holds still while a row asks, to be clicked; KRunner runs a row's first
            # action, Allow once, on Shift+Enter, and says so only in a tooltip
            line = "Waiting for your answer · Shift+Enter allows once"
        elif status == "retry":
            line = f"Retrying: {retry}"
        else:
            thinking, words = latest_of(state["messages"])
            line = ("Thinking" if thinking else "Working") + (f" · {seconds(elapsed)}" if elapsed is not None else "")
            if words:
                line += " · " + tail(words)
        matches.append(match("answer", line, ICON, 0.9, hint, ["copy", "terminal"]))
    elif said or steps:
        matches.append(match("answer", markup(shorten("\n".join(said) or "Done.")), ICON, 0.9, hint,
                             ["copy", "terminal"], True, styled=True))
    if steps and working and not error:
        shown = steps if len(steps) <= MAX_STEPS else steps[-(MAX_STEPS - 1):]
        if len(shown) < len(steps):
            earlier = len(steps) - len(shown)
            matches.append(match("step" + SEP + "earlier", f"+{earlier} earlier step{'s' if earlier > 1 else ''}",
                                 "utilities-terminal", 0.85, "Open in terminal"))
        first = len(steps) - len(shown)
        for i, step in enumerate(shown):  # in the order taken
            matches.append(match("step" + SEP + str(first + i), step, "utilities-terminal", 0.84 - i * 0.001,
                                 "Open in terminal"))
    elif steps:
        matches.append(match("step" + SEP + "all", steps_summary(steps), "utilities-terminal", 0.85,
                             "Open in terminal to see them"))
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
        self.notified = set()  # the permission requests told of in a notification
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
        if self.asked_at is None or (self.current() or {}).get("permissions"):
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
            matches.append(match("continue" + SEP + text, text, ICON, 0.9, "Continue the conversation"))
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
                "Continue this conversation · " + ago(s.get("time", {}).get("updated", 0))))
        return matches

    def hint(self):
        # The one place outside the conversation that names the agent behind kortex.
        return match("hint", f"Ask {NAME}: type your request", ICON, 1.0,
                     f"{self.agent.title} answers · {self.marker} shows the current conversation")

    def waiting_match(self):
        elapsed = self.elapsed() if self.waiting else None
        return match("waiting", "Waiting for the agent…" + (f" {seconds(elapsed)}" if elapsed else ""), ICON, 0.9,
                     "It starts with the first request, which can take a minute")

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
                          f"Search, ask, fix · {NAME} answers in the command palette", category=CATEGORY_LOWEST)]
        matches = [match("ask" + SEP + text, f"Ask {NAME}: {text}", ICON, 1.0,
                         f"A new conversation · {NAME} answers in the command palette · Enter")]
        if self.session:
            matches.append(match("continue" + SEP + text, f"Continue with {NAME}: {text}", ICON, 0.9,
                                 "The current conversation"))
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
        if followup:
            matches.append(match("continue" + SEP + followup, followup, ICON, 1.0, "Continue the conversation · Enter"))
        if self.problem:
            matches.append(match("problem", self.problem, "dialog-error", 0.95, "The request did not reach the agent"))
        if self.waiting:
            return matches + [self.waiting_match()]
        if not self.session:
            return matches or [self.hint()]
        self.refresh()
        if self.unreachable:
            return matches + [self.unreachable_match()]
        state = self.current()
        return matches + (view(state, self.elapsed(), self.agent.title) if state else [self.waiting_match()])

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
            }

        def fetched(state, error):
            self.down = error is not None or state is None
            self.unreachable = str(error) if error else ""
            if state is not None:
                self.state = (session, state)

        self.work(fetch, fetched, "state")

    def run(self, match_id, action, menu=False):
        kind, _, arg = match_id.partition(SEP)
        self.last_activity = self.now()
        if kind in ("ask", "continue"):
            self.problem = ""
            self.waiting = arg
            self.request, self.asked_at, self.told, self.followed = arg, self.now(), 0, False
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
            if action in REPLIES:
                state = self.current()
                if state:  # answered: KRunner shows it again without it
                    state["permissions"] = [p for p in state["permissions"] if p["id"] != arg]
                self.work(lambda: self.agent.reply(arg, REPLIES[action]), self.failed)
            self.reopen(self.view_query, menu)
        elif kind in ("answer", "step") and self.session:
            session = self.session
            if action == "terminal" or kind == "step":  # a step: the terminal shows it
                def opened(found, error):
                    if found:
                        attach, env = found
                        self.desktop.launch(terminal_command(attach, self.desktop.terminal()), env)
                    self.failed(None, error)

                self.work(lambda: self.agent.attach(session), opened)
            else:
                def read(messages, error):
                    if messages is not None:
                        said, steps, stopped = reply_of(messages)
                        self.desktop.copy("\n".join(said) or stopped or "\n".join(steps))
                    self.failed(None, error)

                self.work(lambda: self.agent.messages(session), read)

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
                self.finish(f"{NAME} answered", last if len(last) <= MAX_NOTE else last[:MAX_NOTE] + "…")
            return
        if self.shown is None and not self.told and self.now() - self.asked_at >= TELL_AFTER:
            log("KRunner closed while the agent works; a notification follows the request")
            self.told = -1  # asked for; its id comes with the notification server's answer
            self.notify(f"{NAME} is working on it", f"{self.request}\nA notification follows when it answers.",
                        transient=True, follows=True)

    def tick(self):
        """Every REFRESH_MS: ask the agent for news; while KRunner shows kortex's matches
        and what they are made of changed, show them again; while it is closed, notify
        of a new permission request, and follow the request with a notification."""
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
        for p in (self.current() or {}).get("permissions", []):
            if p["id"] not in self.notified:
                self.notified.add(p["id"])
                self.notify(f"{NAME} asks first", f"{p.get('permission', '')}: {asked_for(p)}")

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
            subprocess.Popen(command, env=dict(os.environ, **env), start_new_session=True,
                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

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
                    result, error = None, AgentError(repr(e))
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
