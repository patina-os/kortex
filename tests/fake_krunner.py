# SPDX-License-Identifier: GPL-3.0-or-later
"""KRunner's application object and a notification server for the end-to-end test:
org.kde.krunner /App and org.freedesktop.Notifications on the session bus. Both write
what kortex asks of them to the file they are given: every query KRunner is asked to
show, and every notification with its actions. `Invoke` (an interface of the test's
own) clicks a notification's action, as a user would."""

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
        write(f"notify\t{note}\t{summary}\t{','.join(actions)}\t{bool(hints.get('transient'))}")
        return note

    @dbus.service.signal(NOTIFICATIONS, signature="us")
    def ActionInvoked(self, note, action):
        pass

    @dbus.service.method("io.github.patina_os.kortex.Test", in_signature="us")
    def Invoke(self, note, action):
        self.ActionInvoked(note, action)


bus = dbus.SessionBus()
App(dbus.service.BusName("org.kde.krunner", bus), "/App")
Notifications(dbus.service.BusName(NOTIFICATIONS, bus), "/org/freedesktop/Notifications")
print("ready", flush=True)
GLib.MainLoop().run()
