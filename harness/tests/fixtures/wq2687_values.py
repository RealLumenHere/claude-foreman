# wq#2701: the twelve values mask_replay.py 2687 reported BURNED?, pinned as committed
# text. None is a credential: wq#2687 never landed, so its own fixtures and branch name
# were in no tree the replay reads. Each row is (sha256[:12], label, value), and was
# written here straight from the replayed corpus, only after it matched one of:
#   - a blob reachable from a foreman ref: wq#2687's unlanded test file (6 rows);
#   - a foreman ref name: the branch fworker cut for wq#2687 attempts 1 and 2 (4 rows),
#     whose slug `...-mirror-cfut-...` today's cloudflare rule reads as a token — the
#     leak path fworker.slugify now closes;
#   - a sequential fixture named by hash from its masked `sed` probe (2 rows).
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
)
