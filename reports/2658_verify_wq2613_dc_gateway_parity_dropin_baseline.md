# wq#2658 — verify-pass: wq#2613 DC endpoint parity check (nightly) + FLEET_TOOLS/state entries

**Verdict: CONTRADICT** — the shipped bundle (`ops/wq2613-dc-endpoint-parity-check-a1/`) is an
honest, well-researched proposal (correct `aq add` CLI shape, correct per-node service unit
names, honest "staged, not deployed" status matching its author's real sandbox limits), but the
one axis of the parity check that was actually exercisable from this lane — the unit drop-in
hash comparison — is broken by construction: the baseline node (Lumen) is compared against
drop-in filenames that do not exist on Lumen, so `dropin_hashes(lumen)["dc-gateway-hardening.conf"]`
and `["dc-gateway-logging.conf"]` are always `None`. That inverts the check's whole purpose:
a healthy, correctly-hardened remote node will be falsely flagged as drifted every night forever,
while an unreachable/dead node (which also returns `None` for both files) silently passes on this
axis. Reproduced live below, no fleet SSH required.

PR: `lumen/claude-foreman` #1017, branch `feat/wq2613-dc-endpoint-parity-check-nightly-fleet-t-a1`,
tip commit `37d41575`, not yet merged into `main` (`git merge-base --is-ancestor` → false).
Diff is 7 files / 389 insertions, entirely new: `docs/fleet/UN_RETIRE_CHECKLIST.md`,
`ops/wq2613-dc-endpoint-parity-check-a1/{README.md,dc-endpoint-parity-check.py,.service,.timer,
lumen_state_ingest.sh,wq2613_fleet_tools.diff}`.

## What was verified and holds up

- The bundle's honesty about its own limits is accurate: it never touched anubis/bes/nephthys,
  never installed the `.service`/`.timer`, and never hand-applied `wq2613_fleet_tools.diff`.
  Matches `kb/ISSUE-LOG.md` row 751 (this session's own `lumen_busy`/allowlist checks reconfirm
  the same gaps: `aq`/`lumen_state.py`/`state-regen` are not on this lane's Bash allowlist either).
- `aq_add()`'s call shape — `aq add dc-parity <key> <title> <detail> --owner fleet-ops` — matches
  the live CLI exactly. Independently re-confirmed this session (not just trusted from
  `kb/ISSUE-LOG.md` row 330): `python3 /home/lumen/agents/alert-queue/aq.py --help` →
  `aq add <source> <key> <title> [msg] [--owner X]`.
- `GATEWAY_UNIT` is correct for every node: `desktop-commander-mcp` for lumen (confirmed live via
  `systemctl cat desktop-commander-mcp.service` — unit exists, `ExecStart` matches), `dc-gateway`
  for anubis/bes/nephthys (confirmed against `ops/wq2607-dc-endpoint-anubis/dc-gateway.service`
  and `ops/wq2608-dc-endpoint-bes-a1/dc-gateway.service`, both real files in this repo).
- `main()` correctly excludes the baseline node itself (`for node in NODES[1:]`) from
  `check_node()`, so there is no baseline-vs-itself false positive on the other four checks
  (npm versions, `dc-enforce-config.py` stdout, watchdog-active).
- `dc-enforce-config.py`'s stdout shape (`[dc-enforce:tier1] blockedCommands=<n>
  onboarding=<bool>`), independently re-run live on Lumen this session, is a generic count/bool —
  not node-specific text — so comparing it verbatim across nodes as a parity signal is sound.

## Contradiction — the dropin-hash baseline never matches Lumen's real files

`dc-endpoint-parity-check.py:48-52` — `DROPIN_FILES = ["dc-gateway-hardening.conf",
"dc-gateway-logging.conf"]`, a single global constant applied to every node including the
baseline (`main()`: `"dropins": dropin_hashes(lumen)`). Those two filenames come from
`ops/wq2607-dc-endpoint-anubis/` and `ops/wq2608-dc-endpoint-bes-a1/` — drop-ins for anubis/bes's
`dc-gateway.service`. Lumen's own `desktop-commander-mcp.service` was never given drop-ins with
those names.

Confirmed live, this session, on the real box (no ssh, no fleet-exec needed — this is Lumen
itself):

```
$ systemctl cat desktop-commander-mcp.service
...
# /run/systemd/system.control/desktop-commander-mcp.service.d/50-CPUWeight.conf
[Service]
CPUWeight=1000
# /run/systemd/system.control/desktop-commander-mcp.service.d/50-IOWeight.conf
[Service]
IOWeight=1000
# /etc/systemd/system/desktop-commander-mcp.service.d/no-body-logs-journald.conf
...
```

No `dc-gateway-hardening.conf` or `dc-gateway-logging.conf` anywhere in Lumen's drop-in set —
the hardening is applied via `systemctl set-property` (auto-named `50-*Weight.conf`) and the
logging drop-in was authored under a different name (`no-body-logs-journald.conf`, wq#1698).

Ran the PR's own shipped function directly against the real host (checked out the file from
`origin/feat/wq2613-dc-endpoint-parity-check-nightly-fleet-t-a1` into a scratch path, imported
it, called nothing but the pure local check — no re-implementation):

```
>>> import _wq2613_check as m
>>> m.dropin_hashes(m.NODES[0])   # lumen, the baseline node
{'dc-gateway-hardening.conf': None, 'dc-gateway-logging.conf': None}
```

`sha256sum` on a nonexistent path always fails (`ok=False`), so `dropin_hashes()`'s
`hashes[f] = out.split()[0] if ok and out else None` line makes both baseline values `None` on
every single run — this isn't a today-only fluke, it's structural.

In `check_node()`:

```python
hashes = dropin_hashes(node)
for f, h in hashes.items():
    base_h = baseline["dropins"].get(f)   # always None
    if h != base_h:
        findings.append(...)
```

Both real-world outcomes on this axis are wrong:

- **A correctly-hardened remote node** (anubis/bes/nephthys, once deployed per wq#2607/wq#2608,
  with real `dc-gateway-hardening.conf`/`dc-gateway-logging.conf` sha256 hashes) will always have
  `h != None` → flagged as drifted, forever, on a node that is in fact in parity. Since `aq add`
  upserts on `(source,key)`, this doesn't spam new rows nightly, but it pins a permanently-false
  "DC parity drift: <node>.dropin.dc-gateway-hardening.conf" alert open in `aq` that can never
  self-resolve, because the baseline it's compared against is unfixable without a code change.
- **An unreachable or genuinely misconfigured node** (ssh down, files never installed) also
  returns `{file: None, file: None}` from `dropin_hashes()` (the `run()` exception path also
  yields `ok=False`) → `None == None` → **no finding on this axis**, masking the exact
  "liveness != health" class of defect the design doc (and this PR's own module docstring) calls
  out as the motivating case for the whole check.

This is not a rare edge case — it fires on the very first invocation, for any node in any state,
because the baseline side is broken independent of what the remote side reports.

## Disposition

Not fixing `dc-endpoint-parity-check.py` under this ticket — the verify-pass archetype is
explicitly "do not re-implement," and picking the right per-node/per-file drop-in mapping is a
real design decision (constants keyed by node, not one global `DROPIN_FILES` list) that belongs
to the fix's author, not a verifier. Urgency is bounded for now the same way wq#2646 found for
wq#2608's `rollback.sh`: nothing has been deployed yet (`.service`/`.timer` not installed, no
ssh access from any BUILD lane so far), so the bug is currently dormant — but it will misfire on
every dropin.* row from the very first night this timer runs for real, and will silently pass a
genuinely-drifted-or-dead node on that same axis. Escalating to opus-5@high per ticket
instructions and `research/models.yaml`'s `verify_pass.escalate_on_contradiction`, recommending
`DROPIN_FILES` become a per-node (or per-`GATEWAY_UNIT`) mapping — keyed off each node's actual
drop-in filenames, not a single constant borrowed from anubis/bes's units — before this ships.
