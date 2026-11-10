#!/usr/bin/python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""A stand-in for an external service, as an MCP server on stdio. Two flavours:

  mock_service.py tracker <state.json>   issues like Linear's: teams, issues, comments
  mock_service.py cloud <state.json>     notes, a calendar and tasks, like Nextcloud's

The state file is read before and written after every call, so a test can seed it and
check what the agent changed. Every call is appended to <state.json>.calls (JSON lines).
"""
import json
import sys
from pathlib import Path

flavour, state_path = sys.argv[1], Path(sys.argv[2])

SEED = {
    "tracker": {"teams": ["Platform", "Design"], "next": 104, "issues": [
        {"id": "PLA-101", "team": "Platform", "title": "Login page times out on slow connections",
         "description": "Users on 3G see a timeout after 10 s.", "status": "In Progress", "assignee": "mara", "priority": "high", "comments": []},
        {"id": "PLA-102", "team": "Platform", "title": "Upgrade Postgres to 17",
         "description": "", "status": "Todo", "assignee": None, "priority": "medium", "comments": []},
        {"id": "DES-103", "team": "Design", "title": "Dark mode contrast too low in settings",
         "description": "Labels at 2.8:1.", "status": "Todo", "assignee": "jo", "priority": "low", "comments": []}]},
    "cloud": {"next": 4, "notes": [
        {"id": 1, "title": "Shopping", "content": "oat milk\nbread\ncoffee"},
        {"id": 2, "title": "Server maintenance", "content": "Renew the TLS certificate before 2026-12-01.\nRotate backups weekly."},
        {"id": 3, "title": "Ideas", "content": "A theme for e-ink screens."}],
        "events": [{"id": 1, "title": "Dentist", "start": "2026-11-12T09:00", "end": "2026-11-12T09:45"},
                   {"id": 2, "title": "Team sync", "start": "2026-11-13T14:00", "end": "2026-11-13T15:00"}],
        "tasks": [{"id": 1, "title": "File the VAT return", "due": "2026-11-15", "done": False}]},
}

S = {"type": "string"}
I = {"type": "integer"}


def tool(name, description, properties=None, required=()):
    return {"name": name, "description": description,
            "inputSchema": {"type": "object", "properties": properties or {}, "required": list(required)}}


TOOLS = {
    "tracker": [
        tool("list_teams", "List the teams."),
        tool("list_issues", "List issues, optionally filtered by team, status or assignee.",
             {"team": S, "status": S, "assignee": S}),
        tool("get_issue", "One issue with its comments.", {"id": S}, ["id"]),
        tool("search_issues", "Search issues by words in the title or description.", {"query": S}, ["query"]),
        tool("create_issue", "Create an issue in a team.",
             {"team": S, "title": S, "description": S, "priority": {"type": "string", "enum": ["low", "medium", "high"]},
              "assignee": S}, ["team", "title"]),
        tool("update_issue", "Change an issue's status (Todo, In Progress, Done), assignee, priority or title.",
             {"id": S, "status": S, "assignee": S, "priority": S, "title": S}, ["id"]),
        tool("add_comment", "Add a comment to an issue.", {"id": S, "body": S}, ["id", "body"]),
    ],
    "cloud": [
        tool("list_notes", "List the notes (id and title)."),
        tool("get_note", "A note's content.", {"id": I}, ["id"]),
        tool("create_note", "Create a note.", {"title": S, "content": S}, ["title", "content"]),
        tool("update_note", "Replace a note's content.", {"id": I, "content": S}, ["id", "content"]),
        tool("delete_note", "Delete a note for good.", {"id": I}, ["id"]),
        tool("list_events", "Calendar events, optionally from a day (YYYY-MM-DD) on.", {"from_day": S}),
        tool("create_event", "Create a calendar event; start and end as YYYY-MM-DDTHH:MM.",
             {"title": S, "start": S, "end": S}, ["title", "start", "end"]),
        tool("list_tasks", "List the tasks."),
        tool("create_task", "Create a task, optionally with a due day (YYYY-MM-DD).", {"title": S, "due": S}, ["title"]),
        tool("complete_task", "Mark a task as done.", {"id": I}, ["id"]),
    ],
}


def load():
    if not state_path.exists():
        state_path.write_text(json.dumps(SEED[flavour], indent=1))
    return json.loads(state_path.read_text())


def find(items, key):
    for item in items:
        if str(item["id"]).lower() == str(key).lower():
            return item
    raise KeyError(f"nothing with the id {key}")


def call(name, a):
    d = load()
    if flavour == "tracker":
        issues = d["issues"]
        if name == "list_teams":
            out = d["teams"]
        elif name == "list_issues":
            out = [{k: i[k] for k in ("id", "team", "title", "status", "assignee", "priority")} for i in issues
                   if all(not a.get(k) or str(i[k]).lower() == a[k].lower() for k in ("team", "status", "assignee"))]
        elif name == "get_issue":
            out = find(issues, a["id"])
        elif name == "search_issues":
            words = a["query"].lower().split()
            out = [{"id": i["id"], "title": i["title"], "status": i["status"]} for i in issues
                   if any(w in (i["title"] + " " + i["description"]).lower() for w in words)]
        elif name == "create_issue":
            if a["team"] not in d["teams"]:
                raise KeyError(f"no team {a['team']}; the teams are {', '.join(d['teams'])}")
            out = {"id": f"{a['team'][:3].upper()}-{d['next']}", "team": a["team"], "title": a["title"],
                   "description": a.get("description", ""), "status": "Todo", "assignee": a.get("assignee"),
                   "priority": a.get("priority", "medium"), "comments": []}
            d["next"] += 1
            issues.append(out)
        elif name == "update_issue":
            out = find(issues, a["id"])
            if a.get("status") and a["status"] not in ("Todo", "In Progress", "Done"):
                raise KeyError("status must be Todo, In Progress or Done")
            out.update({k: a[k] for k in ("status", "assignee", "priority", "title") if a.get(k)})
        elif name == "add_comment":
            out = find(issues, a["id"])
            out["comments"].append(a["body"])
        else:
            raise KeyError(f"no tool {name}")
    else:
        if name == "list_notes":
            out = [{"id": n["id"], "title": n["title"]} for n in d["notes"]]
        elif name == "get_note":
            out = find(d["notes"], a["id"])
        elif name == "create_note":
            out = {"id": d["next"], "title": a["title"], "content": a["content"]}
            d["next"] += 1
            d["notes"].append(out)
        elif name == "update_note":
            out = find(d["notes"], a["id"])
            out["content"] = a["content"]
        elif name == "delete_note":
            out = find(d["notes"], a["id"])
            d["notes"].remove(out)
            out = {"deleted": out["title"]}
        elif name == "list_events":
            out = [e for e in d["events"] if e["start"] >= a.get("from_day", "")]
        elif name == "create_event":
            out = {"id": d["next"], "title": a["title"], "start": a["start"], "end": a["end"]}
            d["next"] += 1
            d["events"].append(out)
        elif name == "list_tasks":
            out = d["tasks"]
        elif name == "create_task":
            out = {"id": d["next"], "title": a["title"], "due": a.get("due"), "done": False}
            d["next"] += 1
            d["tasks"].append(out)
        elif name == "complete_task":
            out = find(d["tasks"], a["id"])
            out["done"] = True
        else:
            raise KeyError(f"no tool {name}")
    state_path.write_text(json.dumps(d, indent=1))
    return out


def answer(request):
    method, params = request.get("method"), request.get("params") or {}
    if method == "initialize":
        return {"protocolVersion": params.get("protocolVersion", "2025-06-18"), "capabilities": {"tools": {}},
                "serverInfo": {"name": f"mock-{flavour}", "version": "1"}}
    if method == "tools/list":
        return {"tools": TOOLS[flavour]}
    if method == "tools/call":
        with open(str(state_path) + ".calls", "a") as f:
            f.write(json.dumps({"tool": params.get("name"), "arguments": params.get("arguments")}) + "\n")
        try:
            out = call(params.get("name"), params.get("arguments") or {})
            return {"content": [{"type": "text", "text": json.dumps(out, indent=1)}]}
        except (KeyError, TypeError, ValueError) as e:
            return {"content": [{"type": "text", "text": f"error: {e.args[0] if e.args else e}"}], "isError": True}
    if method == "ping":
        return {}
    return None


load()
for line in sys.stdin:
    try:
        request = json.loads(line)
    except ValueError:
        continue
    if "id" not in request:
        continue
    result = answer(request)
    reply = {"jsonrpc": "2.0", "id": request["id"]}
    if result is None:
        reply["error"] = {"code": -32601, "message": "method not found"}
    else:
        reply["result"] = result
    print(json.dumps(reply), flush=True)
