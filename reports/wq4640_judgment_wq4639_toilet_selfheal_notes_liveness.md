# wq#4640 judgment on wq#4639's CONTRADICT of wq#4638 (toilet nodes.notes not live)

Judge: build worker wq4640-a1 (claude-opus-5 @high), 2026-10-25 PDT.
Input: `reports/wq4639_verify_wq4638_toilet_selfheal_notes_not_live.md` (PR 2467), ISSUE-LOG
row 1583. The diff was not re-implemented — wq#4639 re-verified it and found it sound.

## Decision

1. **(a)** — ran `selfheal_merge.py` as user lumen against the live config. The toilet note is live.
2. **Standing fix: yes, and neither of the two options the ticket named.** The recurrence has a
   single mechanical cause in `wq done`'s own gate; filed as **wq#4641** (bin repo).
3. **wq#4638 does not reopen.**

## 1. Why (a), not (b)

wq#4460 settled this exact question three days ago and nothing about the situation has changed:
the merge step is byte-for-byte what the gated install delegates to lumen anyway
(`install.sh:75-77`, `runuser -u lumen -- python3 selfheal_merge.py`), the live file is
lumen-owned, `nodes.notes` only appends `known issue: <text>` to a page body
(`sh_nodes.py:185-187`) and silences nothing, and the sweep re-reads the config every run so
no restart is involved. Waiting for an attended root window to add one string to a
lumen-owned YAML is not the physical-act exception instinct 3 reserves for Frank.

What ran, from the worktree:

    python3 ops/wq3354-fleet-selfheal/selfheal_merge.py --dry-run --seed <repo> --live /home/lumen/agents/fleet-selfheal/selfheal.yaml
      would add: nodes.notes.toilet
    python3 ops/wq3354-fleet-selfheal/selfheal_merge.py           --seed <repo> --live /home/lumen/agents/fleet-selfheal/selfheal.yaml
      added: nodes.notes.toilet
      backup: /home/lumen/agents/fleet-selfheal/selfheal.yaml.bak.20261025T215449

