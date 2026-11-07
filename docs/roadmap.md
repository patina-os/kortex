# Roadmap

## First release, done when

- [ ] A request in KRunner on Debian 13's Plasma answers from a local model through
  opencode and Ollama, and from a cloud model with the same configuration switched.
  The first run on real hardware (0.3.4, a cloud model) stood still at "Thinking…":
  opencode keeps a part empty until it is done, which 0.4.1 fills in from its event
  stream. To try again with 0.4.1, with a cloud and with a local model.
- [x] A request that needs a change shows the Allow / Always / Deny rows, and nothing
  runs before one is chosen: the end-to-end tests, and patina's desktop workflow in a
  real Plasma 6.3 session.
- [x] KRunner follows the conversation by itself (0.4.0): patina's desktop workflow
  fails without kortex's "KRunner asked again".
- [ ] Open in terminal continues the session, on a real desktop.
- [ ] Installing and removing it per user leaves nothing behind but the password file.

## Choices still open

- **Streaming in KRunner.** KRunner follows each refresh, but whether reopening it on
  every change keeps the selection steady enough to read a growing answer, or whether
  kortex should refresh less often, is still to see on a real desktop, on Plasma 6.3
  (Debian 13) and the current Plasma.
- **A name of its own.** The conversation is called by the agent's name (`opencode ›`)
  from one place, the agent's `title`, so that an assistant named by the user can take
  it later.
- **Other agents.** opencode and Hermes are the two backends, each a small adapter
  behind the same calls: send, follow, answer a permission. A third (Goose, for
  example) would be another.

## How it reaches users

1. A release here, installed per user with `install.sh`, no root: what patina does
   today, from the tagged release (`manifest/upstream.yaml` in patina).
2. The KDE Store, where Plasma's "Get New Stuff" finds KRunner runners.
3. A Debian package, so that patina can install it from Debian's archive like the rest
   of what it installs.
