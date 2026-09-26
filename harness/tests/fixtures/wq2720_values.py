# wq#2795: the one value mask_replay.py 2720 reported BURNED?, pinned as committed text.
# It is not a credential. wq#2720 attempt 2 typed it into its own `python3 -c` probe
# ("kv-secret under-masking when the value starts with a foreign prefix") as
# `TOKEN: ` + an `AKIA` + lowercase + `1234` line; its first occurrence in the transcript
# is that assistant tool_use input, with no earlier tool_result carrying it. An AWS key ID
# is AKIA + 16 of [0-9A-Z], so a lowercase body cannot be one — the aws rule never fired,
# only kv-secret on the `TOKEN:` label. Named by hash from its shape, in-process.
# Split at a seam so reading this file pages nothing; mask_replay.account() closes it.
# Pinned by test_mask_replay.py::test_wq2720_corpus_burned_nothing.
WQ2720_VALUES = (
    ("40e9cc706e1d", "kv-secret", "AKI" "Asomelowercasejunk1234"),
)

# The 5 records the replay reproduces from wq#2720's corpus under today's table.
# test_wq2720_corpus_burned_nothing asserts containment, not the count.
WQ2720_RECORDS = frozenset((
    "18f6278e7af4", "254aa248acb4", "40e9cc706e1d", "76b4100ba253", "e69bbe113521",
))
