# wq#1977 — judgment on wq#1973's CONTRADICT of wq#1968 (litellm key rotation)

**Verdict: the contradiction is upheld, and the resolution is fix-as-fast-follow, not
reopen.**

## Why not reopen

wq#1973 recommended blocking wq#1968 or landing the fix before the first live run. Neither
"block" option is available as stated: PR#602 already merged (`aa635c3`, the only commit
touching `ops/wq1747-litellm-key-rotation/`). Reopening the ticket would not un-merge it,
and the script's own README still gates it behind an operator with real proxy/filesystem
access, so nothing has run against production. The cheapest safe point is therefore a
fast-follow on `main` that lands before the first live run — which is this change.

## The defect (confirmed)

`rotate_one` decided systemd scope from the *rewritten env file* paths:

```python
is_user = any(str(f).startswith(str(Path.home() / ".config" / "systemd")) for f in files)
```

`map_file_to_units` had already collapsed system- and user-unit-dir hits into one
undifferentiated `set[str]`, discarding the only signal that actually answers the
question. Since this fleet's env files live at
`~/.secrets/litellm-service-keys/env/<alias>.env` and never under `~/.config/systemd`,
`is_user` was False for every real alias — even though the default `--unit-dir` list
explicitly includes `~/.config/systemd/user`, so the author clearly intended `--user`
consumers to be in scope.

Impact is worse than a no-op restart: by the time the restart runs, the proxy-side key is
already regenerated and the env file already rewritten, so a `--user` consumer keeps
running on an invalidated key while the script reports success.

## The fix

1. **Scope from the unit, not the env file.** `unit_scope_is_user()` reads scope off the
   unit file's own path (any consecutive `systemd`/`user` component pair, which covers
   `~/.config/systemd/user`, `/etc/systemd/user`, and drop-in dirs under them).
   `map_file_to_units` now returns `(unit_name, is_user)` pairs, and each unit is restarted
   in its own scope — a single env file shared by one system unit and one user unit gets
   two correctly-scoped restarts.
2. **Converge on retry.** `record_state` moved ahead of the restarts, and a failing restart
   is now collected into `units_failed_restart` (exit 1, printed with a "restart these by
   hand" note) instead of an uncaught `CalledProcessError` that propagated past the marker.
   Previously a wrong-scope restart error left no idempotency marker, so the next run
   treated the alias as unrotated and minted *another* key — invalidating the consumer a
   second time instead of converging. Writing the marker first is correct because
   regenerate+rewrite is the irreversible half; under-restarting fails loudly at the
   consumer, whereas re-regenerating never converges.

## Evidence

- `ops/wq1747-litellm-key-rotation/rotate.py:211-224` (`unit_scope_is_user`),
  `:226-248` (`map_file_to_units` returning scoped pairs), `:347-372` (record-then-restart,
  per-unit scope, failure collection).
- Three new tests in `test_rotate.py`
  (`test_user_scoped_unit_gets_a_user_scoped_restart`,
  `test_system_and_user_units_each_get_their_own_scope`,
  `test_restart_failure_records_state_and_does_not_re_regenerate`) **fail against the
  pre-fix `rotate.py` from `main` and pass after**; full suite 10/10.

## Not done

`/home/lumen/kb/ISSUE-LOG.md` row 400 still reads `OPEN — escalated, fix not applied`. The
worker guard denies Write/Edit inside the primary `kb` tree (wq#1719/wq#900) and `kb` is not
part of this repo, so a worker cannot close its own row. **Someone with kb write access
should flip row 400 to `FIXED` and point it at this report.**
