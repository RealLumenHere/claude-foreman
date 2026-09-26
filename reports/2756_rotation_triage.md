# wq#2756 -- triage the unaccounted values in a current-table replay of wq#2732

## Live corpus note
The ticket cited 9 unaccounted values from wq#2740's pass. Re-running
`python3 harness/mask_replay.py 2732 --rules current --json` now shows **10** -- the corpus
grows over time by design (wq#2740's own report warns of exactly this: "a count off a live
corpus is not evidence"). Triaged the live set below, not the stale count.

## Disposition of all 10

| value_sha12 | shape | Disposition |
|---|---|---|
| `89136daf65ca` | kv-secret, `LANGFUSE_SECRET_KEY:`, len16 | **LIVE.** Hash-confirmed match to the Langfuse project API key actually configured in `litellm_config.yaml`. **Rotated** -- see below. |
| `a3b99d59dbb0` | github, `ghp_`, len36 | Benign, exact reconstruction. Raw log line at snapshot line 54766 shows the literal source: a Python one-liner (`tok = 'ghp_' + 'A'*36`) that wq#2732 ran testing mask-secrets' own glue-detection. `hashlib.sha256(b'A'*36).hexdigest()[:12]` = `a3b99d59dbb0`, confirmed. |
| `9db44f3cd56d` | url-auth, postgresql, len24 | Already triaged and closed in wq#2777 -- the pgvector password live April 2026 through 2026-09-25 04:18, superseded. |
| `0cc16ed01e2c` | url-auth, postgresql, len32 | Already triaged and closed in wq#2777 -- the pgvector password live 09-25 05:16 through 09-26 00:59 (wq#2512 window), superseded by wq#2700. |
| `218a735a2550` | kv-secret, `Server key:`, len19 | Benign, not a credential class at all. Traced to source: SSH to `farida` (QuestFree RE edge node) running a `bleak`-based BLE companion script against physical Meta Quest hardware; the line is `ECDH OK! Server key: <val>` debug output from the device's own DevMode pairing handshake. An ECDH-derived key is per-session and ephemeral by protocol design -- there is nothing to rotate. |
| `33c3186d1334` | kv-secret, `token:`, len17 | Benign, false positive. Source: Commander's own File Preview MCP UI host page, which bundles a minified markdown-it-style tokenizer (`e.md.options.typographer` is markdown-it's own option flag). `token:`/`t=` here is JS tokenizer-loop code, not a credential. |
| `1bd4635b5896` | kv-secret, `LITELLM_MASTER_KEY=`, len23 | Dead. Hash-confirmed match to `"sk-lumen-litellm-master"`, the stale May-2026 placeholder documented in `claude_memory.md`'s "CURRENT STATE OVERRIDES — 2026-05-13" section (long superseded; wq#949 rotated all 14 litellm service keys 2026-08-26). Confirmed distinct from the current live `LITELLM_MASTER_KEY`. |
| `b21f55c9fc8b` | kv-secret, `GITEA_TOKEN=`, len34 | Benign, exact hash match to `"FAKE_NEW_TOKEN_1234567890123456789"`, the current committed content of `bin/tests/test_gitea_rotate_finish.sh`. |
| `75c89af73fa6` | kv-secret, `GITEA_TOKEN=`, len32 | Benign, exact hash match to `"placeholder000000000000000000000"`, also current committed content of the same test file. |
| `5d04e64dac64` | kv-secret, `GITEA_TOKEN=`, len25 | **Not resolved with proof.** Same MCP log snapshot, same test file family, same test pattern as the two rows above -- but no revision in `bin/tests/test_gitea_rotate_finish.sh`'s git history (`git log --all`, 2 near-duplicate commits, both matching today's content) contains a 25-char literal. Working theory: an uncommitted intermediate edit-in-progress state from the same session. Left genuinely unaccounted rather than force-closed -- see `harness/tests/fixtures/wq2756_research_note.py` (this PR) for the full reasoning trail, so a future replay doesn't restart from zero. |

## The one live rotation: Langfuse project API key

`LANGFUSE_SECRET_KEY`/`LANGFUSE_PUBLIC_KEY` (`pk-lf-lumen-2026`) in `/home/lumen/agent/litellm_config.yaml`
matched a value found live in the clear in the same supergateway log snapshot wq#2777 covered.
Rotated end-to-end:

1. Backed up `api_keys` table (`/home/lumen/backups/wq2756/api_keys_pre_rotate_*.sql`) and both
   `docker-compose.yml`/`litellm_config.yaml` (timestamped `.bak`s alongside the originals).
2. Deleted the compromised row directly from `langfuse-postgres.api_keys` by id -- a pure DELETE
   is safe and hash-algorithm-agnostic; no need to reverse-engineer Langfuse's internal secret
   hashing scheme (traced into the compiled `langfuse-web` bundle looking for it; the actual
   seed/create logic wasn't in the reachable chunks, and guessing at a live auth system's hash
   format was the wrong risk to take).
3. Generated a new `pk-lf-`/`sk-lf-` pair, wrote it to `docker-compose.yml`'s
   `LANGFUSE_INIT_PROJECT_PUBLIC_KEY`/`SECRET_KEY`, recreated `langfuse-web` -- its own startup
   code detected no key matching the new public key and created one, correctly hashed by
   Langfuse's own code, not reimplemented here. Confirmed in the DB immediately after.
4. Wrote the same new pair into `litellm_config.yaml`, restarted `litellm`.
5. **Found and closed a gap during verification**: the old key still returned valid data after
   the DB delete. Langfuse caches key-validation results in Redis (`api-key:<hash>` keys, no
   TTL visible); cleared the 3 cache entries directly. Old key -> `401` immediately after.
6. Verified end-to-end: `litellm` container healthy, "Initialized Success/Failure Callbacks -
   ['langfuse']" with no auth errors, a real completion request went through, and 3 fresh rows
   landed in ClickHouse's `traces` table within 2 minutes -- ingestion genuinely works on the new
   credential, not just "no error at startup." New key independently confirmed via
   `/api/public/projects` (200) after the cache clear.

Recorded in `rotated_ledger.json` (this PR).
