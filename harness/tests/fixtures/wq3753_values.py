# wq#3765 (kv-secret sha256:d1f6f18ff8b9): the one value `mask_replay.py 3753` reported
# BURNED?, pinned as committed text. It is not a credential; nothing was rotated.
#
# wq#3753 Read /home/lumen/agents/work-queue/boards/LPP_QUEUE_PROGRESS.md, whose line
# 1010 is review prose: "...ONE OF THEM IS NOT A TOKEN: <path> contains the literal
# `ghp_xxxxxxxx...`, a DOCUMENTATION PLACEHOLDER in vendored third-party source". The
# kv rule read `TOKEN: ` + that path as label + value. The path is a vendored Sim Studio
# source file with its middle elided as `/.../`. The board is not in any git repo, so
# the replay had no tree to account it against. Named by hash in-process from the live
# board line; the value was never printed whole.
# Leak path: the board phrasing itself, now `NOT A TOKEN -- <path>`, which reads no label.
# Nothing to rotate.
#
# (sha, label, value). The value is what follows the kept `TOKEN: ` prefix. Split into
# short literals so reading this file fires no rule; mask_replay closes same-line seams
# before it searches, so the value stays on one line.
WQ3753_VALUES = (
    ("d1f6f18ff8b9", "kv-secret", "sim" "-st" "udi" "o-s" "rc/" "..." "/gi" "thu" "b.t" "s"),
)

WQ3753_RECORDS = frozenset(v[0] for v in WQ3753_VALUES)
