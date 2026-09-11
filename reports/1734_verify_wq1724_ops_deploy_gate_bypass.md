# wq#1734 — Adversarial verify-pass of wq#1724 (ops install.sh deploy-evidence gate)

**Verdict: CONTRADICT** — the deploy-evidence check both PRs shipped is trivially defeated
by pointing `deploy_target`/`--deployed` at a file that ships *inside the ops artifact
itself*, rather than at a genuinely deployed, independent location. The check only verifies
"some file's bytes match a file the artifact ships" — it never verifies the named target is
outside the artifact directory it's supposed to prove reached somewhere else. This is the
exact wq#1632 failure mode (install.sh landed, nothing deployed) wearing a `deploy_target`
flag that satisfies the new gate without anything having deployed at all.

## Ticket-metadata note (not the main finding, but worth recording)

wq#1734's own detail line names "PR#92" and "Verify repo: /home/lumen/foreman". Gitea PR#92
on `claude-foreman` (fetched via `refs/pull/92/head`, tip `8ff0d47`) is an unrelated,
already-merged PR ("dispatch-config-lint — detect lane/config drift") — it has nothing to do
with wq#1724. wq#1724's own done-notes name the actual two PRs: **lumen-bin PR#92** (merged
`9948f63`, `bin/wq`'s `ops_deploy_check`) and **claude-foreman PR#427** (merged `fb00e22`,
`harness/acceptance.py`). The dispatcher appears to have carried the *lumen-bin* PR number
into a ticket scoped to the *foreman* repo. Verified against `origin/main` in this worktree:
`fb00e22` ("fix(harness): require deploy evidence for ops install.sh cites") is an ancestor
of `origin/main`'s `harness/acceptance.py` history — that's the real diff reviewed below.

## What was shipped (PR#427, commit fb00e22, merged to main)

`harness/acceptance.py:403-438` — `_OPS_INSTALL_RE` matches an evidence citation ending in
`ops/wq<N>*/install.sh`; `_ops_deploy_evidence()` then requires the criterion's
`deploy_target` to name an existing file whose sha256 matches some other file shipped
alongside `install.sh` in the same artifact directory (excluding `install.sh` itself from
candidacy). `harness/tests/test_evidence_gate.py:218-279` adds three tests: no
`deploy_target` → rejected; `deploy_target` byte-matching the shipped payload → accepted;
`deploy_target` with mismatched bytes → rejected. All 36 tests in the file pass
(`python3 -m pytest tests/test_evidence_gate.py -q` → `36 passed`).

The sibling `bin/wq` change (`ops_deploy_check`, lumen-bin PR#92/`9948f63`) implements the
identical algorithm against `--deployed` for `wq done` (`/home/lumen/bin/wq:2487-2512`) —
same structure, same gap, described below.

## The bypass

`_ops_deploy_evidence` (`harness/acceptance.py:420-438`) walks `art_dir` (the ops artifact's
own directory, `os.path.dirname(abspath)` of the cited `install.sh`) looking for a file
whose bytes match `deploy_target`. It excludes only the literal filename `install.sh` from
candidacy — it does **not** require `deploy_target` to resolve to a path outside `art_dir`.
So a `deploy_target` that simply points at the artifact's own shipped payload file (still
sitting right next to `install.sh`, never installed anywhere) matches itself and passes:

```python
# harness/acceptance.py:420-438, unmodified by this finding
def _ops_deploy_evidence(entry, abspath):
    target = str(entry.get("deploy_target") or "").strip()
    art_dir = os.path.dirname(abspath)
    ...
    target_hash = _sha256(target)
    for root, _dirs, files in os.walk(art_dir):
        for name in files:
            if name == "install.sh":
                continue
            cand = os.path.join(root, name)
            if target_hash is not None and _sha256(cand) == target_hash:
                return True, f"{target} byte-matches {cand}"   # <- cand can BE target
    return False, ...
```

Reproduction (standalone, not part of the shipped suite — mirrors the shipped
`_write_ops_artifact` fixture exactly, wq1632-shaped):

```
$ python3 /tmp/probe_wq1734.py
RESULT ok= True
failed= []
```

The probe: commits `foreman/ops/wq1632-fake-fix/{install.sh,lumen_state.py}` on the branch
(same fixture shape as `test_ops_install_citation_*` in the shipped suite), then sets
`deploy_target` to the **absolute path of `lumen_state.py` inside that same ops directory**
— i.e., nothing was ever copied/deployed anywhere; the "target" is just the artifact's own
undeployed payload. `acceptance.verify()` returns `ok=True` with no failures. The function's
own comment (`harness/acceptance.py:424-427`) says `deploy_target` should name "a live file"
— the code never checks that; it only checks a byte match, and the artifact's own copy of
itself always byte-matches.

The same flaw is structurally present in `bin/wq:2487-2512`'s `ops_deploy_check` (lumen-bin
PR#92): it walks `art_dir` and excludes only `name == "install.sh"`, with no check that
`deployed_target` resolves outside `art_dir`. A `wq done <id> --deployed
foreman/ops/wqNNNN-x/payload.py` where `payload.py` is the artifact's own shipped file would
pass `ops_deploy_check` exactly as it does here — closing done on a ticket whose "deploy
evidence" is the artifact pointing at itself.

## Why this matters for the ticket's own stated goal

wq#1724 exists specifically because wq#1632 closed done citing an ops artifact that never
reached its target. The fix's whole job is to make that unrepresentable. It does raise the
bar over "no deploy_target at all" (the plain landed-but-never-run case), and it does catch
a stale/mismatched target (the two other shipped tests). But it does not catch the
self-referential case — an agent (or a careless human) who supplies a `deploy_target`
pointing back into the artifact directory it just wrote gets `ok=True`/exit 0 with zero
actual deployment, i.e., wq#1632's exact scenario plus one extra field that looks like proof
but isn't. Given the class this ticket is meant to close is "ships an ops/ artifact and
closes done without it ever deploying," a target that is itself part of the undeployed
artifact should be the *first* case rejected, not an open gap.

## Other checks — no issues found

- `_OPS_INSTALL_RE` correctly scopes to `ops/wq<digits>.../install.sh` and does not fire on
  unrelated citations; citation path traversal, missing-file, and out-of-diff checks
  (`_check_citation`, lines 441-465) are unaffected by this change and still correct.
- `_sha256`'s `OSError → None` path cannot be abused to force a false match: `target_hash is
  not None` gates every comparison, so an unreadable `deploy_target` fails closed.
- The three new shipped tests are correct as far as they go and all pass; they simply never
  exercise a `deploy_target` located inside `art_dir`.

## Disposition

Do not treat wq#1724/PR#427 (and its lumen-bin sibling PR#92) as having fully closed the
wq#1632 failure class. Recommend: require `deploy_target`/`--deployed` to resolve to a path
outside `art_dir` (e.g., `os.path.realpath(target)` must not start with
`os.path.realpath(art_dir) + os.sep`) in both `harness/acceptance.py:_ops_deploy_evidence`
and `bin/wq:ops_deploy_check`, then extend each shipped test file with the self-referential
case this report reproduces.

Escalating per ticket protocol: same-model-class contradiction (author claude-sonnet-5,
verifier claude-sonnet-5@high) → opus-5@high judgment trigger.