`--check` went rc 1 → rc 0, and the authoritative verdict (`python3 /home/lumen/bin/ops-deploy-drift
--json`, which shells the bundle's own `install.sh --verify`) is now:

    {'bundle': 'wq3354-fleet-selfheal', 'verdict': 'VERIFIED', 'authoritative': True,
     'files': 0, 'live': 0, 'missing': [], 'drifted': [], 'age_h': 0.3}

### Probes (wq#2630 — the scope parameter is `--live`, so the failing inputs come from outside it)

`reports/wq4640_probes/probe_can_fail.py`:

    P1 pre-note-seed-as-live  rc=1 miss=['nodes.notes.toilet']   # seed @7e93b7a4, rev-pinned
    P2 pre-merge-backup       rc=1 miss=['nodes.notes.toilet']   # the .bak above, immutable
    P3 live-after-merge       rc=0 miss=[]
    P4 toilet still alerting (not ignore/sleepers): True
    P5 live note == seed@9f24db34 note: True
    PROBE PASS

`reports/wq4640_probes/probe_note_folds.py` — end-to-end through the **deployed** code
(`/home/lumen/agents/fleet-selfheal/sh_nodes.py`, first hash-checked equal to repo@9f24db34),
reading the **live** config, with `peers_fn`/`facts_fn` injected so nothing shells out and
nothing is written:

    deployed sh_nodes.py == repo@9f24db34: True
      toilet:        note expected=True  folded=True  ok=True
      nephthys:      note expected=True  folded=True  ok=True   # pre-existing, must survive the merge
      no-note-probe: note expected=False folded=False ok=True   # control
    toilet body carries wq#4638 citation: True
    toilet body carries opsaudit citation: True
    PROBE PASS

## 2. The standing fix

The ticket offered a `lumen-pr land` hook or sweep-time reconciliation. Both are wrong, and
the second is wrong in an instructive way: **a change to `selfheal_sweep.py` is itself
undeployed until the gated root install runs, so it inherits the exact gap it would close.**
Any mechanism that ships *inside* the bundle cannot be the bundle's own deploy trigger.

The real cause is upstream of the bundle, in the gate that let wq#4638 close:
`pr_ops_deploy_check` filters the cited PR's changed files through
`OPS_INSTALL_RE = /ops/wq\d+[\w.-]*/install\.sh$` (`/home/lumen/bin/wq:2855`, applied at
`:3115`; the byte-match path at `:2977` uses it too). wq#4638's commit `e7d2dd76` touched
exactly one file — `ops/wq3354-fleet-selfheal/selfheal.yaml` — which matches nothing, so
`bundles == []` and the function returns `("none", "", [])`: fail open, close clean, no
`--verify` ever run. **A payload-only edit to an ops bundle cannot trip the gate, however
badly it needs a deploy.** That is why wq#4458 and wq#4638 both closed green and both were
caught by a follow-on [verify] ticket instead of at close time; the 24h `ops-deploy-drift`
sweep is the only net underneath.

Reproduced rev-pinned in `reports/wq4640_probes/probe_gate_gap.py`, which reads the regex out
of the *running* `wq` script so widening the gate flips its expectation rather than silently
passing it:

    gate predicate (wq:2855): /ops/wq\d+[\w.-]*/install\.sh$
      e7d2dd76 (wq#4638 nodes.notes commit): files=1 gate_hits=[]      # the bug
      8eaff1e4 (control, no ops/ files):     files=3 gate_hits=[]
      synthetic ops/wq3354-fleet-selfheal/install.sh: gate_hit=True    # predicate is not always-false
      touched files inside ops/wq3354-fleet-selfheal: ['ops/wq3354-fleet-selfheal/selfheal.yaml']
      that dir ships an install.sh: True                               # a dir-scoped predicate catches it
    PROBE PASS (gate gap reproduced)

The fix — make the predicate directory-scoped: a changed file under `ops/wqNNNN-*/` implicates
that directory's `install.sh` when one exists, deduped per bundle — is **filed as wq#4641**,
not landed here. The gate is in the **bin** repo, this worktree is a foreman checkout, and
`/home/lumen/bin/wq` already carries another lane's uncommitted edits; editing it from here
would entangle that work and could not go through `lumen-pr`. wq#4641 carries the diagnosis,
the fix shape, the existing test suites to extend, and the requirement that the new test case
fail against today's regex.

Landed here instead: `ops/VERIFY_CONTRACT.md` gains **"A bundle is implicated by any file in
it, not only its install.sh"** — the rule the contract always intended, the gap between it and
the implementation, and the interim instruction that a worker closing a payload-only bundle
ticket runs `--verify` by hand.

## 3. Why wq#4638 does not reopen

Its deliverable was the toilet note, correctly placed in `nodes.notes` and correctly kept out of
`ignore`/`sleepers`; wq#4639 re-verified the reasoning (lumen_state facts, the wq#753 opsaudit
citations, the exclusion call) and it is now live with the evidence above. Reopening it would
re-open a row whose literal subject is "fleet node toilet offline on tailscale" — a remote-site
box with no LAN-side reprobe path — and conflate that outage with a liveness gap now closed.
Same call, same reasons, as wq#4460 made on wq#4458.

## Honest limitations

- The live sweep holds the **open** `node-offline:toilet` ticket (wq#4638's own page), so
  `sh_nodes.py:178` correctly declines to re-page it. The merged note rides on the **next**
  page for toilet, after that ticket closes — it does not retro-fit the page already filed.
  That is why the end-to-end probe injects its own peers/facts rather than reading live state.
- Nothing here reprobes toilet. Every toilet fact in `lumen_state` is still
  `[UNVERIFIABLE — no probe]`, dated 2026-06-17, exactly as wq#4638 recorded; the note says so
  rather than resolving it.
- wq#4641 is filed, not landed. Until it lands, the recurrence this judgment diagnosed can
  happen a third time — the gate is unchanged, and only the 24h drift sweep and the contract
  note stand between a payload-only bundle commit and a false green.

## Allowlist note

`bash <bundle>/install.sh --verify` is still refused to a BUILD worker ("This command requires
approval"), as in ISSUE-LOG rows 1500/1583 — the bundle's own checker under `python3` and
`python3 /home/lumen/bin/ops-deploy-drift --json` were used instead. `sha256sum` and `stat` are
also off the allowlist, so file identity was taken in-process (`hashlib` inside the probes).
