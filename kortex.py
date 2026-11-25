#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""kortex: the AI agent in KDE Plasma's command palette.

A KRunner D-Bus runner (org.kde.krunner1). Any search in KRunner or Plasma's start menu
ends with "Ask kortex: …" (first when it reads as a question), `ai <request>` asks at
once, `ai` alone lists the recent conversations, and `kortex ›` (`ai ›` too) shows the
current one: while the agent works, a row that says how long it has been at it and what
it wrote or thought last, and a row for each step it took; then its answer. Every action
the agent wants to take is a match of its own with Allow once, Always and Deny. Each of
those rows is one line, except an answer and a row that asks, so KRunner's window holds
still while the agent works. The start menu shows neither the answer nor those actions,
so the conversation opens in KRunner. While KRunner is closed, a notification follows
the request until the agent answers. The agent is opencode or Hermes Agent
(~/.config/kortex/agent, KORTEX_AGENT): kortex starts its server (`opencode serve`,
`hermes gateway run`) on 127.0.0.1 when it is first needed and talks to its HTTP API
from a thread of its own, so KRunner never waits for the agent.

D-Bus activation starts kortex (see install.sh); it stops itself and the server after
a quiet quarter of an hour.
"""

import base64
import html
import http.client
import json
import os
import queue
import re
import secrets
import shlex
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

VERSION = "0.9.1"
SERVICE = "io.github.patina_os.kortex"
OBJECT_PATH = "/runner"
IFACE = "org.kde.krunner1"
RUNNER_ID = "kortex"  # X-KDE-PluginInfo-Name in kortex.desktop
TRIGGER = "ai"
NAME = "kortex"  # what the palette, its rows and its notifications call it, whatever the agent
VIEW_MARK = "›"  # after kortex's name (`kortex ›`), or `ai`: the current conversation
# KRunner asks again only for a query that differs from the one it shows, so a refresh
# puts an invisible word joiner after the marker, or takes it away again. A refresh
# rewrites KRunner's field, so it comes only while the field holds the bare `kortex ›`
# (or `ai`), never while a follow-up is typed after it.
JOINER = "\u2060"
ICON = "kortex"
SEP = "\x1f"  # between the kind of a match and its argument in a match id
MAX_ANSWER = 1500  # characters of an answer KRunner shows; the panel has the rest
# and lines: twelve, a line of the answer as long as it wraps to at about LINE characters,
# so the answer's row stays well inside a screen 768 px high
MAX_LINES, LINE = 12, 90
MAX_LATEST = 40  # characters of what the agent wrote or thought last, after the seconds
PANEL_SHARE, PANEL_MIN, PANEL_MAX = 0.2, 480, 720  # the panel's width: a fifth of the screen, in px
MAX_NOTE = 300  # characters a notification shows of what the agent said last; Show has the rest
MIN_SEARCH = 3  # letters a search needs before it is offered to the agent
TELL_AFTER = 3  # seconds a request runs before a closed KRunner gets a notification
SHOW_PROBLEM = 10  # seconds a failed answer, Stop or panel shows before a fetch that works clears it
TELL_RETRY = 10  # seconds the provider is retried before a closed KRunner is told
REFRESH_MS = 700
IDLE_EXIT = 15 * 60  # seconds without a request before kortex and its server stop
CATEGORY_RELEVANCE = 100  # KRunner::QueryMatch::CategoryRelevance::Highest
CATEGORY_LOWEST = 0  # ::Lowest, for "Ask kortex" below what a search finds
# A search that starts with one of these and has three words or more reads as a request
# to the agent, and so does one that ends with a question mark: "Ask kortex" comes
# first then. English and German, the words a question or a request starts with.
ASKING = frozenset({
    "how", "why", "what", "what's", "whats", "when", "where", "who", "which", "can",
    "could", "would", "should", "is", "are", "does", "did", "explain", "help", "fix",
    "write", "install", "tell", "please", "update", "wie", "warum", "wieso",
    "weshalb", "was", "wann", "wo", "wer", "welche", "welcher", "welches", "welchen",
    "welchem", "wozu", "woher", "wieviel", "kann", "kannst", "können", "könnt",
    "könntest", "könnte", "koennte", "koennen", "soll", "sollte", "ist", "sind", "gibt",
    "erkläre", "erklaere", "erklär", "hilf", "bitte", "mach", "mache", "schreib",
    "schreibe", "installiere", "repariere", "aktualisiere", "zeig", "zeige", "ändere",
    "aendere", "stell",
})
# Not among them: do, set, show, make and change, which start the names of settings
# ("do not disturb", "show hidden files") as often as a request.
# With one of these, two words are a request: "install photocraft" would else leave
# KRunner's own "Run install photocraft" first, which runs coreutils' install.
ASKING_TWO = frozenset({"install", "installiere"})
# Always is offered only for a command that looks and nothing else, whatever it is
# given: opencode's rule for Always is the command's start and `*` ("find *"), so it
# lets any of its arguments through later, in every conversation; one Always on a
# `find … | wc -l` let `find -delete` and `find -exec sudo …` run unasked. None of
# these writes a file, prints one's content (cat, head and grep would read the secrets
# opencode's read tool keeps back) or changes the system. Not du or wc either: given
# `--files0-from`, they print the lines of a file, a private key, in their errors (the
# test round of 25 November 2026). A command of two words is one of opencode's rules of
# two words ("git status *").
LOOK_ONLY = frozenset({
    "df", "free", "lsblk", "findmnt", "uname", "uptime", "whoami", "id", "locale",
    "lscpu", "lsusb", "lspci", "lsmod", "nproc", "ls", "stat", "pwd", "which", "type",
    "ps", "pgrep", "ss", "realpath", "basename", "dirname", "readlink",
    "apt-cache", "dpkg-query", "systemctl status", "systemctl is-active",
    "systemctl is-enabled", "systemctl list-units", "systemctl list-timers",
    "git status",
})

# The kinds of step besides bash for which Always may be offered: opencode's tools that
# look and change nothing. Not a change to a file, a folder outside the home folder
# (its rule holds for any step in it) or a service's tool: a service's tools that only
# read are allowed without asking (patina's rules), so one that asks changes something,
# and its rule is any call of it ("any cloud_delete_note"), in every conversation. Not
# grep either: it prints what it finds in a file, so one Always would let it read token
# files and private keys in every later conversation (the test round of November 2026).
# Nor a fetch or a search: what it is given goes out, and one Always let every later
# address, with anything in it, go to any host in every conversation.
ALWAYS_KINDS = frozenset({"glob", "list", "lsp"})


def looks_only(command):
    """True for a command that only looks (LOOK_ONLY), with any arguments; a rule's
    `*` is any arguments. A redirect, a substitution or a second command is more."""
    command = command.strip().removesuffix("*").strip()
    if not command or re.search(r"[<>`$;&|(){}\n]", command):
        return False
    words = command.split()
    return any(" ".join(words[:n]) in LOOK_ONLY for n in (1, 2))


# What a step that asks would do, in words kortex works out from the step itself, not
# from what the agent says of it: in a test an agent called a folder that held a thesis
# "one stale cache file". Each part of a command is read for the program it runs, past
# the shell's own words (`then`, `do`, `{`), wrappers (`env`, `nice`, `timeout`), `sudo`
# and a path (`/bin/rm`), and that program and its verb decide. What deletes, runs as
# root, installs or removes software or changes a service is HELD: it asks every time,
# whatever the user handed over with "Allow the rest of this task" (the maintainer's
# list, November 2026), and so is what kortex cannot read.
ROOT = frozenset({"sudo", "su", "pkexec", "doas", "run0"})
DELETES = frozenset({"rm", "rmdir", "shred", "unlink", "truncate", "wipefs", "mkfs", "dd", "trash", "trash-put",
                     "trash-rm", "trash-empty", "gio trash", "gio remove", "git clean", "git reset", "git rm",
                     "git restore"})
INSTALLS = frozenset({"patina install", "patina update"})
# Package managers install or remove only with one of these verbs: `flatpak list`,
# `dpkg -l` and `pip show` only look.
PACKAGES = frozenset({"apt", "apt-get", "aptitude", "dpkg", "flatpak", "snap", "pip", "pip3", "pipx", "npm", "pnpm",
                      "yarn", "cargo", "uv", "zypper", "dnf", "pacman", "gem", "go", "make"})
CHANGES_SOFTWARE = frozenset({
    "install", "i", "in", "add", "remove", "rm", "uninstall", "un", "erase", "purge", "autoremove", "upgrade",
    "full-upgrade", "dist-upgrade", "dup", "update", "up", "refresh", "reinstall", "build-dep", "satisfy", "clean",
    "autoclean", "sync", "ci", "get", "link", "repair", "patch", "-i", "--install", "-r", "--remove", "-P",
    "--purge", "--unpack", "--configure"})
LOOKS_AT_SOFTWARE = frozenset({"list", "search", "show", "info", "policy", "remote-info", "remote-ls", "remotes",
                               "history", "outdated", "freeze", "view", "ls", "why", "help", "depends", "rdepends",
                               "madison", "changelog", "documentation", "version"})
# Services, timers and cron: a change to them runs later, unasked, and again.
SERVICES = frozenset({"systemctl", "service", "crontab", "at", "batch", "atrm", "loginctl", "systemd-run",
                      "update-rc.d"})
LOOKS_AT_SERVICES = frozenset({"status", "is-active", "is-enabled", "is-failed", "list-units", "list-timers",
                               "list-unit-files", "list-sockets", "list-dependencies", "list-jobs", "list-sessions",
                               "list-users", "show", "show-session", "show-user", "user-status", "session-status",
                               "cat", "help", "get-default"})
# A file there is a service, a timer, an autostart entry or a cron job.
SERVICE_PATHS = re.compile(r"systemd/(user|system)|/autostart\b|/cron|/etc/init\.d|\.(service|timer|socket|path)$")
STOPS = frozenset({"kill", "pkill", "killall", "xkill", "skill"})
NETWORK = frozenset({"nmcli", "ip", "iw", "networkctl", "rfkill", "bluetoothctl", "resolvectl", "hostnamectl"})
CHANGES_NETWORK = frozenset({"up", "down", "add", "delete", "del", "modify", "mod", "connect", "disconnect", "set",
                             "flush", "replace", "change", "on", "off", "block", "unblock", "reload", "reconnect",
                             "remove", "pair", "trust", "untrust", "power", "hostname", "dns", "domain", "edit",
                             "renew", "reconfigure", "forget", "import"})
FILES = frozenset({"mkdir", "touch", "cp", "mv", "ln", "chmod", "chown", "tar", "unzip", "zip", "gzip", "gunzip",
                   "install", "rsync", "patch", "tee", "split", "xdg-user-dirs-update"})
# What only looks, for the line over a row (Always is LOOK_ONLY's)
LOOKS = frozenset({w for w in LOOK_ONLY if " " not in w} | {"du", "wc", "cat", "head", "tail", "grep", "egrep",
                                                           "fgrep", "rg", "echo", "printf", "test", "[", "[["})
SHELLS = frozenset({"sh", "bash", "zsh", "dash", "ksh", "fish"})
INTERPRETERS = frozenset({"python", "python3", "perl", "ruby", "node", "php", "lua"})
# The shell's own words before a program, words that run nothing, and heads of a loop
SHELL_WORDS = frozenset({"if", "then", "else", "elif", "while", "until", "do", "!", "{", "time", "coproc"})
SHELL_ENDS = frozenset({"fi", "done", "esac", "}", "true", "false", ":", ";;"})
SHELL_HEADS = frozenset({"for", "case", "select", "in"})
WRAPPERS = frozenset({"command", "builtin", "env", "nice", "timeout", "nohup", "stdbuf", "ionice", "setsid", "chrt",
                      "taskset", "unbuffer", "xargs"} | ROOT)
# A wrapper's options that take the next word as their value
VALUED = {"sudo": {"-u", "-g", "-C", "-D", "-h", "-p", "-r", "-t", "-U", "-T"}, "doas": {"-u", "-C"},
          "run0": {"-u", "-D", "--user"}, "pkexec": {"--user", "-u"}, "nice": {"-n"}, "ionice": {"-c", "-n", "-p"},
          "timeout": {"-s", "-k", "--signal", "--kill-after"}, "stdbuf": {"-i", "-o", "-e"},
          "env": {"-u", "-C", "--unset", "--chdir"}, "git": {"-C", "-c"}, "taskset": {"-c"},
          "xargs": {"-I", "-n", "-d", "-L", "-P", "-s", "-a", "-E"}}
# Redirects that write no file: to /dev/null, or one output to another (`2>&1`)
QUIET = re.compile(r"(?:\d+|&)?>>?\s*/dev/(?:null|stdout|stderr|tty)(?![\w/])|\d*[<>]&(?:\d+-?|-)(?![\w/.])")
# A quoted word, whose `;`, `|` and `>` are no shell's
QUOTED = re.compile(r"'[^']*'|\"(?:[^\"\\]|\\.)*\"")
# A command cut where a shell cuts it: `&&`, `||`, `;`, a pipe, a line, `&`
PARTS = re.compile(r"&&|\|\||\|&|[;|\n]|(?<![<>&])&(?![<>&])")
# A command that can do more than its words say: another program's words as a
# command, a substitution, find's own commands.
HIDES = re.compile(r"\$\(|`|<\(|>\(|\b(eval|xargs|exec)\b|\b(ba|z|da|k)?sh\s+(-\w+\s+)*-\w*c\b|\s-(exec|execdir|ok|okdir)\b")
HELD_KINDS = (("deletes", "Deletes"), ("installs", "Installs or removes software"), ("services", "Changes a service"),
              ("stops", "Stops a program"), ("overwrites", "Can overwrite a file"),
              ("hides", "Runs a command that can do more than it says"), ("unreadable", "Runs a command kortex cannot read"))
OTHER_KINDS = (("network", "Changes your network"), ("files", "Creates or changes files"), ("runs", "Runs a command"),
               ("looks", "Only looks"))
# What the user still answers every time with "Allow the rest of this task": patina says
# the same, in the same words.
STILL_ASKS = ("Still asks: deleting, running as root, installing software, changing or enabling a service, "
              "and the connected services' changes")
# The verbs of a service's tools (MCP) that read, and those that delete
SERVICE_READS = frozenset({"list", "get", "search", "read", "find", "query", "fetch", "view", "show", "describe",
                           "count", "lookup"})
SERVICE_DELETES = frozenset({"delete", "remove", "archive", "trash", "destroy", "drop", "purge", "clear"})
# opencode's own tools; any other tool that asks is a connected service's (MCP).
OWN_KINDS = frozenset({"bash", "edit", "write", "patch", "multiedit", "read", "glob", "grep", "list", "webfetch",
                       "websearch", "codesearch", "external_directory", "task", "todowrite", "question", "lsp",
                       "skill", "doom_loop"})


def without_heredocs(command):
    """A command without the text of its here-documents (`cat > x <<EOF … EOF`), which
    is a file's or a program's input, not commands of the shell's."""
    lines, until = [], None
    for line in command.split("\n"):
        if until is not None:
            if line.strip() == until:
                until = None
            continue
        lines.append(line)
        tag = re.search(r"<<-?\s*['\"]?(\w+)['\"]?", line)
        until = tag.group(1) if tag else None
    return "\n".join(lines)


def program_of(words):
    """What a part of a command runs: (its program's name, the words after it, whether
    it runs as root). The name is "" where the part runs nothing (a loop's head, `fi`,
    a redirect alone) and None where kortex cannot tell (a program named by a variable,
    a function's definition, `su -c`)."""
    root, wrapper, rest = False, "", list(words)
    while rest:
        word = rest.pop(0)
        if re.fullmatch(r"\d*(<|>|>>|>\||<<-?|<<<)|&>>?", word):  # a redirect; its target is the next word
            rest[:1] = []
            continue
        if re.match(r"\d*[<>]|&>", word):  # a redirect and its target
            continue
        if wrapper and (word.startswith("-") or re.fullmatch(r"(0x)?[\d.]+[smhd]?|\w+=\S*", word)):
            if wrapper == "env" and word.startswith(("-S", "--split-string")):
                return None, rest, root  # a command in one word
            if word in VALUED.get(wrapper, ()):
                rest[:1] = []
            continue
        name = re.sub(r"[\\'\"]", "", word).lstrip("({!")
        if not name or name in SHELL_WORDS or re.fullmatch(r"\w+=\S*", name):  # FOO=1 before the program
            continue
        if name in SHELL_HEADS:
            return "", [], root
        name = name.rstrip(");")
        if name in SHELL_ENDS or not name:
            return "", [], root
        if name == "function" or not re.fullmatch(r"[\w.+@:,%~/-]+|\[\[?", name):
            return None, rest, root
        name = name.rsplit("/", 1)[-1] or None
        if name == "su" and "-c" in rest:
            return None, rest, True
        if name in WRAPPERS:
            root, wrapper = root or name in ROOT, name
            continue
        return name, rest, root
    return "", [], root


