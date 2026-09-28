# wq#2701: the twelve values mask_replay.py 2687 reported BURNED?, pinned as committed
# text. None is a credential: wq#2687 never landed, so its own fixtures and branch name
# were in no tree the replay reads. Each row is (sha256[:12], label, value), and was
# written here straight from the replayed corpus, only after it matched one of:
#   - a blob reachable from a foreman ref: wq#2687's unlanded test file (6 rows);
#   - a foreman ref name: the branch fworker cut for wq#2687 attempts 1 and 2 (4 rows),
#     whose slug `...-mirror-cfut-...` today's cloudflare rule reads as a token — the
#     leak path fworker.slugify now closes;
#   - a sequential fixture named by hash from its masked `sed` probe (2 rows).
# wq#3076: two more kv-secret bodies (last 2 rows), same shape as the `sed` probe pair
# above — sequential-alphabet test echoes (`echo 'SECRET=...' | mask-secrets`) from two
# different wq#2687 attempt worktrees (`...-sed-bound`, `...-sed-bounded-tail`). The
# line-wide `today_closed` used to excuse them via an unrelated accounted hit further
# down the same JSONL line; scoring only the hit's own span surfaces them on their own.
# Pinned by test_mask_replay.py::test_wq2687_corpus_burned_nothing.
WQ2687_VALUES = (
    ("012d7baf3dee", "cloudflare", "settings.override."),
    ("416784616b2f", "cloudflare", "-kv-secr-a1"),
    ("4a20506e0864", "cloudflare", "live_value_still_masks"),
    ("57f614878f60", "cloudflare", "abc123xyz789"),
    ("81220b6ada8d", "cloudflare", "trailing_period_survives"),
    ("a3a2ea7af7a6", "cloudflare", "abc123def456ghi789xyz"),
    ("cdd92de0ef4c", "cloudflare", "live_value_at_floor_still_masks"),
    ("f0ff05bc0020", "cloudflare", "-kv-secr-a2"),
    ("73b942907b52", "kv-secret", "abcdefghijklmnop."),
    ("c8c07793d6f8", "kv-secret", "abcdefghijklm."),
    ("2706fe11e718", "sk-key", "-cf" "ut-kv-secr-a1"),
    ("97f01d9ff3e2", "sk-key", "-cf" "ut-kv-secr-a2"),
    ("7f45a25d5811", "kv-secret", 'abcdef' '123456' '.'),
    ("ad1416ae1ef9", "kv-secret", 'abcdef' 'ghijkl' '9'),
)

# wq#2765: two more values the wq#2740 rule (cab2c41: a body under the floor accounts
# only with its prefix in front) brought back as BURNED?: ordinary words the fworker's
# own PR body wrote right after the label's prefix — `cfut` + `-prefix` and `cfut_` +
# `-style` — which a bare-body lookup had cleared by coincidence. Rows are
# (sha256[:12] of the body, label, prefix, prefix+body); the string is split one
# character into the prefix so reading this file pages nothing, and the seam-closed
# view mask_replay.account reads makes the pair whole again.
WQ2687_PROSE = (
    ("333aa9c1f664", "cloudflare", "cfut", "cf" "ut-prefix"),
    ("131a95eb6af8", "cloudflare", "cfut_", "cf" "ut_-style"),
)

# wq#2765: every (hash-only) record the replay reproduces from wq#2687's faithful
# artefacts — the 44 the wq#2701 verdict counted plus attempt 3's (20:32-20:37Z, after
# the count was pinned at b28a936 18:59Z). test_wq2687_corpus_burned_nothing asserts
# containment, not a count: a new artefact may ADD a record (it is caught by
# `unaccounted` if it matters), but none of these may leave.
WQ2687_RECORDS = frozenset((
    "012d7baf3dee", "077e211841b8", "131a95eb6af8", "1fc04aae4c07", "1fd882463874", "20227a136ec3",
    "20227a136ec3", "21e60eb807e5", "2706fe11e718", "2b7631697dda", "333aa9c1f664", "34e1b54e5c8c",
    "38de98f38239", "3c1c495ff227", "416784616b2f", "41a7ba8ea8a1", "4634d1ff2af5", "4788ae148eb0",
    "4a20506e0864", "57f614878f60", "6711a24c460a", "6fbcdb8df881", "73b942907b52", "76880d60191a",
    "7f45a25d5811", "81220b6ada8d", "8f4f46e5efef", "97f01d9ff3e2", "a32aebae4867", "a3a2ea7af7a6",
    "a4bc51f68eca", "a745825001bc", "ad1416ae1ef9", "b8789db0c2da", "b8b2abf0547b", "c775e7b757ed",
    "c8c07793d6f8", "cdd92de0ef4c", "cde0fb0dec14", "dd65eea0329d", "de20960ffcae", "eb522505a0e3",
    "f0ff05bc0020", "f39dac6cbaba", "f50f2d7b79dd", "ff1fd85b7adb", "ffa2b4e40c8d",
))
