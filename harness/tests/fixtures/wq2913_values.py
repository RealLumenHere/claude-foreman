# wq#2936: the values mask_replay.py 2913 reported BURNED?, pinned as committed text.
# None is a credential; each was named by hash, in-process, from the shape that made it.
#  - three are pytest ids of test_mask_foreign_prefix_matrix.py (`sk-key-letter-` +
#    the slack prefix + `-off{1,3,5}-masking`), printed on the FAILED lines of wq#2913's
#    own test runs. These three are the ones the tripwire filed wq#2936 on.
#  - three are what wq#2913's probe_sk.py printed from masking.mask_text: `sk-` + `z`*off
#    + the slack prefix + the mask literal, off in (1, 3, 5).
#  - one is `<body>` behind the github prefix, a placeholder in the docstring wq#2913 was
#    writing for masking._redundant (`remote https://x:<that>@host`).
# (sha, label, prefix, prefix + value). Split at seams so reading this file pages nothing;
# mask_replay.account() closes the joined string.
#
# wq#2955: the file is in TWO sets, because a hash pins a value AS SOME RULE CUT IT, and
# the rules move. Which set a tuple sits in is a fact about today's table, not about the
# value; the values themselves are committed text either way, and the no-rotation verdict
# wq#2936 reached does not depend on the split. WQ2913_ROW_924 below is the part that
# never moves.

# What today's table still cuts out of wq#2913's corpus. This is the set
# test_wq2913_corpus_burned_nothing scores under CURRENT_PATTERNS, so a rule change can
# and does move tuples in and out of it.
WQ2913_VALUES = (
    # wq#2955: 960404a ("mask the union of every rule's spans") re-cut the three pytest
    # ids. The line is unchanged; the slack rule now takes the span sk-key used to, so
    # the same text yields a different prefix, a different body and a different hash.
    ("567fd51d9d8a", "slack", "xoxb-", "xo" "xb--off1-masking"),
    ("376086870b7f", "slack", "xoxb-", "xo" "xb--off3-masking"),
    ("f36b7dd79665", "slack", "xoxb-", "xo" "xb--off5-masking"),
    # wq#2924 re-cut row 924's eighth value (retired below): with `key-` no longer a
    # qualifier, sk-key keeps `sk-` and its value runs up to where slack's match starts.
    ("80ddc9fffb71", "sk-key", "sk-", "s" "k-key-letter-"),
)

# What wq#2913's live tripwire cut and today's table will not cut again. These belong in
# no set keyed on CURRENT_PATTERNS: the replay cannot reproduce them from any tree, so
# asking it to is how wq#2936 shipped red. Three rule changes retired them, the first two
# landing after wq#2913 ran and before or just after wq#2936 pinned them:
#  - 960404a (wq#2960, "mask the union of every rule's spans") re-cut the six sk-key
#    values. The three pytest ids now read as slack (above); the three probe_sk outputs
#    end in the mask literal and are dropped outright.
#  - 05e65ce (wq#2931, "skip url-auth bodies holding a placeholder",
#    harness/masking.py:524) taught url-auth to refuse a body holding a `<name>` template
#    placeholder — 57 seconds before wq#2936's own commits landed.
#  - wq#2924 cut sk-'s qualifiers down to the ones providers issue, retiring the eighth
#    value: a shorter truncation-cut of the off1 id where sk-key read `key-` as a
#    qualifier (`sk-key-`) and its 16-char body stopped at `--off`. wq#2936 counted it
#    and never wrote a tuple for it; it was accounted only by substring overlap with the
#    off1 tuple, which is why nobody missed it.
# Pinned by test_wq2913_retired_pins_are_not_cut_by_todays_table, which asserts the
# refusal rather than the value: revert or narrow either rule and it fails, naming the
# tuple to move back up.
WQ2913_RULE_RETIRED = (
    ("ba8aad54215e", "sk-key", "sk-key-letter-", "s" "k-key-letter-xo" "xb--off1-masking"),
    ("cb01c565429c", "sk-key", "sk-key-letter-", "s" "k-key-letter-xo" "xb--off3-masking"),
    ("810f9fde938b", "sk-key", "sk-key-letter-", "s" "k-key-letter-xo" "xb--off5-masking"),
    ("72b0b008eebb", "sk-key", "sk-", "s" "k-zxo" "xb-MASKED"),
    ("b4ef69356f53", "sk-key", "sk-", "s" "k-zzzxo" "xb-MASKED"),
    ("f3a0c6e016c9", "sk-key", "sk-", "s" "k-zzzzzxo" "xb-MASKED"),
    ("9fbe965f997e", "url-auth", "https://x:", "https://x:" "gh" "p_<body>"),
    ("41964b438456", "sk-key", "sk-key-", "s" "k-key-letter-xo" "xb--off"),
)

# The eight values ISSUE-LOG row 924 accounts for wq#2913, and the one thing here no rule
# change can move: it is what the run's live tripwire reported, a closed historical fact.
# Every one of them must still be pinned by one of the two sets above — that, not a
# hardcoded `len(...) == 8`, is what stops a value quietly leaving the file when a rule
# stops cutting it. wq#2936 wrote the count instead and shipped red against its own seven;
# wq#2962 then took the count down to seven, which made the test green by agreeing with
# the omission.
WQ2913_ROW_924 = frozenset((
    "ba8aad54215e", "cb01c565429c", "810f9fde938b", "41964b438456",
    "72b0b008eebb", "b4ef69356f53", "f3a0c6e016c9", "9fbe965f997e",
))

# The records the replay reproduces from wq#2913's corpus under today's table.
# test_wq2913_corpus_burned_nothing asserts containment, not the count.
WQ2913_RECORDS = frozenset(v[0] for v in WQ2913_VALUES)
