# wq#2661 — judgment: wq#2623 Coral needle-tier scope, missed mailbot gates

Ruling on the two questions wq#2625's verify-pass escalated after CONTRADICTING
PR#984 / `ab4dded00f`. Written 2026-09-25.

## The escalation was correct on fact

Re-verified independently, not taken on the ticket's word:

| Claim | Status | Evidence |
|---|---|---|
| `screenRomanticDrift` is a live per-inbound binary model gate | **confirmed** | `mailbot/src/worker.js:323-362`; called at `:919` in `romanticDriftHint(...) \|\| (await screenRomanticDrift(...)) !== false` |
| `runCritic` is a live 6-boolean model gate | **confirmed** | `worker.js:246-306`; called at `:1009` and again at `:1034` after regen |
| both call `api.deepseek.com/chat/completions`, `model=deepseek-v4-flash` | **confirmed** | `worker.js:252-268`, `:326-342` |
| both return boolean JSON verdicts, not open generation | **confirmed** | `response_format:{type:"json_object"}`, `max_tokens` 300 / 40, boolean type-checks at `:288-292` and `:353` |
| zero `mailbot` hits in `CORAL_NEEDLE_TIER.md`, `MODEL_ROUTING_AUDIT.md`, `FLEET_COST_PLAN.md` | **confirmed** | `grep -rn mailbot` over all three, exit 0 hits |
| `screenRomanticDrift` is shape-wise a stronger EdgeTPU candidate than the §6 LPP pilot | **confirmed** | it is already the binary classifier §6 steps 1-3 would have to *build* out of the LPP extractor |

One correction to the escalation's diagnosis. It attributes the miss to
`MODEL_ROUTING_AUDIT.md:12`'s grep scope (`agents/ services/ agent/ bin/ minerva/`,
actually at `:41-42`) omitting `mailbot/`. Adding `mailbot/` to that list would not have
caught it: the grep's *pattern* is `--model opus|fable|best|…`, a CLI-flag shape mailbot's
source never contains. The audit's surface list came from `systemctl list-unit-files`, the
timer roster and `crontab -l` — all on-Lumen process instruments — and mailbot is a
Cloudflare Worker (`mailbot/wrangler.toml:1-5`) with no unit, timer or cron entry. The gap
is the enumeration *method*, not a directory list. This matters for (b).

Also found while verifying: mailbot makes two further DeepSeek calls per exchange,
`generateReply` (`worker.js:176`) and `generateNotesUpdate` (`worker.js:576`). Generative,
so not needle-tier candidates — but they dominate mailbot's real spend and are equally
unpriced anywhere.

## (a) Doc revision required; fleet-wide no-go stands unchanged

**Both.** The completeness claim was false and is now repaired; the verdict it supported
survives the repair on two independent grounds.

*Cost.* Priced for the first time in `CORAL_NEEDLE_TIER.md` §3: ≤$0.00108 per inbound
email with every cap saturated and every optional call firing (drift screen ≤770 in / ~10
out; critic ≤2065 in / ~120 out, up to 2×, at $0.208/$0.251 per M). At the ~15 inbound/day
the local D1 mirror shows that is **~$0.49/mo**; at a deliberately generous 100/day,
**~$3.3/mo**. Either figure sits below the $0.60/mo residual §3 already ruled too small to
justify a label-train-quantize-compile-retrain pipeline. Volume is grade C — the mirror
(`mailbot/mail.db`) stopped at 2026-09-03 and `lumen-mail-sync` is inactive
(`FLEET_SERVICE_ATLAS.md:294`) — but the conclusion needs two orders of magnitude of
traffic growth to move, and `worker.js:204-205` calls the inbox low volume in its own
comment.

*Architecture — the stronger ground, and the one nobody had.* mailbot executes in a
Cloudflare Worker isolate at the edge. The needle-tier premise is a USB Coral attached to
Lumen. Serving these gates from that Coral means the Worker round-trips to Lumen instead
of to `api.deepseek.com`: one remote call swapped for a worse one, plus a new hard
dependency on a single self-hosted box for a mail path that currently survives Lumen being
down. No volume or price makes that arithmetic work. This is a fourth disqualifier class
the original doc did not have, now recorded as §0(d) and §2.2.

