# wq#1890 — Adversarial verify-pass of wq#1885 (despag: bisect wq#1812 admission gate against T-08)

**Verdict: CONTRADICT** — wq#1885's bisection methodology and its headline finding are
sound: `926416f` and `2fe67a9` (wq#1812's admission gate) both fail identically when
`CLAUDE_BIN` resolves to a bare `"claude"` off `PATH`, so 2fe67a9 is correctly ruled out
as the T-08 regression's cause. But the fix does not close the defect it diagnosed — it
hardens the default in `despag/backends/claude_max.py:32` and leaves an identical,
un-hardened `CLAUDE_BIN = os.environ.get("DESPAG_CLAUDE_BIN", "claude")` live at
`despag/app.py:63`, which gates `compute_available()` → the `model_unavailable` 422 check
at `despag/app.py:591`, **one call before** the wq#1812 admission gate the ticket was
bisecting even runs. Reproduced live, on this branch's HEAD (`efa4bd3`), in this exact
BUILD-worker sandbox — the same class of headless environment the PR's own commit
message names as the risk case.

## What the fix does (confirmed correct as far as it goes)

`despag/backends/claude_max.py:32` — the `CLAUDE_BIN` used to spawn the actual `claude -p`
turn subprocess — now defaults to `/home/lumen/.npm-global/bin/claude` instead of a bare
`"claude"`. `despag test suite: 139/139 pass` (`python3 -m pytest despag -q`, reran
independently). The commit message's own T-08/T-09/T-10 bisection claim is plausible and
consistent with the prior verify report `despag/reports/P1_EXIT.md:35-39`, which had
already predicted this exact outcome ("this run's clean T-08 pass suggests the earlier
failure may have been the same PATH gap, not the admission gate").

## Where it breaks: the identical bug is still live one call earlier

`despag/app.py:63` has its own, separate `CLAUDE_BIN` default:

```python
CLAUDE_BIN = os.environ.get("DESPAG_CLAUDE_BIN", "claude")   # unchanged by this PR
```

used only by `_claude_logged_in()` (app.py:112-123, `subprocess.run([CLAUDE_BIN, "auth",
"status"], ...)`), which feeds `compute_available()` (app.py:149-158). `compute_available`
is checked in `POST /v1/responses` at **app.py:591**, immediately before the comment block
introducing the wq#1812 admission gate at app.py:596-604:

```python
if not compute_available(harness):
    return error_response("model_unavailable", ..., status=422)
# wq#1812 P1-7: account-global Max admission gate. Checked before ANY other ...
if not admission.is_local_model(model):
    ...
```

If `CLAUDE_BIN` resolves to a bare `"claude"` and it isn't on `PATH` — precisely the
condition this PR's own comment in `claude_max.py:25-27` says can't be trusted "outside an
interactive login shell (systemd services, sandboxed BUILD-worker conformance runs)" —
`_claude_logged_in()` returns `False`, `compute_available()` returns `False` for the
`chrn_claude-max` harness, and every request to it is rejected with `model_unavailable`
before the admission gate, before turn dispatch, before anything this PR touched.

### Reproduced live, on this branch's HEAD

In this exact BUILD-worker sandbox (no interactive login shell, no `DESPAG_CLAUDE_BIN`
override — the "production is expected to have PATH set" scenario the PR's fix leans on):

```
$ python3 -c "import shutil; print(shutil.which('claude'))"
None
```

```python
>>> import despag.app as app
>>> app.CLAUDE_BIN
'claude'
>>> app._claude_logged_in()
False
>>> app.compute_available(app.HARNESS_BY_ID["chrn_claude-max"])
False
```

And against a live server (`despag/tools`-style launch, isolated `--data-dir`, no
`DESPAG_CLAUDE_BIN` set, matching a naive systemd deploy):

```
$ curl -s -X POST http://127.0.0.1:8799/v1/responses \
    -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
    -d '{"input":"hi","metadata":{"harness_id":"chrn_claude-max"}}'
{"error":{"type":"invalid_request_error","code":"model_unavailable",
  "message":"harness 'chrn_claude-max' has no available model", ...}}
```

This is the exact failure class the PR's commit message describes ("indistinguishable
from a real task failure to a UHP client") — except it never reaches a turn at all; it is
rejected at admission, ahead of the very gate under bisection.

## Why the bisection's test runs didn't catch this

`despag/tools/run_p1_exit.py:32` (`_configure_env`, pre-existing since wq#1882) does
`os.environ.setdefault("DESPAG_CLAUDE_BIN", "/home/lumen/.npm-global/bin/claude")` before
every conformance run. Both `app.py:63` and `claude_max.py:32` read `DESPAG_CLAUDE_BIN`
first and fall back to their own hardcoded literal only when it's unset — so every
bisection run this PR performed (926416f, 2fe67a9, and branch HEAD) had the env var set
and never exercised either module's fallback default. The PR's fix to
`claude_max.py:32`'s fallback is therefore untested by its own probe: nothing in the
3/3-PASS runs distinguishes `"claude"` from the absolute path, because `DESPAG_CLAUDE_BIN`
was already correct in every run. The gap only shows up with the env var unset, which is
exactly the deploy condition (`despag/reports/P1_EXIT.md:112-115`, still an open item for
wq#1883) the fix's own reasoning is about.

`despag/tests/test_uhp.py:153` (`monkeypatch.setattr(app_module, "compute_available", ...)`)
and `:393` (`monkeypatch.setattr(app_module, "_claude_logged_in", lambda: True)`)
permanently stub this exact function in the unit suite — so `139/139 passing` is not
evidence this path works; the tests are structurally blind to it by construction.

## What's correctly fixed

- `despag/backends/claude_max.py`'s own default no longer depends on ambient `PATH`.
- The bisection correctly exonerates 2fe67a9/wq#1812's admission-gate *logic* — no
  admission-gate code change was needed for that regression, and none was made.
- `acceptance.json`/`evidence.json` were updated, but their citation
  (`despag/backends/claude_max.py:25-32`) is honest about scope — it never claimed to
  cover `app.py`. The gap is in the PR's framing ("fix real regression cause") outrunning
  its actual diff, not in a false evidence citation.

## Recommendation

Apply the same fix to `despag/app.py:63` (`CLAUDE_BIN = os.environ.get("DESPAG_CLAUDE_BIN",
"/home/lumen/.npm-global/bin/claude")`), matching `claude_max.py` and `claude-shim/
claude_shim.py:41`. This is a one-line, low-risk, mechanical follow-up, not a new
admission-gate design question — filing wq#1891 (claude-opus-5@high, judgment trigger per
this ticket's own escalation rule) to land it and to decide whether `despag/tests/
test_uhp.py`'s permanent stub of `compute_available`/`_claude_logged_in` should be
loosened for at least one test that exercises the real subprocess-missing path.
