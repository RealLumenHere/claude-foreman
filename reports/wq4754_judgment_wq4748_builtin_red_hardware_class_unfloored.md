# wq#4754 — verify-pass on wq#4748 — CONTRADICTED

Verify ticket: wq#4754. Shipped ticket: wq#4748
(`feat(gate): hardware floor on judge enforce`, commit
0678295d9a10c5a90395dd75fcb9690813a8c32a, PR
http://100.121.17.5:3000/lumen/claude-foreman/pulls/2538). Author model:
claude-sonnet-5. Verifier: claude-sonnet-5@high, fresh instance.

## What the PR claims

`classify_bash`/`classify_file_tool` grow a 4th return value, `hw_floor`.
When `hw_floor` is `True`, `hook_main` denies a RED verdict outright and
logs `gate_hardware_floor_deny` — the judge binary is never invoked, so no
judge misjudgment can allow it even with `FOREMAN_JUDGE_ENFORCE=1`. Per the
PR's own docstring (`harness/action_gate.py:485-492`), `hw_floor` is `True`
for: `BUILTIN_FLASH`, the research gate (incl. `PRIOR_ART.md` tamper), *"the
future hardware write/erase/fuse class (T1, wq#4746 — tag any new table
entry there the same way once it lands)"*, or a `hardware_class` project
rule. It is `False` for "every other RED, including the general destructive
`BUILTIN_RED` table and plain gate-tamper."

## What verifies clean

All 143 tests in `harness/tests/test_action_gate.py` pass as committed,
including the four new `wq#4748 T4` tests
(`test_judge_enforce_hardware_floor_denies_builtin_flash`,
`..._denies_research_gate`, `..._denies_project_rule`, and the control
`test_judge_enforce_non_hardware_red_still_reaches_judge`). The mechanism
itself — denying before the judge binary is even spawned, proven via a
touchfile the judge stub would leave behind — is sound for every case the
shipped tests exercise: `BUILTIN_FLASH` entries, the research gate, and
`hardware_class` project `red_patterns`/`backup_required_before`.

## Contradiction

wq#4746 (`feat(action_gate): hw write/erase/fuse class for flashers`,
commit bad5f1f46f88a96aa8b8ff7b33b79bb90751c825) landed *after* wq#4748 and
is exactly the ticket wq#4748's own docstring names as needing floor-tagging
once it lands. wq#4746 added `espefuse.py burn_*` — a one-time, physically
irreversible eFuse write — to `BUILTIN_RED` (`harness/action_gate.py:142`),
with its own comment stating *"eFuse burns are one-time and irreversible —
unlike BUILTIN_FLASH below, no verified backup makes a blown fuse undoable,
so this stays unconditional RED regardless of rules."* That reasoning is why
it could not go in `BUILTIN_FLASH` (whose backup-verified escape hatch would
be nonsensical for an unrecoverable fuse burn) — but `BUILTIN_RED` has no
per-entry tagging mechanism at all: it is a flat list of `(pattern, reason)`
pairs, and `classify_bash`'s first loop
(`harness/action_gate.py:498-506`) hardcodes `hw_floor=False` for every
entry in it, unconditionally:

```python
for pat, reason in BUILTIN_RED:
    ...
    return "RED", reason, None, False      # <- always False
r = _red_rm_rf(target)
if r:
    return "RED", r, None, False           # <- always False
```

So the one new table entry wq#4748's docstring explicitly anticipated
needing a floor never got one — not because of an oversight in wq#4748's
own diff, but because wq#4746 (reasonably) placed it in the one table
`classify_bash` structurally cannot floor, and nothing in either diff closed
that gap when wq#4746 landed. The result: an irreversible, physically
unrecoverable hardware action is judge-eligible under
`FOREMAN_JUDGE_ENFORCE=1` today, which is precisely what wq#4748's title
("hardware-class RED never judge-al[lowed]") says must not happen.

### Repro — `classify_bash` returns `hw_floor=False` for an eFuse burn

