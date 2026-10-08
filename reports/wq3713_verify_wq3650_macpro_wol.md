# wq#3713 — verify wq#3650 Mac Pro WoL wake-on-demand + ops bundle for isis/hathor

Verifier: claude-sonnet-5@high (fresh instance, build worker, worktree `wq3713-a1`). Author of
wq#3650: claude-sonnet-5 (same model). Archetype 8 adversarial verify-pass. Target commit
`3c561e93` ("feat(wq3650): Mac Pro WoL wake-on-demand + ops bundle for isis/hathor"), already on
this branch's history (18 files, +710/-18). PR URL in the ticket
(`http://100.121.17.5:3000/lumen/claude-foreman/pulls/1737`) 404s; the gitea root answers 200 and
an unauthenticated repo-search only returns `lumen/mailbot`/`lumen/questfree`, so this reads as an
auth-walled private repo, not a fabricated URL (the ticket supplied it, I didn't construct it —
instinct #8 is about values *I* introduce). Verified directly against the commit in the local repo
instead, which is the authoritative artifact either way.

## Verdict: CONFIRM

## What wq#3650 claimed

Seven ticket items for isis/hathor (Apple MacPro6,1): (1) WoL via ethtool on every boot, (2) GPU
lowest power state, (3) mbpfan, (4) cap Ollama to 12 physical threads, (5) disable turbo, (6) wire
a wake-judge into the dispatch pipeline, (7) auto-sleep at load<0.2 for 30 min. Items 2/3 were
already live from earlier tickets and correctly not re-shipped. Items 1/4/5/7 are a new ops bundle
(`ops/wq3650-macpro-wol/`). Item 6 is `dispatcher/wake_judge.py`/`.sh` plus a `wake.method: wol`
branch added to `dispatcher/node_power.py`'s existing `wake()`/`power_down()`. The PR's own
README explicitly states `wake.method` is left `plug-ac` for both nodes — the dispatcher code is
"real and tested, but not switched on yet" — because hathor is Frank-pinned `always-on` and isis's
`power_policy: warm` + `state: down` has an open abrupt-power-loss RCA that a follow-up ticket must
clear first. Only `wake.mac` was added to `research/inference_nodes.yaml` for both nodes.

## What I checked, independently of the claimed transcript

1. **Read the diff cold** via `git log`/`git show 3c561e93 --stat` and per-file `git diff
   b3eb4e20 3c561e93 -- <path>`, then `Read` on every changed/new file in full
   (`dispatcher/node_power.py`, `harness/node_ports.py`, `dispatcher/wake_judge.py`/`.sh`, all 10
   files under `ops/wq3650-macpro-wol/`, `research/INFERENCE_NODE_REGISTRY.md`,
   `research/inference_nodes.yaml`).

2. **Ran the bundle's own test suite** from the worktree:
   `python3 -m pytest dispatcher/tests/test_node_power.py dispatcher/tests/test_wake_judge.py` →
   **51 passed**, 0 failed, including the six wol-specific cases (`sends a magic packet instead of
   plug-on`, `no-mac is refused without sending anything`, `failure excludes the node a day`,
   `wol node drain skips plug-off and leaves AC on`, plus `wake_judge.py`'s own send/poll/dispatch,
   send-only, send-failure, ssh-timeout, non-wol-node-refused, mac-missing, unknown-node tests).

3. **Hand-traced the two wol branches against the registry schema** rather than trusting the test
   mocks alone: `NodePower.wake()` (node_power.py:399-406) and `power_down()`'s step 6
   (node_power.py:483-494) both read `(row.get("wake") or {}).get("method", "plug-ac")` and branch
   identically — `ports.wol(mac)` instead of `ports.plug(.., "on")` on wake; plug-off skipped
   entirely on drain, with the clean `systemctl poweroff` (step 4, unconditional) left as the whole
   sequence. `wake_judge.py`'s standalone `wake()` duplicates only the 3-step shape (send, poll
   ssh, kick one dispatch pass), not the deadline-cap math — correctly scoped as a human/cron entry
   point outside the 600s dispatch-pass budget, not the in-pass code path.

4. **Verified the MACs are real, not guessed** (instinct #8-style check, this time on the author's
   claims rather than mine): `research/INVENTORY_RECONCILIATION.md:117` (hathor) and `:127` (isis)
   — both live-probed 2026-09-10 — give `enp12s0 00:3e:e1:c9:07:48` (hathor) and
   `enp12s0 00:3e:e1:c8:27:97` (isis). Both match `research/inference_nodes.yaml`'s new
   `wake.mac` fields and the README's "MACs (verified, not guessed)" table exactly.

5. **Checked whether `wakeonlan` (the new `ports.wol()`'s only real-world dependency,
   `harness/node_ports.py:128-137`) actually exists on Lumen**: `which wakeonlan` → `/usr/bin/wakeonlan`,
   present. Not a claim made in the PR, but a silent dependency that would otherwise make every
   `method: wol` wake a guaranteed `wol_send_failed`.

6. **Read `ops/wq3650-macpro-wol/install.sh` and every script it installs**
   (`macpro-wol-enable.sh`, `macpro-no-turbo.sh`, `macpro-autosleep.sh` + `.timer`,
   `10-wq3650-threads.conf`) for self-consistency: the autosleep script's
   `NEED_STREAK=ceil(WINDOW_MIN/CHECK_INTERVAL_MIN)=6` matches the timer's `OnUnitActiveSec=5min`
   default exactly; the no-turbo script fails loudly (never silently no-ops) if neither
   `intel_pstate/no_turbo` nor `cpufreq/boost` exists; the wol-enable script's `--check` is
   read-only and distinct from the write path, matching the README's division of labor.

