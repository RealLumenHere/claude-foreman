# wq#3553 verify-pass: wq#3456 [sentinel] aq/fleet-selfheal alert source — CONTRADICT

**Verdict: CONTRADICT.** Commit `84ef7e6b` (`fix(selfheal): drop leakwatch private-group/env-name
alarms`, merged as part of this branch's HEAD) adds `leakwatch.group_readers()` to suppress the
"group-accessible" alert when a file's group is a user-private group that reaches nobody besides
the owner. The function's own docstring states the hardening guarantee explicitly: *"An
unresolvable gid or owner can't be proven private and still counts."* The gid half of that
guarantee is implemented and tested. The **owner half is not implemented** — when the file's
owner UID doesn't resolve to any passwd entry, the function can still return an empty set,
which makes `check()` silently skip the alert for exactly the class of file the sentence promises
to keep alerting on.

## What the PR does

Diff (`84ef7e6b`, 4 files, `harness/masking.py`, `harness/tests/test_masking.py`,
`ops/wq3354-fleet-selfheal/leakwatch.py`, `ops/wq3354-fleet-selfheal/tests/test_leakwatch.py`,
`token-engine/token_engine_daemon.py`):

1. `leakwatch.group_readers(st)` (`leakwatch.py:105-122`) — accounts other than the owner that
   the file's group lets in. Resolves `grp.getgrgid(st.st_gid)`; if that raises `KeyError` it
   returns a non-empty sentinel (`{f"gid {st.st_gid}"}`), guaranteeing `check()` still alerts.
   Otherwise it unions `gr_mem` with every `pwd.getpwall()` entry whose primary gid matches,
   discards the owner's name, and returns the rest. `check()` (`leakwatch.py:136-153`) now skips
   the alert entirely when this set is empty.
2. `harness/masking.py` / `token-engine/token_engine_daemon.py` — a new `_KV_ENV_NAME` pattern
   (`[A-Z]{1,20}(?:_[A-Z]{1,20}){1,7}`) extends the existing `key`-noun-only identifier
   suppression to ALL_CAPS env-var names, so `claude_memory.md:248`'s `LiteLLM key = ..._KEY`
   line stops being misread as a burned credential.

Both halves are narrowly scoped and match their stated intent. I probed the masking half with
the authors' own tradeoff in mind (a real credential that happens to be a pure-letter,
underscore-joined ALL_CAPS string under a `key` label would also be suppressed) but that gap is
disclosed and deliberately tested against the alternative (digits, lowercase, over-cap runs,
and non-`key` nouns like `password` still fire) — not a contradiction, a documented tradeoff.

## The contradiction, with evidence

`group_readers()`'s docstring (`leakwatch.py:106-111`) makes two promises in one sentence: an
unresolvable **gid** still counts, and an unresolvable **owner** still counts. The code only
implements the first:

```python
try:
    g = grp.getgrgid(st.st_gid)
except KeyError:
    return {f"gid {st.st_gid}"}        # gid unresolvable -> non-empty, still alerts
try:
    owner = pwd.getpwuid(st.st_uid).pw_name
except KeyError:
    owner = None                        # owner unresolvable -> falls through, no sentinel
readers = set(g.gr_mem) | {p.pw_name for p in pwd.getpwall() if p.pw_gid == st.st_gid}
readers.discard(owner)
return readers
```

When the owner's UID has no passwd entry (deleted account, UID left over from a host rebuild or
a restored backup with a different UID mapping — all plausible on a fleet where "every other
node may be rebooted freely" and gets reinstalled) *and* the group is otherwise empty
(`gr_mem == []`, no other account shares its gid as primary — the exact "looks private" shape
the fix targets), `readers` ends up empty. `check()` then does `if not readers: return` and never
records or alerts, even though the owner could not be confirmed to be excluded from that
reader set. This is the inverse of "still counts."

Reproduced directly against this branch's `leakwatch.py` (not a hypothetical reading — ran it):

```
$ python3 -c "..."   # monkeypatches grp.getgrgid (resolves, empty gr_mem),
                      # pwd.getpwuid (raises KeyError), pwd.getpwall (empty)
readers computed: set()
```

And end-to-end through the real `check()` entry point on a real `0640` file, with a fake `ctx`
matching the test harness's own shape:

```
alerts: []
readers computed: set()
RESULT: SILENTLY SUPPRESSED (bug)
```

No alert fires, `ctx.out.alerts` is empty — a secret-store backup, group-readable, whose owner
cannot even be confirmed, passes through leakwatch's `chmod` rule with zero signal.

Negative control confirming this is specifically the owner-resolution gap, not a general
regression: the *existing* passing test `test_group_reaching_another_account_is_reported`
(`test_leakwatch.py:99-116`) exercises the gid-`KeyError` branch and gets the correct non-empty
`{f"gid ..."}` result — that half of the docstring's promise is real and tested. There is no
analogous test for a `pwd.getpwuid` `KeyError`; `test_user_private_group_is_not_reported`
(`test_leakwatch.py:88-97`) only covers the case where the owner resolves fine and the group is
genuinely private. The acceptance probe never includes an input from outside that population —
an owner UID that cannot be resolved — so it could not have caught this (wq#2630's standing
doctrine on this exact failure shape).

## Things checked that are NOT contradictions

- **Full test suite passes as claimed.** `ops/wq3354-fleet-selfheal/tests`: 38/38 pass (PR
  claimed 37; this branch has one additional test from a later, unrelated patch layered on top —
  not a discrepancy in this diff). `token-engine/tests/test_daemon.py` env-name/identifier/key
  subset: 7/7 pass.
- **`harness/tests/test_masking.py`: 185 passed, 1 failed** —
  `test_the_mask_suite_does_not_arm_its_own_tripwire` fails, flagging a `kv-secret` hit in
  `ops/wq3354-fleet-selfheal/tests/test_resource_classes.py:16` ("rotate key: "). Checked: that
  file predates wq#3456 by two unrelated commits (`a34608e7`, `1601d418`) and `harness/masking.py`
  is byte-identical between `84ef7e6b` and this branch's HEAD — this is the same class of
  pre-existing, repo-corpus-drift failure the PR itself disclosed (ISSUE-LOG row 812's "a fixture
  drifting out from under a pinned count, not a code regression"), not something wq#3456
  introduced.
- **The gid-unresolvable path is correctly implemented and tested** (see negative control above).
- **The masking.py / token_engine_daemon.py env-name regex change is internally consistent**
  between the two files and correctly scoped to the `key` noun only (via the pre-existing `kn`
  conditional group, `masking.py:158`); `password`/`secret`/`token`/`credentials` labels are
  unaffected, matching the PR's own claim.
- **This is not a live-vs-deployed drift gap** (unlike wq#3546/wq#3450's sibling finding) — the
  defect reproduces purely in-repo, against the shipped function itself.

## Disposition

`wq#3553` closes with verdict **CONTRADICT**, escalated to opus-5@high per ticket instructions
(archetype verify-pass policy: `escalate_on_contradiction`). Not re-implemented — verify-pass is
not a re-implementation gate. The follow-up judgment pass should decide the fix shape: the
simplest is mirroring the existing gid branch — wrap the `pwd.getpwuid` lookup so a `KeyError`
there also forces a non-empty/sentinel return (e.g. `return {f"uid {st.st_uid}"}` before computing
`readers`), then add the missing test (`pwd.getpwuid` raising `KeyError` via the existing
`_fake_accounts`-style monkeypatch) so the probe can fail on this input too.
