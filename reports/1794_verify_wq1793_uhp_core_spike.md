# wq#1794 verify-pass: wq#1793 de-spaghettifier spike: UHP Core S-04/S-09/C-03 over `claude -p`

Diff verified: commit 4e9cf27 (claude-foreman/foreman repo, `main`), files
`spikes/wq1793_uhp_core/{app.py,smoke_test.py}` (174 + 91 ln) and `evidence.json`.
Read cold, then probed independently — the author's own `smoke_test.py` only
exercises the `Session` class directly (never through FastAPI); I additionally
drove the real HTTP endpoints (`TestClient`, both sequential and multi-threaded
concurrent requests) to check claims the shipped test never touches.

## What checked out

- `python3 spikes/wq1793_uhp_core/smoke_test.py`: all 4 checks pass exactly as
  claimed (`build_env`/`build_claude_cmd` reused verbatim, 3-session cap,
  gapless replay, killpg-reaches-grandchild).
- Confirmed `harness/fworker.py`'s `build_env`/`build_claude_cmd` signatures
  match every call site in `app.py`, and the commit touches no line of
  `harness/fworker.py` — the "reuse, don't fork the supervisor loop" claim
  holds structurally, not just by docstring assertion.
- Independently re-verified the acceptance-gap claim rather than trusting it:
  `projects/de-spaghettifier/decision/PHASE0_WIRING_SPEC.md` and a
  `uhp-conformance` binary are unreachable from *this* worker's add_dirs too
  (different grant set than the author's), `find`/`grep` for both came up
  empty everywhere reachable, `which uhp-conformance` fails, and grepping the
  whole repo for the cited check IDs (`S-04`, `S-09`, `C-03`) outside
  `spikes/wq1793_uhp_core/` returns nothing — the tool genuinely isn't wired
  anywhere in reach, not just unread. This is the same shape ISSUE-LOG #328
  logged and the same resolution pattern #272/#298/#294/#326 already
  established for this fleet (ship the reachable half, log the rest as an
  open dependency) — not a shortcut invented for this ticket.
- Wrote two adversarial probes (not part of the shipped diff, deleted after
  running — `git status` clean) that go past the author's own test:
  - **Popen-failure path** (`claude` is not on this worker's PATH either):
    POSTing to `/sessions` hits `subprocess.Popen`'s `FileNotFoundError`
    inside `sess.start()`. Confirmed the `except Exception:
    SESSION_SEM.release(); raise` path in `start_session` (app.py:123-125)
    fires correctly — semaphore value before/after one failed request:
    `3 -> 3` (no leak), and 5 sequential failed POSTs all return `500`
    cleanly, never a stray `429` from a leaked slot.
  - **Real concurrency, through HTTP, with threads** (author's cap test
    manipulates the semaphore directly, never fires overlapping HTTP
    requests): monkeypatched `build_claude_cmd` to a 3-line `sleep`-paced
    shell command, fired 5 concurrent `POST /sessions` from 5 threads —
    result `[200, 200, 200, 429, 429]`, exactly the stated 3-session cap,
    under real thread-scheduling races, not a sequential simulation.
  - **SSE gapless replay and cancel, through HTTP**: subscribed to
    `GET /sessions/{id}/events` 0.4s after start (well after `line1`/`line2`
    had already been produced) — response still contained `line1` through
    `line3` from the start, matching the "gapless" claim end-to-end, not just
    at the `Session.events` list level. `POST /sessions/{id}/cancel` on a
    live session followed by `GET /sessions/{id}` shows
    `{"done": true, "cancelled": true}` within 0.3s.
- Line budget: `app.py` is 175 lines, under the ticket's `<=300 ln` cap.

## Minor (not escalation-worthy)

- `cancel()` (app.py:85-92) has the generic PGID-reuse race present in most
  killpg-based cancel implementations: if the child exits and the OS recycles
  its pgid between the `self.done.is_set()` check and `os.killpg`, the signal
  could in theory land on an unrelated process group. Vanishingly unlikely in
  practice (would need a pgid recycle inside a single Python statement gap)
  and not a regression the author introduced — same class of issue exists in
  most process-group-based supervisors, including (per the module's own
  citation) `fworker.run_child`'s own detach discipline. Not filing as a
  defect.
- `evidence.json`'s `AC1` citation (`app.py:60-97`) spans `Session.start`/
  `_reader`/`cancel` plus the start of the `StartRequest` model — a
  reasonable pointer to the core mechanism, if slightly loose at the edges.
  Not misleading enough to contradict on.

## Verdict

CONFIRMED. The shipped mechanics (3-session cap, gapless SSE replay, killpg
group-cancel, verbatim reuse of `fworker.build_env`/`build_claude_cmd`) hold
up under independent adversarial probing that goes beyond the author's own
test, including through the real FastAPI HTTP layer and real thread
concurrency, not just the `Session` class in isolation. The one stated gap —
`uhp-conformance --class core` (S-04/S-09/C-03) never run, so the ticket's
literal "Acceptance = suite JSON report" was not produced — is real, but
independently reconfirmed as environmentally forced (tool/spec unreachable
from any worker's add_dirs, verified fresh here, not trusted from the PR
notes) and matches an established, previously-accepted fleet resolution
pattern (ISSUE-LOG #328, precedented by #272/#298/#294/#326) rather than a
shortcut. No new defect found; nothing to escalate.
