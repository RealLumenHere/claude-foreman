"""wq#3036: pinned corpus for test_burn_filter.py's tree-wide anti-vacuity sweep.

wq#2288/wq#2297 tightened kv-secret's guards (_KV_NOT_CALLED, _KV_NOT_IDENT) until every
credential-shaped identifier the sweep's original corpus relied on (`key =
apply_key_allowlist(reg)` and kin, wq#2314) stopped matching, so
test_no_tracked_module_pages_when_a_worker_reads_it's tree-wide sweep of harness/*.py +
dispatcher/*.py started reading 0 matches across all 784 tracked modules -- its own
anti-vacuity guard (wq#2630) correctly refused to pass a corpus of zero rather than pass
vacuously (kb/ISSUE-LOG.md row 990).

This module gives that sweep one committed, benign match to screen: a plain identifier
assignment below, never called, never a live credential anywhere. Under a real runner it
is accounted for by burn_filter.is_committed_literal finding the line in this same tracked
file, the same way the historical `apply_key_allowlist` case was; under FakeGrep (nothing
declared committed) it is unaccounted and pages, which is what proves the sweep still
screens something real. If a future masking change stops this line from matching too,
replace the identifier below with another of the same shape -- the point is that the
sweep needs something real to screen, not this exact string.

Pinned for test_masking.py's TRIPWIRE_CORPUS_EXCUSED (test_the_mask_suite_does_not_arm_its_own_tripwire):
value_sha256_12 bf43137fb255, shape (kv-secret, "pending_rotation_secret = ", 29).
"""

queued_epoch_signing_material = "not-a-real-value-see-module-docstring"
pending_rotation_secret = queued_epoch_signing_material
