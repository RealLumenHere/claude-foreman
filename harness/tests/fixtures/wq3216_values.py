# wq#3218 (sk-key sha256:6a91579efe20): the one value `mask_replay.py 3216` reported
# BURNED?, pinned as committed text. It is not a credential; nothing was rotated.
#
# wq#3216 verified wq#3211's Langfuse 4002 fix (langfuse_client.ingest). Its end-to-end
# probe was a `python3 -c` that monkeypatched urlopen to raise a 4002 and called
# `lc.ingest('http://langfuse.internal', 'pk-lf-8a4fdeadbeef', <secret>, ...)` with a
# placeholder secret: an `sk-lf-` head on the words "real secret value" and `1234`.
# Its only occurrence in the run's transcript is that assistant tool_use input (one
# JSONL line); no tool_result, read or artefact carries it, so the model typed it. The
# committed test_langfuse_client.py passes "not-a-real-secret-value" with no `sk-` head,
# which is why that file never paged. Named by hash in-process from the run's corpus.
# Leak path: a probe typing a whole `sk-`-headed literal instead of building it at
# runtime. Nothing to rotate.
#
# (sha, label, value). The value is what follows the kept `sk-` prefix. Split into short
# literals so reading this file fires no rule; mask_replay closes same-line seams before
# it searches, so the value stays on one line.
WQ3216_VALUES = (
    ("6a91579efe20", "sk-key", "lf-" "rea" "lse" "cre" "tva" "lue" "123" "4"),
)

WQ3216_RECORDS = frozenset(v[0] for v in WQ3216_VALUES)
