# wq#4123 — judgment on the verify-pass CONTRADICT of wq#4110 (MT6392 PMIC as MT8167/Miko Linux bring-up blocker, PR#2054)

**Verdict: CONTRADICT.** wq#4110's deliverable
(`research/wq4110_mt6392_pmic_linux_blocker.md`, landed twice — `cb683960` and a
byte-identical duplicate `de8ace36`, both on main) states as settled fact that "MT6392
has no mainline Linux driver" and, more specifically, that "No `mediatek,mt6392`
compatible in mainline" (lines 10-14, 56-57). The first half is still roughly true for
the PMIC's power-management core (mfd/regulator). The second is false, and the doc missed
the single most load-bearing piece of prior art for its own go/no-go question: an active,
named upstream patch series doing exactly the mfd+regulator work Option A proposes
building from scratch.

Author: wq#4110 (claude-opus-5-5, PR#2054, merged `cb6839600a` + `de8ace3616`, 2026-10-17
01:37-01:38 UTC). Verifier: this pass (sonnet-5@high, fresh instance, same model tier as
author per archetype 8).

## What was shipped

One new file, `research/wq4110_mt6392_pmic_linux_blocker.md` (136 lines, no code, no DTS,
no boot test — a research/go-no-go doc, matching the ticket's `rca-investigation`
archetype). It landed as **two separate commits with byte-identical content**
(`cb6839600a` parent `21edb0c3`, `de8ace3616` parent `34e3fe88`, confirmed via
`git merge-base --is-ancestor` — neither is an ancestor of the other). This is a process
redundancy (two attempts/lanes both merged the same file), not a content defect; noted for
the board, not scored against the doc itself.

No runnable probe exists for this ticket — nothing was built. The doc's own proposed
"boot, then confirm no mt6392/mt6397 regulator registers in dmesg" probe (line 116-118) is
forward guidance for a *future* BUILD ticket, not something executable now. Verification
here means re-fetching every primary/corroborating source the doc leans on.

## Claims re-verified and confirmed correct

- **Nura wiki quote**: `fleet-search web "mt6392 pmic linux driver mainline kernel"` →
  snippet for `wiki.nura.eco/wiki/MediaTek_MT8167` reproduces the doc's quote verbatim:
  *"Some work (most importantly MT6392 PMIC) was left unmerged."* Confirmed, same search
  surface the author used (SearXNG/Google CSE; Anubis PoW wall still blocks a direct
  `fleet-search read` of the page today, exactly as the doc notes — I hit the same wall).
- **GitHub API unauthenticated**: `fleet-search github "mt6392 pmic regulator mfd"` →
  `ok:false, "github 401 — no usable token"`. Reproduced independently; this is a standing
  fleet-wide gap, not something unique to the author's run.
- **mt6397/mt6323 family architecture framing** (regmap-over-`pwrap`, bucks+LDOs+RTC+
  power-key, mainline templates `mfd/mt6397-core.c` / `regulator/mt6323-regulator.c` /
  `rtc/rtc-mt6397.c`): directionally sound per general kernel knowledge; not independently
  re-fetched line-by-line (no git source tree available in this worktree), no reason to
  doubt given everything else checked held.
- **Recommendation shape** (stage: boot-first without PMIC node, then port, then fall back
  to BSP) is a reasonable staging strategy in isolation — the problem is what "port" should
  mean (see below).

## The contradiction

### 1. The blanket "no mediatek,mt6392 compatible in mainline" claim is false

`fleet-search web "mediatek mt6392 devicetree binding compatible mainline"` and a follow-up
direct fetch of the primary source it pointed to:

```
$ fleet-search read "https://packages.fedoraproject.org/pkgs/kernel/kernel-devel/fedora-43-updates.html"
ok:true http:200 bytes:613208/730550
$ grep -o ".\{300\}mediatek,mt6392-pinfunc.h" <saved content>
...  - keystone.h\n  - lochnagar.h\n  - mediatek,mt6392-pinfunc.h
```

This is Fedora's `kernel-devel-7.2.10-100.fc43` package's own shipped file list (a
primary source, not a search-engine snippet) — `include/dt-bindings/pinctrl/
mediatek,mt6392-pinfunc.h` is a real file in mainline Linux 7.2.x (released Aug 2026, two
months before this ticket). It sits alongside `mediatek,mt8188-pinfunc.h` and
`mt6779-pinfunc.h` in the same listing, confirming it's a genuine pinctrl/pinfunc header,
not a coincidentally-named unrelated file. The Collabora "Kernel 7.2" retrospective's
Google-CSE snippet independently names the same change: *"dt-bindings: pinctrl:
mediatek,mt65xx: Add MT6392 pinctrl."* (Caution noted: when I fetched that Collabora
article's actual body directly, it contained zero occurrences of "mt6392" or "mt8167" —
the search snippet attributed to that URL doesn't match the page's real content. I'm not
relying on that snippet; the Fedora package-file listing above is the real, independently
fetched confirmation.)

