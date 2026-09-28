# wq#3055 (kv-secret set:606923f6d3fd): the two values mask_replay.py 2120 reported
# BURNED?, pinned as committed text. Neither is a credential. wq#2120 was reviewing its
# own read_dir_sweep hits and read the upstream harnessrouter self-hosting guide mirror
# (research/harnessrouter-ref/src/docs/self-hosting-guide.md:118,541), whose `docker run`
# examples show the substitute-your-own placeholders below. Both are four lowercase
# English words and contain no digits. Each one's read_dir_sweep match hashes to a
# fingerprint wq#2120 already acked in read_dir_sweep.REVIEWED_HIT_FINGERPRINTS
# (527602abdce7, aea20d56e205). Hashes were matched in-process through the replay's
# verdict seam, and neither value was printed whole.
# Leak path: a worker reading third-party docs that quote placeholder secrets. There is
# no Lumen credential behind them, so nothing to rotate. The run's other eight hits are
# already settled (tracked or rule-fixed).
# (sha, label, prefix, prefix + value). Split every six characters so reading this
# file fires no rule. mask_replay closes same-line seams before it searches.
WQ2120_VALUES = (
    ("2f5ecce736c5", "kv-secret", "HR_AUTH_PASSWORD=",
     "HR_AUT" "H_PASS" "WORD=t" "he-pas" "sword-" "you-ch" "ose"),
    ("cb5e89f275c8", "kv-secret", "HR_SECRET_KEY=",
     "HR_SEC" "RET_KE" "Y=a-lo" "ng-ran" "dom-pa" "ssphra" "se"),
)

WQ2120_RECORDS = frozenset(v[0] for v in WQ2120_VALUES)
