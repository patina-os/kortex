# SPDX-License-Identifier: GPL-3.0-or-later
"""Unit tests: what kortex shows and does, with opencode and the desktop as fakes."""

import importlib.util
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

spec = importlib.util.spec_from_file_location("kortex", Path(__file__).parent.parent / "kortex.py")
kortex = importlib.util.module_from_spec(spec)
spec.loader.exec_module(kortex)
SEP = kortex.SEP


def user(text):
    return {"info": {"role": "user"}, "parts": [{"type": "text", "text": text}]}


def assistant(*parts, error=None):
    info = {"role": "assistant"}
    if error:
        info["error"] = error
    return {"info": info, "parts": list(parts)}


def text(t, **extra):
    return dict({"type": "text", "text": t}, **extra)


def tool(name, status="completed", **given):
    return {"type": "tool", "tool": name, "state": {"status": status, "input": given}}


class FakeOpencode:
    def __init__(self):
        self.url = "http://127.0.0.1:4096"
        self.password = "secret"
        self.created, self.asked, self.replies = [], [], []
        self.history = []
        self.state = ("idle", "")
        self.pending = []
        self.fail = None
        self.services_added = 0

    def stopped(self):
        return False

    def add_services(self):
        self.services_added += 1

    attach = kortex.Opencode.attach  # the real command, with this fake's url and password

    def check(self):
        if self.fail:
            raise kortex.AgentError(self.fail)

    def new_session(self, title):
        self.check()
        self.created.append(title)
        return f"ses_{len(self.created)}"

    def ask(self, session, t):
        self.check()
        self.asked.append((session, t))

    def messages(self, session):
        self.check()
        return self.history

    def status(self, session):
        self.check()
        return self.state

    def permissions(self, session):
        self.check()
        return [p for p in self.pending if p["sessionID"] == session]

    def reply(self, request_id, reply):
        self.replies.append((request_id, reply))

    def sessions(self, limit):
        self.check()
        return [{"id": "ses_old", "title": "Why is my wifi slow", "time": {"updated": 0}}]


class FakeDesktop:
    def __init__(self):
        self.queries, self.copied, self.launched, self.notes = [], [], [], []

    def query(self, t):
        self.queries.append(t)

    def copy(self, t):
        self.copied.append(t)

    def terminal(self):
        return "konsole"

    def launch(self, command, env):
        self.launched.append((command, env))

    def notify(self, summary, body):
        self.notes.append((summary, body))


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def new():
    clock = Clock()
    oc, desk = FakeOpencode(), FakeDesktop()
    return kortex.Kortex(oc, desk, clock), oc, desk, clock


PERMISSION = {"id": "per_1", "sessionID": "ses_1", "permission": "bash",
              "patterns": ["patina theme mode light"], "metadata": {}, "always": []}


FETCH = {"id": "per_9", "sessionID": "ses_1", "permission": "webfetch", "patterns": ["https://example.org/"],
         "metadata": {"url": "https://example.org/", "format": "text"}, "always": ["*"]}


def event(kind, properties):
    return "data: " + kortex.json.dumps({"id": "evt_1", "type": kind, "properties": properties}) + "\n"


class Events(unittest.TestCase):
    def test_asked_then_replied(self):
        asked = {}
        for line in [event("server.connected", {}), "\n", event("permission.asked", FETCH), ": comment\n"]:
            kortex.note_event(asked, line)
        self.assertEqual(asked, {"per_9": FETCH})
        kortex.note_event(asked, event("permission.replied", {"sessionID": "ses_1", "requestID": "per_9", "reply": "once"}))
        self.assertEqual(asked, {})

    def test_noise_is_ignored(self):
        asked = {"per_9": FETCH}
        for line in ["data: {not json\n", "data: []\n", "data: null\n", event("server.heartbeat", {}),
                     event("permission.asked", {"sessionID": "ses_1"}), event("permission.replied", {})]:
            kortex.note_event(asked, line)
        self.assertEqual(asked, {"per_9": FETCH})

    def test_follow_reads_the_stream_until_the_server_changes(self):
        oc = kortex.Opencode("http://127.0.0.1:1", "secret")
        lines = [event("permission.asked", FETCH).encode(), b"\n"]

        class Stream:
            def __enter__(self):
                return iter(lines)

            def __exit__(self, *_):
                oc.url = None  # the server stopped

        with mock.patch.object(oc.opener, "open", return_value=Stream()) as opened, \
                mock.patch.object(kortex.time, "sleep"):
            oc.follow("http://127.0.0.1:1")
        self.assertEqual(opened.call_args[0][0].full_url, "http://127.0.0.1:1/event")
        self.assertTrue(opened.call_args[0][0].get_header("Authorization").startswith("Basic "))
        self.assertEqual(oc.asked, {"per_9": FETCH})


