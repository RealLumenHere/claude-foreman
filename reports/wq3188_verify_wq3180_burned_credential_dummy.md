# wq#3188 — verify-pass of wq#3180 (PR#1447: burned credential sha256:08c83376aab6)

**Ticket:** wq#3188, archetype verify-pass, verifier claude-sonnet-5.5@high (session overnight-1)
**Target:** wq#3180 (author claude-opus-5-5), PR#1447, commits `714f063d` / `0e26f567`.
**Verdict: CONFIRM.** No contradiction found.

## Claim under test

The mask tripwire fired on wq#3169 attempt 1 for a `kv-secret` value with sha256 prefix
`08c83376aab6`. wq#3180 concludes it is the placeholder body `sk-abc123XYZ` that the probe
test writes into a throwaway `.env`, not a credential, and rotates nothing.

## What I checked

1. **The hash is the dummy.** `printf "sk-abc123XYZ" | sha256sum` gives prefix
   `08c83376aab6`; the record's `value_len` is 12, and `sk-abc123XYZ` is 12 characters.
   The variant without the `sk-` head hashes to `2fdd330a7315`, so it is exactly this string.
2. **It is not a live value.** I hashed every value in the `env_file` entries my regex found
   in the compose files that mention litellm (66 values across `/home/lumen/docker/docker-compose.yml`
   and `sim-studio`), comparing against the prefix without printing any. No match. This is
   a spot check, not proof of absence elsewhere.
3. **Replay agrees.** `python3 harness/mask_replay.py 3169` exits 0: "1 credential-shaped
   values in the run's model context, 0 unaccounted", tracked at
   `ops/wq3169-litellm-completion-probe/tests/test_litellm_completion_probe.py:41`
   (`env.write_text("OTHER=1\nLITELLM_MASTER_KEY=...\nMORE=2\n")`).
4. **The suppressed candidates.** The replay lists three audit-only rows.
   `MISTRAL_API_KEY: ` len 15 hashes to `861f0936375d` = sha256 of the literal string
   `MISTRAL_API_KEY` (15 chars), i.e. the key's own name (the `self-name` suppression). The other two
   (`TOKEN = `, `token = `, mask-hits-2) are labelled pointer+call and audit-only in the
   replay output. I did not chase them further; nothing was reported unaccounted.
5. **The pin tests pass.** `pytest harness/tests/test_mask_replay.py -k "wq3169 or wq3180"`:
   2 passed. The fixture `wq3169_values.py` splits the literal (`"sk-" "abc" "123" "XYZ"`)
   so reading it does not re-fire the rule, and its own test asserts
   `masking.mask_text(text)[1] == []`.
6. **Leak path documented.** The fixture header and ISSUE-LOG #1077 name the cause: a test
   typing a whole kv-secret-shaped assignment instead of building it at runtime.

## Residual note (not a contradiction)

The leak path is closed only by the note left on wq#3169; the probe test still writes the
whole assignment literal. That is committed text and harmless by the evidence above, but a
future edit that swaps in a real key would re-arm the tripwire. Building the body at runtime
in that test would make the class impossible instead of pinned.
