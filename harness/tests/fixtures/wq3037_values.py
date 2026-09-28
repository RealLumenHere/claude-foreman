# wq#3039: the three values mask_replay.py 3037 reported BURNED?, pinned as committed text.
# Neither is a credential. wq#3037 (verify-pass of wq#3018's orphan-prefix backstop)
# typed a `python3 -c` probe list feeding masking._orphan_matches() doubled-prefix
# chains — `github_pat_abc` glued to `github_pat_` + an English phrase, and `sk-ant-`
# glued to `sk-ant-` + another — and the command text itself reached the transcript.
# A later probe in the same run (comma+space separator after a masked `sk-ant-`) added
# a third, an uppercase phrase. All bodies are words, not keys. Named by hash, in-process, from
# the replay's verdict seam; neither was printed by wq#3039. The run's other two hits
# are settled already (slack rule-fixed, aws tracked in test_mask_at_rest.py).
# Leak path: a mask probe has to show the masker what it feeds it; the worker-prompt
# rule (split literals at the prefix seam) is the control, nothing to rotate.
# (sha, label, prefix, prefix + value). Split every six characters so reading this
# file fires no rule; mask_replay closes same-line seams before it searches.
WQ3037_VALUES = (
    ("86dbbec3c5a6", "sk-key", "sk-ant-",
     "sk-ant" "-anoth" "er-exa" "mple-v" "alue-t" "hat-is" "-long-" "enough" "-for-f" "loor"),
    ("a972ef9c0161", "github", "github_pat_",
     "github" "_pat_d" "ocumen" "tation" "_examp" "le_her" "e"),
    ("3cfc0d4a2cf9", "sk-key", "sk-ant-",
     "sk-ant" "-ANOTH" "EREXAM" "PLEVAL" "UE"),
)

WQ3037_RECORDS = frozenset(v[0] for v in WQ3037_VALUES)