class Parse(unittest.TestCase):
    def test_kinds(self):
        self.assertEqual(kortex.parse("ai why is my wifi slow"), ("ask", "why is my wifi slow"))
        self.assertEqual(kortex.parse("AI  hello "), ("ask", "hello"))
        self.assertEqual(kortex.parse("ai"), ("list", ""))
        self.assertEqual(kortex.parse("ai ›"), ("view", ""))
        self.assertEqual(kortex.parse("ai › and in dark?"), ("view", "and in dark?"))
        self.assertEqual(kortex.parse("aircraft"), (None, ""))
        self.assertEqual(kortex.parse("firefox"), (None, ""))


class Answer(unittest.TestCase):
    def test_reply_to_the_last_request_only(self):
        history = [user("first"), assistant(text("old answer")), user("second"),
                   assistant(text("thinking aloud", synthetic=True), tool("bash", command="nmcli dev"),
                             text("Your wifi runs on 2.4 GHz."))]
        self.assertEqual(kortex.answer_of(history),
                         (["› bash nmcli dev", "Your wifi runs on 2.4 GHz."], ""))

    def test_error(self):
        history = [user("hi"), assistant(error={"name": "ProviderAuthError", "data": {"message": "No API key"}})]
        self.assertEqual(kortex.answer_of(history), ([], "No API key"))

    def test_failed_tool(self):
        self.assertEqual(kortex.tool_line(tool("edit", "error", filePath="/etc/x")), "› edit /etc/x (failed)")


class View(unittest.TestCase):
    def test_permission_first_then_answer(self):
        state = {"messages": [user("light theme"), assistant(text("Switching."))],
                 "status": ("busy", ""), "permissions": [PERMISSION]}
        matches = kortex.view(state)
        self.assertEqual([m[0] for m in matches], ["permission" + SEP + "per_1", "answer"])
        self.assertEqual(matches[0][1], "Allow bash: patina theme mode light")
        self.assertEqual(matches[0][4]["actions"], ["allow-once", "allow-always", "deny"])
        self.assertTrue(matches[0][4]["multiline"])  # a long command is shown whole
        self.assertEqual(matches[1][1], "Switching.")
        self.assertTrue(matches[1][4]["multiline"])
        self.assertTrue(matches[1][4]["subtext"].startswith("Working…"))

    def test_thinking(self):
        state = {"messages": [user("hi")], "status": ("busy", ""), "permissions": []}
        self.assertEqual(kortex.view(state)[0][1], "Thinking…")

    def test_long_answer_keeps_the_end(self):
        state = {"messages": [user("hi"), assistant(text("x" * 5000 + "END"))], "status": ("idle", ""),
                 "permissions": []}
        shown = kortex.view(state)[0][1]
        self.assertTrue(shown.endswith("END"))
        self.assertLessEqual(len(shown), kortex.MAX_ANSWER + 1)


