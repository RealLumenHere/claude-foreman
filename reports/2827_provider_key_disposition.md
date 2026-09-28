# wq#2827 — 5 live LiteLLM provider keys in the pre-pipe DC log: disposition

Decision (per the wq#2764/wq#1970/wq#2756 convention — hash-confirm the value, then
weigh real exposure against whether rotation is actually executable, not blanket-rotate
on every live-value match): **ACCEPT-AS-IS. No rotation performed.**

## Why accept, not rotate

1. **No incremental exposure.** The five values (Cerebras/DeepSeek/Gemini/Mistral/
   OpenRouter) live in a 0600 `lumen`-owned file (`agent/litellm_config.yaml`) on the
   same host as the log that leaked them. Anyone with read access to the log already had
   read access to the config it was cat'd from — the leak added no new reader. wq#2806's
   own sweep (`reports/2806_config_shape_mask.md:61-79`) confirms the source
   (`supergateway/desktop-commander-mcp.log.1`, pre-wq#2546 pipe) was scrubbed in place
   at 10:06Z and the all-rules sweep over `/home/lumen/logs` is now `distinct_values=0`.

2. **No automated rotation path exists for any of the five today.** Per
   `research/VENDOR_KEY_ROTATION.md` (wq#1970) and its verify-pass
   (`reports/1972_verify_wq1970_vendor_key_rotation.md`, which confirms the vendor-API
   findings are accurate — only a citation and file-path defect were contradicted, not
   the technical content):
   - DeepSeek, Mistral, Cerebras: dashboard-only key management, no create/revoke API
     documented for any of the three (`VENDOR_KEY_ROTATION.md:77-99`).
   - OpenRouter: has a Provisioning-Key API (`POST/GET/DELETE /api/v1/keys`), but it
     needs a one-time Frank dashboard step (mint the Provisioning Key itself) that was
     never done — wq#1970 shipped as a research doc only, no orchestrator script exists
     (`VENDOR_KEY_ROTATION.md:64-75`, confirmed nothing built in wq#1972's verify-pass).
   - Gemini: self-service rotation only exists if the key is migrated from a bare AI
     Studio key into a GCP-managed API key; that migration was never done
     (`VENDOR_KEY_ROTATION.md:104-118`).
   Rotating "at the providers" for any of these five means a human dashboard visit —
   the fleet's own convention (`VENDOR_KEY_ROTATION.md:219-231`, recommendation #2) is
   to leave dashboard-only vendors on calendared manual rotation rather than forcing a
   reactive one on every contained-exposure finding.

3. **Not executable from this sandbox regardless.** This build worker's worktree/
   `--add-dir` set does not include `/home/lumen/agent/` (confirmed: not present in the
   session's allowed-directories list), so even the config-file half of "rotate +
   update `agent/litellm_config.yaml` + reload" cannot be carried out from here — matches
   `VENDOR_KEY_ROTATION.md:18-22`'s prior finding of the same boundary.

## Contrast with the one case that WAS rotated (wq#2756)

`reports/2756_rotation_triage.md:24-51` rotated a live Langfuse key found the same way
(matched `litellm_config.yaml`, found in the clear in a supergateway log snapshot)
because that rotation was **fully API-drivable end-to-end** — a Postgres delete plus
Langfuse's own web-init key-creation flow, no dashboard visit, no Frank dependency. The
five keys here fail that test: none of the five has an equivalent no-human path today.
That is the actual decision rule, not "matched a live file => rotate."

## Other items named in the ticket (already resolved, no new action)

- `LITELLM_MASTER_KEY` (`1bd4635b5896`): already identified as the stale May-2026
  placeholder, distinct from the current live key
  (`reports/2756_rotation_triage.md:19`, `claude_memory.md`'s "CURRENT STATE OVERRIDES —
  2026-05-13" section). Confirms the ticket's own note ("not the agent/.env one").
- `ghp_` token `a3b99d59dbb0`: already resolved as benign, an exact reconstruction of a
  test one-liner (`'ghp_' + 'A'*36`) from wq#2732's own mask-secrets probing
  (`reports/2756_rotation_triage.md:14`).
- `ghp_` token `76b4100ba253`: **not resolved** — distinct hash from the one above, no
  matching source found in this sweep. The token-engine store that could confirm or
  deny it against real live tokens is unreadable from Lumen (ticket's own note). Queued
  for operator check below rather than guessed.
- `GITEA_TOKEN`/`GITEA_PASSWORD` values not matching any live file: no live credential,
  no action.

## Physical task queued (not a gate — instinct 3)

Filed for Frank, `--frank-gated`, no-probe (dashboard-only, no deterministic check
exists): hand-rotate the five provider keys as calendar hygiene whenever convenient
(exposure is already contained, so this is hygiene, not urgent), do the one-time
OpenRouter Provisioning Key setup so a future OpenRouter leak is self-service per
`VENDOR_KEY_ROTATION.md:64-75`, and check `ghp_` hash `76b4100ba253` against the
token-engine store.
