# SPDX-License-Identifier: GPL-3.0-or-later
"""KRunner's application object for the end-to-end test: org.kde.krunner /App on the
session bus, which writes every query kortex asks it to show to the file it is given."""

import sys

import dbus
import dbus.service
from dbus.mainloop.glib import DBusGMainLoop
from gi.repository import GLib

DBusGMainLoop(set_as_default=True)
record = sys.argv[1]


class App(dbus.service.Object):
    @dbus.service.method("org.kde.krunner.App", in_signature="ss")
    def querySingleRunner(self, runner, term):
        with open(record, "a") as f:
            f.write(f"{runner}\t{term}\n")

    @dbus.service.method("org.kde.krunner.App", in_signature="s")
    def query(self, term):
        with open(record, "a") as f:
            f.write(f"\t{term}\n")


App(dbus.service.BusName("org.kde.krunner", dbus.SessionBus()), "/App")
print("ready", flush=True)
GLib.MainLoop().run()
