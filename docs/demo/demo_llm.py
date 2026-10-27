"""The scripted model for the kortex demo recording: an OpenAI-compatible endpoint that
streams slowly, like a small local model, and uses tools the way an agent would."""

import json
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

DELAY = 0.12  # seconds between streamed words


def content(message):
    c = message.get("content") or ""
    if isinstance(c, list):
        return " ".join(p.get("text", "") for p in c if isinstance(p, dict))
    return c


def plan(body):
    """(text, tool call or None) for one chat request."""
    messages = body.get("messages", [])
    system = " ".join(content(m) for m in messages if m.get("role") == "system").lower()
    last_user = max((i for i, m in enumerate(messages) if m.get("role") == "user"), default=-1)
    asked = content(messages[last_user]).lower() if last_user >= 0 else ""
    tools_after = [m for m in messages[last_user + 1:] if m.get("role") == "tool"]
    if not body.get("tools") or "title" in system and "generate" in system:
        if "disk" in asked:
            return "Disk space at home", None
        if "photocraft" in asked:
            return "PhotoCraft", None
        return "Conversation", None
    if "disk" in asked:
        if not tools_after:
            return "Let me look.", ("bash", {"command": "df -h /", "description": "Show free disk space"})
        out = content(tools_after[-1])
        row = next((line for line in out.splitlines() if line.rstrip().endswith(" /")), "")
        cols = row.split()
        if len(cols) >= 5:
            return (f"Your system disk has {cols[3]} free of {cols[1]} ({cols[4]} used). "
                    "That is plenty; nothing to clean up."), None
        return "I could not read the disk usage.", None
    if "photocraft" in asked:
        # Two changes, each asked for: patina's artcraft bundle brings the ArtCraft
        # Launcher, and the launcher installs PhotoCraft once the user picks it.
        if not tools_after:
            return "PhotoCraft comes with the ArtCraft Launcher, patina's artcraft bundle. I will install it.", (
                "bash", {"command": "patina install --bundles artcraft",
                         "description": "Install patina's artcraft bundle"})
        if len(tools_after) == 1:
            return "Installed. Now the launcher:", (
                "bash", {"command": "setsid -f artcraft-launcher", "description": "Open the ArtCraft Launcher"})
        return "The launcher is open: pick PhotoCraft there, and it keeps it up to date.", None
    return "I can answer questions, change settings once you allow it, and run commands for you.", None


def chunk(delta, finish=None):
    return {"id": "chatcmpl-demo", "object": "chat.completion.chunk", "created": 0, "model": "demo",
            "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps({"object": "list", "data": [{"id": "demo", "object": "model"}]}).encode())

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        text, call = plan(body)
        if not body.get("stream"):
            raw = json.dumps({"id": "x", "object": "chat.completion", "created": 0, "model": "demo",
                              "choices": [{"index": 0, "message": {"role": "assistant", "content": text},
                                           "finish_reason": "stop"}],
                              "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        slow = bool(body.get("tools"))

        def send(event):
            self.wfile.write(b"data: " + json.dumps(event).encode() + b"\n\n")
            self.wfile.flush()

        send(chunk({"role": "assistant", "content": ""}))
        words = re.findall(r"\S+\s*", text)
        for w in words:
            send(chunk({"content": w}))
            if slow:
                time.sleep(DELAY)
        if call:
            name, args = call
            send(chunk({"tool_calls": [{"index": 0, "id": "call_1", "type": "function",
                                        "function": {"name": name, "arguments": json.dumps(args)}}]}))
            send(chunk({}, "tool_calls"))
        else:
            send(chunk({}, "stop"))
        self.wfile.write(b"data: [DONE]\n\n")


def serve():
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server