7. **Cross-read `research/INFERENCE_NODE_REGISTRY.md`'s updated schema doc against the actual
   YAML and code** — `wake{method, mac, measured_ready_s, last_measured}` field list, the new
   wake/drain prose (wol skips plug-ctl, AC stays on, EFI-WoL-survives-clean-shutdown-not-AC-loss
   rationale) — all consistent with what the code does, no doc/code drift found.

## Where I tried to break it

- **Looked for the obvious trap: is `wake.method: wol` actually live for any registry node?**
  Grepped `research/inference_nodes.yaml` for `method:` — all three entries (hathor, isis, plus
  the one other `plug-ac` row) are `plug-ac`; `grep -n "method: wol"` returns nothing. So the new
  branch is dormant on every real node today. This is not a hidden gap — the README has an entire
  section ("Why isis/hathor still wake via plug-ac today, not WoL") that volunteers this and gives
  three concrete, defensible reasons (hathor's always-on pin, isis's open power-loss RCA, and
  `measured_ready_s: null` meaning neither node can be cold-auto-woken regardless of method). The
  claim and the evidence agree.
- **Looked for a safety hole the dormancy might be hiding**: `interlock()` (node_power.py:138-160,
  unmodified by this diff) still gates the *entire* drain — including the wol-only clean-poweroff
  path — on `idle_watts` being measured and the plug reading within 30% of it, even though a wol
  node's drain never touches the plug. This means a future wol+cold isis/hathor could have its
  idle-power-down refused for a plug-wattage reason entirely unrelated to the action being gated.
  Judged **not a defect worth contradicting on**: it fails *safe* (refuses to act rather than acting
  wrongly), it only matters once a follow-up ticket flips `power_policy: cold` +
  `method: wol` — which the README already scopes as needing its own soak test — and both nodes
  currently have `idle_watts` measured (86.6 / 103.2), so it would not even block them as configured
  today. Worth a one-line note for whoever lands that follow-up, not a verify-pass blocker.
- **Checked the one failure mode unit tests can't reach: the live ops bundle on the real boxes.**
  Tried the ticket's own instructed probe,
  `bash /home/lumen/foreman/ops/wq3650-macpro-wol/install.sh --verify` → refused, "This command
  requires approval" (not on this worker's `Bash` allowlist — `harness/worker-settings.json` has no
  `Bash(bash *)` entry outside `Bash(bash scripts/*)`). Per protocol this is not retryable, so I did
  not retry it. Checked whether I could reproduce its individual `vremote_*` checks by hand instead
  (the wq#2619 verify precedent's approach) — found I can't get even that far: `install.sh --verify`
  itself calls `ssh` to isis/hathor directly, and this worker's `fleet-exec` grant (checked via
  `/home/lumen/bin/fleet-exec grant`) is scoped to `{"kind": "ssh", "node": "anubis", "verbs":
  ["run"]}` only — no grant, and no raw `Bash(ssh *)` allowlist entry either, for isis or hathor.
  This is not a contradiction of anything the PR claims: the README's own "Deploy" section says
  outright that "No lane available to this build worker has ssh+root to isis or hathor" and ships
  the bundle for a lane that does, rather than claiming it's already live. My access gap is the
  identical gap the author already disclosed, from the same worker class. `ops_verified
  (wq3650-macpro-wol)` therefore cannot be independently driven to PASS or FAIL from this sandbox
  today — it is UNKNOWN/not-yet-deployed by the PR's own account, and nothing I found contradicts
  that account.
- **Checked `git ls-files -s` file modes for the whole bundle** looking for a packaging mismatch:
  `install.sh` is committed `100644`, while the equivalent bundle `ops/wq2166-gpu-lowpower-ordering/
  install.sh` is `100755`. Minor — the README's own deploy snippet already invokes it as
  `sudo bash /tmp/wq3650-macpro-wol/install.sh`, so the missing `+x` bit doesn't break the
  documented path, only a hypothetical direct `./install.sh` invocation. Noting, not contradicting.
- No contradiction found in the dispatcher code, the tests, the ops scripts, or the documentation.

## Verdict: CONFIRM

wq#3650's dispatcher integration (`node_power.py`'s wol branch, `wake_judge.py`/`.sh`) is correctly
implemented and well-tested — 51/51 passing, including every wol-specific case, hand-traced against
the registry schema rather than trusted blind. The MACs are independently verified against live
2026-09-10 probe data, not guessed. The ops bundle's scripts are internally consistent and match
their own timer/conf defaults. The PR is honest about what it did *not* do: `wake.method` is left
`plug-ac` on both real nodes, with a clearly reasoned why and a named follow-up ticket, and the
bundle is explicitly shipped-not-deployed pending an ssh+root lane neither the author nor this
verifier has. The one latent issue found (`interlock()` still gates a wol-node's AC-preserving
drain on plug-wattage conditions that drain path never uses) is dormant, fails safe, and doesn't
block either node as currently configured — flagged for the eventual wol-cutover ticket rather than
escalated as a contradiction.

## Evidence commands (read-only, re-runnable)

```
git -C /home/lumen/foreman show 3c561e93 --stat
git -C /home/lumen/foreman diff b3eb4e20 3c561e93 -- dispatcher/node_power.py research/inference_nodes.yaml research/INFERENCE_NODE_REGISTRY.md
python3 -m pytest dispatcher/tests/test_node_power.py dispatcher/tests/test_wake_judge.py
grep -n "method: wol\|method:" research/inference_nodes.yaml
grep -n "enp12s0" research/INVENTORY_RECONCILIATION.md
which wakeonlan
git -C /home/lumen/foreman ls-files -s ops/wq3650-macpro-wol/install.sh ops/wq2166-gpu-lowpower-ordering/install.sh
/home/lumen/bin/fleet-exec grant
```
