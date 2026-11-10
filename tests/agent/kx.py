#!/usr/bin/python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""kx: drive the installed kortex over D-Bus as KRunner does, and print what the palette
would show. A tool for testing an agent by hand (tests/agent/README.md), not a test.

  kx up                      start a private session bus, a KRunner/notification stand-in
                             and (by D-Bus activation) the installed kortex
  kx down                    stop them
  kx ask "<request>"         a new conversation; watch it (see the options)
  kx say "<follow-up>"       continue the current conversation; watch it
  kx watch                   watch the current conversation again
  kx answer <allow-once|allow-always|deny>   answer the first waiting row, then watch
  kx pick "<label or your own words>"        answer the agent's question, then watch
  kx open                    press Meta+A: the query kortex opens KRunner with is in `kx notes`
  kx search "<text>" [--menu]  the rows kortex returns for any search, once
  kx notes                   the notifications and KRunner queries kortex asked for
  kx log                     kortex's own log (stderr)

Options of ask/say/watch/answer:
  --on-ask stop|allow|deny   what to do when a row asks for permission (default stop:
                             print the row and exit 3, so you decide with `kx answer`;
                             a question of the agent's exits 5, for `kx pick`)
  --closed                   act as if KRunner was closed after asking (notifications follow)
  --timeout N                seconds to watch (default 300); exit 4 when it runs out
Every changed set of rows is printed with the seconds since the watch began.
"""
import os
import re
import subprocess
import sys
import time
from pathlib import Path

HOME = Path.home()
RUN = HOME / ".kx"
SERVICE = "io.github.patina_os.kortex"
SEP = "\x1f"
JOINER = "⁠"
BUS_CONFIG = """<!DOCTYPE busconfig PUBLIC "-//freedesktop//DTD D-Bus Bus Configuration 1.0//EN"
 "http://www.freedesktop.org/standards/dbus/1.0/busconfig.dtd">
<busconfig>
  <include>/usr/share/dbus-1/session.conf</include>
  <servicedir>{services}</servicedir>
</busconfig>
"""


def up():
    RUN.mkdir(exist_ok=True)
    if (RUN / "address").exists():
        down()
    services = HOME / ".local/share/dbus-1/services"
    (RUN / "bus.conf").write_text(BUS_CONFIG.format(services=services))
    # kortex's stderr: the bus daemon's, which starts it
    with open(RUN / "kortex.log", "ab") as out:  # the daemon keeps its own copy
        daemon = subprocess.Popen(["dbus-daemon", f"--config-file={RUN / 'bus.conf'}", "--nofork",
                                   "--print-address=1"], stdout=subprocess.PIPE, stderr=out, text=True,
                                  start_new_session=True)
    address = daemon.stdout.readline().strip()
    (RUN / "address").write_text(address)
    (RUN / "pids").write_text(f"{daemon.pid}\n")
    env = dict(os.environ, DBUS_SESSION_BUS_ADDRESS=address)
    (RUN / "record").touch()
    fake = subprocess.Popen([sys.executable, str(Path(__file__).resolve().parent.parent / "fake_krunner.py"), str(RUN / "record")], env=env,
                            stdout=subprocess.PIPE, text=True, start_new_session=True)
    assert fake.stdout.readline().strip() == "ready"
    with open(RUN / "pids", "a") as f:
        f.write(f"{fake.pid}\n")
    print("up:", address)


def down():
    # kortex stops itself and its agent's server when its bus goes away
    for pid in (RUN / "pids").read_text().split() if (RUN / "pids").exists() else []:
        subprocess.run(["kill", pid], check=False, stderr=subprocess.DEVNULL)
    for name in ("address", "pids"):
        (RUN / name).unlink(missing_ok=True)
    print("down")


def dbus_bus():
    import dbus
    if not (RUN / "address").exists():
        sys.exit("kx: run `kx up` first")
    return dbus.bus.BusConnection((RUN / "address").read_text().strip())


def runner():
    import dbus
    return dbus.Interface(dbus_bus().get_object(SERVICE, "/runner"), "org.kde.krunner1")


def rows(k, query):
    return [(str(m[0]), str(m[1]), str(m[2]), float(m[4]), str(m[5].get("subtext", "")),
             [str(a) for a in m[5].get("actions", [])], bool(m[5].get("multiline"))) for m in k.Match(query)]


def show(found, since=None):
    stamp = f"[{time.monotonic() - since:6.1f}s] " if since is not None else ""
    print(f"{stamp}---- {len(found)} row(s)")
    for match_id, text, icon, relevance, subtext, actions, multiline in found:
        kind = match_id.split(SEP)[0]
        acts = f"  actions: {', '.join(actions)}" if actions else ""
        print(f"  ({kind}, {icon}, {relevance:.2f}{', multiline' if multiline else ''}) {text}")
        print(f"      sub: {subtext}{acts}")
    sys.stdout.flush()


def option(args, name, default=None, flag=False):
    if name in args:
        i = args.index(name)
        if flag:
            del args[i]
            return True
        value = args[i + 1]
        del args[i:i + 2]
        return value
    return False if flag else default


def watch(k, args):
    on_ask = option(args, "--on-ask", "stop")
    timeout = float(option(args, "--timeout", "300"))
    closed = option(args, "--closed", flag=True)
    began = time.monotonic()
    last, answered, toggle = None, set(), False
    if closed:
        k.Teardown()
        time.sleep(1.6)  # past the closing kortex takes for its own
        k.Teardown()
    while time.monotonic() - began < timeout:
        if closed:
            # KRunner is closed: only the notifications tell; the conversation is read at the end
            record = (RUN / "record").read_text()
            if "kortex answered" in record.split("=== mark\n")[-1] or "kortex stopped" in record.split("=== mark\n")[-1]:
                print(f"[{time.monotonic() - began:6.1f}s] a notification says the agent is done; `kx notes` shows it")
                show(rows(k, "kortex ›"), began)
                return 0
            time.sleep(0.7)
            continue
        toggle = not toggle
        found = rows(k, "kortex ›" + (JOINER if toggle else "") + " ")
        # the seconds change every time; compare without them for printing
        key = repr([(m[0], re.sub(r"\d+ (min \d+ )?s\b", "", m[1]), m[5]) for m in found])
        if key != last:
            last = key
            show(found, began)
        asks = [m for m in found if m[0].startswith("permission" + SEP) and m[0] not in answered]
        if asks:
            if on_ask == "stop":
                print("kx: a row waits for your answer: kx answer allow-once|allow-always|deny")
                return 3
            answered.add(asks[0][0])
            action = "allow-once" if on_ask == "allow" else "deny"
            print(f"[{time.monotonic() - began:6.1f}s] kx: {action} -> {asks[0][1].splitlines()[0][:200]}")
            k.Run(asks[0][0], action)
            time.sleep(0.5)
            continue
        options = [m for m in found if m[0].startswith("option" + SEP)]
        question = [m for m in found if m[0].startswith("question" + SEP)]
        if question and on_ask != "allow":
            print("kx: the agent asks you; answer with: kx pick \"<label or your own words>\"")
            return 5
        if question and options:
            print(f"[{time.monotonic() - began:6.1f}s] kx: picking {options[0][1]}")
            k.Run(options[0][0], "")
            time.sleep(0.5)
            continue
        kinds = [m[0].split(SEP)[0] for m in found]
        done = [m for m in found if m[0] == "answer" and (m[6] or m[2] == "dialog-error")]
        if done or "problem" in kinds and "waiting" not in kinds:
            if not done:
                time.sleep(2)  # a problem row: see whether it stays
                show(rows(k, "kortex › "), began)
            print(f"[{time.monotonic() - began:6.1f}s] kx: done")
            return 0
        time.sleep(0.7)
    print(f"kx: still not done after {timeout:.0f} s")
    show(rows(k, "kortex › "), began)
    return 4


def main(args):
    if not args or args[0] in ("-h", "--help", "help"):
        print(__doc__)
        return 0
    command, args = args[0], args[1:]
    if command == "up":
        return up()
    if command == "down":
        return down()
    if command == "notes":
        print((RUN / "record").read_text())
        return 0
    if command == "log":
        print((RUN / "kortex.log").read_text()[-6000:])
        return 0
    k = runner()
    if command == "search":
        menu = option(args, "--menu", flag=True)
        if menu:
            print("kx: --menu needs plasmashell as the caller; showing KRunner's rows")
        show(rows(k, args[0]))
        return 0
    with open(RUN / "record", "a") as f:
        f.write("=== mark\n")
    if command in ("ask", "say"):
        text = args.pop(0)
        if command == "ask":
            found = rows(k, text)  # any search: the row a user would press
            show(found)
            ask = next((m for m in found if m[0].startswith("ask" + SEP)), None)
            if ask is None:
                found = rows(k, "ai " + text)
                ask = next(m for m in found if m[0].startswith("ask" + SEP))
        else:
            found = rows(k, "kortex › " + text)
            show(found)
            ask = next(m for m in found if m[0].startswith("continue" + SEP))
        k.Run(ask[0], "")
        return watch(k, args)
    if command == "watch":
        return watch(k, args)
    if command == "open":
        runner_object = dbus_bus().get_object(SERVICE, "/runner")
        runner_object.Open(dbus_interface=SERVICE)
        time.sleep(0.5)
        asked = (RUN / "record").read_text().split("=== mark\n")[-1].strip("\n")
        print(f"kx: KRunner opened with {asked!r}" if asked else "kx: KRunner was not asked")
        return 0
    if command == "pick":
        label = args.pop(0)
        found = rows(k, "kortex › " + label)
        pick = next((m for m in rows(k, "kortex › ") if m[0].startswith("option" + SEP) and m[1] == label), None) or \
            next((m for m in found if m[0].startswith("option" + SEP)), None)
        if not pick:
            print("kx: nothing asks")
            show(found)
            return 1
        print(f"kx: answering {pick[1]} ({pick[4]})")
        k.Run(pick[0], "")
        time.sleep(0.8)
        return watch(k, args)
    if command == "answer":
        action = args.pop(0)
        found = rows(k, "kortex › ")
        asks = [m for m in found if m[0].startswith("permission" + SEP)]
        if not asks:
            print("kx: nothing waits for an answer")
            show(found)
            return 1
        print(f"kx: {action} -> {asks[0][1].splitlines()[0][:200]}")
        k.Run(asks[0][0], action)
        time.sleep(0.8)
        return watch(k, args)
    sys.exit(f"kx: unknown command {command}; kx help")


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]) or 0)
