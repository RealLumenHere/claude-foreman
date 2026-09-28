# wq#2855 — verify-pass of wq#2827 (PR#1170): 5 leaked LiteLLM provider keys, accept-as-is

**Verdict: CONFIRM.** wq#2827's disposition (`reports/2827_provider_key_disposition.md`,
commit 77d3e8b, PR#1170) is a docs-only security disposition — no code shipped, nothing to
re-run as a functional probe. Verification here is evidentiary: trace every load-bearing
claim in the report back to its cited source and check it holds.

## What was verified

1. **Ticket-to-report fidelity.** `wq show 2827`'s original hashes (`18006fca7ab2`,
   `8ebbeceb577e`, `005ffd75c2fa`, `861f0936375d`, `92df3a66daaf` for Cerebras/DeepSeek/
   Gemini/Mistral/OpenRouter respectively) and secondary findings (`LITELLM_MASTER_KEY`
   `1bd4635b5896`, `ghp_` hashes `a3b99d59dbb0` and `76b4100ba253`) all appear correctly
   and completely in the shipped report. No cherry-picking or dropped findings.

2. **Containment claim.** `reports/2806_config_shape_mask.md:75-79` independently
   confirms the source log was scrubbed in place at 10:06Z and the sweep over
   `/home/lumen/logs` is `distinct_values=0`, and states the exposure as "0600 lumen
   file" — matches the wq#2827 report's containment argument verbatim rather than
   introducing a new unverifiable claim.

3. **Per-vendor rotation-path claims**, checked against `research/VENDOR_KEY_ROTATION.md`:
   - OpenRouter: confirmed has a Provisioning Key API (`VENDOR_KEY_ROTATION.md:64-71`),
     requiring a one-time operator dashboard step never done.
   - DeepSeek/Mistral/Cerebras: confirmed "no public key-management API" per
     `VENDOR_KEY_ROTATION.md:77-99`.
   - Gemini: confirmed self-service rotation requires prior migration to a GCP-managed
     key, not done (`VENDOR_KEY_ROTATION.md:106-118`).
   All five match the report's claim that no no-human rotation path exists today.

4. **wq#1972 verify-pass characterization.** The wq#2827 report cites wq#1972 as
   confirming "the vendor-API findings are accurate — only a citation and file-path
   defect were contradicted." Checked `reports/1972_verify_wq1970_vendor_key_rotation.md`
   directly: its verdict line is in fact **CONTRADICT**, but the contradiction is scoped
   to a fabricated `GITEA_TOKEN` citation and a file-path/location defect
   (`1972...md:3-89`); the same report explicitly credits the doc's "technical content...
   elsewhere (OpenRouter provisioning keys, Cloudflare...)" as sound. wq#2827's summary is
   accurate, not a misreading of a CONTRADICT verdict into a false CONFIRM.

5. **wq#2756 precedent (Langfuse rotation) as the contrast case.** Confirmed
   `reports/2756_rotation_triage.md:13-51` describes a fully API-drivable rotation
   (Postgres delete + Langfuse web-init key creation), and separately confirms
   `1bd4635b5896` as the dead May-2026 `LITELLM_MASTER_KEY` placeholder and
   `a3b99d59dbb0` as a benign test artifact from wq#2732's mask-secrets glue-detection
   test. Independently recomputed the hash claim: `sha256(b'A'*36).hexdigest()[:12] ==
   'a3b99d59dbb0'` — matches, confirming the hash-confirm methodology this whole chain
   relies on is sound, not just asserted.

6. **Physical-task queuing (instinct 3 compliance).** `ghp_ 76b4100ba253` is unresolved
   (token-engine store unreadable from Lumen) and correctly not treated as a blocking
   gate: wq#2827 closed `done`, and a companion ticket wq#2849 was filed
   (`status: frank_gated`, `gate_type: frank_decision`, explicit `[NO-PROBE: ... require
   Frank's own provider/account access]` note) carrying the hand-rotation hygiene, the
   OpenRouter provisioning-key one-time setup, and the `ghp_` token-engine check. This is
   the correct pattern — decide and record, queue the human-only step, don't block on it.

7. **Report placement.** `reports/2827_provider_key_disposition.md` lives under
   `reports/`, not the repo root (wq#2805 lesson respected).

## What I tried to break

- Checked whether the "no incremental exposure" claim rests on anything beyond the
  0600-file assertion already made by wq#2806 (it doesn't — no new unverifiable premise
  introduced).
- Checked whether wq#1972's CONTRADICT verdict was being silently laundered into a false
  confirmation — it wasn't; the report's phrasing precisely matches the scope of what was
  and wasn't contradicted.
- Recomputed one of the chain's hash claims independently rather than trusting the prior
  report's arithmetic.
- Confirmed the "not executable from this sandbox" claim about `/home/lumen/agent/` by
  attempting the same directory access from this verify worktree — also blocked, which is
  consistent with (not just asserted by) the original report.

No contradiction found. Nothing escalates to opus-5@high judgment.

## Evidence index

- `agents/work-queue` ticket text: `wq show 2827`, `wq show 2849`
- `foreman/reports/2827_provider_key_disposition.md` (commit `77d3e8b`)
- `foreman/reports/2806_config_shape_mask.md:75-79`
- `foreman/research/VENDOR_KEY_ROTATION.md:35-118`
- `foreman/reports/1972_verify_wq1970_vendor_key_rotation.md`
- `foreman/reports/2756_rotation_triage.md:13-51`
- `foreman/kb/ISSUE-LOG.md` row 871
