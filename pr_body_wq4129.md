# wq#4129 — amend wq#4110's MT6392 PMIC doc after wq#4123's CONTRADICT

## What

Amends `research/wq4110_mt6392_pmic_linux_blocker.md`:

- **Pins the in-flight upstream series** instead of inferring it:
  `[PATCH v10 0/9] Add support for MT6392 PMIC`, Luca Leonardo Scorcia, linux-mediatek,
  2026-09-02, msgid `20260902193351.185771-1-l.scorcia@gmail.com`. Nine patches —
  dt-bindings (mfd / input / regulator), an `MFD_CELL_*` refactor,
  `mfd: mt6397: Add support for MT6392 PMIC` (+614, Reviewed-by AngeloGioacchino Del Regno),
  `regulator: Add MediaTek MT6392 regulator` (+816, same Reviewed-by), pinctrl, pmic-keys,
  `mt6392.dtsi` — with a per-patch table and msgid scheme in the doc.
- **Records review state from the thread:** Lee Jones asked "Still waiting on Acks by the
  looks of things?" on 2026-09-17 and Scorcia confirmed. Parked on subsystem Acks, not
  rejected and not abandoned; no v11 and no merge notice through 2026-10-16.
- **Rewrites Option A** to apply and test that series on the fleet's kernel/DTS and reply
  `Tested-by:` upstream. The from-scratch BSP hand-port becomes **Option A2**, kept only for
  the case where v10 will not apply; Option D (author + upstream from scratch) is marked
  obsolete.
- **Corrects the mainline claim in both directions:**
  `include/dt-bindings/pinctrl/mediatek,mt6392-pinfunc.h` does ship in Linux 7.2 and is *not*
  from this series, but the `mediatek,mt6392` mfd/regulator **compatibles** remain unmerged
  (patches 1-3). wq#4110 was over-broad rather than flatly false.
- **Notes that Miko sits inside the series' tested population:** Xiaomi Mi Smart Clock X04G,
  Lenovo Smart Clock 2, PlayStation Classic Mini, and patch 9/9 touches the
  **pumpkin-common** DTS include — the same DTS family the doc derives Miko's from.
- **Reconciles the duplicate commits** in a Provenance section, and rewrites the follow-up
  ticket list (the GitHub-token request is withdrawn; lore-over-git needs no credential).

Also annotates `reports/wq4123_judgment_wq4110_missed_mt6392_upstream_series.md` with two
corrections to that report:

- "landed twice, both on `main`" is wrong. Only `de8ace3616` is an ancestor of `origin/main`;
  `cb6839600a` lives only on the stale wq#4110 lane branch. `main` has one add-commit and the
  tree one copy, so history was never doubled. Deleting the leftover branch is named as a
  cleanup candidate and deliberately left undone — no content lives only there.
- Its explicit open question, whether the series covers mfd+regulator or only pinctrl/DT, is
  answered: both.

## Why

wq#4123's verify-pass was correct on substance, and its finding changes the doc's
recommendation. Option A as originally written proposed hand-porting MediaTek's abandoned 4.x
BSP driver; a reviewed, tenth-revision upstream submission of exactly that driver already
exists, so the original plan would have reproduced — worse — work that is sitting on a mailing
list waiting for Acks. The doc is a go/no-go input for Miko Linux bring-up, so leaving the
stale recommendation in place would have mispriced the blocker.

## How

The series could not be found over HTTP: `lore.kernel.org`, `patchwork.kernel.org` and
`elixir.bootlin.com` all return the Anubis proof-of-work page to `fleet-search read`, which is
why wq#4110's rate-limited, HTTP-only prior-art pass concluded nothing upstream existed. The
public-inbox **git** transport is not walled:

```
git clone --shallow-since=2026-02-01 https://lore.kernel.org/linux-mediatek/0 <dir>   # 72 MB, 11430 msgs
git -C <dir> log --format='%h|%ad|%s' --date=short -i --grep=6392
git -C <dir> show <sha>:m                                                            # raw mail + patch
```

Logged as ISSUE-LOG #1386 as the standing technique for upstream-status questions, with
#1388 for a trap hit along the way: `fleet-search read` of a raw `.yaml` returns `ok:false`
with empty content but a nonzero byte count, so counting a term in it produces a convincing
fake "0 occurrences" — nearly banked as proof of absence. ISSUE-LOG row 1384 is flipped to
RESOLVED with a back-reference.

## Testing

Docs-only change; no code paths touched. Evidence is primary-source re-fetches plus git
checks, all re-runnable:

| Claim | Check | Result |
|---|---|---|
| v10 series exists, 9 patches, 2026-09-02 | `git -C <lore-clone> log -i --grep=6392` | cover letter + patches 1/9-9/9 listed |
| mfd + regulator coverage and Reviewed-by | `git show e93c7c42:m`, `git show 372af696:m` | +614 / +816, both Reviewed-by AngeloGioacchino Del Regno |
| review state 2026-09-17 | `git show a4e92f18:m`, `git show 9f7d5a82:m` | Lee Jones "waiting on Acks", author confirms |
| archive currency (no v11 / no merge) | `git -C <lore-clone> log -1 --date=iso` | latest message 2026-10-16 21:36 UTC |
| `mediatek,mt6392-pinfunc.h` in mainline 7.2 | `fleet-search read` Fedora kernel-devel listing | `ok:true http:200`, one `mt6392` hit in the `include/dt-bindings/pinctrl/` block, package `7.2.10-100.fc43` |
| header is not from this series | `git show bc0238e0:m` diffstat | touches only `pinctrl-mt6397.c` + `pinctrl-mtk-mt6392.h` |
| duplicate-commit reconciliation | `git merge-base --is-ancestor cb6839600a origin/main` → no; `… de8ace3616 origin/main` → yes; `git log --full-history --diff-filter=A -- <path>` → one commit; `git ls-files \| grep -c wq4110_mt6392` → 1 | history not doubled |

The last row is the falsifiable one: it was run expecting wq#4123's "both on main" to hold,
and it did not — the check changed the conclusion rather than confirming it.

## Risk / rollback

Low. Documentation and an ISSUE-LOG status flip only; nothing executable changes. Rollback is
`git revert` of this commit. The amendment's one perishable element is the v10 pin, so the doc
carries a monthly re-grep of the lore mirror as a follow-up — a v11 or a merge changes the
plan, and the pinned msgid makes the delta explicit.
