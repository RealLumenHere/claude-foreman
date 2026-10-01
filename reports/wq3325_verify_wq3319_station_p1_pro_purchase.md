# wq#3325 verify: wq#3319 [physical] wq#3294-C2: Frank buys 2 Station P1 Pros for Commander

Verdict: **CONFIRM**

PR: foreman PR#1532, merged to `main` as `529f3de917` (squash). Diff verified:
`git show 529f3de917` — two new files, no edits to existing code:
`ops/wq3319-a1/{README.md,commander-pool.example.json}`.

## What wq#3319/PR#1532 claimed

A purchase ticket (`[physical]`, `[NO-PROBE]`) that no session can execute. The
diff: (1) records the decision to buy 2 Station P1 Pro units now, not 6, citing
`DESIGN.md`'s own recommendation; (2) stages an on-arrival runbook (clone
`ops/wq2608-dc-endpoint-bes-a1` per node, do not copy Bes's shared-box memory
limits verbatim, flag an open question on `commander-pool-failover.service`
single-vs-per-node install); (3) ships a verified-schema
`commander-pool.example.json` for the mcpw pool join step.

## What I checked

Read the diff cold, then verified every citable factual claim against its live
source rather than taking it on faith:

- **`DESIGN.md` §C** — `ops/wq3294-commander-ha/DESIGN.md:61-68` reads "buy 2
  now, not 6 — two dedicated always-on backends plus Lumen and Bes gives N+2;
  revisit after a month of uptime data." Matches the README's claim verbatim.
- **`commander-pool.example.json` schema** — read `pool_failover.py`'s
  `tick()`/`main()` directly: config keys are accessed via plain
  `config["backends"]` / `config.get(...)`, so an unrecognized key (`_comment`)
  is silently ignored, not a parse error. The README's claim that the schema
  is verified against exactly `interval_s/probe_timeout_s/fail_threshold/
  rise_threshold/backends/on_demote/on_promote` holds.
- **`--config` path** — `commander-pool-failover.service:14` hardcodes
  `--config /home/bes/etc/commander-pool.json`, matching the README's claim
  about where the on-arrival ticket must deploy the populated file.
- **Unit header comment** — `commander-pool-failover.service`'s header reads
  "Installs on BES (and later each P1 Pro), never only on Lumen: the driver
  must survive the death of the node it fails away from" — byte-for-byte what
  the README quotes.
- **The open question itself** — `DESIGN.md:33` (architecture diagram) and
  `DESIGN.md:46-49` ("## B — Health-check / failover loop (shipped here)...
  Runs on **Bes**, not Lumen") both describe one central driver, not one per
  backend. The README's flagged ambiguity — the unit comment's "(and later
  each P1 Pro)" read against a single-active-loop design — is a real,
  well-founded catch, correctly left open rather than silently resolved either
  way (duplicate-copy risk: double demote/promote hook firing, no stated
  leader election).
- **wq#2608 README citations** — `ops/wq2608-dc-endpoint-bes-a1/README.md:16-19`
  carries the `MemoryHigh=3G MemoryMax=4G ... far below Lumen's 20G/26G because
  Bes is a shared 16G box` text cited; `:88-89` carries the "Lumen's
  `desktop-commander-mcp.service` unit shape (`systemctl cat`)" text cited.
- **Live Lumen memory limits** — `systemctl cat desktop-commander-mcp.service`
  on this box shows `MemoryHigh=20G` / `MemoryMax=26G` exactly as cited for the
  on-arrival fallback reference point.
- **`kb/ISSUE-LOG.md` #33 / `ops/wq2611-a1/README.md:13`** — both citations
  (bench power-cycle blocker; CF API token physical-task blocker) check out
  against their targets. Aside, unrelated to this diff: `ISSUE-LOG.md` has two
  rows both numbered 33 (wq#678 and wq#362) — a pre-existing duplicate from
  before `issue_log_append.py`'s allocator lock, not something wq#3319
  introduced or depends on; not filing a new row for it since it isn't
  blocking anything here.

No incorrect or unverifiable claim found anywhere in the diff.

## Secondary gap: no `--verify` handler (closed this session)

Per `ops/VERIFY_CONTRACT.md` and this ticket's own text, `ops/wq3319-a1` shipped
with no `install.sh`, so no `--verify` handler. Unlike a CONTRADICT verdict,
confirming here requires adding one first. The bundle installs nothing of its
own (the purchase hasn't happened; the on-arrival apply is a follow-up ticket
once hardware exists), so the handler is a drift check tying the README's own
citations to their live sources rather than a deploy-match check:

- `_verify_schema.py` — `commander-pool.example.json` still uses only the keys
  `pool_failover.py` reads, and has at least one well-formed `{name,url}`
  backend.
- `commander-pool-failover.service`'s source still contains the
  `--config /home/bes/etc/commander-pool.json` string the README cites.
- `desktop-commander-mcp.service` still shows `MemoryHigh=20G`/`MemoryMax=26G`,
  the README's on-arrival fallback reference point.

Added `ops/wq3319-a1/install.sh` + `ops/wq3319-a1/_verify_schema.py` in this
PR. Confirmed the probe can fail (wq#2630): injected an unknown key into
`commander-pool.example.json`, re-ran, got `MISS unknown keys ... ['bogus_future_key']`
/ exit 1, then restored the file and got `VERIFY OK` / exit 0 again. Ran against
the primary tree after landing:

```
$ bash /home/lumen/foreman/ops/wq3319-a1/install.sh --verify
VERIFY OK
EXIT=0
```

## Things I looked for and did not find a contradiction in

- Whether the "buy 2, not 6" decision needed fresh judgment — no, it's a
  direct restatement of `DESIGN.md`'s own pre-made recommendation, not a new
  call invented this session.
- Whether the ticket should have been left blocked on Frank instead — no,
  this is exactly the standing physical-task instinct's intended shape: the
  irreducible physical act (the purchase) is queued, everything downstream
  that doesn't need the hardware is staged now.
- Whether `commander-pool.example.json` should have included a populated
  `bes-dc` entry already live — no, `ops/wq2608-dc-endpoint-bes-a1/apply.sh`
  hasn't run yet per that bundle's own state; marking it "staged" rather than
  live is accurate.

## Action taken

Confirmed on wq#3319/wq#3325 via `wq done`. No re-implementation of wq#3319's
content; the only change in this PR is the `ops/VERIFY_CONTRACT.md`-mandated
`--verify` handler the ticket text required before confirming.
