# SPDX-License-Identifier: GPL-3.0-or-later
"""An MCP server for the end-to-end test: stdio, one tool (`ping`), nothing else. It
stands for a service connected while kortex runs; the test checks that the model sees
its tool on the next request."""

import json
import sys


def answer(request):
    method = request.get("method")
    if method == "initialize":
        return {"protocolVersion": request.get("params", {}).get("protocolVersion", "2025-06-18"),
                "capabilities": {"tools": {}}, "serverInfo": {"name": "kortex-test", "version": "1"}}
    if method == "tools/list":
        return {"tools": [{"name": "ping", "description": "Answers pong.",
                           "inputSchema": {"type": "object", "properties": {}}}]}
    if method == "tools/call":
        return {"content": [{"type": "text", "text": "pong"}]}
    if method == "ping":
        return {}
    return None


for line in sys.stdin:
    try:
        request = json.loads(line)
    except ValueError:
        continue
    if "id" not in request:
        continue  # a notification
    result = answer(request)
    reply = {"jsonrpc": "2.0", "id": request["id"]}
    if result is None:
        reply["error"] = {"code": -32601, "message": "method not found"}
    else:
        reply["result"] = result
    print(json.dumps(reply), flush=True)