def kinds_of(part):
    """What one part of a command does: the kinds of HELD_KINDS and OTHER_KINDS it is,
    and whether it runs as root."""
    name, args, root = program_of(part.split())
    if name is None:
        return {"unreadable"}, root
    if not name:
        return set(), root
    i = 0
    while i < len(args) and args[i].startswith("-"):  # git -C ~/x clean: its verb is clean
        i += 2 if args[i] in VALUED.get(name, ()) else 1
    sub = args[i] if i < len(args) else ""
    two = f"{name} {sub}"
    found = set()
    if (name in DELETES or two in DELETES or name.startswith("mkfs.") or name == "find" and "-delete" in args
            or name == "rsync" and any(a.startswith(("--delete", "--remove-source")) for a in args)
            or name == "mv" and "/dev/null" in args):
        found.add("deletes")
    if two in INSTALLS or name in PACKAGES and sub not in LOOKS_AT_SOFTWARE and (
            any(re.match(r"-(R|U|S(?![a-z]*[sigl]))", a) for a in args) if name == "pacman"
            else any(a in CHANGES_SOFTWARE for a in args)):
        found.add("installs")
    subs = [a for a in args if not a.startswith("-")]
    if name in SERVICES:
        if name in ("crontab", "at", "batch"):
            changes = "-l" not in args  # `crontab -l` lists; any other takes a new table
        elif name in ("atrm", "systemd-run", "update-rc.d"):
            changes = True
        elif name == "service":
            changes = len(subs) > 1 and subs[1] != "status"
        else:
            changes = bool(subs) and subs[0] not in LOOKS_AT_SERVICES
        if changes:
            found.add("services")
    if name not in LOOKS and any(SERVICE_PATHS.search(a) for a in args):
        found.add("services")
    if name in STOPS:
        found.add("stops")
    if (name in ("cp", "dd") and {"/dev/null", "/dev/zero", "if=/dev/null", "if=/dev/zero"} & set(args)
            or name == "tee" and subs and not {"-a", "--append"} & set(args)):
        found.add("overwrites")
    if (name in SHELLS and (not subs or "-s" in args) or name in INTERPRETERS
            and (not args or {"-c", "-e", "-E", "--eval", "-"} & set(args)) or name in (".", "source", "eval", "exec")):
        found.add("hides")  # a program read from its input, or given as words
    if name in NETWORK and any(a in CHANGES_NETWORK for a in args):
        found.add("network")
    if name in FILES or name == "sed" and any(a.startswith(("-i", "--in-place")) for a in args):
        found.add("files")
    if not found:
        looks = (name in LOOKS or two in LOOK_ONLY or name in NETWORK or name in SERVICES
                 or name in PACKAGES and (sub in LOOKS_AT_SOFTWARE or not sub and name in ("dpkg", "pacman")))
        found.add("looks" if looks else "runs")
    return found, root


def service_risk(service, tool):
    """What a connected service's tool would do, from its verb: every one that asks is
    held, a read too (patina lets the reads it knows of run without a question)."""
    verb = re.split(r"[_.\-\s]", tool.strip(), maxsplit=1)[0].lower()
    if verb in SERVICE_READS:
        return f"Reads from {service}", True
    if verb in SERVICE_DELETES:
        return f"Deletes in {service}", True
    return f"Changes something in {service}", True


def risk(p):
    """(what a step that asks would do, in a few words; whether it is held): a held
    step asks every time, also after "Allow the rest of this task". Held are what
    deletes, runs as root, installs or removes software, changes or enables a service
    or a timer, stops a program or changes something in a connected service, and what
    kortex cannot judge: a command that hides another or that it cannot read, a step
    outside the home folder, a tool that prints files, anything of Hermes' without a
    command."""
    kind = p.get("permission", "")
    command = (p.get("metadata") or {}).get("command")
    if not isinstance(command, str) or not command.strip():
        # Hermes names what it asks for in its own words and gives the command as a
        # pattern; opencode's patterns for bash are the command, cut where a shell cuts it
        command = "\n".join(p.get("patterns") or []) if p.get("always") is None or kind == "bash" else ""
    if p.get("service") and command.strip():  # Hermes' gate before a service's tool: "update_issue {…}"
        return service_risk(kind, command)
    if command.strip():
        command = without_heredocs(command)
        hides = HIDES.search(command)
        command = QUOTED.sub(lambda m: re.sub(r"[;&|<>\n()]", " ", m.group()), command)
        command = QUIET.sub(" ", command)
        found, root = set(), False
        for part in PARTS.split(command):
            kinds, as_root = kinds_of(part)
            found |= kinds
            root = root or as_root
        if hides:
            found.add("hides")
        if re.search(r"(?<!>)>(?!>)", command):
            found.add("overwrites")  # `>`, `2>`, `&>`: a file emptied before it is written
        elif ">>" in command:
            found.add("files")
        if any(SERVICE_PATHS.search(target) for target in re.findall(r">>?\|?\s*(\S+)", command)):
            found.add("services")  # `cat > ~/.config/systemd/user/x.timer`
        held = [label for name, label in HELD_KINDS if name in found]
        if held:
            return " · ".join(held + (["as root"] if root else [])), True
        if root:
            return "Runs as root", True
        if kind == "external_directory":
            return "Reaches outside your home folder", True
        label = next((label for name, label in OTHER_KINDS if name in found), "Runs a command")
        if label == "Only looks" and not all(kinds_of(part)[0] <= {"looks"} for part in PARTS.split(command)):
            label = "Runs a command"
        return label, p.get("always") is None and kind not in OWN_KINDS  # Hermes' gate for a service
    if kind == "external_directory":
        return "Reaches outside your home folder", True
    if kind in ("edit", "write", "patch", "multiedit"):
        path = str((p.get("metadata") or {}).get("filepath") or " ".join(p.get("patterns") or []))
        if SERVICE_PATHS.search(path):
            return "Changes a service", True
        return "Changes a file", False
    if kind in ("read", "grep"):
        return "Reads what is in your files", True
    if kind == "webfetch":
        return "Sends this address to the web", False
    if kind in ("websearch", "codesearch"):
        return "Sends this search to the web", False
    if kind in OWN_KINDS:
        return "Takes a step", False
    if "_" in kind:
        return service_risk(*kind.split("_", 1))
    return (f"Changes something in {kind}" if kind else "Takes a step"), True


def handover_on():
    """True when the user turned on "Allow the rest of this task": KORTEX_HANDOVER, else
    ~/.config/kortex/handover, `on`. Off unless they did: opencode asks before every
    change, and a yes that covers steps not seen yet is the user's own choice."""
    given = os.environ.get("KORTEX_HANDOVER")
    if given is None:
        try:
            given = (config_dir() / "handover").read_text()
        except OSError:
            given = ""
    return given.strip() == "on"


def set_handover(on):
    path = config_dir() / "handover"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("on\n" if on else "off\n")


# The KWin script that docks the panel (Desktop.dock): the window of one process, at
# the right edge of the screen it opens on, as high as the work area, kept above and
# active (a second Meta+Shift+A brings it back).
PANEL_SCRIPT = """
const pid = %d, share = %s, least = %d, most = %d;
function dock(window) {
    if (!window || window.pid !== pid || !window.normalWindow) {
        return false;
    }
    const area = workspace.clientArea(KWin.MaximizeArea, window);
    const width = Math.round(Math.min(most, Math.max(least, area.width * share)));
    window.frameGeometry = {x: area.x + area.width - width, y: area.y, width: width, height: area.height};
    window.keepAbove = true;
    window.minimized = false;
    workspace.activeWindow = window;
    return true;
}
if (!workspace.windowList().some(dock)) {
    workspace.windowAdded.connect(dock);
}
"""

# The answers to a step that asks, as its rows and a notification's buttons say them
ALLOW_ONCE, ALLOW_REST, DENY = "Allow this once", "Allow the rest of this task", "Don't, stop here"
# The actions a match can offer: id, text, icon. KRunner asks for them once.
ACTIONS = [
    ("allow-once", ALLOW_ONCE, "dialog-ok-apply"),
    ("allow-always", "Always allow", "media-playlist-repeat"),  # not a second check mark
    ("deny", DENY, "dialog-cancel"),
    ("copy", "Copy the answer", "edit-copy"),
    ("terminal", "Show what the agent does", "view-right-new"),  # the panel, docked on the right
    ("stop", "Stop", "process-stop"),
    ("instructions", "Edit the agent's instructions", "document-edit"),  # on the hint row
]
REPLIES = {"allow-once": "once", "allow-always": "always", "deny": "reject"}
# The rows that answer a step that asks, by the kind of their match id
ANSWERS = {"allow": "allow-once", "always": "allow-always", "rest": "rest", "deny": "deny"}
HERMES_CHOICES = {"once": "once", "always": "always", "reject": "deny"}  # Hermes' words for them
# opencode 1.19 answers GET /permission with a 400 for the whole list while one request in
# it lacks an optional field (a webfetch without a timeout). kortex then takes the requests
# from opencode's event stream; one it did not see there is shown as waiting, unanswerable.
UNREADABLE = {"id": "unreadable", "permission": "a request kortex cannot show"}


def log(*words):
    print("kortex:", *words, file=sys.stderr, flush=True)


class AgentError(Exception):
    """The agent is missing, did not start, or did not answer."""


class NotReady(AgentError):
    """The agent cannot answer at all: its model's provider has no key (NO_KEY)."""


def http_status(error):
    """The HTTP status the agent answered a failed request with, or None."""
    cause = error.__cause__
    return cause.code if isinstance(cause, urllib.error.HTTPError) else None


def config_dir():
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "kortex"


def state_dir():
    return Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local/state") / "kortex"


def opencode_config():
    """opencode's own configuration file, where its MCP servers (`mcp`) are."""
    given = os.environ.get("OPENCODE_CONFIG")
    if given:
        return Path(given)
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "opencode" / "opencode.json"


def opencode_auth():
    """opencode's own login, the keys of its providers, which its server reads as it starts."""
    return Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local/share") / "opencode" / "auth.json"


def known_provider_name(provider):
    """A provider's name for the palette: from opencode's settings ("Your own ·
    api.example.eu"), else from its copy of the providers it knows (models.dev, "Mistral"),
    else the id."""
    name = provider_name(provider)
    if name != provider:
        return name
    cache = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "opencode" / "models.json"
    try:
        return str(json.loads(cache.read_text())[provider]["name"]) or provider
    except (OSError, ValueError, LookupError, TypeError):
        return provider


def hermes_config():
    """Hermes' own configuration file, where its MCP servers (`mcp_servers`) are."""
    return Path(os.environ.get("HERMES_HOME") or Path.home() / ".hermes") / "config.yaml"


def agent_name():
    """The agent kortex talks to: KORTEX_AGENT, else ~/.config/kortex/agent, else opencode."""
    given = os.environ.get("KORTEX_AGENT")
    if not given:
        try:
            given = (config_dir() / "agent").read_text().strip()
        except OSError:
            given = ""
    return "hermes" if given == "hermes" else "opencode"


def fill_placeholders(text, base):
    """opencode's placeholders in a piece of its configuration, filled as opencode fills
    them when it reads its file: {env:NAME} and {file:path} (~/ or relative to `base`),
    the file's content trimmed; `text` is JSON and stays JSON."""
    def file(m):
        return json.dumps((base / Path(m.group(1)).expanduser()).read_text().strip())[1:-1]
    text = re.sub(r"\{env:([^}]+)\}", lambda m: json.dumps(os.environ.get(m.group(1), ""))[1:-1], text)
    return re.sub(r"\{file:([^}]+)\}", file, text)


def mtime(path):
    try:
        return path.stat().st_mtime_ns
    except OSError:
        return None


def server_password():
    """The password of the server kortex starts: made once, readable by the user only."""
    path = config_dir() / "password"
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(secrets.token_urlsafe(32) + "\n")
    return path.read_text().strip()


def find_opencode():
    """opencode on PATH, or where its installers put it; KORTEX_OPENCODE overrides."""
    given = os.environ.get("KORTEX_OPENCODE")
    if given:
        return given
    home = Path.home()
    path = os.pathsep.join([os.environ.get("PATH", ""), str(home / ".local/bin"), str(home / ".opencode/bin")])
    found = shutil.which("opencode", path=path)
    if not found:
        raise AgentError("opencode is not installed (https://opencode.ai)")
    return found


def find_patina():
    """patina's command, when this desktop has patina, or None: it keeps the user's own
    instructions for the agent beside its own (`patina ai prompt`). PATINA_PATH names
    its checkout."""
    given = os.environ.get("PATINA_PATH")
    if given and (Path(given) / "bin/patina").is_file():
        return str(Path(given) / "bin/patina")
    path = os.pathsep.join([os.environ.get("PATH", ""), str(Path.home() / ".local/bin")])
    return shutil.which("patina", path=path)


def find_hermes():
    """hermes on PATH, or where its installer links it; KORTEX_HERMES overrides."""
    given = os.environ.get("KORTEX_HERMES")
    if given:
        return given
    home = Path.home()
    path = os.pathsep.join([os.environ.get("PATH", ""), str(home / ".local/bin"),
                            str(home / ".hermes/hermes-agent/venv/bin")])
    found = shutil.which("hermes", path=path)
    if not found:
        raise AgentError("Hermes Agent is not installed (https://hermes-agent.nousresearch.com)")
    return found


def note_event(asked, line, streamed=None, errors=None):
    """Keep `asked`, the pending permission requests by id, current with one line of
    opencode's event stream (`data: {"type": ..., "properties": ...}`), and `streamed`, the
    text of each part still being written, by part id: opencode 1.19 keeps a text or
    reasoning part empty in GET /session/:id/message until the part is done, and sends
    the words on its event stream meanwhile. `errors`: the error each conversation stopped
    on last, by session id, with when it came (ms): opencode 1.19 says one that comes
    before it writes a reply ("Model not found" for a provider without a key) only here,
    and stores the request without a reply."""
    if not line.startswith("data:"):
        return
    try:
        event = json.loads(line[5:])
        kind, props = event["type"], event["properties"]
        if kind == "permission.asked":
            asked[props["id"]] = props
        elif kind == "permission.replied":
            asked.pop(props["requestID"], None)
        elif streamed is not None and kind == "message.part.delta" and props.get("field") == "text":
            streamed[props["partID"]] = streamed.get(props["partID"], "") + props["delta"]
        elif streamed is not None and kind == "message.part.updated" and props["part"].get("text"):
            streamed.pop(props["part"]["id"], None)  # done: GET /message has the whole of it now
        elif errors is not None and kind == "session.error" and props.get("sessionID"):
            errors[props["sessionID"]] = (time.time() * 1000, props.get("error") or {})
    except (ValueError, LookupError, TypeError, AttributeError):
        pass  # not an event kortex follows


def retry_message(status, now=time.time):
    """Why opencode waits before it tries a request again, and when it will: its own
    title for the reason ("Free limit reached") where it gives one, since the provider's
    words may be an offer ("subscribe to …"), and the time of the next try once that is
    a minute or more away, so a limit that lasts hours does not read as a moment."""
    words = (status.get("action") or {}).get("title") or status.get("message", "")
    try:
        at = float(status.get("next") or 0) / 1000
    except (TypeError, ValueError):
        at = 0
    if at - now() >= 60:
        words += (" · " if words else "") + "next try " + time.strftime("%H:%M", time.localtime(at))
    elif isinstance(status.get("attempt"), int) and status["attempt"] > 1:
        words += (" · " if words else "") + f"try {status['attempt']}"
    return words


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Server:
    """An agent's HTTP server on this machine, started by kortex on first use and stopped
    with it; or one the environment names that runs already, which kortex never stops."""

    def __init__(self, url=None, password=None):
        self.url = url
        self.password = password
        self.process = None
        # Never through a proxy: the server is on this machine.
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        self.asked = {}  # permission requests from an event stream, by id; under self.lock
        self.streamed = {}  # the text of parts still being written, by part id; under self.lock
        self.errors = {}  # the error each conversation stopped on last, (when, error) by id; under self.lock
        self.lock = threading.Lock()
        self.listened = None  # the server the event stream is followed for
        self.config_seen = None  # the agent's configuration's mtime as the server in use read it
        self.auth_seen = None  # and its keys' file's

    def owned(self):
        return self.process is not None

    def not_ready(self):
        """Why the agent cannot answer at all, said before a request goes to it, or None:
        Hermes says a provider's error in its run, which kortex follows."""

    def stopped(self):
        """True when the server kortex started has exited since."""
        return self.process is not None and self.process.poll() is not None

    def launch(self, command, env, log_name, health, wait=30):
        """Start the server and wait until `health` answers."""
        state_dir().mkdir(parents=True, exist_ok=True)
        log(f"starting {' '.join(command)}")
        with open(state_dir() / log_name, "ab") as output:
            begun = output.tell()
            self.process = subprocess.Popen(
                command, cwd=Path.home(), env=dict(os.environ, **env), stdin=subprocess.DEVNULL,
                stdout=output, stderr=output, start_new_session=True)
        deadline = time.monotonic() + wait
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                with open(state_dir() / log_name, "rb") as output:
                    output.seek(begun)
                    said = output.read().decode("utf-8", "replace")
                error = start_error(said)
                advice = start_advice(error)
                raise AgentError(f"{self.title} did not start: " + (f"{advice}\nIts words: {error}" if advice else error))
            try:
                # opencode leaves the first connection it accepts while it starts
                # unanswered; a short wait per try moves on to the next one.
                self.request("GET", health, start=False, timeout=1, quiet=True)
                return
            except AgentError:
                time.sleep(0.2)
        raise AgentError(f"{command[0]} did not answer within {wait} seconds")

    def stop(self):
        if self.process is None:
            return
        self.process.terminate()
        try:
            self.process.wait(5)
        except subprocess.TimeoutExpired:
            self.process.kill()
        self.process = None
        self.url = None
        self.listened = None  # a new server may get the same port
        with self.lock:
            self.asked.clear()
            self.streamed.clear()
            self.errors.clear()

    def request(self, method, path, body=None, start=True, timeout=10, quiet=False):
        if start:
            self.start()
        data = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request(self.url + path, data=data, method=method)
        req.add_header("Content-Type", "application/json")
        self.authorize(req)
        try:
            with self.opener.open(req, timeout=timeout) as response:
                raw = response.read()
            return json.loads(raw) if raw else None
        except (urllib.error.URLError, OSError, http.client.HTTPException, ValueError) as e:
            if not quiet:  # the details, for the journal; not each try while a server starts
                log(f"{method} {path}: {e}")
            raise AgentError(f"{self.title} {failure(e, timeout)}") from e


