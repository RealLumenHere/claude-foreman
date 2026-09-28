# wq#3001 judgment: wq#2999 contradicted wq#2991 (ssh generic target set)

## Ruling
The contradiction is upheld on both points. Fixed in claude-foreman PR #1368 (864f5410b8).

## (a) bare `a` and the other indefinite words
`_SSH_GENERIC_TARGETS` held every ticket containing `ssh a`, `ssh an`, `ssh any` or `ssh some`. Those are the commonest words after a verb in retrospective prose, so "we tried ssh a few times before it worked" was reported `unresolved` and parked pre-spawn like a real capability gap.

Decision: gate, do not drop. `the/each/every/that/this` stay unconditional (no idiomatic prose reading). `a/an/any/some` count as an authored instruction only when the next word is a machine noun (`ssh a node`, `ssh any host`). Dropping them outright would reopen `ssh a node` as a silent drop.

## (b) `ssh to/into/onto the node`
In scope. It is the same silent-drop class as wq#2914 with a different filler word, and the ticket asked for a class fix. `to/into/onto` now count when a determiner and a machine noun follow. `ssh to bes` (a named node, the wq#879 prose row) is unchanged.

## Evidence
- 12 new SSH_ROWS in `harness/tests/test_capability_lint.py`; 8 of them fail against the pre-fix lint (checked in a clean archive of the pre-fix tree), so the probe can fail.
- `harness/tests/test_capability_lint.py`: 154 passed. Capability suites plus `dispatcher/tests/test_capability_replay.py`: 364 passed, 1 xfailed (already xfail).
- Re-run on main after landing: `ssh a few times` and `ssh any time` give no finding; `ssh the node`, `ssh each node`, `ssh a node`, `ssh into the node` are held; `ssh to bes` stays prose.

## Not done
The full harness suite has pre-existing failures unrelated to this change, e.g. `test_agents_spec.py::test_real_agents_yaml_loads_and_lints_clean` (agents.yaml primary_tuple drift against models.yaml). It fails identically on the pre-fix tree.
