# wq#3080 (github sha256:22c1d24bcd03): the one value `mask_replay.py 3078` reported
# BURNED?, pinned as committed text. It is not a credential; nothing was rotated.
#
# wq#3078 (verify-pass of wq#3065) probed masking._PROSE_BODY from a `python3 -c` Bash
# command whose `tests` list held the literal `ghp_` + `"a" * 36` next to ordinary words
# ('placeholders', 'X-GNOME-Autostart-enabled', 'abc-123-def', ...). The value sits in
# that tool_use input (transcript 58d44ee1-ee60-477d-a6bf-78742535431b, four copies) and
# has one distinct character, zero entropy. It was named by hash in-process from the
# corpus: sha256("a" * 36)[:12] == 22c1d24bcd03. wq#3080 printed only its letter and
# length, never the value.
#
# Leak path: a worker typing a whole credential-shaped dummy into a command, which the
# worker prompt already forbids (wq#2795: split it at the prefix seam or build it at
# runtime). Nothing to rotate — no Lumen credential is behind this string.
#
# (sha, label, prefix, prefix + value). Split every six characters so reading this file
# fires no rule; mask_replay closes same-line seams before it searches.
WQ3078_VALUES = (
    ("22c1d24bcd03", "github", "ghp_",
     "ghp_aa" "aaaaaa" "aaaaaa" "aaaaaa" "aaaaaa" "aaaaaa" "aaaa"),
)

WQ3078_RECORDS = frozenset(v[0] for v in WQ3078_VALUES)