def start_error(output):
    """Why an agent's server exited as it started, from what it wrote: its error line and
    the details under it (opencode on a broken opencode.json: "Config file at … is not
    valid JSON(C)", then "ValueExpected at line 1, column 2"). The palette showed the
    path of the program and of its log."""
    lines = [line.strip() for line in re.sub(r"\x1b\[[0-9;]*m", "", output).splitlines() if line.strip()]
    error = next((line for line in lines if line.startswith("Error:")), lines[-1] if lines else "")
    error = error.removeprefix("Error:").strip().rstrip(":")
    if "--- Errors ---" in lines:
        details = lines[lines.index("--- Errors ---") + 1:][:1]
        error += "".join(f": {d}" for d in details if not d.startswith("---"))
    return shorten_end(error, 300) if error else "it exited without a word (its log is in ~/.local/state/kortex)"


def start_advice(error):
    """What to do about an agent that did not start, for what a user can mend: a file its
    settings name that is gone (the key of `patina ai login`), a settings file that is
    not valid JSON. "" for anything else; the agent's own words follow either way."""
    missing = re.search(r"\{file:([^}]+)\}", error)
    if missing and re.search(r"bad file reference|does not exist|not found|ENOENT", error):
        return (f"A file its settings name is missing ({missing.group(1)}). Run patina ai login to set the key "
                "again, or mend the agent's own settings.")
    if re.search(r"not valid JSON|JSON\(C\)|Expected at line|parse error", error, re.IGNORECASE):
        return ("Its settings file is not valid. Undo your last change to it, or run patina ai on to write it "
                "again.")
    return ""


def failure(error, timeout):
    """Why a request to the agent's server failed, in words for the palette: what
    urllib says ("<urlopen error [Errno 111] Connection refused>") explains nothing to
    a user, and the log has it."""
    if isinstance(error, urllib.error.HTTPError):
        if error.code in (401, 403):
            return f"refused kortex's password (HTTP {error.code})"
        if error.code >= 500:
            return f"failed on the request (HTTP {error.code})"
        return f"did not take the request (HTTP {error.code})"
    reason = getattr(error, "reason", error)
    if isinstance(reason, ConnectionRefusedError):
        return "is not running"
    if isinstance(reason, TimeoutError):
        return f"did not answer within {timeout} s"
    if isinstance(error, ValueError):
        return "sent an answer kortex cannot read"
    return "could not be reached"


class Opencode(Server):
    """opencode's HTTP server (`opencode serve`). KORTEX_OPENCODE_URL names one that runs
    already."""

    title = "opencode"  # the agent's name, which the palette says once, as a detail

    @classmethod
    def from_environment(cls):
        url = os.environ.get("KORTEX_OPENCODE_URL")
        if url:
            return cls(url.rstrip("/"), os.environ.get("KORTEX_OPENCODE_PASSWORD"))
        return cls(None, server_password())

    def start(self):
        if self.url and (self.process is None or self.process.poll() is None):
            self.listen()
            return
        command = find_opencode()
        port = free_port()
        self.config_seen = mtime(opencode_config())  # the new server reads it as it is now
        self.auth_seen = mtime(opencode_auth())  # and its keys
        self.url = f"http://127.0.0.1:{port}"
        self.launch([command, "serve", "--hostname", "127.0.0.1", "--port", str(port)],
                    {"OPENCODE_SERVER_PASSWORD": self.password}, "opencode.log", "/global/health")
        self.listen()

    def add_services(self):
        """MCP servers added to opencode's configuration since its server started, added
        to the running server (POST /mcp), which does not read its configuration again: a
        service connected mid-conversation (`patina ai connect`) is there at once. One
        changed or removed waits for the next start. opencode fills {file:...} and {env:...}
        only in what it reads from its file, so kortex fills them in what it sends."""
        path = opencode_config()
        now = mtime(path)
        if self.url is None or now == self.config_seen:
            return
        self.config_seen = now
        try:
            wanted = json.loads(path.read_text()).get("mcp") or {}
            running = self.request("GET", "/mcp", start=False) or {}
            for name, config in wanted.items():
                if name not in running and isinstance(config, dict) and config.get("enabled", True):
                    log(f"adding the MCP server {name} to the running agent")
                    config = json.loads(fill_placeholders(json.dumps(config), path.parent))
                    self.request("POST", "/mcp", {"name": name, "config": config}, start=False)
        except (OSError, ValueError, AttributeError, AgentError) as e:
            log(f"MCP servers of {path} not added: {e}")  # opencode reports its own file's problems

    def listen(self):
        """Follow the event stream of the server in use, in a thread of its own."""
        if self.listened != self.url:
            self.listened = self.url
            threading.Thread(target=self.follow, args=(self.url,), daemon=True).start()

    def follow(self, url):
        """The thread: read the stream while `url` is the server in use; no GLib or D-Bus
        here, only the requests under the lock."""
        req = urllib.request.Request(url + "/event")
        self.authorize(req)
        while self.url == url:
            try:
                with self.opener.open(req, timeout=60) as response:
                    for raw in response:
                        if self.url != url:
                            break
                        with self.lock:
                            note_event(self.asked, raw.decode("utf-8", "replace"), self.streamed, self.errors)
            except (OSError, http.client.HTTPException):
                pass  # the server stopped or restarts; try again while it is the one in use
            time.sleep(1)

    def authorize(self, req):
        if self.password:
            token = base64.b64encode(f"opencode:{self.password}".encode()).decode()
            req.add_header("Authorization", "Basic " + token)

    def attach(self, session):
        """The command, and its environment, that continues a session in opencode's own
        interface, or a new one without `session`; the password goes in the environment,
        not on the command line."""
        self.start()
        return ([find_opencode(), "attach", self.url] + (["--session", session] if session else []),
                {"OPENCODE_SERVER_PASSWORD": self.password or ""})

    def new_session(self, title):
        return self.request("POST", "/session", {"title": title})["id"]

    def instructions_file(self):
        """The user's own instructions for opencode, which it reads beside a project's."""
        return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "opencode" / "AGENTS.md"

    def ask(self, session, text):
        self.request("POST", f"/session/{session}/prompt_async", {"parts": [{"type": "text", "text": text}]})

    def not_ready(self):
        """Why opencode cannot answer at all, said before a request goes to it: the
        provider of its model is not among those it can use (GET /config/providers),
        since it has no key; NO_KEY with its name, or None. opencode took such a request,
        said "Model not found" on its event stream only, and stopped without a reply. Its
        server reads the keys as it starts: one added since (opencode's own login) is read
        again first, while nothing of it works."""
        try:
            model = (self.request("GET", "/config") or {}).get("model") or ""
            provider = model.partition("/")[0] if "/" in model else ""
            usable = self.usable() if provider else None
            if usable is not None and provider not in usable and mtime(opencode_auth()) != self.auth_seen \
                    and self.owned() and not self.busy():
                self.auth_seen = mtime(opencode_auth())
                log("opencode's keys changed since its server started; it reads them again")
                self.request("POST", "/instance/dispose")
                usable = self.usable()
        except AgentError as e:
            if http_status(e) in (400, 404):  # an opencode without these
                return None
            raise
        if usable is None or provider in usable:
            return None
        return NO_KEY.format(name=known_provider_name(provider))

    def usable(self):
        """The providers opencode can use, by id, or None when it does not say."""
        found = (self.request("GET", "/config/providers") or {}).get("providers")
        if not isinstance(found, list):
            return None
        return {p.get("id") for p in found if isinstance(p, dict)}

    def login_command(self):
        """opencode's own login to a provider, for a desktop without patina."""
        return [find_opencode(), "auth", "login"]

    def messages(self, session):
        """The conversation, with the parts still being written filled in from the event
        stream, so the answer, and what the agent thinks first, grow on screen; and the
        error it stopped on, from the event stream, as its reply where the request has
        none (opencode stores the request and says the error only there). "Model not
        found" for a provider without a key says that."""
        found = self.request("GET", f"/session/{session}/message")
        with self.lock:
            for message in found:
                for part in message.get("parts", []):
                    if not part.get("text") and part.get("id") in self.streamed:
                        part["text"] = self.streamed[part["id"]]
            stopped = self.errors.get(session)
        i = last_user(found)
        if (stopped and i >= 0 and stopped[0] >= ((found[i]["info"].get("time") or {}).get("created") or 0)
                and not any(m["info"].get("role") == "assistant" for m in found[i + 1:])):
            error = stopped[1] if isinstance(stopped[1], dict) else {}
            if NO_MODEL.search(str((error.get("data") or {}).get("message") or "")):
                try:
                    reason = self.not_ready()
                except AgentError:
                    reason = None
                if reason:
                    error = {"name": "NotReady", "data": {"message": reason}}
            found.append({"info": {"role": "assistant", "error": error,
                                   "time": {"created": stopped[0], "completed": stopped[0]}}, "parts": []})
        return found

    def status(self, session):
        """idle, busy or retry, with the retry's message."""
        status = self.request("GET", "/session/status").get(session) or {"type": "idle"}
        return status.get("type", "idle"), retry_message(status)

    def busy(self):
        """True while any conversation of the server works: one in the panel too."""
        found = self.request("GET", "/session/status", start=False) or {}
        return any(isinstance(s, dict) and s.get("type", "idle") != "idle" for s in found.values())

    def family(self, session):
        """The conversation and the ones its agent started for a part of the work (the
        `task` tool): what one of those asks is asked of this conversation's user."""
        try:
            children = self.request("GET", f"/session/{session}/children") or []
        except AgentError:
            children = []
        return {session} | {c["id"] for c in children if isinstance(c, dict) and c.get("id")}

    def permissions(self, session):
        mine = self.family(session)
        try:
            found = self.request("GET", "/permission")
        except AgentError as e:
            if http_status(e) != 400:
                raise
            with self.lock:
                found = list(self.asked.values())
            # A request of another conversation the stream did show explains the 400.
            return [p for p in found if p.get("sessionID") in mine] or ([] if found else [UNREADABLE])
        return [p for p in found if p.get("sessionID") in mine]

    def questions(self, session):
        """What the agent asks the user (its `question` tool) and waits for: each with
        its id, the question and the answers it offers."""
        mine = self.family(session)
        try:
            found = self.request("GET", "/question") or []
        except AgentError as e:
            if http_status(e) in (400, 404):  # an opencode without it
                return []
            raise
        asked = []
        for q in found:
            if q.get("sessionID") not in mine:
                continue
            for i, one in enumerate(q.get("questions") or []):
                options = [o for o in one.get("options") or [] if isinstance(o, dict) and o.get("label")]
                asked.append({"id": q["id"], "index": i, "of": len(q["questions"]),
                              "question": one.get("question") or one.get("header") or "A question",
                              "options": [o["label"] for o in options],
                              "about": [str(o.get("description") or "") for o in options]})
        return asked

    def answer(self, request_id, answers):
        """Answer a question: one list of labels (or the user's own words) per question."""
        try:
            self.request("POST", f"/question/{request_id}/reply", {"answers": answers})
        except AgentError as e:
            if http_status(e) != 404:  # answered already, in the terminal say
                raise

    def abort(self, session):
        """Stop what the agent does in the conversation."""
        self.request("POST", f"/session/{session}/abort")

    def reply(self, request_id, reply):
        try:
            self.request("POST", f"/permission/{request_id}/reply", {"reply": reply})
        except AgentError as e:
            if http_status(e) != 404:  # 404: answered already, in the terminal say
                raise

    def sessions(self, limit):
        found = self.request("GET", f"/session?roots=true&limit={limit}")
        found.sort(key=lambda s: s.get("time", {}).get("updated", 0), reverse=True)
        return found[:limit]


class Hermes(Server):
    """Hermes Agent's API server (`hermes gateway run`, its API server switched on by a
    key in the environment). KORTEX_HERMES_URL and KORTEX_HERMES_KEY name one that runs
    already. Hermes asks before a command it counts as dangerous, not before every
    change; the request comes on the run's event stream."""

    title = "Hermes"
    DONE = ("completed", "failed", "cancelled", "interrupted", "stopped")

    def __init__(self, url=None, password=None):
        super().__init__(url, password)
        self.runs = {}  # the last run of each conversation, by session id

    @classmethod
    def from_environment(cls):
        url = os.environ.get("KORTEX_HERMES_URL")
        if url:
            return cls(url.rstrip("/"), os.environ.get("KORTEX_HERMES_KEY"))
        return cls(None, server_password())

    def start(self):
        if self.url and (self.process is None or self.process.poll() is None):
            return
        command = find_hermes()
        port = free_port()
        self.config_seen = mtime(hermes_config())
        self.url = f"http://127.0.0.1:{port}"
        self.launch([command, "gateway", "run"],
                    {"API_SERVER_KEY": self.password, "API_SERVER_PORT": str(port), "API_SERVER_HOST": "127.0.0.1"},
                    "hermes.log", "/health", wait=60)

    def authorize(self, req):
        if self.password:
            req.add_header("Authorization", "Bearer " + self.password)

    def attach(self, session):
        return [find_hermes()] + (["--resume", session] if session else []), {}

    def stop(self):
        super().stop()
        self.runs.clear()  # a new gateway knows none of them

    def instructions_file(self):
        """Hermes' own file of the user's instructions, its persona."""
        return hermes_config().parent / "SOUL.md"

    def add_services(self):
        """Hermes reads its MCP servers (`mcp_servers` in config.yaml) when its gateway
        starts. When the file changed since, the gateway kortex started stops once no run
        is busy, and the next request starts it again with them; the conversations stay."""
        now = mtime(hermes_config())
        if not self.owned() or self.url is None or now == self.config_seen:
            return
        if any(self.status(session)[0] == "busy" for session in list(self.runs)):
            return
        log("Hermes' configuration changed; restarting its gateway for the next request")
        self.stop()

    def follow(self, run, session):
        """The thread: keep the approval requests of one run, from its event stream."""
        req = urllib.request.Request(f"{self.url}/v1/runs/{run}/events")
        self.authorize(req)
        try:
            with self.opener.open(req, timeout=600) as response:
                for raw in response:
                    with self.lock:
                        if note_hermes_event(self.asked, raw.decode("utf-8", "replace"), session, run):
                            break
        except (OSError, http.client.HTTPException):
            pass  # the run ended or the server stopped; its status says which
        with self.lock:
            for key in [k for k, p in self.asked.items() if p["run"] == run]:
                del self.asked[key]

    def new_session(self, title):
        """A conversation named after the request. Hermes takes a title once: the same
        request asked again (the first 60 characters of it) was refused for good, with
        an HTTP error as the palette's answer. The second gets the time behind it."""
        try:
            return self.request("POST", "/api/sessions", {"title": title})["session"]["id"]
        except AgentError as e:
            if http_status(e) != 400:
                raise
        again = f"{title[:50]} · {time.strftime('%d.%m. %H:%M:%S')}"
        return self.request("POST", "/api/sessions", {"title": again})["session"]["id"]

    def ask(self, session, text):
        run = self.request("POST", "/v1/runs", {"input": text, "session_id": session})["run_id"]
        self.runs[session] = run
        threading.Thread(target=self.follow, args=(run, session), daemon=True).start()

    def run_state(self, session):
        """The last run of a conversation; {} for none, or one this gateway does not know."""
        run = self.runs.get(session)
        if not run:
            return {}
        try:
            return self.request("GET", f"/v1/runs/{run}")
        except AgentError as e:
            if http_status(e) == 404:
                return {}
            raise

    def messages(self, session):
        found = self.request("GET", f"/api/sessions/{session}/messages")
        return hermes_messages(found.get("data") or [], self.run_state(session))

    def status(self, session):
        state = self.run_state(session).get("status")
        return ("idle", "") if state is None or state in self.DONE else ("busy", "")

    def busy(self):
        """True while a run of the gateway works: one in its own interface kortex does
        not know of is not seen."""
        return any(self.status(session)[0] == "busy" for session in list(self.runs))

    def permissions(self, session):
        with self.lock:
            return [p for p in self.asked.values() if p["sessionID"] == session]

    def questions(self, session):
        return []  # Hermes asks in its answer

    def reply(self, request_id, reply):
        """The request is kept until Hermes has the answer, so one that did not reach it
        is shown again and can be answered again."""
        with self.lock:
            asked = self.asked.get(request_id)
        if not asked:
            return
        try:
            self.request("POST", f"/v1/runs/{asked['run']}/approval",
                         {"request_id": request_id, "choice": HERMES_CHOICES[reply]})
        except AgentError as e:
            # 404: the run is over; 409: Hermes gave up on the question. Nothing waits
            # for the answer either way.
            if http_status(e) not in (404, 409):
                raise
        with self.lock:
            self.asked.pop(request_id, None)

    def sessions(self, limit):
        found = self.request("GET", f"/api/sessions?limit={limit}").get("data") or []
        found = [{"id": s["id"], "title": s.get("title"),
                  "time": {"updated": int((s.get("last_active") or s.get("started_at") or 0) * 1000)}} for s in found]
        found.sort(key=lambda s: s["time"]["updated"], reverse=True)
        return found[:limit]


