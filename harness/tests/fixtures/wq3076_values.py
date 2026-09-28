# wq#3082 (sk-key values set:248906ee66a0): the values `mask_replay.py 3076` reported
# BURNED?, pinned as committed text. None is a credential; nothing was rotated.
#
# sha256:e50b5e28e576 / 20cb115bcd84 (sk-key, 32/31 chars): wq#3076 attempt 2 ran
# `git show docs/wq2278-token-engine-t10-remaining-verbs-and-mig-a1:harness/tests/
# test_gateway_key_per_ticket.py | grep -n "sk-\|MINTED\|STATIC..."` (transcript
# 17801f73-ef06-44be-ac03-0c2244979056, toolu_01VPRfiGCkmBG2YjpseB86yn) and printed that
# branch's MINTED/STATIC gateway-key dummies, lines 23-24 of commit 076e7eb1. They are
# already known by hash (test_mask_replay.py WQ2278_BRANCH_FIXTURES, wq#2924), but only
# the unmerged branch holds them whole — main has the renamed `-dummy` pair (ef8f568) —
# so a replay against main's tree could not account them.
#
# sha256:9fbc246950ba (kv-secret, 17 chars): a dict label in wq#3076's own
# /tmp/fworker-wq3076/diagnose.py (snapshot 20260928T203453Z), the value of a
# `"CHANGE" + "ME_TOKEN"` candidate key, an origin tag for the probe's hit list.
#
# All three were named by hash in-process from the corpus; wq#3082 printed only hashes
# and lengths. Leak path: reading committed fixtures off a branch main does not carry.
# Nothing to rotate — no Lumen credential is behind these strings.
#
# (sha, label, value) or (sha, label, prefix, prefix + value). Split every six characters
# so reading this file fires no rule; mask_replay closes same-line seams before it searches.
WQ3076_VALUES = (
    ("e50b5e28e576", "sk-key", "sk-",
     "sk-min" "ted-pe" "r-tick" "et-key" "-00000" "00001"),
    ("20cb115bcd84", "sk-key", "sk-",
     "sk-sta" "tic-lo" "nglive" "d-key-" "000000" "0001"),
    ("9fbc246950ba", "kv-secret",
     "user/n" "on-hit" "-list"),
)

WQ3076_RECORDS = frozenset(v[0] for v in WQ3076_VALUES)
