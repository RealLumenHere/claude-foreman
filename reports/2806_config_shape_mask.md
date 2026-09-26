# wq#2806 — config-file credential shapes: masking grammar widened, log pipe checked

Session sec-2-9d0da3, 2026-09-26. Design: `ops/wq2806-config-shape-mask/DESIGN.md`.

## What was wrong (measured, not the ticket's guess)
The ticket named two shapes (`<password>` XML, `KEY: value`). A survey of the config files
actually in use on Lumen (`ops/wq2806-config-shape-mask/survey_shapes.py`: 22 compose
files, agent/.env and 14 snapshots, .secrets/*.env, litellm_config.yaml, both ClickHouse
users.d XML files read out of the running containers) found 411 credential-labelled
lines. masking.py left 100 in the clear, bin/mask-secrets 49, and 23 more were masked
only partially. Five grammar defects, none a missing keyword:

1. no XML rule at all;
2. kv-secret's label read at most two `_`-segments deep and opens on `\b`, so
   `LANGFUSE_S3_EVENT_UPLOAD_SECRET_ACCESS_KEY`, `MCP_PG_READER_PASSWORD`,
   `R2_SECRET_ACCESS_KEY`, `CLAUDE_SHIM_API_KEY` were invisible (that is the ticket's
   "KEY: value");
3. body charset `[A-Za-z0-9._-]` — live `GITEA_PASSWORD` kept 12 characters after its
   `+`, `GHOST_ADMIN_KEY` (`id:secret`) kept the whole secret half;
4. no `_PASS` (live `NTFY_PASS`, `WEBAPP_PASS`) or `SALT` noun;
5. `'`-quoted values never opened.

Found on the way: the hump lookahead `(?=[A-Z])` consumed nothing, so an ALL_CAPS label
could be split anywhere and `PUBLIC_KEY=<v>` slipped the public-key carve-out on main.

## What changed (PR #1158, #1159, #1160, #1161)
- `harness/masking.py`: `xml-secret` rule; label depth 0–7 with one-letter `_` segments;
  body allow-list `[A-Za-z0-9._\-+/=:!#$%^*@~]`; `'` quotes; whitespace no longer
  crosses a newline; `_pass`/`salt` nouns; hump requires a Capitalised word; an
  env-identifier kv form (ALL_CAPS + strong noun) at floor 8; pointer exclusions for
  URLs, `${X:-…}` operators, `***`/`%`/`your-`/`changeme` placeholders; a recorded
  `identifier` suppression for `KEY = "snake_case_constant"`.
- Same grammar in `token-engine/token_engine_daemon.py` (test-pinned mirror) and
  `harness/read_dir_sweep.py`; sed copy `ops/wq2316-mask-secrets-boundary/mask-secrets`
  gains the XML line, the charset, floor 8 on strong nouns.
- Four committed fixtures split at the rule boundary (they became credential-shaped
  under the new grammar and armed the tripwire corpus walk).
- `ops/wq2760-dsn-mask/sweep_dsn_history.py --all-rules`.
- `ops/wq2806-config-shape-mask/`: DESIGN.md, survey_shapes.py, `log-pipe-check.sh`,
  install.sh (`--verify` includes the pipe check).

After: 0 real misses in either masker on the same 411 lines (what remains is
`*_PUBLIC_KEY`, `${…}` templates, `os.environ/` pointers, key IDs).

## Ticket part (b): the log pipe
Redact-on-write was ALREADY live: `wq2546-mask-body-log.conf` routes supergateway stdout
through `bin/mask-secrets`, in effect since the 02:29Z restart. What was wrong is that
the pipe's sed read its program once at 02:29Z — `/proc/<sed>/cmdline` carried no
url-auth rule while the file did. Rules edited after a unit start never reach the pipe
(wq#2327's class). `log-pipe-check.sh` now says STALE/CURRENT (exit 1/0; proven both
ways), and the detached restart is the documented next step after any mask-secrets edit.

## Sweep
`sweep_dsn_history.py --all-rules --context /home/lumen/logs` (2.8 GB). Two earlier runs
stalled on one Commander log line (a 554 KB base64 blob) — that was the kv rule's hump
word, which could end at any capital of a mixed-case run; PR #1161 ends every word at a
point the text fixes (0.37 s on that line now) and PR #1160 moved the identifier guard
behind the label (890 -> 520 ms on the 1.2 MB line). The run of record:
`/tmp/sec-2-9d0da3/sweep_logs.txt`, 51 distinct values, all in supergateway/.

## Disposition
Every value hashed against every credential a rule or the survey reads in the live config
set (850 hashes: agent/, .secrets/, compose files, litellm config, container XML, gh/git
credentials):
- **11 matched a live file.** Five are the LiteLLM provider keys in
  agent/litellm_config.yaml (Cerebras, DeepSeek, Gemini, Mistral, OpenRouter), 11-12
  occurrences each in the pre-pipe rotation log.1 — someone cat'd the config through
  Commander before wq#2546. The langfuse `<password>` and `SALT` are this session's own
  reads through the pipe while it still ran the old sed (no XML rule); the rest matched
  gate snapshots (rotated in wq#2754/2777), and the LITELLM_MASTER_KEY hit is an old key.
- **40 matched nothing live**: fixtures from this session's own test runs (DEFAULT_PASS,
  TAPO_PASS, K3S_TOKEN, PROBE_TICKETS_FILED_KEY, SECRET =), the wq#2777 DSNs (old=yes),
  dead GITEA/NTFY/Mongo values, two ghp_ tokens (token-engine store is unreadable from
  lumen; verify there — wq#2827).
- **Scrubbed**: desktop-commander-mcp.log (in place, same inode, the pipe kept appending),
  .log.1 (atomic swap) and .log.3.gz (recompressed) through the new mask-secrets at
  10:06Z; the all-rules sweep over the directory is now `distinct_values=0`.
- **Rotation decision** for the five provider keys is wq#2827 (exposure: 0600 lumen file
  on the host that holds the plaintext config; no other reader).