def note_hermes_event(asked, line, session, run):
    """Keep `asked` current with one line of a Hermes run's event stream; True once the
    run has ended."""
    if not line.startswith("data:"):
        return False
    try:
        event = json.loads(line[5:])
        kind = event.get("event")
        if kind == "approval.request":
            what, command = event.get("description") or "a command", event.get("command", "")
            service = command.endswith("(plugin approval rule)") and ": " in what
            if service:
                # A hook sent the tool to the approval (patina's gate before a service's
                # tool): its message says what is asked, "tracker: update_issue {…}";
                # the command is Hermes' name for the rule.
                what, command = what.split(": ", 1)
            asked[event["request_id"]] = {
                "id": event["request_id"], "sessionID": session, "run": run,
                "permission": what, "patterns": [command], **({"service": True} if service else {})}
        elif kind == "approval.responded":
            asked.pop(event.get("request_id"), None)
        elif kind in ("message.delta", "message.interim", "reasoning.available", "reasoning.delta"):
            # The model writes or thinks again: no command of this run waits any more.
            # Hermes gives up on a question after a while (`approvals.timeout`, five
            # minutes) and goes on without the command, and says so nowhere: its run
            # still reads "waiting_for_approval". The row stood in the palette, "Waiting
            # for your answer", while the agent worked on.
            for key in [k for k, p in asked.items() if p["run"] == run]:
                del asked[key]
        return kind in ("run.completed", "run.failed", "run.cancelled", "run.interrupted")
    except (ValueError, LookupError, TypeError, AttributeError):
        return False  # not an event kortex follows


def hermes_messages(found, run):
    """Hermes' messages of a conversation in the shape the palette shows (opencode's):
    text parts, and one tool part per call with the result's status."""
    results = {m.get("tool_call_id"): m for m in found if m.get("role") == "tool"}
    messages = []
    for m in found:
        role = m.get("role")
        if role not in ("user", "assistant"):
            continue
        parts = [{"type": "text", "text": m["content"]}] if m.get("content") else []
        for call in m.get("tool_calls") or []:
            function = call.get("function") or {}
            try:
                given = json.loads(function.get("arguments") or "{}")
            except ValueError:
                given = {}
            result, status = results.get(call.get("id")), "running"
            if result is not None:
                try:
                    outcome = json.loads(result.get("content") or "{}")
                except ValueError:
                    outcome = {}
                failed = isinstance(outcome, dict) and (outcome.get("error") or outcome.get("exit_code") not in (None, 0))
                status = "error" if failed else "completed"
            parts.append({"type": "tool", "tool": function.get("name", "tool"), "state": {"status": status, "input": given}})
        messages.append({"info": {"role": role}, "parts": parts})
    if run.get("status") == "failed":
        error = run.get("error") or run.get("last_error") or "the run failed"
        error = error.get("message", str(error)) if isinstance(error, dict) else str(error)
        messages.append({"info": {"role": "assistant", "error": {"data": {"message": error}}}, "parts": []})
    return messages


# ---- what the palette shows: plain functions of opencode's data ------------------------


def parse(query, agent=None):
    """(kind, text): ask with the request, list for `ai` alone, view with an optional
    follow-up after `kortex ›` or `ai ›` (`>` is read as `›`), or (None, "") for any other search. The
    agent's name with the marker (`opencode ›`), as 0.4 called the conversation, shows
    it too. Any case, and a tab, are read as typed."""
    q = query.replace(JOINER, "").strip()
    if q.lower() == TRIGGER:
        return "list", ""
    for word in dict.fromkeys(w for w in (NAME, TRIGGER, agent) if w):
        head, rest = q[:len(word)], q[len(word):]
        # `kortex ›`, `kortex›`, not `kortexx ›`
        # `>` too: few keyboards have a key for ›, which the hint names
        if head.lower() == word.lower() and rest.lstrip().startswith((VIEW_MARK, ">")) and not rest[:1].isalnum():
            return "view", rest.lstrip()[1:].strip()
    if q[:len(TRIGGER)].lower() != TRIGGER or not q[len(TRIGGER):][:1].isspace():
        return None, ""
    return "ask", q[len(TRIGGER):].strip()


def asking(text):
    """True when a search reads as a request to the agent rather than a search: it ends
    with a question mark, or it has three words or more and starts with a question or a
    request (ASKING), or two and starts with install (ASKING_TWO)."""
    words = text.lower().split()
    first = words[0].strip(",.:!") if words else ""
    return (text.rstrip().endswith("?") or (len(words) >= 3 and first in ASKING)
            or (len(words) >= 2 and first in ASKING_TWO))


def tool_line(part):
    """A step the agent took, on one line: the tool and what it was given."""
    state = part.get("state") or {}
    given = state.get("input") or {}
    detail = given.get("command") or given.get("filePath") or given.get("path") or given.get("url") or state.get("title") or ""
    if not detail and isinstance(given, dict):
        # A service's tool (MCP): what it was given says which issue or note it touched.
        detail = shorten_end(", ".join(f"{k}={v}" for k, v in given.items() if isinstance(v, (str, int, float, bool))), 100)
    line = " ".join(f"{part.get('tool', 'tool')} {detail}".splitlines()).rstrip()  # a heredoc's too
    if state.get("status") == "error":
        line += " (denied)" if denied(part) else " (failed)"
    return line


def composing(part):
    """True for a tool call the model is still writing: it has no input yet, and its
    row would be the tool's bare name."""
    state = part.get("state") or {}
    return state.get("status") == "pending" and not state.get("input")


def denied(part):
    """True for a step the user denied: opencode marks it failed, with these words."""
    state = part.get("state") or {}
    return state.get("status") == "error" and "rejected permission" in str(state.get("error", ""))


EXAMPLES = ("why is my wifi slow?", "what keeps the fan busy?")  # for the empty palette; both only look
DENIED = "Stopped: you denied the last step, so it did not run. Say what to do instead."
# The user's own Stop: what the agent wrote until then stays, under these words, not an error
STOPPED = "You stopped it here. Say what to do instead."
# What an agent's provider that cannot be reached, or refuses its key, says (opencode
# passes the provider's or the network's words on): the palette names the provider and
# where to check it, the words go under that.
UNREACHABLE = re.compile(r"cannot connect|connection|econn\w+|enotfound|etimedout|getaddrinfo|fetch failed|tunnel"
                         r"|bad gateway|unauthori[sz]ed|api key|\b40[13]\b|forbidden|certificate", re.IGNORECASE)
# A model the provider does not have ("Model not found: Qwen 9.9 99B")
NO_MODEL = re.compile(r"model.{0,80}(not found|does not exist|not available|not supported|unknown)"
                      r"|(unknown|no such|invalid) model", re.IGNORECASE)
LOGIN = "patina ai login, or in the agent's own settings"


def provider_name(provider):
    """A provider's name as opencode's settings give it ("Your own · api.example.eu"),
    else its id: the id ("custom") said nothing to the user."""
    try:
        return str(json.loads(opencode_config().read_text())["provider"][provider]["name"]) or provider
    except (OSError, ValueError, LookupError, TypeError):
        return provider


def error_words(error):
    """The message of an error a provider sent as data ("{'error': {'message': …}}"),
    which the palette showed as it came."""
    found = re.search(r"""['"]message['"]\s*:\s*(['"])(.+?)\1\s*[,}]""", error)
    return found.group(2) if found else error


def why_stopped(messages, error):
    """The error that stopped the agent, in words for the palette and the notification:
    for a provider it cannot reach or that refuses its key, which provider and where to
    check it, with the error under it; a raw "Cannot connect to API: ECONNREFUSED" after a
    minute of retries said neither. For a model the provider lacks, where to pick another."""
    if error == "Aborted":
        return STOPPED
    words = error_words(error)
    if NO_MODEL.search(words):
        return f"{words}\nPick another model with {LOGIN}."
    if not UNREACHABLE.search(error):
        return words
    provider = next((m["info"].get("providerID") for m in reversed(messages)
                     if m["info"].get("role") == "assistant" and m["info"].get("providerID")), "")
    name = provider_name(provider) if provider else ""
    return (f"The agent could not reach its provider{f' ({name})' if name else ''}. Check its address and "
            f"key with {LOGIN}.\n{words}")


def retry_words(retry):
    """Why the agent tries its provider again, in a few words: one it cannot reach says
    so, and where to check it, at once; the provider's words, a line of 125 characters
    the row cut, are the log's."""
    if not UNREACHABLE.search(retry):
        return retry
    tail = re.search(r" · ((?:next )?try [\d:]+)$", retry)
    return "Can't reach the provider" + (f" · {tail.group(1)}" if tail else "") + " · patina ai login checks it"


# A model may end its turn after it thought, without a word or a step (Qwen 3.6 did it
# again and again): the palette showed nothing, and the notification "Done.".
NO_ANSWER = "The agent stopped without an answer."
# A request the agent stored and went idle on, without a reply or an error: opencode does
# so for a model its settings do not list, and says why only on its event stream, which
# kortex may not have heard (it started after, or the agent's server did). The palette
# showed nothing for minutes, and a conversation opened later its request alone.
NOT_TAKEN = (f"The agent did not answer, and said nothing of why. Its model may be one its provider does not "
             f"have: pick another with {LOGIN}.")
NOT_TAKEN_AFTER = 20  # seconds a request may sit unanswered while the agent is idle
# The agent's model is of a provider it has no key for (`not_ready`): opencode took the
# request, said "Model not found" on its event stream only and stopped, and the palette
# showed nothing. Said before the request goes, with a row that logs in (LOGIN_ROW).
NO_KEY = "{name} has no key yet, so the agent cannot answer. Log in with " + LOGIN + "."
LOGIN_ROW = ("Log in to the agent's provider", "Enter opens the login in a terminal")


def needs_login(text):
    """True for an error that says the provider has no key (NO_KEY)."""
    return "has no key yet, so the agent cannot answer" in text
# A reply whose message never completed although the agent is idle: its server stopped
# (or was killed) mid-answer and a new one read the conversation; the text it has is cut
# off, and showed as a finished answer.
CUT_OFF = "Cut off: the agent stopped before it finished, and what follows is all it wrote. Ask again."


def cut_off(messages):
    """True when the agent's last message of the reply never completed: opencode stamps
    `time.completed` on each that does. Hermes stamps none, and is never taken as cut off."""
    last = next((m for m in reversed(messages[last_user(messages) + 1:]) if m["info"].get("role") == "assistant"), None)
    if last is None:
        return False
    info = last["info"]
    return isinstance(info.get("time"), dict) and not info["time"].get("completed") and not info.get("error")


# A turn that ends after a step without a word, or on words that announce more ("I'll
# clean what's safe now:", "Let me create the timer."): what it said before read as done
# ("I'll add the cron job…" with no cron job), and steps alone read "Done." after a
# folder was made and nothing in it.
UNFINISHED = "The agent stopped here without saying it is done. If it is not, say “go on”."
ANNOUNCES = re.compile(r"(?:(?:ok(?:ay)?|now|next|first|then|so|alright),?\s+)?(?:i'll|i’ll|i will|i'm going to"
                       r"|i am going to|let me|let's)\b(?!\s+(?:know|be)\b)", re.IGNORECASE)


def unfinished(words):
    """True when what the agent said last announces more than it did: it ends on a colon,
    or its last sentence says what it will do next."""
    words = words.strip()
    return words.endswith(":") or bool(ANNOUNCES.match(last_sentence(words + "\n")))


def step_words(part):
    """A step that ran, in a few words: its command, else the tool and what it was given."""
    command = ((part.get("state") or {}).get("input") or {}).get("command")
    return head(" ".join(command.split()), 80) if isinstance(command, str) and command.strip() else tool_line(part)


def last_thought(messages):
    """What the agent thought last in its reply, on a line, or "": the list a model put
    only in its thoughts was lost behind "Done."."""
    thought = next((p.get("text", "") for p in reversed(reply_parts(messages)) if p.get("type") == "reasoning"
                    and p.get("text", "").strip()), "")
    return "Its last thought: " + head(" ".join(thought.split()), MAX_NOTE) if thought else ""


def answer_of(messages):
    """The agent's reply to the last request as the palette gives it: what it said
    after its last step, since what it said between the steps ("let me check…") is how
    it got there, not the answer; all it said when nothing follows the last step;
    UNFINISHED after it when it took steps and said nothing since, or announced more;
    NO_ANSWER when it did neither. When the last thing in the reply is a step the user
    denied, what it said before that step, what ran before it and that it stopped there:
    opencode ends the turn on a Deny, and the words before read as if it went on. A Stop
    of the user's keeps what it wrote."""
    said, steps, error = reply_of(messages)
    parts = [p for p in reply_parts(messages) if p.get("type") in ("text", "tool")
             and not p.get("synthetic") and not p.get("ignored") and (p.get("type") == "tool" or p.get("text", "").strip())]
    last = max((i for i, p in enumerate(parts) if p["type"] == "tool"), default=-1)
    after = [p["text"].strip() for p in parts[last + 1:]]
    if parts and parts[-1]["type"] == "tool" and denied(parts[-1]):
        ran = [step_words(p) for p in parts[:-1] if p["type"] == "tool" and (p.get("state") or {}).get("status") == "completed"]
        before = ["What ran before it: " + ("… · " if len(ran) > 4 else "") + " · ".join(ran[-4:])] if ran else []
        return said[-1:] + before + [DENIED]
    if error == "Aborted":
        return (after or said[-1:]) + [STOPPED]
    if cut_off(messages):
        return [CUT_OFF] + said[-1:]
    if not said and not steps:
        return [NO_ANSWER]
    if after:
        return after + ([UNFINISHED] if unfinished(after[-1]) else [])
    if said:  # it said what it would do, then took a step, and ended without a word
        return [said[-1], UNFINISHED]
    thought = last_thought(messages)
    return ([thought] if thought else []) + [UNFINISHED]


def last_user(messages):
    return max((i for i, m in enumerate(messages) if m["info"].get("role") == "user"), default=-1)


# What a model thinks, in its text as tags ("<think>…</think>", one still open)
THINKING = re.compile(r"<think(?:ing)?>.*?(?:</think(?:ing)?>|$)", re.DOTALL)


def reply_parts(messages):
    """The parts of the agent's reply to the last request, in order; a text part without
    the thoughts a model wrote into it."""
    parts = []
    for m in messages[last_user(messages) + 1:]:
        if m["info"].get("role") != "assistant":
            continue
        for p in m.get("parts", []):
            if p.get("type") == "text" and "<think" in str(p.get("text", "")):
                p = dict(p, text=THINKING.sub("", p["text"]))
            parts.append(p)
    return parts


def reply_of(messages):
    """The agent's reply to the last request: what it said (its text parts), the steps
    it took (a line per tool it used) and the error that stopped it, if one did."""
    said, steps, error = [], [], ""
    for part in reply_parts(messages):
        if part.get("type") == "text" and not part.get("synthetic") and not part.get("ignored"):
            if part.get("text", "").strip():
                said.append(part["text"].strip())
        elif part.get("type") == "tool" and not composing(part):
            steps.append(tool_line(part))
    for message in messages[last_user(messages) + 1:]:
        info = message["info"]
        if info.get("role") == "assistant" and info.get("error"):
            data = info["error"].get("data") or {}
            error = data.get("message") or info["error"].get("name", "error")
    return said, steps, error


def latest_of(messages):
    """What the agent wrote last while it works, on one line: (True, its thoughts) when a
    reasoning model thinks, (False, its words) when it writes, (False, "") when it took a
    step last or has written nothing yet."""
    parts = [p for p in reply_parts(messages) if p.get("type") in ("text", "reasoning", "tool")
             and not p.get("synthetic") and not p.get("ignored")]
    if not parts or parts[-1]["type"] == "tool":
        return False, ""
    return parts[-1]["type"] == "reasoning", last_sentence(parts[-1].get("text", ""))


def plain(line):
    """A line of Markdown in plain words: without the mark of a heading, a list's item or
    a quote, and without bold and code marks."""
    line = re.sub(r"^\s*(#{1,6}\s+|[-*+]\s+|\d+[.)]\s+|>\s*)", "", line)
    return line.replace("**", "").replace("__", "").replace("`", "").strip()


def last_sentence(text):
    """The last sentence of `text` that is complete, on one line, in plain words, or ""
    before the first is: what streams in is cut off mid-sentence, and its end changes
    with every word. A line that has ended is complete (a heading, a list's item); a
    code block's lines are no sentences."""
    # a stop ends a sentence only before a space or the end: "8.0G" and "df.py" go on.
    # Only the ends are searched for, the last two of them, in linear time: a pattern
    # for the whole sentence took seconds on a long answer, on the D-Bus thread.
    raw = text.split("\n")
    lines, code = [], False
    for i, line in enumerate(raw):
        if line.strip().startswith("```"):
            code = not code
        elif not code:
            lines.append((line, i < len(raw) - 1))  # the last line may still be written
    for line, complete in reversed(lines):
        line = " ".join(plain(line).split())
        ends = [m.end() for m in re.finditer(r"[.!?](?= |$)", line)]
        if complete and line and (not ends or ends[-1] != len(line)):
            ends.append(len(line))
        if ends:
            return line[ends[-2] if len(ends) > 1 else 0:ends[-1]].strip()
    return ""


DOING = {"bash": "running a command", "read": "reading a file", "edit": "changing a file",
         "write": "writing a file", "glob": "looking for files", "grep": "searching files",
         "list": "looking at a folder", "webfetch": "reading a web page", "task": "handing part of it on",
         "todowrite": "planning", "question": "asking you"}


def doing(messages):
    """What the agent does right now, in words, while its last part is a step: the
    palette shows no commands, and "Working" alone looks stuck during a long one."""
    parts = [p for p in reply_parts(messages) if p.get("type") in ("text", "reasoning", "tool")
             and not p.get("synthetic") and not p.get("ignored")]
    if not parts or parts[-1]["type"] != "tool" or composing(parts[-1]):
        return ""
    tool = str(parts[-1].get("tool", ""))
    if tool in DOING:
        return DOING[tool]
    service = tool.split("_", 1)[0] if "_" in tool else ""
    return f"using {service}" if service else "taking a step"