class Runner(unittest.TestCase):
    def test_ask_needs_no_server(self):
        k, oc, _desk, _ = new()
        oc.fail = "down"
        self.assertEqual([m[0] for m in k.match("ai hello")], ["ask" + SEP + "hello"])

    def test_not_ours(self):
        k, *_ = new()
        self.assertEqual(k.match("konsole"), [])

    def test_ask_then_view(self):
        k, oc, desk, _ = new()
        k.run("ask" + SEP + "switch to the light theme", "")
        self.assertEqual(oc.created, ["switch to the light theme"])
        self.assertEqual(oc.asked, [("ses_1", "switch to the light theme")])
        self.assertEqual(desk.queries, ["ai ›"])
        oc.history = [user("switch to the light theme")]
        oc.state = ("busy", "")
        oc.pending = [PERMISSION]
        ids = [m[0] for m in k.match("ai ›")]
        self.assertEqual(ids, ["permission" + SEP + "per_1", "answer"])
        self.assertTrue(k.viewing)

    def test_continue_offered_with_a_conversation(self):
        k, oc, _desk, _ = new()
        k.run("ask" + SEP + "first", "")
        ids = [m[0] for m in k.match("ai second")]
        self.assertEqual(ids, ["ask" + SEP + "second", "continue" + SEP + "second"])
        k.run("continue" + SEP + "second", "")
        self.assertEqual(oc.asked[-1], ("ses_1", "second"))
        self.assertEqual(oc.created, ["first"])

    def test_permission_needs_an_action(self):
        k, oc, _desk, _ = new()
        k.run("ask" + SEP + "x", "")
        k.run("permission" + SEP + "per_1", "")  # Enter alone answers nothing
        self.assertEqual(oc.replies, [])
        k.run("permission" + SEP + "per_1", "allow-once")
        k.run("permission" + SEP + "per_2", "deny")
        k.run("permission" + SEP + "per_3", "allow-always")
        self.assertEqual(oc.replies, [("per_1", "once"), ("per_2", "reject"), ("per_3", "always")])

    def test_unreadable_permission_is_shown_but_not_answered(self):
        k, oc, desk, _ = new()
        k.run("ask" + SEP + "x", "")
        oc.pending = [dict(kortex.UNREADABLE, sessionID="ses_1")]
        stuck = [m for m in k.match("ai ›") if m[0] == "stuck"]
        self.assertEqual(len(stuck), 1)
        self.assertEqual(stuck[0][4]["actions"], [])
        k.run("stuck", "")
        self.assertEqual((desk.launched, oc.replies), ([], []))

    def test_permissions_400_come_from_the_event_stream(self):
        oc = kortex.Opencode("http://127.0.0.1:1", "secret")

        def fail(code):
            def request(*_args, **_kwargs):
                cause = kortex.urllib.error.HTTPError("http://127.0.0.1:1/permission", code, "x", {}, None)
                raise kortex.AgentError(f"GET /permission: {cause}") from cause
            return request

        with mock.patch.object(oc, "request", fail(400)):
            self.assertEqual(oc.permissions("ses_1"), [kortex.UNREADABLE])
            oc.asked = {"per_9": FETCH}
            self.assertEqual(oc.permissions("ses_1"), [FETCH])
            self.assertEqual(oc.permissions("ses_2"), [])  # the 400 is ses_1's request
        with mock.patch.object(oc, "request", fail(500)), self.assertRaises(kortex.AgentError):
            oc.permissions("ses_1")

    def test_copy_and_terminal(self):
        k, oc, desk, _ = new()
        k.run("ask" + SEP + "x", "")
        oc.history = [user("x"), assistant(text("the answer"))]
        k.run("answer", "")
        self.assertEqual(desk.copied, ["the answer"])
        with mock.patch.object(kortex, "find_opencode", return_value="/usr/bin/opencode"):
            k.run("answer", "terminal")
        command, env = desk.launched[0]
        self.assertEqual(command, ["konsole", "-e", "/usr/bin/opencode", "attach", oc.url, "--session", "ses_1"])
        self.assertEqual(env, {"OPENCODE_SERVER_PASSWORD": "secret"})
        self.assertNotIn("secret", command)

    def test_list(self):
        k, *_ = new()
        ids = [m[0] for m in k.match("ai")]
        self.assertEqual(ids, ["hint", "session" + SEP + "ses_old"])

    def test_unreachable(self):
        k, oc, _desk, _ = new()
        oc.fail = "connection refused"
        m = k.match("ai")
        self.assertEqual(m[0][0], "problem")
        k.run("ask" + SEP + "x", "")
        self.assertIn("connection refused", k.problem)
        oc.fail = None
        self.assertEqual(k.match("ai ›")[0][0], "problem")

    def test_teardown_after_a_run_is_ignored_once(self):
        k, _oc, _desk, clock = new()
        k.run("ask" + SEP + "x", "")
        k.match("ai ›")
        k.teardown()  # the closing the run caused
        self.assertTrue(k.viewing)
        clock.t += 0.5
        k.teardown()  # the user closed it
        self.assertFalse(k.viewing)

    def test_tick_reopens_only_on_change(self):
        k, oc, desk, _clock = new()
        k.run("ask" + SEP + "x", "")
        oc.history = [user("x")]
        oc.state = ("busy", "")
        k.match("ai ›")
        before = len(desk.queries)
        k.tick()
        self.assertEqual(len(desk.queries), before)
        oc.history = [user("x"), assistant(text("hel"))]
        k.tick()
        # KRunner asks again only for a query that differs from the one it shows
        self.assertNotEqual(desk.queries[-1], "ai ›")
        self.assertEqual(kortex.parse(desk.queries[-1]), ("view", ""))
        self.assertEqual(len(desk.queries), before + 1)
        k.match(desk.queries[-1])
        oc.history = [user("x"), assistant(text("hello"))]
        k.tick()
        self.assertEqual(desk.queries[-1], "ai ›")

    def test_tick_notifies_when_closed(self):
        k, oc, desk, _clock = new()
        k.run("ask" + SEP + "x", "")
        oc.pending = [PERMISSION]
        k.tick()
        k.tick()
        self.assertEqual(len(desk.notes), 1)
        self.assertIn("patina theme mode light", desk.notes[0][1])

    def test_idle(self):
        k, oc, _desk, clock = new()
        self.assertFalse(k.idle())
        clock.t += kortex.IDLE_EXIT + 1
        self.assertTrue(k.idle())
        k.session = "ses_1"
        oc.state = ("busy", "")
        self.assertFalse(k.idle())


