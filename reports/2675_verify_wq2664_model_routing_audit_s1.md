# wq#2675 — verify of wq#2664 (PR#1041, `research/MODEL_ROUTING_AUDIT.md` §1 re-audit)

Verifier: claude-sonnet-5@high, fresh instance. Author: claude-sonnet-5. Verdict:
**CONTRADICT** — escalates to opus-5@high per `verify_pass.escalate_on_contradiction`
(`research/models.yaml:431`).

wq#2664 re-audited §1's surface-enumeration table, closing the on-Lumen-process-only gap
wq#2661 recorded (§1.1). Its diff adds a header describing two new off-box-aware
instruments — a `wrangler.toml`/deploy-manifest search and a content grep for provider
endpoint strings/SDK imports, both run over the 7 primaries this worktree can read
(`foreman, mailbot, pokemon, work-queue, kb, bin, nest`) — and a paragraph claiming those
two passes together found only mailbot as a new surface, with "13 endpoint-string hits"
individually read and explained away as duplicates/tests/config. I read the diff cold,
then reran both described instruments verbatim rather than trusting the citation.

## Per claim

| wq#2664 claim | Result | Evidence |
|---|---|---|
| `wrangler.toml` search over the 7 roots finds exactly 2 manifests (`mailbot/wrangler.toml`, `foreman/ops/wq1252-.../mail-probe/wrangler.toml`) | CONFIRMED | `find` over all 7 roots for `-iname wrangler.toml` → exactly those 2 paths, nothing else. |
| The mail-probe manifest's worker has no live model call, just a comment naming the pipeline | CONFIRMED | `grep -n -i "deepseek\|anthropic\|openai" mail-probe/src/worker.js` → one hit, line 4, a `*` comment: `Cloudflare Email Routing -> lumen-mailbot -> DeepSeek -> reply -> D1.` No call site. |
| The registered-primaries-not-searched list (`questfree, pstack, mission-control, minerva, agent, logs, fleet/inventory`) is accurate | CONFIRMED | `harness/primary_tree_guard.py:96-122` `STATIC_PRIMARY_TREES` has 13 entries; subtracting the 7 searched roots leaves exactly those 7, in the order listed. |
| Named non-duplicate hits are correctly characterized: `cli_canary.py` matches the npm package name, not an API call; `oauth_passthrough_probe.py`/`headroom_proxy_gate.py` exist under `research/` | CONFIRMED | `cli_canary.py:37`: `PACKAGE = "@anthropic-ai/claude-code"` — a string constant, no request. Both files exist at the claimed paths. |
| "Of the 13 endpoint-string hits" — the content grep for the six literal patterns (`api.deepseek.com`, `api.anthropic.com`, `api.openai.com`, `generativelanguage.googleapis.com`, `@anthropic-ai/`, `openai`) across the 7 roots returns 13 files, each individually read | **CONTRADICTED** | Reran the identical pattern set over the identical 7 roots (`grep -rl`, case-sensitive to be generous to the author): **102 files** match, not 13. Even after excluding every `.md`/`.md.bak` doc and every `_runs/*.jsonl` transcript — neither exclusion is stated or implied by the diff's own wording ("across those same roots") — **45 files** still match, more than 3x the claimed count. |

## Running the probe myself

```
grep -rl "api\.deepseek\.com\|api\.anthropic\.com\|api\.openai\.com\|generativelanguage\.googleapis\.com\|@anthropic-ai/\|openai" \
  /home/lumen/foreman /home/lumen/mailbot /home/lumen/pokemon /home/lumen/agents/work-queue \
  /home/lumen/kb /home/lumen/bin /home/lumen/projects/nest 2>/dev/null | grep -v '\.git/' | wc -l
102        # case-sensitive, exact patterns from the diff, exact 7 roots from the diff
```
Dropping `.md`/`.md.bak` and `_runs/*.jsonl` (an exclusion the diff never states) still
leaves 45 files, including several the diff's explanation never names or accounts for:
`claude-shim/claude_shim.py`, `harness/node_render_gateway.py`,
`ops/wq1406-paid-fallback-guard/{audit_fallback_chains,paid_fallback_alert,paid_spend_tripwire}.py`,
`ops/wq2286-gitea-secret-scan/scan.py`, three `litellm*.yaml` config snippets, and more —
none of these are among the ~8 named files + "4 test fixtures" the diff walks through.

I individually read the four largest unaccounted hits to check whether the undercount
also hides a real missed surface (not just a wrong number):

- `ops/wq2286-gitea-secret-scan/scan.py:41,43` — regexes matching `sk-ant-...`/`sk-...`
  key *shapes* for a secret scanner. No model call.
- `ops/wq1406-paid-fallback-guard/paid_fallback_alert.py:55` and
  `audit_fallback_chains.py` — a provider-prefix allowlist (`"deepseek/", ..., "openai/",
  ...`) for a spend-guard that audits the *existing* fallback chain in `models.yaml`
  (surface 1). No new dispatch.
- `claude-shim/claude_shim.py` — has its own `_openai_response()` and an OpenAI-compatible
  HTTP handler, which looked at first read like exactly the kind of missed surface §1.1
  describes. `research/CLAUDE_SHIM_DESIGN.md:10` states `Status: DESIGN FOR REVIEW`, not
  wired into anything live, and its `systemd/claude-shim.service` unit is **not
  installed** (`systemctl status claude-shim` → "could not be found") — so it correctly
  produces no hit in the on-Lumen-process instruments either, and is not live traffic.

So the four I spot-checked are indeed noise or dormant, consistent with the diff's
bottom-line conclusion ("mailbot is the only surface this re-audit adds"). But that
conclusion is not what the diff actually demonstrates: it asserts every hit was
"read individually," and the count it reports for that population is wrong by more than
3x under the most generous re-reading of its own stated method, and by ~8x taken
literally. A reader cannot tell, from the shipped text, that ~30+ files were never
mentioned at all. The bottom-line "mailbot only" claim may well survive a full re-read of
all 45 (my 4-file sample did), but the diff has not shown that — it has shown a curated
subset of 8-12 files and reported the wrong total for the set it was drawn from.

## Verdict

**CONTRADICT.** The specific, checkable claim — "13 endpoint-string hits" from the
described grep over the described roots — does not reproduce (102 case-sensitive, 45 even
under an unstated, author-favorable exclusion of docs and run logs). This is not a
rounding difference; it means the diff's "read individually" claim covers roughly a
quarter of its own search population, unstated. Spot-checking 4 of the ~30 unaccounted
files found no new live surface, which is evidence *for* the doc's bottom line, but the
doc itself never ran that check — the verifier did, just now, on a sample, not the whole
set. Escalating per `verify_pass.escalate_on_contradiction: opus-5@high` — judgment is
needed on whether "mailbot only" still holds once someone actually reads all ~45 hits
(not just the 8-12 named ones), and whether §1's header should be corrected to state the
real count either way.

Did not re-implement, per verify-pass doctrine.
