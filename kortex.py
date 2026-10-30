#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""kortex: the AI agent in KDE Plasma's command palette.

A KRunner D-Bus runner (org.kde.krunner1). In KRunner, `ai <request>` asks the agent,
`ai` alone lists the recent conversations and `ai ›` shows the current one: the
answer as it grows, and every action the agent wants to take as a match of its own
with Allow once, Always and Deny. The agent is opencode: kortex starts `opencode
serve` on 127.0.0.1 when it is first needed and talks to its HTTP API.

D-Bus activation starts kortex (see install.sh); it stops itself and the server after
a quiet quarter of an hour.
"""

import base64
import http.client
import json
import os
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

VERSION = "0.1.3"
SERVICE = "io.github.patina_os.kortex"
OBJECT_PATH = "/runner"
IFACE = "org.kde.krunner1"
RUNNER_ID = "kortex"  # X-KDE-PluginInfo-Name in kortex.desktop
TRIGGER = "ai"
VIEW = "ai ›"  # the query that shows the current conversation
# KRunner asks again only for a query that differs from the one it shows, so a refresh
# puts an invisible word joiner after the marker, or takes it away again.
JOINER = "\u2060"
ICON = "kortex"
SEP = "\x1f"  # between the kind of a match and its argument in a match id
MAX_ANSWER = 1500  # characters of an answer KRunner shows; the terminal has the rest
REFRESH_MS = 700
IDLE_EXIT = 15 * 60  # seconds without a request before kortex and its server stop
CATEGORY_RELEVANCE = 100  # KRunner::QueryMatch::CategoryRelevance::Highest

# The actions a match can offer: id, text, icon. KRunner asks for them once.
ACTIONS = [
    ("allow-once", "Allow once", "dialog-ok-apply"),
    ("allow-always", "Always allow", "dialog-ok"),
    ("deny", "Deny", "dialog-cancel"),
    ("copy", "Copy the answer", "edit-copy"),
    ("terminal", "Open in terminal", "utilities-terminal"),
]
REPLIES = {"allow-once": "once", "allow-always": "always", "deny": "reject"}
# opencode 1.19 answers GET /permission with a 400 for the whole list while one request in
# it lacks an optional field (a webfetch without a timeout). kortex then takes the requests
# from opencode's event stream; one it did not see there is shown as waiting, unanswerable.
UNREADABLE = {"id": "unreadable", "permission": "a request kortex cannot show"}


def log(*words):
    print("kortex:", *words, file=sys.stderr, flush=True)


class OpencodeError(Exception):
    """opencode is missing, did not start, or did not answer."""


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
        raise OpencodeError("opencode is not installed (https://opencode.ai)")
    return found


def note_event(asked, line):
    """Keep `asked`, the pending permission requests by id, current with one line of
    opencode's event stream (`data: {"type": ..., "properties": ...}`)."""
    if not line.startswith("data:"):
        return
    try:
        event = json.loads(line[5:])
        if event["type"] == "permission.asked":
            asked[event["properties"]["id"]] = event["properties"]
        elif event["type"] == "permission.replied":
            asked.pop(event["properties"]["requestID"], None)
    except (ValueError, LookupError, TypeError):
        pass  # not an event kortex follows


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Opencode:
    """opencode's HTTP server. Started by kortex on first use unless KORTEX_OPENCODE_URL
    names one that runs already; then that one is used and never stopped."""

    def __init__(self, url=None, password=None):
        self.url = url
        self.password = password
        self.process = None
        # Never through a proxy: the server is on this machine.
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        self.asked = {}  # permission requests from the event stream, by id; under self.lock
        self.lock = threading.Lock()
        self.listened = None  # the server the event stream is followed for
        self.config_seen = None  # opencode_config()'s mtime as the server in use read it

    @classmethod
    def from_environment(cls):
        url = os.environ.get("KORTEX_OPENCODE_URL")
        if url:
            return cls(url.rstrip("/"), os.environ.get("KORTEX_OPENCODE_PASSWORD"))
        return cls(None, server_password())

    def owned(self):
        return self.process is not None

    def stopped(self):
        """True when the server kortex started has exited since."""
        return self.process is not None and self.process.poll() is not None

    def start(self):
        if self.url and (self.process is None or self.process.poll() is None):
            self.listen()
            return
        command = find_opencode()
        port = free_port()
        self.config_seen = mtime(opencode_config())  # the new server reads it as it is now
        state_dir().mkdir(parents=True, exist_ok=True)
        log(f"starting {command} serve on 127.0.0.1:{port}")
        with open(state_dir() / "opencode.log", "ab") as output:
            self.process = subprocess.Popen(
                [command, "serve", "--hostname", "127.0.0.1", "--port", str(port)],
                cwd=Path.home(),
                env=dict(os.environ, OPENCODE_SERVER_PASSWORD=self.password),
                stdin=subprocess.DEVNULL,
                stdout=output,
                stderr=output,
                start_new_session=True,
            )
        self.url = f"http://127.0.0.1:{port}"
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                raise OpencodeError(f"opencode serve exited; see {state_dir() / 'opencode.log'}")
            try:
                # opencode leaves the first connection it accepts while it starts
                # unanswered; a short wait per try moves on to the next one.
                self.request("GET", "/global/health", start=False, timeout=1)
                self.listen()
                return
            except OpencodeError:
                time.sleep(0.2)
        raise OpencodeError("opencode serve did not answer within 30 seconds")

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

    def add_services(self):
        """MCP servers added to opencode's configuration since its server started, added
        to the running server (POST /mcp), which does not read its configuration again: a
        service connected mid-conversation (`patina ai connect`) is there at once. One
        changed or removed waits for the next start."""
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
                    self.request("POST", "/mcp", {"name": name, "config": config}, start=False)
        except (OSError, ValueError, AttributeError, OpencodeError) as e:
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
                            note_event(self.asked, raw.decode("utf-8", "replace"))
            except (OSError, http.client.HTTPException):
                pass  # the server stopped or restarts; try again while it is the one in use
            time.sleep(1)

    def authorize(self, req):
        if self.password:
            token = base64.b64encode(f"opencode:{self.password}".encode()).decode()
            req.add_header("Authorization", "Basic " + token)

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
            raise OpencodeError(f"{method} {path}: {e}") from e

    def new_session(self, title):
        return self.request("POST", "/session", {"title": title})["id"]

    def ask(self, session, text):
        self.request("POST", f"/session/{session}/prompt_async", {"parts": [{"type": "text", "text": text}]})

    def messages(self, session):
        return self.request("GET", f"/session/{session}/message")

    def status(self, session):
        """idle, busy or retry, with the retry's message."""
        status = self.request("GET", "/session/status").get(session) or {"type": "idle"}
        return status.get("type", "idle"), status.get("message", "")

    def permissions(self, session):
        try:
            found = self.request("GET", "/permission")
        except OpencodeError as e:
            if not (isinstance(e.__cause__, urllib.error.HTTPError) and e.__cause__.code == 400):
                raise
            with self.lock:
                found = list(self.asked.values())
            # A request of another conversation the stream did show explains the 400.
            return [p for p in found if p.get("sessionID") == session] or ([] if found else [UNREADABLE])
        return [p for p in found if p.get("sessionID") == session]

    def reply(self, request_id, reply):
        self.request("POST", f"/permission/{request_id}/reply", {"reply": reply})

    def sessions(self, limit):
        found = self.request("GET", f"/session?roots=true&limit={limit}")
        found.sort(key=lambda s: s.get("time", {}).get("updated", 0), reverse=True)
        return found[:limit]


