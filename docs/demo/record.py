# SPDX-License-Identifier: GPL-3.0-or-later
"""Record kortex in action for the README: the setup of tests/e2e.py (a private session
bus that starts kortex by D-Bus activation, stand-ins for KRunner's /App, the
notification server and KWin's scripting, a real `opencode serve`), opencode with the
permissions patina gives it, and a scripted model that thinks before it answers and
explains each change first (demo_llm.py). Meta+A is kortex's `Open`, as patina binds it.
Every Match, Run and Teardown goes over D-Bus as KRunner sends it, every notification
as the notification server gets it, and every answer kortex gives is written to
timeline.json, which page.html replays. At the end, Show what the agent does opens the
panel: the terminal kortex starts is a stand-in that notes the command, and what the
panel shows is read from the agent's own server, the conversation as opencode keeps it.

    KORTEX_OPENCODE=/path/to/opencode python3 docs/demo/record.py
"""

import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import urllib.request
from base64 import b64encode
from pathlib import Path

import dbus

demo = Path(__file__).resolve().parent
repo = demo.parent.parent
sys.path.insert(0, str(demo))
import demo_llm

SERVICE = "io.github.patina_os.kortex"
SEP = "\x1f"
JOINER = "\u2060"  # in a query kortex puts on screen to refresh what KRunner shows already
MARKER = "kortex ›"  # the conversation
OPENCODE = os.environ.get("KORTEX_OPENCODE") or shutil.which("opencode")
MODEL = "nemotron-3.5-bolt:e4b"  # the name the scripted model answers under
# opencode's permissions as patina sets them (defaults/apps.yaml in patina): reading runs
# freely, changes ask, a few read-only commands are allowed.
PERMISSION = {
    "edit": "ask", "webfetch": "allow", "external_directory": "ask",
    "bash": {"*": "ask", "cat *": "allow", "df *": "allow", "free *": "allow", "git diff*": "allow",
             "git log*": "allow", "git status*": "allow", "ls*": "allow", "lsblk*": "allow",
             "patina doctor*": "allow", "patina keys*": "allow", "patina theme list*": "allow",
             "systemctl status *": "allow", "uname *": "allow"},
}
BUS_CONFIG = """<!DOCTYPE busconfig PUBLIC "-//freedesktop//DTD D-Bus Bus Configuration 1.0//EN"
 "http://www.freedesktop.org/standards/dbus/1.0/busconfig.dtd">
<busconfig>
  <include>/usr/share/dbus-1/session.conf</include>
  <servicedir>{services}</servicedir>
</busconfig>
"""

timeline = []
start = time.monotonic()


def plain(matches):
    return [{"id": str(m[0]).split(SEP)[0], "key": str(m[0]).split(SEP)[-1] if SEP in str(m[0]) else "", "text": str(m[1]), "subtext": str(m[5].get("subtext", "")),
             "actions": [str(a) for a in m[5].get("actions", [])], "multiline": bool(m[5].get("multiline", False))}
            for m in matches]


def note(kind, **data):
    timeline.append(dict(t=round((time.monotonic() - start) * 1000), kind=kind, **data))
    print(kind, json.dumps(data, ensure_ascii=False)[:160], flush=True)


