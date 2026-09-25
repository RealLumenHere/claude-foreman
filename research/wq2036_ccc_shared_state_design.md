# wq#2036 — CCC shared chat state: sync semantics decision

Author: orch-burn-4-cfa06f, 2026-09-24. Status: design answered, build BLOCKED on hardware.

## Verified state at time of writing

- `maksoor` is **unreachable**: tailscale reports `offline, last seen 12h ago`;
  `ping 100.81.204.74` = 100% packet loss; `ssh` = connection timed out.
- `thor` is up, CCC running (electron pid 5962), node v22.22.1.
- Thor's `~/.config/Claude Command Center/ccc-state.json` is 1587 bytes, shape
  `{"tabs": [7 items], "pageOffset": 0}` — matches the ticket's "Thor holds 7 tiles".
- Repo HEAD `370a83c`, source of truth `/home/lumen/projects/claude-command-center`.

Everything below is design that needed no hardware. The build needs maksoor online.

## The question the ticket asked

> sync ccc-state.json between Thor and maksoor (last-writer-wins is probably wrong —
> needs merge or leader election; decide and justify)

## Decision: neither. Per-tab OR-Set over per-host append-only op-logs.

### Why last-writer-wins is wrong

`ccc-state.json` is not a document, it is a **set of open tiles**. LWW is applied to
the whole file, so the machine that saves last obliterates the other's entire tile
set. This is not a rare race: `persist()` is called on essentially every interaction
(`src/main.js:255,340,352,385,403,473` — activate, close, reorder, relayout), and
`store.saveNow(snapshot)` fires synchronously on `win.on('close')` (`:742`) and
`app.on('before-quit')` (`:793`). So **shutting maksoor down with 2 tiles open would
wipe Thor's 7** — the worst possible moment for a clobber, and the one most likely to
happen daily.

### Why leader election is wrong

There is no correctness constraint that needs a single writer, and the failure mode is
bad: the follower's UI becomes read-only or laggy, and when the leader is off (Thor
sleeps) Frank cannot open a tile on maksoor at all. It fails closed on a machine that
is off most of the day. Over-engineered for two personal boxes.

### Recommended: OR-Set keyed on conversation id

A tab is open iff `max(addedAt) > max(closedAt)` across hosts.

- Closing a tile writes a **tombstone** `{id, closedAt, host}` rather than dropping the
  entry — otherwise a delete on one machine is indistinguishable from an add not yet
  seen on the other, and closed tabs resurrect forever.
- Ties break deterministically on host name.
- Tombstones GC after 30 days.

This gives the behaviour Frank actually asked for: close a chat on Thor, it closes on
maksoor; open one on maksoor, it appears on Thor. No clobber, no leader.

### Transport trick: never share a mutable file

Each host appends only to **its own** file — `ccc-ops.<host>.jsonl` — and reads all of
them. No two machines ever write the same file, so **any** file transport (syncthing,
rsync, git) is safe by construction and can never produce a sync conflict. Merging is a
pure function of the op-logs. `ccc-state.json` stays as the local materialised cache.

## Correction to the ticket's scope

The ticket says "sync ccc-state.json". Do **not** sync all of it:

- **`tabs` (which conversations are open)** — sync. This is the shared intent.
- **`pageOffset`, tile order, geometry** — **machine-local, never sync.** Thor is X11
  `:0` on DP-3 with `bar-fix.sh` wmctrl placement; maksoor is GNOME/Wayland on a
  different display. Syncing layout across different screens is actively wrong and
  would fight Thor's bar placement — which the ticket separately flags as a thing
  architecture A must not break. It applies to B too.

## Remaining build steps (need maksoor)

1. op-log read/merge in `src/persistence.js`, materialise into the existing snapshot.
2. reload-on-focus in `src/main.js`, guarded so it never interrupts a streaming
   response. The guard is the risky part and must be written against a live streaming
   tile — untestable while maksoor is down and not worth landing blind on Frank's
   primary chat app.
3. maksoor launcher: Thor's `start-ccc.sh` scrapes env from the live xfwm4 process;
   maksoor is GNOME/Wayland, so it needs a different env source, not a port.
4. Frank logs into claude.ai on maksoor himself (explicitly his task per the ticket —
   do not copy Thor's `persist:claude` partition).

## Why nothing was landed

Deploying to Thor means restarting Frank's live CCC (7 open tiles, active session, and
per the ticket the whole app must restart because inject CSS/JS is cached at app
start). Landing an untestable streaming guard onto his primary chat tool, to fix a
cross-machine feature whose other machine is powered off, is not a trade worth making.
