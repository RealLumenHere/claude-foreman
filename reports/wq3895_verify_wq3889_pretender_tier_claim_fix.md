# wq#3895 — verify-pass of wq#3889 fix: TOOL_REGISTRY.md fabricated Pretender tier claim

**Verdict: CONFIRMED.** The fix removes the fabricated `limpet/examples/pretender.toml`
tier/version/gate claim at both cited locations and replaces it with language that
independently checks out against the real repo state.

PR: http://100.121.17.5:3000/lumen/claude-foreman/pulls/1866 (merged as `357e416cf8` on
main, merge commit `e6da98bb`)

## What wq#3889 claimed

wq#3886's verify-pass of wq#3877 found `TOOL_REGISTRY.md:94-96,139` asserted, as present
fact, that Pretender's catalog entry "stays at the community catalog tier
(`limpet/examples/pretender.toml`, wq#3850, version \"0\", unpinned)" and that this toml's
own header gates further promotion — and that none of this (file, tier concept, version,
gate) exists anywhere in the repo. wq#3889's fix replaces both passages with: Pretender's
entry is a JSON row in `tool-catalog.json` per wq#3850, no `limpet/examples/pretender.toml`
exists, and no promotion-tier/gating mechanism exists in the repo at all — while leaving
the NO-GO verdict itself untouched.

## What I checked, independently of the diff's own narration

1. **Read the actual commit diff** (`git show 357e416c -- TOOL_REGISTRY.md`), not the
   ticket's paraphrase of it — 8 insertions/5 deletions across the two cited hunks
   (lines ~94-98 and ~138-143 pre-fix).
2. **Re-ran wq#3886's three falsification angles against the current repo state**, since a
   verify pass must check the underlying facts, not just that the diff matches the ticket
   description:
   - `ls limpet/examples/` → exactly `ghidra-mcp.toml`, `ghidra.toml`, `hackrf.toml`. No
     `pretender.toml`, confirming the new text's claim.
   - `grep -rn -i tier limpet/` → zero hits anywhere in the `limpet/` tree (schema, YAML
     manifests, JSON fixtures, code). `grep -n "version" limpet/registry/schema.py` shows
     only a plain `version` field, no `tier` field — confirms "no promotion-tier/gating
     mechanism currently exists in the repo" is accurate, not just asserted.
   - `wq show 3850` (the ticket that actually populated the catalog) done-notes:
     "10 of the 11 tools were already in `/home/lumen/projects/limpet/tool-catalog.json`
     (... Pretender ...)" and "per-tool plugin manifests (limpet-plugin.toml) ... none
     exist for any existing catalog entry" — matches the new text's "tracked as a JSON row
     in `tool-catalog.json` per wq#3850" exactly, straight from the primary source rather
     than trusting wq#3886's quote of it.
3. **Checked post-fix `TOOL_REGISTRY.md` for every residual trace of the fabricated
   claim** (`grep -n -i "tier\|pretender.toml\|unpromoted\|version \"0\""`): only the new,
   accurate sentences remain at the Pretender section; no stray copy of the old wording
   survives elsewhere in the file.
4. **Looked for a near-miss that would contradict the new text's "no tiering scheme
   exists" claim**: line 222 of the current file has a *different* section (Fuzzware,
   wq#3878/3875, unrelated ticket) saying a bare-metal MCU target would warrant adding
   Fuzzware "as a community-tier `limpet/examples/fuzzware.toml` catalog entry at that
   point, not now" — phrased as a hypothetical future action if a trigger condition is
   ever met, not a present-tense claim that such a file or tier mechanism exists today.
   Confirmed `limpet/examples/` has no `fuzzware.toml` either, consistent with "not now."
   This is out of scope for wq#3889 (a different ticket's section) and does not contradict
   the fixed sentence.
5. **Confirmed the NO-GO verdict's substance is untouched**: the surrounding
   hardware-rig/frozen-toolchain/Conware-overfitting reasoning in the Verdict paragraph is
   identical before and after, only the tier/file sentence was swapped — matches wq#3889's
   scope (narrow, documentation-only, doesn't reopen wq#3877).

## What would have broken this verdict (and didn't)

- If `limpet/examples/pretender.toml` actually existed (e.g. created by a concurrent
  ticket after wq#3886's pass), the fix's "there is no ... pretender.toml" would itself now
  be the fabrication — checked live, it still doesn't exist.
- If any tier/version/gate concept existed elsewhere in the registry code or schema that
  wq#3886 missed, "no promotion-tier/gating mechanism currently exists in the repo" would
  be an overbroad new claim — repo-wide grep for `tier` under `limpet/` is empty.
- If wq#3850's done-notes didn't actually say what wq#3886 quoted, the new text's citation
  would be unsupported — read `wq show 3850` directly rather than trusting the quote.

No escalation to judgment needed — board this as verified.
