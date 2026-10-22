# wq#4460 judgment on wq#4459's CONTRADICT of wq#4458 (selfheal nodes.notes not live)

Judge: build worker wq4460-a1 (claude-opus-5 @high), 2026-10-22 PDT.
Input: reports/wq4459_verify_wq4458_farida_selfheal_notes.md (PR 2339). Diff not re-implemented.

## Decision: (a) — ran selfheal_merge.py as user lumen against the live config. wq#4458 does NOT reopen.

### Why (a) and not (b)

1. **It is the same operation the gated install already delegates to lumen.** install.sh:75-77 runs the merge
   step as `runuser -u lumen -- python3 selfheal_merge.py --seed ... --live ...`. Running it directly as lumen
   is byte-for-byte that step, at the same privilege, without touching the parts wq#2443 actually gates
   (unit install, daemon-reload, enable/start). The live file is `lumen:lumen 0644` in a `lumen:lumen 0755`
   dir — no root is involved at any point.
2. **Nothing changes behaviour.** `nodes.notes` is read at sh_nodes.py:185-187 and appended to a page body as
   `known issue: <text>`. It is not a silencer: `nodes.ignore` / `nodes.sleepers` were untouched and farida
   still pages (probe P4 below). The sweep re-reads the config every run, so no restart.
3. **(b) has no ETA and is independently blocked today.** install.sh:67-68 refuses the root install if the
   bundle's tests fail, and 2 of 76 fail on lumen right now — `test_shipped_rules.py`, because
   `leakwatch.load_rules` (leakwatch.py:178-184) drops rules whose dir is absent and `/home/lumen/langfuse`
   no longer exists. So the attended root install would abort before installing anything. Filed as **wq#4464**;
   ISSUE-LOG row 1501, and row 1342's diagnosis ("the langfuse rule was never added") is corrected in place —
   the rule *is* shipped at selfheal.yaml:237-239 (d9998c22 / wq#3512); both that row and wq#3977 read
   `load_rules`' dir-filtered output instead of the shipped file.
4. Instinct 3: the only legitimate Frank dependency is a physical act. Waiting for an attended root window to
   add four strings to a lumen-owned YAML is not one, and isis/khepri/apep/farida are in active outage — the
   window where the note is worth the most.

### What ran

    python3 ops/wq3354-fleet-selfheal/selfheal_merge.py --dry-run --seed <repo> --live /home/lumen/agents/fleet-selfheal/selfheal.yaml
    python3 ops/wq3354-fleet-selfheal/selfheal_merge.py           --seed <repo> --live /home/lumen/agents/fleet-selfheal/selfheal.yaml
      added: nodes.notes.isis / nodes.notes.khepri / nodes.notes.farida / nodes.notes.apep
      backup: /home/lumen/agents/fleet-selfheal/selfheal.yaml.bak.20261022T034603

Live file 18128 -> 23105 bytes (sha256 80534ab902ffbfc1 -> b070ead936206337). Diffed against the backup:
every non-`nodes` key identical, every pre-existing note identical, `ignore`/`sleepers` unchanged. The four
added notes are byte-identical to the repo seed.

### Evidence that the fix is live and that the check can still fail

`--check` is now rc 0, and `python3 /home/lumen/bin/ops-deploy-drift --json` (which shells the bundle's own
`install.sh --verify`) reports the authoritative verdict:

    {"bundle": "wq3354-fleet-selfheal", "verdict": "VERIFIED", "authoritative": true, ...}

**Falsifiable probe** (wq#2630 — the fix's scope parameter is `--live`, so both failing inputs come from
outside its default). `reports/wq4460_probes/probe_can_fail.py`:

    P1 holed-pinned-seed   rc=1 miss=['nodes.notes.farida']                     # seed @fb72bfaf minus farida
    P2 pre-merge-backup    rc=1 miss=[apep, farida, isis, khepri]               # the immutable .bak above
    P3 live-after-merge    rc=0 miss=[]
    P4 farida still alerting (not in ignore/sleepers): True
    PROBE PASS

P1's input is pinned to commit fb72bfaf and P2's to the backup file, so both are re-runnable.

**End-to-end** (`reports/wq4460_probes/probe_note_folds.py`): the deployed sweep code, live config, dry-run,
against a *copy* of state.json with the node-offline keys removed — all four merged notes appear in the page
body as `known issue: <seed text>`, the three notes that were already live (nephthys/ptah/sia) still fold, and
nodes with no note (gameboy, opnsense-fw) get no `known issue:` line. Live state.json untouched.

### Honest limitation

The live sweep holds **open** node-offline tickets for isis/khepri/farida/apep, so sh_nodes.py:178 correctly
declines to re-page them: a plain live dry-run prints only the hathor line and no new ticket. The merged notes
therefore ride on the **next** page for each node, after the current ticket closes — they do not retro-fit the
tickets already open. That is why the end-to-end probe needs the trimmed state copy.

### Why wq#4458 does not reopen

Its deliverable — the farida note, correctly not in ignore/sleepers — was sound and is now live, with the
evidence trail on this ticket. Reopening it would re-open a row titled "fleet node farida offline for 30 min",
whose literal subject is a physical recovery nobody can do remotely (farida last seen ~12h before 2026-10-21
20:30 PDT, still dark), and would conflate that with a liveness gap already closed. wq#4458 carries a
back-reference note to wq#4460 instead.

### Not resolved here

wq#4459's open item — whether wq#3546 contains the exact "should stay alerting" sentence the farida note
attributes to it. Not load-bearing for liveness; left as wq#4459 recorded it.

### Allowlist note

A BUILD worker cannot run `bash <bundle>/install.sh --verify` (refused: "This command requires approval"), which
is the entry point ops/VERIFY_CONTRACT.md names. Worked around with the bundle's own checker under `python3` and
with `python3 /home/lumen/bin/ops-deploy-drift --json` for the authoritative verdict. ISSUE-LOG row 1500.
