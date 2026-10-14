# wq#3899 — verify wq#3884: Limpet plugin manifests + url/license fill

Archetype 8 adversarial verify-pass. Author: claude-sonnet-5. Verifier: claude-sonnet-5@high
(fresh instance). PR: http://100.121.17.5:3000/lumen/claude-foreman/pulls/1876 (merged
539f6bffdc, now `e1276b32` on main / this worktree).

## Liveness probe

Ran the ticket's exact probe against the live file:

```
python3 -c "import json;d=json.load(open('/home/lumen/projects/limpet/tool-catalog.json'));
assert all(t.get('url') for t in d['tools'] if t['name'] in
('HALucinator','FirmWire','fwtriage','IFDA','LLM4Decompile','Netzob','spreadtrum_flash'))"
```

**PASS.** All 7 previously-null entries now carry a `url`.

## Claims checked independently

1. **7 null catalog entries filled (url/license/stars).** Printed each entry from
   `/home/lumen/projects/limpet/tool-catalog.json`: all 7 have `url` + `license`; 3
   (FirmWire, LLM4Decompile, Netzob) have `stars=None`, matching the disclosed blocker
   (ISSUE-LOG row 1313: `fleet-search github` 401, ecosyste.ms 402). Not a gap the PR
   hid — it's logged and the probe never asked for stars.

2. **fwtriage license correction (Apache-2.0 → MIT).** Fetched
   `raw.githubusercontent.com/taffy210/fw-triage/main/LICENSE` directly: HTTP 200,
   **1068 bytes** — matches stock MIT license size (~1.0–1.1 KB), not Apache-2.0
   (~11.3 KB). Corroborates the correction without trusting the PR's own say-so.
   `fleet-search web` also turned up the real repo (taffy210/fw-triage, cited in an
   unrelated firmware-teardown blog post), confirming the URL isn't fabricated.

3. **LLM4Decompile license ("MIT + DeepSeek License").** `fleet-search web` returned a
   GitHub snippet verbatim: *"This code repository is licensed under the MIT and
   DeepSeek License."* Matches exactly.

4. **IFDA URL/existence.** `fleet-search read https://github.com/cn0xroot/IFDA` returned
   a live (cached from 2026-10-14T19:57:32Z — i.e. the original author's own fetch, ~6 min
   after their `claimed_at`) 430KB page describing exactly the IoT firmware RE/vuln-discovery
   tool the catalog entry claims. Real, not guessed.

5. **total_tools 147→104 reconciliation.** `tool-catalog.json.total_tools_note` present
   and cites `design/wq3806_limpet_architecture.md §1.3` for "147 was a target, not a
   count." `total_tools=104` and `len(tools)==104` — consistent.

6. **11 manifests validate via `manifest.parse_manifest`.** Independently ran
   `limpet/manifest.py:parse_manifest` against all 14 `limpet/examples/*.toml` (11 new +
   3 pre-existing: ghidra, ghidra-mcp, hackrf) — **all 14 parse clean**, no
   `ManifestError`.

7. **Manifest ↔ catalog consistency.** Cross-checked `license` and `source.git` on all
   11 new manifests against the corresponding `tool-catalog.json` entries:
   - License: 11/11 match (including the 3 NO-GO tools correctly marked
     `license = "unverified"` where the catalog has `null`).
   - URL: 10/11 match. **pretender.toml** disagrees with the catalog
     (`ucsb-seclab/pretender` in the manifest vs. `mit-ll/pretender` in the catalog) —
     but this is **disclosed inline** in the manifest's own `[source].note`: "catalog's
     existing url field ... was not independently re-verified or changed by this
     ticket -- out of wq#3884's 7-tool scope; flagging the discrepancy for a follow-up."
     Not a hidden defect; the PR surfaces it and correctly leaves it alone (Pretender
     wasn't one of the 7 null-field tools in scope).

8. **NO-GO `[adoption]` tables (Fuzzware/Pretender/Sootty).** All three carry
   `[adoption] status = "no-go"` with a `ticket` and `reference = "TOOL_REGISTRY.md"`.
   Checked `TOOL_REGISTRY.md`'s Fuzzware section: it does document `fuzzware -h` /
   `fuzzware <util> -h` as real CLI entrypoints (sourced from the upstream README) — so
   fuzzware.toml's `[health] command = "fuzzware -h"` isn't fabricated, just unexecuted
   on this fleet (tool isn't installed, consistent with NO-GO).

9. **ISSUE-LOG entries 1313/1314.** Both rows exist verbatim as described in the PR's
   done-notes (`grep -n "^| 1313 |\|^| 1314 |" kb/ISSUE-LOG.md`): 1313 is the
   fleet-search/ecosyste.ms star-count blocker, 1314 is the `registry.py`/`registry/`
   package-name collision in `limpet/cli.py` worked around by importing
   `manifest.parse_manifest` directly.

## Minor observation (non-blocking)

`fuzzware.toml` is the only one of the 3 NO-GO manifests that *does* carry a `[health]`
block, while `pretender.toml`/`sootty.toml` explicitly omit theirs with a comment citing
the principle "fabricating a health command for a tool nobody has run here would be
guessing, not verifying." Fuzzware's command is real (confirmed in TOOL_REGISTRY.md,
point 8 above) and the plugin is also marked `status = "no-go"`, so there's no
functional harm — but it's a small inconsistency in how the same principle was applied
across the three NO-GO files. Not worth a redispatch; noting for anyone touching these
manifests next.

## Verdict

**CONFIRM.** Probe passes. Every substantive claim in the done-notes (catalog fills,
license correction, manifest validation, NO-GO adoption tables, ISSUE-LOG rows) checked
out against independent evidence (live LICENSE fetch, live GitHub page fetch,
`fleet-search` snippets, direct `manifest.parse_manifest` runs, grep of ISSUE-LOG) rather
than re-trusting the PR's own notes. No contradiction found; no escalation to opus-5@high.