class Services(unittest.TestCase):
    """MCP servers added to opencode's configuration reach the running server."""

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.env = mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": self.dir.name})
        self.env.start()
        self.config = Path(self.dir.name) / "opencode" / "opencode.json"
        self.config.parent.mkdir()
        self.config.write_text('{"model": "ollama/qwen3.5"}')
        self.oc = kortex.Opencode("http://127.0.0.1:4096", None)
        self.oc.config_seen = kortex.mtime(self.config)  # as a server kortex started read it
        self.calls = []
        self.running = {}

        def request(method, path, body=None, start=True, timeout=10):
            self.calls.append((method, path, body))
            return self.running if method == "GET" else {}
        self.oc.request = request

    def tearDown(self):
        self.env.stop()
        self.dir.cleanup()

    def write(self, mcp):
        self.config.write_text(kortex.json.dumps({"model": "ollama/qwen3.5", "mcp": mcp}))
        os.utime(self.config, ns=(self.oc.config_seen + 10**9,) * 2)

    def test_nothing_changed_asks_nothing(self):
        self.oc.add_services()
        self.assertEqual(self.calls, [])

    def test_a_new_server_is_added_and_others_are_left(self):
        linear = {"type": "remote", "url": "https://mcp.linear.app/mcp"}
        self.running = {"notion": {"status": "connected"}}
        self.write({"linear": linear, "notion": {"type": "remote", "url": "https://mcp.notion.com/mcp"},
                    "github": {"type": "remote", "url": "https://api.githubcopilot.com/mcp/", "enabled": False}})
        self.oc.add_services()
        self.assertEqual(self.calls, [("GET", "/mcp", None), ("POST", "/mcp", {"name": "linear", "config": linear})])
        self.calls.clear()
        self.oc.add_services()  # once per change of the file
        self.assertEqual(self.calls, [])

    def test_a_file_opencode_reads_but_json_does_not_is_left_alone(self):
        self.config.write_text('{"mcp": {} // a comment\n}')
        os.utime(self.config, ns=(self.oc.config_seen + 10**9,) * 2)
        self.oc.add_services()
        self.assertEqual(self.calls, [])

    def test_no_server_yet_waits_for_its_start(self):
        self.oc.url = None
        self.write({"linear": {"type": "remote", "url": "https://mcp.linear.app/mcp"}})
        self.oc.add_services()
        self.assertEqual(self.calls, [])

    def test_runner_adds_services_before_it_asks(self):
        k, oc, _, _ = new()
        k.run("ask" + SEP + "file a ticket", "")
        self.assertEqual(oc.services_added, 1)
        self.assertEqual(oc.asked, [("ses_1", "file a ticket")])


