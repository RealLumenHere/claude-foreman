# wq#3180 (kv-secret sha256:08c83376aab6): the one value `mask_replay.py 3169` reported
# BURNED?, pinned as committed text. It is not a credential; nothing was rotated.
#
# wq#3169 built the LiteLLM real-completion probe (ops/wq3169-litellm-completion-probe).
# Its test_litellm_completion_probe.py checks that the master key is read from a .env
# file by writing a throwaway one: `OTHER=1`, a `LITELLM_MASTER_KEY` line, `MORE=2`. The
# Write call carried that line whole, so the dummy body reached the stream-json and the
# kv-secret tripwire fired on it. The body is an `sk-` head on `abc`, `123`, `XYZ`:
# the alphabet in order, then digits in order, then the alphabet's tail. It was named by
# hash in-process from the run's snapshot; wq#3180 printed only its hash, length, shape
# and 3-char chunks. Leak path: a test fixture typing a whole kv-secret-shaped assignment
# instead of building the body at runtime. Nothing to rotate.
#
# (sha, label, value). Split into short literals so reading this file fires no rule;
# mask_replay closes same-line seams before it searches, so the value stays on one line.
WQ3169_VALUES = (
    ("08c83376aab6", "kv-secret", "sk-" "abc" "123" "XYZ"),
)

WQ3169_RECORDS = frozenset(v[0] for v in WQ3169_VALUES)
