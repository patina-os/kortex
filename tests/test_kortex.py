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

    def stopped(self):
        return False

    def check(self):
        if self.fail:
            raise kortex.OpencodeError(self.fail)

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


class Files(unittest.TestCase):
    def test_password_is_private_and_stable(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": d}):
            first = kortex.server_password()
            self.assertEqual(first, kortex.server_password())
            mode = (Path(d) / "kortex" / "password").stat().st_mode & 0o777
            self.assertEqual(mode, 0o600)

    def test_kitty_takes_the_program_as_is(self):
        with mock.patch.object(kortex, "find_opencode", return_value="opencode"):
            self.assertEqual(kortex.terminal_command("u", "s", "kitty --single-instance"),
                             ["kitty", "--single-instance", "opencode", "attach", "u", "--session", "s"])


if __name__ == "__main__":
    unittest.main()
