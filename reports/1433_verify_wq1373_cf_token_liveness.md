# wq#1433 — verify-pass: wq#1373 CF token liveness monitor

**Verdict: CONTRADICT** — the prober's own error handling has a gap that
reproduces the exact silent-failure mode wq#1373 exists to close: a single
malformed manifest entry (the kind of typo you'd introduce doing the
config-only edit the README recommends for adding future tokens) raises an
uncaught exception, kills the entire pass before any alert fires, and the
shipped systemd unit's `SuccessExitStatus=0 1` then reports that crash to
`lumen-cron-deadman` as a normal, healthy run. Everything else in the diff
holds up under adversarial testing.

PR: http://100.121.17.5:3000/lumen/claude-foreman/pulls/309
(`4e9816b`, branch `feat/wq1373-cf-token-expiry-liveness-monitor-the-37--a1`).

## What was verified and holds up

- Ran the shipped `cf_token_liveness.py` against the shipped `tokens.json`
  live: `cf_token_liveness: 2/2 tokens live` (exit 0) — reproduces the PR's
  own claimed verification run against the real `/home/lumen/mailbot/.cf_d1_token`
  and `.cf_d1_write_token` files.
- Reproduced the documented failure path with a synthetic manifest (garbage
  token, missing token file, back-dated `minted`): correctly reported 2
  failures + 1 TTL warning, `[dry-run] would alert priority=urgent ...` /
  `priority=high ...`, exit 1 — matches the PR's own verification transcript
  (I got `HTTP Error 401` where the author's run showed `404`; both are
  Cloudflare rejecting a garbage bearer token, not a discrepancy in the
  script's behavior).
- `probe_d1`/`probe_workers`/`probe_dns`/`probe_r2`/`probe_access` each build
  the URL and call shape the README claims; the D1 shape matches
  `mailbot/mail_recall.py`'s proven-live request as cited.
- TTL warning math (`ttl_warning()`) correctly fires at `remaining <= 3`
  independent of pass/fail, and never touches the exit code — confirmed by
  the synthetic run above.
- The disclosed scope gap — `tokens.json` covers only the two mailbot D1
  tokens, not the #37 Access-mgmt token that motivated the ticket — is
  honestly stated in README.md, install.sh's post-install banner, and
  evidence.json (ISSUE-LOG.md #189: `claude_credentials.md` is guard-denied
  to this worker, so it couldn't discover the other tokens' file paths). Not
  a fabrication, not treated as a contradiction — it's a real gap but an
  admitted one with a documented, mechanical follow-up.

## Contradiction — an unhandled exception in one entry kills the whole pass, silently

`check_one()` (`ops/wq1373-cf-token-liveness/cf_token_liveness.py:126-138`)
only wraps the `probe()` call itself:

```python
def check_one(entry, now):
    name, token_file = entry["name"], entry["token_file"]
    ...
    try:
        probe(entry, _read(token_file))
    except (urllib.error.HTTPError, urllib.error.URLError, RuntimeError, OSError) as e:
        return False, f"{name}: {entry['scope']} probe failed: {e}", None
    return True, None, ttl_warning(entry, now)
```

Every `probe_*` function does unguarded `entry["account_id_file"]` /
`entry["database_id_file"]` / `entry["zone_id_file"]` lookups
(`cf_token_liveness.py:74,82,88,94,100`). A manifest entry missing one of
those keys — or missing `name`/`token_file`/`scope` themselves, at line 128 —
raises `KeyError`, which is not in the caught exception tuple. Reproduced
directly against the shipped script, unmodified:

```
$ python3 cf_token_liveness.py --manifest tokens_badkey.json --dry-run
Traceback (most recent call last):
  ...
  File ".../cf_token_liveness.py", line 74, in probe_d1
    account = _read(entry["account_id_file"])
KeyError: 'account_id_file'
exit=1
```

Manifest for that run was two entries: one missing `account_id_file`,
followed by the known-good `mailbot-d1-read` entry. The crash happens on the
*first* entry and the loop in `run()` never reaches the second, healthy
token — so a single bad entry doesn't just misreport itself, it stops every
other token in the manifest from being checked that pass. No `_alert()` call
is ever reached, so **no ntfy fires** — urgent or otherwise.

This compounds with the shipped systemd unit. `cf-token-liveness.service`
sets `SuccessExitStatus=0 1` specifically so that the *intentional* exit 1
("a dead token is the finding, not a unit failure" — comment at
`cf-token-liveness.service:15-17`) doesn't trip `OnFailure=`/a restart loop.
But Python's default handler for an uncaught exception also exits 1 — the
same code. `SuccessExitStatus=0 1` cannot distinguish "the prober ran clean
and found a dead token" from "the prober itself crashed before checking
anything." `ExecStartPost=-/home/lumen/bin/lumen-cron-stamp %n` therefore
stamps a crashed run exactly like a healthy one, so `lumen-cron-deadman`
(wq#1184) — the safety net this design explicitly leans on for "a dead
prober is itself caught" (README.md:83-84) — never fires either.

Net effect: a manifest typo (the exact one-JSON-object edit the README
prescribes for wiring up the #37 token and every other pending one) silently
disables the entire monitor — no urgent page, no TTL warning, no deadman
catch — which is the precise "nothing was watching" failure mode
(ISSUE-LOG.md #37) this ticket was opened to prevent.

## Disposition

The probe logic, alerting thresholds, and TTL math are correct and reproduce
the author's own verification transcript exactly. The gap is in failure
containment: `check_one()`'s try/except needs to wrap the full per-entry
evaluation (not just the network call) and catch config-shape errors
(`KeyError`, and likely `json.JSONDecodeError`/`ValueError` for a non-JSON CF
response body) as a per-token failure, not let them escape `run()`
uncaught — and/or the systemd unit needs a way to tell "found a dead token"
apart from "the prober crashed" (e.g. a distinct exit code for the crash
path, with `SuccessExitStatus` only covering the intentional one). Which
shape to take is a judgment call, not mine to make here — escalating to
opus-5@high per this ticket's own instructions. Not filing the fix myself:
verify-pass is not a re-implementation gate (precedent wq#872/#1268).