def explained(p, messages):
    """What the agent said, in its own words, right before the step that asks: the
    text after the step before it, its lines kept. "" when it said nothing in between."""
    parts = [x for x in reply_parts(messages) if not x.get("synthetic") and not x.get("ignored")
             and (x.get("type") == "tool" or x.get("type") == "text" and x.get("text", "").strip())]
    call = (p.get("tool") or {}).get("callID")
    # the last step of that call: a model may give two calls one id
    at = max((i for i, x in enumerate(parts) if call and x.get("type") == "tool" and x.get("callID") == call),
             default=None)
    if at is None:  # Hermes names no call: the step that asks is the last, or not stored yet
        last = parts[-1] if parts else {}
        running = last.get("type") == "tool" and (last.get("state") or {}).get("status") in ("pending", "running")
        at = len(parts) - 1 if running else len(parts)
    for x in reversed(parts[:at]):
        if x["type"] == "tool":
            return ""
        return re.sub(r"\n\s*\n+", "\n", x["text"].strip())
    return ""


def gist(words, most=240):
    """At most `most` characters of what the agent said before a step: its start and its
    last sentence, which says what it will do now; a cut at the end dropped the plan."""
    if len(words) <= most:
        return words
    sentences = [s for s in re.split(r"(?<=[.!?])\s+|\n+", words) if s.strip()]
    last = sentences[-1].strip()
    if len(sentences) < 2 or len(last) > most * 2 // 3:
        return head(" ".join(words.split()), most)
    return head(" ".join(" ".join(sentences[:-1]).split()), most - len(last) - 1) + " " + last


def is_service_tool(p):
    """True for opencode's request for a connected service's tool (`tracker_update_issue`)."""
    kind = p.get("permission", "")
    return kind not in OWN_KINDS and "_" in kind and p.get("always") is not None


def asking_html(p, messages):
    """What a row, or a notification, that asks says after the line of what the step
    would do, as styled text: "Allow", the agent's own words right before the step, as in
    a chat, its Markdown read, with what the step does under them as it is written: the
    command (the words may describe the next step: "I'll install it" over an
    `apt-cache show`), the path and the changed lines of a file, what a service's tool
    is given, since words can be wrong and name no file or ticket ("I'll set both
    tickets to Done." over the first of them). What the step does alone when the agent
    said nothing first, not a blind Allow. The panel shows every step."""
    what = asked_for(p, messages)
    told = asks(p)
    if what != "*":  # a tool that is given nothing
        told += f" ({what})" if is_service_tool(p) else f": {what}"
    if p.get("permission") == "external_directory" and (p.get("metadata") or {}).get("command"):
        # the command reaches outside the home folder: where, then the command
        told = f"{asks(p)} ({', '.join(p.get('patterns') or [])}): {what}"
    words = explained(p, messages)
    if not words:
        return markup("Allow " + told)
    step = what if p.get("permission") in ("bash", None) else told[:1].upper() + told[1:]
    return "Allow: " + rich(gist(words)) + "<br>" + markup(step)


def replied(messages, request):
    """True when the agent has replied to `request`: it is the last request the
    conversation has (in what the agent stored of it, which may hold more), and a reply of
    the agent follows it."""
    i = last_user(messages)
    if i < 0 or not any(m["info"].get("role") == "assistant" for m in messages[i + 1:]):
        return False
    said = " ".join(p.get("text", "") for p in messages[i].get("parts", [])
                    if p.get("type") == "text" and not p.get("synthetic"))
    return request.strip() in said


MORE = "… (the rest: Show the whole answer, below)"
TABLE_RULE = re.compile(r"\|?[\s:|-]*-[\s:|-]*\|?")


def table_rule(line):
    """True for the rule under a table's head (`|---|---|`), which an answer's row drops."""
    line = line.strip()
    return "|" in line and bool(TABLE_RULE.fullmatch(line))


