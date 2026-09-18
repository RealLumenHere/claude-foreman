# wq#2017 — judgment on wq#2016's CONTRADICT of wq#1987

wq#2016's adversarial verify-pass of wq#1987 (lumen-bin PR #95, merged
`c988a7e`) contradicted two factual claims in the shipped attribution
commit `d96d5b1`. Both contradictions are UPHELD. Both are now fixed.

The attribution work itself — wq#1675/wq#1659/`lpp-*` deletions — was
re-verified correct by wq#2016 and is not in dispute; nothing about it
changed here.

## 1. UPHELD: the gatus binary was deleted, not "untracked, kept on disk"

`d96d5b1` claimed the trivy precedent: untrack the 50MB ELF from the
index, leave the file on disk. It did not. `/home/lumen/bin/gatus` was
absent from disk entirely.

Why the claim looked true and wasn't: "untrack but keep on disk" is
`git rm --cached <f>`. A plain `git rm` (or a filesystem delete plus
`git add -A`) produces an identical diffstat — `50446590 → 0` — and an
identical-sounding commit message, while destroying the working copy.
The diffstat cannot tell the two apart; `ls` and `git status --ignored`
can. The trivy line did use the `--cached` form and
`/home/lumen/bin/trivy` (168456354 bytes) is still on disk, which is
what gatus should have looked like.

ISSUE-LOG #66 (wq#1279) had already classified this exact file as a real
deployed artifact — it was one of the 12 untracked files deliberately
preserved through a `sync-live` stash cycle — not scratch, not a
throwaway build product.

**Fix applied.** The blob survived in history, so it was restored
byte-for-byte as an untracked, already-gitignored file:

    git -C /home/lumen/bin cat-file blob 847dc04:gatus > /home/lumen/bin/gatus
    chmod 755 /home/lumen/bin/gatus

Verification:

| check | result |
| --- | --- |
| `git -C /home/lumen/bin rev-parse 847dc04:gatus` | `e5ed291bc14c4ba77e783051fe454d6791bc9665` |
| `git -C /home/lumen/bin hash-object /home/lumen/bin/gatus` | `e5ed291bc14c4ba77e783051fe454d6791bc9665` (identical) |
| `ls -la /home/lumen/bin/gatus` | `-rwxr-xr-x … 50446590` — exact original size |
| `file /home/lumen/bin/gatus` | Go ELF 64-bit LSB executable, BuildID `b67fccf3714e491d127a6ee91b2b899b0f192a67` |
| `git -C /home/lumen/bin status --short --ignored` | `!! gatus`, tree otherwise clean — the trivy shape |
| `.gitignore:16` | `/gatus` — still ignored, so restoring it cannot re-dirty the repo |

**Nothing broke while it was missing.** No caller execs the binary. The
only `gatus` references under `/home/lumen/bin` are `.gitignore:16`,
`gatus-ntfy-relay` (a loopback-only alert shim that never invokes it),
and a comment at `fleet-auth-ssh-probe.sh:3`. The live monitor is the
pinned container image `twinproduction/gatus:v5.10.0`
(`pin_digests.py:37`), configured out of `/home/lumen/kb/gatus/config.yaml`
— a separate runtime path from the on-disk binary. That is precisely why
the deletion was silent: latent loss of a recoverable artifact, not an
outage. Worth noting the failure mode, though — a binary nothing
currently execs is exactly the kind of loss no alarm catches, which is
what makes the "kept on disk" claim's inaccuracy the real defect rather
than its blast radius.

## 2. UPHELD: `agent/lumen_self_healer.py` exists; wq#1112 is not lost

`d96d5b1` and `reports/wq1987_repo_sweep_bin_content_diverged.md` both
claimed `tests/test_healer_stale_target.py` cannot run because its
module "does not exist anywhere in this repo or tree". False on both
counts.

Run as its own docstring documents
(`tests/test_healer_stale_target.py:13`, `Run: python3
tests/test_healer_stale_target.py`), every check passes — ending in
`ALL CHECKS PASSED`, covering `not-found`/`masked`/`loaded` LoadState
classification, per-unit ticket dedup across repeated calls, dedup scope
across distinct units, and the `check_all()` path proving a retired unit
files one ticket and never enters the restart ladder.

