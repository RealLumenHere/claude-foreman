# wq#4048 — judgment: wq#3972's tshark/afl++ entries missing license_risk / redistribution_posture

**Ruling: REAL GAP. Upheld, and fixed here rather than reopened.**

Escalated from wq#4047 (verify pass on wq#3972, PR lumen/claude-foreman#1986,
merged 4b973906). Judge: claude-opus-5, worktree `/home/lumen/worktrees/wq4048-a1`.

## The question

Is a trust=core catalog entry landing without `license_risk` /
`redistribution_posture` a real redistribution-compliance gap, or is it out of
scope for a tool-seed ticket?

## Why it is in scope

1. The policy is not advisory and not external to the ticket. It lives in the
   file wq#3972 edited, four lines above the entries it added
   (`limpet/tool-catalog.json:18`, `provenance.license_risk_policy`), and says
   "Every tool entry going forward MUST also carry 'license_risk' ... and
   'redistribution_posture'". It was introduced by the immediately preceding
   commit to that same file (2c603e62, wq#3959). "Going forward" cannot mean
   anything narrower than the next entry added.
2. `trust=core` is not a label, it is a shipping decision. The catalog's own
   `trust_tiers` reads "core = proven on fleet, **in base image**" — the base
   image being the commercially-sold Limpet ISO. Adding a core entry *is* the
   act of deciding to redistribute the tool.
3. tshark is the policy's own worked case. GPL-2.0 is copyleft; the policy cites
   EMBA/FACT (GPL-3.0) as "safe to bundle with a source offer". A tool-seed
   ticket that cannot be bothered to classify a copyleft tool it is putting in a
   commercial image is exactly the failure the policy was written to prevent.

Counter-argument considered and rejected: "wq#3972's ACCEPT list only named
manifest validation, catalog reconcile and the ISO package list, so licensing was
out of its scope." An ACCEPT list enumerates what must be *demonstrated*, not an
exemption from the invariants of the file being edited. The 41 pre-policy entries
are genuinely out of scope for wq#3972 — the two it authored are not.

## Classification applied

| tool | license | license_risk | redistribution_posture | reasoning |
|---|---|---|---|---|
| tshark | GPL-2.0 | `copyleft-source-offer` | `bundle` | Copyleft, same class as the policy's GPL-3.0/EMBA example. GPL-2.0 permits commercial redistribution; what it adds is an obligation to offer corresponding source. That is a build obligation on the ISO, not a bundling blocker — so `bundle`, not `external-invoke`. |
| afl++ | Apache-2.0 | `none` | `bundle` | Permissive, OSI-approved; notice/attribution only. Matches the existing ghidra worked example (`limpet/tool-catalog.json:212-215`), also Apache-2.0. |

Upstream-license verification by live search was not available: `fleet-search web`
returned the known all-engines-suspended outage (wq#2595/wq#2599/wq#2601), the same
wall wq#3972 itself hit. The classification rests on the license strings wq#3972
recorded in the entries (`GPL-2.0`, `Apache-2.0`) and the policy's own enum, not on
a fresh upstream read; neither string is in dispute in the wq#4047 report.

## Why fixed, not reopened

The gap's cause is not that wq#3972's author was careless — it is that
`grep -rln license_risk limpet --include='*.py'` had no hits. A MUST with no check
behind it is a comment. Reopening wq#3972 would relabel two entries and leave the
next ticket free to repeat the omission, so this ticket also lands the check:

- `limpet/license_policy.py` — enum validation plus a **ratchet**: the 41 entries
  that predate wq#3959 are frozen in `LEGACY_UNAUDITED` and exempt from the
  must-declare rule (they are still enum-checked); any name outside that set must
  carry both fields. New entries cannot omit them.
- `limpet/test_license_policy.py` — runs the check against the live catalog, and
  (the falsifiable half) asserts a *new* entry without the fields, a partial
  declaration, and out-of-enum values each produce exactly one violation.
- `limpet/iso-builder/gen_tool_packages.py:76-82` — the ISO install manifest now
  drops any entry whose posture is not `bundle`. The ISO is where redistribution
  actually happens, so that is where the posture has to bite. No catalog entry is
  non-`bundle` today, so the shipped manifest is unchanged; the path is covered by
  a fixture test instead of waiting for the first FAT-2.0-shaped tool to arrive.

## Left open (deliberately)

The 41 pre-policy entries are unclassified, and several are high-probability
copyleft that the ISO already bakes in (qemu-user/qemu-system, gdb-multiarch,
flashrom, openocd, sigrok-cli, radare2). That is a per-tool license audit, not a
judgment call, and guessing 41 licenses here would be exactly the fabrication this
catalog's provenance block refuses. Filed as **wq#4050** (build lane, pri 2);
`LEGACY_UNAUDITED` shrinks as that audit lands, and the ratchet makes the backlog
finite and visible rather than open-ended.

## Evidence

```
$ grep -c '"license_risk"' limpet/tool-catalog.json      # liveness probe: was 1
3
$ python3 -m limpet.license_policy; echo $?
0 license-policy violation(s) in .../limpet/tool-catalog.json
0
$ python3 -m pytest limpet/test_license_policy.py limpet/iso-builder/test_gen_tool_packages.py -q
25 passed
$ python3 -m pytest limpet/registry/tests -q
37 passed
```