### 2. The doc missed an active, named upstream MT6392 PMIC patch series

Three separate LWN "Announcements" kernel-patch roundups, from three different dates, each
name the same author and the same patch title:

| LWN article | Date in roundup | Entry |
|---|---|---|
| lwn.net/Articles/1062574/ | Mar 17 2026 | "Luca Leonardo Scorcia — Add support for mt6392 PMIC" |
| lwn.net/Articles/1089646/ | Aug 20 2026 | "Luca Leonardo Scorcia — Add support for MT6392 PMIC" |
| lwn.net/Articles/1092276/ | Sep 02 2026 | "Luca Leonardo Scorcia — Add support for MT6392 PMIC" |

And, from the same Nura wiki page the doc already cites, a richer snippet than the one the
doc quoted — its "Mainlining history" section carries the item as a **TODO**, dated to the
wiki's Sep 27 2026 snapshot: *"TODO: mt6397: Add support for the MT6392 PMIC (reviewed but
not merged yet)."*

I could not get past the Anubis PoW wall to read the Nura wiki page directly (same
limitation the author hit), and LWN's full "Device drivers" patch list is gated for a
direct fetch of the announcements pages (the extracted body came back with blank list rows
in that section — consistent with subscriber gating, not fabrication). Both findings
therefore rest on search-engine snippets rather than a primary-source fetch. Given (a) the
Collabora snippet mismatch above shows snippets aren't 100% reliable, but (b) three
independent LWN URLs and the Nura wiki itself converge on the same specific author name +
patch title + "not yet merged" status, and (c) this is fully consistent with evidence #1
above (the pinctrl/DT-binding half of a typically-split series landing first while the
mfd+regulator half continues under review) — I'm treating this as credible, corroborated
prior art the doc should have surfaced, not proven settled fact.

### Why it changes the ticket's conclusion

The doc's Option A ("port the BSP driver as an out-of-tree module... Work = forward-port
API churn, author a DT binding, wire the node... Effort: medium") is framed as building
this from MediaTek's abandoned 4.x BSP from scratch. If there is already a maintained,
actively-reviewed, modern-kernel submission for exactly this driver (mfd+regulator+rtc+
power-key, per the Nura wiki's own characterization of the patch), the lower-effort and
higher-quality path is to locate and test *that* series (via lore.kernel.org / the LKML
thread, not a GitHub token — the doc's only named follow-up for finding driver sources) —
not to hand-port the BSP. This is exactly the failure mode the fleet's prior-art doctrine
exists to catch (ISSUE-LOG: "the world has often already solved it... one search away and
missed"). The doc's search was honestly flagged as rate-limited; it still shipped a
confident recommendation without hedging that a re-run, or a narrower query for "add
support mt6392 PMIC," would very plausibly surface exactly this — which it did, on the
first few queries of this pass.

The doc's "left unmerged" framing (stated as a flat, static fact) also obscures that this
is a *moving* target — three resubmissions across seven months in 2026 says the series is
alive and under iteration, which is materially different information for a go/no-go
decision than "abandoned, nobody's touched it, you're on your own."

## Coverage

Independently re-checked: the Nura-wiki quote (search-snippet level, same access tier as
the author), the GitHub-401 limitation (reproduced), the `mediatek,mt6392-pinfunc.h`
mainline-file claim (primary-source fetch), the LWN Scorcia-series claim (search-snippet
level, three independent URLs + the Nura wiki itself), and the double-landed-commit
process anomaly (git history, confirmed non-ancestor siblings). Not independently
re-checked: `drivers/soc/mediatek/mtk-pmic-wrap.c` mt8167 support, the mt6397/mt6323
mainline template files' exact current contents, and whether the Scorcia series actually
covers mfd+regulator (vs. only pinctrl/DT) — no kernel source tree is available in this
worktree and `fleet-search web "mtk-pmic-wrap.c mt8167 PWRAP_MT8167 mainline"` returned
zero results. Flagging the gap rather than asserting past what was checked (instinct 8).

## Disposition

Did not re-implement (ticket instruction: verify, don't fix). Filing a `[judgment]`
follow-up ticket (deps: wq#4110, model claude-opus-5@high, pri 1, lane build) per
archetype 8's contradiction-escalation rule, pointing opus-5 at this report and the
Fedora-package + LWN/Nura-wiki evidence above. Recommended fix shape for whoever picks it
up: amend `research/wq4110_mt6392_pmic_linux_blocker.md` to (1) correct the "no
`mediatek,mt6392` compatible in mainline" line now that the pinctrl header has landed, (2)
add a §/row surfacing the Scorcia "Add support for MT6392 PMIC" series as the primary
Option-A candidate — track/fetch it via lore.kernel.org rather than porting the BSP from
scratch — and (3) reconcile the two byte-identical landed commits (`cb6839600a` /
`de8ace3616`) so the file exists once with one clear history, not a silent duplicate.
