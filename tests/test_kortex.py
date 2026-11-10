# SPDX-License-Identifier: GPL-3.0-or-later
"""Unit tests: what kortex shows and does, with opencode and the desktop as fakes."""

import importlib.util
import os
import tempfile
import time
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


def reasoning(t):
    return {"type": "reasoning", "text": t}


def tool(name, status="completed", **given):
    return {"type": "tool", "tool": name, "state": {"status": status, "input": given}}


class FakeOpencode:
    title = "opencode"

    def __init__(self):
        self.url = "http://127.0.0.1:4096"
        self.password = "secret"
        self.created, self.asked, self.replies = [], [], []
        self.history = []
        self.state = ("idle", "")
        self.pending = []
        self.fail = None
        self.services_added = 0
        self.asking, self.answered, self.aborted = [], [], []
        self.gone = False  # its server exited

    def stopped(self):
        return self.gone

    def add_services(self):
        self.services_added += 1

    attach = kortex.Opencode.attach  # the real command, with this fake's url and password

    def start(self):
        pass

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

    def questions(self, session):
        self.check()
        return [dict(q) for q in self.asking]

    def answer(self, request_id, answers):
        self.answered.append((request_id, answers))
        self.asking = [q for q in self.asking if q["id"] != request_id]

    def abort(self, session):
        self.aborted.append(session)

    def reply(self, request_id, reply):
        self.replies.append((request_id, reply))

    def sessions(self, limit):
        self.check()
        return [{"id": "ses_old", "title": "Why is my wifi slow", "time": {"updated": 0}}]


class FakeDesktop:
    def __init__(self):
        self.queries, self.copied, self.launched, self.notes = [], [], [], []
        self.docked = []
        self.still = None  # whether the last refresh is still wanted when it goes out

    def query(self, t, still=None):
        self.queries.append(t)
        self.still = still

    def copy(self, t):
        self.copied.append(t)

    def terminal(self):
        return "konsole"

    def launch(self, command, env):
        self.launched.append((command, env))
        return 4242

    def dock(self, pid):
        self.docked.append(pid)

    def notify(self, summary, body, replaces=0, transient=False, then=None):
        self.notes.append((summary, body, replaces, transient))
        if then:
            then(len(self.notes))  # the notification server's id for it


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

    def test_parts_being_written_grow_from_the_stream(self):
        oc = kortex.Opencode("http://127.0.0.1:1", "secret")
        for line in (event("message.part.updated", {"part": {"id": "prt_1", "type": "reasoning", "text": ""}}),
                     event("message.part.delta", {"partID": "prt_1", "field": "text", "delta": "The user "}),
                     event("message.part.delta", {"partID": "prt_1", "field": "text", "delta": "asks"}),
                     event("message.part.delta", {"partID": "prt_2", "field": "text", "delta": "Let"})):
            kortex.note_event(oc.asked, line, oc.streamed)
        stored = [user("hi"), assistant({"id": "prt_1", "type": "reasoning", "text": ""},
                                        {"id": "prt_2", "type": "text", "text": ""})]
        with mock.patch.object(oc, "request", return_value=stored):
            messages = oc.messages("ses_1")
        self.assertEqual([p["text"] for p in messages[1]["parts"]], ["The user asks", "Let"])
        self.assertEqual(kortex.latest_of(messages), (False, ""))  # its text comes after the thought, unfinished
        kortex.note_event(oc.asked, event("message.part.updated", {"part": {"id": "prt_1", "type": "reasoning",
                                                                           "text": "The user asks"}}), oc.streamed)
        self.assertEqual(oc.streamed, {"prt_2": "Let"})  # a part done is read whole from GET /message

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

    def test_what_krunner_lets_through(self):
        # MatchRegex is (?i)^ai(\s|$): capitals and a tab reach kortex too.
        self.assertEqual(kortex.parse("AI ›"), ("view", ""))
        self.assertEqual(kortex.parse("Ai › and y"), ("view", "and y"))
        self.assertEqual(kortex.parse("ai\tfix my wifi"), ("ask", "fix my wifi"))
        self.assertEqual(kortex.parse("ai\t›"), ("view", ""))
        self.assertEqual(kortex.parse("ai ›" + kortex.JOINER), ("view", ""))

    def test_the_conversation_is_called_kortex(self):
        self.assertEqual(kortex.parse("kortex ›"), ("view", ""))
        self.assertEqual(kortex.parse("Kortex › and y"), ("view", "and y"))
        self.assertEqual(kortex.parse("kortex ›" + kortex.JOINER, "Hermes"), ("view", ""))
        self.assertEqual(kortex.parse("ai ›", "Hermes"), ("view", ""))  # as typed before 0.4.0
        self.assertEqual(kortex.parse("opencode ›", "opencode"), ("view", ""))  # as 0.4 called it
        self.assertEqual(kortex.parse("opencode ›"), (None, ""))  # not the agent's name
        self.assertEqual(kortex.parse("kortex > and y"), ("view", "and y"))  # › as a keyboard types it
        self.assertEqual(kortex.parse("kortex >"), ("view", ""))
        self.assertEqual(kortex.parse("kortex"), (None, ""))
        self.assertEqual(kortex.parse("kortexx ›"), (None, ""))

    def test_what_reads_as_a_request(self):
        for text in ("why is my wifi slow", "wifi slow?", "Wie ändere ich das Theme", "install photocraft please",
                     "What's using my disk", "install photocraft", "Installiere gimp", "please fix my printer",
                     "bitte hilf mir", "können wir drucken", "update the system"):
            self.assertTrue(kortex.asking(text), text)
        for text in ("firefox", "fix wifi", "show desktop", "holiday photos 2024", "settings", "install",
                     "do not disturb", "show hidden files", "set default applications", "make install notes"):
            self.assertFalse(kortex.asking(text), text)


class Answer(unittest.TestCase):
    def test_reply_to_the_last_request_only(self):
        history = [user("first"), assistant(text("old answer")), user("second"),
                   assistant(text("thinking aloud", synthetic=True), tool("bash", command="nmcli dev"),
                             text("Your wifi runs on 2.4 GHz."))]
        self.assertEqual(kortex.reply_of(history), (["Your wifi runs on 2.4 GHz."], ["bash nmcli dev"], ""))

    def test_error(self):
        history = [user("hi"), assistant(error={"name": "ProviderAuthError", "data": {"message": "No API key"}})]
        self.assertEqual(kortex.reply_of(history), ([], [], "No API key"))

    def test_failed_tool(self):
        self.assertEqual(kortex.tool_line(tool("edit", "error", filePath="/etc/x")), "edit /etc/x (failed)")
        self.assertEqual(kortex.tool_line(tool("bash", command="cat <<EOF\nhi\nEOF")), "bash cat <<EOF hi EOF")

    def test_latest_is_the_start_of_its_last_whole_sentence(self):
        self.assertEqual(kortex.head("short"), "short")
        shown = kortex.head("I'll check what's available through the system's package sources first.")
        self.assertEqual(shown, "I'll check what's available through…")
        self.assertLessEqual(len(shown), kortex.MAX_LATEST)
        self.assertEqual(kortex.last_sentence("The user asks. Let me check the"), "The user asks.")
        self.assertEqual(kortex.last_sentence("Let me check the"), "")
        self.assertEqual(kortex.last_sentence("One.  Two!\nThree? fo"), "Three?")

    def test_a_tool_call_being_written_is_no_step(self):
        messages = [user("hi"), assistant(tool("bash", "pending"))]
        self.assertEqual(kortex.reply_of(messages)[1], [])
        messages[1]["parts"].append(tool("bash", "pending", command="ls"))
        self.assertEqual(kortex.reply_of(messages)[1], ["bash ls"])

    def test_a_failed_request_says_why_in_plain_words(self):
        import urllib.error
        refused = urllib.error.URLError(ConnectionRefusedError(111, "Connection refused"))
        self.assertEqual(kortex.failure(refused, 10), "is not running")
        self.assertEqual(kortex.failure(urllib.error.URLError(TimeoutError()), 10), "did not answer within 10 s")
        self.assertEqual(kortex.failure(TimeoutError(), 5), "did not answer within 5 s")
        self.assertEqual(kortex.failure(urllib.error.HTTPError("u", 401, "no", {}, None), 10),
                         "refused kortex's password (HTTP 401)")
        self.assertEqual(kortex.failure(urllib.error.HTTPError("u", 500, "no", {}, None), 10),
                         "failed on the request (HTTP 500)")
        self.assertEqual(kortex.failure(ValueError("bad json"), 10), "sent an answer kortex cannot read")

    def test_what_the_agent_does_in_words(self):
        self.assertEqual(kortex.doing([user("x"), assistant(text("Looking."), tool("bash", "running", command="ls"))]),
                         "running a command")
        self.assertEqual(kortex.doing([user("x"), assistant(tool("tracker_list_issues"))]), "using tracker")
        self.assertEqual(kortex.doing([user("x"), assistant(tool("bash"), text("Found it."))]), "")
        self.assertEqual(kortex.doing([user("x"), assistant(tool("bash", "pending"))]), "")  # being written