def shorten(text, most=MAX_ANSWER, lines=MAX_LINES):
    """The start of an answer KRunner can show, cut at a line's or a word's end: an
    answer starts with what matters, and a list cut at its head is no list. At most
    `most` characters and `lines` lines as the row wraps them: a long answer was a
    row taller than a 768-px screen, with a scrollbar. A code fence and a table's rule
    are lines the row does not show, and the cut never ends on a table's head."""
    used, end, code = 0, 0, False
    for line in text.split("\n"):
        fence = line.strip().startswith("```")
        rows = 0 if fence or not code and table_rule(line) else max(1, -(-len(line) // LINE))
        code = code != fence
        if used + rows > lines:  # what of this line still fits
            most = min(most, end + (lines - used) * LINE)
            break
        used, end = used + rows, end + len(line) + 1
    if len(text) <= most:
        return text
    start = text[:most]
    cut = max(start.rfind("\n"), start.rfind(" "))
    kept = (start[:cut] if cut > most // 2 else start).rstrip().split("\n")
    full = text.split("\n")
    while kept and (not kept[-1].strip() or table_rule(kept[-1]) or kept[-1].strip().startswith("```")
                    or kept[-1].strip().startswith("|") and len(kept) < len(full) and table_rule(full[len(kept)])):
        kept.pop()  # a table's head without a row of it, or its rule
    return "\n".join(kept).rstrip() + "\n" + MORE


def shorten_end(text, most):
    """The start of `text`, at most `most` characters."""
    return text if len(text) <= most else text[:most - 1] + "…"


def head(text, most=MAX_LATEST):
    """The start of `text`, at most `most` characters, cut at a word's end."""
    if len(text) <= most:
        return text
    start = text[:most - 1]
    return (start.rsplit(" ", 1)[0] if " " in start else start).rstrip(",;: ") + "…"


def seconds(elapsed):
    """How long the agent has been at it, as the palette says it."""
    s = int(elapsed)
    return f"{s} s" if s < 60 else f"{s // 60} min {s % 60} s"


def markup(text):
    """Text as KRunner's styled text (HTML), where `<x ...>` would vanish and a line break
    would be a space."""
    return html.escape(text, quote=False).replace("\n", "<br>")


def rich(text):
    """An answer as KRunner's styled text: escaped, and the Markdown a model writes
    whatever it is told, read as far as a row can show it: **bold** and *italics*, a
    heading as a bold line, a list's dash as a bullet, a table's row with · between its
    cells, a link as its words, a quote without its mark, and code without its
    backticks and fences. A code block's lines stay as written, their indent too: a
    `# comment` in it was a heading."""
    lines, code = [], False
    for line in text.split("\n"):
        bare = line.strip()
        if bare.startswith("```"):
            code = not code
            continue
        if code:
            line = html.escape(line, quote=False)
            lines.append(re.sub(r"^ +", lambda m: "&nbsp;" * len(m.group()), line))
            continue
        if table_rule(bare):
            continue  # the rule under a table's head
        if bare.startswith("|") and bare.endswith("|") and len(bare) > 1:
            line = " · ".join(cell.strip() for cell in bare.strip("|").split("|"))
        line = re.sub(r"^\s*>\s?", "", line)  # a quote's mark
        line = re.sub(r"\[([^\]\n]+)\]\([^)\s]+\)", r"\1", line)  # a link: its words
        line = line.replace("\\*", "\x00").replace("\\_", "\x01")  # escaped by the model: shown as written
        line = html.escape(line, quote=False).replace("`", "")
        heading = re.match(r"\s*#{1,6}\s+(.*)", line)
        if heading:
            line = "**" + heading.group(1).replace("**", "") + "**"
        line = re.sub(r"^(\s*)[-*+]\s+", "\\1• ", line)
        line = re.sub(r"\*\*(.+?)\*\*|__(.+?)__", lambda m: f"<b>{m.group(1) or m.group(2)}</b>", line)
        line = re.sub(r"(?<![\w*])\*(?![\s*])([^*]+?)(?<![\s*])\*(?![\w*])", r"<i>\1</i>", line)
        line = re.sub(r"(?<![\w_])_(?![\s_])([^_]+?)(?<![\s_])_(?![\w_])", r"<i>\1</i>", line)
        line = line.replace("**", "")  # a bold a cut left open
        lines.append(line.replace("\x00", "*").replace("\x01", "_"))
    return "<br>".join(lines)


def match(match_id, text, icon=ICON, relevance=1.0, subtext="", actions=(), multiline=False,
          category=CATEGORY_RELEVANCE, styled=False):
    """One match. KRunner shows a multiline one as styled text, so its text goes as
    escaped HTML, unless it is that already (`styled`)."""
    if multiline and not styled:
        text = markup(text)
    return (match_id, text, icon, relevance,
            {"subtext": subtext, "actions": list(actions), "multiline": multiline, "category": category})


def tool_input(p, messages):
    """What the tool call behind a permission request was given, from the conversation:
    opencode names the call (`tool.callID`) but sends a service's tool as `*`."""
    call = (p.get("tool") or {}).get("callID")
    if not call:
        return {}
    for message in messages:
        for part in message.get("parts", []):
            if part.get("type") == "tool" and part.get("callID") == call:
                given = (part.get("state") or {}).get("input")
                return given if isinstance(given, dict) else {}
    return {}


def diff_lines(diff):
    """The lines a unified diff adds (+) and takes away (-)."""
    return [line for line in (diff or "").splitlines() if line[:1] in "+-" and not line.startswith(("+++", "---"))]


def changed_lines(diff, most=6):
    """The lines a unified diff adds and takes away, the first `most` of them, in words."""
    lines = diff_lines(diff)
    shown = [("adds: " if line[0] == "+" else "removes: ") + shorten_end(line[1:], 110) for line in lines[:most]]
    if len(lines) > most:
        shown.append(f"… {len(lines) - most} more changed lines")
    return shown


def asked_for(p, messages=()):
    """What a permission request would do: opencode's whole command for bash (its
    patterns are the command cut at pipes, `&&` and `;`); for a change to a file, its
    whole path and the lines that change; for a service's tool, what it is given;
    else the patterns."""
    meta = p.get("metadata") or {}
    command = meta.get("command")
    if isinstance(command, str) and command.strip():
        return command
    if p.get("permission") == "edit" and meta.get("filepath"):
        return "\n".join([str(meta["filepath"])] + changed_lines(meta.get("diff")))
    patterns = p.get("patterns") or []
    if patterns in ([], ["*"]):
        given = tool_input(p, messages)
        told = ", ".join(f"{k}={v}" for k, v in given.items() if isinstance(v, (str, int, float, bool)))
        if told:
            return shorten_end(told, 300)
    return ", ".join(patterns) or p.get("permission", "")


def ran_line(p, messages=()):
    """What a step allowed on "Allow the rest of this task" did, on a line for the
    answer: its command, or the file it changed and how many lines, not the diff."""
    meta = p.get("metadata") or {}
    if p.get("permission") == "edit" and meta.get("filepath"):
        lines = diff_lines(meta.get("diff"))
        added = sum(line.startswith("+") for line in lines)
        return f"Changed {meta['filepath']} ({added} line{'s' if added != 1 else ''} added, {len(lines) - added} removed)"
    return asked_for(p, messages)


# How a row names what is asked for, after "Allow": plain words for opencode's tools
ASKS = {"bash": "this command", "edit": "a change to", "external_directory": "access outside your home folder",
        "webfetch": "reading a web page", "websearch": "a web search", "codesearch": "a code search",
        "read": "reading", "grep": "searching files", "glob": "looking for files", "list": "looking at a folder"}


def asks(p):
    """How a row names what is asked for: plain words for opencode's tools, a service's
    tool as the service and what it does ("tracker: update issue"), else the agent's word."""
    kind = p.get("permission", "this")
    if kind in ASKS:
        return ASKS[kind]
    if is_service_tool(p):
        service, tool = kind.split("_", 1)
        return f"{service}: {tool.replace('_', ' ')}"
    return kind


def always_offered(p):
    """True when Always may be offered: only for commands that look and nothing else
    (LOOK_ONLY), since opencode's rule for it lets any arguments through, in every
    conversation, and for opencode's tools that only look (ALWAYS_KINDS). A change to a
    file, a service's tool and any other command offer Allow once and Deny.
    opencode says what its rule would be (`always`); Hermes does not, so there the
    command itself decides, each of its parts, cut where a shell would cut it."""
    rules = p.get("always")
    if rules is None:
        # Hermes names no rule. Its Always is kept for good, for every command of the
        # kind that asked ("sudo with privilege flag": every sudo).
        command = " ".join(p.get("patterns") or [])
        return all(looks_only(part) for part in re.split(r"&&|\|\||[;|\n]", command))
    if p.get("permission") == "bash":
        return all(looks_only(rule) for rule in rules)
    return p.get("permission") in ALWAYS_KINDS


def rule_words(p):
    """What Always would let through, in words: "ls …, df …" for opencode's rules
    `ls *` and `df *`."""
    rules = p.get("always")
    if rules is None:
        return "commands like this"
    if rules == ["*"]:
        return asks(p) + ", whatever it is given"
    return ", ".join(r.removesuffix("*").rstrip() + " …" if r.endswith("*") else r for r in rules)


def step_name(command):
    """A command in a word or two: its program, and the `sudo` before it ("sudo apt-get")."""
    words = PARTS.split(QUIET.sub(" ", command))[0].split()
    name, _args, root = program_of(words)
    first = words[0].rsplit("/", 1)[-1] if words else "command"
    return (f"{first} " if root and first in ROOT else "") + (name or first)


def done_so_far(messages, most=6):
    """The steps the agent took for this request, each in a word: a command's program,
    else the tool."""
    names = []
    for part in reply_parts(messages):
        if part.get("type") != "tool" or composing(part):
            continue
        command = ((part.get("state") or {}).get("input") or {}).get("command")
        names.append(step_name(command) if isinstance(command, str) and command.split() else str(part.get("tool", "step")))
    if not names:
        return "No steps yet"
    return "Done so far: " + ("… · " if len(names) > most else "") + " · ".join(names[-most:])


def view(state, elapsed=None, agent="", stop=False, handover=False, rest=False, ran=(), cut=True):
    """The matches of the conversation (`kortex ›`), as a chat shows it: what the agent
    asks to do, in its own words, and what it asks the user; while it works, a status
    row (how long it has been at it, `elapsed` seconds or None, and what it thinks or
    does), its last words, its steps so far in a word each and the way to the panel;
    then its answer. Every row but a question, a step that asks with its answers and
    the answer is one line, which KRunner gives a fixed height, so its window holds
    still while the agent works.

    A step that asks is a row that says what it would do (`risk`), the agent's words
    and the step itself, and its answers are rows of their own, to read and to pick
    with Enter, with what they cover in their own text (KRunner shows a subtext only on
    the selected row, cut): Allow this once, then Always (where it may be offered) or,
    with `handover`, Allow the rest of this task (not for a held step), then Don't.
    `rest`: the user allowed the rest of this request; `ran`: what ran on that yes.
    `stop`: the agent's work can be stopped, so the status row offers it. `cut`: an
    answer that reads as cut off read so twice; opencode may say idle a moment before
    it stamps the message complete, and until then it shows as working."""
    said, steps, error = reply_of(state["messages"])
    matches = []
    first = True
    for i, p in enumerate(state["permissions"]):
        if p["id"] == UNREADABLE["id"]:
            matches.append(match("stuck", "The agent waits for an answer kortex cannot show (an opencode bug)",
                                 "dialog-warning", 1.0, "Clears when opencode restarts"))
            continue
        label, held = risk(p)
        at = 1.0 - i * 0.05
        text = f"<b>{html.escape(label, quote=False)}</b><br>" + asking_html(p, state["messages"])
        # all of it, never elided; Enter on it answers nothing, its answers are the rows below
        matches.append(match("permission" + SEP + p["id"], text, "dialog-warning", at, "Asks first", (), True,
                             styled=True))
        if not first:  # one question at a time has its answers as rows; the next has them once this is answered
            continue
        first = False
        matches.append(match("allow" + SEP + p["id"], ALLOW_ONCE, "dialog-ok-apply", at - 0.01, "Enter"))
        if always_offered(p) and (p.get("always") or p.get("always") is None) and (not held or p.get("always") is None):
            # opencode keeps the rule for every conversation, until its server stops:
            # `ls *` lets any ls through
            matches.append(match("always" + SEP + p["id"], f"Always allow: {rule_words(p)}\nIn every conversation, "
                                 "until the agent restarts", "media-playlist-repeat", at - 0.02, "Enter", (), True))
        elif handover and not held:
            matches.append(match("rest" + SEP + p["id"], f"{ALLOW_REST}\n{STILL_ASKS}", "dialog-ok-apply", at - 0.02,
                                 "Enter", (), True))
        elif handover:
            matches.append(match("info" + SEP + "held", f"{label}: asks every time, also with {ALLOW_REST}",
                                 "dialog-information", at - 0.02, "", (), True))
        matches.append(match("deny" + SEP + p["id"], DENY, "dialog-cancel", at - 0.03, "Enter"))
    questions = state.get("questions") or []
    if questions:
        q = questions[0]
        text = f"{NAME} asks: {q['question']}"
        if not q["options"]:
            text += "\nType your answer after " + f"{NAME} {VIEW_MARK}" + " and press Enter"
        matches.append(match("question" + SEP + q["id"], text, "dialog-question", 0.79,
                             "Pick an answer below, or type one", (), True))
        about = q.get("about") or []
        for i, label in enumerate(q["options"]):
            told = f"{label} · {about[i]}" if i < len(about) and about[i] else label
            matches.append(match(SEP.join(("option", q["id"], str(q["index"]), label)), told, "dialog-ok-apply",
                                 0.78 - i * 0.001, "Enter answers with this"))
    status, retry = state["status"]
    cutting = cut_off(state["messages"])
    if cutting and not cut and status == "idle":
        status = "busy"  # read as cut off once: it may be stamped complete a moment later
    working = status != "idle"
    actions = ["terminal"] + (["stop"] if stop else [])
    if error and error != "Aborted":
        stopped = why_stopped(state["messages"], error)
        matches.append(match("answer", stopped, "dialog-error", 0.7, "The agent stopped", ["terminal"], True))
        if needs_login(stopped):
            matches.append(match("login", LOGIN_ROW[0], "dialog-password", 0.69, LOGIN_ROW[1]))
    elif working and (state["permissions"] or questions):
        # holds still while a row asks, to be clicked
        matches.append(match("answer", "Waiting for your answer", ICON, 0.7, agent, actions))
    elif working:
        took = f"{len(steps)} step{'s' if len(steps) > 1 else ''} so far" if steps else ""
        if status == "retry":
            line = "Retrying" + (f" · {seconds(elapsed)}" if elapsed is not None else "")
            line += f" · {retry_words(retry)}" if retry else ""
        else:
            thinking, words = latest_of(state["messages"])
            line = ("Thinking" if thinking else "Working") + (f" · {seconds(elapsed)}" if elapsed is not None else "")
            # its thoughts, or what it does; its words are the row below
            words = head(words) if thinking and words else doing(state["messages"]) or took
            if words:
                line += " · " + words
        matches.append(match("answer", line, ICON, 0.7, agent, actions))
        if status != "retry" or said or steps:  # a provider not reached: no rows that say nothing came
            latest = last_sentence(said[-1]) if said else ""
            matches.append(match("info" + SEP + "said", latest or "Nothing said yet", "dialog-messages", 0.69,
                                 "The agent's words"))
            matches.append(match("info" + SEP + "done", done_so_far(state["messages"]), "view-list-details", 0.68,
                                 "Its steps, each in a word"))
        if rest:
            n = len(ran)
            matches.append(match("ask-again", f"{ALLOW_REST}: on" + (f" · {n} step{'s' if n > 1 else ''} ran on it"
                                                                     if n else ""),
                                 "dialog-ok-apply", 0.67, "Enter takes it back: the next change asks again"))
        elif handover:
            matches.append(match("info" + SEP + "asks", "Every change asks", "dialog-information", 0.67,
                                 f"A question offers {ALLOW_REST}"))
        matches.append(match("steps", "Show every step", "view-right-new", 0.66, "Opens the panel beside your windows"))
    elif (last_user(state["messages"]) >= 0 and not replied(state["messages"], "")
          and (elapsed is None or elapsed >= NOT_TAKEN_AFTER)):
        # stored and never answered, the agent idle: what stopped it was said while kortex
        # did not listen (NOT_TAKEN). A conversation opened later showed its request alone,
        # or nothing. Enter asks the same again.
        request = asked_last(state["messages"])
        matches.append(match(("continue" + SEP + request) if request else "answer", NOT_TAKEN, "dialog-warning", 0.7,
                             (agent + " · " if agent else "") + ("Enter asks again" if request else "Ask again"), (),
                             True))
    elif not said and not steps and replied(state["messages"], "") and not cutting and not error:
        # it thought, and stopped: Enter asks the same again
        request = asked_last(state["messages"])
        thought = last_thought(state["messages"])
        matches.append(match(("continue" + SEP + request) if request else "answer",
                             NO_ANSWER + (f"\n{thought}" if thought else ""), "dialog-warning", 0.7,
                             (agent + " · " if agent else "") + ("Enter asks again" if request else "Ask again"), (),
                             bool(thought)))
    elif said or steps or cutting or error:
        took = f" · {len(steps)} step{'s' if len(steps) > 1 else ''}" if steps else ""
        said = "\n".join(answer_of(state["messages"]))
        shown = shorten(said)
        matches.append(match("answer", rich(shown), ICON, 0.7,
                             (agent + " · " if agent else "") + "Enter copies" + took, ["copy", "terminal"], True,
                             styled=True))
        if ran:  # what no row asked for: said, never left to the agent's own account of it
            matches.append(match("info" + SEP + "ran", f"Ran on {ALLOW_REST}, without a question of its own:\n"
                                 + "\n".join(shorten_end(" ".join(r.split()), 110) for r in ran),
                                 "dialog-information", 0.69, "", (), True))
        request = asked_last(state["messages"])
        if cutting and request:  # Enter asks the same again
            matches.append(match("continue" + SEP + request, "Ask again", ICON, 0.685, "Enter asks it again"))
        if shown != said:  # a row of its own: the row's icon for the panel says nothing of the rest
            matches.append(match("more", "Show the whole answer", "view-right-new", 0.68,
                                 "Enter opens the conversation in the panel beside your windows"))
        elif steps:
            matches.append(match("steps", "Show every step", "view-right-new", 0.66, "Opens the panel beside your windows"))
    return matches


def asked_last(messages):
    """The user's last request in the conversation, as they wrote it."""
    i = last_user(messages)
    if i < 0:
        return ""
    return " ".join(p.get("text", "") for p in messages[i].get("parts", [])
                    if p.get("type") == "text" and not p.get("synthetic")).strip()


def ending(messages):
    """How the agent's reply to the last request ended, for a notification: its summary
    and body. Its last words, not the steps it took: after a task they say how it went,
    and a plain answer is its last words anyway. Cut at a word's end."""
    said, _steps, error = reply_of(messages)
    if error and error != "Aborted":
        return f"{NAME} stopped", why_stopped(messages, error)
    answer = answer_of(messages)
    if answer[0] == CUT_OFF:
        return f"{NAME} stopped", CUT_OFF
    end = answer[-1]
    if end == NO_ANSWER:
        return f"{NAME} stopped", NO_ANSWER + " Show opens the conversation, to ask again."
    if end == DENIED:
        return f"{NAME} answered", DENIED
    if end in (UNFINISHED, STOPPED):
        before = answer[-2] if len(answer) > 1 else ""
        body = (head(before, MAX_NOTE - len(end) - 1) + "\n" if before else "") + end
        return (f"{NAME} stopped" if end == STOPPED else f"{NAME} answered"), body
    return f"{NAME} answered", head(said[-1] if said else end, MAX_NOTE)


def ago(ms):
    minutes = int((time.time() - ms / 1000) // 60)
    if minutes < 1:
        return "just now"
    if minutes < 60:
        return f"{minutes} min ago"
    if minutes < 48 * 60:
        return f"{minutes // 60} h ago"
    return f"{minutes // (24 * 60)} days ago"


def terminal_command(attach, terminal):
    """The terminal command that runs `attach`, the agent's own interface on a session.
    kitty's window takes kortex's app id, which names kortex's hidden application entry
    (kortex-panel.desktop), so the taskbar shows the panel as kortex, with its icon, and
    not among the terminal's other windows; another terminal keeps its own."""
    words = shlex.split(terminal) or ["konsole"]
    if Path(words[0]).name.startswith("kitty"):
        return words + ["--class", SERVICE] + attach  # kitty takes the program as it is
    return words + ["-e"] + attach


# ---- the runner ------------------------------------------------------------------------


def at_once(work, then):
    """Run `work` and hand `then` its result and error: how the tests run kortex's
    requests to the agent; main() runs them in a thread of their own."""
    try:
        result = work()
    except AgentError as e:
        then(None, e)
    else:
        then(result, None)


class Kortex:
    """What KRunner's calls do, without D-Bus: the D-Bus service in main() forwards to
    it, and the tests drive it directly. `agent` is the agent's server (Opencode or
    Hermes); `desktop` reopens KRunner with a query, copies, starts programs and
    notifies; `now` is a clock; `background(work, then)` runs requests to the agent.

    KRunner waits for nothing here: every request to the agent goes to `background`,
    and a match shows what the agent said last. A request still on its way, or an agent
    that starts (Hermes can take a minute), shows as a waiting row, and the tick shows
    KRunner again when there is news. Only `background`'s jobs call the agent, and only
    they set `session` while a request is sent, in the order they were made.

    The request the user made last is followed until the agent has replied (`asked_at`,
    `request`): the palette says how long it has been at it, and while KRunner is closed
    one notification says that the agent works on it, then what it answered."""

    def __init__(self, agent, desktop, now=time.monotonic, background=at_once, memory=None):
        self.agent = agent
        self.desktop = desktop
        self.now = now
        self.background = background
        self.session = None  # the conversation `kortex ›` shows
        self.state = None  # (session, what the agent said of it last)
        self.sessions = None  # the recent conversations, as the agent listed them last
        self.down = False  # the last request found no server, or none that answered
        self.unreachable = ""  # why the agent did not answer the last request
        self.waiting = ""  # a request on its way to the agent
        self.jobs = 0  # requests in the background
        self.fetching = set()  # what is being fetched, once at a time
        self.shown = None  # the query of kortex's matches KRunner shows, or None
        self.heard = 0  # KRunner's calls so far; a refresh decided before the latest is dropped
        self.view_query = self.opening
        self.closing_at = None  # when a run closed KRunner that `reopen` reopens
        self.signature = None  # what KRunner was shown, to compare with what is known now
        self.notified = set()  # the permission requests and questions told of in a notification
        self.answers = {}  # the answers picked so far to a request of several questions, by its id
        self.replied = set()  # the permission requests answered here, which a fetch begun before may bring back
        self.put_off = False  # told once that the provider puts the request off
        self.retrying_since = None  # when the agent began to try its provider again
        self.cut_fetches = 0  # the fetches in a row that read the reply as cut off
        self.logged = set()  # and in the journal
        self.problem = ""  # why the last request to the agent failed
        self.problem_was = ""  # and what did not happen: "Not sent", "Not answered" …
        self.problem_at = None  # and when
        self.last_activity = now()
        self.request = ""  # the request followed until the agent has replied to it
        self.asked_at = None  # when it was made; None once the agent has replied
        self.told = 0  # the notification that follows it, once there is one
        self.followed = False  # KRunner showed the news of it once (logged once)
        self.panel_of = None  # the panel open last: (its process id, the session it shows)
        self.shows = {}  # the notifications whose Show opens a conversation: its session, or None for the current
        # Requests followed in a conversation left for another (a new request, one picked
        # from the list) while the agent worked on them: (request, asked_at) by session,
        # until the agent has replied; a notification says so then.
        self.earlier = {}
        self.rest = None  # the conversation whose request the user allowed the rest of
        self.ran = []  # what ran on that yes, without a row of its own, for the answer to say
        self.asks = {}  # the notification that asks, by the step's request id or the question's (id, index)
        self.quiet_at = None  # when the agent's server said last that nothing of it works
        # The conversation shown last, kept across a restart of kortex: after one, `kortex ›`
        # showed nothing and the list's examples.
        self.memory = memory
        try:
            kept = memory.read_text().split() if memory else []
        except OSError:
            kept = []
        if len(kept) == 2 and kept[0] == agent.title:
            self.session = kept[1]

    @property
    def handover(self):
        """Allow the rest of this task is offered: the user turned it on, and the agent
        asks before every change. Hermes asks only before what it counts as dangerous,
        so each of its questions is answered by itself."""
        return self.agent.title != "Hermes" and handover_on()

    def remember(self):
        """Keep the conversation shown, for the next start of kortex."""
        if self.memory is None:
            return
        try:
            self.memory.parent.mkdir(parents=True, exist_ok=True)
            self.memory.write_text(f"{self.agent.title} {self.session}\n" if self.session else "")
        except OSError as e:
            log(f"the conversation is not kept for the next start: {e}")

    @property
    def marker(self):
        """The query that shows the current conversation: kortex's name and ›."""
        return f"{NAME} {VIEW_MARK}"

    @property
    def opening(self):
        """What kortex puts in KRunner's field to show the conversation: the marker and a
        space, so what is typed next is a follow-up as it stands. KRunner does not ask
        again for a space typed at the end, so a space of the user's there would be lost
        to the next refresh."""
        return self.marker + " "

    def parse(self, query):
        return parse(query, self.agent.title)

    @property
    def viewing(self):
        """KRunner shows the conversation right now."""
        return self.shown is not None and self.parse(self.shown)[0] == "view"

    def elapsed(self):
        """Seconds since the request followed was made, while the agent is at it and asks
        for nothing (a waiting Allow row must hold still to be clicked), else None."""
        state = self.current() or {}
        if self.asked_at is None or state.get("permissions") or state.get("questions"):
            return None
        return int(self.now() - self.asked_at)

    def work(self, job, then, key=None):
        """Run `job`, requests to the agent, in the background; `then(result, error)` gets
        what it brought. A job with a key runs once at a time."""
        if key in self.fetching:
            return
        if key:
            self.fetching.add(key)
        self.jobs += 1

        def done(result, error):
            self.jobs -= 1
            self.fetching.discard(key)
            if error:
                log(error)
            then(result, error)

        self.background(job, done)

    def failed(self, _result, error, what="Not sent"):
        """A request to the agent failed: the conversation shows why, with `what` did
        not happen. A request not sent stays until the next is; anything else (an
        answer to a row, Stop, the panel) until the conversation is fetched again."""
        if error:
            self.problem, self.problem_was, self.problem_at = str(error), what, self.now()

    def failing(self, what):
        """A `then` for work whose failure says `what` did not happen."""
        return lambda result, error: self.failed(result, error, what)

    def config(self, menu=False):
        # Each program that searches asks for this once. Both send every query, so any
        # search can go to the agent; KRunner from one letter, so kortex hears the first
        # one typed over the conversation and no refresh puts the conversation back.
        return {"MinLetterCount": MIN_SEARCH if menu else 1}

    def match(self, query, menu=False):
        kind, text = self.parse(query)
        if not menu:
            self.heard += 1
        if kind is None:
            if not menu:
                self.shown = None  # another search: nothing of kortex's to keep current
            return self.anything(query)
        self.last_activity = self.now()
        if menu:
            if kind == "ask":
                return self.ask_matches(text)
            return self.list_matches(menu) if kind == "list" else self.menu_view_matches(text)
        if JOINER in query and not self.followed and self.asked_at is not None:
            self.followed = True
            log("KRunner asked again: the palette follows the conversation")
        self.shown = query.strip() if kind != "ask" else None
        if kind == "ask":
            matches = self.ask_matches(text)
        elif kind == "list":
            matches = self.list_matches()
        else:
            self.view_query = self.shown if text else self.opening
            matches = self.view_matches(text)
        self.signature = self.seen()
        return matches

    def ask_matches(self, text):
        if not text:
            return [self.hint()]
        matches = [match("ask" + SEP + text, text, ICON, 1.0, f"Ask {NAME} · Enter")]
        if self.session:
            matches.append(match("continue" + SEP + text, text, ICON, 0.9, "This conversation"))
        return matches

    def list_matches(self, menu=False):
        def listed(found, error):
            self.unreachable = str(error) if error else ""
            if found is not None:
                self.sessions = found

        self.work(lambda: self.agent.sessions(5), listed, "sessions")
        if self.unreachable:
            return [self.unreachable_match()]
        matches = [self.hint()]
        if self.sessions is None and not menu:  # the start menu does not show it again
            matches.append(match("waiting", "Loading your conversations…", ICON, 0.9,
                                 "" if self.agent.url and not self.agent.stopped() else "The agent starts for it"))
        for i, s in enumerate(self.sessions or []):
            matches.append(match(
                "session" + SEP + s["id"], s.get("title") or "Conversation", "view-history", 0.8 - i * 0.1,
                "Continue · " + ago(s.get("time", {}).get("updated", 0))))
        if not menu:
            # nothing asked yet: what a request looks like. Not while the list is from
            # before this conversation, which the fetch just begun brings, nor before
            # the agent listed none.
            if self.sessions == [] and not self.session:
                for i, example in enumerate(EXAMPLES):
                    matches.append(match("try" + SEP + example, f"Try: {example}", ICON, 0.3 - i * 0.01,
                                         "Enter puts it in the field"))
            if self.agent.title != "Hermes":
                on = handover_on()
                matches.append(match("handover", f"{ALLOW_REST}: {'on' if on else 'off'}",
                                     "dialog-ok-apply" if on else "dialog-information", 0.2,
                                     "Enter turns it off: every change asks again" if on else
                                     "Enter turns it on: one yes covers the rest of a request · "
                                     + STILL_ASKS[:1].lower() + STILL_ASKS[1:]))
        return matches

    def hint(self):
        # The one place outside the conversation that names the agent behind kortex.
        return match("hint", f"Ask {NAME}: type your request", ICON, 1.0, f"{self.agent.title} answers",
                     ["instructions"])

    def waiting_match(self):
        """The row while a request is on its way, worded as the row that follows it once
        the agent has it, its seconds counted from the start; or while the conversation
        is fetched."""
        if self.asked_at is None or not self.waiting:
            return match("waiting", "Loading the conversation…", ICON, 0.9, "")
        starts = not self.agent.url or self.agent.stopped()
        return match("waiting", f"Working · {seconds(self.now() - self.asked_at)}", ICON, 0.9,
                     "The agent starts first" if starts else self.agent.title)

    def unreachable_match(self):
        return match("problem", "The agent is not reachable", "dialog-error", 1.0, self.unreachable)

    def anything(self, query):
        """Any search, offered to the agent ("Ask kortex: …" reads as something to
        press): below what Plasma finds, or first when it reads as a question or a
        request, with a conversation to continue then too."""
        text = query.replace(JOINER, "").strip()
        if len(text) < MIN_SEARCH:
            return []
        if not asking(text):
            return [match("ask" + SEP + text, f"Ask {NAME}: {text}", ICON, 0.1,
                          "Search, ask, fix", category=CATEGORY_LOWEST)]
        matches = [match("ask" + SEP + text, f"Ask {NAME}: {text}", ICON, 1.0,
                         "New conversation · Enter")]
        if self.session:
            matches.append(match("continue" + SEP + text, f"Continue with {NAME}: {text}", ICON, 0.9,
                                 "This conversation"))
        return matches

    def menu_view_matches(self, followup):
        """The conversation (`kortex ›`) in the start menu, which shows neither the
        answer nor its actions: the conversation opens in KRunner instead."""
        matches = []
        if followup:
            matches.append(match("continue" + SEP + followup, followup, ICON, 1.0, "Continue the conversation · Enter"))
        if self.session:
            matches.append(match("show", "The current conversation", ICON, 0.9, "Opens in the command palette · Enter"))
        return matches or [self.hint()]

    def view_matches(self, followup):
        matches = []
        asked = (self.current() or {}).get("questions") or []
        if followup and asked:
            # What is typed while the agent asks is the answer to it, not a new request.
            q = asked[0]
            matches.append(match(SEP.join(("option", q["id"], str(q["index"]), followup)), followup, ICON, 1.0,
                                 "Your answer · Enter"))
        elif followup:
            waits = next((p for p in (self.current() or {}).get("permissions", []) if p["id"] != UNREADABLE["id"]), None)
            if followup.lower() == "go" and waits and self.handover and not risk(waits)[1]:
                # the word for it, for hands on the keyboard
                matches.append(match("rest" + SEP + waits["id"], f"{ALLOW_REST}\n{STILL_ASKS}", "dialog-ok-apply", 1.0,
                                     "Enter", (), True))
            elif followup.lower() == "ask" and self.rest == self.session:
                matches.append(match("ask-again", f"Take back {ALLOW_REST}", "dialog-information", 1.0,
                                     "Enter: the next change asks again"))
            matches.append(match("continue" + SEP + followup, followup, ICON, 0.999, "Continue · Enter"))
        if self.problem:
            again = self.request if self.problem_was == "Not sent" else ""  # Enter sends it again
            matches.append(match("problem" + (SEP + again if again else ""), self.problem, "dialog-error", 0.95,
                                 self.problem_was + (" · Enter asks again" if again else ""), (), True))
            if needs_login(self.problem):
                matches.append(match("login", LOGIN_ROW[0], "dialog-password", 0.94, LOGIN_ROW[1]))
        if self.waiting:
            return matches + [self.waiting_match()]
        if not self.session:
            return matches or [self.hint()]
        self.refresh()
        if self.unreachable:
            return matches + [self.unreachable_match()]
        state = self.current()
        if not state:
            return matches + [self.waiting_match()]
        return matches + view(state, self.elapsed(), self.agent.title, hasattr(self.agent, "abort"),
                              self.handover, self.rest == self.session, self.ran, self.cut_fetches >= 2)

    def current(self):
        """What the agent said last of the conversation `kortex ›` shows, or None."""
        if self.state and self.state[0] == self.session:
            return self.state[1]
        return None

    def seen(self):
        """What KRunner's matches are made of, to compare with what it was shown."""
        if self.shown is None:
            return None
        if self.parse(self.shown)[0] == "list":
            return repr((self.sessions, self.unreachable))
        return repr((self.session, self.current(), self.problem, self.waiting, self.unreachable, self.elapsed(),
                     self.rest, len(self.ran), self.handover))

    def refresh(self, start=True):
        """Ask the agent for the conversation in the background; the tick shows what
        changed. A stopped server starts again for KRunner's request (`start`), not for
        the tick's."""
        session = self.session
        if not session:
            return

        def fetch():
            if not start and (not self.agent.url or self.agent.stopped()):
                return None
            self.agent.add_services()
            return {
                "messages": self.agent.messages(session),
                "status": self.agent.status(session),
                "permissions": self.agent.permissions(session),
                "questions": self.unanswered(self.agent.questions(session)),
            }

        def fetched(state, error):
            if error is not None and http_status(error) == 404 and session == self.session and not self.waiting:
                # a conversation the agent no longer has (kept from before a restart)
                log("the agent has no such conversation; showing none")
                self.session, self.state, self.unreachable = None, None, ""
                self.remember()
                return
            self.down = error is not None or state is None
            self.unreachable = str(error) if error else ""
            if state is not None:
                self.cut_fetches = self.cut_fetches + 1 if cut_off(state["messages"]) else 0
                # A fetch that began before an Allow brings the request back once more: it
                # was answered, and would show again, or be told of in a notification.
                state["permissions"] = [p for p in state["permissions"] if p["id"] not in self.replied]
                self.state = (session, state)
                if self.rest == session:
                    self.run_rest(state)
                if (self.problem and self.problem_was not in ("Not sent", "No answer")
                        and self.now() - self.problem_at >= SHOW_PROBLEM):
                    # the agent answers again: an answer or a Stop may go again
                    self.problem = self.problem_was = ""
            elif self.asked_at is not None and not self.waiting and self.agent.stopped():
                # The agent's server exited while it worked on the request: nothing more
                # comes, and "working" would stand until kortex stops.
                self.state = None
                self.unreachable = f"{self.agent.title} stopped while it worked; ask again"
                self.finish(f"{NAME} stopped", self.unreachable)

        self.work(fetch, fetched, "state")

    def run_rest(self, state):
        """The user allowed the rest of this request: a step that asks and is not held
        (`risk`) is allowed once, without a row; the answer says what ran so."""
        for p in list(state["permissions"]):
            if p["id"] == UNREADABLE["id"] or risk(p)[1]:
                continue  # it asks, as ever
            what = ran_line(p, state["messages"])
            log(f"allowed on the user's yes for the rest: {' '.join(what.split())[:120]}")
            self.ran.append(what)
            self.reply(state, p["id"], "allow-once")

    def reply(self, state, request, action):
        """Answer a step that asks, and take it from what is shown and told of."""
        self.replied.add(request)
        if state:  # answered: KRunner shows it again without it
            state["permissions"] = [p for p in state["permissions"] if p["id"] != request]

        def answered(_result, error):
            if error:  # not answered: the row comes back with the next fetch, to answer again
                self.replied.discard(request)
            self.failed(None, error, "Not answered")

        self.work(lambda: self.agent.reply(request, REPLIES[action]), answered)
        if request in self.asks:  # the notification that asked for it has its answer
            self.desktop.dismiss(self.asks.pop(request))

    def answer_step(self, request, action):
        """The user's answer to a step that asks: allow-once, allow-always (only where
        it is offered), deny, or rest: this once and, of this request, every later step
        that is not held."""
        state = self.current()
        asked = next((p for p in (state or {}).get("permissions", []) if p["id"] == request), None)
        if action == "allow-always" and asked and not always_offered(asked):
            return  # not offered for this row; nothing is answered
        if action == "rest":
            if not asked or not self.handover or risk(asked)[1]:
                return  # not offered for this row either
            self.rest = self.session
            log("the user allowed the rest of this request")
            action = "allow-once"
        elif action == "deny":
            self.rest = None  # a no takes the rest back as well
        if action in REPLIES:
            self.reply(state, request, action)

    def pick(self, request, index, label):
        """The user's answer to a question of the agent's: sent once every question of
        its request has one; the notification that asked it goes."""
        state = self.current()
        asked = [q for q in (state or {}).get("questions", []) if q["id"] == request]
        if asked:
            picked = self.answers.setdefault(request, {})
            picked[index] = [label]
            state["questions"] = self.unanswered(state["questions"])
            if len(picked) >= asked[0]["of"]:  # every question of the request has its answer
                answers = [picked[i] for i in sorted(picked)]
                del self.answers[request]
                self.work(lambda: self.agent.answer(request, answers), self.failing("Not answered"))
        if (request, index) in self.asks:
            self.desktop.dismiss(self.asks.pop((request, index)))

    def unanswered(self, questions):
        """The questions still to answer: without those of a request of several whose
        answer was picked already and waits for the others."""
        return [q for q in questions if q["index"] not in self.answers.get(q["id"], {})]

    def run(self, match_id, action, menu=False):
        kind, _, arg = match_id.partition(SEP)
        self.last_activity = self.now()
        if kind in ("ask", "continue"):
            if kind == "ask" and self.asked_at is not None and self.session and not self.waiting:
                # a new conversation: the request of this one is followed on in the background
                self.earlier[self.session] = (self.request, self.asked_at)
            self.problem = ""
            self.waiting = arg
            self.rest, self.ran = None, []  # a yes for the rest was for the request before
            self.request, self.asked_at, self.told, self.followed = arg, self.now(), 0, False
            self.put_off, self.retrying_since = False, None
            log(f"asking {self.agent.title}" + (" in the conversation" if kind == "continue" else ""))

            def send():
                # A model whose provider has no key: said now, and the request not sent
                # (opencode took it and said why only on its event stream).
                reason = self.agent.not_ready() if hasattr(self.agent, "not_ready") else None
                if reason:
                    raise NotReady(reason)
                if kind == "ask" or not self.session:
                    self.session = self.agent.new_session(head(arg, 60))
                self.agent.add_services()
                self.agent.ask(self.session, arg)

            def sent(_result, error):
                self.waiting = ""
                if not error:
                    # What is known of the conversation is from before this request: a
                    # follow-up that repeats the last request would read as replied to.
                    self.state = None
                    self.remember()
                self.failed(None, error)
                if error:
                    self.finish(f"{NAME} cannot answer yet" if isinstance(error, NotReady)
                                else f"{NAME} did not get the request", str(error))

            self.reopen(self.opening, menu)
            self.work(send, sent)
        elif kind == "session":
            self.switch(arg)
            self.reopen(self.opening, menu)
        elif kind == "show":
            self.reopen(self.opening, menu)
        elif kind in ANSWERS or kind == "permission":
            # a row that answers, or a caller that runs the asking row with an answer (kx, a test)
            self.answer_step(arg, ANSWERS.get(kind, action))
            self.reopen(self.opening if kind == "rest" else self.view_query, menu)
        elif kind == "ask-again":
            self.rest = None
            self.reopen(self.opening, menu)
        elif kind == "info":
            self.reopen(self.view_query, menu)  # it says something; Enter leaves the conversation open
        elif kind == "try":
            self.reopen(f"{TRIGGER} {arg}", menu)
        elif kind == "handover":
            set_handover(not handover_on())
            self.reopen(TRIGGER, menu)
        elif kind == "option":
            request, index, label = arg.split(SEP, 2)
            self.pick(request, int(index), label)
            self.reopen(self.opening, menu)
        elif kind == "question":
            self.reopen(self.view_query, menu)  # the question itself: its answers are the rows below
        elif kind in ("more", "steps"):
            self.panel()
        elif kind == "login":
            self.login()
        elif kind == "hint" and action == "instructions":
            self.instructions()
        elif kind in ("hint", "waiting"):
            # it says something; Enter leaves the palette on what it showed
            self.reopen(self.view_query if self.viewing else TRIGGER + " ", menu)
        elif kind == "problem":
            if arg:  # a request that was not sent goes again
                self.run(("continue" if self.session else "ask") + SEP + arg, "", menu)
            else:  # the agent was not reached: Enter asks it again
                self.unreachable = ""
                self.reopen(self.view_query if self.viewing else TRIGGER + " ", menu)
        elif kind == "answer" and action == "stop" and self.session:
            session = self.session
            self.work(lambda: self.agent.abort(session), self.failing("Not stopped"))
            self.reopen(self.view_query, menu)
        elif kind == "answer" and self.session:
            state = self.current()
            if action == "terminal":
                self.panel()
            elif state and state["status"][0] != "idle" and not reply_of(state["messages"])[2]:
                # the row that says how long: there is no answer to copy yet, and Enter
                # on it emptied the clipboard
                self.reopen(self.view_query, menu)
            else:
                session = self.session
                def read(messages, error):
                    if messages is not None:
                        said, steps, stopped = reply_of(messages)
                        self.desktop.copy("\n".join(said) or stopped or "\n".join(steps))
                    self.failed(None, error, "Not copied")

                self.work(lambda: self.agent.messages(session), read)

    def login(self):
        """Log in to the agent's provider, the row under an error that says it has no key:
        `patina ai login` in a terminal, which also picks another provider, else the
        agent's own login. Enter on the error then asks again."""
        self.last_activity = self.now()
        patina = self.desktop.patina()
        try:
            command = [patina, "ai", "login"] if patina else self.agent.login_command()
        except (AgentError, AttributeError) as e:
            self.failed(None, e, "Not opened")
            return
        self.desktop.launch(terminal_command(command, self.desktop.terminal()), {})

    def instructions(self):
        """Edit the agent's instructions, the hint row's action. With patina, `patina ai
        prompt` in a terminal: the user's own instructions in addition to patina's, handed
        to either agent, which follows them from the next request. Without it, the agent's
        own file of the user's instructions in the desktop's editor."""
        self.last_activity = self.now()
        patina = self.desktop.patina()
        if patina:
            self.desktop.launch(terminal_command([patina, "ai", "prompt"], self.desktop.terminal()), {})
        else:
            self.desktop.edit(self.agent.instructions_file())

    def panel(self):
        """Show what the agent does: its own interface on the conversation, in a terminal
        docked to the screen's right edge, so the user works on beside it; the agent's
        interface alone when there is no conversation (Meta+Shift+A)."""
        self.last_activity = self.now()
        session = self.session
        if self.panel_of and self.desktop.alive(self.panel_of[0]):
            if self.panel_of[1] == session:  # open on this conversation: docked and brought to the front
                self.desktop.dock(self.panel_of[0])
                return
            self.desktop.close(self.panel_of[0])  # on another: one panel, on this one

        def opened(found, error):
            if found:
                attach, env = found
                pid = self.desktop.launch(terminal_command(attach, self.desktop.terminal()), env)
                self.panel_of = (pid, session)
                self.desktop.dock(pid)
            self.failed(None, error, "The panel did not open")
            if error and self.shown is None:  # Meta+Shift+A with the palette closed: nothing else would say so
                self.notify(f"{NAME}: the panel did not open", str(error))

        self.work(lambda: self.agent.attach(session), opened, "panel")  # a second press while it starts adds none

    def reopen(self, query, menu=False):
        """After a run, which closes KRunner, or the start menu (`menu`): open KRunner
        on the conversation. Only a run in KRunner is followed by KRunner's teardown."""
        self.closing_at = None if menu else self.now()
        self.desktop.query(query)

    def teardown(self, menu=False):
        # The start menu's teardown ends its search, never the conversation, which it
        # does not show. KRunner's one right after a run in it is the closing that run
        # caused, which `reopen` undoes; any other means the user closed KRunner.
        if menu:
            return
        self.heard += 1  # KRunner closed, or its field was emptied
        if self.closing_at is not None and self.now() - self.closing_at < 1.5:
            self.closing_at = None
            return
        self.closing_at = None
        self.shown = None

    def open(self):
        """Meta+A (patina's `ask` key): KRunner on kortex alone, on the conversation while
        a request runs or waits for an answer, else on `ai` for a new request: KRunner
        calls no runner for an empty field, so an empty one showed nothing at all."""
        self.last_activity = self.now()
        state = self.current() or {}
        busy = self.asked_at is not None or self.waiting or state.get("permissions") or state.get("questions")
        # `ai`: the hint, what a request looks like and the recent conversations; what is
        # typed after it is a new request
        self.desktop.query(self.opening if busy and self.session else TRIGGER + " ")

    def show(self, note):
        """A notification's Show: KRunner opens on the conversation, or on the one the
        notification is of."""
        if note in self.shows:
            if self.shows[note]:
                self.switch(self.shows[note])
            self.desktop.query(self.opening)

    def act(self, note, action):
        """A notification's action: Show (or a click on it) opens the conversation; one
        that asks for a step also takes its answer, with KRunner left closed."""
        if action in ("allow-once", "rest", "deny"):
            request = next((r for r, n in self.asks.items() if n == note), None)
            if request:
                self.last_activity = self.now()
                self.answer_step(request, action)
            return
        if action.startswith("option-"):  # an answer to the agent's question
            key = next((r for r, n in self.asks.items() if n == note and isinstance(r, tuple)), None)
            q = next((q for q in (self.current() or {}).get("questions", []) if (q["id"], q["index"]) == key), None)
            i = int(action.removeprefix("option-")) if action.removeprefix("option-").isdigit() else -1
            if q and 0 <= i < len(q["options"]):
                self.last_activity = self.now()
                self.pick(q["id"], q["index"], q["options"][i])
            return
        self.show(note)

    def switch(self, session):
        """Show another conversation: the request followed in this one is followed on
        in the background (`earlier`), and one followed so in that one is followed here."""
        if session == self.session:
            return
        if self.asked_at is not None and self.session and not self.waiting:
            self.earlier[self.session] = (self.request, self.asked_at)
        self.session = session
        self.remember()
        self.rest, self.ran, self.cut_fetches = None, [], 0
        self.request, self.asked_at = self.earlier.pop(session, ("", None))
        self.told, self.followed, self.put_off = 0, False, False

    def notify(self, summary, body, replaces=0, transient=False, follows=False, session=None, asks=None, actions=(),
               styled=False):
        """A notification with Show, which opens the conversation (or `session`). Its
        body is markup to the notification server, as a multiline match is to KRunner,
        unless it is that already (`styled`). One that `follows` the request is the one
        the next replaces; one that `asks` for a step or a question has `actions` that
        answer it, and goes once it is answered."""
        def sent(note):
            self.shows[note] = session
            if follows:
                self.told = note
            if asks:
                self.asks[asks] = note

        self.desktop.notify(summary, body if styled else markup(body), replaces, transient, sent, list(actions))

    def finish(self, summary, body):
        """The request followed has ended: the agent replied, or it never got it. While
        KRunner is closed a notification says how, in place of the one that followed it."""
        if self.asked_at is None:
            return
        log(f"{summary} after {seconds(self.now() - self.asked_at)}")
        if self.shown is None:  # an answer's Markdown read, as in the palette
            self.notify(summary, rich(body or "Done."), max(self.told, 0), styled=True)
        self.asked_at = None
        self.rest = None  # the yes was for this request; `ran` stays for its answer to show

    def follow(self):
        """The request followed, while it runs: done once the agent has replied to it;
        while KRunner is closed, told of once after TELL_AFTER seconds, and of its end."""
        state = self.current()
        if self.asked_at is None or self.waiting or not state:
            return
        error = reply_of(state["messages"])[2]
        if replied(state["messages"], self.request) and (error or state["status"][0] == "idle"):
            summary, body = ending(state["messages"])
            if body == CUT_OFF and self.cut_fetches < 2:
                # opencode may say idle a moment before it stamps the message complete:
                # cut off only when the next fetch says so too
                return
            self.finish(summary, body)
            return
        status, why = state["status"]
        if (status == "idle" and not state["permissions"] and not state.get("questions")
                and self.now() - self.asked_at >= NOT_TAKEN_AFTER
                and asked_last(state["messages"]) == self.request.strip()
                and not any(m["info"].get("role") == "assistant" for m in state["messages"][last_user(state["messages"]):])):
            # The agent stored the request and went idle without a reply: nothing more comes.
            # The conversation says so as its answer (view); a notification while closed.
            self.finish(f"{NAME} stopped", NOT_TAKEN)
            return
        if status != "retry":
            self.retrying_since = None
        elif self.retrying_since is None:
            self.retrying_since = self.now()
        if (status == "retry" and self.shown is None and not self.put_off
                and ("next try" in why or self.now() - self.retrying_since >= TELL_RETRY)):
            # A limit that lasts, or a provider that cannot be reached: said once, in a
            # notification that stays in the history; "working on it" said nothing of it
            # for over a minute.
            self.put_off = True
            self.notify(f"{NAME} waits for the provider", f"{retry_words(why)}\n{self.request}\nIt tries again by "
                        "itself. Show opens the conversation, where Stop ends it.", max(self.told, 0), follows=True)
            return
        if self.shown is None and not self.told and self.now() - self.asked_at >= TELL_AFTER:
            log("KRunner closed while the agent works; a notification follows the request")
            self.told = -1  # asked for; its id comes with the notification server's answer
            self.notify(f"{NAME} is working on it", f"{self.request}\nA notification follows when it answers.",
                        transient=True, follows=True)

    def follow_earlier(self):
        """The requests of conversations left while the agent worked on them: a
        notification when one asks first and when the agent has replied, whose Show
        opens that conversation. One whose server stopped is dropped: nothing comes."""
        for session, (request, _at) in list(self.earlier.items()):
            def fetch(session=session):
                if not self.agent.url or self.agent.stopped():
                    return None
                return {"messages": self.agent.messages(session), "status": self.agent.status(session),
                        "permissions": self.agent.permissions(session)}

            def fetched(state, error, session=session, request=request):
                if error:
                    return  # asked again on the next tick
                if state is None:
                    self.earlier.pop(session, None)
                    return
                for p in state["permissions"]:
                    if p["id"] not in self.notified:
                        self.notified.add(p["id"])
                        self.notify(f"{NAME} asks first", markup(head(request, 60)) + "<br>"
                                    + asking_html(p, state["messages"]), session=session, styled=True)
                if (replied(state["messages"], request)
                        and (reply_of(state["messages"])[2] or state["status"][0] == "idle")):
                    self.earlier.pop(session, None)
                    summary, body = ending(state["messages"])
                    log(f"{summary} in an earlier conversation")
                    self.notify(summary, markup(head(request, 60)) + "<br>" + rich(body), session=session, styled=True)

            self.work(fetch, fetched, "earlier" + SEP + session)

    def tick(self):
        """Every REFRESH_MS: ask the agent for news; while KRunner shows kortex's matches
        and what they are made of changed, show them again; while it is closed, notify
        of a new permission request, and follow the request with a notification. With
        KRunner on something else and the agent done, there is nothing to ask it: the
        next look at the conversation fetches it."""
        self.follow_earlier()
        state = self.current() or {}
        if (self.shown is not None or self.asked_at is not None or self.waiting or not state
                or state["status"][0] != "idle" or state.get("permissions") or state.get("questions")):
            self.refresh(start=False)
        for p in (self.current() or {}).get("permissions", []):
            if p["id"] not in self.logged:
                self.logged.add(p["id"])
                log(f"{self.agent.title} asks first: {p.get('permission', 'a request')}")
        if self.shown is not None:
            kind, followup = self.parse(self.shown)
            # A follow-up being typed is the user's: a refresh would put back what KRunner
            # asked for last, without the keys typed since. Its rows refresh with each
            # key, and once it runs the field holds the bare marker again.
            if not followup and self.seen() != self.signature:
                self.signature = self.seen()
                # KRunner asks again only for a query that differs from the one it shows:
                # an invisible word joiner goes after `kortex ›` or `ai`, or away again,
                # with the space after them (`ai ›` and `AI ›` become `kortex ›`).
                head = self.marker if kind == "view" else TRIGGER
                q = head + ("" if JOINER in self.shown else JOINER) + " "
                heard = self.heard
                # Sent a moment later, and only if KRunner has called kortex since: the
                # user typed or emptied the field meanwhile.
                self.desktop.query(q, lambda: self.heard == heard)
            self.follow()
            return
        self.follow()
        state = self.current() or {}
        for p in state.get("permissions", []):
            if p["id"] not in self.notified:
                self.notified.add(p["id"])
                label, held = risk(p)
                self.notify(f"{NAME} asks first · {label}", asking_html(p, state.get("messages", ())), styled=True,
                            asks=p["id"], actions=[("allow-once", ALLOW_ONCE)]
                            + ([("rest", ALLOW_REST)] if self.handover and not held else []) + [("deny", DENY)])
        for q in state.get("questions", []):
            key = (q["id"], q["index"])
            if key not in self.notified:
                self.notified.add(key)
                # its answers as buttons, the first three; the others in the palette
                self.notify(f"{NAME} asks you", q["question"] + "".join(f"\n• {o}" for o in q["options"]), asks=key,
                            actions=[(f"option-{i}", o) for i, o in enumerate(q["options"][:3])])

    def idle(self):
        """True when kortex may stop: nothing asked for a while, nothing on its way to the
        agent, no panel open on it and nothing of the agent's at work, which its server
        is asked for: a task given in the panel was stopped mid-step after a quarter of
        an hour without a palette."""
        if self.now() - self.last_activity < IDLE_EXIT or self.jobs or self.earlier:
            return False
        if self.panel_of and self.desktop.alive(self.panel_of[0]):
            return False
        state = self.current()
        if not (self.down or not state or state["status"][0] == "idle"):
            return False
        if self.quiet_at is None or self.now() - self.quiet_at > 5:
            def heard(busy, _error):  # a server that does not answer is quiet
                if busy:
                    self.last_activity, self.quiet_at = self.now(), None
                else:
                    self.quiet_at = self.now()

            self.work(lambda: bool(self.agent.url) and not self.agent.stopped() and self.agent.busy(), heard, "busy")
        return self.quiet_at is not None and self.now() - self.quiet_at <= 5 and not self.jobs


# ---- D-Bus -----------------------------------------------------------------------------


def main():
    import dbus
    import dbus.service
    from dbus.mainloop.glib import DBusGMainLoop
    from gi.repository import GLib

    DBusGMainLoop(set_as_default=True)
    bus = dbus.SessionBus()
    loop = GLib.MainLoop()
    # When the session bus goes away (a session that ends without a signal for kortex),
    # libdbus would end kortex on the spot and leave the agent's server running.
    bus.set_exit_on_disconnect(False)
    bus.call_on_disconnection(lambda _bus: loop.quit())

    def ignore(*_):
        pass

    def logged(e):
        log(e)

    class Desktop:
        """The few things kortex asks of the desktop, over D-Bus where Plasma offers it."""

        def query(self, text, still=None):
            """Open KRunner on kortex with `text`, a moment later; a refresh passes
            `still`, which says whether it is still wanted then."""
            def call():
                if still is None or still():
                    bus.get_object("org.kde.krunner", "/App").querySingleRunner(
                        RUNNER_ID, text, dbus_interface="org.kde.krunner.App",
                        reply_handler=ignore, error_handler=logged)
                return False

            GLib.timeout_add(150, call)

        def copy(self, text):
            try:
                bus.get_object("org.kde.klipper", "/klipper").setClipboardContents(
                    text, dbus_interface="org.kde.klipper.klipper")
            except dbus.DBusException:
                if shutil.which("wl-copy"):
                    subprocess.run(["wl-copy"], input=text.encode(), check=False)

        def terminal(self):
            try:
                out = subprocess.run(
                    ["kreadconfig6", "--file", "kdeglobals", "--group", "General", "--key", "TerminalApplication"],
                    capture_output=True, text=True, check=False).stdout.strip()
            except OSError:
                out = ""
            if out:
                return out
            return next((t for t in ("konsole", "kitty", "x-terminal-emulator") if shutil.which(t)), "konsole")

        def __init__(self):
            self.started = {}  # the programs launched, by process id

        def patina(self):
            return find_patina()

        def edit(self, path):
            """Open a text file in the desktop's editor, empty when it was not there."""
            path.parent.mkdir(parents=True, exist_ok=True)
            path.touch()
            self.launch(["xdg-open", str(path)], {})

        def launch(self, command, env):
            process = subprocess.Popen(command, env=dict(os.environ, **env), start_new_session=True,
                                       stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                       stderr=subprocess.DEVNULL)
            self.started[process.pid] = process
            return process.pid

        def alive(self, pid):
            process = self.started.get(pid)
            return process is not None and process.poll() is None

        def close(self, pid):
            process = self.started.pop(pid, None)
            if process is not None:
                process.terminate()

        def dock(self, pid):
            """Place the window of process `pid` at the screen's right edge, full height, a
            fifth of the width (PANEL_*), above other windows: a KWin script, loaded over
            D-Bus for the moment the window takes to appear. Plasma lets no program place
            its own window on Wayland."""
            script = state_dir() / "panel.js"
            state_dir().mkdir(parents=True, exist_ok=True)
            script.write_text(PANEL_SCRIPT % (pid, PANEL_SHARE, PANEL_MIN, PANEL_MAX))
            try:
                kwin = bus.get_object("org.kde.KWin", "/Scripting")
                kwin.unloadScript("kortex-panel", dbus_interface="org.kde.kwin.Scripting")
                # loadScript is overloaded (path; path and name): the signature picks one
                number = kwin.loadScript(str(script), "kortex-panel", signature="ss",
                                         dbus_interface="org.kde.kwin.Scripting")
                bus.get_object("org.kde.KWin", f"/Scripting/Script{int(number)}").run(
                    dbus_interface="org.kde.kwin.Script")
            except (dbus.DBusException, TypeError) as e:  # no KWin, or another one
                log(f"the panel stays where the terminal put it: {e}")
                return

            def unload():
                try:
                    kwin.unloadScript("kortex-panel", dbus_interface="org.kde.kwin.Scripting")
                except dbus.DBusException:
                    pass
                return False

            GLib.timeout_add_seconds(20, unload)

        def dismiss(self, note):
            """Close a notification that asked for something answered since."""
            bus.get_object("org.freedesktop.Notifications", "/org/freedesktop/Notifications").CloseNotification(
                dbus.UInt32(note), dbus_interface="org.freedesktop.Notifications",
                reply_handler=ignore, error_handler=ignore)

        def notify(self, summary, body, replaces=0, transient=False, then=None, actions=()):
            # "default" is a click on the notification, "show" its button; both open the
            # conversation (ActionInvoked below); `actions` (id, label) come before Show
            # and answer what the notification asks. A transient one leaves no trace in
            # the history.
            hints = {"transient": dbus.Boolean(True)} if transient else {}
            buttons = [word for action in actions for word in action]
            bus.get_object("org.freedesktop.Notifications", "/org/freedesktop/Notifications").Notify(
                "kortex", dbus.UInt32(replaces), ICON, summary, body,
                dbus.Array(["default", "Show"] + buttons + ["show", "Show"], signature="s"),
                dbus.Dictionary(hints, signature="sv"), dbus.Int32(-1),
                dbus_interface="org.freedesktop.Notifications",
                reply_handler=lambda note: then and then(int(note)), error_handler=logged)

    class Worker:
        """The thread that makes every request to the agent, one after another, so the
        D-Bus loop answers KRunner at once however long the agent takes (Hermes' gateway
        can take a minute to start). What a request brings goes back to the loop."""

        def __init__(self):
            self.jobs = queue.SimpleQueue()
            threading.Thread(target=self.serve, daemon=True).start()

        def __call__(self, work, then):
            self.jobs.put((work, then))

        def serve(self):
            while True:
                work, then = self.jobs.get()
                try:
                    result, error = work(), None
                except AgentError as e:
                    result, error = None, e
                except Exception as e:  # noqa: BLE001 - the thread must outlive any job
                    log(f"a request failed: {e!r}")
                    result, error = None, AgentError(f"kortex failed on it ({type(e).__name__}); its log says more")
                GLib.idle_add(hand_back, then, result, error)

    def hand_back(then, result, error):
        try:
            then(result, error)
        except Exception as e:  # noqa: BLE001 - a callback that raises would be called again
            log(f"after a request: {e!r}")
        return False

    chosen = agent_name()
    agent = Hermes.from_environment() if chosen == "hermes" else Opencode.from_environment()
    kortex = Kortex(agent, Desktop(), background=Worker(), memory=state_dir() / "session")

    def to_dbus(m):
        match_id, text, icon, relevance, props = m
        properties = dbus.Dictionary({
            "subtext": dbus.String(props["subtext"]),
            "actions": dbus.Array(props["actions"], signature="s"),
            "multiline": dbus.Boolean(props["multiline"]),
        }, signature="sv")
        return dbus.Struct((match_id, text, icon, dbus.Int32(props["category"]), dbus.Double(relevance), properties),
                           signature="sssida{sv}")

    def from_menu(sender):
        """True when plasmashell calls, which runs the start menu (Kickoff, and Plasma's other
        launchers); KRunner and everything else get the command palette's matches."""
        try:
            return sender == bus.get_name_owner("org.kde.plasmashell")
        except dbus.DBusException:
            return False

    class Runner(dbus.service.Object):
        @dbus.service.method(IFACE, out_signature="a{sv}", sender_keyword="sender")
        def Config(self, sender=None):
            return dbus.Dictionary(kortex.config(from_menu(sender)), signature="sv")

        @dbus.service.method(IFACE, out_signature="a(sss)")
        def Actions(self):
            return ACTIONS

        @dbus.service.method(IFACE, in_signature="s", out_signature="a(sssida{sv})", sender_keyword="sender")
        def Match(self, query, sender=None):
            return dbus.Array([to_dbus(m) for m in kortex.match(str(query), from_menu(sender))],
                              signature="(sssida{sv})")

        @dbus.service.method(IFACE, in_signature="ss", sender_keyword="sender")
        def Run(self, match_id, action_id, sender=None):
            kortex.run(str(match_id), str(action_id), from_menu(sender))

        @dbus.service.method(IFACE, sender_keyword="sender")
        def Teardown(self, sender=None):
            kortex.teardown(from_menu(sender))

        @dbus.service.method(IFACE, in_signature="s")
        def SetActivationToken(self, token):
            os.environ["XDG_ACTIVATION_TOKEN"] = str(token)

        @dbus.service.method(SERVICE)
        def Open(self):
            # Not KRunner's: what Meta+A calls (patina's ask.desktop)
            kortex.open()

        @dbus.service.method(SERVICE)
        def Panel(self):
            # Not KRunner's: what Meta+Shift+A calls (patina's agent.desktop)
            kortex.panel()

    try:
        name = dbus.service.BusName(SERVICE, bus, do_not_queue=True)
    except dbus.exceptions.NameExistsException:
        log(f"{SERVICE} runs already")
        return 0
    Runner(name, OBJECT_PATH)

    def tick():
        try:
            kortex.tick()
        except Exception as e:  # noqa: BLE001 - a timer that raises is never called again
            log(f"tick: {e!r}")
        if kortex.idle():
            log("quiet for a while; stopping")
            loop.quit()
        elif agent_name() != chosen:  # `patina ai on hermes`, say: D-Bus starts a kortex for it
            log(f"the agent is {agent_name()} now; stopping")
            loop.quit()
        return True

    # A notification's action (Show, a click on it, an answer to what it asks): the
    # server tells every program; kortex takes those of its own notifications.
    bus.add_signal_receiver(
        lambda note, action: kortex.act(int(note), str(action)), signal_name="ActionInvoked",
        dbus_interface="org.freedesktop.Notifications", path="/org/freedesktop/Notifications")
    GLib.timeout_add(REFRESH_MS, tick)
    for sig in (signal.SIGTERM, signal.SIGINT):
        GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, sig, lambda: loop.quit() or False)
    log(f"{VERSION} ready on {SERVICE}")
    try:
        loop.run()
    finally:
        agent.stop()
    return 0


if __name__ == "__main__":
    if sys.argv[1:] in (["--version"], ["-V"]):
        print(f"kortex {VERSION}")
        sys.exit(0)
    sys.exit(main())