# ---- what the palette shows: plain functions of opencode's data ------------------------


def parse(query):
    """(kind, text): ask with the request, list for `ai` alone, view with an optional
    follow-up, or (None, "") for a query that is not kortex's."""
    q = query.replace(JOINER, "").strip()
    if q.startswith(VIEW):
        return "view", q[len(VIEW):].strip()
    if q.lower() == TRIGGER:
        return "list", ""
    if q[:len(TRIGGER) + 1].lower() == TRIGGER + " ":
        return "ask", q[len(TRIGGER) + 1:].strip()
    return None, ""


def tool_line(part):
    state = part.get("state") or {}
    given = state.get("input") or {}
    detail = given.get("command") or given.get("filePath") or given.get("url") or state.get("title") or ""
    line = f"› {part.get('tool', 'tool')} {detail}".rstrip()
    if state.get("status") == "error":
        line += " (failed)"
    return line


def answer_of(messages):
    """The agent's reply to the last request, as lines in order (its text and one line
    per tool it used), and the error that stopped it, if one did."""
    last_user = max((i for i, m in enumerate(messages) if m["info"].get("role") == "user"), default=-1)
    lines, error = [], ""
    for message in messages[last_user + 1:]:
        info = message["info"]
        if info.get("role") != "assistant":
            continue
        for part in message.get("parts", []):
            if part.get("type") == "text" and not part.get("synthetic") and not part.get("ignored"):
                if part.get("text", "").strip():
                    lines.append(part["text"].strip())
            elif part.get("type") == "tool":
                lines.append(tool_line(part))
        if info.get("error"):
            data = info["error"].get("data") or {}
            error = data.get("message") or info["error"].get("name", "error")
    return lines, error


