# wq#3793 — adversarial verify + judgment on wq#3792 (nephthys-qwen14b judge fallback identity pin)

- **Ticket verified:** wq#3792, PRs http://100.121.17.5:3000/lumen/claude-foreman/pulls/1790
  (foreman, commit `30a2d2c4`) and http://100.121.17.5:3000/lumen/lumen-bin/pulls/248
  (lumen-bin, commit `a38a1df`)
- **Verifier:** claude-sonnet-5@high (fresh instance, same model as author)
- **Escalation:** opus-5@high (contradiction found → judgment trigger)
- **Verdict: CONTRADICTION CONFIRMED — HIGH severity, latent.** wq#3792's own `done` claim
  is false: neither fix commit reached `main` in either repo. Disposition: **reopened
  wq#3792** (not a new follow-up ticket — the original deliverable was simply never landed).

## Scope

wq#3792 was the follow-up from wq#3789's judgment of wq#3783: add a `nephthys-qwen14b`
identity entry to `judge/JUDGE_MODEL_IDENTITY.json`, and change `bin/judge`'s
`required=` test from `model_source.startswith("pin:")` to
`not model_source.startswith("env:")` so a `fallback:` routing decision is held to the
same wq#2821 anti-retag identity check as `pin:`, plus tests for both.

## What works — the code itself is correct and tested

Checked out both commits in disposable detached worktrees (`git worktree add --detach`
into `/tmp`, removed after) rather than trusting the diff by inspection alone:

- foreman `30a2d2c4`: `pytest judge/test_judge_fallback_routing.py judge/test_judge_identity.py`
  → **13 passed**, including `test_fallback_source_with_no_identity_pin_fails_closed` and
  `test_nephthys_qwen14b_pending_entry_fails_closed_until_populated`.
- lumen-bin `a38a1df`: `pytest tests/test_wq3792_judge_identity_required_on_fallback.py`
  → **3 passed**, including `test_fallback_source_requires_identity`, which asserts
  `required=True` is actually passed for a `fallback:` source — exactly the regression
  wq#3789 found.
- The `nephthys-qwen14b` identity entry correctly ships with `blob_sha256: ""` rather than
  a guessed digest (nephthys has been offline 5d, `/api/show` unreachable) — an empty
  digest still fails closed on every call until a real `/api/show` fills it in, which is
  the right call per wq#2821 and instinct 8 (no fabricated evidence).

The diff is not the problem. Deployment is.

## Contradiction found and confirmed

Neither commit is merged to the branch either repo actually runs from:

```
$ git -C /home/lumen/foreman merge-base --is-ancestor 30a2d2c4 HEAD   # HEAD = main
→ not an ancestor
$ grep nephthys /home/lumen/foreman/judge/JUDGE_MODEL_IDENTITY.json
→ (no match — no entry on live main)

$ git -C /home/lumen/bin merge-base --is-ancestor a38a1df HEAD        # HEAD = main
→ not an ancestor
$ grep -n "required=" /home/lumen/bin/judge
149:    ident_err = verify_model_identity(model, required=model_source.startswith("pin:"))
→ the live, deployed judge script still has the pre-fix line. /home/lumen/bin is the
  real bin directory the fleet executes, not a throwaway worktree.
```

wq#3792's own cited liveness probe fails against live state right now:

```
$ grep -q nephthys-qwen14b /home/lumen/foreman/judge/JUDGE_MODEL_IDENTITY.json; echo $?
1
```

This is not ordinary merge lag. The direct predecessor, wq#3783's fix (`8d7fce65`,
`fix(judge): hathor-aware fallback routing`), **is** an ancestor of `main` (`git log main
--grep=3783` finds it), merged 2026-10-13 07:37:32 — about 20 minutes before its own
verify ticket (wq#3789) even ran (verify commit 07:48:09, judgment commit 07:48:52). In
this fleet, "fix lands on main promptly, independent of when the verify ticket runs" is
the established pattern. wq#3792's fix sitting unmerged on `feat/wq3792-judge-fallback-
rung-nephthys-qwen14b-has-a1` (foreman) and `fix/wq3792-judge-identity-fallback`
(lumen-bin) while marked `done` breaks that pattern rather than following it.

## opus-5@high judgment (verbatim verdict)

> **VERDICT: CONTRADICTION CONFIRMED.** wq#3792's "done" claim is false. Neither fix
> commit is on main, and the live `/home/lumen/bin/judge` line 149 still gates identity
> checks on `pin:` only. Severity is HIGH but latent. Right now a `fallback:` route still
> fails closed, but only because nephthys is unreachable. That is a side effect of the
> outage, not a design guarantee. Reopen wq#3792 and land both PRs now. Don't file a
> separate ticket, because the ticket's own deliverable was never delivered.

Supporting reasoning (condensed): `verify_model_identity()` returns `None` (pass) for any
model with no identity entry when `required=False`; `fallback:` still gets
`required=False` on live main. Today that resolves safely only because `resolve_judge_model()`
routes to nephthys, nephthys is unreachable, and `call_judge()` fails and the verdict
fails closed anyway — a side effect of the outage, not a guarantee. The moment nephthys
comes back online while hathor is asleep, a judge verdict will be accepted from an
unverified model with zero identity check — exactly the wq#2821 risk this ticket exists to
close. Either merge order (bin first or foreman first) stays safe in the interim: with
only the bin fix landed, nephthys has no identity entry and fails closed under the new
`required=not env:` test; with only the foreman fix landed, the entry exists but its empty
`blob_sha256` still fails closed on `/api/show`.

## Disposition

- **wq#3792 reopened** (`wq edit 3792 --status open`) rather than filed as a new
  follow-up ticket — the code is already correct and tested at `30a2d2c4` /
  `a38a1df`; what's missing is landing those two already-reviewed PRs, not new
  implementation work.
- Noted on wq#3792 for whichever worker claims it next: land PR#1790 (foreman) and
  PR#248 (lumen-bin) to `main`, in either order (both tested fail-closed on their own),
  then re-run the ticket's own liveness probe
  (`grep -q nephthys-qwen14b /home/lumen/foreman/judge/JUDGE_MODEL_IDENTITY.json`) and
  confirm `/home/lumen/bin/judge`'s `required=` line reads
  `not model_source.startswith("env:")` before closing done again.
- Do NOT re-revert or redo the code — only the landing step is missing.
