# wq#1851 — Adversarial verify-pass of wq#1807 (despag P1-4: backing.py Protocols + control_sqlite)

**Verdict: CONTRADICT** — the shipped diff (`2a7c136` `feat(despag): wq1807 P1-4 backing
protocols + control_sqlite`, HEAD of `/home/lumen/foreman` at review time,
http://100.121.17.5:3000/lumen/claude-foreman/pulls/526) reproduces its own claimed test
count exactly (62/62 pytest, live), but two real, independently-reproduced defects sit
outside what the shipped suite exercises. One of them breaks the exact feature this
ticket's own report claims is "wired": a second normal request to the same UHP session
within 5 minutes of the first is incorrectly rejected as busy, even though nothing is
in flight.

## What was shipped

`despag/backing/__init__.py` (4 `typing.Protocol` interfaces — `GraphStore`, `BlobStore`,
`SecretStore`, `WorkspaceFiles` — plus local impls `SqliteGraphStore`, `FileBlobStore`,
`FileSecretStore`, `LocalWorkspaceFiles`, and `make_backing()`), `despag/backing/
control_sqlite.py` (`ControlStore`: idempotency reserve/settle/release, session
lease+fence, response terminal latch — all single-transaction `BEGIN IMMEDIATE`),
`despag/app.py` wiring (`Idempotency-Key` on `POST /v1/responses`, session lease via
`metadata.session_id`/`previous_response_id`, 409 `session_busy`), `despag/reports/
p1-4_backing.md`, and three test files (`test_backing.py`, `test_control.py`,
additions to `test_uhp.py`).

## Method — ran it, not just read it

- `cd /home/lumen/worktrees/wq1851-a1 && python3 -m pytest despag/tests/test_backing.py
  despag/tests/test_control.py despag/tests/test_uhp.py -q` → **62 passed**, matches the
  report's claimed count.
- Direct Python probes against `despag.backing.FileBlobStore` (not through HTTP — no
  route uses `BlobStore` yet in this PR, so this is a probe of the shipped module, not
  the wired app).
- A live `fastapi.testclient.TestClient(despag.app.app)` instance, POSTing `/v1/responses`
  the same way a real UHP client would, to check the claimed "409 session_busy... when
  another request already holds it" behavior end to end.

## Claim-by-claim check

1. **62/62 pytest, as claimed** — reproduced exactly.

2. **FINDING (severity: high) — `FileBlobStore`'s path-escape check is a bare
   `str.startswith`, so it fails to isolate a `kb` from a sibling directory whose name
   happens to share its prefix** (`despag/backing/__init__.py:177-182`):
   ```python
   def _p(self, kb: str, file_id: str) -> Path:
       p = (self._root / kb / file_id).resolve()
       base = (self._root / kb).resolve()
       if not str(p).startswith(str(base)):
           raise ValueError(f"blob id escapes store: {file_id!r}")
       return p
   ```
   `str(base)` has no trailing separator, so `"/root/kb1x".startswith("/root/kb1")` is
   `True` even though `kb1x` is a different, sibling `kb`. Reproduced live:
   ```python
   store = FileBlobStore(tmp)
   await store.put("kb1x", "secret.txt", b"TOP SECRET DATA")   # a different kb's blob
   await store.get("kb1", "../kb1x/secret.txt")                # caller only has "kb1"
   # -> b'TOP SECRET DATA'   (should have raised ValueError / returned None)
   ```
   `put`/`delete` are exploitable the same way (`get`'s `_p` call is shared code). This
   is exactly the isolation boundary the module's own docstring promises ("content-
   addressed-by-caller bytes, **keyed (kb, file_id)**") and the shipped test suite
   already tests the *adjacent* case — `test_backing.py:90-91` covers `"../../etc/
   passwd"`, which escapes the prefix check entirely and is correctly rejected — but
   never tests a same-prefix sibling directory, which is the case the prefix check
   itself is structurally blind to. Not yet reachable over HTTP in this PR (no route
   wires `BlobStore` to a request yet), so today's blast radius is zero, but the
   vulnerable code ships now and the fix belongs in this module, not in whichever
   future caller trips over it. `FileBlobStore.list()` has the same `startswith` check
   at `despag/backing/__init__.py:214` (`top`/`base`) — probed too; not independently
   exploitable today because `list()`'s subsequent prefix-relative-key math happens to
   filter the traversal case out as a side effect, not because the boundary check is
   correct. The boundary check itself is still wrong and should be fixed at the same
   time (e.g. compare against `base` with an added trailing `os.sep`, or use
   `Path.is_relative_to`).

3. **FINDING (severity: high) — the session lease taken by `POST /v1/responses` is
   never released, so a second normal request to the same session within
   `SESSION_LEASE_TTL_S` (300s) is rejected `409 session_busy` even though nothing is
   in flight** (`despag/app.py:284-306`). `create_response` calls `CONTROL.idem_release`
   after the (stub) response is produced (`:305`, and again on the lease-conflict path
   at `:288`), but there is no matching `CONTROL.lease_release` call anywhere in
   `app.py` — `grep -n lease_release despag/app.py` returns nothing; the primitive is
   only exercised by `ControlStore`'s own unit test
   (`despag/tests/test_control.py:175-178`), never by the route. Reproduced live against
   `TestClient`:
   ```
   POST /v1/responses {"metadata": {"session_id": "hsess_a"}}   -> 501 lumen_not_implemented
   POST /v1/responses {"metadata": {"session_id": "hsess_a"}}   -> 409 session_busy
   ```
   Both calls are made back-to-back by the same test process, well within
   `fresh_s` (75s default) and `SESSION_LEASE_TTL_S` (300s) — there is no concurrency
   and no task actually in flight (the P1-1 stub returns synchronously every time), yet
   the second call is rejected. The report (`despag/reports/p1-4_backing.md:89-92`)
   describes this as "409 `session_busy` via the session lease... `reject_on_conflict=
   True`", and the shipped test
   (`test_responses_session_busy_when_lease_is_held_by_another_owner`,
   `despag/tests/test_uhp.py:122-135`) only covers a lease pre-admitted by a *different*
   owner via a direct `ControlStore` call — it never exercises two consecutive real
   HTTP turns on the same session, which is the normal multi-turn conversation case and
   the one this bug actually breaks. As shipped, `POST /v1/responses` cannot be called
   twice for the same session within 5 minutes without the second call failing — not an
   edge case, the default path for any real UHP conversation.

## Adversarial angles tried, nothing found

- `SqliteGraphStore.upsert`'s merge-not-replace semantics, `add_edge` idempotency via
  `uuid5` — both correct and match the docstring, reproduced beyond the shipped tests
  (re-upsert with overlapping+new keys, double `add_edge` with the label swapped to
  confirm it's part of the id).
- `FileSecretStore` encryption: `put(..., require_encryption=True)` with no
  `DESPAG_SECRET_KEY` set raises `SecretsNotConfigured` rather than writing plaintext —
  confirmed live, including the boundary case of a value already on disk unencrypted
  from before a key was configured (`_open` correctly treats a non-`despagenc1:`-
  prefixed value as legacy plaintext, not a decrypt error).
- `LocalWorkspaceFiles`/`_safe_member` — unlike `FileBlobStore`, this rejects by
  `posixpath.normpath` equality rather than a resolved-path prefix check, so it is not
  vulnerable to the same sibling-prefix class; tried `sid` values containing path
  separators and `..` — all correctly rejected by the `sid` sanitization regex at
  `despag/backing/__init__.py:336`.
- `ControlStore`'s terminal latch (`resp_mark_terminal`) — confirmed one-way: a
  `completed` response cannot be moved to `failed` by a later call, and a racing
  `resp_put_running` after terminal is a no-op. `resp_mark_terminal` (`control_sqlite.py:
  277-297`) has no `except sqlite3.Error` clause (unlike every sibling method), only a
  bare `finally: con.close()` — on a write error the transaction is left open and
  relies on Python's `sqlite3` closing an unfinished transaction as an implicit
  rollback. Not escalated as a finding: verified this does not leave the DB in a torn
  state (closing without COMMIT discards the pending write), just inconsistent style
  versus the rest of the file.
- Idempotency replay path (`_idem_admit`, `despag/app.py:231-249`) — bounded-wait
  polling loop, correctly returns `"busy"` after 10 iterations rather than looping
  forever; correctly re-reserves if the winner's reservation disappears mid-wait.

## Bottom line

CONTRADICT. The measurable claim (62/62 pytest) is honest and reproduced live. But
adversarial probing beyond the shipped tests found two real defects: a `kb`-isolation
escape in `FileBlobStore` via a same-prefix sibling directory (#2 — not yet reachable
over HTTP in this PR, but shipped and wrong), and a session lease that is admitted but
never released by the one route that admits it, so any second real turn on the same UHP
session within 5 minutes is falsely rejected `session_busy` (#3 — reachable today,
breaks the default multi-turn case). Neither is re-implemented here per the verify-pass
archetype — filed as a follow-up fix ticket (wq#1854, `claude-opus-5@high`, deps on
wq#1807) per this archetype's escalation rule.
