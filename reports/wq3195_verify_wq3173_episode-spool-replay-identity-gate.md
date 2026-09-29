# wq#3195 verify: wq#3173 [memory] 33 episode commits rejected by memoryd identity gate

Verdict: **CONTRADICT** (escalates per ticket policy to opus-5@high judgment)

PR: `lumen/minerva` PR#55, already merged to `main` as `833bc1273018` (rebased
during merge onto `b25333a` wq#3183; diffed `833bc12` against the PR branch tip
`f2d4d5a` for every wq#3173-touched file — `lumen_episode.py`,
`lumen_episode_spool_replay.py`, `tests/test_episode_spool.py`,
`tests/test_minerva_audit_chokepoint.py`, `staging/*` — byte-identical, so the
review below is against what is actually live on `main`).

Repo access note: this BUILD worker's worktree/add-dirs never included
`/home/lumen/minerva` (same dispatch gap logged at `kb/ISSUE-LOG.md` rows
984b/985/986). Used row 985's documented workaround: `git init` an empty repo
under this worker's own `/tmp/fworker-wq3195/`, then `git fetch
http://100.121.17.5:3000/lumen/minerva.git main` and `refs/pull/55/head` — URLs
aren't filesystem paths, so the sandbox never sees them. No add-dir grant
needed or used.

## What wq#3173 claimed

Ticket: memoryd 24h showed 91×`POST /commit` 201 vs 33×403 (roughly **1 in 4**
episode commits rejected) — `'agent X is not in the wq session registry'`,
`'agent does not hold a live claim on wq#N'`, `'agent writes must carry
source_ref=wq#N (I-7)'`. The gate is correct; the bug is client-side:
`lumen_episode.py commit` printed the same "committed" line and exited 0 on a
reject as on a real accept, so a session never learned its write missed the
governed audit trail. Ticket's own FIX text: *"lumen_episode.py commit exits
non-zero with the reject reason in the first line, spools the payload to
~/.episode_spool/, and a timer replays spooled entries once the agent holds a
claim."*

Done-notes claim 39+10 tests passing and that "4 pre-existing chokepoint tests
broken by commit()'s new (nid, reject_reason) return" were fixed.

## What I checked

Read `lumen_episode.py`, `lumen_episode_spool_replay.py`, both test files, and
the two systemd units cold. Ran the PR's own tests in this worktree:

- `tests/test_episode_spool.py`: 15 passed.
- `tests/test_minerva_audit_chokepoint.py`: 24 passed.

Confirmed `commit()`'s new `(nid, reject_reason)` return, the CLI's new exit
codes (0 accept / 2 bad-kind / 3 not-persisted / **4 memoryd-reject**), the 403
body now being parsed for `reason` (previously discarded — genuinely fixes a
real bug), and the spool/replay round-trip (`_spool_rejected` →
`lumen_episode_spool_replay.replay_one`, same `_memoryd_commit` code path,
original `$WQ_AGENT` correctly saved/restored, attempts/expiry handled). No
defect found in any of that — it does what the ticket asked, correctly, in
isolation.

Then searched for every caller of `lumen_episode.commit()`/`lumen_episode.py
commit` reachable from this worker's granted dirs (`foreman`, `bin`,
`agents/work-queue`, `kb`, `mailbot`, `pokemon`, `projects/nest`), since a
CLI-contract change (new nonzero exit code) is exactly the kind of thing that
breaks a caller nobody in the PR's own repo would see.

## Contradiction found (CONFIRMED, reproduced)

`foreman/memory/episodic.py:commit_to_archive()` (the function `fworker.py`
calls **unconditionally after every attempt of every ticket**,
`fworker.py:3987`, inside `commit_episode_to_archive()`) shells out to exactly
this CLI:

```python
cmd = [sys.executable, episode_bin or episode_bin_path(), "commit", ...]
subprocess.run(cmd, check=True, timeout=timeout,
               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
return digest
```