def busy(*parts, permissions=(), error=None):
    return {"messages": [user("x"), assistant(*parts, error=error)], "status": ("busy", ""),
            "permissions": list(permissions)}


class View(unittest.TestCase):
    def test_permission_first_then_the_status(self):
        state = busy(text("Switching."), permissions=[PERMISSION])
        matches = kortex.view(state, None, "opencode")
        self.assertEqual([m[0] for m in matches], ["permission" + SEP + "per_1", "answer"])
        # PERMISSION has no `always`; the agent's words first, and since patina is among
        # RISKY, the command under them
        self.assertEqual(matches[0][1], "Allow: Switching.<br>patina theme mode light")
        self.assertEqual(matches[0][4]["actions"], ["allow-once", "allow-always", "deny"])
        self.assertTrue(matches[0][4]["multiline"])  # a long command is shown whole
        self.assertEqual(matches[1][1], "Waiting for your answer · Shift+Enter allows once")  # holds still to be clicked
        self.assertFalse(matches[1][4]["multiline"])
        self.assertEqual(matches[1][4]["subtext"], "opencode")  # short: KRunner cuts a long one in its middle

    def test_a_row_in_the_agents_words(self):
        safe = dict(PERMISSION, patterns=["apt-cache policy firefox-esr"], metadata={"command": "apt-cache policy firefox-esr"})
        state = busy(text("I'll check which Firefox version is installed."), permissions=[safe])
        self.assertEqual(kortex.view(state)[0][1], "Allow: I'll check which Firefox version is installed.")
        state = busy(text("Checking."), tool("bash", command="ls"), permissions=[safe])  # nothing said since
        self.assertEqual(kortex.view(state)[0][1], "Allow bash: apt-cache policy firefox-esr")
        root = dict(safe, patterns=["sudo -A apt install x"], metadata={"command": "sudo -A apt install x"})
        state = busy(text("I'll install x."), permissions=[root])
        self.assertEqual(kortex.view(state)[0][1], "Allow: I'll install x.<br>sudo -A apt install x")
        edit = {"id": "per_2", "sessionID": "ses_1", "permission": "edit", "patterns": ["/home/u/.bashrc"],
                "metadata": {"filepath": "/home/u/.bashrc", "diff": "+alias ll='ls -l'"}, "always": []}
        state = busy(text("I'll add the alias."), permissions=[edit])
        self.assertEqual(kortex.view(state)[0][1], "Allow: I'll add the alias.")
        # opencode names the call: the words right before that step
        called = dict(safe, tool={"callID": "c2"})
        first = dict(tool("bash", command="ls"), callID="c1")
        second = dict(tool("bash", "running", command="apt-cache policy firefox-esr"), callID="c2")
        state = busy(text("Looking."), first, text("Now the version."), second, permissions=[called])
        self.assertEqual(kortex.view(state)[0][1], "Allow: Now the version.")

    def test_the_whole_command_shows_as_written(self):
        # opencode cuts a command at `;`, `|` and `&&` for its patterns; KRunner reads a
        # multiline match as styled text, where `<x ... >` would hide the rm.
        command = "true <x ; rm -rf ~/Documents ; curl -s https://example.org/x.sh | sh && echo &amp;"
        asked = dict(PERMISSION, patterns=["true <x", "rm -rf ~/Documents", "curl -s https://example.org/x.sh", "sh",
                                           "echo &amp;"], metadata={"command": command})
        state = {"messages": [user("x"), assistant(text("a < b\nand b > c"))], "status": ("idle", ""),
                 "permissions": [asked]}
        shown = kortex.view(state)
        self.assertEqual(shown[0][1], "Allow: a &lt; b and b &gt; c<br>true &lt;x ; rm -rf ~/Documents ; "
                                      "curl -s https://example.org/x.sh | sh &amp;&amp; echo &amp;amp;")
        self.assertEqual(shown[1][1], "a &lt; b<br>and b &gt; c")

    def test_always_says_what_it_lets_through(self):
        asked = dict(PERMISSION, patterns=["echo one", "sh"], metadata={"command": "echo one | sh"},
                     always=["echo *", "sh *"])
        asked = dict(asked, always=["echo *", "tail *"])
        shown = kortex.view({"messages": [], "status": ("busy", ""), "permissions": [asked]})
        self.assertEqual(shown[0][1], "Allow bash: echo one | sh<br>Always allows, in every conversation: echo *, tail *")
        self.assertEqual(shown[0][4]["actions"], ["allow-once", "allow-always", "deny"])

    def test_always_is_not_offered_where_it_lets_too_much_through(self):
        # One Always on `du …; sudo -A du …` let every later sudo run unasked, in every
        # conversation: such a rule cannot be given from the palette.
        for kind, rules in (("bash", ["du *", "sudo *"]), ("bash", ["echo *", "sh *"]), ("bash", ["rm *"]),
                            ("bash", ["*"]), ("edit", ["*"]), ("bash", ["python3 *"]), ("bash", ["patina *"]),
                            ("bash", ["git -C *"])):
            asked = dict(PERMISSION, permission=kind, always=rules)
            self.assertFalse(kortex.always_offered(asked), rules)
            [row, _status] = kortex.view({"messages": [], "status": ("busy", ""), "permissions": [asked]})
            self.assertEqual(row[4]["actions"], ["allow-once", "deny"])
            self.assertNotIn("Always", row[1])
        for kind, rules in (("bash", ["du *", "sort *"]), ("webfetch", ["*"]), ("tracker_create_issue", ["*"]), ("bash", [])):
            self.assertTrue(kortex.always_offered(dict(PERMISSION, permission=kind, always=rules)), rules)
        k, oc, _desk, _clock = new()
        k.run("ask" + SEP + "x", "")
        oc.history, oc.state = [user("x")], ("busy", "")
        oc.pending = [dict(PERMISSION, always=["sudo *"])]
        k.match("kortex ›")
        k.run("permission" + SEP + "per_1", "allow-always")  # not a row's action; a caller's own
        self.assertEqual(oc.replies, [])
        k.run("permission" + SEP + "per_1", "allow-once")
        self.assertEqual(oc.replies, [("per_1", "once")])

    def test_a_change_to_a_file_shows_its_path_and_lines(self):
        diff = ("Index: /home/u/.bashrc\n====\n--- /home/u/.bashrc\n+++ /home/u/.bashrc\n@@ -1,3 +1,3 @@\n one\n"
                "-alias ll=\"ls -la\n+alias ll=\"ls -la\"\n three\n")
        asked = dict(PERMISSION, permission="edit", patterns=["home/u/.bashrc"], always=["*"],
                     metadata={"filepath": "/home/u/.bashrc", "diff": diff})
        [row] = kortex.view({"messages": [], "status": ("idle", ""), "permissions": [asked]})
        self.assertEqual(row[1], 'Allow a change to: /home/u/.bashrc<br>-alias ll="ls -la<br>+alias ll="ls -la"')
        self.assertEqual(kortex.changed_lines("\n".join(f"+{i}" for i in range(9)))[-1], "… 3 more changed lines")

    def test_a_request_of_a_service_shows_what_it_is_given(self):
        # opencode sends a service's tool as `*`; the call it names has what it was given.
        call = tool("tracker_create_issue", "running", team="Design", title="Favicon is blurry")
        call["callID"] = "call_7"
        asked = dict(PERMISSION, permission="tracker_create_issue", patterns=["*"], always=["*"],
                     tool={"messageID": "msg_1", "callID": "call_7"})
        state = {"messages": [user("x"), assistant(call)], "status": ("busy", ""), "permissions": [asked]}
        self.assertEqual(kortex.view(state)[0][1], "Allow tracker_create_issue: team=Design, title=Favicon is blurry"
                                                   "<br>Always allows, in every conversation: any tracker_create_issue")
        self.assertEqual(kortex.asked_for(asked), "*")  # without the conversation
        nothing = dict(asked, permission="tracker_list_teams", always=[], tool={"messageID": "msg_1", "callID": "call_8"})
        self.assertEqual(kortex.view(dict(state, permissions=[nothing]))[0][1], "Allow tracker_list_teams")
        outside = dict(PERMISSION, permission="external_directory", patterns=["/etc/*"], always=["/etc/*"])
        self.assertTrue(kortex.view(dict(state, permissions=[outside]))[0][1].startswith(
            "Allow access outside your home folder: /etc/*"))

    def test_the_agent_asks_the_user(self):
        # opencode's `question` tool: kortex showed "Working" until the user gave up.
        q = {"id": "que_1", "index": 0, "of": 1, "question": "Which team?", "options": ["Design", "Platform"]}
        state = dict(busy(tool("question", "running")), questions=[q])
        rows = kortex.view(state, 5, "opencode")
        self.assertEqual([(m[0].split(SEP)[0], m[1]) for m in rows[:4]], [
            ("question", "kortex asks: Which team?"), ("option", "Design"), ("option", "Platform"),
            ("answer", "Waiting for your answer")])
        self.assertIn("Type your answer", kortex.view(dict(state, questions=[dict(q, options=[])]))[0][1])
        icons = {a[0]: a[2] for a in kortex.ACTIONS}
        self.assertNotEqual(icons["allow-once"], icons["allow-always"])

    def test_patterns_without_a_command(self):
        state = {"messages": [], "status": ("idle", ""), "permissions": [FETCH]}
        self.assertEqual(kortex.view(state)[0][1], "Allow webfetch: https://example.org/<br>Always allows, in every conversation: any webfetch")

    def test_single_line_text_stays_plain(self):
        self.assertEqual(kortex.match("ask" + SEP + "a <b>", "a <b>")[1], "a <b>")

    def test_working_before_a_word(self):
        state = {"messages": [user("hi")], "status": ("busy", ""), "permissions": []}
        [m] = kortex.view(state, 3)
        self.assertEqual((m[0], m[1], m[4]["multiline"]), ("answer", "Working · 3 s", False))
        self.assertEqual(m[4]["actions"], ["copy", "terminal"])

    def test_the_status_says_what_the_agent_wrote_or_thought_last(self):
        state = busy(reasoning("The user  greets me;\nI <should> greet back."))
        [m] = kortex.view(state, 12)
        self.assertEqual(m[1], "Thinking · 12 s · The user greets me; I <should> greet…")  # one line, plain text
        state["messages"][1]["parts"].append(text("Hello there, nice to see you!"))  # then it answers
        self.assertEqual(kortex.view(state, 75)[0][1], "Working · 1 min 15 s · Hello there, nice to see you!")
        state["messages"][1]["parts"].append(reasoning("x" * 500))
        state["status"] = ("idle", "")  # done: its thoughts are not part of the answer
        [m] = kortex.view(state)
        self.assertEqual((m[1], m[4]["multiline"]), ("Hello there, nice to see you!", True))

    def test_retrying(self):
        state = dict(busy(text("x")), status=("retry", "rate limited"))
        self.assertEqual(kortex.view(state, 5)[0][1], "Retrying: rate limited")

    def test_a_step_of_a_service_says_what_it_was_given(self):
        step = tool("tracker_update_issue", id="DES-103", priority="high", labels=["x"])
        self.assertEqual(kortex.tool_line(step), "tracker_update_issue id=DES-103, priority=high")
        self.assertEqual(kortex.tool_line(tool("cloud_list_notes")), "cloud_list_notes")
        long = kortex.tool_line(tool("cloud_create_note", title="t", content="x" * 300))
        self.assertTrue(len(long) < 125 and long.endswith("…"), long)

    def test_a_denied_step_does_not_end_in_done(self):
        # opencode ends the turn when a step is denied; the palette said "Done.", or
        # what the agent had said before, as if it had gone on.
        no = tool("bash", "error", command="rm -rf ~/Downloads/.cache")
        no["state"]["error"] = "The user rejected permission to use this specific tool call."
        state = {"messages": [user("clean up"), assistant(no)], "status": ("idle", ""), "permissions": []}
        [answer] = kortex.view(state)
        self.assertEqual(answer[1], kortex.DENIED)
        self.assertEqual(answer[4]["subtext"], "Enter copies · 1 step")
        self.assertEqual(kortex.tool_line(no), "bash rm -rf ~/Downloads/.cache (denied)")
        state["messages"] = [user("clean up"), assistant(text("I will delete the cache."), no)]
        self.assertEqual(kortex.view(state)[0][1], "I will delete the cache.<br>" + kortex.DENIED)
        state["messages"] = [user("clean up"), assistant(no, text("Then I leave it."))]  # an agent that goes on
        self.assertEqual(kortex.view(state)[0][1], "Then I leave it.")
        state["messages"] = [user("clean up"), assistant(tool("bash", command="true"))]
        self.assertEqual(kortex.view(state)[0][1], "Done.")
        k, oc, desk, _clock = new()
        k.run("ask" + SEP + "clean up", "", menu=True)
        oc.history = [user("clean up"), assistant(text("I will delete the cache."), no)]
        k.tick()
        self.assertEqual(desk.notes[-1][:2], ("kortex answered", kortex.DENIED))

    def test_a_retry_says_why_and_when(self):
        # opencode's status of a request its provider put off: the provider's words may be
        # an offer, opencode's title says what happened, and `next` when it tries again.
        limit = {"type": "retry", "attempt": 1, "message": "Free usage exceeded, subscribe to Go",
                 "action": {"reason": "free_tier_limit", "title": "Free limit reached"}, "next": 4_000_000}
        at = time.strftime("%H:%M", time.localtime(4000))
        self.assertEqual(kortex.retry_message(limit, now=lambda: 1000), f"Free limit reached · next try {at}")
        self.assertEqual(kortex.retry_message(limit, now=lambda: 3990), "Free limit reached")  # in a moment
        self.assertEqual(kortex.retry_message({"type": "retry", "message": "rate limited"}), "rate limited")
        self.assertEqual(kortex.retry_message({"type": "busy"}), "")
        self.assertEqual(kortex.retry_message({"type": "retry", "message": "x", "next": "soon"}), "x")

    def test_no_step_rows_while_the_agent_works(self):
        # a chat: the commands are the panel's (Show what the agent does)
        state = busy(text("Looking."), tool("bash", command="apt-cache search claude"), tool("read", filePath="/etc/x"))
        matches = kortex.view(state, 9)
        self.assertEqual([m[0] for m in matches], ["answer"])
        self.assertEqual(matches[0][1], "Working · 9 s · reading a file")  # a step was the last thing it did
        self.assertIn("terminal", matches[0][4]["actions"])
        self.assertFalse(matches[0][4]["multiline"])  # one line: the window holds still

    def test_done_the_answer_and_its_steps_on_one_row(self):
        state = busy(text("Looking."), tool("bash", command="df -h"), tool("bash", command="du -sh ~"),
                     text("Your disk is full of <caches>."))
        state["status"] = ("idle", "")
        matches = kortex.view(state)
        self.assertEqual([m[0] for m in matches], ["answer"])
        # what it said after its last step: "Looking." is how it got there
        self.assertEqual(matches[0][1], "Your disk is full of &lt;caches&gt;.")
        self.assertEqual(matches[0][4]["subtext"], "Enter copies · 2 steps")

    def test_done_with_steps_alone(self):
        state = busy(tool("bash", command="patina theme mode light"))
        state["status"] = ("idle", "")
        self.assertEqual([m[1] for m in kortex.view(state)], ["Done."])

    def test_the_agent_stopped(self):
        state = busy(tool("bash", command="ls"), error={"name": "APIError", "data": {"message": "rate limited"}})
        state["status"] = ("idle", "")
        matches = kortex.view(state)
        self.assertEqual([(m[0], m[1]) for m in matches], [("answer", "rate limited")])

    def test_long_answer_keeps_the_start(self):
        # It kept the end: a list of seven tickets began at the sixth.
        state = {"messages": [user("hi"), assistant(text("START " + "word " * 1000 + "END"))], "status": ("idle", ""),
                 "permissions": []}
        shown = kortex.view(state)[0][1]
        self.assertTrue(shown.startswith("START word"))
        self.assertTrue(shown.endswith("word<br>" + kortex.MORE))
        self.assertLessEqual(len(shown), kortex.MAX_ANSWER + len(kortex.MORE) + 4)

    def test_an_answer_s_markdown_is_read(self):
        said = ("Your home is 1.6 GB:\n\n| Path | Size |\n|---|---|\n| `~/.npm` | 506 MB |\n\n### Next\n"
                "- **npm cache**: run `npm cache clean`\n```\nnpm cache clean --force\n```\na < b * c")
        self.assertEqual(kortex.rich(said), "Your home is 1.6 GB:<br><br>Path · Size<br>~/.npm · 506 MB<br><br><b>Next</b>"
                         "<br>• <b>npm cache</b>: run npm cache clean<br>npm cache clean --force<br>a &lt; b * c")

    def test_stopped_by_the_user(self):
        state = busy(tool("bash", command="sleep 60"), error={"name": "MessageAbortedError", "data": {"message": "Aborted"}})
        state["status"] = ("idle", "")
        self.assertEqual(kortex.view(state)[0][1], "Stopped. Say what to do instead.")
        working = busy(text("x"))
        self.assertEqual(kortex.view(working, 3, "opencode", True)[0][4]["actions"], ["copy", "terminal", "stop"])
        self.assertEqual(kortex.view(working, 3, "Hermes")[0][4]["actions"], ["copy", "terminal"])


