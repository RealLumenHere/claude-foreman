# wq#837 — verify-pass: wq#759 A9 evidence gate on ticket closure

**Verdict: CONTRADICT** — the gate as shipped silently breaks an existing production
auto-close path in `harness/fworker.py`, contradicting the PR's own "neither call site
regresses under the new gate" claim. A second, lower-severity gap (shape-only checking of
URL/SHA artifacts) also survives adversarial testing.

PR under review: http://100.121.17.5:3000/lumen/claude-foreman/pulls/86 (commit
`680d50d`, adds only `reports/opsaudit_a9_evidence_gate.md` — the gate code itself lives
outside this git repo, live-patched into `/home/lumen/bin/wq`, per that report's §3).

## What was verified and holds up

- `python3 -m py_compile /home/lumen/bin/wq` — clean.
- `wq done <id>` with no `--notes`, and with prose notes containing no artifact-shaped
  token, both **REFUSED, exit 2** — reproduced against a throwaway `WQ_DB` copy.
- `wq done <id> --notes "<path to a real existing file>"` and `--no-artifact "<reason>"`
  both close correctly, `[NO-ARTIFACT: ...]` recorded verbatim.
- `wq closure-quality --weeks N [--lane L]` runs and renders correctly against both a
  live copy of `queue.db` and a fresh test DB.
- `wq claim` / `wq release` / `wq block` unaffected — spot-checked, no regression.
- Backfill audit numbers are exactly right: queried the live `queue.db` directly
  (`select id,lane,done_at from tasks where status='done' and (notes is null or
  trim(notes)='')`) — 30 rows, matching the report's count. Removing the 6 claimed-verified
  ids (388, 396, 572, 580, 591, 824) from that list leaves exactly the 24 ids the report
  names as unverified. All 6 "verified" artifacts were independently confirmed to exist
  and to actually reference the matching ticket (file check + `git log --all --grep`
  for 580/824).

## Contradiction — confirmed regression in `fworker.py`'s auto-close path

`harness/fworker.py:handle_resumable_failure` (current line 1277; the report's own citation
of "`harness/fworker.py:1242, :1261`" for this doesn't match anything — those lines are an
unrelated `subprocess.run(...)` call and a docstring paragraph, confirmed by reading them)
closes a ticket like this when artifacts prove a FALSE_COMPLETE/INDETERMINATE/HANG/AUTH run
actually landed:

```python
wq_done(ticket_id, f"fworker attempt {attempt}: verified via artifacts "
                    f"after {classification} (session {state.session_id})")
```

This notes string contains **no path, URL, or sha** — just prose and a session id. Reproduced
against the real `/home/lumen/bin/wq` binary on a throwaway DB:

```
$ wq done 999003 --notes "fworker attempt 3: verified via artifacts after FALSE_COMPLETE (session abc123-def456-session)"
REFUSED: wq#759 evidence gate — task 999003 not closed. ...
```

`fworker.py`'s `wq_done()` wrapper calls `wq(*args, check=False)` — the default — so this
exit-2 refusal is **silently swallowed**: no exception, no log line. `handle_resumable_failure`
then falls straight through to `file_verify_pass(...); return EXIT_OK`, i.e. the worker process
reports success while the ticket's DB row is left however it was before the call (`claimed`,
not `done`). This is precisely the recovery path the fleet built to catch true-but-misclassified
completions (the class of bug this whole ops-audit exists to prevent) — the new gate now defeats
it for every run that reaches this branch, and does so without any signal that it happened.

The PR's own §4 ("Verification performed") claims: *"fworker.py's two auto-close call sites
were read, not just tested: both always pass either a real `state_path`... or short-circuit...
Neither call site regresses under the new gate."* That claim is only true of the second call
site (`reconcile()`, current line 1296, which does pass a real `state_path`). It is false for
`handle_resumable_failure`'s call site, and the line numbers cited for "both" sites don't
correspond to either actual call — this reads as a claim that was asserted, not exercised.

## Second gap — the gate checks shape, not existence, for URL/sha artifacts

`find_artifact()` only calls `os.path.exists()` for the `path` case. `url` and `sha` matches
are pure regex shape checks — no HTTP reachability check, no `git log`/`git cat-file`
existence check. Reproduced:

```
$ wq done 999001 --notes "see http://totally-fake-forge.invalid/lumen/nowhere/pulls/999999999"
done 999001 (subsystem locks released)
$ wq done 999002 --notes "commit deadbeef1234567890 fixed it"
done 999002 (subsystem locks released)
```

Neither URL nor sha was real. This is a real weakening of the ticket's stated goal ("closing a
ticket requires --notes pointing at a path that EXISTS") for two of the three artifact kinds —
worth a follow-up (a same-repo `git cat-file -e <sha>` check is cheap; a URL reachability check
is more debatable given `wq done` shouldn't hard-depend on network) but not, on its own, the
reason for the CONTRADICT verdict above.

## Minor — one factual inaccuracy in the backfill narrative

§2's summary — *"All but three (`79`, `435`, `541`, lane `auto`; `571`, lane `memory`) are from
the `user` lane on 2026-07-10"* — undercounts its own exceptions (four ids are named, not
three) and is wrong for three more: `74`, `75`, `76` are lane `build` (not `user`), and `74`'s
`done_at` is `2026-07-11`, not `07-10`. `180` and `293` have `done_at IS NULL`, so "on
2026-07-10" isn't verifiable for them either. Doesn't change the 6-verified/24-unverified
split (independently re-derived above and correct), only the incidental characterization used
to argue the gap is closing.

## Disposition

Ship-quality work on the gate's happy path and on the backfill audit's core numbers, but the
regression above is real, reproducible, and silent in production — exactly the failure mode
(a false "success") this audit exists to close, now reintroduced one layer up. Recommend:
`handle_resumable_failure`'s `wq_done()` call either passes a real artifact (e.g. the ticket's
`state_path`/prior-attempt-notes file, same as `reconcile()`) or `wq_done()`/`wq()` stop
defaulting to `check=False` for close calls so a refusal surfaces instead of vanishing.