So the escalation's own hedge — "does not necessarily flip the fleet-wide no-go" — was
right, and for a firmer reason than the volume guess it offered.

On the drift screen specifically: it is the best-shaped candidate on the fleet and it is
still not a pilot, because it loses the two things that made LPP's persona pre-filter
*conditional* rather than flat no. LPP's payoff is latency on an `await`ed live SAY path
with a human waiting (`agent.ts:1367-1371`); mailbot's drift screen sits in an async email
reply that spends 1600-2600 output tokens on `generateReply` right after it, so a saved
round-trip is imperceptible. And LPP runs on Lumen, where a Coral is a local call. Better
shape, no payoff — recorded in §6 so the next reader does not re-derive the shape argument
and conclude it was overlooked.

**Shipped:** `research/CORAL_NEEDLE_TIER.md` — revision banner; §0 gains disqualifier (d);
§2's second table gains both gates; new §2.2 (enumeration gap + the edge-vs-Coral
disqualifier); §3 gains the pricing table and volume caveat; §5 gains two scored NO-GO
rows; §6 gains the why-not-a-second-pilot note; §7 marks mailbot volume explicitly *not* a
revisit condition and names prompt caching on `CRITIC_SYSTEM` as the real lever if cost
ever matters.

## (b) Yes — widen, but by enumeration source, not by directory

Adding `mailbot/` to the §1 grep is the wrong fix for the reason above, and would leave the
class of gap wide open: anything that calls a provider HTTP endpoint directly is invisible
to a CLI-flag pattern regardless of which directories it is pointed at, and anything
deployed off-box is invisible to systemd/cron enumeration regardless of the grep.

Ruled: the next audit enumerates from **deployment manifests plus endpoint strings**, not
from the local process table. Two added passes — (i) every `wrangler.toml`/deploy manifest
under the registered primaries (`UID_SPLIT_FWORKER.md:126` already maintains the list),
(ii) a content grep for `api.deepseek.com`, `api.anthropic.com`, `api.openai.com`,
`generativelanguage.googleapis.com`, `@anthropic-ai/`, `openai` across those same roots —
and §1 states its enumeration scope explicitly so a future reader can see the boundary.

Worth flagging beyond the ticket: mailbot reaches DeepSeek with a worker-held key, not via
LiteLLM and not via the `claude` CLI, so it is outside the gateway's usage accounting too.
Anything built on gateway telemetry is blind to it by the same mechanism.

**Shipped:** `research/MODEL_ROUTING_AUDIT.md` — scope caveat under §1's enumeration
paragraph, and new §1.1 recording the missing surface, the method root cause, why the
obvious grep-widening fails, that `model_routing_audit.py` enumerates nothing (so §1's list
was never reproducible the way §2's numbers are), and the fix. Filed **wq#2664** for the
re-audit itself plus the missing `FLEET_COST_PLAN.md` mailbot row — the audit's numeric
tables are a 2026-08-22 measurement this ticket cannot honestly re-run, so the interim
caveat ships and the re-measurement is ticketed.

## Not done

- Live D1 volume for mailbot. The local mirror is stale; reading live D1 needs Cloudflare
  credentials, which instinct 4 and the guard hook put out of reach here. §3 carries the
  caveat and wq#2664 carries the task.
- `research/FLEET_COST_PLAN.md` is untouched. Its rows are priced from measured spend; a
  mailbot row built on a stale-mirror volume estimate would look like a measurement and
  not be one. Ticketed on wq#2664 instead.
- wq#2625 itself is still open with its verify doc (`2625_verify_wq2623_mailbot_gate_gap.md`)
  uncommitted — not present in the foreman repo or any branch reachable here. This ticket
  re-derived the evidence from source rather than depending on it.