class Runner(unittest.TestCase):
    def test_ask_needs_no_server(self):
        k, oc, _desk, _ = new()
        oc.fail = "down"
        self.assertEqual([m[0] for m in k.match("ai hello")], ["ask" + SEP + "hello"])

    def test_any_search_is_offered_to_the_agent(self):
        k, oc, desk, _ = new()
        oc.fail = "down"  # offering it needs no server
        self.assertEqual(k.config(menu=True), {"MinLetterCount": 3})
        self.assertEqual(k.config(), {"MinLetterCount": 1})  # every search, from its first letter
        for menu in (True, False):
            [m] = k.match("wifi settings ", menu=menu)
            self.assertEqual(m[0], "ask" + SEP + "wifi settings")
            self.assertEqual(m[1], "Ask kortex: wifi settings")
            self.assertEqual(m[4]["subtext"], "Search, ask, fix")
            self.assertEqual((m[3], m[4]["category"]), (0.1, kortex.CATEGORY_LOWEST))  # below what Plasma finds
        self.assertEqual(k.match("ko"), [])
        self.assertEqual(k.match("ai hello", menu=True)[0][0], "ask" + SEP + "hello")
        oc.fail = None
        k.run(m[0], "")
        self.assertEqual(oc.asked, [("ses_1", "wifi settings")])
        self.assertEqual(desk.queries, ["kortex › "])

    def test_a_question_comes_first(self):
        k, *_ = new()
        [m] = k.match("why is my wifi slow")
        self.assertEqual((m[0], m[3], m[4]["category"]), ("ask" + SEP + "why is my wifi slow", 1.0,
                                                          kortex.CATEGORY_RELEVANCE))
        k.run(m[0], "")
        ids = [m[0] for m in k.match("and how do I fix it?", menu=True)]
        self.assertEqual(ids, ["ask" + SEP + "and how do I fix it?", "continue" + SEP + "and how do I fix it?"])

    def test_another_search_leaves_the_conversation(self):
        k, oc, desk, _clock = new()
        k.run("ask" + SEP + "x", "")
        k.match("kortex ›")
        self.assertTrue(k.viewing)
        k.match("firefox")  # the user types another search over it
        self.assertFalse(k.viewing)
        oc.history = [user("x"), assistant(text("news"))]
        k.tick()
        self.assertEqual(desk.queries, ["kortex › "])  # the search stays

    def test_the_rows_say_kortex_and_the_hint_names_the_agent(self):
        k = kortex.Kortex(kortex.Hermes("http://127.0.0.1:1", "key"), FakeDesktop())
        self.assertEqual(k.match("fix my wifi", menu=True)[0][1], "Ask kortex: fix my wifi")
        self.assertEqual(k.ask_matches("hello")[0][4]["subtext"], "Ask kortex · Enter")
        self.assertEqual(k.ask_matches("")[0][1], "Ask kortex: type your request")
        self.assertEqual(k.ask_matches("")[0][4]["subtext"], "Hermes answers")
        self.assertEqual(k.marker, "kortex ›")

    def test_the_start_menu_opens_the_conversation_in_the_palette(self):
        k, oc, desk, _ = new()
        self.assertEqual([m[0] for m in k.match("ai ›", menu=True)], ["hint"])
        k.run("ask" + SEP + "x", "")
        oc.pending = [PERMISSION]
        ids = [m[0] for m in k.match("ai › and y", menu=True)]
        self.assertEqual(ids, ["continue" + SEP + "and y", "show"])  # no permission it cannot answer
        self.assertFalse(k.viewing)
        k.run("show", "")
        self.assertEqual(desk.queries, ["kortex › ", "kortex › "])

    def test_ask_then_view(self):
        k, oc, desk, _ = new()
        k.run("ask" + SEP + "switch to the light theme", "")
        self.assertEqual(oc.created, ["switch to the light theme"])
        self.assertEqual(oc.asked, [("ses_1", "switch to the light theme")])
        self.assertEqual(desk.queries, ["kortex › "])
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

    def test_what_a_sub_agent_asks_is_asked_here(self):
        # A conversation the agent starts for a part of the work (its `task` tool) asks
        # under its own id; kortex showed nothing, and both waited for good.
        oc = kortex.Opencode("http://127.0.0.1:1", "secret")
        child = dict(FETCH, id="per_2", sessionID="ses_child")
        asked = {"id": "que_1", "sessionID": "ses_child", "questions": [
            {"question": "Which team?", "header": "Team", "options": [{"label": "Design", "description": "d"}]}]}
        answers = {"/session/ses_1/children": [{"id": "ses_child"}], "/permission": [child, dict(FETCH, sessionID="ses_9")],
                   "/question": [asked]}
        with mock.patch.object(oc, "request", lambda _method, path, *_a, **_k: answers[path]):
            self.assertEqual(oc.permissions("ses_1"), [child])
            self.assertEqual(oc.questions("ses_1"), [
                {"id": "que_1", "index": 0, "of": 1, "question": "Which team?", "options": ["Design"]}])
            self.assertEqual(oc.family("ses_1"), {"ses_1", "ses_child"})

    def test_copy_and_terminal(self):
        k, oc, desk, _ = new()
        k.run("ask" + SEP + "x", "")
        oc.history = [user("x"), assistant(text("the answer"))]
        k.run("answer", "")
        self.assertEqual(desk.copied, ["the answer"])
        with mock.patch.object(kortex, "find_opencode", return_value="/usr/bin/opencode"):
            k.run("answer", "terminal")  # Show what the agent does
        command, env = desk.launched[0]
        self.assertEqual(command, ["konsole", "-e", "/usr/bin/opencode", "attach", oc.url, "--session", "ses_1"])
        self.assertEqual(env, {"OPENCODE_SERVER_PASSWORD": "secret"})
        self.assertNotIn("secret", command)
        self.assertEqual(desk.docked, [4242])  # the terminal's window goes to the right edge

    def test_the_panel_without_a_conversation(self):
        oc = kortex.Opencode("http://127.0.0.1:4096", "secret")
        with mock.patch.object(kortex, "find_opencode", return_value="/usr/bin/opencode"), \
                mock.patch.object(oc, "start"):
            self.assertEqual(oc.attach(None)[0], ["/usr/bin/opencode", "attach", "http://127.0.0.1:4096"])
        hermes = kortex.Hermes.from_environment()
        with mock.patch.object(kortex, "find_hermes", return_value="/usr/bin/hermes"):
            self.assertEqual(hermes.attach(None)[0], ["/usr/bin/hermes"])
            self.assertEqual(hermes.attach("s1")[0], ["/usr/bin/hermes", "--resume", "s1"])
        k, oc, desk, _ = new()
        with mock.patch.object(kortex, "find_opencode", return_value="/usr/bin/opencode"):
            k.panel()  # Meta+Shift+A before any request
        self.assertEqual(desk.launched[0][0], ["konsole", "-e", "/usr/bin/opencode", "attach", oc.url])
        self.assertEqual(desk.docked, [4242])

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

    def test_krunner_closed_after_a_start_menu_run_stays_closed(self):
        # The start menu's teardown may come before its run or not at all, and is not
        # KRunner's: the user closing KRunner at once must stop the refresh.
        k, oc, desk, clock = new()
        k.teardown(menu=True)
        k.run("ask" + SEP + "x", "", menu=True)
        k.match("ai ›")
        self.assertTrue(k.viewing)
        clock.t += 0.5
        k.teardown()  # the user closes KRunner
        self.assertFalse(k.viewing)
        oc.history = [user("x"), assistant(text("news"))]
        k.tick()
        self.assertEqual(desk.queries, ["kortex › "])  # not opened again

    def test_a_closed_palette_asks_nothing_once_the_agent_is_done(self):
        k, oc, _desk, clock = new()
        k.run("ask" + SEP + "x", "")
        k.match("ai ›")
        clock.t += 2
        k.teardown()
        oc.history = [user("x"), assistant(text("done"))]
        k.tick()  # the request is followed until it is answered
        self.assertIsNone(k.asked_at)
        fetched = oc.services_added
        for _ in range(5):
            k.tick()
        self.assertEqual(oc.services_added, fetched)  # nothing more to ask while KRunner is closed
        k.match("ai ›")  # a look at the conversation fetches it again
        self.assertGreater(oc.services_added, fetched)

    def test_meta_a_opens_the_conversation_while_a_request_runs(self):
        k, oc, desk, clock = new()
        k.open()
        self.assertEqual(desk.queries, [""])  # nothing runs: an empty field for a request
        k.run("ask" + SEP + "x", "")
        oc.state = ("busy", "")
        k.match("ai ›")
        clock.t += 2
        k.teardown()
        k.tick()
        k.open()
        self.assertEqual(desk.queries[-1], "kortex › ")
        oc.history, oc.state = [user("x"), assistant(text("done"))], ("idle", "")
        k.tick()
        k.open()
        self.assertEqual(desk.queries[-1], "")  # answered: Meta+A is for the next request

    def test_the_start_menu_closing_late_keeps_the_conversation_on_screen(self):
        k, oc, desk, clock = new()
        k.run("ask" + SEP + "x", "", menu=True)
        k.match("ai ›")
        clock.t += 3
        k.teardown(menu=True)
        oc.history = [user("x"), assistant(text("news"))]
        k.tick()
        self.assertEqual(len(desk.queries), 2)  # KRunner gets the news

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
        self.assertNotEqual(desk.queries[-1], "kortex › ")
        self.assertEqual(k.parse(desk.queries[-1]), ("view", ""))
        self.assertEqual(len(desk.queries), before + 1)
        k.match(desk.queries[-1])
        oc.history = [user("x"), assistant(text("hello"))]
        k.tick()
        self.assertEqual(desk.queries[-1], "kortex › ")

    def test_the_seconds_count_while_the_agent_works(self):
        k, oc, desk, clock = new()
        k.run("ask" + SEP + "x", "")
        oc.history = [user("x")]
        oc.state = ("busy", "")
        k.match("kortex ›")
        k.tick()
        before = len(desk.queries)
        k.tick()
        self.assertEqual(len(desk.queries), before)
        clock.t += 1
        k.tick()  # a second later: KRunner shows it again
        self.assertEqual(len(desk.queries), before + 1)
        self.assertEqual(k.match(desk.queries[-1])[0][1], "Working · 1 s")
        oc.pending = [PERMISSION]
        k.tick()
        k.match(desk.queries[-1])
        before = len(desk.queries)
        clock.t += 5
        k.tick()  # an Allow row holds still to be clicked
        self.assertEqual(len(desk.queries), before)

    def test_a_follow_up_being_typed_is_never_rewritten(self):
        k, oc, desk, clock = new()
        k.run("ask" + SEP + "x", "")
        oc.history = [user("x")]
        oc.state = ("busy", "")
        k.match("kortex › change the boot ord")
        before = len(desk.queries)
        for _ in range(3):  # the seconds count on, the answer arrives: the field stays the user's
            clock.t += 1
            k.tick()
        oc.history = [user("x"), assistant(text("done"))]
        k.tick()
        self.assertEqual(len(desk.queries), before)
        # each key shows what is new, and once the follow-up is gone the refreshes go on
        self.assertEqual(k.match("kortex › change the boot order")[0][0], "continue" + SEP + "change the boot order")
        k.match("kortex › ")
        oc.history = [user("x"), assistant(text("done, and more"))]
        k.tick()
        self.assertEqual(len(desk.queries), before + 1)
        self.assertEqual(desk.queries[-1], "kortex ›" + kortex.JOINER + " ")

    def test_a_refresh_is_dropped_once_krunner_calls_again(self):
        k, oc, desk, clock = new()
        k.run("ask" + SEP + "x", "")
        oc.history = [user("x")]
        oc.state = ("busy", "")
        k.match("kortex › ")
        clock.t += 1
        k.tick()
        self.assertTrue(desk.still())  # nothing typed since: it goes out
        k.match("c")  # typed over the conversation before the refresh went out
        self.assertFalse(desk.still())
        k.match("kortex › ")
        clock.t += 1
        k.tick()
        k.teardown()  # the field emptied, or KRunner closed
        self.assertFalse(desk.still())

    def test_tick_notifies_when_closed(self):
        k, oc, desk, _clock = new()
        k.run("ask" + SEP + "x", "")
        oc.pending = [PERMISSION]
        k.tick()
        k.tick()
        self.assertEqual(len(desk.notes), 1)
        self.assertEqual(desk.notes[0][0], "kortex asks first")
        self.assertIn("patina theme mode light", desk.notes[0][1])
        oc.pending = [dict(PERMISSION, id="per_2", metadata={"command": "ls <x; rm -r y >z"})]
        k.tick()
        self.assertIn("bash: ls &lt;x; rm -r y &gt;z", desk.notes[1][1])  # the body is markup too
        k.show(2)  # its Show
        self.assertEqual(desk.queries[-1], "kortex › ")
        k.show(99)  # another program's notification
        self.assertEqual(len(desk.queries), 2)

    def test_a_notification_follows_the_request_while_krunner_is_closed(self):
        k, oc, desk, clock = new()
        k.run("ask" + SEP + "why is my wifi slow", "")
        k.match("kortex ›")
        clock.t += 2
        k.teardown()  # the user clicks elsewhere
        oc.history = [user("why is my wifi slow"), assistant(reasoning("Let me look"))]
        oc.state = ("busy", "")
        k.tick()
        self.assertEqual(desk.notes, [])  # not at once
        clock.t += 1
        k.tick()
        k.tick()
        self.assertEqual(len(desk.notes), 1)
        summary, body, _replaces, transient = desk.notes[0]
        self.assertEqual((summary, transient), ("kortex is working on it", True))
        self.assertIn("why is my wifi slow", body)
        oc.history[1]["parts"].append(text("It runs on 2.4 GHz <slow>."))
        oc.state = ("idle", "")
        k.tick()
        self.assertEqual(desk.notes[1], ("kortex answered", "It runs on 2.4 GHz &lt;slow&gt;.", 1, False))
        k.tick()
        self.assertEqual(len(desk.notes), 2)
        k.show(2)
        self.assertEqual(desk.queries[-1], "kortex › ")

    def test_a_follow_up_is_not_answered_by_the_answer_before_it(self):
        # What kortex knew of the conversation when a follow-up was sent held the last
        # answer: a follow-up that repeats the request read as replied to, at once.
        k, oc, desk, _clock = new()
        k.run("ask" + SEP + "try again", "", menu=True)
        oc.history = [user("try again"), assistant(text("first answer"))]
        k.tick()
        self.assertEqual(desk.notes[-1][:2], ("kortex answered", "first answer"))
        k.run("continue" + SEP + "try again", "", menu=True)
        self.assertIsNone(k.current())  # nothing is known of the conversation yet
        k.follow()
        self.assertEqual(len(desk.notes), 1)
        oc.history += [user("try again")]
        oc.state = ("busy", "")
        k.tick()
        self.assertEqual(len(desk.notes), 1)
        oc.history += [assistant(text("second answer"))]
        oc.state = ("idle", "")
        k.tick()
        self.assertEqual(desk.notes[-1][:2], ("kortex answered", "second answer"))

    def test_another_conversation_ends_the_following_of_a_request(self):
        k, oc, desk, clock = new()
        k.run("ask" + SEP + "why is my wifi slow", "")
        oc.history, oc.state = [user("why is my wifi slow")], ("busy", "")
        k.run("session" + SEP + "ses_old", "")  # an older conversation from the list
        k.teardown()
        clock.t += 2
        k.teardown()  # the user closes KRunner
        clock.t += 10
        k.tick()
        k.tick()
        self.assertEqual(desk.notes, [])  # no "working on it" for a conversation that is not asked

    def test_the_journal_tells_how_a_request_went(self):
        k, oc, desk, clock = new()
        with mock.patch.object(kortex, "log") as logged:
            k.run("ask" + SEP + "x", "")
            k.match("kortex ›")
            oc.history, oc.state, oc.pending = [user("x")], ("busy", ""), [PERMISSION]
            k.tick()
            k.tick()
            k.match(desk.queries[-1])  # KRunner asks again for what kortex showed it
            k.match(desk.queries[-1])
            oc.history, oc.state, oc.pending = [user("x"), assistant(text("done"))], ("idle", ""), []
            clock.t += 4
            k.tick()
        self.assertEqual([c.args[0] for c in logged.call_args_list], [
            "asking opencode", "opencode asks first: bash",
            "KRunner asked again: the palette follows the conversation", "kortex answered after 4 s"])

    def test_the_notification_tells_how_a_task_ended(self):
        k, oc, desk, _clock = new()
        k.run("ask" + SEP + "install photocraft", "", menu=True)
        oc.history = [user("install photocraft"), assistant(
            text("PhotoCraft comes with the ArtCraft Launcher. " + "x" * 400), tool("bash", command="patina install"),
            text("The launcher is open: pick PhotoCraft there."))]
        k.tick()
        self.assertEqual(desk.notes[-1][1], "The launcher is open: pick PhotoCraft there.")
        oc.history = [user("explain"), assistant(text("y" * 500))]
        k.run("ask" + SEP + "explain", "", menu=True)
        k.tick()
        self.assertEqual(desk.notes[-1][1], "y" * kortex.MAX_NOTE + "…")  # a long answer from its start

    def test_an_answer_on_screen_needs_no_notification(self):
        k, oc, desk, clock = new()
        k.run("ask" + SEP + "x", "")
        k.match("kortex ›")
        clock.t += 30
        oc.history = [user("x"), assistant(text("done"))]
        k.tick()
        oc.history = [user("x"), assistant(error={"name": "APIError", "data": {"message": "rate limited"}})]
        k.run("continue" + SEP + "x", "")
        k.tick()
        self.assertEqual(desk.notes, [])

    def test_a_stopped_agent_is_told_of(self):
        k, oc, desk, _clock = new()
        k.run("ask" + SEP + "x", "", menu=True)  # KRunner never opened
        oc.history = [user("x"), assistant(error={"name": "APIError", "data": {"message": "rate limited"}})]
        k.tick()
        self.assertEqual(desk.notes, [("kortex stopped", "rate limited", 0, False)])

    def test_a_question_of_the_agent_is_answered_in_the_palette(self):
        k, oc, desk, _clock = new()
        k.run("ask" + SEP + "file a ticket", "")
        oc.history, oc.state = [user("file a ticket"), assistant(tool("question", "running"))], ("busy", "")
        oc.asking = [{"id": "que_1", "index": 0, "of": 1, "question": "Which team?", "options": ["Design", "Platform"]}]
        k.match("kortex ›")
        rows = k.match("kortex ›")
        option = next(m for m in rows if m[1] == "Platform")
        self.assertIsNone(k.elapsed())  # the rows hold still to be picked
        k.run(option[0], "")
        self.assertEqual(oc.answered, [("que_1", [["Platform"]])])
        self.assertFalse([m for m in k.match("kortex ›") if m[0].startswith("option")])
        # typed in place of a pick: the words after the marker are the answer, not a new request
        oc.asking = [{"id": "que_2", "index": 0, "of": 1, "question": "Which title?", "options": []}]
        k.tick()
        typed = k.match("kortex › Favicon is blurry")[0]
        self.assertEqual((typed[1], typed[4]["subtext"]), ("Favicon is blurry", "Your answer · Enter"))
        k.run(typed[0], "")
        self.assertEqual(oc.answered[-1], ("que_2", [["Favicon is blurry"]]))
        self.assertEqual(oc.asked, [("ses_1", "file a ticket")])  # nothing new was asked
        self.assertEqual(desk.notes, [])

    def test_a_request_of_several_questions_is_answered_once_all_are(self):
        k, oc, _desk, _clock = new()
        k.run("ask" + SEP + "x", "")
        oc.history, oc.state = [user("x")], ("busy", "")
        oc.asking = [{"id": "que_1", "index": 0, "of": 2, "question": "Team?", "options": ["Design"]},
                     {"id": "que_1", "index": 1, "of": 2, "question": "Priority?", "options": ["high", "low"]}]
        k.match("kortex ›")
        first = next(m for m in k.match("kortex ›") if m[0].startswith("option"))
        k.run(first[0], "")
        self.assertEqual(oc.answered, [])  # the second is still to answer
        k.tick()  # the agent still lists both; the first has its answer
        rows = k.match("kortex ›")
        self.assertEqual(rows[0][1], "kortex asks: Priority?")
        k.run(next(m for m in rows if m[1] == "low")[0], "")
        self.assertEqual(oc.answered, [("que_1", [["Design"], ["low"]])])

    def test_a_question_reaches_a_closed_krunner(self):
        k, oc, desk, _clock = new()
        k.run("ask" + SEP + "x", "", menu=True)
        oc.history, oc.state = [user("x")], ("busy", "")
        oc.asking = [{"id": "que_1", "index": 0, "of": 1, "question": "Which team?", "options": ["Design"]}]
        k.tick()
        k.tick()
        self.assertEqual([n[:2] for n in desk.notes], [("kortex asks you", "Which team?")])

    def test_stop_ends_what_the_agent_does(self):
        k, oc, _desk, _clock = new()
        k.run("ask" + SEP + "x", "")
        oc.history, oc.state = [user("x"), assistant(text("on it"))], ("busy", "")
        k.match("kortex ›")
        row = k.match("kortex ›")[0]
        self.assertEqual(row[4]["actions"], ["copy", "terminal", "stop"])
        k.run("answer", "stop")
        self.assertEqual(oc.aborted, ["ses_1"])

    def test_a_server_that_exits_ends_the_request(self):
        # kortex said "working" until it stopped itself a quarter of an hour later.
        k, oc, desk, clock = new()
        k.run("ask" + SEP + "x", "", menu=True)
        oc.history, oc.state = [user("x")], ("busy", "")
        k.tick()
        oc.gone, oc.url = True, None
        clock.t += 5
        k.tick()
        self.assertEqual(desk.notes[-1][:2], ("kortex stopped", "opencode stopped while it worked; ask again"))
        self.assertIsNone(k.asked_at)
        k.tick()
        self.assertEqual(len([n for n in desk.notes if n[0] == "kortex stopped"]), 1)

    def test_a_put_off_request_is_told_of_once(self):
        k, oc, desk, clock = new()
        k.run("ask" + SEP + "x", "", menu=True)
        oc.history, oc.state = [user("x")], ("retry", "Free limit reached · next try 00:39")
        clock.t += 5
        k.tick()
        k.tick()
        waits = [n for n in desk.notes if n[0] == "kortex waits for the provider"]
        self.assertEqual(len(waits), 1)
        self.assertIn("next try 00:39", waits[0][1])
        self.assertFalse(waits[0][3])  # it stays in the history

    def test_an_older_answer_is_not_the_reply(self):
        k, oc, desk, _clock = new()
        k.run("ask" + SEP + "first", "", menu=True)
        oc.history = [user("first"), assistant(text("old"))]
        k.tick()
        k.run("continue" + SEP + "second", "", menu=True)
        k.tick()  # the agent has not stored the request yet: its last reply is the old one
        self.assertEqual(len(desk.notes), 1)
        oc.history += [user("second"), assistant(text("new"))]
        k.tick()
        self.assertEqual(desk.notes[-1][:2], ("kortex answered", "new"))

    def test_idle(self):
        k, oc, _desk, clock = new()
        self.assertFalse(k.idle())
        clock.t += kortex.IDLE_EXIT + 1
        self.assertTrue(k.idle())
        k.session = "ses_1"
        oc.state = ("busy", "")
        k.tick()  # kortex knows the status from its last request
        self.assertFalse(k.idle())


