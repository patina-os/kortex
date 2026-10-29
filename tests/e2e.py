# SPDX-License-Identifier: GPL-3.0-or-later
"""The end-to-end test: kortex as KRunner uses it, against a real `opencode serve`.

A private session bus that starts kortex by D-Bus activation, as install.sh sets it up,
a stand-in for KRunner's application object, a test model on
localhost (tests/mock_llm.py) and opencode configured to use it. Then, through D-Bus as
KRunner would: ask for a file, see the agent ask before it runs the command, check that
nothing ran, allow it once, see the file and the answer. Needs opencode (KORTEX_OPENCODE
or on PATH), python3-dbus and python3-gi; opencode fetches its provider package from
npm on first use, so it needs the network once.

    python3 tests/e2e.py
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

here = Path(__file__).resolve().parent
sys.path.insert(0, str(here))
import mock_llm

SERVICE = "io.github.patina_os.kortex"
SEP = "\x1f"
BUS_CONFIG = """<!DOCTYPE busconfig PUBLIC "-//freedesktop//DTD D-Bus Bus Configuration 1.0//EN"
 "http://www.freedesktop.org/standards/dbus/1.0/busconfig.dtd">
<busconfig>
  <include>/usr/share/dbus-1/session.conf</include>
  <servicedir>{services}</servicedir>
</busconfig>
"""


def wait_for(what, check, timeout=120):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        found = check()
        if found:
            return found
        time.sleep(0.5)
    raise AssertionError(f"timed out waiting for {what}")


def running(pid):
    """True while the process runs (a zombie has stopped)."""
    try:
        return Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0] != "Z"
    except OSError:
        return False


def main():
    opencode = os.environ.get("KORTEX_OPENCODE") or shutil.which("opencode")
    if not opencode:
        sys.exit("e2e: opencode is not installed; set KORTEX_OPENCODE")
    home = Path(tempfile.mkdtemp(prefix="kortex-e2e-"))
    model = mock_llm.serve()
    port = model.server_address[1]
    config = home / ".config/opencode"
    config.mkdir(parents=True)
    (config / "opencode.json").write_text(json.dumps({
        "$schema": "https://opencode.ai/config.json",
        "autoupdate": False,
        "share": "disabled",
        "provider": {"mock": {"npm": "@ai-sdk/openai-compatible", "name": "Test model",
                              "options": {"baseURL": f"http://127.0.0.1:{port}/v1", "apiKey": "test"},
                              "models": {"mock": {"name": "mock", "tool_call": True}}}},
        "model": "mock/mock",
        "small_model": "mock/mock",
        "permission": {"edit": "ask", "bash": {"*": "ask"}},
    }))
    env = dict(os.environ, HOME=str(home), XDG_CONFIG_HOME=str(home / ".config"),
               XDG_DATA_HOME=str(home / ".local/share"), XDG_STATE_HOME=str(home / ".local/state"),
               XDG_CACHE_HOME=str(home / ".cache"), KORTEX_OPENCODE=opencode, NO_PROXY="127.0.0.1,localhost")
    env.pop("KORTEX_OPENCODE_URL", None)
    services = home / "dbus-services"
    services.mkdir()
    (services / f"{SERVICE}.service").write_text(
        f"[D-BUS Service]\nName={SERVICE}\nExec={sys.executable} {here.parent / 'kortex.py'}\n")
    (home / "bus.conf").write_text(BUS_CONFIG.format(services=services))
    bus_daemon = subprocess.Popen(["dbus-daemon", f"--config-file={home / 'bus.conf'}", "--nofork",
                                   "--print-address=1"], stdout=subprocess.PIPE, text=True, env=env)
    address = bus_daemon.stdout.readline().strip()
    env["DBUS_SESSION_BUS_ADDRESS"] = address
    record = home / "krunner-queries"
    record.touch()
    krunner = subprocess.Popen([sys.executable, str(here / "fake_krunner.py"), str(record)], env=env,
                               stdout=subprocess.PIPE, text=True)
    assert krunner.stdout.readline().strip() == "ready"
    bus = dbus.bus.BusConnection(address)
    pid = None
    servers = []
    try:
        k = dbus.Interface(bus.get_object(SERVICE, "/runner"), "org.kde.krunner1")  # activates kortex
        daemon = dbus.Interface(bus.get_object("org.freedesktop.DBus", "/org/freedesktop/DBus"), "org.freedesktop.DBus")
        pid = int(daemon.GetConnectionUnixProcessID(SERVICE))
        print("e2e: D-Bus activation started kortex")

        config_reply = k.Config()
        assert str(config_reply["MatchRegex"]).endswith("ai(\\s|$)"), config_reply
        assert [str(a[0]) for a in k.Actions()] == ["allow-once", "allow-always", "deny", "copy", "terminal"]
        assert list(k.Match("firefox")) == []

        request = "make the file kortex-proof"
        matches = k.Match("ai " + request)
        assert str(matches[0][0]) == "ask" + SEP + request, matches
        print("e2e: ask match ok")

        k.Run("ask" + SEP + request, "", timeout=60)
        wait_for("KRunner reopened on the conversation", lambda: "kortex\tai ›" in record.read_text(), 10)
        print("e2e: KRunner reopened on the conversation")

        def permission():
            for m in k.Match("ai ›", timeout=30):
                if str(m[0]).startswith("permission" + SEP):
                    return m
        asked = wait_for("the agent to ask first", permission)
        assert str(asked[1]) == f"Allow bash: {mock_llm.TOOL_COMMAND}", asked[1]
        assert list(asked[5]["actions"]) == ["allow-once", "allow-always", "deny"]
        time.sleep(1)
        assert not (home / "kortex-proof").exists(), "the command ran before it was allowed"
        print("e2e: the agent asks first, and nothing ran:", str(asked[1]))

        k.Run(str(asked[0]), "allow-once", timeout=30)
        wait_for("the command to run", lambda: (home / "kortex-proof").exists(), 60)

        def answer():
            for m in k.Match("ai ›", timeout=30):
                if str(m[0]) == "answer" and mock_llm.DONE in str(m[1]):
                    return m
        done = wait_for("the answer", answer)
        assert bool(done[5]["multiline"])
        assert "permission" not in " ".join(str(m[0]) for m in k.Match("ai ›"))
        print("e2e: allowed once, the file is there, the answer reads:", repr(str(done[1])))

        listed = k.Match("ai", timeout=30)
        assert any(str(m[0]).startswith("session" + SEP) for m in listed), listed
        print("e2e: ai lists the conversation")
        k.Teardown()
        password = (home / ".config/kortex/password")
        assert oct(password.stat().st_mode & 0o777) == "0o600"
    finally:
        if pid:
            # kortex's children: the opencode serve it started, which must stop with it
            servers = subprocess.run(["pgrep", "-P", str(pid)], capture_output=True, text=True,
                                     check=False).stdout.split()
            os.kill(pid, signal.SIGTERM)
            wait_for("kortex to stop", lambda: not bus.name_has_owner(SERVICE), 15)
        krunner.terminate()
        bus_daemon.terminate()
        model.shutdown()
    assert servers, "kortex started no opencode serve"
    assert not [p for p in servers if running(p)], f"opencode serve outlived kortex: {servers}"
    shutil.rmtree(home, ignore_errors=True)
    print("e2e: passed")


if __name__ == "__main__":
    main()
