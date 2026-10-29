#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Install kortex for this user: the program, KRunner's metadata, the D-Bus activation
# file and the icon, under ~/.local/share. No root. Run it again to update.
set -euo pipefail

here="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
data="${XDG_DATA_HOME:-$HOME/.local/share}"
service=io.github.patina_os.kortex

if ! python3 -c 'import dbus, gi' 2> /dev/null; then
  echo "kortex: python3-dbus and python3-gi are missing; on Debian: sudo apt install python3-dbus python3-gi" >&2
  exit 1
fi
if ! command -v opencode > /dev/null && [[ ! -x "$HOME/.local/bin/opencode" && ! -x "$HOME/.opencode/bin/opencode" ]]; then
  echo "kortex: opencode is not installed yet (https://opencode.ai); kortex needs it to answer" >&2
fi

install -D -m 0755 "$here/kortex.py" "$data/kortex/kortex.py"
install -D -m 0644 "$here/kortex.desktop" "$data/krunner/dbusplugins/kortex.desktop"
install -D -m 0644 "$here/kortex.svg" "$data/icons/hicolor/scalable/apps/kortex.svg"
mkdir -p "$data/dbus-1/services"
printf '[D-BUS Service]\nName=%s\nExec=/usr/bin/python3 "%s"\n' "$service" "$data/kortex/kortex.py" \
  > "$data/dbus-1/services/$service.service"
# The name before 0.1.2, from the project's first account.
rm -f "$data/dbus-1/services/io.github.patina21.kortex.service"

# A running kortex keeps the old program until it stops; KRunner reads its plugins at start.
dbus-send --session --dest=org.freedesktop.DBus --type=method_call /org/freedesktop/DBus \
  org.freedesktop.DBus.ReloadConfig 2> /dev/null || true
pkill -f "$data/kortex/kortex.py" 2> /dev/null || true
if command -v kquitapp6 > /dev/null; then
  kquitapp6 krunner 2> /dev/null || true
fi
echo "kortex: installed; open KRunner (Meta+Space or Alt+Space) and type: ai and your request"
