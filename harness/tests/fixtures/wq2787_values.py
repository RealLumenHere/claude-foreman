# wq#2839: the one value mask_replay.py 2787 reported BURNED?, pinned as committed text.
# It is not a credential. wq#2787 (a verify-pass of wq#2782) typed it into its own
# `python3 -c` probe of masking.scan as `CF_API_TOKEN=` + a hex alphabet run; its first
# occurrence in the transcript is that assistant tool_use input (and its retry), with no
# earlier tool_result carrying it. The body is `abcdef` + `0123456789` twice, then `abcd`:
# 36 chars, 16 distinct, a counting run, not a minted Cloudflare token. Named by hash from
# its shape, in-process. Split at a seam so reading this file pages nothing;
# mask_replay.account() closes it.
# Pinned by test_mask_replay.py::test_wq2787_corpus_burned_nothing.
WQ2787_VALUES = (
    ("30f8df099069", "kv-secret", "abcdef0123456789" "abcdef0123456789" "abcd"),
)

# The records the replay reproduces from wq#2787's corpus under today's table.
# test_wq2787_corpus_burned_nothing asserts containment, not the count.
WQ2787_RECORDS = frozenset((
    "30f8df099069",
))
