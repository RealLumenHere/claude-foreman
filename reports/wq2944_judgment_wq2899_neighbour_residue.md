# wq#2944 judgment: wq#2926 contradicted wq#2899 (sk-key own-tail absorb)

## Ruling
The contradiction was right about the code it read and is superseded on main. No masking
change is landed. What lands is a test pinning the invariants that do hold.

## Why the original fix (af9ce4c0) is not landed
wq#2926 read the sequential path, where `sk-cfut_MASKED` reached sk-key's own-output screen
and the owning rule's hit was dropped. wq#2913 replaced that with a union pass, and
`_redundant()` now drops an sk-key container over a neighbour's whole credential on purpose:
one burn, named by the inner rule. Porting the per-rule screen onto that pass (masking.py
`_matches`, `_own_output`, `_redundant`, plus the daemon mirror) does make sk-key alarm, and it
breaks `test_mask_replay.py::test_wq3021_corpus_burned_nothing`: two extra burns
(`aadcebbf9c9c`, `a3edfcc8de6e`) on the two composite dummies wq#3021 fed the masker. That test
is the repo's standing decision that a container over a foreign credential files no second
rotation, so the port is the double-burn regression, not a fix. The port is kept for reference
at /home/lumen/scratch/wq2944_per_rule_screen.patch.

## What holds on main today
The 24 rule/neighbour/gap shapes reachable under the union pass, each run through
masking.py, the token-engine daemon and the sed mirror (72 runs; the 8 in the last bullet count shapes, not runs):
- live characters of the neighbour's body in the output: 0
- runs where nothing was masked: 0
- runs where the neighbour's label did not alarm: 0
- shapes where the owning rule label is absent: 8 of 24, all sk-key over a cloudflare, github or slack token (by design, above)

The kept fixed prefix of the neighbour can stand in front of the mask (`sk-cfut_MASKED`,
`xoxb-cfut_MASKED`). It is the table's declared non-secret part, so this is cosmetic. The
rotation is filed against the neighbour's value, which is the only live one.

## Delivered
`harness/tests/test_mask_neighbour_residue.py` (107 tests): no live character survives in any
of the three implementations, something is masked, the neighbour alarms, a rule's own output
stays silent, and url-auth/kv-secret still read a foreign prefix as their own output.
It is a regression pin, not a fix: it passes on main as it stands.

## Pre-existing red, not from this change
`test_the_mask_suite_does_not_arm_its_own_tripwire`: committed reports under reports/ (wq3021,
wq3062, wq3067, wq3070) carry whole credential shapes. Rewrite them to describe shapes.
