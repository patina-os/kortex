#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Remove what install.sh put in place. The server password in ~/.config/kortex stays,
# and opencode keeps its conversations.
set -euo pipefail

data="${XDG_DATA_HOME:-$HOME/.local/share}"
service=io.github.patina21.kortex

pkill -f "$data/kortex/kortex.py" 2> /dev/null || true
rm -f "$data/krunner/dbusplugins/kortex.desktop" "$data/dbus-1/services/$service.service" \
  "$data/icons/hicolor/scalable/apps/kortex.svg"
rm -rf "$data/kortex"
if command -v kquitapp6 > /dev/null; then
  kquitapp6 krunner 2> /dev/null || true
fi
echo "kortex: removed; ${XDG_CONFIG_HOME:-$HOME/.config}/kortex/password is left for you to delete"