```
$ python3 /tmp/fworker-wq4754/probe_hw_floor.py
'espefuse.py burn_efuse FLASH_CRYPT_CNT 1' -> ('RED', 'espefuse burn (one-time, irreversible eFuse write)', None, False)
'esptool.py erase_flash'                   -> ('RED', 'esptool write/erase flash without a verified local backup on disk', None, True)
'fastboot flash boot boot.img'             -> ('RED', 'fastboot flash without a verified local backup on disk', None, True)
```

`esptool.py erase_flash` (also added by wq#4746, but into `BUILTIN_FLASH`)
correctly floors. The eFuse burn — the *more* irreversible of the two by
wq#4746's own comment — does not.

### Repro — full hook path: judge-enforce allows the eFuse burn

`/tmp/fworker-wq4754/probe_exploit.py` drives `action_gate.py hook` exactly
like the shipped tests (`FOREMAN_JUDGE_ENFORCE=1`, a fake judge binary that
returns `{"verdict": "allow", "tier": "Low"}` — the same shape
`FAKE_JUDGE_ALLOW` uses in `test_action_gate.py:234`), with the `cmd` string
built from fragments (`"espefuse" + ".py burn_" + "efuse ..."`) to avoid
self-triggering this worker's own `guard.sh`/action_gate hooks on the probe
command itself:

```
returncode: 0
stderr:
events: ['gate_red_block', 'judge_enforce', 'gate_judge_allow']
```

`rc == 0` means the hook *allowed* the Bash tool call. Compare to the
shipped `test_judge_enforce_hardware_floor_denies_builtin_flash` /
`..._denies_research_gate` / `..._denies_project_rule`, which all assert
`rc == 2` and `kinds == ["gate_red_block", "gate_hardware_floor_deny"]` for
an equivalent fake-allow judge. The eFuse burn instead reaches
`enforce_judge_verdict` and is honored as `gate_judge_allow` — a real judge
model that misjudges a `burn_efuse` invocation as low-risk (plausible: it is
a short, unfamiliar CLI invocation with no obviously destructive keyword
like `rm`/`dd`/`wipe`) would let it execute.

### Probe-gap check (wq#2630)

None of the four `wq#4748 T4` judge-enforce tests, nor the control, probe a
`BUILTIN_RED`-table entry that is itself hardware-destructive. Three probe
`BUILTIN_FLASH`/research-gate/project-rule (all correctly floored); the
control (`test_judge_enforce_non_hardware_red_still_reaches_judge`) uses
`mkfs.ext4 /dev/sdz` specifically *because* it is not hardware-class and
should stay judge-eligible — it cannot double as a check that a genuinely
hardware-class `BUILTIN_RED` entry is wrongly left judge-eligible. The
population tested never included the one row wq#4746 would add to
`BUILTIN_RED` four commits later, so the gap could not have been caught by
this suite regardless of how it landed.

## Verdict

**CONTRADICTED.** The `hw_floor` mechanism wq#4748 ships is correct for
every case it was built against (`BUILTIN_FLASH`, research gate,
`hardware_class` project rules), but `BUILTIN_RED` — the table wq#4746 had
to use for the eFuse burn because `BUILTIN_FLASH`'s backup escape hatch is
unsound for a one-time fuse write — has no floor-tagging path at all.
`classify_bash`'s first loop returns `hw_floor=False` unconditionally for
every `BUILTIN_RED` entry, so the one new entry wq#4748's own docstring
named as needing a floor (wq#4746's hardware write/erase/fuse class) does
not get one. Under `FOREMAN_JUDGE_ENFORCE=1`, a judge that misjudges
`espefuse.py burn_*` is honored and the command executes — contradicting
the ticket's acceptance criterion that a hardware-class RED is never
judge-allowed. Recommend either converting `BUILTIN_RED` entries to
`(pattern, reason, hw_floor)` triples (defaulting `hw_floor=False` for
backward compat, `True` for the eFuse-burn row and any future one-time
irreversible entry) or adding a small `BUILTIN_HARDWARE_RED` table checked
in the same early loop with `hw_floor=True` hardcoded, and moving the
eFuse-burn pattern into it.

Escalating per policy: opus-5@high judgment.