def main():
    if not OPENCODE:
        sys.exit("record: opencode is not installed; set KORTEX_OPENCODE")
    home = Path(tempfile.mkdtemp(prefix="kortex-demo-"))
    model = demo_llm.serve()
    port = model.server_address[1]
    (home / ".config/opencode").mkdir(parents=True)
    (home / ".config/opencode/opencode.json").write_text(json.dumps({
        "$schema": "https://opencode.ai/config.json", "autoupdate": False, "share": "disabled",
        "provider": {"ollama": {"npm": "@ai-sdk/openai-compatible", "name": "Ollama (local)",
                                "options": {"baseURL": f"http://127.0.0.1:{port}/v1", "apiKey": "demo"},
                                "models": {MODEL: {"name": MODEL, "tool_call": True}}}},
        "model": f"ollama/{MODEL}", "small_model": f"ollama/{MODEL}", "permission": PERMISSION,
    }))
    # Stand-ins for `patina` and the ArtCraft Launcher, so the install has something
    # harmless to run: patina prints what `patina install --bundles artcraft` prints.
    (home / "bin").mkdir()
    stand_ins = {
        "patina": "printf 'patina: %s\\n' 'bundles: base artcraft (apt backend, x86_64)' "
                  "'packages: installing fuse3' 'artcraft-launcher: installing (binary)' 'done: 2 changes'",
        "artcraft-launcher": "exit 0",
        # The terminal kortex starts for the panel: it notes what it was to run, and the
        # password kortex hands it in the environment, for the recording to read the
        # conversation from the agent's server as the panel would show it.
        "konsole": f'printf "%s\\n" "$OPENCODE_SERVER_PASSWORD" "$@" > {home}/terminal; exec sleep 30',
    }
    for name, body in stand_ins.items():
        fake = home / "bin" / name
        fake.write_text(f"#!/bin/sh\n{body}\n")
        fake.chmod(0o755)
    env = dict(os.environ, HOME=str(home), XDG_CONFIG_HOME=str(home / ".config"),
               XDG_DATA_HOME=str(home / ".local/share"), XDG_STATE_HOME=str(home / ".local/state"),
               XDG_CACHE_HOME=str(home / ".cache"), KORTEX_OPENCODE=OPENCODE, NO_PROXY="127.0.0.1,localhost",
               PATH=f"{home / 'bin'}:{os.environ['PATH']}")
    env.pop("KORTEX_OPENCODE_URL", None)
    services = home / "dbus-services"
    services.mkdir()
    (services / f"{SERVICE}.service").write_text(
        f"[D-BUS Service]\nName={SERVICE}\nExec={sys.executable} {repo / 'kortex.py'}\n")
    (home / "bus.conf").write_text(BUS_CONFIG.format(services=services))
    bus_daemon = subprocess.Popen(["dbus-daemon", f"--config-file={home / 'bus.conf'}", "--nofork",
                                   "--print-address=1"], stdout=subprocess.PIPE, text=True, env=env,
                                  stderr=subprocess.DEVNULL)
    address = bus_daemon.stdout.readline().strip()
    env["DBUS_SESSION_BUS_ADDRESS"] = address
    record = home / "krunner-queries"
    record.touch()
    krunner = subprocess.Popen([sys.executable, str(repo / "tests/fake_krunner.py"), str(record)], env=env,
                               stdout=subprocess.PIPE, text=True)
    assert krunner.stdout.readline().strip() == "ready"
    bus = dbus.bus.BusConnection(address)
    k = dbus.Interface(bus.get_object(SERVICE, "/runner"), "org.kde.krunner1")
    daemon = dbus.Interface(bus.get_object("org.freedesktop.DBus", "/org/freedesktop/DBus"), "org.freedesktop.DBus")
    pid = int(daemon.GetConnectionUnixProcessID(SERVICE))
    seen = 0

    def reopens():
        """What kortex asked of KRunner and the notification server since the last look."""
        nonlocal seen
        lines = record.read_text().splitlines()
        for line in lines[seen:]:
            kind, *rest = line.split("\t")
            if kind == "notify":
                note("notify", id=int(rest[0]), summary=rest[1], actions=rest[2].split(","),
                     transient=rest[3] == "True", body=rest[4])
            elif kind == "kwin":
                note("kwin", what=rest[0], name=rest[1] if len(rest) > 1 else "")
            elif JOINER not in rest[0]:  # not a refresh of what KRunner shows already
                note("reopen", query=rest[0])
        seen = len(lines)

    def type_query(q):
        note("type", query=q, matches=plain(k.Match(q, timeout=60)))

    def run(match_id, action=""):
        note("run", id=match_id.split(SEP)[0], action=action)
        k.Run(match_id, action, timeout=60)
        time.sleep(0.3)
        reopens()

    def watch(until, timeout=90):
        last = None
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            matches = plain(k.Match(MARKER, timeout=30))
            if matches != last:
                note("view", query=MARKER, matches=matches)
                last = matches
                if until(matches):
                    return
            time.sleep(0.15)
        raise AssertionError("timed out")

    def answered(text):
        return lambda ms: any(m["id"] == "answer" and m["multiline"] and text in m["text"] for m in ms)

    def asking(text):
        return lambda ms: any(m["id"] == "permission" and text in m["text"] for m in ms)

    def meta_a():
        """Meta+A, as patina binds it: kortex's Open, which opens KRunner on kortex alone,
        or on the conversation while the agent waits for an answer."""
        nonlocal seen
        bus.get_object(SERVICE, "/runner").Open(dbus_interface=SERVICE, timeout=30)
        time.sleep(0.4)
        lines = record.read_text().splitlines()
        # what Open asked KRunner for: the last query that is no refresh of the one shown
        opened = [line.split("\t", 1)[1] for line in lines[seen:] if line.startswith("kortex\t") and JOINER not in line]
        opened = opened[-1] if opened else ""
        seen = len(lines)
        note("shortcut", key="Meta+A", opened=opened)
        return opened

    def panel():
        """Show what the agent does: the terminal kortex starts, and what it shows."""
        run("answer", "terminal")
        terminal = home / "terminal"
        deadline = time.monotonic() + 30
        while not terminal.exists() and time.monotonic() < deadline:
            time.sleep(0.2)
        time.sleep(0.5)
        reopens()
        password, *argv = terminal.read_text().splitlines()
        attach = argv[argv.index("attach") + 1:]
        url, session = attach[0], attach[attach.index("--session") + 1]
        req = urllib.request.Request(f"{url}/session/{session}/message")
        req.add_header("Authorization", "Basic " + b64encode(f"opencode:{password}".encode()).decode())
        with urllib.request.urlopen(req, timeout=10) as response:
            messages = json.load(response)
        lines = []
        for message in messages:
            for part in message.get("parts", []):
                kind = part.get("type")
                if message["info"].get("role") == "user" and kind == "text" and not part.get("synthetic"):
                    lines.append({"kind": "user", "text": part["text"]})
                elif kind == "reasoning" and part.get("text", "").strip():
                    lines.append({"kind": "thought", "text": part["text"].strip()})
                elif kind == "text" and part.get("text", "").strip():
                    lines.append({"kind": "text", "text": part["text"].strip()})
                elif kind == "tool":
                    state = part.get("state") or {}
                    output = str(state.get("output") or state.get("metadata", {}).get("output") or "")
                    lines.append({"kind": "tool", "text": (state.get("input") or {}).get("command", part.get("tool")),
                                  "out": output.strip().splitlines()[:4]})
        note("panel", command=" ".join(argv), lines=lines)

    try:
        version = subprocess.run([OPENCODE, "--version"], capture_output=True, text=True, check=True).stdout.strip()
        kortex_version = subprocess.run([sys.executable, str(repo / "kortex.py"), "--version"],
                                        capture_output=True, text=True, check=True).stdout.split()[-1]
        note("start", kortex=kortex_version, opencode=version)
        # Meta+A opens KRunner on kortex alone (patina's `ask` action): no `ai` to type.
        meta_a()
        q1 = "how full is my disk?"
        type_query(q1)
        run("ask" + SEP + q1)
        watch(answered("free of"))
        time.sleep(2.5)  # the answer is read; kortex is done with the request
        meta_a()
        q2 = "install PhotoCraft for me"
        type_query(q2)
        run("ask" + SEP + q2)
        watch(asking("patina install"))
        time.sleep(1.5)
        note("pause", what="nothing has run yet")
        asked = next(m for m in k.Match(MARKER) if str(m[0]).startswith("permission" + SEP))
        run(str(asked[0]), "allow-once")
        # The user clicks elsewhere, which closes KRunner (its Teardown, the one after the
        # closing the run caused), and kortex follows the request with notifications.
        time.sleep(1.7)
        note("close")
        k.Teardown()
        deadline = time.monotonic() + 90
        while not any(e["kind"] == "notify" and e["summary"].endswith("asks first") for e in timeline):
            assert time.monotonic() < deadline, "no notification of the second step"
            time.sleep(0.2)
            reopens()
        time.sleep(2)
        # Meta+A while the agent waits: KRunner opens on the conversation, the row in view.
        assert meta_a().startswith(MARKER), "Meta+A did not open the conversation"
        watch(asking("open the ArtCraft Launcher"))
        time.sleep(1.2)
        asked = next(m for m in k.Match(MARKER) if str(m[0]).startswith("permission" + SEP))
        run(str(asked[0]), "allow-once")
        watch(answered("up to date"))
        time.sleep(1.5)
        panel()
        k.Teardown()
    finally:
        os.kill(pid, signal.SIGTERM)
        time.sleep(2)
        krunner.terminate()
        bus_daemon.terminate()
        model.shutdown()
    (demo / "timeline.json").write_text(json.dumps(timeline, ensure_ascii=False, indent=1) + "\n")
    shutil.rmtree(home, ignore_errors=True)
    print("record: wrote", demo / "timeline.json")


if __name__ == "__main__":
    main()
