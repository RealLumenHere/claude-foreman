# wq#2706: the one value mask_replay.py 2702 reported BURNED?, pinned as committed text.
# It is not a credential. wq#2702 (verify-pass of wq#2697's mask-secrets glue fix) typed
# it into its own Write of /tmp/fworker-wq2702/triple_glue_input.txt: the tracked github
# fixture (ops/wq2316-mask-secrets-boundary) glued to `AKIA` + 16 x `Q` and `xoxb`, followed
# by `-12345678901`, so a triple-glued probe line. Named by hash from its shape, never read
# off the artefact. The row's own aws value, e69bbe113521, is the `Q` x 16 body of that
# run's `AKIA$(printf 'Q%.0s' {1..16})` probe; today's rules already read it as rule-fixed.
# Split at a seam so reading this file pages nothing; mask_replay.account() closes it.
# Pinned by test_mask_replay.py::test_wq2702_corpus_burned_nothing.
WQ2702_VALUES = (
    ("6d4c27f66a96", "github",
     "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8AKI" "AQQQQQQQQQQQQQQQQxoxb"),
)

# wq#2765: the 21 records the replay reproduces from wq#2702's faithful artefacts (a
# 22nd came only from a `read-stale` ISSUE-LOG window, i.e. today's text, and moves).
# test_wq2702_corpus_burned_nothing asserts containment, not the count.
WQ2702_RECORDS = frozenset((
    "1fd882463874", "20227a136ec3", "20227a136ec3", "254aa248acb4", "2c9e04df04aa", "34e1b54e5c8c",
    "38de98f38239", "3c1c495ff227", "4634d1ff2af5", "4f24ad8b3156", "6d4c27f66a96", "6fbcdb8df881",
    "76b4100ba253", "999329de6185", "a32aebae4867", "b8789db0c2da", "cde0fb0dec14", "de20960ffcae",
    "e69bbe113521", "eb522505a0e3", "f50f2d7b79dd",
))