def shorten(text):
    return text if len(text) <= MAX_ANSWER else "…" + text[-MAX_ANSWER:]


def match(match_id, text, icon=ICON, relevance=1.0, subtext="", actions=(), multiline=False):
    return (match_id, text, icon, relevance, {"subtext": subtext, "actions": list(actions), "multiline": multiline})


def view(state):
    """The matches of `ai ›`: what the agent asks to do first, then its answer."""
    lines, error = answer_of(state["messages"])
    matches = []
    for p in state["permissions"]:
        if p["id"] == UNREADABLE["id"]:
            matches.append(match("stuck", "The agent waits for an answer kortex cannot show (an opencode bug)",
                                 "dialog-warning", 1.0, "Asked before kortex was listening; it clears when opencode restarts"))
            continue
        what = ", ".join(p.get("patterns") or []) or p.get("permission", "")
        matches.append(match(
            "permission" + SEP + p["id"], f"Allow {p.get('permission', 'this')}: {what}", "dialog-warning", 1.0,
            "The agent asks before it does this", ["allow-once", "allow-always", "deny"], True))  # all of it, never elided
    if error:
        matches.append(match("answer", error, "dialog-error", 0.95, "The agent stopped", ["terminal"], True))
    status, retry = state["status"]
    body = "\n".join(lines)
    if body or status != "idle":
        subtext = "Copy · Open in terminal"
        if status == "busy":
            subtext = "Working… · " + subtext
        elif status == "retry":
            subtext = f"Retrying: {retry} · " + subtext
        matches.append(match("answer", shorten(body or "Thinking…"), ICON, 0.9, subtext, ["copy", "terminal"], True))
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


def terminal_command(url, session, terminal):
    """The terminal command that continues a session in opencode's own interface."""
    words = shlex.split(terminal) or ["konsole"]
    attach = [find_opencode(), "attach", url, "--session", session]
    if Path(words[0]).name.startswith("kitty"):
        return words + attach  # kitty takes the program as it is
    return words + ["-e"] + attach


# ---- the runner ------------------------------------------------------------------------


