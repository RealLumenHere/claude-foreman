# wq#3029: the two values mask_replay.py 3021 reported BURNED?, pinned as committed text.
# Neither is a credential. wq#3021 (verify-pass of wq#2924's sk-key own-prefix fix) ran
# /tmp/fworker-wq3021/probe_sk7.py, which built its two cases at runtime — `sk-ant-`
# glued to `AKIA` + `"1" * 16` and to `ghp_` + `"a" * 30` — and printed `repr(case)`
# before the masker's output, so each whole dummy reached the transcript. Both were
# named by hash, in-process, from that one tool result; neither was typed or printed
# by wq#3029. The run's 15 other sk-key hits account to
# reports/wq3021_verify_wq2924_sk_key_own_prefix_gap.md and are not repeated here.
# No leak path to close: a mask probe has to print what it feeds the masker.
# (sha, label, prefix, prefix + value). Split every six characters so reading this
# file fires no rule; mask_replay closes same-line seams before it searches.
WQ3021_VALUES = (
    ("3a54fc0cbc0b", "github", "ghp_",
     "ghp_aa" "aaaaaa" "aaaaaa" "aaaaaa" "aaaaaa" "aaaa"),
    ("807ae5f38db4", "aws", "AKIA",
     "AKIA11" "111111" "111111" "11"),
)

WQ3021_RECORDS = frozenset(v[0] for v in WQ3021_VALUES)
