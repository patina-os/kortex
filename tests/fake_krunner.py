# SPDX-License-Identifier: GPL-3.0-or-later
"""KRunner's application object, a notification server and KWin's scripting for the
end-to-end test and the demo: org.kde.krunner /App, org.freedesktop.Notifications and
org.kde.KWin /Scripting on the session bus. They write what kortex asks of them to the
file they are given: every query KRunner is asked to show, every notification with its
actions and its body (on one line), and every KWin script loaded to place a window (the
panel), with the process it places. `Invoke` (an interface of the test's own) clicks a
notification's action, as a user would."""

import re
import sys

import dbus
import dbus.service
from dbus.mainloop.glib import DBusGMainLoop
from gi.repository import GLib

DBusGMainLoop(set_as_default=True)
record = sys.argv[1]
NOTIFICATIONS = "org.freedesktop.Notifications"


def write(line):
    with open(record, "a") as f:
        f.write(line + "\n")


class App(dbus.service.Object):
    @dbus.service.method("org.kde.krunner.App", in_signature="ss")
    def querySingleRunner(self, runner, term):
        write(f"{runner}\t{term}")

    @dbus.service.method("org.kde.krunner.App", in_signature="s")
    def query(self, term):
        write(f"\t{term}")


class Notifications(dbus.service.Object):
    last = 0

    @dbus.service.method(NOTIFICATIONS, in_signature="susssasa{sv}i", out_signature="u")
    def Notify(self, app, replaces, icon, summary, body, actions, hints, timeout):
        if not replaces:
            Notifications.last += 1
        note = int(replaces) or Notifications.last
        body = body.replace("\t", " ").replace("\n", "<br>")
        write(f"notify\t{note}\t{summary}\t{','.join(actions)}\t{bool(hints.get('transient'))}\t{body}")
        return note

    @dbus.service.signal(NOTIFICATIONS, signature="us")
    def ActionInvoked(self, note, action):
        pass

    @dbus.service.method("io.github.patina_os.kortex.Test", in_signature="us")
    def Invoke(self, note, action):
        self.ActionInvoked(note, action)


class Scripting(dbus.service.Object):
    """KWin's /Scripting, as far as kortex's panel uses it: a script loaded from a file
    under a name, then run through its own object, and unloaded by name."""
    loaded = 0

    @dbus.service.method("org.kde.kwin.Scripting", in_signature="ss", out_signature="i")
    def loadScript(self, path, name):
        Scripting.loaded += 1
        with open(path) as f:
            pid = re.search(r"const pid = (\d+)", f.read())
        write(f"kwin\tload\t{name}\t{pid.group(1) if pid else ''}")
        Script(bus, f"/Scripting/Script{Scripting.loaded}")
        return Scripting.loaded

    @dbus.service.method("org.kde.kwin.Scripting", in_signature="s", out_signature="b")
    def unloadScript(self, name):
        return True


class Script(dbus.service.Object):
    @dbus.service.method("org.kde.kwin.Script")
    def run(self):
        write("kwin\trun")


bus = dbus.SessionBus()
App(dbus.service.BusName("org.kde.krunner", bus), "/App")
Scripting(dbus.service.BusName("org.kde.KWin", bus), "/Scripting")
Notifications(dbus.service.BusName(NOTIFICATIONS, bus), "/org/freedesktop/Notifications")
print("ready", flush=True)
GLib.MainLoop().run()
