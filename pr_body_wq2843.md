# wq#2843 — the CF-rotation hold reads `CF_API_TOKEN`, not just the word "cloudflare"

## What

One shape, `harness/cf_hold.py` `CF_NAMED`, now decides whether a rotation row is under
Frank's CF-resource hold, and both walls read it: `fworker.is_cf_rotation()` (files the
row `--frank-gated`) and `foreman_dispatcher.CF_ROTATION_HOLD_RE` (refuses to claim it).
`rotation_title(..., cf_held=True)` appends a `[cf-hold]` marker so the title-only second
wall can recognise the row the first wall just identified.

## Why

wq#2787's verify pass CONTRADICTed wq#2782 (ISSUE-LOG 869). Both walls keyed on the
literal substring `cloudflare`. The fleet's own deployed config names CF credentials
without that word — `ops/wq2608-dc-endpoint-bes-a1/dns_access_setup.sh:46` is
`CF_API_TOKEN=$(cat …)` — and a burned `CF_API_TOKEN` is labelled `kv-secret`, so a pri-1
rotate-today row for a Cloudflare API token filed OPEN and dispatched to an unattended
worker: the exact chain behind the 2026-09-25 mcpw/Commander outage that ISSUE-LOG 850
rule c holds.

Second finding, not in wq#2787: widening the title regex alone could NOT have closed the
second wall. A rotation title carries the mask LABELS and nothing else (`… (kv-secret)`),
never the masked context, so the held row's title named no CF resource at all. Hence the
marker.

## How

    CF_NAMED = r"(?:cloudflare|(?<![A-Za-z0-9])CF[_-](?=[A-Za-z0-9]))"

- `cloudflare` stays bare (no trailing `\b`): `_` is a word character, so `\bcloudflare\b`
  does not match `CLOUDFLARE_ZONE_ID`.
- The separator directly behind `CF` is the whole discriminator: `CF_API_TOKEN`,
  `CF_ZONE_ID`, `CF_ACCOUNT_ID`, `WQ_CF_ZONE_ID` match; `CFG_SECRET`, `CFLAG_TOKEN`,
  `NETCF_TOKEN` do not. The lookbehind is `(?<![A-Za-z0-9])` rather than `\b` so the shape
  is still found as a segment of a longer name, where `\b` would not fire after `_`.
- The dispatcher composes its title pattern from that same string, so the two walls can no
  longer drift apart — which is how this bug survived wq#2782.
- `[cf-hold]` is itself in the `CF[_-]` shape, and goes at the END of the title:
  `ROTATION_TITLE_PREFIX` (the chain guard) and `mask_triage.SOURCE_RE` both match from
  the front and are untouched. It is part of the wq#2341 dedupe key like everything else
  in the title, which is correct — the same value burning twice produces the same marker.

Over-firing is the safe direction on purpose: a match only ever routes a
credential-rotation row to Frank instead of to a fire-and-forget worker, while a miss is
the outage again.

## Testing

Offline throughout: scratch `WQ_DB` sqlite only, fake `lumen-run`/ntfy shims, no live
services. Every new probe is keyed on `CF_API_TOKEN`/`CF_ZONE_ID`, never on `cloudflare`
or `CLOUDFLARE_*` — wq#2782's tests all fed the check an input containing the word, so
they passed against a regex that was only ever `r"cloudflare"` (wq#2630).

- `python3 -m pytest harness/tests/test_masking.py -q` → 110 passed. New: the `CF_KV_LINE`
  fixture (asserts `cloudflare` is in neither the input nor the record), the
  `CFG_`/`CFLAG`/`NETCF_`/`PERF_` controls, `mask_alarm()` end-to-end on the ISSUE-LOG 869
  reproduction, and the title-suffix invariants.
- `python3 -m pytest dispatcher/tests/test_dispatcher.py -k "cf_rotation or frank_gate" -q`
  → 3 passed. The new case is the cross-wall probe: the held titles are built by
  `fworker.rotation_title()` itself, so what the first wall EMITS is what the second wall
  is asked to hold, with three `[rotation]` controls that must still dispatch.
- `python3 -m pytest harness/tests/test_mask_triage.py harness/tests/test_mask_replay.py -q`
  → 115 passed.
- Full `dispatcher/tests/test_dispatcher.py`: 1 failed, 149 passed. The one failure,
  `test_auto_lane_non_sweep_ticket_still_unmapped`, is NOT this branch's — it reproduces
  in the primary checkout at foreman `d031a6d` with no wq#2843 diff present (an auto-lane
  ticket repo_sweep did not file gets spawned with a guessed repo instead of being refused
  `lane_unmapped`). Filed as wq#2870, ISSUE-LOG 879.
- Out-of-corpus probe, outside the fixture population entirely: the variable NAME parsed
  from the deployed `dns_access_setup.sh` pinned at foreman `93a79d6` (the live line is a
  pointer masking suppresses, so only the body is synthetic) — old `is_cf_rotation` False
  and old dispatcher wall False; new both True.

## Risk / rollback

Held rows go to Frank instead of to a worker, so the failure mode of a false match is a
manual rotation, never an unattended one. Title change is additive and only on held rows;
the parsers that read rotation titles (chain guard, triage) match from the front and are
covered by tests. Rollback is `git revert` of this commit — the hold reverts to
`cloudflare`-only, i.e. today's main.

Deleting `harness/cf_hold.py` and its two callers is how the hold is lifted once
ISSUE-LOG 850 is closed. There is no override flag, by design.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