def hermes_event(**fields):
    return "data: " + kortex.json.dumps(fields) + "\n"


class HermesBackend(unittest.TestCase):
    """Hermes Agent's API server: its events, messages and approvals in kortex's terms."""

    def setUp(self):
        self.h = kortex.Hermes("http://127.0.0.1:8642", "key")
        self.calls = []
        self.answers = {}

        def request(method, path, body=None, start=True, timeout=10):
            self.calls.append((method, path, body))
            return self.answers.get(path, {})
        self.h.request = request

    def test_approval_requests_come_and_go_with_the_stream(self):
        asked = {}
        request = hermes_event(event="approval.request", request_id="req_1", command="rm -r old-photos",
                               description="recursive delete", choices=["once", "session", "always", "deny"])
        self.assertFalse(kortex.note_hermes_event(asked, request, "ses_1", "run_1"))
        self.assertEqual(asked["req_1"], {"id": "req_1", "sessionID": "ses_1", "run": "run_1",
                                          "permission": "recursive delete", "patterns": ["rm -r old-photos"]})
        kortex.note_hermes_event(asked, hermes_event(event="approval.responded", request_id="req_1"), "ses_1", "run_1")
        self.assertEqual(asked, {})
        self.assertFalse(kortex.note_hermes_event(asked, ": keepalive\n", "ses_1", "run_1"))
        self.assertFalse(kortex.note_hermes_event(asked, "data: {not json\n", "ses_1", "run_1"))
        self.assertTrue(kortex.note_hermes_event(asked, hermes_event(event="run.completed"), "ses_1", "run_1"))

    def test_messages_read_like_opencode_s(self):
        found = [
            {"role": "user", "content": "remove old-photos"},
            {"role": "assistant", "content": "", "tool_calls": [
                {"id": "c1", "function": {"name": "terminal", "arguments": '{"command": "rm -r old-photos"}'}},
                {"id": "c2", "function": {"name": "read_file", "arguments": '{"path": "notes.txt"}'}},
                {"id": "c3", "function": {"name": "terminal", "arguments": '{"command": "ls"}'}}]},
            {"role": "tool", "tool_call_id": "c1", "content": '{"output": "", "exit_code": 0, "error": null}'},
            {"role": "tool", "tool_call_id": "c2", "content": '{"error": "no such file"}'},
            {"role": "assistant", "content": "Removed it."},
        ]
        messages = kortex.hermes_messages(found, {"status": "running"})
        lines, error = kortex.answer_of(messages)
        self.assertEqual(lines, ["› terminal rm -r old-photos", "› read_file notes.txt (failed)", "› terminal ls", "Removed it."])
        self.assertEqual(error, "")
        self.assertEqual(messages[1]["parts"][2]["state"]["status"], "running")  # no result yet

    def test_a_failed_run_shows_its_error(self):
        messages = kortex.hermes_messages([{"role": "user", "content": "x"}], {"status": "failed", "error": "model unreachable"})
        self.assertEqual(kortex.answer_of(messages), ([], "model unreachable"))

    def test_permission_shows_what_hermes_counts_as_dangerous(self):
        self.h.asked["req_1"] = {"id": "req_1", "sessionID": "ses_1", "run": "run_1",
                                 "permission": "recursive delete", "patterns": ["rm -r old-photos"]}
        shown = kortex.view({"messages": [], "status": ("busy", ""), "permissions": self.h.permissions("ses_1")})
        self.assertEqual(shown[0][1], "Allow recursive delete: rm -r old-photos")
        self.assertEqual(self.h.permissions("ses_2"), [])

    def test_replies_use_hermes_words_and_its_run(self):
        self.h.asked["req_1"] = {"id": "req_1", "sessionID": "ses_1", "run": "run_1", "permission": "x", "patterns": []}
        self.h.reply("req_1", "reject")
        self.assertEqual(self.calls, [("POST", "/v1/runs/run_1/approval", {"request_id": "req_1", "choice": "deny"})])
        self.assertEqual(self.h.asked, {})
        self.h.reply("req_9", "once")  # already answered elsewhere: nothing to send
        self.assertEqual(len(self.calls), 1)

    def test_status_follows_the_last_run(self):
        self.assertEqual(self.h.status("ses_1"), ("idle", ""))
        self.h.runs["ses_1"] = "run_1"
        self.answers["/v1/runs/run_1"] = {"status": "waiting_for_approval"}
        self.assertEqual(self.h.status("ses_1"), ("busy", ""))
        self.answers["/v1/runs/run_1"] = {"status": "completed"}
        self.assertEqual(self.h.status("ses_1"), ("idle", ""))

    def test_a_run_the_gateway_does_not_know_is_done(self):
        self.h.runs["ses_1"] = "run_old"

        def gone(method, path, body=None, start=True, timeout=10):
            try:
                raise kortex.urllib.error.HTTPError(path, 404, "not found", {}, None)
            except kortex.urllib.error.HTTPError as e:
                raise kortex.AgentError("GET " + path) from e
        with mock.patch.object(self.h, "request", gone):
            self.assertEqual(self.h.status("ses_1"), ("idle", ""))
        self.h.process = mock.Mock()
        self.h.stop()
        self.assertEqual(self.h.runs, {})

    def test_sessions_newest_first(self):
        self.answers["/api/sessions?limit=5"] = {"data": [
            {"id": "a", "title": "Old", "last_active": 100.0}, {"id": "b", "title": "New", "last_active": 200.5}]}
        self.assertEqual(self.h.sessions(5), [{"id": "b", "title": "New", "time": {"updated": 200500}},
                                              {"id": "a", "title": "Old", "time": {"updated": 100000}}])

    def test_terminal_resumes_the_conversation(self):
        with mock.patch.object(kortex, "find_hermes", return_value="/home/u/.local/bin/hermes"):
            self.assertEqual(self.h.attach("ses_1"), (["/home/u/.local/bin/hermes", "--resume", "ses_1"], {}))

    def test_agent_is_chosen_by_file_or_environment(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": d}):
            os.environ.pop("KORTEX_AGENT", None)
            self.assertEqual(kortex.agent_name(), "opencode")
            (Path(d) / "kortex").mkdir()
            (Path(d) / "kortex" / "agent").write_text("hermes\n")
            self.assertEqual(kortex.agent_name(), "hermes")
            os.environ["KORTEX_AGENT"] = "opencode"
            self.assertEqual(kortex.agent_name(), "opencode")


class Files(unittest.TestCase):
    def test_password_is_private_and_stable(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": d}):
            first = kortex.server_password()
            self.assertEqual(first, kortex.server_password())
            mode = (Path(d) / "kortex" / "password").stat().st_mode & 0o777
            self.assertEqual(mode, 0o600)

    def test_kitty_takes_the_program_as_is(self):
        with mock.patch.object(kortex, "find_opencode", return_value="opencode"):
            attach = ["opencode", "attach", "u", "--session", "s"]
            self.assertEqual(kortex.terminal_command(attach, "kitty --single-instance"),
                             ["kitty", "--single-instance", "opencode", "attach", "u", "--session", "s"])


if __name__ == "__main__":
    unittest.main()
