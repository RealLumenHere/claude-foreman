# wq#2952: the two values mask_replay.py 2922 reported BURNED?, pinned as committed text.
# Neither is a credential; each was named by hash, in-process, from the shape that made it.
#  - `_ALARM` behind the bare cloudflare prefix: an English word in a comment wq#2922 was
#    writing into ops/wq2316-mask-secrets-boundary/mask-secrets (the pre-image its gate
#    snapshot froze), describing the rule re-triggering on its own output.
#  - `_QQQQQ` behind the same prefix: the Q-filled dummy wq#2922's offset probe built at
#    runtime, printed back on the `cf/off5:` line of that probe's output.
# The aws and slack values the tripwire filed wq#2952 on already account to
# harness/tests/test_masking.py; they are not repeated here.
# (sha, label, prefix, prefix + value). Split at seams so reading this file pages nothing;
# mask_replay.account() closes the joined string.
# Pinned by test_mask_replay.py::test_wq2922_pins_are_their_hashes_and_read_silently.
WQ2922_VALUES = (
    ("d356ea83d848", "cloudflare", "cfut", "cf" "ut_ALARM"),
    ("807399bef03a", "cloudflare", "cfut", "cf" "ut_QQQQQ"),
)

# The records the replay reproduces from wq#2922's corpus under today's table.
# test_wq2922_corpus_burned_nothing asserts containment, not the count.
WQ2922_RECORDS = frozenset(v[0] for v in WQ2922_VALUES)