class Later:
    """The worker thread, held: requests to the agent wait until the test lets them run."""

    def __init__(self):
        self.jobs = []

    def __call__(self, work, then):
        self.jobs.append((work, then))

    def run(self):
        while self.jobs:
            kortex.at_once(*self.jobs.pop(0))


def held():
    k, oc, desk, clock = new()
    k.background = later = Later()
    return k, oc, desk, clock, later


class Background(unittest.TestCase):
    """KRunner never waits for the agent: it gets what kortex knows, and the news later."""

    def test_a_request_shows_a_waiting_row_until_the_agent_answers(self):
        k, oc, desk, _clock, later = held()
        k.run("ask" + SEP + "x", "")
        self.assertEqual(desk.queries, ["kortex › "])  # KRunner opens on the conversation at once
        self.assertEqual(oc.created, [])  # while nothing has reached the agent yet
        self.assertEqual([m[0] for m in k.match("ai ›")], ["waiting"])
        later.run()
        self.assertEqual(oc.asked, [("ses_1", "x")])
        oc.history = [user("x"), assistant(text("done"))]
        k.tick()  # the request has reached the agent: KRunner asks again
        self.assertEqual(len(desk.queries), 2)
        for _ in range(3):  # every keystroke is a match; the agent is asked once
            self.assertEqual([m[0] for m in k.match(desk.queries[-1])], ["waiting"])
        self.assertEqual(len(later.jobs), 1)
        later.run()
        k.tick()
        self.assertEqual(len(desk.queries), 3)
        [answer] = k.match(desk.queries[-1])
        self.assertEqual((answer[0], answer[1]), ("answer", "done"))

    def test_ai_lists_the_conversations_once_the_agent_answers(self):
        k, _oc, desk, _clock, later = held()
        self.assertEqual([m[0] for m in k.match("ai")], ["hint", "waiting"])
        later.run()
        k.tick()
        self.assertEqual(desk.queries, ["ai" + kortex.JOINER + " "])  # `ai` still, as a query that differs
        self.assertEqual([m[0] for m in k.match(desk.queries[-1])], ["hint", "session" + SEP + "ses_old"])
        later.run()
        k.tick()
        self.assertEqual(len(desk.queries), 1)  # nothing new

    def test_an_answered_permission_leaves_the_view_at_once(self):
        k, oc, _desk, _clock, later = held()
        k.run("ask" + SEP + "x", "")
        oc.pending = [PERMISSION]
        later.run()
        k.match("ai ›")
        later.run()
        self.assertEqual(k.match("ai ›")[0][0], "permission" + SEP + "per_1")
        k.run("permission" + SEP + "per_1", "allow-once")
        self.assertNotIn("permission" + SEP + "per_1", [m[0] for m in k.match("ai ›")])
        later.run()
        self.assertEqual(oc.replies, [("per_1", "once")])

    def test_not_idle_while_a_request_is_on_its_way(self):
        k, _oc, _desk, clock, later = held()
        k.run("ask" + SEP + "x", "")
        clock.t += kortex.IDLE_EXIT + 1
        self.assertFalse(k.idle())
        later.run()
        self.assertTrue(k.idle())

    def test_a_failed_request_shows_why(self):
        k, oc, desk, _clock, later = held()
        oc.fail = "opencode did not answer within 30 seconds"
        k.run("ask" + SEP + "x", "")
        k.match("ai ›")
        later.run()
        k.tick()
        self.assertEqual(len(desk.queries), 2)
        problem = k.match(desk.queries[-1])[0]
        self.assertEqual((problem[0], problem[4]["subtext"]), ("problem", "Not sent"))
        self.assertIn("30 seconds", problem[1])


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

    def test_secrets_are_filled_in_as_opencode_fills_them(self):
        # patina's GitHub and Nextcloud presets name a token file; POST /mcp takes the
        # config as it is, so a placeholder sent unfilled reached the server literally.
        token = Path(self.dir.name) / "github.token"
        token.write_text("ghp_secret\n")
        (self.config.parent / "nc.token").write_text("app-password")
        self.write({"github": {"type": "remote", "url": "https://api.githubcopilot.com/mcp/",
                               "headers": {"Authorization": "Bearer {file:" + str(token) + "}"}},
                    "nextcloud": {"type": "local", "command": ["uvx", "nextcloud-mcp-server"],
                                  "environment": {"NEXTCLOUD_PASSWORD": "{file:nc.token}",
                                                  "NEXTCLOUD_HOST": "{env:KORTEX_TEST_HOST}"}}})
        with mock.patch.dict(os.environ, {"KORTEX_TEST_HOST": "https://cloud.example.org"}):
            self.oc.add_services()
        posted = {body["name"]: body["config"] for method, _path, body in self.calls if method == "POST"}
        self.assertEqual(posted["github"]["headers"]["Authorization"], "Bearer ghp_secret")
        self.assertEqual(posted["nextcloud"]["environment"],
                         {"NEXTCLOUD_PASSWORD": "app-password", "NEXTCLOUD_HOST": "https://cloud.example.org"})

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

    def test_always_is_not_offered_on_hermes_rows_of_sudo_and_the_like(self):
        # Hermes keeps an Always for good, for the kind of command that asked: one Always
        # on `sudo -A apt-get clean` let every later sudo run unasked.
        def asked(command, why="sudo with privilege flag (stdin/askpass/shell/list)"):
            return {"id": "req_1", "sessionID": "ses_1", "run": "run_1", "permission": why, "patterns": [command]}
        for command in ("sudo -A apt-get clean", "rm -rf ~/junk", "bash -ic 'type ll'", "ls /tmp; sudo -A umount /home/u/Downloads",
                        "out=$(true) && rm /tmp/backup.iso", "python3 - <<'EOF'"):
            self.assertFalse(kortex.always_offered(asked(command)), command)
            row = kortex.view({"messages": [], "status": ("busy", ""), "permissions": [asked(command)]})[0]
            self.assertEqual(row[4]["actions"], ["allow-once", "deny"])
        for command in ("update_issue {\"id\":\"PLA-1\"}", "cp ~/.bashrc ~/.bashrc.bak", "flatpak install --user -y flathub org.kde.kalk"):
            self.assertTrue(kortex.always_offered(asked(command, "tracker")), command)

    def test_a_question_hermes_gave_up_on_leaves_the_palette(self):
        # After `approvals.timeout` Hermes goes on without the command. The row stayed,
        # "Waiting for your answer", and Allow on it was "HTTP Error 409: Conflict".
        # Its run still reads "waiting_for_approval" then; that the model writes again is
        # what says the question is over.
        asked = {}
        request = hermes_event(event="approval.request", request_id="req_1", command="sudo -A true", description="sudo")
        other = hermes_event(event="approval.request", request_id="req_9", command="rm -r x", description="delete")
        kortex.note_hermes_event(asked, request, "ses_1", "run_1")
        kortex.note_hermes_event(asked, other, "ses_2", "run_2")
        for still in ("tool.completed", "tool.started"):  # the command that waited ends, another starts
            kortex.note_hermes_event(asked, hermes_event(event=still, tool="terminal"), "ses_1", "run_1")
        self.assertEqual(sorted(asked), ["req_1", "req_9"])
        kortex.note_hermes_event(asked, hermes_event(event="message.delta", delta="It"), "ses_1", "run_1")
        self.assertEqual(sorted(asked), ["req_9"])  # another run's question stays
        # and an answer that comes too late is not an error
        self.h.asked = {"req_2": {"id": "req_2", "sessionID": "ses_1", "run": "run_1", "permission": "x", "patterns": ["y"]}}

        def conflict(method, path, body=None, start=True, timeout=10):
            cause = kortex.urllib.error.HTTPError("http://127.0.0.1:8642" + path, 409, "Conflict", {}, None)
            raise kortex.AgentError(f"POST {path}: {cause}") from cause
        self.h.request = conflict
        self.h.reply("req_2", "once")
        self.assertEqual(self.h.asked, {})

    def test_a_title_in_use_gets_the_time_behind_it(self):
        # Hermes takes a title once; the same request asked again was "HTTP Error 400".
        titles = []

        def request(method, path, body=None, start=True, timeout=10):
            titles.append(body["title"])
            if len(titles) == 1:
                cause = kortex.urllib.error.HTTPError("http://127.0.0.1:8642/api/sessions", 400, "Bad Request", {}, None)
                raise kortex.AgentError(f"POST /api/sessions: {cause}") from cause
            return {"session": {"id": "api_2"}}
        self.h.request = request
        self.assertEqual(self.h.new_session("what's in my calendar this week"), "api_2")
        self.assertEqual(titles[0], "what's in my calendar this week")
        self.assertTrue(titles[1].startswith("what's in my calendar this week · "))
        self.assertNotEqual(titles[0], titles[1])

        def down(*_args, **_kwargs):
            cause = kortex.urllib.error.HTTPError("http://127.0.0.1:8642/api/sessions", 500, "x", {}, None)
            raise kortex.AgentError("POST /api/sessions") from cause
        self.h.request = down
        with self.assertRaises(kortex.AgentError):
            self.h.new_session("x")

    def test_approval_requests_come_and_go_with_the_stream(self):
        asked = {}
        request = hermes_event(event="approval.request", request_id="req_1", command="rm -r old-photos",
                               description="recursive delete", choices=["once", "session", "always", "deny"])
        self.assertFalse(kortex.note_hermes_event(asked, request, "ses_1", "run_1"))
        self.assertEqual(asked["req_1"], {"id": "req_1", "sessionID": "ses_1", "run": "run_1",
                                          "permission": "recursive delete", "patterns": ["rm -r old-photos"]})
        gate = hermes_event(event="approval.request", request_id="req_2", description='tracker: update_issue {"id":"PLA-1"}',
                            command="<mcp__tracker__update_issue> (plugin approval rule)")
        kortex.note_hermes_event(asked, gate, "ses_1", "run_1")  # a hook's rule: its message says what is asked
        self.assertEqual((asked["req_2"]["permission"], asked["req_2"]["patterns"]), ("tracker", ['update_issue {"id":"PLA-1"}']))
        del asked["req_2"]
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
        said, steps, error = kortex.reply_of(messages)
        self.assertEqual(steps, ["terminal rm -r old-photos", "read_file notes.txt (failed)", "terminal ls"])
        self.assertEqual((said, error), (["Removed it."], ""))
        self.assertEqual(messages[1]["parts"][2]["state"]["status"], "running")  # no result yet

    def test_a_failed_run_shows_its_error(self):
        messages = kortex.hermes_messages([{"role": "user", "content": "x"}], {"status": "failed", "error": "model unreachable"})
        self.assertEqual(kortex.reply_of(messages), ([], [], "model unreachable"))

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
    def test_the_runner_metadata_names_this_kortex(self):
        desktop = (Path(__file__).parent.parent / "kortex.desktop").read_text().splitlines()
        self.assertIn("X-KDE-PluginInfo-Version=" + kortex.VERSION, desktop)
        self.assertIn("X-Plasma-DBusRunner-Service=" + kortex.SERVICE, desktop)
        self.assertIn("X-Plasma-DBusRunner-Path=" + kortex.OBJECT_PATH, desktop)
        self.assertIn("X-KDE-PluginInfo-Name=" + kortex.RUNNER_ID, desktop)

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


