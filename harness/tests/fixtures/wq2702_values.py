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
