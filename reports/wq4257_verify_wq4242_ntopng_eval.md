# wq#4257 — verify wq#4242: EVALUATE ntopng network monitoring for post-OPNsense deploy

**Verdict: CONFIRM**

Archetype 8 adversarial verify-pass. Author: claude-sonnet-5. Verifier: claude-sonnet-5@high
(fresh instance). Target: commit `1a5edd3b` "docs(research): wq4242 evaluate ntopng - DEFER,
same gate as R19" (`research/WQ4242_NTOPNG_EVAL.md`, 72 lines, new file, no code changed),
PR http://100.121.17.5:3000/lumen/claude-foreman/pulls/2170, already merged to `main`
(confirmed via `git branch --all --contains 1a5edd3b`). No functional probe ships with a
docs-only research verdict; "running its probe" here means independently re-executing every
cited grep/read against the live repo rather than trusting the citations.

## What the diff claims

ntopng was already evaluated as `os-ntopng` in `DIGEST_Fleet_searchability_md.md` R19
(DEFER, "same gate" as R18/R10). This ticket re-confirms that verdict with the live evidence
behind the gate rather than re-deriving it: no live OPNsense box carrying traffic (both
deployment modes — the `os-ntopng` plugin and standalone ntopng+Redis over NetFlow/sFlow —
need either a kernel traffic mirror from a live firewall or a flow exporter on hardware
slated for demotion), and no sandbox has SSH reach to OPNsense/Pi-hole/Flint3 even to spike
the plugin install. Recommendation: do nothing now; re-open once OPNsense is live on the
tailnet **and** `fleet-exec-policy.yaml` grants a path to it.

## Verification performed

1. **Read cold**: the full 72-line diff, no prior context from the author's session.
2. **Re-ran every file/line citation against the live repo** rather than trusting them:
   - `DIGEST_Fleet_searchability_md.md:69,77,78` — R10/R18/R19 rows match verbatim
     (`grep -n "R19\|R18\|R10 "`), including the exact DEFER verdicts and "Same gate" text.
   - `NETWORK_PREMORTEM.md:5` — Status line matches verbatim: "PRE-DEPLOYMENT CHECKLIST...
     It is not a deployment."
   - `OPNSENSE_REWIRE_PREP.md:1-7,15,32-45,100-106` — header disclaimer ("nothing on the
     fleet or OPNsense was changed"), the `192.168.1.1`→`192.168.8.1/24` target-LAN record,
     the RAM-unverified TODO, and the WAN-cabling/`opnsense-fw offline, last seen 9d ago`
     line all match verbatim.
   - `reports/wq4049_verify_wq4011_opnsense_rewire_prep.md` — confirmed `**Verdict: CONFIRM**`
     at line 3, matching the "independently CONFIRMed" claim.
   - `ops/wq3653-opnsense-tailscale-pihole-a1/README.md:1-9` and
     `harness/fleet-exec-policy.yaml:189` — the `ssh:` allow-list at line 189 is
     `[lumen, isis, bes, anubis, maat, sobek, hathor, ptah, sekhmet, horus, neith, khnum,
     maksoor]`; OPNsense/Pi-hole/Flint3 are absent, matching the claim.
   - `FLEET_MONITORING_REDESIGN.md:79` — Gatus `0-backhaul`, 4/4 `success=true`, matches.
3. **Checked for drift since the doc was written** (Oct 18): `git log` on
   `fleet-exec-policy.yaml`, `OPNSENSE_REWIRE_PREP.md`, and the wq3653 README since
   `1a5edd3b` shows only an unrelated `maksoor` SSH grant — nothing reopens the gate.
4. **Adversarial checks that found two evidentiary imprecisions, neither load-bearing**:
   - `grep -n "opnsense\|pihole\|flint" harness/fleet-exec-policy.yaml` (as literally
     written in the doc, case-sensitive and case-insensitive) returns **zero matches**, not
     "matches nothing but comment lines" — there are no comment-line mentions of any of the
     three terms either. The doc's parenthetical overstates what the grep shows. It doesn't
     change the substance: zero matches still proves the three hosts aren't in the
     allow-list, and the doc's primary citation for that claim (`fleet-exec-policy.yaml:189`
     via the wq3653 README) is accurate and sufficient on its own.
   - "No vnstat/iftop/NetFlow/sFlow/SNMP tooling exists anywhere in this repo (grepped
     clean)" is imprecise if read as a literal string-absence claim: `pysnmp` appears as a
     routersploit dependency (`ops/wq3388-routersploit/README.md:34`,
     `reports/wq3401_verify_wq3388_routersploit-install.md:46`), `snmp` appears in a
     cron-job-name classification regex (`ops/wq1302-n8n-eval/cron_migration_audit.py:133`),
     and in EMBA firmware-probe docs (`design/wq4005_emba_dissection.md:54`). None of these
     are deployed fleet-monitoring tooling — a pentest module dependency, a job-name regex,
     and firmware-analysis documentation aren't a competing NetFlow/SNMP dashboard — so the
     substantive claim ("no redundant-tooling risk for a traffic-visibility tool") holds;
     only the "grepped clean" shorthand is too strong.
5. **Checked the inferential leap in "VictoriaMetrics/Grafana on Bes... ships no flow or
   bandwidth dashboard either"**: `WQ4238_LOKI_PROMTAIL_EVAL.md:9-33` confirms VictoriaMetrics
   and Grafana are live on Bes but is about log aggregation, not flow/bandwidth dashboards —
   it doesn't itself state the negative. The inference is reasonable (nothing in that or any
   other doc describes a flow/bandwidth panel) but the citation doesn't fully carry the
   specific "no flow dashboard" clause on its own.
6. **Live re-verification attempted, correctly out of scope for a BUILD sandbox**:
   `ssh lumen@100.117.66.102 'tailscale status'` is not on this worktree's command
   allowlist (not in `harness/worker-settings.json`) — refused, not retried per the
   non-retryable-refusal rule. This is consistent with the doc's own framing: it cites the
   wq#4011-era "offline, last seen 9d ago" pin rather than claiming a fresh live check, and
   explicitly defers the live Tailscale re-check to whoever re-opens the ticket.

## Conclusion

Every file/line citation that grounds the DEFER verdict checks out verbatim against the
live repo, including the two independent-access gates (no SSH reach to the three boxes;
no live OPNsense traffic to mirror or export) that the verdict actually turns on. The two
imprecisions found (an overstated grep description, a "grepped clean" claim that's true in
substance but not in literal string-match terms) are evidentiary sloppiness, not errors that
change the verdict — removing both sentences entirely would leave the DEFER conclusion
exactly as supported as it is now, by the other, accurate citations. **CONFIRM** — no
escalation to opus-5@high.
