# wq#3029: the two values mask_replay.py 3021 reported BURNED?, pinned as committed text.
# Neither is a credential. wq#3021 (verify-pass of wq#2924's sk-key own-prefix fix) ran
# /tmp/fworker-wq3021/probe_sk7.py, which built its two cases at runtime — `sk-ant-`
# glued to `AKIA` + `"1" * 16` and to `ghp_` + `"a" * 30` — and printed `repr(case)`
# before the masker's output, so each whole dummy reached the transcript. Both were
# named by hash, in-process, from that one tool result; neither was typed or printed
# by wq#3029. The run's 15 other sk-key hits account to
# reports/wq3021_verify_wq2924_sk_key_own_prefix_gap.md and are not repeated here.
# No leak path to close: a mask probe has to print what it feeds the masker.
#
# wq#3076: twelve more sk-key rows from the SAME probe_sk7.py repr(case) dump — the
# sk-/sk-proj-/sk-ant- shape sweep (casing, `None`/`or`/`api03`/`org`/`letters`
# suffixes, and one doubled-prefix edge case) that the old line-wide `today_closed`
# used to excuse wholesale via the aws/github hits already pinned above, on the same
# JSONL line. Scoring only the hit's own span (mask_replay.py `today_closed`) surfaces
# each one; same probe, same no-leak-path reasoning, so they are pinned here too. The
# last row (42492da06234) is a second, later repr(case) dump in the same transcript;
# its `a`-run body is also a coincidental substring of the github pin's `a`-run above,
# which the self-consistency check (`_assert_corpus_pinned`'s fixture-popped pass)
# catches and is why it gets its own row instead of free-riding on that coincidence.
# (sha, label, prefix, prefix + value). Split every six characters so reading this
# file fires no rule; mask_replay closes same-line seams before it searches.
WQ3021_VALUES = (
    ("3a54fc0cbc0b", "github", "ghp_",
     "ghp_aa" "aaaaaa" "aaaaaa" "aaaaaa" "aaaaaa" "aaaa"),
    ("807ae5f38db4", "aws", "AKIA",
     "AKIA11" "111111" "111111" "11"),
    ("09133bbde442", "sk-key", "sk-proj-",
     'sk-pro' 'j-Ab-9' '999999' '999999' '999999' '9'),
    ("1e7572e78084", "sk-key", "sk-ant-",
     'sk-ant' '-aaaaa' 'aaaaaa' 'aaaaaa' 'aaask-' 'ant-'),
    ("5993c3a58c9d", "sk-key", "sk-",
     'sk-org' '-99999' '999999' '999999' '999'),
    ("5f42ecbfd29c", "sk-key", "sk-",
     'sk-Ant' '-99999' '999999' '999999' '999'),
    ("66b7a7d4c7af", "sk-key", "sk-",
     'sk-ANT' '-99999' '999999' '999999' '999'),
    ("6baad88490d4", "sk-key", "sk-ant-",
     'sk-ant' '-None-' '999999' '999999' '999999' '99'),
    ("6f27a37d274f", "sk-key", "sk-ant-",
     'sk-ant' '-or-99' '999999' '999999' '999999'),
    ("a23935b45d5f", "sk-key", "sk-ant-",
     'sk-ant' '-api03' '-99999' '999999' '999999' '999'),
    ("bea881b175ac", "sk-key", "sk-",
     'sk-let' 'ters-'),
    ("c21ea9e8bfcb", "sk-key", "sk-ant-",
     'sk-ant' '-proj-' '999999' '999999' '999999' '99'),
    ("efbe42620ff9", "sk-key", "sk-ant-",
     'sk-ant' '-bbbbb' 'bbbbbb' 'bbbbbb' 'bbb'),
    ("42492da06234", "sk-key", "sk-ant-",
     'sk-ant' '-aaaaa' 'aaaaaa' 'aaaaaa' 'aaa'),
)

WQ3021_RECORDS = frozenset(v[0] for v in WQ3021_VALUES)
