# wq#2806 — credential shapes the mask family cannot read (design)

Session sec-2-9d0da3, 2026-09-26. Read before the diff.

## Root cause (evidence grade A: measured on live fleet config, not guessed)

`survey_shapes.py` (this directory) ran every credential-labelled line in the config
files ACTUALLY IN USE on Lumen (22 docker-compose files, agent/.env + 14 .env snapshots,
.secrets/*.env, litellm_config.yaml, the two ClickHouse users.d XML files read out of the
running containers) through both maskers. 411 labelled lines; masking.py left 100 in the
clear, bin/mask-secrets 49, and 23 more were masked only PARTIALLY (a 6+ char run of the
value survived next to the mask). Values were never printed; the survey keys on
value_sha12 (`/tmp/sec-2-9d0da3/survey_out.txt` for the run of record).

Every miss is one of five grammar defects. None is a missing keyword in a list.

| # | defect | live example (label only) | who misses |
|---|---|---|---|
| 1 | **no XML rule.** `<password>v</password>` has no `:`/`=` after the label, so kv-secret never engages. | ClickHouse `users.d/default-user.xml` `<password>` (the wq#2754 leak) | both |
| 2 | **label depth capped at 2 qualifiers.** `_KV_LABEL` is `(WORD)? WORD NOUN` anchored on `\b`, and `_` is a word char, so a label can only be read from its FIRST segment. Fleet labels run 3–6 segments deep. | `LANGFUSE_S3_EVENT_UPLOAD_SECRET_ACCESS_KEY`, `MCP_PG_READER_PASSWORD`, `R2_SECRET_ACCESS_KEY`, `CLAUDE_SHIM_API_KEY`, `XPACK_..._ENCRYPTIONKEY` | masking.py (sed's rule is unanchored, so it reads them) |
| 3 | **value charset `[A-Za-z0-9._-]`.** Real passwords carry `+ ! # : / =`. The body stops at the first such char, so the mask keeps the tail. | `GITEA_PASSWORD` (live, 12 chars after a `+` survive), `GHOST_ADMIN_KEY` (`id:secret` — the 64-char secret half survives) | both, partial |
| 4 | **noun vocabulary lacks `pass` (qualified) and `salt`.** | `NTFY_PASS`, `WEBAPP_PASS` (agent/.env, live), `SALT:` (langfuse compose, the API-key hashing salt) | both |
| 5 | **single-quoted values.** The separator class is `["\s]*`, so `password: 'v'` never opens. | YAML style, 0 live hits in the survey but the next one is one edit away | both |

Plus the floor: three live values are 10–11 chars (`MONGO_INITDB_ROOT_PASSWORD`,
`WEBAPP_PASS`), under masking.py's 12. The floor is a deliberate precision choice
(masking.py: "precision belongs in the label, not the floor"), so it moves only where the
label is unambiguous (see Options, C).

## Class

Both tables were grown from the shapes that had ALREADY LEAKED — prose, source code, DSNs —
one ticket at a time. Config files (env / YAML / XML), where the fleet's credentials actually
LIVE, were never surveyed as a corpus. The class is "rule grammar derived from incident
samples, not from the population", and the fix that makes it structurally harder to repeat
is the survey kept as a regression instrument (test_config_shapes.py), not any one regex.

The same label grammar (depth-2 cap) is copied into four places: harness/masking.py,
token-engine/token_engine_daemon.py (private copy, test-pinned), harness/read_dir_sweep.py
(at-rest sweep, floor 20) and, in a cruder unanchored form, bin/mask-secrets. wq#2313 owns
the sed copy's kv divergence; this ticket ports the grammar change to every copy it touches.

## Options

A. Keyword additions only (add `PASS`, `SALT`, an XML rule). Rejected: fixes 2 of 5 defects,
   leaves the deep-label and charset classes that hold the LIVE misses.
B. Replace regexes with a config-file parser (dotenv/YAML/XML aware). Rejected: the maskers
   run on arbitrary text streams (a Commander stdout line, a JSONL transcript), where there
   is no file to parse; and POSIX ERE (the sed copy, the live log pipe) has no parser.
C. **Chosen: widen the grammar in place, per defect, with the survey as the test.**
   - XML: new rule `xml-secret` — `<tag>` whose name contains a credential noun segment,
     text body `[^<\s]{8,}` anchored on the `</` that follows (same anchoring argument as
     url-auth: an anchored body cannot run past the value, so it needs no `_body()` bound;
     `_matches()` still screens `<password>MASKED</password>` as own output).
   - Depth: `_KV_LABEL` becomes `(WORD){0,7} NOT_PUBLIC WORD NOUN`. The public-key
     carve-out sits before the LAST qualifier, which is the only position it can occupy
     (the noun is last), so `..._PUBLIC_KEY` stays excluded at any depth.
   - Charset: `_KV_BODY` = `[A-Za-z0-9._\-+/=:!#$%^*@~]` — an explicit allow-list, still
     no whitespace, quotes, backtick, `, ; ( ) [ ] { } < > | \ & ?`, so JSON/YAML/shell
     delimiters still end the value and `_KV_NOT_CALLED` keeps its meaning. `_KV_POINTER`
     gains `(?![A-Za-z][A-Za-z0-9+.-]*://)` because `:` and `/` now let `api key: https://…`
     reach the floor.
   - Nouns: `pass` only behind `[_-]` (`NTFY_PASS`, never `first pass:` or `bypass`);
     `salt` bare (the langfuse compose shape; prose `salt:` with a 12+ opaque body is
     not a shape this corpus has).
   - Quotes: `["'\s]*` around the separator.
   - Floor: a second rule `kv-secret-env` — ALL_CAPS label with >=1 `_` qualifier ending in
     a STRONG noun (PASSWORD|PASSWD|PASSPHRASE|PASS|SECRET|TOKEN|SALT, never KEY/PAT),
     case-sensitive, floor 8. Env-file identifiers are not prose; the ambiguity the 12
     floor guards against does not exist for them. Listed BEFORE kv-secret so the
     stricter label claims the line first; a value both read is masked once.
   - sed copy: same charset, same nouns, XML line, floor 8 across the board (that copy is
     "deliberately wider": over-masking a stream is free, and it is the copy on the live
     log pipe).

## What (b) found: redact-on-write is ALREADY LIVE, and frozen

`/etc/systemd/system/desktop-commander-mcp.service.d/wq2546-mask-body-log.conf` routes
supergateway stdout through `bin/mask-secrets` before the log file. The unit restarted at
02:29Z today with it in effect (`ps`: bash mask-secrets pid 3937665 -> sed 3937666, child
of supergateway 3937662). The ticket's premise (b) was stale when filed.

But the pipe is a sed process that read its program ONCE at 02:29Z. `/proc/3937666/cmdline`
has no url-auth rule: the wq#2760 edit landed on disk after the restart and never reached
the pipe. Same class as wq#2327 (a table imported at spawn scores for the process's whole
life). Fix in this ticket: `log-pipe-check.sh` compares the running sed program to the
on-disk script and reports STALE/CURRENT (exit 1 on STALE, so a timer can raise aq), and
the restart that loads the new program is done detached (`systemd-run --on-active`), never
foreground, per the Commander-path rule.

## Rollback

`install.sh` keeps `bin/mask-secrets.pre-wq2806`; `git revert` of the PR restores every
Python copy; the drop-in is untouched. A rule-table regression is caught by
`_still_masks()` (deployed() refuses a table that stops labelling the canary) and by the
survey's zero-miss assertion, which now runs as a pytest.

## Test-to-failure plan

1. Survey: every live labelled line masks with no 6+ char run of the value surviving, both
   maskers (pytest reads the survey over a synthetic copy of the shapes — real values never
   enter the tree).
2. Existing suites green: harness/tests/test_masking.py, test_mask_replay.py,
   test_read_dir_sweep.py, ops/wq2316/test_mask_secrets_boundary.py, token-engine tests.
3. FALSE_POSITIVES / KV_FALSE_POSITIVES / doctrine corpus (kb/ISSUE-LOG.md) still silent —
   the widened charset and depth must not re-arm a single prose line.
4. Sweep rerun over /home/lumen/logs with every rule (not just url-auth); each hit hashed
   against live env values; live matches -> rotation ticket, dead -> recorded.