class Kortex:
    """What KRunner's calls do, without D-Bus: the D-Bus service in main() forwards to
    it, and the tests drive it directly. `desktop` reopens KRunner with a query, copies,
    starts programs and notifies; `now` is a clock."""

    def __init__(self, opencode, desktop, now=time.monotonic):
        self.opencode = opencode
        self.desktop = desktop
        self.now = now
        self.session = None  # the conversation `ai ›` shows
        self.viewing = False  # KRunner shows `ai ›` right now
        self.view_query = VIEW
        self.closing_at = None  # when a run closed KRunner that `show` reopens
        self.signature = None
        self.notified = set()
        self.problem = ""
        self.last_activity = now()

    def config(self):
        return {"MatchRegex": r"(?i)^ai(\s|$)", "MinLetterCount": 2}

    def match(self, query):
        kind, text = parse(query)
        if kind is None:
            return []
        self.last_activity = self.now()
        try:
            if kind == "ask":
                return self.ask_matches(text)
            if kind == "list":
                return self.list_matches()
            self.view_query = query.strip()
            return self.view_matches(text)
        except OpencodeError as e:
            log(e)
            return [match("problem", "The agent is not reachable", "dialog-error", 1.0, str(e))]

    def ask_matches(self, text):
        if not text:
            return [self.hint()]
        matches = [match("ask" + SEP + text, text, ICON, 1.0, "Ask the agent · Enter")]
        if self.session:
            matches.append(match("continue" + SEP + text, text, ICON, 0.9, "Continue the conversation"))
        return matches

    def list_matches(self):
        matches = []
        for i, s in enumerate(self.opencode.sessions(5)):
            matches.append(match(
                "session" + SEP + s["id"], s.get("title") or "Conversation", "view-history", 0.8 - i * 0.1,
                "Continue this conversation · " + ago(s.get("time", {}).get("updated", 0))))
        return [self.hint()] + matches

    def hint(self):
        return match("hint", "Ask the agent: type ai and your request", ICON, 1.0,
                     "ai › shows the current conversation")

    def view_matches(self, followup):
        self.viewing = True
        matches = []
        if followup:
            matches.append(match("continue" + SEP + followup, followup, ICON, 1.0, "Continue the conversation · Enter"))
        if self.problem:
            matches.append(match("problem", self.problem, "dialog-error", 0.95, "The request did not reach the agent"))
        if not self.session:
            return matches or [self.hint()]
        state = self.snapshot()
        self.signature = repr(state)
        return matches + view(state)

    def snapshot(self):
        return {
            "messages": self.opencode.messages(self.session),
            "status": self.opencode.status(self.session),
            "permissions": self.opencode.permissions(self.session),
        }

    def run(self, match_id, action):
        kind, _, arg = match_id.partition(SEP)
        self.last_activity = self.now()
        try:
            if kind in ("ask", "continue"):
                self.problem = ""
                if kind == "ask" or not self.session:
                    self.session = self.opencode.new_session(arg[:60])
                self.opencode.add_services()
                self.opencode.ask(self.session, arg)
                self.reopen(VIEW)
            elif kind == "session":
                self.session = arg
                self.reopen(VIEW)
            elif kind == "permission":
                if action in REPLIES:
                    self.opencode.reply(arg, REPLIES[action])
                self.reopen(self.view_query)
            elif kind == "answer" and self.session:
                if action == "terminal":
                    self.desktop.launch(
                        terminal_command(self.opencode.url, self.session, self.desktop.terminal()),
                        {"OPENCODE_SERVER_PASSWORD": self.opencode.password or ""})
                else:
                    lines, error = answer_of(self.opencode.messages(self.session))
                    self.desktop.copy("\n".join(lines) or error)
        except OpencodeError as e:
            log(e)
            self.problem = str(e)
            self.reopen(VIEW)

    def reopen(self, query):
        """After a run, which closes KRunner: open it again on the conversation."""
        self.closing_at = self.now()
        self.desktop.query(query)

    def teardown(self):
        # The one teardown right after a run is the closing that run caused, which
        # `reopen` undoes; any other means the user closed KRunner.
        if self.closing_at is not None and self.now() - self.closing_at < 1.5:
            self.closing_at = None
            return
        self.closing_at = None
        self.viewing = False

    def tick(self):
        """Every REFRESH_MS: while KRunner shows the conversation and it changed, show
        it again; while it is closed, notify of a new permission request."""
        if not self.session or not self.opencode.url or self.opencode.stopped():
            return  # a stopped server starts again on the next request from KRunner, not from here
        self.opencode.add_services()
        try:
            state = self.snapshot()
        except OpencodeError as e:
            log(e)
            return
        if self.viewing:
            if repr(state) != self.signature:
                self.signature = repr(state)
                q = self.view_query
                q = q.replace(JOINER, "") if JOINER in q else VIEW + JOINER + q[len(VIEW):]
                self.desktop.query(q)  # a query that differs, so KRunner asks again and gets the news
            return
        for p in state["permissions"]:
            if p["id"] not in self.notified:
                self.notified.add(p["id"])
                what = ", ".join(p.get("patterns") or []) or p.get("permission", "")
                self.desktop.notify("The agent asks first", f"{p.get('permission', '')}: {what}\nOpen it with: ai ›")

    def idle(self):
        """True when kortex may stop: nothing asked for a while and no request running."""
        if self.now() - self.last_activity < IDLE_EXIT:
            return False
        if self.session and self.opencode.url:
            try:
                if self.opencode.status(self.session)[0] != "idle":
                    return False
            except OpencodeError:
                pass
        return True


