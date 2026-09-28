# wq#2936: the seven values mask_replay.py 2913 reported BURNED?, pinned as committed text.
# None is a credential; each was named by hash, in-process, from the shape that made it.
#  - three are pytest ids of test_mask_foreign_prefix_matrix.py (`sk-key-letter-` +
#    the slack prefix + `-off{1,3,5}-masking`), printed on the FAILED lines of wq#2913's
#    own test runs. sk-key reads `key-letter-` as its prefix group and the rest as a body.
#    These three are the ones the tripwire filed wq#2936 on.
#  - three are what wq#2913's probe_sk.py printed from masking.mask_text: `sk-` + `z`*off
#    + the slack prefix + the mask literal, off in (1, 3, 5).
#  - one is `<body>` behind the github prefix, a placeholder in the docstring wq#2913 was
#    writing for masking._redundant (`remote https://x:<that>@host`).
# (sha, label, prefix, prefix + value). Split at seams so reading this file pages nothing;
# mask_replay.account() closes the joined string.
# Pinned by test_mask_replay.py::test_wq2913_pins_are_their_hashes_and_read_silently.
WQ2913_VALUES = (
    ("ba8aad54215e", "sk-key", "sk-key-letter-", "s" "k-key-letter-xo" "xb--off1-masking"),
    ("cb01c565429c", "sk-key", "sk-key-letter-", "s" "k-key-letter-xo" "xb--off3-masking"),
    ("810f9fde938b", "sk-key", "sk-key-letter-", "s" "k-key-letter-xo" "xb--off5-masking"),
    ("72b0b008eebb", "sk-key", "sk-", "s" "k-zxo" "xb-MASKED"),
    ("b4ef69356f53", "sk-key", "sk-", "s" "k-zzzxo" "xb-MASKED"),
    ("f3a0c6e016c9", "sk-key", "sk-", "s" "k-zzzzzxo" "xb-MASKED"),
    ("9fbe965f997e", "url-auth", "https://x:", "https://x:" "gh" "p_<body>"),
)

# The records the replay reproduces from wq#2913's corpus under today's table.
# test_wq2913_corpus_burned_nothing asserts containment, not the count.
WQ2913_RECORDS = frozenset(v[0] for v in WQ2913_VALUES)
