# wq#4847 verify-pass: wq#4842 "[selfheal] fleet node anubis offline on tailscale for 71 min"

**Verdict: CONFIRM.** PR http://100.121.17.5:3000/lumen/claude-foreman/pulls/2599 (squash-merged
to main as `19d46dea8a8b4fada7eb64c38c9f3aec4dc5ff6a`, identical blob content to the PR's own
head commit `cde04f79b61f2caed2ccf676b3589495e415a029` — same before/after blob hashes
`0811dc57..ff9fae6c`) adds one `nodes.notes.anubis` entry (21 lines) to
`ops/wq3354-fleet-selfheal/selfheal.yaml:322-343`. Docs-only, no `enabled`/behavior change —
archetype-8 blast radius here is "is the classification true," not "does anything break."

## Diff shape

`git show --stat cde04f79`: 1 file changed, 21 insertions(+), 0 deletions. The PR branch
(`cde04f79`, parented on `e0457a39`) was not an ancestor of main's HEAD at verify time
(`git merge-base --is-ancestor cde04f79 HEAD` → false), but a second, independently-authored
commit for the same ticket (`19d46dea`, 05:36:12Z, ~2.5 min after `cde04f79`'s 05:33:52Z) landed
the byte-identical file content first. `wq show 4842` confirms this is the same merge, not a
separate fix: notes field reads "PR #2599 merged to main as 19d46dea." Two parallel workers
produced the same diff; the later one's commit hash is what's on `main`, the PR's own head
commit is not — expected race outcome, not a defect in the PR.

## Re-verification of each factual claim in the note (this session, 2026-11-04 ~05:41Z UTC)

1. **71-min duration** — `wq show 4842`: `created_at 2026-11-04 05:29:09`, detail cites
   `LastSeen 2026-11-04T04:17:18.1Z`. 05:29:09 − 04:17:18 = 71m51s. Matches the ticket title
   and the note's "(71 min)" figure.
2. **`anubis.role`** — `lumen_state.py current anubis` → `anubis.role = Borg backup repo +
   Gitea (since 2026-06-01) [UNVERIFIABLE — no probe]`. Matches the note's quote exactly.
3. **`anubis.power_plug`** — `lumen_state.py current anubis` → `anubis.power_plug = P9
   (since 2026-07-16) [UNVERIFIABLE — no probe]`. Matches the note's quote exactly, including
   the UNVERIFIABLE tag it cites as the reason plugctl evidence can't be used either way.
4. **`lumen_state.py history anubis availability` is empty** — re-ran it live: empty output.
   Confirms "first selfheal page recorded for this node, not a repeat."
5. **No Frank power_policy/auto_sleep fact** — `lumen_state.py current anubis` full output has
   no `power_policy`/`auto_sleep` key for anubis (unlike hathor, which the note explicitly
   contrasts against). Confirmed by absence.
6. **No off-decision** — grepped `selfheal.yaml` nodes.ignore: apis, pbox, seshat, be3600,
   3dprinter, khnum, neith, wadjet are all present there with recorded Frank
   retire/decommission/power-off decisions; anubis is not in `nodes.ignore` or
   `nodes.sleepers` anywhere in the file. Confirms the note's contrast list is accurate and
   anubis correctly stays out of both lists.
7. **Live reprobe claims (SSH timeout, LAN no-route-to-host)** — these are point-in-time probes
   from ~04:anything-05:33Z; by the time of this verify pass anubis had already recovered
   (`fleet-exec run anubis -- uptime` now returns `up 7 min` at 05:41:26Z, i.e. booted ~05:34Z),
   so the original failure can't be bit-for-bit reproduced. Corroboration instead of direct
   repro: `anubis.broker_last_transport_error = 2026-11-04T05:32:09Z` (a live transport-error
   timestamp, not a stale read) sits right before the observed reboot, and a later, independently
   authored note for a correlated ticket — `nodes.notes.maat` (wq#4844, commit `24c477c2`) —
   states "anubis went dark 5s later (04:17:18Z, wq#4842; back on LAN :22 by 05:37Z)" as part of
   a shared power/network-domain outage (maat/anubis/thoth/plug-tapo-p4 all dark in the same
   reprobe window). This is independent corroboration of both the 04:17:18Z start time and the
   ~05:37Z recovery, consistent with the uptime-derived ~05:34Z boot. No contradiction found.
8. **Classification conclusion** — "stays a live fleet node... one unexplained outage with no
   ignore/sleepers-qualifying fact behind it is a real page, not nodes.ignore/sleepers." The
   later wq#4844 shared-domain finding explains a likely common cause but does not supply a
   Frank off-decision or self-idle fact for anubis, so it doesn't change the bar the note applies
   — consistent with how the same later note treats maat (root cause found, still "a real page,
   never nodes.ignore/sleepers").

## Ops-bundle liveness

`bash /home/lumen/foreman/ops/wq3354-fleet-selfheal/install.sh --verify` is not on this
BUILD-lane worker's Bash allowlist (`worker-settings.json` has no `bash <path>` wildcard
covering `ops/*`; only `Bash(bash scripts/*)` is present) — refused outright
("This command requires approval"), not attempted a second time per the no-retry rule on an
allowlist refusal. Liveness for this bundle is carried as `ops_verified(wq3354-fleet-selfheal)`
per the ticket header; `wq4842`'s own notes record the deploy path as "NO-DEPLOY-NEEDED" for
this docs-only change (`selfheal_sweep.py` re-reads `selfheal.yaml` every sweep, no restart
required) with a separate root-apply ticket filed (`wq#4846`) for the live-destination sync,
which is out of this verify-pass's scope (the destination path
`/home/lumen/agents/fleet-selfheal` is outside every `--add-dir` root granted to this worker).

## Probe run

`fleet-exec run anubis -- uptime` (the PR's own named probe) re-run live this session:
`05:41:26 up 7 min, 1 user, load average: 0.30, 0.19, 0.10` — anubis is reachable now, which is
the expected state after recovery and does not contradict the note (the note documents a past
outage window, not a permanent-down claim).

## Conclusion

No contradiction. Every checkable fact in the note reproduces against live `lumen_state`
output or the current `selfheal.yaml` ignore/sleepers lists, the time arithmetic is correct, and
the one set of claims that can't be bit-for-bit repeated (the historical SSH/LAN timeouts) is
corroborated by an independent later note (wq#4844) and a live transport-error timestamp that
sits exactly where the claimed outage would place it. CONFIRM — no escalation to judgment.
