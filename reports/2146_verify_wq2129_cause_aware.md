# wq#2146 — verify-pass of wq#2129 (PR #704, dd533b5)

Verifier: claude-sonnet-5@high. Scope: `dispatcher/runaway.py`, `dispatcher/self_heal.py`,
`dispatcher/uncharge_attempt.py` and their tests, read cold against the ticket's PROOF.

## Verdict: CONFIRM, with two latent defects filed as a follow-up

The ticket's PROOF holds and I could not break it. Two defects sit in the RUNG_FAULT release
path, both dormant because nothing in the repo writes `backend_cooldown.json` yet.

## What held

| Claim | Evidence |
|---|---|
| 5 rung-charged attempts are not stuck; 5 ticket-charged are | `runaway.py:82-103` `charged_attempts`; `test_runaway.py` |
| Absent `charged_to` = ticket | `self_heal.py:358-361` `charged_to` |
| >7d and pre-edit attempts drop out | `runaway.py:97-102`; `finished_at` is epoch in real records (`fworker.py:2519`) |
| Rollback lever `FOREMAN_CHARGE_MODE=legacy` | `self_heal.py:332`, used at `runaway.py:150` and `self_heal.py:371`; probe: legacy reproduces the old count |
| uncharge bulk never deletes, re-run is a no-op, `--dry-run` writes nothing | `uncharge_attempt.py:113-157` |
| Suites | `pytest test_runaway.py test_self_heal.py harness/tests/test_uncharge_attempt.py` = 77 passed. Rest of `dispatcher/tests`: 383 passed, 1 failed (`test_capability_replay[1407]`, live-wq-DB replay, file untouched by the PR) |

Live check (read-only): `stuck_ticket_ids()` new vs `FOREMAN_CHARGE_MODE=legacy`: 11 tickets vs 22, no ticket added.
The 11 dropped (325, 358, 679, 913, 1178, 1197, 1631, 1691, 1732, 1819, 1841) I spot-checked
(1631/1691/1732/1819/1841): their ticket-charged attempts are 7.2-10.4 days old, or ran on a
model other than the ticket's current pin. That is the intended behaviour.

## Defects (both in `self_heal.backend_in_cooldown`, `self_heal.py:400-416`)

Probe: `python3 ops/wq2146-verify/probe_cooldown_reader.py` (exit 1 today). It compares
against the sibling reader `local_capacity.cooled_down_backends` (`local_capacity.py:74-91`),
which the same rebuild ships and whose docstring lists both file shapes.

1. **Bare-epoch entry never lapses.** `{"hathor-llama8b": <epoch>}` is a documented shape. For a
   non-dict entry `backend_in_cooldown` sets `until = None` and returns True, so a cooldown that
   expired an hour ago still reads as present. A RUNG_FAULT ticket in that state is skipped with
   `rung_backend_in_cooldown` on every pass, forever. Fail-closed, but it defeats the point of
   the RUNG_FAULT rule.
2. **Keys carrying `@effort` are not matched.** `local_capacity` normalises with `base_name()`;
   `self_heal` compares the raw name. A live `{"hathor-llama8b@low": {"until": <future>}}` reads as
   not cooling down, leaving only the health-URL probe (2 s timeout) to stop a release into a
   backend that was just marked bad. Fail-open.

Why it does not block: the spec item names "present in the file" as the rule, no code writes the
file yet (step 4 writer, `DISPATCH_REBUILD_SPEC.md:105(c)`), and the acceptance PROOF does not
touch the release path. The fix is one line: delegate to `local_capacity.cooled_down_backends()`.

## Smaller notes (no action)

- `rung_backend` (`self_heal.py:427-439`) returns None for a ticket pinned to a cloud model, so a
  RUNG_FAULT block on a cloud rung is never auto-released (`backend_unresolved`). Acceptable:
  RUNG_FAULT is specified for local backends.
- `_predates_edit` infers an edit from attempt records because wq stores no edit timestamp
  (`runaway.py:73-79`). An edit back to a pin the ticket already ran under leaves no trace, so
  those older attempts still count. Conservative direction; not worth a ticket.
