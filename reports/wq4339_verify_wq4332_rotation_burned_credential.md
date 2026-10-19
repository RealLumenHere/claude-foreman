# wq#4339 — verify wq#4332: kv-secret sha256:d626f1edf3c2 rotation verdict

**Verdict: CONFIRM**

Author: claude-opus-5-5 (wq#4332). No PR/code diff was shipped — a pure judgment
ticket (run `mask_replay.py`, read evidence, settle in `rotated_ledger.json`).
Verifier: claude-sonnet-5@high (fresh instance, archetype 8 adversarial verify-pass).

## What wq#4332 claimed

`mask_replay.py 4330` flagged one `kv-secret` (prefix `token=`, 56 chars,
sha256:d626f1edf3c2) BURNED in wq#4330's attempt-1 stream-json. Root cause: wq#4330
(standing up Remote Desktop Commander on Lumen) read its own Supabase signup
confirmation email during account creation; the `/auth/v1/verify?token=...&type=signup`
link's token matched the kv-secret pattern. Settled as **NOT ROTATED** in
`rotated_ledger.json`: a one-time Supabase email-confirmation token_hash (DesktopCommander
auth project `olvbkozcufcbptfogatw`), single-use and cleared on verify, replaced
server-side on resend, Supabase caps Email OTP expiry at 86400s — dead by
2026-10-20T03:32:12Z at the latest. Not a fleet credential; nothing to rotate.

## What I checked, independently of the tool's own narration

1. **Re-ran the probe.** `python3 harness/mask_replay.py 4330` from the `foreman`
   checkout (branch `main`, up to date with `origin/main`) reproduces the exact claim:
   "1 credential-shaped values in the run's model context, 0 unaccounted," exit 0, the
   value shown as `[ROTATED]` via `rotated_ledger.json`'s wq4332 entry.
2. **Checked ledger structure for tampering/corruption risk** (wq#3174-class race:
   parallel workers writing the same shared file). `rotated_ledger.json` parses as
   valid JSON, 9 entries, all `value_sha256_12` keys unique, tail entry matches the
   ticket's claimed sha/ticket/note verbatim.
3. **Cross-checked the raw hit record.** `_runs/wq4330/mask-hits-1.json` records two
   hits, both already masked on disk (`token=MASKED`), context
   `https://olvbkozcufcbptfogatw.supabase.co/auth/v1/verify?token=MASKED&type=signup&redirect_to=https://auth.desktopcommander.app`
   — internally consistent with a Supabase email-confirmation link, matching the
   ledger note's domain/project/flow exactly. Did not attempt to reconstruct or
   verify the plaintext value itself (unlike a committed-fixture case, this is a real
   secret-shaped value with no safe plaintext to recompute a hash against — correctly
   handled via ledger settlement rather than pinning a "committed text" fixture, which
   would mean committing a real secret to the repo).
4. **Independent corroboration that the token was actually consumed** (the load-bearing
   factual claim: "single-use, cleared on verify"). wq#4330's own done-notes/report
   (`reports/wq4330_remote_dc_lumen_test.md:9-10`) independently document the signup flow:
   `POST auth.desktopcommander.app/auth/backend/signup` → "confirmation link pulled from
   fleet mail (`mail_recall.py recall desktopcommander`), tokens captured from the
   Supabase 303 redirect fragment" → device verified. This confirms the confirm-email
   link was actually followed (triggering Supabase's redirect and consuming the token),
   not merely assumed dead by expiry — independent of wq#4332's own reasoning chain.
5. **Verified the 86400s Supabase OTP-expiry claim** (prior-art cited as "supabase
   discussion #50775 + auth docs" in kb/ISSUE-LOG.md row 1463) via a fresh
   `fleet-search web` query (not the same phrasing): returned Supabase's own GoTrue
   config source, `if config.Mailer.OtpExp == 0 { config.Mailer.OtpExp = 86400 // 1 day }`
   — confirms 86400s is the real default/cap, not a fabricated figure. Expiry math
   (03:32:12Z + 24h = 2026-10-20T03:32:12Z) checks out.
6. **Checked for other burn sites** (wq#2341: row is keyed by value, not run). Grepped
   `_runs/` fleet-wide for `d626f1edf3c2`: only `wq4330/mask-hits-1.json` and wq#4332's
   own evidence/episode files reference it. No `also fired on wq#N` entries were missing.
7. **Checked for leakage beyond the masked run artifact.** Grepped
   `reports/wq4330_remote_dc_lumen_test.md` for `token=` — no match. The full-length
   token never reached any committed file; the only place it existed was transient
   model context, which is exactly the scope instinct 4 treats as "burned."
8. **ISSUE-LOG bookkeeping.** `kb/ISSUE-LOG.md` row 1463 is filed for this exact
   hash/ticket pair, status `done`, consistent with the ledger entry — no gap found
   (unlike wq#3832's verify-pass, which had to backfill a missing row).
9. **Zero blast radius.** `git status --porcelain harness/masking.py
   harness/mask_replay.py` is empty — wq#4332 touched no masking code, only the
   untracked `rotated_ledger.json` data file and the kb issue log row.

## What would have broken this verdict (and didn't)

- If `mask_replay.py 4330` had reported any unaccounted values → ledger entry would be
  incomplete. It reports 0.
- If the Supabase OTP expiry figure were fabricated → would undercut the "dead by
  2026-10-20" timeline. Independently reproduced from Supabase's own source via a
  differently-worded search.
- If wq#4330's own report hadn't independently corroborated the confirm-link being
  followed → "single-use, cleared on verify" would rest on inference alone. It's
  corroborated by wq#4330's own flow description, written before wq#4332 existed.
- If the full token had leaked into any committed file (report, PR, ISSUE-LOG) →
  would mean real exposure beyond transient model context. Grepped and found none.
- If another run anywhere in `_runs/` had printed the same value → wq#4332 would have
  missed a burn site per wq#2341. None found.

## Scope note

wq#4332 shipped no diff/PR — a pure judgment ticket. The verify surface is "is the
stated evidence true, and was the required bookkeeping done," checked above via paths
independent of wq#4332's own narration (wq#4330's prior, independently-written report;
a fresh web search on the expiry figure; a grep for other burn sites and for leakage).
No contradiction found; no escalation to opus-5@high needed. Board this as verified.
