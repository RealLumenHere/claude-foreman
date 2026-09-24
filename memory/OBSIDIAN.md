# Obsidian vault as cross-session memory (wq#2442)

Frank spent weeks on Claude Projects per an earlier Claude recommendation.
Verdict, 2026-09-24: "it does NOT share context between chats, just a
folder, no cross-chat memory unless an agent explicitly writes to memory."
That recommendation was wrong and cost weeks. Frank paid for Obsidian
instead (year, in full). This is the real implementation: a vault an agent
writes to at attempt end, and a vault an agent can query back at the start
of a later one — the loop Projects never closed.

## Why direct files, not the Local REST API plugin

The documented way to script Obsidian is the community "Local REST API"
plugin: an HTTPS endpoint the desktop app exposes while it's open. Every
fleet write here happens on Lumen, headless, with no Obsidian app running
and nobody at a screen to keep one open — that plugin has no host to run
on. It's irrelevant to this design, not missing from it.

The alternative needs no plugin and no running app at all: **an Obsidian
vault is just a directory of markdown files** with YAML frontmatter.
Writing a `.md` file into the vault *is* writing a note; Obsidian only
needs to be pointed at the directory to render it, any time later, on any
device. That's what `obsidian_bridge.py` does.

## What's implemented

| piece | file |
|---|---|
| note/log rendering + write/read/search | `memory/obsidian_bridge.py` |
| the digest it consumes (unchanged) | `memory/episodic.py:digest_events()` |
| the call site, wired after the existing archival commit | `harness/fworker.py:commit_episode_to_archive()` |
| tests | `memory/tests/test_obsidian_bridge.py` |

Flow, one ticket:

```
attempt_end events (episode.jsonl)
        │  episodic.digest_events()          (unchanged, wq#1635)
        ▼
      digest  ──────────────┬──────────────────────────────┐
                             │                               │
                 lumen_episode.py commit          obsidian_bridge.write_note()
                 → archival_notes (minerva)        → <vault>/Fleet Memory/wq<id>.md
                                                    → <vault>/Fleet Memory/_Log.md (append)
```

Both writes are independently best-effort (`harness/fworker.py`
`commit_episode_to_archive`): a dead vault mount degrades to "no Obsidian
note," never an attempt failure, same fail-open contract as the rest of
`memory/` (see `memory/README.md`).

Recall side, for an agent that wants prior-session context in a prompt:

```python
import obsidian_bridge
obsidian_bridge.search_notes("dispatcher budget")   # [(path, lineno, line), ...]
obsidian_bridge.read_note(2428)                       # full text of one ticket's note, or None
```

No embeddings, no external service — grep over markdown, matching the
stdlib-only, no-network posture the rest of `memory/` already holds to
(`memory/cmt_retrieval.py`).

## Vault location and sync — what's decided vs. what needs Frank

`OBSIDIAN_VAULT_ROOT` (env, default `/home/lumen/obsidian-vault`) is the
only knob. Nothing in this module talks to Obsidian's own paid Sync
service or needs Frank's Obsidian account credentials on Lumen at all —
that protocol is proprietary to the desktop/mobile apps. The design
instead treats the vault as **a synced folder**: point `OBSIDIAN_VAULT_ROOT`
at a directory that reaches Frank's devices by whatever file-sync layer
the fleet already runs there (e.g. Syncthing), and Obsidian on his end
just opens that folder as a vault — his paid Obsidian Sync can then
additionally sync it between *his own* devices on top, unaffected by how
it got to the first one.

Two things are physical actions only Frank can do, filed as a follow-up
ticket rather than a gate on this one (autonomy instinct 3 — decide and
act, queue what only he can touch):

- create/confirm the real `/home/lumen/obsidian-vault` directory and its
  sync partner (Syncthing pairing or equivalent) to a device he reads
  Obsidian on — a physical device pairing, not something this session can
  do from inside its worktree sandbox;
- point his Obsidian app at that folder as a vault (or fold it into an
  existing one) — his account, his devices.

Once that directory exists and is reachable, no code changes are needed:
`OBSIDIAN_VAULT_ROOT` already defaults to it, and the write path in
`harness/fworker.py` is live now — every ticket that reaches the archival
commit starts writing a note the next time `commit_episode_to_archive` runs.
