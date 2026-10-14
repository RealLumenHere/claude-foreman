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
    # wq#3753 follow-up (mask_replay 3753: 1 unaccounted). The wq#1482 smoke-test command
    # (`echo "GITHUB_TOKEN=ghp_..." > wq1482_smoke/root1/leak.env`) reached the model
    # already masked, as the masker's own elided placeholder `ghp_MASK...` + `MASKEDLEN6`.
    # The kv rule read the 11-char placeholder as a value. It is the masker's output,
    # not a credential, and it was never any live token; nothing to rotate.
    ("14a7e03ec8dd", "kv-secret", "ghp" "_MA" "SK." ".."),
)

# mask_replay.account() only excuses a body under TRUNCATED_FLOOR (12) when the rule's kept
# prefix sits directly in front of it, so the placeholder is also committed WITH its
# `GITHUB_TOKEN=` label, as it appeared in the smoke-test command. Split like the values.
WQ3753_PLACEHOLDER_CONTEXT = "GIT" "HUB" "_TO" "KEN" "=ghp" "_MA" "SK." ".."

WQ3753_RECORDS = frozenset(v[0] for v in WQ3753_VALUES)
