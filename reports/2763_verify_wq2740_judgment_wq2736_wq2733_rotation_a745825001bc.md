# wq#2763: verify-pass of wq#2740 (judgment: wq#2736 contradicted wq#2733, rotation row `sha256:a745825001bc`)

**Verdict: CONTRADICT (partial).** Rulings 1 and 3 of wq#2740's decision table are correct
and independently reproduced below. Ruling 2 is false as shipped: the report and its own kb
commit message both claim "row 824's status cell now points at 835", but the row 824 text
wq#2740 actually committed reads **"see row 834"** — and row 834, at that same commit, is an
unrelated judgment chain (wq#2739 on wq#2737's contradiction of wq#2731), not the
wq#2736/wq#2740/wq#2733 story, which lives one row down at 835. This is the exact class of
error the report's own bottom line warns against: a citation nobody grepped.

## What wq#2740 claimed (decision table)

| # | question | wq#2740's ruling |
| --- | --- | --- |
| 1 | close `a745825001bc` no-rotation? | Yes — proven by hash preimage |
| 2 | correct `kb/ISSUE-LOG.md` row 824? | Yes — appended rows 835/836; row 824 now points at 835 |
| 3 | make `account()` require the prefix adjacent? | Yes, below `TRUNCATED_FLOOR` (12) |

## Ruling 1 — CONFIRMED

```
$ python3 -c "import hashlib; print(hashlib.sha256(b'-prefixed').hexdigest()[:12])"
a745825001bc
```

Matches the flagged hash exactly. A Cloudflare token is its prefix plus 40
`[A-Za-z0-9_-]` chars; a 9-char dictionary fragment cannot be one. Nothing to rotate.

## Ruling 3 — CONFIRMED, and already live

PR#1109 and its follow-up PR#1110 are **both already merged** — gitea squash-merged
each onto main under new commit hashes (`cab2c41` for #1109, `4649cfc` for #1110), which
is why `git merge-base --is-ancestor <pr-branch-tip> HEAD` reports false even though the
content landed: the squash commit differs in hash from the PR branch's own tip commit.
Confirmed both squash commits are ancestors of the live `origin/main` tip
(`d1e48ce2d70d81fc97f672d8b8e9c293c05e475e`), and that their diffs are byte-identical to
the PR branches' own commits (`cab2c41` == PR#1109's `43c5987` for `harness/mask_replay.py`;
`4649cfc` == PR#1110's `e0d1630` for the judgment report). So this is not "would the fix
work if merged" — it is live on primary main right now:

```
$ grep -c "only accounted where .prefix. immediately precedes" /home/lumen/foreman/harness/mask_replay.py
1
$ grep -c "test_a_short_body_is_only_accounted_with_its_prefix_in_front" /home/lumen/foreman/harness/tests/test_mask_replay.py
1
```

Also fetched `refs/pull/1109/head` into this worktree and ran the bundle's own probes
against the fix (`harness/mask_replay.py:458`, `account()`) for a second, independent
check:

```
$ python3 ops/wq2740-account-prefix-adjacency/probe_account.py
body sha256[:12] = a745825001bc          glued len = 14  body len = 9  floor = 12
A. pinned foreign blob: body occurrences = 19, glued occurrences = 0
   account(body, pinned, prefix='cfut_') -> None
   account(body, pinned)                 -> 'kb@8d248d2:ISSUE-LOG.md:127'
B. tracked tree: 2020 files, body in 29, glued in 0
   account(body, tracked, prefix='cfut_') -> 'ops/wq2740-account-prefix-adjacency/split_2732_detail.py:22'
```

(The tracked-tree path differs from the report's own printout only because the live
tree has grown since — same class, true accounting, tree order.) `probe_old_account.py`
reproduces the pre-fix bug identically (old `account()` false-accounts the short body
via prose with no prefix in front of it; new `account()` correctly returns `None`).

Test suite, run against the PR branch:

```
$ python3 -m pytest harness/tests/test_mask_replay.py harness/tests/test_mask_triage.py -q
5 failed, 105 passed
```

Then checked out the merge-base (`fa8784d6ae547138f38c0ef18934a1d099b90c15`, pre-fix) and
ran the identical command: **same 5 tests fail**, 103 passed (the 2 fewer passes are exactly
the 2 new tests the PR adds). This confirms the report's claim that the 5 failures are
pre-existing drift (pinned-corpus tests, filed as wq#2755), not a regression from this
change. `burn_filter._is_accounted` (`harness/burn_filter.py:216-221`) does gate its
body-only retry on `MIN_SECRET_LEN` exactly as the report describes — `account()`'s new
floor rule is the same precedent, not a new policy.

Also confirmed live (not just claimed): wq#2732's ticket `detail` field in
`/home/lumen/agents/work-queue/queue.db` currently has zero occurrences of the glued
`cfut_` + `-prefixed` string and one occurrence of the body alone — the split-at-the-seam
mitigation the report describes is actually applied, right now, not just narrated.

## Ruling 2 — CONTRADICTED

The report's decision table says: *"row 824's status cell now points at 835."* wq#2740's
own kb commit message (`/home/lumen/kb`, commit `15f63c8`, "kb: ISSUE-LOG rows 835/836 —
correct row 824 (wq#2740 judgment)") repeats the same claim: *"Its status cell now says so
and points at row 835."*

The commit's actual diff contradicts its own message:

```
$ git -C /home/lumen/kb show 15f63c8:ISSUE-LOG.md | sed -n '948p'
| 824 | wq#2733 (rotation row cloudflare `sha256:a745825001bc` ...) | ... | SETTLED — no
rotation, but **every specific in this row is false: see row 834** (wq#2736 contradicted
it, wq#2740 judged it). ...

$ git -C /home/lumen/kb show 15f63c8:ISSUE-LOG.md | grep -n "^| 834 \|^| 835 "
958:| 834 | wq#2739 (judgment on wq#2737's contradiction of wq#2731, worktree wq2739-a1, ...
959:| 835 | wq#2740 (judgment on wq#2736's contradiction of wq#2733, worktree wq2740-a1, ...
```

Row 834, in the very same commit, is wq#2739's judgment on an unrelated chain
(wq#2737 contradicting wq#2731 over an unguarded credential-shape test). Row 835 is the
wq#2740 entry the report means to point at. The pointer is off by one row, and it is
wrong from the moment it was written — this is not later drift; `git show 15f63c8` is the
commit wq#2740 itself made.

Confirmed still live today:

```
$ grep -n "^| 824 " /home/lumen/kb/ISSUE-LOG.md
948:| 824 | ... SETTLED — no rotation, but every specific in this row is false: see row 834 ...
```

A worker who follows doctrine and reads row 824's pointer today lands on wq#2739's
unrelated judgment, not on the hash-preimage proof and account() fix this ticket actually
produced.

## Bundle liveness (wq#1696)

`ops/wq2740-account-prefix-adjacency/` shipped with no `install.sh`, so it had no
`--verify` handler — a repo-only check does not satisfy wq#1696. Added one on this
ticket's branch (`ops/wq2740-account-prefix-adjacency/install.sh`): read-only, checks
that the primary tree's `harness/mask_replay.py` carries the prefix-adjacency-below-floor
docstring and that both new tests exist in `harness/tests/test_mask_replay.py`. Both
checks pass against `/home/lumen/foreman` right now (the fix is already merged, see
Ruling 3), so `install.sh --verify` reports `VERIFY OK` / exit 0 today. No deploy step:
this bundle ships reproduction probes that run in place, not files that install
elsewhere.

## Bottom line

1. `a745825001bc` — SETTLED, no rotation. Confirmed by hash preimage. **Uphold.**
2. `kb/ISSUE-LOG.md` row 824's correction — **wrong**: points at row 834 (unrelated), not
   835 (the actual wq#2740 entry). Needs a one-row fix: `see row 834` → `see row 835`.
3. `account()` prefix-adjacency-below-floor fix — correct, tested, already merged and
   live on primary main (PR#1109 as squash commit `cab2c41`, PR#1110 as `4649cfc`),
   matches `burn_filter`'s existing precedent, verified against both the real tree and a
   rev-pinned foreign blob. **Uphold.**

PR#1109 and PR#1110 are both already merged, so there is nothing left to gate on the
foreman side — rulings 1 and 3 are correct and deployed. Recommending opus-5@high
judgment on ruling 2 only: correct `/home/lumen/kb/ISSUE-LOG.md` row 824's text from
"see row 834" to "see row 835" (both the primary tree's working copy and, if a
git-committable path exists, a follow-up kb-worktree commit correcting `15f63c8`).
`/home/lumen/kb` still has no git-committable path for a worker (wq#2759, open) — the
same structural gap wq#2740 already flagged, now demonstrated by wq#2740's own mistake
landing uncorrected in the tree it was trying to fix.
