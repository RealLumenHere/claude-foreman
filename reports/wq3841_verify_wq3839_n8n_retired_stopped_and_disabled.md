# wq#3841 — verify wq#3839: n8n retired: stopped and disabled, 0 workflows, 7 CRITICAL CVEs

Verdict: **CONTRADICT**. Escalating to opus-5@high (judgment ticket filed below).

PR: http://100.121.17.5:3000/lumen/claude-foreman/pulls/1832 — not reachable via
unauthenticated `curl` from this worktree (404, same auth gap noted in
wq3791's verify report); not treated as fleet evidence. The merge is directly
readable from local git history instead: content commit `f5a034f248`, merge
commit `40c14a2f`, currently `HEAD` of this verify worktree's branch base.
Diff touches two files: `docs/fleet/retired_services.yaml` (+33, new `n8n`
entry) and `research/FLEET_SERVICE_ATLAS.md` (2-line status-column fix).

## What checked out (CONFIRMED)

1. **Units actually stopped+disabled.** `systemctl show -p LoadState
   -p ActiveState -p UnitFileState n8n.service n8n-watcher.service` on this
   node (I am the Lumen build worker — no SSH wrap needed) returns
   `loaded`/`inactive`/`disabled` for both, matching the PR's claim and test
   plan exactly.

2. **7 CRITICAL CVEs, no fix short of major upgrade — independently
   corroborated, not fabricated.** wq#3766 (CVE batch, done 2026-10-12,
   two days before this PR) remediated n8n's bundled deps (vm2 19 CVEs,
   fast-xml-parser, proxy-addr, shell-quote, tar bumped within major) and
   left exactly this residue in its own notes: *"REMAINING, not done: 7 n8n
   CRITICAL pairs need a major n8n upgrade (simple-git 3->4, argv-parser
   1->2, tar 6->7, expr-eval has no fix, form-data/handlebars need new
   deps) — n8n currently has 0 published workflows so retiring it is the
   cleaner fix (Frank call)."* wq#3839's retirement is that exact
   Frank-call being exercised. Installed version `2.32.7` matches byte-exact
   across two snapshots taken during wq#3839's own run
   (`foreman/_runs/wq3839/snapshots/*package.json`, `"version": "2.32.7"`,
   identical before/after — confirms no upgrade silently happened).

3. **wq#1302 SKIP verdict citation is accurate.** `wq show 1302` notes:
   "n8n: SKIP. Measured 0/109 fleet systemd timers movable as pure n8n
   workflows" — matches the retired-entry's "0/109 fleet timers movable."

4. **retired_services.yaml schema/format.** `load_state: loaded` (unit
   files kept, not removed) has direct precedent in the same file (lines
   69, 135, 235, 342 before this diff) — not a novel or inconsistent
   convention.

## What did NOT check out (CONTRADICT)

The new yaml entry's `guards` section (`docs/fleet/retired_services.yaml:366-370`)
justifies disabling `n8n-watcher.service` alongside `n8n.service` by saying
the watcher "posted [failures] to the wrong ntfy topic, `lumen_alerts` not
`lumen-alerts`, per FLEET_SERVICE_ATLAS.md §7 F-6 ... the topic-mismatch bug
was already silencing it." **That bug was fixed a month before this PR
and the watcher's alerting was working at retirement time:**

- `wq show 1645` (done 2026-09-19): *"Fixed: n8n_watcher.py ntfy() now
  shells to /home/lumen/bin/ntfy-send -t lumen-alerts (house path,
  delivery-verified, OOB fallback) instead of raw curl to
  :8080/lumen_alerts ... Verified: ... live test send delivered (rc=0),
  service restarted, journal clean, 'n8n Watcher Online' delivered via new
  path."*
- A filesystem snapshot of the live `/home/lumen/agent/n8n_watcher.py`
  taken 2026-10-12 13:32:02Z (two days before wq#3839, captured incidentally
  during wq#3766's run — `foreman/_runs/wq3766/snapshots/20261012T133202Z-home_lumen_agent_n8n_watcher.py`)
  shows the fixed code live: `NTFY_SEND = "/home/lumen/bin/ntfy-send"`
  with an inline comment "`wq#1645: house path ... fixed to the real topic
  'lumen-alerts'`", and the `ntfy()` call passes `"-t", "lumen-alerts"`
  (hyphen, correct topic) — not the broken underscore path the retirement
  entry describes.
- `journalctl -u n8n-watcher.service` on this node shows a clean success
  log line from the watcher's last full run before retirement: `Oct 07
  23:38:39 lumen python3[1718326]: 2026-10-07 23:38:39,956 [INFO] ntfy
  sent: n8n Watcher Online` — no `ntfy-send exit`/`ntfy failed` warning
  (which the script logs on any delivery failure). The alert path was
  live and delivering right up to the unit's final stop (`Oct 14
  02:42:07`), not silently swallowed.

So the specific causal claim baked into a now-permanent fleet record
(`retired_services.yaml`'s own header says retirement-audit tooling,
wq#1644, reads this file) is false: n8n-watcher was not "already silenced"
by a stale topic bug — it had working, delivery-verified alerting at
retirement time. The PR's author copied the still-uncorrected prose at
`research/FLEET_SERVICE_ATLAS.md:17-20` and `:379` (present tense: "Zero
of its alerts have ever reached...") straight into the new yaml record
without checking it against current source or the F-6 row's own
resolution column (`research/FLEET_SERVICE_ATLAS.md:556`, `**wq#1645**`,
done) — the atlas's "Findings → tickets" table already named the fix
ticket; the narrative prose above it was just never updated after the fix
landed. wq3839 propagated the staleness into a second, purportedly
authoritative document instead of catching it.

This does not flip the *action* taken — disabling `n8n-watcher` is still
safe, just for a different and simpler reason than the one on record (0
active workflows means nothing fires failures to watch for, independent of
which topic it posts to). But the written justification is a factual
error in a doc other tooling is meant to trust as ground truth, which is
exactly the kind of thing a verify pass exists to catch.

## Secondary note (not a contradiction — a gap)

"0 published workflows ever created on it" is asserted, not demonstrated.
wq#3839's own test plan (`/tmp/fworker-wq3839/pr_body.md`, captured at
`foreman/_runs/wq3839/snapshots/20261014T030254Z-...pr_body.md`) only
checks yaml parse + `systemctl show`; it never queries n8n's workflow
store. I could not independently verify this either way: `/home/lumen/.n8n`
and the n8n binary are outside this worktree's permitted paths (the
`ls`/direct-exec attempts were correctly refused — not retried). The claim
traces back to wq#1302 (Sept 3) and is carried forward 6 weeks without a
fresh check; it may well still be true (nobody is known to have touched
the n8n UI since), but it is inherited, not reconfirmed. Flagging for the
judgment pass; not independently disqualifying on its own.

## Escalation

Filed **[judgment] wq#3842**, lane build, model `claude-opus-5`, effort
`high`, archetype `rca-investigation` — pointed at this report, asked to
rule on whether the stale-justification error requires a follow-up edit to
`docs/fleet/retired_services.yaml`'s `guards` text (and/or a pass fixing
the still-stale prose at `research/FLEET_SERVICE_ATLAS.md:17-20,379`), and
on the weight of the unverified "0 workflows" carry-forward.
