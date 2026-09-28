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
    # wq#3048: the run's _orphan_matches() rows, which the replay could not score until
    # it read the backstop too. Each is `before + after` joined across the mask literal
    # of a `<prefix><filler>MASKED<prefix>...` chain: the 14:22 probe list above, and the
    # 14:29 overfire report (reports/wq3037_verify_wq3018_orphan_backstop_overfire.md)
    # whose filler runs `jjjj`, `hh`, `kkkk`, `mmmm`, `9999` meet a bare second prefix.
# 0bc654921d14 is 86dbbec3c5a6's chain read whole; it accounted only as a substring of
# that pin, so it is pinned on its own.
    # 72458136d6c6 is the aws one wq#3048 was filed on. Synthetic, nothing rotated.
    ("0bc654921d14", "sk-key", "sk-ant-",
     "sk-ant" "-sk-an" "t-anot" "her-ex" "ample-" "value-" "that-i" "s-long" "-enoug" "h-for-" "floor"),
    ("870b480040bc", "github", "github_pat_",
     "github" "_pat_a" "bcgith" "ub_pat" "_docum" "entati" "on_exa" "mple_h" "ere"),
    ("1960aa36bab6", "github", "github_pat_",
     "github" "_pat_j" "jjjjjj" "jjjgit" "hub_pa" "t_"),
    ("09afaafa7c0d", "cloudflare", "cfut_",
     "cfut_c" "fut_xx" "xx"),
    ("44da8897fcc4", "cloudflare", "cfut_",
     "cfut_h" "hcfut_"),
    ("484d412fddca", "sk-key", "sk-ant-",
     "sk-ant" "-kkkkk" "kkkkks" "k-ant-"),
    ("00761f53a2b4", "slack", "xoxb-",
     "xoxb-x" "oxb-an" "other-" "exampl" "e-valu" "e-long" "-enoug" "h"),
    ("e207a241c11d", "slack", "xoxb-",
     "xoxb-m" "mmmxox" "b-"),
    ("72458136d6c6", "aws", "AKIA",
     "AKIA99" "999999" "99999A" "KIA"),
)

WQ3037_RECORDS = frozenset(v[0] for v in WQ3037_VALUES)
