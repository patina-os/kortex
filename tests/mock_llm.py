# SPDX-License-Identifier: GPL-3.0-or-later
"""A model for the end-to-end test: an OpenAI-compatible chat endpoint with fixed
replies. A request that mentions a file gets a bash tool call that creates it; once the
tool has run, or for any other request, it answers in text."""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

TOOL_COMMAND = "touch kortex-proof"
DONE = "Done. kortex-proof is in your home directory."
HELLO = "Hello from the test model."


def content(message):
    c = message.get("content") or ""
    if isinstance(c, list):
        return " ".join(p.get("text", "") for p in c if isinstance(p, dict))
    return c


def reply_for(body):
    """("text", words) or ("tool", arguments) for a chat request."""
    messages = body.get("messages", [])
    last_user = max((i for i, m in enumerate(messages) if m.get("role") == "user"), default=-1)
    after = messages[last_user + 1:]
    if any(m.get("role") == "tool" for m in after):
        return "text", DONE
    if body.get("tools") and "file" in content(messages[last_user]).lower():
        return "tool", {"command": TOOL_COMMAND, "description": "Create the file kortex-proof"}
    return "text", HELLO


def chunk(delta, finish=None):
    return {"id": "chatcmpl-test", "object": "chat.completion.chunk", "created": 0, "model": "mock",
            "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        if self.path.rstrip("/").endswith("/models"):
            self.send(200, {"object": "list", "data": [{"id": "mock", "object": "model"}]})
        else:
            self.send(404, {})

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        kind, value = reply_for(body)
        if not body.get("stream"):
            message = {"role": "assistant", "content": value} if kind == "text" else {
                "role": "assistant", "content": None,
                "tool_calls": [{"id": "call_1", "type": "function",
                                "function": {"name": "bash", "arguments": json.dumps(value)}}]}
            self.send(200, {"id": "chatcmpl-test", "object": "chat.completion", "created": 0, "model": "mock",
                            "choices": [{"index": 0, "message": message,
                                         "finish_reason": "stop" if kind == "text" else "tool_calls"}],
                            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}})
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        events = [chunk({"role": "assistant", "content": ""})]
        if kind == "text":
            words = value.split(" ")
            events += [chunk({"content": w + (" " if i < len(words) - 1 else "")}) for i, w in enumerate(words)]
            events.append(chunk({}, "stop"))
        else:
            events.append(chunk({"tool_calls": [{"index": 0, "id": "call_1", "type": "function",
                                                 "function": {"name": "bash", "arguments": json.dumps(value)}}]}))
            events.append(chunk({}, "tool_calls"))
        for e in events:
            self.wfile.write(b"data: " + json.dumps(e).encode() + b"\n\n")
            self.wfile.flush()
        self.wfile.write(b"data: [DONE]\n\n")

    def send(self, code, payload):
        raw = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


def serve():
    """Start the model on a free port in a thread; returns the server."""
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server