# ---- D-Bus -----------------------------------------------------------------------------


def main():
    import dbus
    import dbus.service
    from dbus.mainloop.glib import DBusGMainLoop
    from gi.repository import GLib

    DBusGMainLoop(set_as_default=True)
    bus = dbus.SessionBus()
    loop = GLib.MainLoop()

    def ignore(*_):
        pass

    def logged(e):
        log(e)

    class Desktop:
        """The few things kortex asks of the desktop, over D-Bus where Plasma offers it."""

        def query(self, text):
            def call():
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

        def notify(self, summary, body):
            bus.get_object("org.freedesktop.Notifications", "/org/freedesktop/Notifications").Notify(
                "kortex", dbus.UInt32(0), ICON, summary, body, dbus.Array([], signature="s"),
                dbus.Dictionary({}, signature="sv"), dbus.Int32(-1),
                dbus_interface="org.freedesktop.Notifications", reply_handler=ignore, error_handler=logged)

    opencode = Opencode.from_environment()
    kortex = Kortex(opencode, Desktop())

    def to_dbus(m):
        match_id, text, icon, relevance, props = m
        properties = dbus.Dictionary({
            "subtext": dbus.String(props["subtext"]),
            "actions": dbus.Array(props["actions"], signature="s"),
            "multiline": dbus.Boolean(props["multiline"]),
        }, signature="sv")
        return dbus.Struct((match_id, text, icon, dbus.Int32(CATEGORY_RELEVANCE), dbus.Double(relevance), properties),
                           signature="sssida{sv}")

    class Runner(dbus.service.Object):
        @dbus.service.method(IFACE, out_signature="a{sv}")
        def Config(self):
            return dbus.Dictionary(kortex.config(), signature="sv")

        @dbus.service.method(IFACE, out_signature="a(sss)")
        def Actions(self):
            return ACTIONS

        @dbus.service.method(IFACE, in_signature="s", out_signature="a(sssida{sv})")
        def Match(self, query):
            return dbus.Array([to_dbus(m) for m in kortex.match(str(query))], signature="(sssida{sv})")

        @dbus.service.method(IFACE, in_signature="ss")
        def Run(self, match_id, action_id):
            kortex.run(str(match_id), str(action_id))

        @dbus.service.method(IFACE)
        def Teardown(self):
            kortex.teardown()

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
        return True

    GLib.timeout_add(REFRESH_MS, tick)
    for sig in (signal.SIGTERM, signal.SIGINT):
        GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, sig, lambda: loop.quit() or False)
    log(f"{VERSION} ready on {SERVICE}")
    try:
        loop.run()
    finally:
        opencode.stop()
    return 0


if __name__ == "__main__":
    if sys.argv[1:] in (["--version"], ["-V"]):
        print(f"kortex {VERSION}")
        sys.exit(0)
    sys.exit(main())
