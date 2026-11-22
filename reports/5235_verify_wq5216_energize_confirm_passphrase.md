# wq#5235 — verify wq#5216 (energize-confirm mint needs a passphrase, not a TTY)

Verdict: **CONFIRM.** The fix holds against the ticket's attack and every forgery I tried. It has four
residuals, none of which is a regression or contradicts the shipped claim. They are listed below.

Diff under test: foreman `37b51ed5` (PR #2701) + `b49a138e`/`1d5bb0e4` (PR #2703). The deployed
`/home/lumen/foreman/harness/energize_confirm.py` on `main` (`e236b8d0`) is the same file as at `1d5bb0e4`:
`git diff --stat 1d5bb0e4 HEAD` on that path is empty, and the last commit that touched it is `37b51ed5`.

## Probe (rev-pinned, able to fail)

I extracted the module at two revisions with `git show <rev>:harness/energize_confirm.py`:
`1d5bb0e4` → `ec_fixed.py` and pre-fix `4b2e86a9` → `ec_prefix.py`. `drive.py` loads one copy, points
its dirs at a temp workdir and runs its CLI. `run_script1.py` runs that under a real
`script -qec ... /dev/null` pty. It waits for each prompt before typing, because getpass's TCSAFLUSH
throws away anything typed ahead. Without that wait, the first run reported a false "EOFError, nothing
minted": the passphrase was never delivered. The scripts are in `reports/5235_verify_wq5216_probe/`.

| case | module | keystrokes | result |
|---|---|---|---|
| ticket attack, no passphrase (negative control) | pre-fix 4b2e86a9 | none | `RC 0 MINTED 1`: the probe can fail |
| script(1) + guessed passphrase | fixed 1d5bb0e4 | wrong line | `wrong passphrase, no token minted`, `RC 1 MINTED 0` |
| script(1) + Frank's passphrase (positive control) | fixed | right line | `RC 0 MINTED 1` |

`forge.py` assumes an attacker who can read everything: the probe verifier.json (salt, params, pub)
plus one genuine signed record. Each forgery below returned **False** from `verify_token`:
- an unsigned pre-fix record
- a v2 record with a random signature
- a genuine record copied under a token name the attacker chose (the signature binds sha256(token))
- an s+Q malleated signature
- an identity R with s=0
- a record signed by the key derived from a guessed passphrase with the stored salt and params
- a float expiry
- a bool expiry
- a capability other than the one minted

Further checks:
- A group-writable verifier makes `issue` refuse.
- Positive control: an API mint followed by verify returns True. A replay of the same token returns False.
- No stray token files are left behind.

## Tests

- System python3, `harness/tests/test_energize_confirm.py test_action_gate.py test_guard.py`: **389 passed** (125.8s, no skips). These include the repo's own script(1) tests: no passphrase, the ticket's exact command, and the positive control.
- `ops/wq3882-limpet-flashrom-mcp-a1/tests`: 24 passed.
- `ops/wq5139-serial-maksoor-guard`, run under the guard venv through `lumen-run start` (unit lumen-job-job-1795326504): **11 passed**. Under system python3 the same 7 tests fail with fastmcp/mcp ImportError. That is the environment (ISSUE-LOG 1659/1695), not the diff.
- The live guard denied my own read-only `ls` of the two energize-confirm dirs: `guard.sh: BLOCKED — denied: energize-confirm ... (Frank-only, wq#5216)`. The deny rule is active.

## Residuals (not contradictions)

1. **Anyone can claim the first passphrase before Frank sets it.** With no verifier.json present,
   `set-passphrase` asks for no current passphrase. Under script(1) I ran
   `ec_fixed.py <tmp> set-passphrase`, then `issue`, with an agent-chosen phrase: `set` was accepted and
   `RC 0 MINTED 1`. Rotation with a wrong current passphrase is refused (`verifier unchanged`). This
   gives an attacker nothing beyond the documented residual: lumen owns `~/.config` and can write
   verifier.json directly (docstring lines 35-39). It is also a designed CLI path, though, and its
   "no verifier" error message names that path. The window closes when Frank completes wq#5233
   step 1. One hardening option: pin the verifier pubkey in a committed file, so swapping the key
   needs a visible PR.
2. **The guard and action_gate denies match by name** (`energize_confirm(.py) issue|set-passphrase`,
   dir strings). A renamed copy or a `$VAR` path gets past them; my probe ran `ec_fixed.py ... issue`
   without being blocked. These denies are defense in depth only. The passphrase is the real control,
   and it held.
3. **Concurrent double-use of one token (TOCTOU, pre-existing).** `verify_token` reads the record,
   then removes it. Two simultaneous verifies of the same token can both read before either removes,
   so both succeed. Exploiting this needs a Frank-minted token in hand, so impact is low.
4. **`ENERGIZE_CONFIRM_SCRIPT` env (pre-existing, out of scope).** The flashrom servers
   (`plugins/limpet-plugin-flashrom/mcp/server.py:31`, `ops/wq3882-.../flashrom_ops.py:33`) still let
   this variable choose which verifier script runs. The MCP launch config sets it, not an agent tool
   call. wq#5216 only asked to drop the token-dir override, and it did: the only remaining
   `ENERGIZE_CONFIRM_DIR` is in the test that proves the variable is ignored.

Deploy state: per ISSUE-LOG 1695, wq#5233 has already rerun serial-maksoor install.sh (VERIFY OK). The
passphrase step needs Frank and is still open, and until he does it every energizing call fails closed
(no verifier → `verify_token` returns False, energize_confirm.py:346-348).
