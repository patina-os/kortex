# Setting up kortex without patina

patina sets up kortex, the agent and its model in one step. Without patina, on any
other Plasma 6 desktop (see [Will it run on my system?](../README.md#will-it-run-on-my-system)),
you do these steps once by hand. They take about ten minutes. This guide uses opencode
as the agent; for Hermes Agent, see [opencode or Hermes](usage.md#opencode-or-hermes).

1. [Python's D-Bus bindings](#1-pythons-d-bus-bindings)
2. [opencode](#2-opencode)
3. [A model](#3-a-model): a cloud provider's login, or your own OpenAI-compatible API
4. [Ask before every change](#4-ask-before-every-change)
5. [kortex itself](#5-kortex-itself)
6. [Try it](#6-try-it)

## 1. Python's D-Bus bindings

Install them from your distribution. pip will not do, because D-Bus starts kortex with
the system's `/usr/bin/python3`.

```sh
sudo apt install python3-dbus python3-gi                  # Debian, Ubuntu, KDE neon
sudo dnf install python3-dbus python3-gobject             # Fedora
sudo zypper install python3-dbus-python python3-gobject   # openSUSE
sudo pacman -S python-dbus python-gobject                 # Arch, Manjaro, CachyOS
```

## 2. opencode

```sh
curl -fsSL https://opencode.ai/install | bash
```

The installer puts opencode in `~/.opencode/bin`, where kortex finds it.
`npm install -g opencode-ai` works as well.

## 3. A model

opencode reads its settings from `~/.config/opencode/opencode.json`. Pick one of the two
ways below; both set `"model"`. Always set it: without it, opencode takes the first
model it knows, which may not be yours.

Whichever you pick, the model must support **tool calling**. Without it the agent cannot
run a command, and kortex has nothing to show you.

### A cloud provider

Log in to the provider; opencode keeps the key in its own file:

```sh
~/.opencode/bin/opencode auth login
```

Then name the model in `~/.config/opencode/opencode.json`, as `provider/model`:

```json
{
  "$schema": "https://opencode.ai/config.json",
  "model": "mistral/mistral-large-latest"
}
```

Other examples: `scaleway/mistral-medium-3.5-128b`, `anthropic/claude-sonnet-5-5`,
`openai/gpt-5.5`. `opencode models` lists the models of the providers you are logged
in to.

### Your own OpenAI-compatible API

Put the key in a file only you can read, and paste the key into it:

```sh
mkdir -p ~/.config/opencode && install -m 600 /dev/null ~/.config/opencode/custom.key
nano ~/.config/opencode/custom.key
```

See which models the API offers. Use your API's address, which usually ends in `/v1`:

```sh
curl -s -H "Authorization: Bearer $(cat ~/.config/opencode/custom.key)" \
  https://api.example.eu/v1/models | grep -o '"id": *"[^"]*"'
```

Then add the API to `~/.config/opencode/opencode.json`, with your address and your
model's name in place of `my-model` (twice):

```json
{
  "$schema": "https://opencode.ai/config.json",
  "provider": {
    "custom": {
      "npm": "@ai-sdk/openai-compatible",
      "name": "My own API",
      "options": {
        "baseURL": "https://api.example.eu/v1",
        "apiKey": "{file:~/.config/opencode/custom.key}"
      },
      "models": { "my-model": { "name": "my-model" } }
    }
  },
  "model": "custom/my-model"
}
```

## 4. Ask before every change

By default opencode runs commands and changes files **without asking**, so kortex never
shows a row to allow. patina writes rules that make it ask first; without patina, add
them to the same `~/.config/opencode/opencode.json`, beside `"model"`:

```json
  "permission": {
    "*": "ask",
    "glob": "allow", "list": "allow", "lsp": "allow",
    "todowrite": "allow", "todoread": "allow", "task": "allow", "question": "allow",
    "edit": "ask", "webfetch": "ask", "grep": "ask", "bash": "ask",
    "read": {
      "*": "allow",
      "*.env": "ask",
      "*.key": "deny", "*.token": "deny", "*.kdbx": "deny",
      "*/.ssh/id_*": "deny", "*/.gnupg/*": "deny",
      "*/opencode/auth.json": "deny"
    }
  }
```

The agent may then read and list files freely, but every command, file change, web
request and content search is a row you allow or deny. The `read` rules keep your keys
from the agent: whatever it reads goes to the model's provider. `*.key` covers the key
file of your own API.

These rules are a short form of patina's (`defaults/apps.yaml` in
[patina](https://github.com/patina-os/patina)), which also let through commands that
only look, such as `df` or `journalctl`.

## 5. kortex itself

```sh
git clone https://github.com/patina-os/kortex && cd kortex
./install.sh
```

It installs per user, under `~/.local/share`, and needs no root. Run `git pull` and
`./install.sh` again to update, and `./uninstall.sh` to remove it.

## 6. Try it

First opencode alone. If it answers here, the model is set up right:

```sh
~/.opencode/bin/opencode run "say hello in one sentence"
```

Then open KRunner (Meta+Space or Alt+Space), type a question and pick "Ask kortex".

After you change `opencode.json`, stop kortex once: it keeps the opencode server that
read the old file, and D-Bus starts it again at the next request.

```sh
pkill -f kortex/kortex.py
```

If it seems stuck, the journal shows each request's way:

```sh
journalctl --user -f | grep -i kortex
```

## Next

- [A key for the agent](usage.md#a-key-for-the-agent): Meta+A and Meta+Shift+A are
  patina's keys; bind your own to the palette and the panel.
- [Root through a dialog](principles.md): point `SUDO_ASKPASS` at a password dialog
  (`ksshaskpass`, for one), so the agent can run `sudo -A`.
- [Using kortex](usage.md): the conversation, the rows that ask and the panel.
