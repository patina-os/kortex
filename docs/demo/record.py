# SPDX-License-Identifier: GPL-3.0-or-later
"""Record kortex in action for the README: the setup of tests/e2e.py (a private session
bus that starts kortex by D-Bus activation, a stand-in for KRunner's /App, a real
`opencode serve`), opencode with the permissions patina gives it, and a scripted model
(demo_llm.py). Every Match and Run goes over D-Bus as KRunner sends it, and every answer
kortex gives is written to timeline.json, which page.html replays.

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
from pathlib import Path

import dbus

demo = Path(__file__).resolve().parent
repo = demo.parent.parent
sys.path.insert(0, str(demo))
import demo_llm

SERVICE = "io.github.patina_os.kortex"
SEP = "\x1f"
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
    return [{"id": str(m[0]).split(SEP)[0], "text": str(m[1]), "subtext": str(m[5].get("subtext", "")),
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
        nonlocal seen
        lines = record.read_text().splitlines()
        for line in lines[seen:]:
            note("reopen", query=line.split("\t", 1)[1])
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
            matches = plain(k.Match("ai ›", timeout=30))
            if matches != last:
                note("view", query="ai ›", matches=matches)
                last = matches
                if until(matches):
                    return
            time.sleep(0.15)
        raise AssertionError("timed out")

    def answered(text):
        return lambda ms: any(m["id"] == "answer" and text in m["text"] and "Working" not in m["subtext"]
                              for m in ms)

    try:
        version = subprocess.run([OPENCODE, "--version"], capture_output=True, text=True, check=True).stdout.strip()
        kortex_version = subprocess.run([sys.executable, str(repo / "kortex.py"), "--version"],
                                        capture_output=True, text=True, check=True).stdout.split()[-1]
        note("start", kortex=kortex_version, opencode=version)
        q1 = "ai how full is my disk?"
        type_query(q1)
        run("ask" + SEP + q1[3:])
        watch(answered("free of"))
        q2 = "ai install photocraft"
        type_query(q2)
        run("ask" + SEP + q2[3:])
        for command in ("patina install", "artcraft-launcher"):
            watch(lambda ms, command=command: any(m["id"] == "permission" and command in m["text"] for m in ms))
            time.sleep(1.5)
            note("pause", what="nothing has run yet")
            asked = next(m for m in k.Match("ai ›") if str(m[0]).startswith("permission" + SEP))
            run(str(asked[0]), "allow-once")
        watch(answered("up to date"))
        type_query("ai")
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
