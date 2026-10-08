# wq#3711 — deploy: sync the live fleet-selfheal copy with wq#3702 (sleepers)

Worker: fworker-wq3711 (claude-fable-5), 2026-10-08. Ticket: deploy wq#3702's
`nodes.sleepers` change (repo commit `b3eb4e20`, `ops/wq3354-fleet-selfheal/`) to the
live copy at `/home/lumen/agents/fleet-selfheal/`, which `install.sh:53` deliberately
never overwrites for `selfheal.yaml`. Design: `design/wq3702_hathor_sleeper_judgment.md`.

## What the live copy actually looked like (deviation from the ticket premise)

The ticket said the live sweep "still has hathor in nodes.ignore". It did not. The live
`selfheal.yaml` nodes block (snapshot: `/tmp/fworker-wq3711/live_selfheal.yaml`, mtime
2026-10-08 07:16) had the same 7-node ignore list as the repo (thor, maksoor, apis, pbox,
seshat, be3600, 3dprinter) and **no hathor anywhere** — no ignore entry, no `notes:`
block (wq#3619's nephthys note was missing too), no sleepers. Other lanes hand-edit this
file (bak files from wq3625/wq3648/wq3655 the same day), and one of those edits had
evidently already dropped the wq#3696 hathor line. So the deploy was purely additive;
there was nothing to remove.

Code files: only `sh_nodes.py` differed from the repo (live md5 `f1cd9eb…` vs repo
`e3ab4c4…`); the other 9 CODE files already matched, so no `install.sh` run (and no
/etc unit churn) was needed.

## What was done (all via `lumen-run` detached units — the live dir is outside a BUILD
worktree's allowed dirs, which is why this ticket exists)

1. **Backup**: live yaml copied to `selfheal.yaml.bak.<ts>_wq3711` in the live dir;
   old `sh_nodes.py` kept at `/tmp/fworker-wq3711/sh_nodes.py.pre-wq3711`.
2. **Code sync**: repo `sh_nodes.py` → live; post-copy md5 identical (`e3ab4c4a427c…`
   both sides, `/tmp/fworker-wq3711/phase_b_md5.txt`).
3. **Yaml sync**: inserted the repo's `sleepers: [hathor]` + `sleeper_tailnet_grace_s:
   300` + `notes:` block (hathor and the previously-missing nephthys note) after the
   ignore list. Verified the whole live nodes block is byte-identical to the repo's
   (36 lines) — the edit script fails loudly on any anchor or diff mismatch.
4. **Stale-state fix (found during verification)**: the first dry-run printed nothing
   for hathor. Root cause: hathor's `state.json` nodes entry was written by the OLD
   `sh_nodes.py`, which cached `node` but not `lan_ip`; the new code caches `lan_ip`
   only on first resolution (`sh_nodes.py:157-168`), so `sleeper_skip` abstained
   (`sh_nodes.py:109-111`) and the pre-existing `node-offline:hathor` tickets entry
   suppressed any output — a silent no-op, indistinguishable from working. Dropped the
   stale nodes entry (tickets dedup entry kept) so the new code rebuilt it through its
   own resolution path. Any node that was offline across a sleepers deploy would hit
   this; one-time transitional, self-healing on the node's next online/offline cycle.
5. **Acceptance dry-run** (as root, same as the real unit; hathor at the time: tailnet
   offline 2h, LAN ping to 192.168.8.172 unreachable — genuinely asleep):

   ```
   [nodes] would hathor dark on tailscale but powered off by design (no LAN answer on 192.168.8.172); no ticket
   dryrun_exit=0
   ```

   hathor asleep files nothing, and the skip is logged with the 'powered off by design'
   reason — the exact wq#3702 contract. The probe could fail and did: the same command
   before step 4 printed nothing, and flipping the one input the probe reads (LAN
   answer) flips the verdict per wq#3702's pinned-fixture evidence.

## Rollback

Restore `selfheal.yaml.bak.<ts>_wq3711` and `/tmp/fworker-wq3711/sh_nodes.py.pre-wq3711`
into `/home/lumen/agents/fleet-selfheal/`. No units were touched; timer stayed
enabled/active throughout.