`check=True` means any nonzero exit raises `subprocess.CalledProcessError`.
Before this PR, `lumen_episode.py commit` exited 0 unconditionally (that was
the bug wq#3173 fixed). After this PR, it exits 4 on a memoryd identity-gate
reject — the ~26% (33/124) case the ticket itself measured. Reproduced
end-to-end against the real `episodic.py` (not mocked), with a stub CLI
matching the new exit contract proven by the PR's own
`test_cli_commit_exits_4_with_reason_first_line_on_reject`:

```
$ python3 -c "
import sys; sys.path.insert(0, '/home/lumen/foreman/memory')
import episodic
episodic.append_event(8888, {'type':'attempt_end','attempt':1,'classification':'SUCCESS','title':'t'}, runs_root='/tmp/fworker-wq3195/runs2')
try:
    d = episodic.commit_to_archive(8888, runs_root='/tmp/fworker-wq3195/runs2', episode_bin='/tmp/fworker-wq3195/repro_stub_episode.py')
    print('NO EXCEPTION, digest=', d)
except Exception as e:
    print('RAISED:', type(e).__name__, e)
"
RAISED: CalledProcessError Command '[...python3, .../repro_stub_episode.py, commit, ...]' returned non-zero exit status 4.
```

`commit_episode_to_archive()` in `fworker.py` (1872-1897) does catch this —
but the catch is on the *outer* try, which means:

1. `episodic.sweep_episodes(runs_root=RUNS_ROOT)` (the line right after
   `commit_to_archive` inside the same `try`) never runs for that attempt —
   the 30-day episode-file sweep silently stops firing on every rejected
   commit, not just the affected ticket's own file.
2. `digest` is never returned, so the function returns early and
   `_memory_import("obsidian_bridge").write_note(ticket_id, digest)` **never
   runs** — on a memoryd reject, the episode no longer reaches Obsidian either.

That second point directly contradicts the design contract `fworker.py`
states for itself two lines above the call site (1879-1884): *"the Obsidian
vault is the layer meant to actually persist across chat sessions, so every
ticket that reaches the archive writes there too. **Independently
best-effort**: a dead vault mount must not take down the archival_notes commit
**or vice versa**."* Pre-PR, a memoryd reject still exited 0, so the Obsidian
write happened (fail-open, as designed) even though the audited row didn't
land. Post-PR, the exact class of write wq#3173 was written to make *more*
visible (identity-gate rejects) is now the one class of write that also loses
its Obsidian note — a second, silent loss layered on top of the first, on
~1-in-4 episode commits fleet-wide. This is a regression in a caller wq#3173
did not touch, is not covered by any test in either repo (`foreman`'s own
`test_episodic.py` mocks `subprocess.run` to never raise, in every existing
test), and directly undermines wq#3173's own stated goal ("sessions lose
episodes silently") by adding a second silent loss on the reject path in the
one place (`fworker.py`) that runs on literally every ticket, including this
one.

## Secondary observation (not escalation-worthy on its own)

`commit()`'s new spool gate is `if nid is None and agent:` — broader than "the
three identity-gate reasons" the docstring frames it as. Any `_memoryd_commit`
failure with an identified agent gets spooled, including a plain network
error or a 500 from a down daemon (`except Exception as e: return None,
f'{type(e).__name__}: ...'`), not just the three named 403 reject reasons.
Harmless in effect (`lumen_episode_spool_replay.py` just retries and
eventually expires a permanently-unreplayable entry after `MAX_AGE_DAYS`/
`MAX_ATTEMPTS`), but it means spool volume can run higher than the ~33/day the
ticket measured, and an entry with no `source_ref` (reject reason 3) will
retry identically forever until it ages out, since nothing about a bare retry
changes whether `source_ref` is present.

## Things I checked and did not find a contradiction in

- Spool file handling: `tempfile.mkstemp` + `os.chmod(0o600)` + `os.replace`
  (atomic, no partial-write window), `EPISODE_SPOOL_DIR` created `0o700`.
- Replay's original-agent-identity swap: `WQ_AGENT` saved/restored correctly
  including the not-previously-set case (verified by the PR's own
  `test_replay_one_replays_under_the_original_agent_identity`, and by reading
  the `finally` block).
- `_memoryd_commit`'s new 403-body parsing: falls back to the old generic
  `commit HTTP {code}` message on non-JSON bodies, doesn't raise.
- Systemd units: timer installed disabled (`Frank-gated enable`, matches
  instinct 3's "queue the physical/policy act, don't gate on it" pattern used
  correctly here since enabling a new unattended timer is the one genuinely
  Frank-shaped call in this PR).

## Action taken

Contradiction posted to wq#3173 via `wq note` (evidence + the reproduction
above). Not re-implementing per ticket instruction ("Do NOT re-implement;
verify"). Fix for whoever picks up the follow-up: either make
`episodic.py:commit_to_archive()` tolerant of exit 4 specifically (it's a
successful local write, not a failure — the caller should treat it like the
old exit-0 case: keep the digest, still write to Obsidian, still sweep), or
change `subprocess.run(cmd, check=True, ...)` to `check=False` and branch on
`returncode` before deciding whether to raise/return `None`. Either way,
`foreman/memory/tests/test_episodic.py` needs a test that drives a real
nonzero exit through `commit_to_archive()` — right now every test in that file
monkeypatches `subprocess.run` to never fail, which is exactly why this didn't
get caught before merge.
