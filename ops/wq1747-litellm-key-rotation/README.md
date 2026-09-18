# wq#1968 — rotate the 15 LiteLLM virtual keys leaked to Bes logs (wq#1747)

wq#1747's findings note identified 15 LiteLLM virtual-key aliases whose raw
values appeared in supergateway body logs shipped to Bes VictoriaLogs (90d
retention, usable from any tailnet node since LiteLLM listens on the
Tailscale IP too):

```
langfuse-evaluator-2, brain-healthcheck-2, lpp-cerebras, lumen-agent-2,
duet-2, ops-emergency-3, sentinel-2, minerva-pipeline-2, lumen-reflector-2,
lumen-messenger-2, minerva-bench-2, lumen-research-2, sia-news-2,
lumen-dispatch-2, lpp-2
```

This worker's sandbox cannot read `/home/lumen/.secrets` (workers cannot
read secrets), so this ticket delivers the rotation **script**, not the
rotation itself — an operator with real proxy/filesystem access runs it.

## What it does, per alias

1. **Look up** the alias's current key record on the LiteLLM proxy
   (`GET /key/list?key_alias=...`) to get its sha256 token fingerprint,
   allowed models, and budget.
2. **Find the raw OLD value on disk**: scan the search roots for
   `sk-...`-shaped substrings and keep the one whose sha256 matches the
   fingerprint from step 1 — same identify-by-hash technique as
   `ops/wq1647-lpp-cerebras-retire/retire_lpp_cerebras.py`, run in reverse
   (alias → hash → raw value instead of raw value → hash → record).
3. **Regenerate**: `POST /key/{old_value}/regenerate` with master-key auth.
   LiteLLM's regenerate keeps alias/models/budget unless overridden, so
   nothing else needs to change proxy-side.
4. **Rewrite consumers**: grep every search root for literal occurrences of
   the OLD value (excluding `_runs`, `.git`, `node_modules`, `.venv`) and
   rewrite each file in place, byte-for-byte, preserving its mode (0600
   stays 0600). Only file *paths* are ever recorded/printed — never
   contents.
5. **Restart**: map each rewritten file to the systemd unit(s) that source
   it via `EnvironmentFile=` (searched under `/etc/systemd/system` and
   `~/.config/systemd`, including drop-ins), and restart them via detached
   `systemd-run --collect` (per the Commander-path instinct: never a
   foreground restart from an unattended script). Units listed in
   `never-restart-units.txt` are skipped and printed for the operator
   instead.
6. **Verify**: one cheap `/chat/completions` call through the proxy with the
   new key.
7. **Record state**: `--state-dir/<alias>.json` holds the alias's
   post-rotation token sha256, so a re-run with nothing changed is a no-op
   (`already_rotated`) instead of rotating again — this is what makes the
   script idempotent, since LiteLLM's regenerate endpoint itself is not
   (every call mints a fresh key).

## Credential hygiene

- The master key comes **only** from the `LITELLM_MASTER_KEY` env var the
  operator sets before running — never hardcoded, never written to a file
  by this script, never logged.
- All proxy calls go through Python's `urllib` in-process, not a `curl`
  subprocess — so no secret ever appears in a process's argv (`ps` would
  show it otherwise; the regenerate endpoint's own contract puts the OLD
  value in the URL path, which is unavoidable, but at least it never leaves
  the process this way).
- Output is masked: a rotated alias's summary line shows a 6-char prefix of
  the new value, file *paths* only, and unit names only — never the key
  itself.

## Usage

```
export LITELLM_MASTER_KEY=...   # never in a URL, never printed
./rotate.sh                      # rotate all 15 known aliases
./rotate.sh --alias lpp-cerebras # rotate just one
./rotate.sh --dry-run            # report what would change, touch nothing
```

Useful overrides (mainly for testing against non-default layouts):
`--base-url`, `--search-root` (repeatable), `--exclude` (repeatable),
`--unit-dir` (repeatable), `--state-dir`, `--never-restart-file`,
`--verify-model` (default: the key's own first allowed model, else
`gpt-4o-mini`).

If an alias's current key can't be found anywhere under the search roots
(`old_value_not_found_on_disk`), the script skips it rather than guessing —
that alias needs manual handling. Same for `alias_not_found` (no matching
`key_alias` on the proxy). Either produces a non-zero exit code.

## Tests

```
cd ops/wq1747-litellm-key-rotation && python3 -m pytest test_rotate.py -v
```

`test_rotate.py` spins up a fake LiteLLM proxy (`http.server`, localhost,
random port) implementing `/key/list`, `/key/{key}/regenerate`, and
`/chat/completions`, monkeypatches `subprocess.run` so no real `systemd-run`
call happens, and drives everything through `tmp_path` — no real secrets,
proxy, or systemd units are touched.

## Also in this PR: wq#1698 journald conf fix

`ops/wq1698-supergateway-log-redaction/no-body-logs-journald.conf` shipped
with both `StandardOutput=` and `StandardError=` pointed at the local log
file. That contradicts the same directory's `README.md`, which explicitly
says the point of the split was to keep `logger.error` output (`Child
exited: code=…`, transport-error diagnostics) in journald for incident
response while only the high-volume body/chatter stream moves to the local
file. wq#1747's findings note flagged this too ("stderr kept in journald,
the shipped conf sent it to the file too — fix the tracked conf"). Fixed:
`StandardError=journal`, `StandardOutput=` unchanged.
