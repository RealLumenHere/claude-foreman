# wq#4430 — verify wq#4427 [rotation] burned credential sha256:1a75379451e9 (kv-secret)

**Verdict: CONFIRM.** wq#4427's "not a credential, no rotation" conclusion is correct.
Independently reproduced and corroborated with evidence the original investigation did
not need to reach for (a direct fetch of the real upstream source).

## What wq#4427 claimed

`notes`: "Settled, no rotation: mask_replay 4424 exit 0 (1 value, 0 unaccounted; committed
Go source, alexandrie authentification.go:20). No also-fired runs." Logged at
ISSUE-LOG.md row 1482. No code diff shipped (archetype `rca-investigation`); the only
artifact is the ISSUE-LOG row plus `_runs/wq4427/evidence/attempt-1/replay-AC1.log`.

## Verification performed (fresh, this ticket)

1. **Reran the probe cold**: `python3 /home/lumen/foreman/harness/mask_replay.py 4424` ->
   `wq#4424: 1 credential-shaped values in the run's model context, 0 unaccounted`,
   `[tracked] kv-secret len=20 sha256=1a75379451e9 ... accounted:
   /tmp/fworker-wq4424/alexandrie/backend/middlewares/authentification.go:20`, exit 0.
   Byte-identical to `_runs/wq4427/evidence/attempt-1/replay-AC1.log`.

2. **Checked the probe can fail** (wq#2630 requirement): the same run logged 4 *suppressed*
   kv-secret candidates (`MYSQL_ROOT_PASSWORD`, `RUSTFS_ACCESS_KEY`, `RUSTFS_SECRET_KEY`,
   `JWT_SECRET` placeholders from the compose file) that stayed pointer-only, out of this
   row's scope — confirms the tool distinguishes real hits from noise rather than
   rubber-stamping everything "accounted."

3. **Did not stop at trusting the tool.** `mask_replay.py`'s `committed_text()` reads the
   blob from the *foreign repo's own* HEAD, which for a throwaway `/tmp` clone the agent
   fully controlled is a potential laundering vector (an agent could locally commit a
   fabricated blob in its own scratch clone and have it "self-account"). Cross-checked
   against the real public upstream instead of trusting that local clone:
   - `git ls-remote https://github.com/Smaug6739/Alexandrie.git HEAD` ->
     `32a8c76da1380637cbaec14455761bacde50b6d3` — matches the pinned SHA wq#4424's own
     research doc (`research/WQ4424_ALEXANDRIE_EVAL.md:12`) cites for its shallow clone.
   - Fetched `backend/middlewares/authentification.go` at that exact commit directly from
     `raw.githubusercontent.com` (bypassing the local clone entirely). The function reads:
     ```go
     func Auth(allowNoAuthorization ...bool) gin.HandlerFunc {
         allowNoToken := false
         if len(allowNoAuthorization) > 0 {
             allowNoToken =
             allowNoAuthorization[0]
         }
         return func(c *gin.Context) {
             tokenString, err := c.Cookie("Authorization")
             if err != nil {
                 if allowNoToken {
     ```
     — matches the masked context window's line anchors (`c.Cookie("Authorization")`,
     `if allowNoToken {`, `c.JSON(http.StatusUnauthorized...`) exactly, and matches
     wq#4424's own eval doc line 106, which independently quotes
     `authentification.go:26` for an unrelated finding (cookie-only JWT). Two independent
     citations of the same file agreeing is strong evidence the clone wasn't tampered with.
   - The flagged value is the identifier `allowNoAuthorization` (the variadic parameter),
     immediately followed by `[0]`. `len("allowNoAuthorization") == 20` (matches
     `value_len=20`), and `sha256("allowNoAuthorization")[:12] == "1a75379451e9"` (computed
     fresh via `python3 -I -c`) — exact match to the burned hash. This is the strongest
     possible confirmation: not "the tool says it's accounted" but "the literal flagged
     string, hashed by hand, reproduces the burned hash, and it is a Go parameter name
     indexed into a slice, not a secret."
   - Root cause matches wq#4427's own follow-up note: `allowNoToken` ends in `Token`
     (kv-secret's suffix trigger) and `allowNoAuthorization[0]` sits on its RHS, so the
     rule read a camelCase identifier-then-`[`-index as a key:value credential pair.
     hump-echo (wq#3324) didn't excuse it because its last hump isn't the label noun.

4. **Checked "no also-fired runs"**: `grep -rl 1a75379451e9 /home/lumen/foreman/_runs/`
   returns only `wq4424` (the original fire), `wq4427` (this replay), and `wq4430` (my own
   rerun just now) — no other run tripped on this value. Claim holds.

5. **Checked no code/masker change shipped under cover of "investigation only"**:
   `git -C /home/lumen/foreman log --all --since "2026-10-19 23:05" --until "2026-10-19
   23:15"` shows commits only for wq4424/4425/4426 research docs — nothing for wq4427.
   Matches the notes' "Masker left unchanged."

## Could this have gone wrong?

The one real attack surface — a foreign-repo commit the agent itself controls being
trusted as "committed" — was the thing worth adversarially probing, and it holds up: the
upstream repo is real, public, MIT-licensed, its HEAD today is still the exact pinned SHA,
and the file content at that SHA (fetched independently, not through the agent's clone)
reproduces the burned hash byte-for-byte from a hand-computed sha256. There is no
plausible path by which this was a live credential.

## Disposition

CONFIRM on the board. No escalation, no judgment trigger. ISSUE-LOG row 1482 stands as
written; no correction needed.