Two compounding errors produced the false claim:

- **Bounded search read as global absence.** The test resolves its
  module by relative path *above* the repo —
  `tests/test_healer_stale_target.py:19`,
  `os.path.join(HERE, "..", "..", "agent", "lumen_self_healer.py")` →
  `/home/lumen/agent/lumen_self_healer.py` (27065 bytes, mtime
  2026-08-30, three weeks before wq#1987's attribution work; real
  stale-target dedup and ticket-filing logic, not a stub). The sweep
  searched only `/home/lumen/bin`, its own `--add-dir` grant, and
  reported absence from a search that structurally could not have found
  it.
- **Wrong runner.** It is a standalone script, not a pytest suite, so
  pytest collects zero from it and it looks broken when probed that way.

**Fix applied.** The "known follow-up" note in
`reports/wq1987_repo_sweep_bin_content_diverged.md` is withdrawn and
replaced with the resolved path, and the gatus bullet in the same
report's attribution list is corrected in place.

## ISSUE-LOG rows (staged — `/home/lumen/kb` is a primary tree)

Appending directly was denied: `guard.sh` blocks worker Write/Edit
inside PRIMARY trees (wq#1719/wq#900), and `/home/lumen/kb` is one
(`harness/guard.sh:196`). A worker cannot land a kb change from a
foreman worktree. Both rows are staged verbatim here for whoever holds
the kb lane; next free number is 425.

    | 425 | wq#1987's sweep commit (d96d5b1) said it "untracked the gatus binary, keeping the file on disk" matching the trivy precedent — but /home/lumen/bin/gatus (50446590-byte Go ELF, a real deployed artifact per #66/wq#1279) was gone from disk entirely; find and `git status --ignored` both confirmed absence | "Untrack but keep on disk" is `git rm --cached <f>` (index-only). A plain `git rm`/filesystem delete produces an identical-looking diffstat (50446590 → 0) and an identical commit message, but destroys the working-tree copy. The trivy line used --cached correctly (/home/lumen/bin/trivy is still present), gatus did not — and because the live monitor is the pinned container twinproduction/gatus:v5.10.0 (pin_digests.py:37) out of kb/gatus/config.yaml, nothing execs the on-disk binary, so the loss was silent | Blob was still in history: `git -C /home/lumen/bin cat-file blob 847dc04:gatus > /home/lumen/bin/gatus && chmod 755`. Verified byte-identical (hash-object == rev-parse 847dc04:gatus == e5ed291b…), now `!! gatus` under `status --ignored` with the tree otherwise clean — the trivy shape. Rule: after claiming an index-only untrack, PROVE it with ls/`status --ignored`, never with the diffstat | FIXED |
    | 426 | The same commit + report claimed agent/lumen_self_healer.py "does not exist anywhere in this repo or tree — the implementation half of wq#1112 was lost". False: it is 27065 bytes at /home/lumen/agent/lumen_self_healer.py and its test passes every check | Two compounding search errors. (a) The sweep searched only /home/lumen/bin — its own --add-dir grant — while tests/test_healer_stale_target.py:19 resolves the module via HERE/../../agent/…, one level ABOVE the repo, outside the grant. An absence claim from a bounded search is not evidence of absence. (b) The file is a standalone script (Run: python3 tests/test_healer_stale_target.py, :13), not a pytest suite, so pytest collects 0 and it looks broken when probed that way | Ran it as documented: ALL CHECKS PASSED. Withdrew the "known follow-up" note in reports/wq1987_repo_sweep_bin_content_diverged.md. Rule: before writing "does not exist anywhere", state the roots actually searched, and resolve a test's own import path rather than assuming its repo | FIXED |

## Verdict

wq#2016 CONTRADICT upheld on both counts. gatus restored and verified
byte-identical; the wq#1112 follow-up withdrawn. No further follow-up
open beyond landing the two staged ISSUE-LOG rows.