def http_error(code):
    """A request that fails as the agent's server answers with `code`."""
    def request(method, path, *_args, **_kwargs):
        cause = kortex.urllib.error.HTTPError("http://127.0.0.1:1" + path, code, "x", {}, None)
        raise kortex.AgentError(f"{method} {path}: {cause}") from cause
    return request


class Replies(unittest.TestCase):
    """An answer to a permission request that the agent no longer waits for is no error;
    one that did not reach the agent can be given again."""

    def test_opencode_takes_404_as_answered(self):
        oc = kortex.Opencode("http://127.0.0.1:1", "secret")
        with mock.patch.object(oc, "request", http_error(404)):
            oc.reply("per_1", "once")  # answered in the terminal already
        with mock.patch.object(oc, "request", http_error(500)), self.assertRaises(kortex.AgentError):
            oc.reply("per_1", "once")

    def test_hermes_keeps_a_request_until_the_answer_arrives(self):
        h = kortex.Hermes("http://127.0.0.1:8642", "key")
        h.asked = {"req_1": {"id": "req_1", "run": "run_1", "sessionID": "ses_1"}}
        with mock.patch.object(h, "request", http_error(502)), self.assertRaises(kortex.AgentError):
            h.reply("req_1", "once")
        self.assertIn("req_1", h.asked)  # shown again, to be answered again
        with mock.patch.object(h, "request", http_error(404)):
            h.reply("req_1", "once")  # the run is over
        self.assertEqual(h.asked, {})
        h.asked = {"req_2": {"id": "req_2", "run": "run_2", "sessionID": "ses_1"}}
        with mock.patch.object(h, "request", return_value={}) as sent:
            h.reply("req_2", "reject")
        sent.assert_called_once_with("POST", "/v1/runs/run_2/approval", {"request_id": "req_2", "choice": "deny"})
        self.assertEqual(h.asked, {})

    def test_the_view_typed_in_capitals_is_shown_again_as_the_view(self):
        k, oc, desk, _clock = new()
        k.run("ask" + SEP + "x", "")
        k.match("AI ›")
        oc.history = [user("x"), assistant(text("news"))]
        k.tick()
        self.assertEqual(k.parse(desk.queries[-1]), ("view", ""))
        self.assertEqual(desk.queries[-1], "kortex ›" + kortex.JOINER + " ")
