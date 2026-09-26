# wq#2666 — verify of wq#2661 (PR#1026, merged 980ead51c3, main=6a0358e)

Verifier: claude-sonnet-5@high, fresh instance. Author: claude-opus-5. Verdict:
**CONFIRM** — no contradiction, no escalation.

wq#2661 was the judgment ticket on wq#2625's CONTRADICTED verify of PR#984
(`research/CORAL_NEEDLE_TIER.md`, wq#2623): the original doc's §2 claimed to
enumerate every model-dispatching surface on the fleet and missed mailbot's two
live per-inbound-email classification gates (`screenRomanticDrift`, `runCritic`
in `mailbot/src/worker.js`). wq#2661's ruling: (a) doc revision required and
shipped — both gates enumerated, priced, scored; fleet-wide no-go verdict stands
unchanged because mailbot runs at Cloudflare's edge, so a Coral on Lumen is on the
wrong network side of it; (b) `MODEL_ROUTING_AUDIT.md`'s grep scope should widen,
but by enumeration *source* (process instruments miss off-box components), not by
adding `mailbot/` to a CLI-flag-shaped pattern mailbot never matches.

I read the diff cold (`git show 980ead51c3`, three files: `research/CORAL_NEEDLE_TIER.md`
+109/-2, `research/MODEL_ROUTING_AUDIT.md` +45, new `2661_judgment_wq2623_mailbot_gate_gap.md`
+112) and independently re-derived every factual claim from source rather than trusting
the PR's own numbers. Did not re-implement.

## Per claim

| wq#2661 / PR#1026 claim | Result | Evidence |
|---|---|---|
| `screenRomanticDrift` at `worker.js:323-362`, live call at `:919`, `max_tokens:40`, returns `{"romantic_drift":boolean}` | CONFIRMED | Read function body directly: def at :323, `max_tokens: 40` at the fetch body, boolean type-check before return. `grep -n` on call sites: `:919` is the only live call. |
| `runCritic` at `worker.js:246-306`, live at `:1009` and `:1034`, `max_tokens:300` | CONFIRMED | Read function body directly: def at :246, `max_tokens: 300`. `grep -n` on call sites confirms both `:1009` and `:1034` (regen path). |
| Pricing: `screenRomanticDrift` ≈$0.00016/call, `runCritic` ≈$0.00046/call, ≤$0.00108/inbound worst case | CONFIRMED | Recomputed independently from stated token counts (770 in/10 out; 2065 in/120 out) at deepseek-v4-flash's $0.208/$0.251 per M tokens: 0.0001627 and 0.0004596/call; 1×drift + 2×critic = $0.00108. Matches exactly. |
| Volume ≈15 inbound/day from `mail.db`, graded C, stale (sync inactive since 2026-09-03) | CONFIRMED, with a caveat the PR itself already discloses | `sqlite3 mail.db "select count(*) from messages where direction='in'"` → 33; `min/max(ts)` → 2026-09-01T00:37 to 2026-09-03T02:55 (≈2.09 days) → 33/2.09 ≈ 15.8/day, consistent with "~15/day". Traced the 33 rows to `seed_20260901.py`, a one-off backfill of **real** historical Gmail export messages (not synthetic test fixtures) — legitimate but a 2-day burst around one introduction thread, not a steady-state sample. The doc already grades this C and flags it as stale/order-of-magnitude only; this doesn't change the verdict since even the 100/day stress case stays under threshold. `FLEET_SERVICE_ATLAS.md:294` confirms `lumen-mail-sync.service` is `inactive/dead`. |
| $0.49/mo at ~15/day, $3.3/mo at 100/day, both below the $0.60/mo residual §3 already rejected | CONFIRMED (minor rounding noise, not material) | Recomputed: $0.00108×15×30=$0.486; ×100×30=$3.24. §5's per-gate breakdown (drift $0.07+critic $0.41=$0.48; drift $0.5+critic $2.6=$3.1) uses 2×runCritic/inbound and is internally consistent but ~$0.2 off from §3's combined figures at the 100/day case — cosmetic rounding across two independently-drafted sections, doesn't move the threshold comparison. |
| mailbot is a Cloudflare Worker (edge), disqualifying a Lumen-side Coral regardless of dollars | CONFIRMED | `mailbot/wrangler.toml:1-5`: `main = "src/worker.js"`, `account_id` set, no systemd unit/timer/cron anywhere on Lumen for mailbot. |
| Widening `MODEL_ROUTING_AUDIT.md`'s grep to include `mailbot/` would NOT have caught this — pattern is a CLI-flag shape (`--model opus|fable|...`) mailbot never uses | CONFIRMED | `research/MODEL_ROUTING_AUDIT.md` original grep (pre-diff, referenced at :41-42) targets `--model opus\|fable\|best\|claude-opus\|claude-fable`; mailbot calls `api.deepseek.com` via raw `fetch`, no CLI flag of any kind. |
| `runCritic` fail-closed: missing/malformed verdict blocks the send, same as any flag being wrong | CONFIRMED | `worker.js:364-367` comment + surrounding logic: "a missing/malformed verdict counts as a failure... the draft doesn't ship ungraded." |
| `FLEET_COST_PLAN.md:218` pulls prompt-caching as the lever for `minerva_classifier`, cited as the analogous future lever for mailbot | CONFIRMED | Line 218: `minerva classifier \| deepseek-v4-flash \| glm-5.3-flash + cache \| ... stable system prompt`. |
| `FLEET_COST_PLAN.md` left mailbot-unpriced, deliberately, with follow-up filed as wq#2664 | CONFIRMED | `grep -c mailbot research/FLEET_COST_PLAN.md` → 0 (still zero after this PR). wq#2664 exists, status open, scoped exactly to "re-audit with deploy-manifest + endpoint-string passes, and price mailbot in FLEET_COST_PLAN" — matches the done-notes claim this was deferred, not dropped. |

## Judgment questions re-checked

Both (a) and (b) from wq#2661's own escalation hold up under independent re-derivation:
the doc-revision path taken (CORAL_NEEDLE_TIER.md §0/§2.2/§3/§5/§6/§7 + MODEL_ROUTING_AUDIT.md
§1 caveat/§1.1) is the correct fix, the no-go verdict is correctly unchanged (cost *and*
now a structural edge-vs-Coral disqualifier), and the grep-widening correction is right —
naive `mailbot/` path-scoping would still miss it.

No claim in the diff overstates its evidence grade, no cited line number or code shape was
wrong, and the one deliberately-deferred item (FLEET_COST_PLAN.md) is tracked, not silently
dropped. Nothing here rises to a contradiction; not escalating to opus-5@high.
