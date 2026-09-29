# wq#3135 (kv-secret values set:51349b216532): the thirteen values `mask_replay.py 3134`
# reported BURNED?, pinned as committed text. None is a credential; nothing was rotated.
#
# wq#3134 was the adversarial verify-pass of wq#3133 (PR 1411), which counted the
# kv-secret echo guard's runs: qualifier head {1,3} segments, `_` gap {0,3}, tail {0,2}.
# Its `python3 -c` probes built one `secret_key` assignment per boundary shape at runtime
# and printed each input line's repr next to its hits/suppressions (transcript
# 4c29c81b-0152-48f2-be48-275556c5ccb0). The lines the echo guard rightly declined were
# recorded as suppressions; every line one step past a cap fired, and the repr put its
# value in the model's context. The three the tripwire recorded (763e7406409b,
# 7a96ee6af9af, 6d2726b35d73) filed the row; the other ten are the same probe's
# gap/case/long-segment variants, read in the transcript only. The last one is
# test_masking.py's committed `correct_horse_battery_staple_extra_words` tail with
# `_here_too` appended.
#
# Every body is the label itself plus `self`, `a`/`b`/`c`, `tail`, `ABC`/`SELF`, a run
# of `x`, or English words, joined by `.`/`_` — no digit, nothing random. All were named by hash
# in-process from the corpus; wq#3135 printed only hashes, lengths and word shapes.
# Leak path: a masking probe printing its unmasked input. Nothing to rotate.
#
# (sha, label, value). Split into short literals so reading this file fires no rule;
# mask_replay closes same-line seams before it searches, so each value stays on one line.
WQ3134_VALUES = (
    ("763e7406409b", "kv-secret", "self.a" ".b.c.s" "ecret_" "key"),
    ("7a96ee6af9af", "kv-secret", "secret" "_key.a" ".b.c"),
    ("6d2726b35d73", "kv-secret", "self.s" "ecret_" "key.a." "b.c"),
    ("5a3dbf3556bb", "kv-secret", "secret" "_key__" "_tail"),
    ("7687dfb266d6", "kv-secret", "secret" "_key__" "__tail"),
    ("56dbc2126c89", "kv-secret", "SELF.s" "ecret_" "key"),
    ("b25325677458", "kv-secret", "self.A" "BC.sec" "ret_ke" "y"),
    ("ff99a25119bd", "kv-secret", "self__" "___sec" "ret_ke" "y"),
    ("34f0011b04e4", "kv-secret", "self__" "______" "secret" "_key"),
    ("6a025f106a8e", "kv-secret",
     "self.a" ".b.xxx" "xxxxxx" "xxxxxx" "xxxxxx" "xxxx.s" "ecret_" "key"),
    ("d0af2f5f3737", "kv-secret",
     "self.a" ".b.xxx" "xxxxxx" "xxxxxx" "xxxxx." "secret" "_key"),
    ("0183773a9b01", "kv-secret",
     "self.s" "ecret_" "key.se" "cret_k" "ey.sec" "ret_ke" "y.b"),
    ("281d28d30237", "kv-secret",
     "self.secret_" "key_correct_" "horse_batter" "y_staple_ext" "ra_words_her" "e_too"),
)

WQ3134_RECORDS = frozenset(v[0] for v in WQ3134_VALUES)
