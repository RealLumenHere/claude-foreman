# wq#2875: the one value mask_replay.py 2875 reported BURNED?, pinned as committed text.
# It is not a credential. wq#2875 (this rotation row's own worker) dumped wq#2848's
# mask-hits records as `label len sha repr(prefix) suppressed`; the last row printed a
# `'TOKEN = '` prefix followed by its suppression reason, and kv-secret read that
# reason as the value. It is the `+`-joined reason masking.suppressions() writes, not a
# secret. Named by hash in-process; mask_replay.account() closes it.
# Pinned by test_mask_replay.py::test_wq2875_pins_are_their_hashes_and_read_silently.
WQ2875_VALUES = (
    ("35bba7abb843", "kv-secret", "pointer+call"),
)
