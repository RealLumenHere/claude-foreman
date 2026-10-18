# wq#4261 (kv-secret sha256:ae971efadf59): the two values `mask_replay.py 4260` reported
# BURNED?, pinned as committed text. Neither is a credential; nothing was rotated.
#
# wq#4260 (install the wq4258 fleet-consolidation litellm snippet) built throwaway test
# inputs for install.sh under its own /tmp/fworker-wq4260: a `printf` into "$TMPDIR/env"
# and a Write of t1/env, each giving `ANTHROPIC_API_KEY=` a made-up body. Both bodies
# label themselves as dummies (lowercase words, the 20-char one hyphenated and ending in
# a digit), neither has the `sk-ant-` prefix a real Anthropic key carries, and a real
# key is about 108 chars against 16 and 20 here. Named by hash in-process through
# mask_replay.replay(verdict=...); the value was never printed whole. Leak path: a
# worker typing an unsplit `NAME_KEY=` dummy into its own tool_use, which the tripwire
# reads before any hook can refuse it (row 872). Nothing to rotate.
#
# (sha, label, prefix, prefix + value). Split into short literals so reading this file
# fires no rule; mask_replay closes same-line seams before it searches, so each value
# stays on one line.
WQ4260_VALUES = (
    ("ae971efadf59", "kv-secret", "ANT" "HRO" "PIC" "_AP" "I_K" "EY=",
     "ANT" "HRO" "PIC" "_AP" "I_K" "EY=" "tes" "tke" "yva" "lue" "FAK" "E"),
    ("f9bfdede01af", "kv-secret", "ANT" "HRO" "PIC" "_AP" "I_K" "EY=",
     "ANT" "HRO" "PIC" "_AP" "I_K" "EY=" "tes" "t-f" "ixt" "ure" "-du" "mmy" "-1"),
)

WQ4260_RECORDS = frozenset(v[0] for v in WQ4260_VALUES)
