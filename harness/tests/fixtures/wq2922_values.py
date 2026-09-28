# wq#2952: the two values mask_replay.py 2922 reported BURNED?, pinned as committed text.
# Neither is a credential; each was named by hash, in-process, from the shape that made it.
#  - `_ALARM` behind the bare cloudflare prefix: an English word in a comment wq#2922 was
#    writing into ops/wq2316-mask-secrets-boundary/mask-secrets (the pre-image its gate
#    snapshot froze), describing the rule re-triggering on its own output.
#  - `_QQQQQ` behind the same prefix: the Q-filled dummy wq#2922's offset probe built at
#    runtime, printed back on the `cf/off5:` line of that probe's output.
# The aws and slack values the tripwire filed wq#2952 on already account to
# harness/tests/test_masking.py; they are not repeated here.
#
# wq#2982: a third value, from the same run, that wq#2972 orphaned rather than wq#2952
# missed. wq#2972 (PR 1272, e79bc64e) split reports/wq2941_probe_self_name_punctuation_
# bypass.py's `REAL_SECRET` literal at the name-equals-quote seam — a correct fix for
# that probe's own-tripwire problem — but wq#2922's run corpus holds a SNAPSHOT of the
# pre-split file, three quoted segments glued after the name-equals-quote seam. The
# snapshot's first (11-char) segment is what `account()` can no longer find in the
# post-split tree, which instead opens that seam onto an empty string before the full
# run glued as one segment. Same class as wq2913's row 924 (wq2955's fix): a pin scored
# against the live tree is invalidated by the next unrelated tree edit. Pinned here as
# committed text, same as the two above — not split into a second "retired" set,
# because CURRENT_PATTERNS still fires on the snapshot line unchanged; only
# `account()`'s tree lookup went stale, not the rule.
# (sha, label, prefix, prefix + value). Split at seams so reading this file pages nothing;
# mask_replay.account() closes the joined string.
# Pinned by test_mask_replay.py::test_wq2922_pins_are_their_hashes_and_read_silently.
WQ2922_VALUES = (
    ("d356ea83d848", "cloudflare", "cfut", "cf" "ut_ALARM"),
    ("807399bef03a", "cloudflare", "cfut", "cf" "ut_QQQQQ"),
    ("53a1492afd3b", "kv-secret", 'REAL_SECRET = "',
     'REAL_SECRET = "' 'Zx8qLp3' 'vWn7'),
    # wq#3048: an _orphan_matches() row the replay could not see until it read the
    # backstop (which postdates this run, so it never fired): the Q-filled ghp_ dummy
    # the probe printed as `v = ...`, joined across the mask literal, so no file holds
    # it whole.
    ("ef5cdbc0ba92", "github", "ghp_",
     "ghp_QQ" "QQQQQQ" "QQQQQQ" "QQQQQQ" "QQQQQQ" "QQQQQQ" "QQQQQQ"),
)

# The records the replay reproduces from wq#2922's corpus under today's table.
# test_wq2922_corpus_burned_nothing asserts containment, not the count.
WQ2922_RECORDS = frozenset(v[0] for v in WQ2922_VALUES)
