# wq#2874 — verify-pass: wq#2850 shared kernel/os/version scoping fix (PR#1188)

**Verdict: CONTRADICT** — PR#1188 only treats a self-identifying value as
"shared" (and therefore subject-scoped) when it is simultaneously OPEN on
**two or more** other subjects. wq#2850's own motivating evidence (wq#2812)
describes the false positive firing with **one** other node still on the
dead value ("any node still legitimately on -30 ... now false-positives" —
singular). Reproduced live against the shipped module: the exact wq#2812
values (sobek.kernel closed `7.0.0-30-generic`, reopened `7.0.0-31-generic`,
one other subject `nephthys` open on `7.0.0-30-generic`) still get flagged
as `scoped: False` and still fire a false positive on a doc that names
neither `sobek` nor any kernel predicate cue.

PR#1188, merged to main as `77c6cbefc6`. Shipped under
`ops/wq1609-minerva-contradiction-scan/`.

## What the diff does

`build_dead_value_index()` (`lumen_contradiction_scan.py:317-381`) now
builds `open_subjects_by_value`: for every currently-open fact, record which
subjects hold each exact value. `shared_values` is the subset of values held
open by `>=2` subjects (`lumen_contradiction_scan.py:336`). An index entry's
`scoped` flag becomes `not self_identifying(val) or val in shared_values`
(`:371`) instead of just `not self_identifying(val)`. `scan_text()` and
`corpus_candidates()` both gate their subject/predicate-cue requirement on
`entry["scoped"]` (`:430`, `:598`) — a scoped entry only flags when the text
also names the closed subject and a predicate cue; an unscoped entry flags
on value match alone, no subject check at all. This is the exact mechanism
the ticket's item (1) specifies: "derive the set from the state store (same
value open on >=2 subjects => shared)."

## Verification performed (adversarial, cold read + probe)

1. Read the diff cold (`git show 77c6cbefc6`), not the PR's own commit
   message framing.
2. Ran the shipped suite as-is: `pytest test_lumen_contradiction_scan.py` →
   `57 passed`. Confirmed by code reading that the diff's own new test,
   `test_value_open_on_two_subjects_is_subject_scoped_despite_looking_self_identifying`,
   only exercises the `N=2`-sharer case (`nephthys` **and** `thoth` both open
   on `7.0.0-30-generic`). No shipped test exercises `N=1` — the boundary
   the threshold itself introduces, and the boundary the real incident sits
   on.
3. **Reproduced the actual wq#2812 incident** against the patched module,
   not a synthetic 2-node scenario:
   ```python
   add_fact(db, "k1", "sobek", "kernel", "7.0.0-30-generic", "2026-09-26", "config")
   add_fact(db, "k2", "sobek", "kernel", "7.0.0-31-generic", None, "config")
   add_fact(db, "k3", "nephthys", "kernel", "7.0.0-30-generic", None, "config")
   ```
   (values match wq#2812's ticket text exactly: closed `-30`, current `-31`;
   only `nephthys` — no third node — shares the dead value, matching the
   ticket's own "any node still legitimately on -30" framing.)

   Result: `entry["scoped"] is False` for `7.0.0-30-generic`. Calling
   `scan_text()` with a line shaped like the real `nephthys.yaml:22`
   (`"kernel: 7.0.0-30-generic\n"`, subject_context naming `nephthys`, never
   `sobek`) still produces a hit:
   ```
   sobek kernel 7.0.0-30-generic -> kernel: 7.0.0-30-generic
   ```
   Re-run twice, deterministic both times. Probe script:
   `/tmp/fworker-wq2874/probe_wq2874.py` (not committed — scratch probe,
   fixture values transcribed from wq#2812's ticket text).
4. Checked the `N=2` case behaves as the shipped test claims (it does —
   `scoped: True`, `scan_text` correctly withholds the hit) to confirm the
   contradiction is specifically about the threshold, not a wholesale
   failure of the fix.
5. Confirmed the regression side still holds: a genuinely unique
   self-identifying value (0 sharers, e.g. `thoth.tailscale_ip`) stays
   unscoped and still flags — the fix does not over-suppress.

## Why this matters (not a corner case)

- The `>=2` threshold is not a conservative buffer around the real bug, it
  sits **above** the real bug's minimum reproduction. The window during
  which this class of false positive actually fires — right after one node
  closes a kernel/os/version value while the rest of the fleet is still
  mid-rollout on the old build — is precisely the window where only *some*
  fraction of nodes have caught up. A staggered two-node rollout (the most
  common real shape: one machine bumped, one not yet) reproduces the
  original wq#2812 symptom through the "fixed" code path unchanged.
- The code's own comment (`lumen_contradiction_scan.py:329-335`) argues from
  the general principle — "a kernel build, OS version, or BIOS/model string
  is fleet vocabulary, not an identifier" — but the implementation only acts
  on that principle when the state store happens to currently have 2+ nodes
  open on the exact same string. The root cause the ticket names (kernel
  strings are inherently non-unique by construction) doesn't depend on how
  many machines the DB currently shows sharing it; the heuristic proxies for
  that root cause and the proxy has a gap at its own boundary.
- The shipped regression test cannot have caught this: it hard-codes `N=2`
  sharers, so it is structurally unable to fail on the `N=1` case wq#2812
  actually was.

## What a correct fix looks like (not implemented here per this archetype's instruction)

Either lower the state-store threshold to `>=1` other subject (closest to
the ticket's own "any node still legitimately on -30" framing — but note
this reduces to "always scope kernel/os/version-shaped values," which is
closer to option (1)'s predicate-aware-list approach the ticket also
offered as an alternative), or combine both: predicate-aware scoping for the
named classes (`kernel`, `os`, `*_version`, `bios`, model strings) regardless
of current sharer count, with the state-store `>=2` check reserved for
values under predicates not in that list. The ticket offered both paths;
the shipped fix took only the state-store path and set its threshold above
the real incident's minimum.

## Scope note

Same repo/path as wq#2850: fix lives in
`ops/wq1609-minerva-contradiction-scan/`. Not evaluated: live corpus
before/after hit counts (the ticket's item (4)) — PR#1188's notes don't cite
a re-run corpus scan either, only the unit suite; that gap is upstream of
this verify-pass and not re-litigated here.
